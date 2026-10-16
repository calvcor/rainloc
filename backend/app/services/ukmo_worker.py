"""
RainLoc - UK Met Office Unified Model (Global Deterministic, 0.09° / ~10 km) Worker & Pipeline
Ingesta secuencial y progresiva de previsiones de precipitación del modelo determinista global del UK Met Office.
Descarga optimizada desde AWS Open Data (s3://met-office-atmospheric-model-data) con botocore UNSIGNED.
Recorte geoespacial a Península Ibérica y Baleares, cálculo de acumulados totales e intervalos (hasta +144h / 6 días),
generación de imágenes PNG transparentes Web Mercator y matrices .npz para consultas instantáneas (0ms).
"""
import os
import gc
import json
import time
import shutil
import logging
import asyncio
import re
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional, List, Tuple, Set, AsyncGenerator

import h5py
import numpy as np
from PIL import Image
from scipy.interpolate import RegularGridInterpolator
import boto3
from botocore import UNSIGNED
from botocore.config import Config

from app.config import settings

logger = logging.getLogger("rainloc-backend.ukmo")

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


class UKMOWorker:
    """Worker para la descarga, transformación y servicio de datos de UK Met Office Global (10 km)."""

    def __init__(self):
        self.cache_dir: Path = settings.UKMO_CACHE_DIR
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.current_manifest: Optional[Dict[str, Any]] = None
        self._sync_lock = asyncio.Lock()
        self._is_syncing: bool = False
        self._in_memory_arrays: Dict[str, np.ndarray] = {}
        self._subscribers: Set[asyncio.Queue] = set()

        self.s3_bucket: str = getattr(settings, "UKMO_S3_BUCKET", "met-office-atmospheric-model-data")
        self.s3_region: str = getattr(settings, "UKMO_S3_REGION", "eu-west-2")
        self.s3_prefix: str = getattr(settings, "UKMO_S3_PREFIX", "global-deterministic-10km")

        self._s3_client = None
        self._load_latest_manifest_from_disk()

    @property
    def s3_client(self):
        if self._s3_client is None:
            self._s3_client = boto3.client(
                "s3",
                region_name=self.s3_region,
                config=Config(signature_version=UNSIGNED)
            )
        return self._s3_client

    def notify_ukmo_update(
        self,
        cycle_str: str,
        available_steps: List[int],
        step_added: Optional[int] = None,
        status: str = "ready",
        is_syncing: bool = False
    ):
        """Notifica de forma inmediata y thread-safe a todos los clientes SSE conectados."""
        max_step = max(available_steps) if available_steps else 0
        run_str = ""
        if "_" in cycle_str:
            parts = cycle_str.split("_")
            if len(parts) > 1:
                run_str = parts[1].lower()
        if not run_str and len(cycle_str) >= 2:
            run_str = cycle_str[-2:].lower()

        max_target = getattr(settings, "UKMO_MAX_STEPS", 144)
        payload = {
            "event": "ukmo_update",
            "model": "ukmo",
            "cycle_str": cycle_str,
            "run": run_str or "00z",
            "step_added": step_added,
            "available_steps": available_steps,
            "max_step": max_step,
            "downloaded_max_step": max_step,
            "raw_max_step": max_step,
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
        try:
            current_meta = self.get_metadata()
            initial_payload = {
                "event": "ukmo_init",
                "model": "ukmo",
                "cycle_str": current_meta.get("cycle_str"),
                "run": current_meta.get("run"),
                "available_steps": current_meta.get("available_steps", []),
                "max_step": current_meta.get("max_step", 0),
                "downloaded_max_step": current_meta.get("downloaded_max_step", current_meta.get("max_step", 0)),
                "raw_max_step": current_meta.get("raw_max_step", current_meta.get("max_step", 0)),
                "is_complete": current_meta.get("is_complete", False),
                "is_updating": current_meta.get("is_updating", False),
                "status": current_meta.get("status", "empty"),
                "timestamp": current_meta.get("updated_at") or datetime.now(timezone.utc).isoformat()
            }
            yield initial_payload

            while True:
                try:
                    event = await asyncio.wait_for(q.get(), timeout=25.0)
                    yield event
                except asyncio.TimeoutError:
                    yield {"event": "ping"}
        finally:
            self._subscribers.discard(q)

    def _load_latest_manifest_from_disk(self):
        """Busca el manifiesto más reciente en disco y lo carga en memoria."""
        try:
            manifest_files = list(self.cache_dir.glob("*/manifest.json"))
            if not manifest_files:
                return
            manifest_files.sort(key=lambda p: p.parent.name, reverse=True)
            for m_path in manifest_files:
                try:
                    with open(m_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        if data.get("available_steps") and len(data["available_steps"]) > 0:
                            self.current_manifest = data
                            logger.info(f"UKMO: Manifiesto cargado desde disco: {data.get('cycle_str')} ({len(data['available_steps'])} pasos)")
                            return
                except Exception as e:
                    logger.warning(f"Error leyendo manifiesto {m_path}: {e}")
        except Exception as e:
            logger.error(f"Error escaneando manifiestos de UKMO: {e}")

    def _get_previous_manifest(self) -> Optional[Dict[str, Any]]:
        """Recupera el manifiesto del ciclo inmediatamente anterior para fallback."""
        try:
            manifest_files = list(self.cache_dir.glob("*/manifest.json"))
            if len(manifest_files) < 2:
                return None
            manifest_files.sort(key=lambda p: p.parent.name, reverse=True)
            cur_cycle = self.current_manifest.get("cycle_str") if self.current_manifest else None
            for mf in manifest_files:
                if cur_cycle and mf.parent.name == cur_cycle:
                    continue
                try:
                    with open(mf, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        if data.get("available_steps") and len(data["available_steps"]) > 0:
                            return data
                except Exception:
                    pass
        except Exception:
            pass
        return None

    def get_metadata(self) -> Dict[str, Any]:
        """Devuelve los metadatos públicos de la corrida activa de UKMO."""
        if not self.current_manifest:
            self._load_latest_manifest_from_disk()

        if self.current_manifest:
            meta = dict(self.current_manifest)
            avail = list(meta.get("available_steps", []))
            raw_max_step = max(avail) if avail else 0
            target_max = getattr(settings, "UKMO_MAX_STEPS", 144)
            raw_complete = (raw_max_step >= target_max)
            meta["available_steps"] = avail
            meta["max_step"] = raw_max_step
            meta["downloaded_max_step"] = raw_max_step
            meta["raw_max_step"] = raw_max_step
            meta["raw_available_steps"] = avail
            meta["is_syncing"] = self._is_syncing
            meta["is_complete"] = raw_complete
            meta["is_updating"] = self._is_syncing or (
                raw_max_step < target_max
                and meta.get("status") != "complete"
            )
            return meta

        return {
            "model": "UK Met Office (10 km)",
            "cycle": None,
            "cycle_str": None,
            "run": None,
            "status": "synchronizing" if self._is_syncing else "empty",
            "available_steps": [],
            "max_step": 0,
            "downloaded_max_step": 0,
            "raw_max_step": 0,
            "is_complete": False,
            "is_updating": self._is_syncing,
            "bbox": SPAIN_BBOX,
            "steps": [],
            "updated_at": None,
            "is_syncing": self._is_syncing
        }

    def get_image_path(self, step: int, layer_type: str = "total") -> Optional[Path]:
        """Devuelve la ruta al archivo PNG del paso y tipo solicitados (con soporte de fallback)."""
        if not self.current_manifest:
            self._load_latest_manifest_from_disk()
        if not self.current_manifest:
            return None

        cycle_str = self.current_manifest.get("cycle_str")
        if not cycle_str:
            return None
        cycle_dir = self.cache_dir / cycle_str
        prefix = "total" if layer_type == "total" else "interval"
        img_path = cycle_dir / f"{prefix}_step_{step:03d}.png"
        if img_path.exists():
            return img_path

        # Fallback a ciclo anterior si aún no está disponible
        prev_manifest = self._get_previous_manifest()
        if prev_manifest and self.current_manifest.get("cycle") and prev_manifest.get("cycle"):
            try:
                latest_dt = datetime.fromisoformat(self.current_manifest["cycle"].replace("Z", "+00:00"))
                prev_dt = datetime.fromisoformat(prev_manifest["cycle"].replace("Z", "+00:00"))
                diff_hours = int(round((latest_dt - prev_dt).total_seconds() / 3600))
                prev_step = step + diff_hours
                prev_dir = self.cache_dir / prev_manifest["cycle_str"]
                prev_img = prev_dir / f"{prefix}_step_{prev_step:03d}.png"
                if prev_img.exists():
                    return prev_img
            except Exception:
                pass

        return None

    def _load_grid_file(self, path_no_ext: Path) -> Optional[np.ndarray]:
        """Carga matriz .npz comprimida o .npy con fallback."""
        npz_p = path_no_ext.with_suffix(".npz")
        if npz_p.exists():
            try:
                with np.load(npz_p) as d:
                    return d["data"] if "data" in d else d[d.files[0]]
            except Exception as e:
                logger.warning(f"Error cargando {npz_p}: {e}")
        npy_p = path_no_ext.with_suffix(".npy")
        if npy_p.exists():
            try:
                return np.load(npy_p)
            except Exception as e:
                logger.warning(f"Error cargando {npy_p}: {e}")
        return None

    def get_matrix(self, step: int, layer_type: str = "total") -> Optional[np.ndarray]:
        """Devuelve la matriz NumPy (950 x 1500) para el paso y capa solicitados."""
        if not self.current_manifest:
            self._load_latest_manifest_from_disk()
        if not self.current_manifest:
            return None

        cycle_str = self.current_manifest.get("cycle_str")
        if not cycle_str:
            return None

        prefix = "total" if layer_type == "total" else "interval"
        cache_key = f"{cycle_str}_{prefix}_{step:03d}"
        if cache_key in self._in_memory_arrays:
            return self._in_memory_arrays[cache_key]

        cycle_dir = self.cache_dir / cycle_str
        grid_base = cycle_dir / f"{prefix}_step_{step:03d}"
        arr = self._load_grid_file(grid_base)

        if arr is not None:
            if len(self._in_memory_arrays) > 30:
                self._in_memory_arrays.clear()
            self._in_memory_arrays[cache_key] = arr
            return arr

        # Fallback a ciclo anterior
        prev_manifest = self._get_previous_manifest()
        if prev_manifest and self.current_manifest.get("cycle") and prev_manifest.get("cycle"):
            try:
                latest_dt = datetime.fromisoformat(self.current_manifest["cycle"].replace("Z", "+00:00"))
                prev_dt = datetime.fromisoformat(prev_manifest["cycle"].replace("Z", "+00:00"))
                diff_hours = int(round((latest_dt - prev_dt).total_seconds() / 3600))
                prev_step = step + diff_hours
                prev_dir = self.cache_dir / prev_manifest["cycle_str"]
                prev_base = prev_dir / f"{prefix}_step_{prev_step:03d}"
                prev_arr = self._load_grid_file(prev_base)
                if prev_arr is not None:
                    return prev_arr
            except Exception:
                pass

        return None

    def get_value_at(self, lat: float, lon: float, step: int, layer_type: str = "total") -> float:
        """Consulta en O(1) (<1ms) el valor en mm para unas coordenadas lat/lon exactas."""
        if not (SPAIN_BBOX["lat_min"] <= lat <= SPAIN_BBOX["lat_max"] and
                SPAIN_BBOX["lon_min"] <= lon <= SPAIN_BBOX["lon_max"]):
            return 0.0

        arr = self.get_matrix(step=step, layer_type=layer_type)
        if arr is None:
            return 0.0

        try:
            y_m = R_EARTH * np.log(np.tan(np.pi / 4 + np.radians(lat) / 2))
            row = int(np.clip(round((Y_MERC_MAX - y_m) / (Y_MERC_MAX - Y_MERC_MIN) * (GRID_H - 1)), 0, GRID_H - 1))
            col = int(np.clip(round((lon - SPAIN_BBOX["lon_min"]) / (SPAIN_BBOX["lon_max"] - SPAIN_BBOX["lon_min"]) * (GRID_W - 1)), 0, GRID_W - 1))
            val = float(arr[row, col])
            return round(val, 2)
        except Exception as e:
            logger.error(f"Error consultando get_value_at UKMO ({lat}, {lon}): {e}")
            return 0.0

    def get_max_point(self, step: int, layer_type: str = "total") -> Optional[Dict[str, Any]]:
        """Calcula las coordenadas lat/lon exactas y el valor en mm del punto de máxima precipitación."""
        arr = self.get_matrix(step=step, layer_type=layer_type)
        if arr is None:
            return None

        try:
            max_val = float(np.nanmax(arr))
            if max_val < 0.1:
                return None

            idx = np.nanargmax(arr)
            row, col = np.unravel_index(idx, arr.shape)
            lat = float(SPAIN_GRID_LATS[row])
            lon = float(SPAIN_GRID_LONS[col])

            return {
                "lat": round(lat, 4),
                "lon": round(lon, 4),
                "val_mm": round(max_val, 1),
                "step": step,
                "type": layer_type
            }
        except Exception as e:
            logger.error(f"Error calculando get_max_point UKMO: {e}")
            return None

    def _determine_latest_ukmo_run(self) -> Tuple[datetime, str, str, str]:
        """
        Consulta S3 para determinar la última corrida disponible de UKMO Global.
        Retorna (cycle_dt, date_str, hh, s3_folder_prefix).
        """
        now = datetime.now(timezone.utc)
        months_to_check = [now.strftime("%Y%m")]
        # Si estamos en los primeros 2 días del mes, comprobar también el mes anterior
        if now.day <= 2:
            prev_month = (now.replace(day=1) - timedelta(days=1)).strftime("%Y%m")
            months_to_check.insert(0, prev_month)

        available_prefixes = []
        for ym in months_to_check:
            prefix = f"{self.s3_prefix}/{ym}"
            try:
                res = self.s3_client.list_objects_v2(
                    Bucket=self.s3_bucket,
                    Prefix=prefix,
                    Delimiter="/"
                )
                for cp in res.get("CommonPrefixes", []):
                    available_prefixes.append(cp.get("Prefix"))
            except Exception as e:
                logger.warning(f"UKMO: Error listando prefijos S3 para {prefix}: {e}")

        available_prefixes.sort()
        if not available_prefixes:
            raise RuntimeError("No se encontraron corridas de UKMO en S3.")

        latest_prefix = available_prefixes[-1]  # ej: "global-deterministic-10km/20261016T0000Z/"
        # Extraer fecha y hora: YYYYMMDDTHH00Z
        m = re.search(r'(\d{8})T(\d{2})00Z', latest_prefix)
        if not m:
            raise ValueError(f"Formato de prefijo UKMO desconocido: {latest_prefix}")

        date_str = m.group(1)
        hh = m.group(2)
        year = int(date_str[:4])
        month = int(date_str[4:6])
        day = int(date_str[6:8])
        cycle_dt = datetime(year, month, day, int(hh), 0, tzinfo=timezone.utc)

        return cycle_dt, date_str, hh, latest_prefix

    def _process_nc_to_grid(self, nc_path: Path) -> Optional[np.ndarray]:
        """
        Lee el archivo NetCDF HDF5 de UKMO, extrae latitudes, longitudes y precipitación (en m -> mm),
        recorta a la Península Ibérica e interpola sobre la cuadrícula Web Mercator estándar de RainLoc.
        """
        try:
            with h5py.File(nc_path, "r") as f:
                if "lwe_thickness_of_precipitation_amount" not in f:
                    logger.warning(f"UKMO: Variable de precipitación no encontrada en {nc_path}")
                    return None

                lats = f["latitude"][:]
                lons = f["longitude"][:]

                # Normalizar longitudes si fueran necesarias (están en [-180, 180])
                if np.any(lons > 180.0):
                    lons = np.where(lons > 180.0, lons - 360.0, lons)
                    sort_lon = np.argsort(lons)
                    lons = lons[sort_lon]

                # Margen de seguridad sobre SPAIN_BBOX: lat [33.5, 46.0], lon [-11.5, 6.5]
                lat_mask = (lats >= 33.5) & (lats <= 46.0)
                lon_mask = (lons >= -11.5) & (lons <= 6.5)

                sub_lats = lats[lat_mask]
                sub_lons = lons[lon_mask]

                # Leer porción indexada de precipitación (en metros) y convertir a mm
                raw_slice = f["lwe_thickness_of_precipitation_amount"][lat_mask, :][:, lon_mask] * 1000.0

            # Asegurar orden ascendente de latitudes para RegularGridInterpolator
            if sub_lats[0] > sub_lats[-1]:
                sub_lats = sub_lats[::-1]
                raw_slice = raw_slice[::-1, :]

            # Asegurar orden ascendente de longitudes
            if sub_lons[0] > sub_lons[-1]:
                sub_lons = sub_lons[::-1]
                raw_slice = raw_slice[:, ::-1]

            # Reemplazar NaNs y valores negativos
            raw_slice = np.nan_to_num(raw_slice, nan=0.0)
            raw_slice = np.maximum(raw_slice, 0.0)

            interp = RegularGridInterpolator(
                (sub_lats, sub_lons),
                raw_slice,
                bounds_error=False,
                fill_value=0.0,
                method="linear"
            )

            lat_grid, lon_grid = np.meshgrid(SPAIN_GRID_LATS, SPAIN_GRID_LONS, indexing="ij")
            grid_points = np.stack([lat_grid.ravel(), lon_grid.ravel()], axis=-1)
            interpolated_1d = interp(grid_points)
            grid_2d = interpolated_1d.reshape((GRID_H, GRID_W)).astype(np.float32)

            return np.maximum(grid_2d, 0.0)

        except Exception as e:
            logger.error(f"Error procesando NetCDF UKMO {nc_path}: {e}")
            return None

    async def sync_ukmo_forecast(self, max_steps: Optional[int] = None):
        """
        Sincroniza la previsión operativa de UKMO Global de forma progresiva.
        Descarga los ficheros NetCDF de acumulación desde AWS S3,
        calcula la precipitación acumulada total y el intervalo,
        genera los PNGs Web Mercator, guarda los .npz y notifica vía SSE.
        """
        if self._is_syncing:
            logger.info("UKMO: Sincronización ya en curso. Omitiendo llamada concurrente.")
            return

        async with self._sync_lock:
            self._is_syncing = True
            try:
                cycle_dt, date_str, hh, s3_folder = await asyncio.to_thread(self._determine_latest_ukmo_run)
                cycle_str = f"{date_str}_{hh}z"
                cycle_iso = cycle_dt.isoformat()
                cycle_dir = self.cache_dir / cycle_str
                cycle_dir.mkdir(parents=True, exist_ok=True)
                temp_nc_dir = cycle_dir / "temp_nc"
                temp_nc_dir.mkdir(parents=True, exist_ok=True)

                target_max_step = max_steps or getattr(settings, "UKMO_MAX_STEPS", 144)

                # Listar todos los ficheros de precipitación acumulada disponibles en S3 para esta corrida
                def list_s3_precip_files():
                    paginator = self.s3_client.get_paginator("list_objects_v2")
                    pages = paginator.paginate(Bucket=self.s3_bucket, Prefix=s3_folder)
                    step_map = {}
                    for p in pages:
                        for obj in p.get("Contents", []):
                            k = obj["Key"]
                            fn = k.split("/")[-1]
                            m = re.search(r'-PT(\d+)H00M-precipitation_accumulation-(PT\d+H)\.nc', fn)
                            if m:
                                s_h = int(m.group(1))
                                interval_code = m.group(2)
                                step_map[s_h] = (k, interval_code)
                    return step_map

                step_files_map = await asyncio.to_thread(list_s3_precip_files)
                all_available_s3_steps = sorted([s for s in step_files_map.keys() if s <= target_max_step])

                logger.info(f"UKMO: Detectado ciclo {cycle_str} en S3 con {len(all_available_s3_steps)} pasos disponibles (hasta +{target_max_step}h).")

                available_steps: List[int] = []
                step_entries: List[Dict[str, Any]] = []

                # Cargar manifiesto previo si existe
                manifest_file = cycle_dir / "manifest.json"
                if manifest_file.exists():
                    try:
                        with open(manifest_file, "r", encoding="utf-8") as f:
                            prev_manifest = json.load(f)
                            available_steps = prev_manifest.get("available_steps", [])
                            step_entries = prev_manifest.get("steps", [])
                    except Exception:
                        pass

                missing_steps = [s for s in all_available_s3_steps if s not in available_steps]
                if not missing_steps and all_available_s3_steps:
                    logger.info(f"UKMO: Todos los pasos de {cycle_str} ya están al día en caché.")
                else:
                    logger.info(f"UKMO: Pasos procesados: {len(available_steps)}/{len(all_available_s3_steps)}.")

                # Notificar inicio de sincronización
                self.notify_ukmo_update(cycle_str, available_steps, status="synchronizing", is_syncing=True)

                prev_raw_total: Optional[np.ndarray] = None

                for step in all_available_s3_steps:
                    total_png = cycle_dir / f"total_step_{step:03d}.png"
                    interval_png = cycle_dir / f"interval_step_{step:03d}.png"
                    total_npz = cycle_dir / f"total_step_{step:03d}.npz"
                    interval_npz = cycle_dir / f"interval_step_{step:03d}.npz"

                    # Si el paso ya está completo en disco
                    if total_png.exists() and interval_png.exists() and total_npz.exists() and interval_npz.exists() and step in available_steps:
                        prev_raw_total = self._load_grid_file(cycle_dir / f"total_step_{step:03d}")
                        continue

                    s3_key, interval_code = step_files_map[step]
                    step_nc = temp_nc_dir / f"ukmo_step_{step:03d}.nc"

                    logger.info(f"UKMO: Descargando paso +{step}h ({interval_code}) desde S3...")
                    def download_file_sync():
                        self.s3_client.download_file(self.s3_bucket, s3_key, str(step_nc))
                    await asyncio.to_thread(download_file_sync)

                    # Procesar e interpolar a cuadrícula Web Mercator
                    interval_grid = await asyncio.to_thread(self._process_nc_to_grid, step_nc)

                    if step_nc.exists():
                        step_nc.unlink()

                    if interval_grid is None:
                        logger.warning(f"UKMO: Fallo al procesar paso +{step}h.")
                        continue

                    # En UKMO, los ficheros de accumulation representan el intervalo acumulado
                    # Para el acumulado total desde t=0:
                    if prev_raw_total is None:
                        prior_steps = [s for s in available_steps if s < step]
                        if prior_steps:
                            prev_raw_total = self._load_grid_file(cycle_dir / f"total_step_{max(prior_steps):03d}")

                    if prev_raw_total is not None:
                        accum_total = np.maximum(prev_raw_total + interval_grid, 0.0)
                    else:
                        accum_total = np.maximum(interval_grid, 0.0)

                    prev_raw_total = accum_total.copy()

                    # Guardar matrices comprimidas .npz
                    np.savez_compressed(total_npz, data=accum_total.astype(np.float32))
                    np.savez_compressed(interval_npz, data=interval_grid.astype(np.float32))

                    # Generar imágenes PNG transparentes Web Mercator
                    rgba_total = colorize_precip_array(accum_total)
                    rgba_interval = colorize_precip_array(interval_grid)

                    img_total = Image.fromarray(rgba_total, mode="RGBA")
                    img_total.save(total_png, format="PNG", optimize=True)

                    img_interval = Image.fromarray(rgba_interval, mode="RGBA")
                    img_interval.save(interval_png, format="PNG", optimize=True)

                    # Registrar paso en metadatos
                    valid_time = cycle_dt + timedelta(hours=step)
                    valid_iso = valid_time.isoformat()
                    madrid_offset = timedelta(hours=2)
                    valid_local = (valid_time + madrid_offset).strftime("%d/%m %H:%M")

                    if step not in available_steps:
                        available_steps.append(step)
                        available_steps.sort()

                    step_entries = [e for e in step_entries if e["step"] != step]
                    step_entries.append({
                        "step": step,
                        "valid_time_iso": valid_iso,
                        "valid_time_local": valid_local,
                        "max_total_mm": round(float(np.nanmax(accum_total)), 1),
                        "max_interval_mm": round(float(np.nanmax(interval_grid)), 1)
                    })
                    step_entries.sort(key=lambda x: x["step"])

                    # Guardar manifest.json actualizado
                    cur_max = max(available_steps) if available_steps else 0
                    is_complete = (len(available_steps) >= len(all_available_s3_steps) and cur_max >= target_max_step)
                    manifest_data = {
                        "model": "UK Met Office (10 km)",
                        "cycle": cycle_iso,
                        "cycle_str": cycle_str,
                        "run": f"{hh}z",
                        "status": "complete" if is_complete else "ready",
                        "available_steps": available_steps,
                        "max_step": cur_max,
                        "downloaded_max_step": cur_max,
                        "raw_max_step": cur_max,
                        "is_complete": is_complete,
                        "bbox": SPAIN_BBOX,
                        "steps": step_entries,
                        "updated_at": datetime.now(timezone.utc).isoformat()
                    }
                    with open(manifest_file, "w", encoding="utf-8") as f:
                        json.dump(manifest_data, f, ensure_ascii=False, indent=2)

                    self.current_manifest = manifest_data

                    # Notificar clientes vía SSE
                    self.notify_ukmo_update(cycle_str, available_steps, step_added=step, status="ready", is_syncing=True)
                    logger.info(f"UKMO: Paso +{step}h procesado con éxito para {cycle_str}.")

                    del rgba_total, rgba_interval, img_total, img_interval
                    gc.collect()

                # Limpieza de carpeta temporal
                shutil.rmtree(temp_nc_dir, ignore_errors=True)
                self._cleanup_old_cycles()

                if available_steps:
                    final_max = max(available_steps)
                    is_complete = (len(available_steps) >= len(all_available_s3_steps) and final_max >= target_max_step)
                    final_status = "complete" if is_complete else "ready"
                    manifest_data = {
                        "model": "UK Met Office (10 km)",
                        "cycle": cycle_iso,
                        "cycle_str": cycle_str,
                        "run": f"{hh}z",
                        "status": final_status,
                        "available_steps": available_steps,
                        "max_step": final_max,
                        "downloaded_max_step": final_max,
                        "raw_max_step": final_max,
                        "is_complete": is_complete,
                        "bbox": SPAIN_BBOX,
                        "steps": step_entries,
                        "updated_at": datetime.now(timezone.utc).isoformat()
                    }
                    with open(manifest_file, "w", encoding="utf-8") as f:
                        json.dump(manifest_data, f, ensure_ascii=False, indent=2)

                    self.current_manifest = manifest_data
                    self.notify_ukmo_update(cycle_str, available_steps, status=final_status, is_syncing=False)
                    logger.info(f"UKMO: Sincronización completada para {cycle_str}. Pasos: {len(available_steps)}.")

            except Exception as e:
                logger.error(f"Error general en sync_ukmo_forecast: {e}", exc_info=True)
            finally:
                self._is_syncing = False

    def _cleanup_old_cycles(self, keep: int = 2):
        """Elimina del disco los ciclos de pronóstico anteriores para ahorrar espacio."""
        try:
            dirs = [d for d in self.cache_dir.iterdir() if d.is_dir() and (d / "manifest.json").exists()]
            if len(dirs) <= keep:
                return
            dirs.sort(key=lambda d: d.name, reverse=True)
            for old_dir in dirs[keep:]:
                logger.info(f"UKMO: Eliminando ciclo antiguo {old_dir.name}")
                shutil.rmtree(old_dir, ignore_errors=True)
        except Exception as e:
            logger.warning(f"Error en limpieza de ciclos antiguos de UKMO: {e}")


ukmo_worker = UKMOWorker()
