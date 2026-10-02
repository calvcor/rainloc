"""
RainLoc - AEMET HARMONIE-AROME (2.5 km / 0.025°) Open Data Worker & Pipeline
Ingesta, decodificación y reproyección Web Mercator de previsiones de precipitación
del modelo convectivo no hidrostático de alta resolución HARMONIE-AROME de AEMET.
Soporte para 48 pasos horarios (+1h a +48h), conversión de capas GeoTIFF rasterizadas a matrices físicas (mm),
generación de imágenes PNG transparentes Web Mercator y matrices .npz para consultas instantáneas (0ms).
"""
import os
import gc
import json
import time
import shutil
import logging
import asyncio
import io
import ssl
import re
import tarfile
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional, List, Tuple, Set, AsyncGenerator

import numpy as np
from PIL import Image
from scipy.interpolate import RegularGridInterpolator

from app.config import settings, HARMONIE_CACHE_DIR

logger = logging.getLogger("rainloc-backend.harmonie")

# Bounding Box y Cuadrícula Web Mercator idéntica al radar, ECMWF, GFS, AROME, ICON y GEM
SPAIN_BBOX = {"lat_min": 35.0, "lat_max": 44.5, "lon_min": -10.0, "lon_max": 5.0}
R_EARTH = 6378137.0
Y_MERC_MIN = R_EARTH * np.log(np.tan(np.pi / 4 + np.radians(SPAIN_BBOX["lat_min"]) / 2))
Y_MERC_MAX = R_EARTH * np.log(np.tan(np.pi / 4 + np.radians(SPAIN_BBOX["lat_max"]) / 2))
GRID_H = 950
GRID_W = 1500

Y_MERC_GRID = np.linspace(Y_MERC_MAX, Y_MERC_MIN, GRID_H)
SPAIN_GRID_LATS = np.degrees(2 * np.arctan(np.exp(Y_MERC_GRID / R_EARTH)) - np.pi / 2)
SPAIN_GRID_LONS = np.linspace(SPAIN_BBOX["lon_min"], SPAIN_BBOX["lon_max"], GRID_W)

# Pasos estándar de predicción HARMONIE-AROME (cada 1 hora desde +1h hasta +48h)
HARMONIE_STEPS = list(range(1, 49))

# URL de descarga de Datos Abiertos AEMET / MITECO para Harmonie-Arome Península y Baleares
AEMET_HARMONIE_URL = getattr(settings, "AEMET_HARMONIE_DOWNLOAD_URL", "https://www.aemet.es/es/api-eltiempo/modelos/download/harmonie/PB")
USER_AGENT = "Mozilla/5.0 (compatible; RainLoc-WeatherService/1.0; +https://github.com/carlosalventosa/RainLoc)"

# Paletas estándar de calibración AEMET Harmonie (Producto 61: 1h precipitación, Producto 228: acumulado total)
PALETTE_61_RGBA = np.array([
    [236, 200, 200, 255],
    [219, 141, 140, 255],
    [204, 84, 83, 255],
    [255, 0, 0, 255],
    [255, 61, 3, 255],
    [255, 122, 8, 255],
    [255, 186, 15, 255],
    [255, 255, 0, 255],
    [191, 230, 0, 255],
    [128, 204, 0, 255],
    [0, 153, 0, 255],
    [0, 178, 64, 255],
    [0, 204, 128, 255],
    [51, 245, 222, 255],
    [176, 224, 230, 255],
    [19, 49, 52, 0]
], dtype=np.uint8)

PALETTE_61_VALS = np.array([
    325.0, 275.0, 215.0, 150.0, 110.0, 90.0, 70.0, 50.0, 35.0, 25.0, 15.0, 7.5, 3.5, 1.5, 0.75, 0.0
], dtype=np.float32)

PALETTE_228_RGBA = np.array([
    [190, 0, 61, 255],
    [171, 26, 133, 255],
    [153, 51, 204, 255],
    [153, 102, 255, 255],
    [255, 120, 212, 255],
    [255, 89, 89, 255],
    [255, 105, 31, 255],
    [255, 138, 23, 255],
    [255, 201, 10, 255],
    [217, 242, 0, 255],
    [168, 245, 0, 255],
    [79, 250, 184, 255],
    [102, 247, 237, 255],
    [176, 250, 247, 255],
    [217, 248, 248, 255],
    [0, 0, 0, 0]
], dtype=np.uint8)

PALETTE_228_VALS = np.array([
    160.0, 135.0, 125.0, 115.0, 105.0, 95.0, 85.0, 75.0, 65.0, 55.0, 45.0, 35.0, 25.0, 15.0, 5.0, 0.0
], dtype=np.float32)


def colorize_precip_array(precip_mm: np.ndarray) -> np.ndarray:
    """
    Rampa de color meteorológica oficial para precipitación acumulada/intervalos (mm).
    Valores < 0.1 mm son transparentes.
    Retorna matriz RGBA uint8 de forma (H, W, 4).
    """
    h, w = precip_mm.shape
    rgba = np.zeros((h, w, 4), dtype=np.uint8)

    # 0.1 - 1.0 mm: Celeste muy suave
    m1 = (precip_mm >= 0.1) & (precip_mm < 1.0)
    rgba[m1] = [186, 230, 253, 160]

    # 1.0 - 3.0 mm: Azul cielo
    m2 = (precip_mm >= 1.0) & (precip_mm < 3.0)
    rgba[m2] = [56, 189, 248, 185]

    # 3.0 - 10.0 mm: Azul intenso
    m3 = (precip_mm >= 3.0) & (precip_mm < 10.0)
    rgba[m3] = [2, 132, 199, 210]

    # 10.0 - 20.0 mm: Verde claro
    m4 = (precip_mm >= 10.0) & (precip_mm < 20.0)
    rgba[m4] = [74, 222, 128, 225]

    # 20.0 - 40.0 mm: Verde bosque
    m5 = (precip_mm >= 20.0) & (precip_mm < 40.0)
    rgba[m5] = [22, 163, 74, 235]

    # 40.0 - 70.0 mm: Amarillo intenso
    m6 = (precip_mm >= 40.0) & (precip_mm < 70.0)
    rgba[m6] = [250, 204, 21, 245]

    # 70.0 - 100.0 mm: Naranja
    m7 = (precip_mm >= 70.0) & (precip_mm < 100.0)
    rgba[m7] = [249, 115, 22, 250]

    # 100.0 - 150.0 mm: Rojo
    m8 = (precip_mm >= 100.0) & (precip_mm < 150.0)
    rgba[m8] = [239, 68, 68, 255]

    # 150.0 - 250.0 mm: Magenta / Púrpura
    m9 = (precip_mm >= 150.0) & (precip_mm < 250.0)
    rgba[m9] = [217, 70, 239, 255]

    # >= 250.0 mm: Blanco / Púrpura brillante
    m10 = (precip_mm >= 250.0)
    rgba[m10] = [255, 255, 255, 255]

    return rgba


def _rgba_to_values(arr: np.ndarray, palette_rgba: np.ndarray, palette_vals: np.ndarray) -> np.ndarray:
    """Convierte un raster RGBA de AEMET a valores en punto flotante (mm) usando distancia euclidiana de color."""
    h, w, _ = arr.shape
    val_grid = np.zeros((h, w), dtype=np.float32)
    mask_active = arr[:, :, 3] > 20

    if not np.any(mask_active):
        return val_grid

    rgb_active = arr[mask_active, :3].astype(np.int32)
    palette_rgb = palette_rgba[:, :3].astype(np.int32)

    # Distancia en espacio RGB
    dists = np.sum((rgb_active[:, None, :] - palette_rgb[None, :, :]) ** 2, axis=2)
    best_idx = np.argmin(dists, axis=1)
    val_grid[mask_active] = palette_vals[best_idx]
    return val_grid


class HarmonieWorker:
    """Worker para la descarga, transformación y servicio de datos de AEMET HARMONIE-AROME (2.5 km / alta resolución)."""

    def __init__(self):
        self.cache_dir: Path = HARMONIE_CACHE_DIR
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.current_manifest: Optional[Dict[str, Any]] = None
        self._sync_lock = asyncio.Lock()
        self._is_syncing: bool = False
        self._in_memory_arrays: Dict[str, np.ndarray] = {}
        self._subscribers: Set[asyncio.Queue] = set()
        self._last_etag: Optional[str] = None
        self._last_modified: Optional[str] = None
        self._load_latest_manifest_from_disk()

    def notify_harmonie_update(self, cycle_str: str, available_steps: List[int], step_added: Optional[int] = None, status: str = "ready", is_syncing: bool = False):
        """Notifica de forma inmediata y thread-safe a todos los clientes SSE conectados."""
        max_step = max(available_steps) if available_steps else 0
        run_str = ""
        if "_" in cycle_str:
            parts = cycle_str.split("_")
            if len(parts) > 1:
                run_str = parts[1].lower()
        if not run_str and len(cycle_str) >= 2:
            run_str = cycle_str[-2:].lower()

        max_target = getattr(settings, "HARMONIE_MAX_STEPS", 48)
        payload = {
            "event": "harmonie_update",
            "cycle_str": cycle_str,
            "run": run_str or "00z",
            "step_added": step_added,
            "available_steps": available_steps,
            "max_step": max_step,
            "is_complete": (max_step >= max_target),
            "is_updating": is_syncing or (max_step < max_target and status != "complete"),
            "status": status,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        dead_subscribers = set()
        for q in list(self._subscribers):
            try:
                loop = getattr(q, "_loop", None)
                if loop and loop.is_running():
                    loop.call_soon_threadsafe(q.put_nowait, payload)
                else:
                    q.put_nowait(payload)
            except Exception:
                dead_subscribers.add(q)
        for q in dead_subscribers:
            self._subscribers.discard(q)

    async def subscribe_stream(self) -> AsyncGenerator[Dict[str, Any], None]:
        """Generador asíncrono para clientes SSE con keep-alive periódico."""
        loop = asyncio.get_running_loop()
        q = asyncio.Queue(maxsize=20)
        q._loop = loop
        self._subscribers.add(q)

        # Enviar estado actual de inmediato
        if self.current_manifest:
            avail = self.current_manifest.get("available_steps", [])
            init_event = {
                "event": "harmonie_update",
                "cycle_str": self.current_manifest.get("cycle_str", ""),
                "run": self.current_manifest.get("cycle_str", "").split("_")[-1].lower() if "_" in self.current_manifest.get("cycle_str", "") else "00z",
                "step_added": None,
                "available_steps": avail,
                "max_step": max(avail) if avail else 0,
                "is_complete": bool(self.current_manifest.get("is_complete")),
                "is_updating": self._is_syncing,
                "status": "ready",
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
            await q.put(init_event)

        try:
            while True:
                try:
                    event = await asyncio.wait_for(q.get(), timeout=25.0)
                    yield event
                except asyncio.TimeoutError:
                    yield {"event": "ping", "timestamp": datetime.now(timezone.utc).isoformat()}
        finally:
            self._subscribers.discard(q)

    def _load_latest_manifest_from_disk(self):
        """Busca el ciclo más reciente en disco y restaura el estado y las matrices .npz en memoria."""
        try:
            manifests = sorted(self.cache_dir.glob("*/manifest.json"), key=lambda p: p.parent.name, reverse=True)
            if manifests:
                latest_manifest_path = manifests[0]
                with open(latest_manifest_path, "r", encoding="utf-8") as f:
                    self.current_manifest = json.load(f)
                
                # Cargar arrays en memoria para consultas instantáneas O(1)
                arrays_path = latest_manifest_path.parent / "arrays.npz"
                if arrays_path.exists():
                    loaded = np.load(arrays_path)
                    for k in loaded.files:
                        self._in_memory_arrays[k] = loaded[k]
                logger.info(f"HarmonieWorker: Restaurado ciclo {self.current_manifest.get('cycle_str')} con {len(self.current_manifest.get('available_steps', []))} pasos.")
        except Exception as e:
            logger.error(f"HarmonieWorker: Error cargando manifest desde disco: {e}")

    def get_metadata(self) -> Dict[str, Any]:
        """Devuelve los metadatos completos del ciclo disponible."""
        if not self.current_manifest:
            return {
                "model": "HARMONIE-AROME (AEMET)",
                "source": "AEMET / MITECO Datos Abiertos (0.025° / ~2.5km)",
                "cycle_str": "",
                "status": "empty",
                "run": "",
                "available_steps": [],
                "steps": [],
                "is_complete": False,
                "is_syncing": self._is_syncing,
                "bbox": SPAIN_BBOX
            }
        out = dict(self.current_manifest)
        out["is_syncing"] = self._is_syncing
        cycle_str = self.current_manifest.get("cycle_str", "")
        run_part = cycle_str.split("_")[1] if "_" in cycle_str else cycle_str
        out["status"] = "ready" if self.current_manifest.get("is_complete") else "syncing"
        out["run"] = run_part
        return out

    def get_image_path(self, step: int, layer_type: str = "total") -> Optional[Path]:
        """Retorna la ruta al archivo PNG en disco del paso y tipo solicitados."""
        if not self.current_manifest:
            return None
        cycle_str = self.current_manifest.get("cycle_str")
        if not cycle_str:
            return None
        cycle_dir = self.cache_dir / cycle_str
        prefix = "total" if layer_type == "total" else "interval"
        img_p = cycle_dir / f"{prefix}_{step:02d}.png"
        if img_p.exists():
            return img_p
        return None

    def get_array(self, step: int, layer_type: str = "total") -> Optional[np.ndarray]:
        """Obtiene la matriz 2D (GRID_H, GRID_W) de valores de precipitación en mm."""
        prefix = "total" if layer_type == "total" else "interval"
        key = f"{prefix}_{step:02d}"
        if key in self._in_memory_arrays:
            return self._in_memory_arrays[key]
        return None

    def get_matrix(self, step: int, layer_type: str = "total") -> Optional[np.ndarray]:
        """Alias compatible con BasinHydrologyService para obtener la matriz 2D (GRID_H, GRID_W)."""
        return self.get_array(step, layer_type)

    def get_value_at(self, lat: float, lon: float, step: int, layer_type: str = "total") -> Optional[float]:
        """Consulta en O(1) el valor en mm para unas coordenadas lat/lon dadas."""
        arr = self.get_array(step, layer_type)
        if arr is None:
            return None
        if not (SPAIN_BBOX["lat_min"] <= lat <= SPAIN_BBOX["lat_max"] and SPAIN_BBOX["lon_min"] <= lon <= SPAIN_BBOX["lon_max"]):
            return 0.0

        y_merc = R_EARTH * np.log(np.tan(np.pi / 4 + np.radians(lat) / 2))
        row = int(np.clip(np.round((Y_MERC_MAX - y_merc) / (Y_MERC_MAX - Y_MERC_MIN) * (GRID_H - 1)), 0, GRID_H - 1))
        col = int(np.clip(np.round((lon - SPAIN_BBOX["lon_min"]) / (SPAIN_BBOX["lon_max"] - SPAIN_BBOX["lon_min"]) * (GRID_W - 1)), 0, GRID_W - 1))

        val = float(arr[row, col])
        return round(val, 2)

    def get_max_point(self, step: int, layer_type: str = "total") -> Optional[Dict[str, Any]]:
        """Obtiene el punto geográfico y valor máximo de precipitación para un paso dado."""
        arr = self.get_array(step, layer_type)
        if arr is None:
            return None
        max_val = float(np.max(arr))
        if max_val <= 0:
            return None
        max_row, max_col = np.unravel_index(np.argmax(arr), arr.shape)
        lat = float(SPAIN_GRID_LATS[max_row])
        lon = float(SPAIN_GRID_LONS[max_col])
        return {
            "lat": round(lat, 4),
            "lon": round(lon, 4),
            "value_mm": round(max_val, 2)
        }

    def get_series(self, lat: float, lon: float) -> Dict[str, Any]:
        """Obtiene la serie temporal completa de precipitación para una ubicación."""
        if not self.current_manifest:
            return {"error": "Sin datos del modelo Harmonie", "steps": []}

        steps_meta = self.current_manifest.get("steps", [])
        series = []
        for s_info in steps_meta:
            st = s_info["step"]
            val_tot = self.get_value_at(lat, lon, st, "total")
            val_int = self.get_value_at(lat, lon, st, "interval")
            series.append({
                "step": st,
                "valid_time_iso": s_info.get("valid_time_iso"),
                "valid_time_local": s_info.get("valid_time_local"),
                "precip_total_mm": val_tot or 0.0,
                "precip_interval_mm": val_int or 0.0
            })
        return {
            "model": "HARMONIE-AROME (AEMET)",
            "cycle_str": self.current_manifest.get("cycle_str"),
            "lat": lat,
            "lon": lon,
            "series": series
        }

    async def sync_harmonie_forecast(self, force: bool = False):
        """Descarga el paquete tar.gz de AEMET Harmonie, procesa los GeoTIFFs y genera el ciclo operativo."""
        if self._is_syncing and not force:
            logger.debug("HarmonieWorker: Ya hay una sincronización en curso. Omitiendo.")
            return

        async with self._sync_lock:
            self._is_syncing = True
            try:
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, self._sync_sync_blocking, force)
            except Exception as e:
                logger.error(f"HarmonieWorker: Error en sincronización: {e}", exc_info=True)
            finally:
                self._is_syncing = False

    def _sync_sync_blocking(self, force: bool = False):
        """Lógica síncrona bloqueante de descarga y procesamiento (ejecutada en worker thread)."""
        logger.info("HarmonieWorker: Iniciando comprobación de nuevas salidas de AEMET Harmonie...")
        ctx = ssl.create_default_context()
        req = urllib.request.Request(
            AEMET_HARMONIE_URL,
            headers={"User-Agent": USER_AGENT}
        )

        try:
            with urllib.request.urlopen(req, timeout=45, context=ctx) as resp:
                if resp.status != 200:
                    logger.warning(f"HarmonieWorker: Respuesta no exitosa de AEMET: HTTP {resp.status}")
                    return

                data = resp.read()
                if not data or len(data) < 100000:
                    logger.warning(f"HarmonieWorker: Archivo recibido demasiado pequeño ({len(data)} bytes).")
                    return

                logger.info(f"HarmonieWorker: Descargado paquete tar.gz ({len(data) / (1024*1024):.2f} MB). Procesando contenido...")

                with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
                    members = tar.getmembers()
                    names = [m.name for m in members]

                    # Filtrar pasos horarios de 61_1HH.tif (precipitación horaria) y 228.tif (total)
                    hourly_1h_files = sorted([n for n in names if n.endswith("61_1HH.tif")])
                    total_files = sorted([n for n in names if n.endswith("228.tif")])

                    if not hourly_1h_files:
                        logger.warning("HarmonieWorker: No se encontraron archivos de precipitación (61_1HH.tif) en el archivo tar.gz.")
                        return

                    # Extraer fecha/hora del primer paso para identificar la pasada
                    # Formato: down_2026-10-02T07:00:00+00:00_61_1HH.tif
                    first_file = hourly_1h_files[0]
                    match_dt = re.search(r"down_(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})", first_file)
                    if match_dt:
                        first_valid_dt = datetime.fromisoformat(match_dt.group(1)).replace(tzinfo=timezone.utc)
                        # El paso 1 corresponde a la hora de inicio del modelo + 1h
                        # La pasada (run) suele ser 00z, 06z, 12z o 18z
                        run_hour = (first_valid_dt.hour - 1) % 24
                        run_dt = first_valid_dt.replace(hour=run_hour, minute=0, second=0) - (timedelta(days=1) if run_hour > first_valid_dt.hour else timedelta(0))
                        cycle_str = f"{run_dt.strftime('%Y%m%d')}_{run_dt.hour:02d}z"
                    else:
                        now_utc = datetime.now(timezone.utc)
                        cycle_str = f"{now_utc.strftime('%Y%m%d')}_{now_utc.hour:02d}z"
                        run_dt = now_utc

                    cycle_dir = self.cache_dir / cycle_str
                    cycle_dir.mkdir(parents=True, exist_ok=True)

                    # Si el ciclo ya está completado y no se fuerza, salir
                    manifest_file = cycle_dir / "manifest.json"
                    if manifest_file.exists() and not force:
                        try:
                            with open(manifest_file, "r", encoding="utf-8") as f:
                                existing_m = json.load(f)
                            if existing_m.get("is_complete") and len(existing_m.get("available_steps", [])) >= len(hourly_1h_files):
                                logger.info(f"HarmonieWorker: El ciclo {cycle_str} ya está completamente procesado ({len(existing_m.get('available_steps', []))} pasos).")
                                self.current_manifest = existing_m
                                return
                        except Exception:
                            pass

                    # Parámetros espaciales de la malla de entrada GeoTIFF AEMET (0.025°)
                    src_lats = np.linspace(34.5125, 44.4875, 400)  # creciente para RegularGridInterpolator
                    src_lons = np.linspace(-11.0125, 4.9625, 640)  # creciente
                    mesh_lats, mesh_lons = np.meshgrid(SPAIN_GRID_LATS, SPAIN_GRID_LONS, indexing="ij")
                    query_pts = np.stack([mesh_lats, mesh_lons], axis=-1)

                    available_steps: List[int] = []
                    steps_metadata: List[Dict[str, Any]] = []
                    arrays_to_save: Dict[str, np.ndarray] = {}

                    accumulated_total = np.zeros((GRID_H, GRID_W), dtype=np.float32)

                    for step_idx, filename in enumerate(hourly_1h_files, start=1):
                        step_num = step_idx
                        f_extracted = tar.extractfile(filename)
                        if not f_extracted:
                            continue

                        img_raw = Image.open(io.BytesIO(f_extracted.read()))
                        arr_rgba = np.array(img_raw)
                        if arr_rgba.shape != (400, 640, 4):
                            continue

                        # 1. Convertir RGBA a matriz física en mm (intervalo 1h)
                        arr_mm_src = _rgba_to_values(arr_rgba, PALETTE_61_RGBA, PALETTE_61_VALS)
                        # Invertir filas porque el TIFF va de Norte a Sur (44.48 -> 34.51) y RegularGridInterpolator requiere lats crecientes
                        arr_mm_src_flipped = np.flipud(arr_mm_src)

                        # 2. Interpolar a la malla estándar Web Mercator RainLoc (950 x 1500)
                        interp_1h = RegularGridInterpolator(
                            (src_lats, src_lons), arr_mm_src_flipped, method="linear", bounds_error=False, fill_value=0.0
                        )
                        grid_interval = np.clip(interp_1h(query_pts).astype(np.float32), 0.0, 500.0)

                        # 3. Procesar o calcular el acumulado total
                        # Buscar si existe 228.tif para esta hora
                        match_dt_step = re.search(r"down_(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})", filename)
                        valid_iso = ""
                        valid_local = ""
                        if match_dt_step:
                            v_dt = datetime.fromisoformat(match_dt_step.group(1)).replace(tzinfo=timezone.utc)
                            valid_iso = v_dt.isoformat()
                            # Convertir a hora local peninsular (UTC+1 / UTC+2)
                            v_local = v_dt.astimezone(timezone(timedelta(hours=2)))  # CEST aprox
                            valid_local = v_local.strftime("%d/%m %H:00")
                        else:
                            v_dt = run_dt + timedelta(hours=step_num)
                            valid_iso = v_dt.isoformat()
                            valid_local = f"+{step_num}h"

                        # Acumular progresivamente
                        accumulated_total += grid_interval
                        grid_total = accumulated_total.copy()

                        # 4. Generar imágenes PNG transparentes Web Mercator
                        rgba_total = colorize_precip_array(grid_total)
                        rgba_interval = colorize_precip_array(grid_interval)

                        img_tot_path = cycle_dir / f"total_{step_num:02d}.png"
                        img_int_path = cycle_dir / f"interval_{step_num:02d}.png"

                        Image.fromarray(rgba_total).save(img_tot_path, "PNG", optimize=True)
                        Image.fromarray(rgba_interval).save(img_int_path, "PNG", optimize=True)

                        # 5. Guardar arrays para npz y memoria
                        arrays_to_save[f"total_{step_num:02d}"] = grid_total
                        arrays_to_save[f"interval_{step_num:02d}"] = grid_interval
                        self._in_memory_arrays[f"total_{step_num:02d}"] = grid_total
                        self._in_memory_arrays[f"interval_{step_num:02d}"] = grid_interval

                        # Estadísticas del paso
                        max_tot = float(np.max(grid_total))
                        max_int = float(np.max(grid_interval))
                        max_row, max_col = np.unravel_index(np.argmax(grid_total), grid_total.shape)
                        max_lat = float(SPAIN_GRID_LATS[max_row])
                        max_lon = float(SPAIN_GRID_LONS[max_col])

                        available_steps.append(step_num)
                        steps_metadata.append({
                            "step": step_num,
                            "delta_hours": 1,
                            "valid_time_iso": valid_iso,
                            "valid_time_local": valid_local,
                            "max_total_precip_mm": round(max_tot, 2),
                            "max_interval_precip_mm": round(max_int, 2),
                            "max_lat": round(max_lat, 4),
                            "max_lon": round(max_lon, 4)
                        })

                    # Guardar archivo arrays.npz comprimido
                    npz_path = cycle_dir / "arrays.npz"
                    np.savez_compressed(npz_path, **arrays_to_save)

                    # Guardar manifest.json
                    manifest_payload = {
                        "model": "HARMONIE-AROME (AEMET)",
                        "source": "AEMET / MITECO Datos Abiertos (0.025° / ~2.5km)",
                        "cycle_str": cycle_str,
                        "run_id": cycle_str,
                        "generated_at": datetime.now(timezone.utc).isoformat(),
                        "is_complete": len(available_steps) >= 48,
                        "downloaded_max_step": max(available_steps) if available_steps else 0,
                        "available_steps": available_steps,
                        "steps": steps_metadata,
                        "bbox": SPAIN_BBOX
                    }
                    with open(manifest_file, "w", encoding="utf-8") as f:
                        json.dump(manifest_payload, f, ensure_ascii=False, indent=2)

                    self.current_manifest = manifest_payload
                    logger.info(f"HarmonieWorker: Ciclo {cycle_str} procesado con éxito ({len(available_steps)} pasos listos).")

                    # Notificar a los clientes conectados
                    self.notify_harmonie_update(
                        cycle_str=cycle_str,
                        available_steps=available_steps,
                        step_added=None,
                        status="ready",
                        is_syncing=False
                    )

                    # Limpiar ciclos antiguos conservando los últimos 2
                    self._cleanup_old_cycles(keep_count=2)

        except urllib.error.HTTPError as e:
            if e.code == 429:
                logger.warning("HarmonieWorker: Rate limit 429 alcanzado en AEMET OpenData. Esperando próxima iteración.")
            else:
                logger.error(f"HarmonieWorker: Error HTTP {e.code} descargando Harmonie: {e}")
        except Exception as e:
            logger.error(f"HarmonieWorker: Error procesando archivo AEMET: {e}", exc_info=True)

    def _cleanup_old_cycles(self, keep_count: int = 2):
        """Elimina directorios de ciclos anteriores para liberar espacio en disco."""
        try:
            dirs = sorted([d for d in self.cache_dir.iterdir() if d.is_dir()], key=lambda p: p.name, reverse=True)
            for old_dir in dirs[keep_count:]:
                logger.info(f"HarmonieWorker: Eliminando ciclo antiguo {old_dir.name}")
                shutil.rmtree(old_dir, ignore_errors=True)
        except Exception as e:
            logger.error(f"HarmonieWorker: Error limpiando ciclos antiguos: {e}")


harmonie_worker = HarmonieWorker()
