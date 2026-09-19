"""
RainLoc - Météo-France / AEMET AROME (1.3 km / 0.01°-0.025°) Open Data Worker & Pipeline
Ingesta secuencial y progresiva de previsiones de precipitación del modelo convectivo de alta resolución AROME.
Soporte para 8 corridas operativas diarias (00z, 03z, 06z, 09z, 12z, 15z, 18z, 21z) cada 3 horas, y pasos hasta +48h.
Descarga optimizada mediante Météo-France Open Data (S3 data.gouv.fr / OVHcloud PNT) con soporte para paquetes 
por bloques (00H06H, 07H12H, 13H18H, etc.) y archivos individuales.
Recorte geoespacial a Península Ibérica y Baleares, conversión de unidades (kg/m² -> mm),
cálculo de acumulados totales e intervalos horarios (hasta +48h / 2 días), generación de
imágenes PNG transparentes Web Mercator y matrices .npy para consultas instantáneas (0ms).
"""
import os
import gc
import json
import time
import shutil
import logging
import asyncio
import warnings
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional, List, Tuple, Set, AsyncGenerator

# Silenciar advertencias internas de combinación y compatibilidad de cfgrib/xarray
warnings.filterwarnings("ignore", category=FutureWarning, module="cfgrib")
warnings.filterwarnings("ignore", category=FutureWarning, module="xarray")
warnings.filterwarnings("ignore", message=".*compat.*", category=FutureWarning)
warnings.filterwarnings("ignore", category=UserWarning, module="cfgrib")
warnings.filterwarnings("ignore", message=".*Ignoring index file.*")

import numpy as np
from PIL import Image
from scipy.interpolate import RegularGridInterpolator

try:
    import xarray as xr
    XARRAY_AVAILABLE = True
except ImportError:
    XARRAY_AVAILABLE = False

try:
    import cfgrib
    CFGRIB_AVAILABLE = True
except ImportError:
    CFGRIB_AVAILABLE = False

from app.config import settings

logger = logging.getLogger("rainloc-backend.arome")

# Bounding Box y Cuadrícula Web Mercator idéntica al radar, ECMWF y GFS para superposición perfecta en Leaflet
SPAIN_BBOX = {"lat_min": 35.0, "lat_max": 44.5, "lon_min": -10.0, "lon_max": 5.0}
R_EARTH = 6378137.0
Y_MERC_MIN = R_EARTH * np.log(np.tan(np.pi / 4 + np.radians(SPAIN_BBOX["lat_min"]) / 2))
Y_MERC_MAX = R_EARTH * np.log(np.tan(np.pi / 4 + np.radians(SPAIN_BBOX["lat_max"]) / 2))
GRID_H = 950
GRID_W = 1500

Y_MERC_GRID = np.linspace(Y_MERC_MAX, Y_MERC_MIN, GRID_H)
SPAIN_GRID_LATS = np.degrees(2 * np.arctan(np.exp(Y_MERC_GRID / R_EARTH)) - np.pi / 2)
SPAIN_GRID_LONS = np.linspace(SPAIN_BBOX["lon_min"], SPAIN_BBOX["lon_max"], GRID_W)

# Pasos estándar de predicción AROME (cada 1 hora desde +1h hasta +48h / 2 días)
AROME_STEPS = list(range(1, 49))

# Endpoints oficiales de datos abiertos Météo-France / AROME
METEOFRANCE_OVH_S3 = "https://meteofrance-pnt.s3.rbx.io.cloud.ovh.net/pnt"
METEOFRANCE_DATAGOUV_PNT = "https://object.data.gouv.fr/meteofrance-pnt/pnt"
METEOFRANCE_DATAGOUV_PDS = "https://object.data.gouv.fr/meteofrance-pds/arome"


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


class AROMEWorker:
    """Worker para la descarga, transformación y servicio de datos de AROME (1.3km / alta resolución)."""

    def __init__(self):
        self.cache_dir: Path = settings.AROME_CACHE_DIR
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.current_manifest: Optional[Dict[str, Any]] = None
        self._sync_lock = asyncio.Lock()
        self._is_syncing: bool = False
        self._in_memory_arrays: Dict[str, np.ndarray] = {}
        self._subscribers: Set[asyncio.Queue] = set()
        self._load_latest_manifest_from_disk()

    def notify_arome_update(self, cycle_str: str, available_steps: List[int], step_added: Optional[int] = None, status: str = "ready", is_syncing: bool = False):
        """Notifica de forma inmediata y thread-safe a todos los clientes SSE conectados."""
        max_step = max(available_steps) if available_steps else 0
        run_str = ""
        if "_" in cycle_str:
            parts = cycle_str.split("_")
            if len(parts) > 1:
                run_str = parts[1].lower()
        if not run_str and len(cycle_str) >= 2:
            run_str = cycle_str[-2:].lower()

        max_target = getattr(settings, "AROME_MAX_STEPS", 48)
        payload = {
            "event": "arome_update",
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
        try:
            current_meta = self.get_metadata()
            initial_payload = {
                "event": "arome_init",
                "cycle_str": current_meta.get("cycle_str"),
                "run": current_meta.get("run"),
                "available_steps": current_meta.get("available_steps", []),
                "max_step": current_meta.get("max_step", 0),
                "is_complete": current_meta.get("is_complete", False),
                "is_updating": current_meta.get("is_updating", False),
                "status": current_meta.get("status", "empty"),
                "timestamp": current_meta.get("updated_at") or datetime.now(timezone.utc).isoformat()
            }
            yield initial_payload

            while True:
                try:
                    payload = await asyncio.wait_for(q.get(), timeout=25.0)
                    yield payload
                except asyncio.TimeoutError:
                    yield {"event": "ping"}
        finally:
            self._subscribers.discard(q)

    def _load_latest_manifest_from_disk(self):
        """Intenta cargar el último manifiesto existente en el disco al arrancar."""
        try:
            cycle_dirs = [d for d in self.cache_dir.iterdir() if d.is_dir() and (d / "manifest.json").exists()]
            if not cycle_dirs:
                return
            cycle_dirs.sort(key=lambda d: d.name, reverse=True)
            latest_dir = cycle_dirs[0]
            manifest_file = latest_dir / "manifest.json"
            with open(manifest_file, "r", encoding="utf-8") as f:
                self.current_manifest = json.load(f)
                logger.info(f"AROME: Manifiesto cargado desde disco para ciclo {self.current_manifest.get('cycle_str')}")
        except Exception as e:
            logger.warning(f"AROME: No se pudo cargar manifiesto previo: {e}")

    def _get_previous_manifest(self) -> Optional[Dict[str, Any]]:
        """Devuelve el manifiesto del ciclo inmediatamente anterior para hibridación de pasos futuros."""
        try:
            dirs = [d for d in self.cache_dir.iterdir() if d.is_dir() and (d / "manifest.json").exists()]
            if len(dirs) < 2:
                return None
            dirs.sort(key=lambda d: d.name, reverse=True)
            prev_dir = dirs[1]
            with open(prev_dir / "manifest.json", "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
        return None

    def get_metadata(self) -> Dict[str, Any]:
        """
        Devuelve los metadatos del ciclo actual con hibridación temporal inteligente (Stitching):
        Si la corrida actual sólo tiene los primeros pasos (ej: +1h..+6h), rellena los pasos futuros
        restantes (+7h..+48h) a partir de la corrida anterior para que el usuario nunca pierda horizonte.
        """
        max_target = getattr(settings, "AROME_MAX_STEPS", 48)
        if not self.current_manifest:
            self._load_latest_manifest_from_disk()

        if self.current_manifest:
            meta = dict(self.current_manifest)
            meta["is_syncing"] = self._is_syncing

            cycle_iso = meta.get("cycle")
            cycle_str = meta.get("cycle_str", "")
            run_str = ""
            if cycle_iso:
                try:
                    dt = datetime.fromisoformat(cycle_iso.replace("Z", "+00:00"))
                    run_str = f"{dt.hour:02d}z"
                except Exception:
                    pass
            if not run_str and "_" in cycle_str:
                parts = cycle_str.split("_")
                if len(parts) > 1:
                    run_str = parts[1].lower()

            meta["run"] = run_str or "00z"
            avail = list(meta.get("available_steps", []))
            steps_dict = {s["step"]: s for s in meta.get("steps", [])}

            # Si la corrida no está completa, intentar rellenar los pasos futuros desde el ciclo anterior
            if len(avail) < max_target and cycle_iso:
                prev_manifest = self._get_previous_manifest()
                if prev_manifest and prev_manifest.get("cycle") and prev_manifest.get("steps"):
                    try:
                        latest_dt = datetime.fromisoformat(cycle_iso.replace("Z", "+00:00"))
                        prev_dt = datetime.fromisoformat(prev_manifest["cycle"].replace("Z", "+00:00"))
                        diff_hours = int(round((latest_dt - prev_dt).total_seconds() / 3600))
                        prev_steps_dict = {s["step"]: s for s in prev_manifest.get("steps", [])}

                        for s in AROME_STEPS:
                            if s <= max_target and s not in steps_dict:
                                prev_s = s + diff_hours
                                if prev_s in prev_steps_dict:
                                    prev_item = prev_steps_dict[prev_s]
                                    valid_dt = latest_dt + timedelta(hours=s)
                                    steps_dict[s] = {
                                        "step": s,
                                        "valid_time_iso": valid_dt.isoformat(),
                                        "valid_time_local": (valid_dt + timedelta(hours=2)).strftime("%d/%m %H:%M"),
                                        "max_total_mm": prev_item.get("max_total_mm"),
                                        "max_interval_mm": prev_item.get("max_interval_mm"),
                                        "is_fallback": True,
                                        "fallback_cycle": prev_manifest.get("cycle_str"),
                                        "fallback_step": prev_s
                                    }
                                    if s not in avail:
                                        avail.append(s)
                    except Exception as e:
                        logger.debug(f"AROME stitching calculation error: {e}")

            avail.sort()
            sorted_steps = [steps_dict[s] for s in avail if s in steps_dict]
            meta["available_steps"] = avail
            meta["steps"] = sorted_steps
            max_step = max(avail) if avail else 0
            meta["max_step"] = max_step
            raw_avail = list(self.current_manifest.get("available_steps", []))
            raw_max_step = max(raw_avail) if raw_avail else 0
            raw_complete = (len(raw_avail) >= max_target)
            meta["raw_available_steps"] = raw_avail
            meta["downloaded_max_step"] = raw_max_step
            meta["raw_max_step"] = raw_max_step
            meta["is_complete"] = raw_complete
            meta["is_updating"] = self._is_syncing or (not raw_complete and self.current_manifest.get("status") != "complete")
            return meta

        return {
            "model": "Météo-France AROME",
            "cycle": None,
            "cycle_str": None,
            "run": None,
            "status": "synchronizing" if self._is_syncing else "empty",
            "available_steps": [],
            "max_step": 0,
            "is_complete": False,
            "is_updating": self._is_syncing,
            "bbox": SPAIN_BBOX,
            "steps": [],
            "updated_at": None,
            "is_syncing": self._is_syncing
        }

    def get_image_path(self, step: int, layer_type: str = "total") -> Optional[Path]:
        """Devuelve la ruta al archivo PNG del paso y tipo solicitados (con soporte de fallback a ciclo anterior)."""
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

        # Fallback a ciclo anterior si este paso aún no está resuelto
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

    def get_value_at(self, lat: float, lon: float, step: int, layer_type: str = "total") -> Optional[float]:
        """Consulta instantánea (0ms) en la matriz NumPy en memoria o disco (con fallback a ciclo anterior)."""
        if not (SPAIN_BBOX["lat_min"] <= lat <= SPAIN_BBOX["lat_max"] and SPAIN_BBOX["lon_min"] <= lon <= SPAIN_BBOX["lon_max"]):
            return None

        if not self.current_manifest:
            self._load_latest_manifest_from_disk()
        if not self.current_manifest:
            return None

        cycle_str = self.current_manifest.get("cycle_str")
        prefix = "total" if layer_type == "total" else "interval"
        mem_key = f"{cycle_str}_{prefix}_{step:03d}"

        arr = self._in_memory_arrays.get(mem_key)
        if arr is None:
            cycle_dir = self.cache_dir / cycle_str
            npy_path = cycle_dir / f"{prefix}_step_{step:03d}.npy"
            if npy_path.exists():
                try:
                    arr = np.load(npy_path)
                    self._in_memory_arrays[mem_key] = arr
                except Exception as e:
                    logger.warning(f"Error leyendo npy {npy_path}: {e}")

        # Fallback a ciclo anterior si aún no está resuelto en el actual
        if arr is None:
            prev_manifest = self._get_previous_manifest()
            if prev_manifest and self.current_manifest.get("cycle") and prev_manifest.get("cycle"):
                try:
                    latest_dt = datetime.fromisoformat(self.current_manifest["cycle"].replace("Z", "+00:00"))
                    prev_dt = datetime.fromisoformat(prev_manifest["cycle"].replace("Z", "+00:00"))
                    diff_hours = int(round((latest_dt - prev_dt).total_seconds() / 3600))
                    prev_step = step + diff_hours
                    prev_cycle_str = prev_manifest["cycle_str"]
                    prev_mem_key = f"{prev_cycle_str}_{prefix}_{prev_step:03d}"
                    arr = self._in_memory_arrays.get(prev_mem_key)
                    if arr is None:
                        prev_dir = self.cache_dir / prev_cycle_str
                        prev_npy = prev_dir / f"{prefix}_step_{prev_step:03d}.npy"
                        if prev_npy.exists():
                            arr = np.load(prev_npy)
                            self._in_memory_arrays[prev_mem_key] = arr
                except Exception:
                    pass

        if arr is None:
            return None

        y_merc = R_EARTH * np.log(np.tan(np.pi / 4 + np.radians(lat) / 2))
        y_idx = int(round((Y_MERC_MAX - y_merc) / (Y_MERC_MAX - Y_MERC_MIN) * (GRID_H - 1)))
        x_idx = int(round((lon - SPAIN_BBOX["lon_min"]) / (SPAIN_BBOX["lon_max"] - SPAIN_BBOX["lon_min"]) * (GRID_W - 1)))

        y_idx = max(0, min(GRID_H - 1, y_idx))
        x_idx = max(0, min(GRID_W - 1, x_idx))

        val = float(arr[y_idx, x_idx])
        return round(val, 2)

    def get_max_point(self, step: int, layer_type: str = "total") -> Optional[Dict[str, Any]]:
        """Calcula las coordenadas lat/lon exactas y el valor en mm del punto de máxima precipitación de AROME."""
        if not self.current_manifest:
            self._load_latest_manifest_from_disk()
        if not self.current_manifest:
            return None

        cycle_str = self.current_manifest.get("cycle_str")
        if not cycle_str:
            return None

        prefix = "total" if layer_type == "total" else "interval"
        mem_key = f"{cycle_str}_{prefix}_{step:03d}"

        arr = self._in_memory_arrays.get(mem_key)
        if arr is None:
            cycle_dir = self.cache_dir / cycle_str
            npy_path = cycle_dir / f"{prefix}_step_{step:03d}.npy"
            if npy_path.exists():
                try:
                    arr = np.load(npy_path)
                    self._in_memory_arrays[mem_key] = arr
                except Exception as e:
                    logger.warning(f"Error leyendo npy {npy_path}: {e}")

        # Fallback a ciclo anterior si aún no está resuelto
        if arr is None:
            prev_manifest = self._get_previous_manifest()
            if prev_manifest and self.current_manifest.get("cycle") and prev_manifest.get("cycle"):
                try:
                    latest_dt = datetime.fromisoformat(self.current_manifest["cycle"].replace("Z", "+00:00"))
                    prev_dt = datetime.fromisoformat(prev_manifest["cycle"].replace("Z", "+00:00"))
                    diff_hours = int(round((latest_dt - prev_dt).total_seconds() / 3600))
                    prev_step = step + diff_hours
                    prev_cycle_str = prev_manifest["cycle_str"]
                    prev_mem_key = f"{prev_cycle_str}_{prefix}_{prev_step:03d}"
                    arr = self._in_memory_arrays.get(prev_mem_key)
                    if arr is None:
                        prev_dir = self.cache_dir / prev_cycle_str
                        prev_npy = prev_dir / f"{prefix}_step_{prev_step:03d}.npy"
                        if prev_npy.exists():
                            arr = np.load(prev_npy)
                            self._in_memory_arrays[prev_mem_key] = arr
                except Exception:
                    pass

        if arr is None:
            return None

        try:
            max_idx = np.unravel_index(np.argmax(arr), arr.shape)
            val = float(arr[max_idx])
            if val < 0.1:
                return {"lat": None, "lon": None, "value_mm": round(val, 2)}

            y_merc = Y_MERC_MAX - (max_idx[0] / (GRID_H - 1)) * (Y_MERC_MAX - Y_MERC_MIN)
            lat = float(np.degrees(2 * np.arctan(np.exp(y_merc / R_EARTH)) - np.pi / 2))
            lon = float(SPAIN_BBOX["lon_min"] + (max_idx[1] / (GRID_W - 1)) * (SPAIN_BBOX["lon_max"] - SPAIN_BBOX["lon_min"]))

            return {
                "lat": round(lat, 4),
                "lon": round(lon, 4),
                "value_mm": round(val, 2)
            }
        except Exception as e:
            logger.error(f"Error calculando max_point AROME: {e}")
            return None

    def _get_candidate_cycles(self) -> List[Tuple[datetime, str, str]]:
        """
        Genera los ciclos candidatos más recientes en orden cronológico descendente
        (8 corridas operativas diarias cada 3 horas: 00z, 03z, 06z, 09z, 12z, 15z, 18z, 21z).
        """
        now = datetime.now(timezone.utc)
        candidates = []
        # Comenzar desde hace 1 hora hasta 24 horas atrás en saltos de 3 horas
        ref_time = now - timedelta(hours=1, minutes=0)
        base_hour = (ref_time.hour // 3) * 3
        current_dt = ref_time.replace(hour=base_hour, minute=0, second=0, microsecond=0)

        for i in range(8):
            dt = current_dt - timedelta(hours=i * 3)
            date_str = dt.strftime("%Y%m%d")
            hh = f"{dt.hour:02d}"
            candidates.append((dt, date_str, hh))

        return candidates

    def _get_chunk_name(self, step: int) -> str:
        """Determina el nombre del bloque de 6 horas de Météo-France según el paso de previsión."""
        if step <= 6:
            return "00H06H"
        elif step <= 12:
            return "07H12H"
        elif step <= 18:
            return "13H18H"
        elif step <= 24:
            return "19H24H"
        elif step <= 30:
            return "25H30H"
        elif step <= 36:
            return "31H36H"
        elif step <= 42:
            return "37H42H"
        else:
            return "43H48H"

    def _download_arome_chunk_or_step(self, date_str: str, hh: str, chunk: str, step: int, dest_path: Path) -> bool:
        """
        Descarga el bloque o paso de precipitación AROME desde los repositorios oficiales de datos abiertos.
        Prueba ordenadamente los paquetes de superficie con precipitación (SP2) en OVHcloud S3, data.gouv.fr PNT y data.gouv.fr PDS.
        """
        step_str = f"{step:02d}"
        year = date_str[0:4]
        month = date_str[4:6]
        day = date_str[6:8]
        iso_date = f"{year}-{month}-{day}"

        candidate_urls = [
            # 1. Météo-France PNT en OVHcloud (Pasos horarios SP2 0.01° - tirf, tsnowp, tgrp)
            f"{METEOFRANCE_OVH_S3}/{iso_date}T{hh}:00:00Z/arome/001/SP2/arome__001__SP2__{step_str}H__{iso_date}T{hh}:00:00Z.grib2",
            # 2. Météo-France PNT en OVHcloud (Pasos horarios SP2 0.025°)
            f"{METEOFRANCE_OVH_S3}/{iso_date}T{hh}:00:00Z/arome/0025/SP2/arome__0025__SP2__{step_str}H__{iso_date}T{hh}:00:00Z.grib2",
            # 3. Data.gouv.fr PNT Mirror (SP2 0.01° / 0.025°)
            f"{METEOFRANCE_DATAGOUV_PNT}/{iso_date}T{hh}:00:00Z/arome/001/SP2/arome__001__SP2__{step_str}H__{iso_date}T{hh}:00:00Z.grib2",
            f"{METEOFRANCE_DATAGOUV_PNT}/{iso_date}T{hh}:00:00Z/arome/0025/SP2/arome__0025__SP2__{step_str}H__{iso_date}T{hh}:00:00Z.grib2",
            # 4. Formato bloques 6h si existiesen en algún mirror
            f"{METEOFRANCE_OVH_S3}/{iso_date}T{hh}:00:00Z/arome/001/SP2/arome__001__SP2__{chunk}__{iso_date}T{hh}:00:00Z.grib2",
            f"{METEOFRANCE_OVH_S3}/{iso_date}T{hh}:00:00Z/arome/0025/SP2/arome__0025__SP2__{chunk}__{iso_date}T{hh}:00:00Z.grib2",
            # 5. Data.gouv.fr PDS Format (SP2)
            f"{METEOFRANCE_DATAGOUV_PDS}/0.01/SP2/{iso_date}T{hh}:00:00Z/arome__0.01__SP2__{iso_date}T{hh}:00:00Z__{step_str}H.grib2",
            f"{METEOFRANCE_DATAGOUV_PDS}/0.025/SP2/{iso_date}T{hh}:00:00Z/arome__0.025__SP2__{iso_date}T{hh}:00:00Z__{step_str}H.grib2",
        ]

        headers = {
            "User-Agent": "RainLoc-GIS/1.0 (+https://github.com/carlosalventosa/RainLoc)",
            "Accept": "*/*"
        }

        for url in candidate_urls:
            try:
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=15) as resp:
                    if resp.status == 200:
                        data = resp.read()
                        if len(data) > 1000:  # Archivo GRIB válido
                            with open(dest_path, "wb") as f:
                                f.write(data)
                            return True
            except Exception as e:
                logger.debug(f"AROME URL intento fallido ({url}): {e}")

        return False

    def _process_grib_to_grid(self, grib_file: Path, target_step: Optional[int] = None) -> Optional[np.ndarray]:
        """
        Lee el archivo GRIB2 de precipitación AROME (sea de paso único o de bloque 6h),
        extrae el paso temporal solicitado e interpola sobre la cuadrícula Web Mercator SPAIN_GRID_LATS / SPAIN_GRID_LONS.
        """
        if not XARRAY_AVAILABLE or not CFGRIB_AVAILABLE:
            logger.error("xarray o cfgrib no están disponibles para procesar GRIB2 de AROME.")
            return None

        datasets = []
        try:
            # 1. Abrir todos los datasets contenidos en el archivo GRIB2
            try:
                datasets = cfgrib.open_datasets(grib_file, backend_kwargs={"indexpath": ""})
            except Exception:
                try:
                    ds = xr.open_dataset(grib_file, engine="cfgrib", backend_kwargs={"indexpath": ""})
                    datasets = [ds]
                except Exception:
                    ds = xr.open_dataset(grib_file, engine="cfgrib", backend_kwargs={"indexpath": "", "filter_by_keys": {"typeOfLevel": "surface"}})
                    datasets = [ds]

            if not datasets:
                logger.warning(f"No se pudieron extraer datasets de {grib_file}")
                return None

            target_da = None
            target_ds = None

            # 2. Buscar variable de precipitación en todos los sub-datasets
            # En AROME SP2: tirf = lluvia acumulada, tsnowp = nieve, tgrp = graupel
            for ds in datasets:
                if "tirf" in ds.data_vars:
                    target_da = ds["tirf"].copy()
                    if "tsnowp" in ds.data_vars:
                        target_da = target_da + ds["tsnowp"]
                    if "tgrp" in ds.data_vars:
                        target_da = target_da + ds["tgrp"]
                    target_ds = ds
                    break
                elif "tp" in ds.data_vars:
                    target_da = ds["tp"]
                    target_ds = ds
                    break
                elif "total_precipitation" in ds.data_vars:
                    target_da = ds["total_precipitation"]
                    target_ds = ds
                    break
                elif "apcp" in ds.data_vars:
                    target_da = ds["apcp"]
                    target_ds = ds
                    break
                elif "lsp" in ds.data_vars and "cp" in ds.data_vars:
                    target_da = ds["lsp"] + ds["cp"]
                    target_ds = ds
                    break

            # Si ninguna coincide con la lista anterior, buscar por nombres alternativos
            if target_da is None:
                candidate_vars = ["precipitation", "precip", "tirf", "lsp", "cp", "r", "paramId_0", "unknown"]
                for ds in datasets:
                    for cand in candidate_vars:
                        if cand in ds.data_vars:
                            target_da = ds[cand]
                            target_ds = ds
                            break
                    if target_da is not None:
                        break

            # Si aún no se encuentra, tomar la primera variable disponible
            if target_da is None:
                for ds in datasets:
                    var_names = list(ds.data_vars.keys())
                    if var_names:
                        target_da = ds[var_names[0]]
                        target_ds = ds
                        logger.info(f"AROME: Usando variable {var_names[0]} encontrada en {grib_file}")
                        break

            if target_da is None or target_ds is None:
                logger.warning(f"No se encontró variable de precipitación en {grib_file}")
                for ds in datasets:
                    ds.close()
                return None

            da = target_da

            # 3. Si el dataset contiene la dimensión 'step', seleccionar el paso requerido
            if "step" in da.dims and target_step is not None:
                try:
                    target_td = np.timedelta64(target_step, 'h')
                    if target_td in da['step'].values:
                        da = da.sel(step=target_td)
                    else:
                        step_vals = [int(s / np.timedelta64(1, 'h')) if isinstance(s, np.timedelta64) else int(s) for s in da['step'].values]
                        if target_step in step_vals:
                            idx = step_vals.index(target_step)
                            da = da.isel(step=idx)
                except Exception as e:
                    logger.debug(f"AROME indexación por paso en dataset xarray: {e}")

            # 4. Extraer coordenadas
            lat_key = "latitude" if "latitude" in target_ds.coords else "lat"
            lon_key = "longitude" if "longitude" in target_ds.coords else "lon"
            lats = target_ds[lat_key].values
            lons = target_ds[lon_key].values

            # Normalizar longitudes [-180, 180]
            if np.any(lons > 180.0):
                lons = np.where(lons > 180.0, lons - 360.0, lons)
                sort_idx = np.argsort(lons)
                lons = lons[sort_idx]
                vals = da.values[:, sort_idx] if da.values.ndim == 2 else da.values[sort_idx]
            else:
                vals = da.values

            if lats[0] > lats[-1]:
                lats = lats[::-1]
                vals = vals[::-1, :] if vals.ndim == 2 else vals

            # Cerrar todos los datasets
            for ds in datasets:
                try:
                    ds.close()
                except Exception:
                    pass

            if vals.ndim > 2:
                vals = vals.squeeze()
            if vals.ndim > 2:
                vals = vals[-1]

            vals = np.nan_to_num(vals, nan=0.0)
            vals = np.maximum(vals, 0.0)

            interp = RegularGridInterpolator(
                (lats, lons),
                vals,
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
            logger.error(f"Error procesando GRIB2 AROME con cfgrib/xarray: {e}")
            for ds in datasets:
                try:
                    ds.close()
                except Exception:
                    pass
            return None

    async def sync_arome_forecast(self, max_steps: Optional[int] = None):
        """
        Sincroniza la previsión operativa de AROME de forma progresiva.
        Comprueba las salidas operativas disponibles (00z, 03z, 06z, 09z, 12z, 15z, 18z, 21z).
        Por cada paso descargado y procesado, genera el PNG Web Mercator,
        guarda la matriz .npy y notifica vía SSE en tiempo real a los clientes.
        """
        if self._is_syncing:
            logger.info("AROME: Sincronización ya en curso. Omitiendo llamada concurrente.")
            return

        async with self._sync_lock:
            self._is_syncing = True
            try:
                candidate_cycles = self._get_candidate_cycles()
                selected_cycle = None
                cycle_dir = None
                temp_grib_dir = None
                target_max_step = max_steps or getattr(settings, "AROME_MAX_STEPS", 48)
                steps_to_process = [s for s in AROME_STEPS if s <= target_max_step]

                # 1. Encontrar el ciclo operativo más reciente con datos disponibles
                for cycle_dt, date_str, hh in candidate_cycles:
                    test_cycle_str = f"{date_str}_{hh}z"
                    test_cycle_dir = self.cache_dir / test_cycle_str
                    test_manifest = test_cycle_dir / "manifest.json"

                    # Si ya está completamente procesado en disco
                    if test_manifest.exists():
                        try:
                            with open(test_manifest, "r", encoding="utf-8") as f:
                                meta = json.load(f)
                                if meta.get("available_steps") and len(meta.get("available_steps")) > 0:
                                    selected_cycle = (cycle_dt, date_str, hh)
                                    cycle_dir = test_cycle_dir
                                    break
                        except Exception:
                            pass

                    # Comprobar disponibilidad descargando el primer paso
                    test_temp_dir = test_cycle_dir / "temp_grib"
                    test_temp_dir.mkdir(parents=True, exist_ok=True)
                    first_chunk = self._get_chunk_name(1)
                    test_grib = test_temp_dir / "arome_step_01.grib2"

                    downloaded = await asyncio.to_thread(
                        self._download_arome_chunk_or_step, date_str, hh, first_chunk, 1, test_grib
                    )

                    if downloaded:
                        logger.info(f"AROME: Salida disponible encontrada: ciclo {test_cycle_str}")
                        selected_cycle = (cycle_dt, date_str, hh)
                        cycle_dir = test_cycle_dir
                        temp_grib_dir = test_temp_dir
                        break
                    else:
                        shutil.rmtree(test_temp_dir, ignore_errors=True)
                        logger.debug(f"AROME: Ciclo {test_cycle_str} aún no disponible, probando anterior...")

                if not selected_cycle or not cycle_dir:
                    logger.warning("AROME: No se encontraron salidas operativas disponibles en los repositorios S3.")
                    return

                cycle_dt, date_str, hh = selected_cycle
                cycle_str = f"{date_str}_{hh}z"
                cycle_iso = cycle_dt.isoformat()
                cycle_dir.mkdir(parents=True, exist_ok=True)
                if not temp_grib_dir:
                    temp_grib_dir = cycle_dir / "temp_grib"
                    temp_grib_dir.mkdir(parents=True, exist_ok=True)

                logger.info(f"AROME: Iniciando sincronización para ciclo {cycle_str} (hasta +{target_max_step}h)...")

                available_steps: List[int] = []
                step_entries: List[Dict[str, Any]] = []

                manifest_file = cycle_dir / "manifest.json"
                if manifest_file.exists():
                    try:
                        with open(manifest_file, "r", encoding="utf-8") as f:
                            prev_manifest = json.load(f)
                            available_steps = prev_manifest.get("available_steps", [])
                            step_entries = prev_manifest.get("steps", [])
                    except Exception:
                        pass

                missing_steps = [s for s in steps_to_process if s not in available_steps]
                if not missing_steps:
                    logger.info(f"AROME: Todos los pasos (+{min(steps_to_process)}h..+{max(steps_to_process)}h) ya están al día en caché para el ciclo {cycle_str}.")
                else:
                    logger.info(f"AROME: Pasos disponibles: {len(available_steps)}/{len(steps_to_process)}. Comprobando si el supercomputador ha publicado el paso +{missing_steps[0]}h...")

                self.notify_arome_update(cycle_str, available_steps, status="synchronizing", is_syncing=True)

                prev_raw_grid: Optional[np.ndarray] = None
                if available_steps:
                    last_step = max(available_steps)
                    npy_path = cycle_dir / f"total_step_{last_step:03d}.npy"
                    if npy_path.exists():
                        try:
                            prev_raw_grid = np.load(npy_path)
                        except Exception:
                            pass

                for step in steps_to_process:
                    total_png = cycle_dir / f"total_step_{step:03d}.png"
                    interval_png = cycle_dir / f"interval_step_{step:03d}.png"
                    total_npy = cycle_dir / f"total_step_{step:03d}.npy"
                    interval_npy = cycle_dir / f"interval_step_{step:03d}.npy"

                    # Si ya está procesado este paso en disco, saltar
                    if total_png.exists() and interval_png.exists() and total_npy.exists() and interval_npy.exists() and step in available_steps:
                        try:
                            prev_raw_grid = np.load(total_npy)
                        except Exception:
                            pass
                        continue

                    # Descargar paso individual
                    step_grib = temp_grib_dir / f"arome_step_{step:02d}.grib2"
                    chunk_name = self._get_chunk_name(step)

                    if not step_grib.exists():
                        logger.info(f"AROME: Comprobando disponibilidad del paso +{step}h en S3...")
                        downloaded = await asyncio.to_thread(
                            self._download_arome_chunk_or_step, date_str, hh, chunk_name, step, step_grib
                        )
                        if not downloaded:
                            logger.info(f"AROME: Paso +{step}h aún no disponible para ciclo {cycle_str}.")
                            break
                        logger.info(f"AROME: Paso +{step}h descargado con éxito. Procesando ráster...")

                    # Procesar e interpolar cuadrícula
                    grid_2d = await asyncio.to_thread(self._process_grib_to_grid, step_grib, step)

                    # Limpiar archivo temporal GRIB tras procesar
                    step_grib.unlink(missing_ok=True)

                    if grid_2d is None:
                        logger.warning(f"AROME: No se pudo procesar matriz para paso +{step}h.")
                        continue

                    # En AROME, la precipitación en HP1/SP1 es acumulada continua desde t0
                    accum_total = np.maximum(grid_2d, 0.0)
                    if prev_raw_grid is not None:
                        if np.nanmean(accum_total) >= np.nanmean(prev_raw_grid):
                            interval_1h = np.maximum(accum_total - prev_raw_grid, 0.0)
                        else:
                            interval_1h = np.maximum(accum_total, 0.0)
                            accum_total = prev_raw_grid + interval_1h
                    else:
                        interval_1h = accum_total.copy()

                    prev_raw_grid = accum_total.copy()

                    # Guardar matrices NumPy .npy
                    np.save(total_npy, accum_total)
                    np.save(interval_npy, interval_1h)

                    # Colorear y guardar imágenes PNG transparentes Web Mercator
                    rgba_total = colorize_precip_array(accum_total)
                    rgba_interval = colorize_precip_array(interval_1h)

                    img_total = Image.fromarray(rgba_total, mode="RGBA")
                    img_total.save(total_png, format="PNG", optimize=True)

                    img_interval = Image.fromarray(rgba_interval, mode="RGBA")
                    img_interval.save(interval_png, format="PNG", optimize=True)

                    # Registrar paso
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
                        "max_interval_mm": round(float(np.nanmax(interval_1h)), 1)
                    })
                    step_entries.sort(key=lambda x: x["step"])

                    # Actualizar manifest.json
                    is_complete = len(available_steps) >= len(steps_to_process)
                    manifest_data = {
                        "model": "Météo-France AROME",
                        "cycle": cycle_iso,
                        "cycle_str": cycle_str,
                        "run": f"{hh}z",
                        "status": "complete" if is_complete else "ready",
                        "available_steps": available_steps,
                        "max_step": max(available_steps) if available_steps else 0,
                        "is_complete": is_complete,
                        "bbox": SPAIN_BBOX,
                        "steps": step_entries,
                        "updated_at": datetime.now(timezone.utc).isoformat()
                    }
                    with open(manifest_file, "w", encoding="utf-8") as f:
                        json.dump(manifest_data, f, ensure_ascii=False, indent=2)

                    self.current_manifest = manifest_data

                    # Notificar a los clientes vía SSE
                    self.notify_arome_update(cycle_str, available_steps, step_added=step, status="ready", is_syncing=True)
                    logger.info(f"AROME: Paso +{step}h procesado y emitido vía SSE para {cycle_str}.")

                    del rgba_total, rgba_interval, img_total, img_interval
                    gc.collect()

                shutil.rmtree(temp_grib_dir, ignore_errors=True)
                self._cleanup_old_cycles()

                final_status = "complete" if (available_steps and max(available_steps) >= target_max_step) else "ready"
                if self.current_manifest:
                    self.current_manifest["status"] = final_status
                    with open(manifest_file, "w", encoding="utf-8") as f:
                        json.dump(self.current_manifest, f, ensure_ascii=False, indent=2)

                self.notify_arome_update(cycle_str, available_steps, status=final_status, is_syncing=False)
                logger.info(f"AROME: Sincronización finalizada para {cycle_str}. Pasos totales: {len(available_steps)}.")

            except Exception as e:
                logger.error(f"Error general en sync_arome_forecast: {e}", exc_info=True)
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
                logger.info(f"AROME: Eliminando ciclo antiguo {old_dir.name}")
                shutil.rmtree(old_dir, ignore_errors=True)
        except Exception as e:
            logger.warning(f"Error en limpieza de ciclos antiguos de AROME: {e}")


arome_worker = AROMEWorker()
