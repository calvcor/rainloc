"""
RainLoc - DWD ICON-EU (0.0625° / ~6.5 km) Open Data Worker & Pipeline
Ingesta secuencial y progresiva de previsiones de precipitación del modelo regional ICON-EU del DWD.
Soporte para 8 corridas operativas diarias (00z, 03z, 06z, 09z, 12z, 15z, 18z, 21z) cada 3 horas, y pasos hasta +120h.
Descarga optimizada directa desde DWD Open Data con descompresión bzip2 (.bz2) ultrarrápida en memoria.
Recorte geoespacial a Península Ibérica y Baleares, conversión de unidades (kg/m² -> mm),
cálculo de acumulados totales e intervalos (hasta +120h / 5 días), generación de
imágenes PNG transparentes Web Mercator y matrices .npz/.npy para consultas instantáneas (0ms).
"""
import os
import gc
import bz2
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

logger = logging.getLogger("rainloc-backend.icon")

# Bounding Box y Cuadrícula Web Mercator idéntica al radar, ECMWF, GFS y AROME para superposición perfecta en Leaflet
SPAIN_BBOX = {"lat_min": 35.0, "lat_max": 44.5, "lon_min": -10.0, "lon_max": 5.0}
R_EARTH = 6378137.0
Y_MERC_MIN = R_EARTH * np.log(np.tan(np.pi / 4 + np.radians(SPAIN_BBOX["lat_min"]) / 2))
Y_MERC_MAX = R_EARTH * np.log(np.tan(np.pi / 4 + np.radians(SPAIN_BBOX["lat_max"]) / 2))
GRID_H = 950
GRID_W = 1500

Y_MERC_GRID = np.linspace(Y_MERC_MAX, Y_MERC_MIN, GRID_H)
SPAIN_GRID_LATS = np.degrees(2 * np.arctan(np.exp(Y_MERC_GRID / R_EARTH)) - np.pi / 2)
SPAIN_GRID_LONS = np.linspace(SPAIN_BBOX["lon_min"], SPAIN_BBOX["lon_max"], GRID_W)

# Pasos estándar de predicción ICON-EU:
# Corridas principales (00, 06, 12, 18z): pasos horarios de 1 a 78h + trihorarios de 81 a 120h (hasta 5 días)
ICON_MAIN_STEPS = list(range(1, 79)) + list(range(81, 123, 3))
# Corridas intermedias (03, 09, 15, 21z): pasos horarios de 1 a 30h
ICON_SHORT_STEPS = list(range(1, 31))

# Endpoints oficiales de datos abiertos DWD ICON-EU
DWD_ICON_EU_BASE = "https://opendata.dwd.de/weather/nwp/icon-eu/grib"


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


class ICONWorker:
    """Worker para la descarga, transformación y servicio de datos de DWD ICON-EU (0.0625° / ~6.5 km)."""

    def __init__(self):
        self.cache_dir: Path = settings.ICON_CACHE_DIR
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.current_manifest: Optional[Dict[str, Any]] = None
        self._sync_lock = asyncio.Lock()
        self._is_syncing: bool = False
        self._in_memory_arrays: Dict[str, np.ndarray] = {}
        self._subscribers: Set[asyncio.Queue] = set()
        self._load_latest_manifest_from_disk()

    def notify_icon_update(self, cycle_str: str, available_steps: List[int], step_added: Optional[int] = None, status: str = "ready", is_syncing: bool = False):
        """Notifica de forma inmediata y thread-safe a todos los clientes SSE conectados."""
        max_step = max(available_steps) if available_steps else 0
        run_str = ""
        if "_" in cycle_str:
            parts = cycle_str.split("_")
            if len(parts) > 1:
                run_str = parts[1].lower()
        if not run_str and len(cycle_str) >= 2:
            run_str = cycle_str[-2:].lower()

        # Determinamos max_target según si es corrida principal o intermedia
        is_main_run = run_str in ("00z", "06z", "12z", "18z")
        default_max = 120 if is_main_run else 30
        max_target = getattr(settings, "ICON_MAX_STEPS", default_max)
        if not is_main_run and max_target > 30:
            max_target = 30

        payload = {
            "event": "icon_update",
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
                "event": "icon_init",
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
                logger.info(f"ICON: Manifiesto cargado desde disco para ciclo {self.current_manifest.get('cycle_str')}")
        except Exception as e:
            logger.warning(f"ICON: No se pudo cargar manifiesto previo: {e}")

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
        Si la corrida actual sólo tiene los primeros pasos, rellena los pasos futuros
        restantes a partir de la corrida anterior para que el usuario nunca pierda horizonte.
        """
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
            steps_dict = {s["step"]: dict(s) for s in meta.get("steps", [])}
            # Asegurar que los pasos nativos tengan el tag de run
            for s_key, s_data in steps_dict.items():
                if "run" not in s_data:
                    s_data["run"] = run_str
                if "is_fallback" not in s_data:
                    s_data["is_fallback"] = False

            is_main = run_str in ("00z", "06z", "12z", "18z")
            target_steps = ICON_MAIN_STEPS if is_main else ICON_SHORT_STEPS
            max_target = getattr(settings, "ICON_MAX_STEPS", 120 if is_main else 30)

            # Si la corrida no está completa, intentar rellenar los pasos futuros desde el ciclo anterior
            if len(avail) < len([s for s in target_steps if s <= max_target]) and cycle_iso:
                prev_manifest = self._get_previous_manifest()
                if prev_manifest and prev_manifest.get("cycle") and prev_manifest.get("steps"):
                    try:
                        latest_dt = datetime.fromisoformat(cycle_iso.replace("Z", "+00:00"))
                        prev_dt = datetime.fromisoformat(prev_manifest["cycle"].replace("Z", "+00:00"))
                        diff_hours = int(round((latest_dt - prev_dt).total_seconds() / 3600))
                        prev_steps_dict = {s["step"]: s for s in prev_manifest.get("steps", [])}
                        prev_run = prev_manifest.get("run") or ""
                        if not prev_run and prev_manifest.get("cycle_str"):
                            p_parts = prev_manifest["cycle_str"].split("_")
                            if len(p_parts) > 1:
                                prev_run = p_parts[1].lower()

                        for s in target_steps:
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
                                        "run": prev_run or "ant.",
                                        "is_fallback": True,
                                        "fallback_cycle": prev_manifest.get("cycle_str"),
                                        "fallback_run": prev_run,
                                        "fallback_step": prev_s
                                    }
                                    if s not in avail:
                                        avail.append(s)
                    except Exception as e:
                        logger.debug(f"ICON stitching calculation error: {e}")

            avail.sort()
            sorted_steps = [steps_dict[s] for s in avail if s in steps_dict]
            meta["available_steps"] = avail
            meta["steps"] = sorted_steps
            max_step = max(avail) if avail else 0
            meta["max_step"] = max_step
            raw_avail = list(self.current_manifest.get("available_steps", []))
            raw_max_step = max(raw_avail) if raw_avail else 0
            raw_complete = (len(raw_avail) >= len([s for s in target_steps if s <= max_target]))
            meta["raw_available_steps"] = raw_avail
            meta["downloaded_max_step"] = raw_max_step
            meta["raw_max_step"] = raw_max_step
            meta["is_complete"] = raw_complete
            meta["is_updating"] = self._is_syncing or (not raw_complete and self.current_manifest.get("status") != "complete")
            return meta

        return {
            "model": "DWD ICON-EU",
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
        """Devuelve la ruta al archivo PNG del paso y tipo solicitados (con soporte de fallback al ciclo anterior)."""
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

        # Fallback a ciclo anterior si aún no está resuelto
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
        """Devuelve la matriz NumPy (950 x 1500) para el paso y capa solicitados (con soporte de fallback)."""
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
            arr = self._load_grid_file(cycle_dir / f"{prefix}_step_{step:03d}")
            if arr is not None:
                self._in_memory_arrays[mem_key] = arr

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
                        arr = self._load_grid_file(prev_dir / f"{prefix}_step_{prev_step:03d}")
                        if arr is not None:
                            self._in_memory_arrays[prev_mem_key] = arr
                except Exception:
                    pass

        return arr

    def get_value_at(self, lat: float, lon: float, step: int, layer_type: str = "total") -> Optional[float]:
        """Consulta instantánea (0ms) en la matriz NumPy en memoria o disco (con fallback a ciclo anterior)."""
        if not (SPAIN_BBOX["lat_min"] <= lat <= SPAIN_BBOX["lat_max"] and SPAIN_BBOX["lon_min"] <= lon <= SPAIN_BBOX["lon_max"]):
            return None

        arr = self.get_matrix(step=step, layer_type=layer_type)
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
        """Calcula las coordenadas lat/lon exactas y el valor en mm del punto de máxima precipitación de ICON-EU."""
        arr = self.get_matrix(step=step, layer_type=layer_type)
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
            logger.error(f"Error calculando max_point ICON: {e}")
            return None

    def _get_candidate_cycles(self) -> List[Tuple[datetime, str, str]]:
        """
        Genera los ciclos candidatos más recientes en orden cronológico descendente
        (8 corridas operativas diarias cada 3 horas: 00z, 03z, 06z, 09z, 12z, 15z, 18z, 21z).
        """
        now = datetime.now(timezone.utc)
        candidates = []
        # Comenzar desde la hora actual UTC hacia atrás en saltos de 3 horas
        base_hour = (now.hour // 3) * 3
        current_dt = now.replace(hour=base_hour, minute=0, second=0, microsecond=0)

        for i in range(8):
            dt = current_dt - timedelta(hours=i * 3)
            date_str = dt.strftime("%Y%m%d")
            hh = f"{dt.hour:02d}"
            candidates.append((dt, date_str, hh))

        return candidates

    def _download_and_decompress_icon_step(self, date_str: str, hh: str, step: int, dest_grib_path: Path) -> bool:
        """
        Descarga el archivo comprimido .bz2 de precipitación TOT_PREC para ICON-EU desde DWD Open Data,
        y lo descomprime al vuelo en memoria escribiendo el GRIB2 descomprimido en dest_grib_path.
        """
        step_str = f"{step:03d}"
        url = f"{DWD_ICON_EU_BASE}/{hh}/tot_prec/icon-eu_europe_regular-lat-lon_single-level_{date_str}{hh}_{step_str}_TOT_PREC.grib2.bz2"
        
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Accept": "*/*"
        }
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=20) as resp:
                if resp.status == 200:
                    compressed_data = resp.read()
                    if len(compressed_data) > 500:
                        # Descomprimir bz2 en memoria
                        decompressed_data = bz2.decompress(compressed_data)
                        with open(dest_grib_path, "wb") as f:
                            f.write(decompressed_data)
                        return True
        except urllib.error.HTTPError as e:
            if e.code == 404:
                logger.debug(f"ICON: Paso +{step}h aún no publicado en DWD ({url})")
            else:
                logger.warning(f"Error HTTP {e.code} descargando ICON ({url}): {e}")
        except Exception as e:
            logger.warning(f"Error descargando ICON desde DWD ({url}): {e}")

        return False

    def _process_grib_to_grid(self, grib_file: Path) -> Optional[np.ndarray]:
        """
        Lee el archivo GRIB2 descomprimido de ICON-EU, extrae latitudes, longitudes y valores de TOT_PREC,
        e interpola sobre la cuadrícula Web Mercator SPAIN_GRID_LATS / SPAIN_GRID_LONS (950 x 1500).
        """
        if not XARRAY_AVAILABLE or not CFGRIB_AVAILABLE:
            logger.error("xarray o cfgrib no están disponibles para procesar GRIB2 de ICON.")
            return None

        try:
            # Abrir dataset GRIB con xarray y cfgrib
            ds = xr.open_dataset(
                grib_file,
                engine="cfgrib",
                backend_kwargs={"indexpath": ""}
            )

            # Buscar variable de precipitación (tot_prec, tp, TOT_PREC, etc.)
            var_name = None
            for candidate in ["tot_prec", "TOT_PREC", "tp", "unknown", "prate"]:
                if candidate in ds.data_vars:
                    var_name = candidate
                    break

            if not var_name:
                var_names = list(ds.data_vars.keys())
                if var_names:
                    var_name = var_names[0]
                else:
                    logger.warning(f"No se encontró variable de precipitación en {grib_file}")
                    ds.close()
                    return None

            da = ds[var_name]
            lats = ds["latitude"].values
            lons = ds["longitude"].values

            # Normalizar coordenadas de longitud si están en rango [0, 360] a [-180, 180]
            if np.any(lons > 180.0):
                lons = np.where(lons > 180.0, lons - 360.0, lons)
                sort_idx = np.argsort(lons)
                lons = lons[sort_idx]
                vals = da.values[:, sort_idx] if da.values.ndim == 2 else da.values[sort_idx]
            else:
                # Asegurar que lons estén ordenadas ascendentes
                if lons[0] > lons[-1]:
                    sort_idx = np.argsort(lons)
                    lons = lons[sort_idx]
                    vals = da.values[:, sort_idx] if da.values.ndim == 2 else da.values[sort_idx]
                else:
                    vals = da.values

            # Asegurar orden ascendente de latitudes para RegularGridInterpolator
            if lats[0] > lats[-1]:
                lats = lats[::-1]
                vals = vals[::-1, :] if vals.ndim == 2 else vals

            ds.close()

            # Asegurar matriz 2D
            if vals.ndim > 2:
                vals = vals.squeeze()

            # Tratar NaNs o valores negativos
            vals = np.nan_to_num(vals, nan=0.0)
            vals = np.maximum(vals, 0.0)

            # Crear interpolador regular 2D
            interp = RegularGridInterpolator(
                (lats, lons),
                vals,
                bounds_error=False,
                fill_value=0.0,
                method="linear"
            )

            # Evaluar sobre la malla de España
            lat_grid, lon_grid = np.meshgrid(SPAIN_GRID_LATS, SPAIN_GRID_LONS, indexing="ij")
            grid_points = np.stack([lat_grid.ravel(), lon_grid.ravel()], axis=-1)
            interpolated_1d = interp(grid_points)
            grid_2d = interpolated_1d.reshape((GRID_H, GRID_W)).astype(np.float32)

            return np.maximum(grid_2d, 0.0)

        except Exception as e:
            logger.error(f"Error procesando GRIB2 ICON-EU con cfgrib/xarray: {e}")
            return None

    async def sync_icon_forecast(self, max_steps: Optional[int] = None):
        """
        Sincroniza la previsión operativa de ICON-EU de forma progresiva.
        Comprueba en orden cronológico inverso las corridas recientes (12z, 09z, 06z, 03z, 00z...).
        Por cada paso descargado y procesado, genera el PNG Web Mercator,
        guarda la matriz .npz y notifica vía SSE en tiempo real a los clientes.
        """
        if self._is_syncing:
            logger.info("ICON: Sincronización ya en curso. Omitiendo llamada concurrente.")
            return

        async with self._sync_lock:
            self._is_syncing = True
            try:
                candidate_cycles = self._get_candidate_cycles()
                selected_cycle = None
                cycle_dir = None
                temp_grib_dir = None
                target_max_step = None
                steps_to_process = None

                # 1. Encontrar el ciclo operativo más reciente con datos disponibles
                for cycle_dt, date_str, hh in candidate_cycles:
                    test_cycle_str = f"{date_str}_{hh}z"
                    test_cycle_dir = self.cache_dir / test_cycle_str
                    test_manifest = test_cycle_dir / "manifest.json"

                    # A. Si ya está procesado en disco y tiene pasos válidos
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

                    # B. Comprobar disponibilidad remota descargando el paso +1h
                    test_temp_grib = test_cycle_dir / "temp_grib"
                    test_temp_grib.mkdir(parents=True, exist_ok=True)
                    test_step_1_grib = test_temp_grib / f"test_icon_step_001.grib2"

                    logger.debug(f"ICON: Comprobando disponibilidad del ciclo {test_cycle_str} en DWD Open Data...")
                    downloaded = await asyncio.to_thread(
                        self._download_and_decompress_icon_step, date_str, hh, 1, test_step_1_grib
                    )

                    if downloaded:
                        selected_cycle = (cycle_dt, date_str, hh)
                        cycle_dir = test_cycle_dir
                        if test_step_1_grib.exists():
                            test_step_1_grib.unlink()
                        break
                    else:
                        if test_step_1_grib.exists():
                            test_step_1_grib.unlink()
                        logger.info(f"ICON: Ciclo {test_cycle_str} no publicado aún en DWD. Comprobando ciclo anterior...")

                if not selected_cycle:
                    logger.warning("ICON: No se encontró ningún ciclo disponible ni en DWD Open Data ni en caché local.")
                    return

                cycle_dt, date_str, hh = selected_cycle
                cycle_str = f"{date_str}_{hh}z"
                cycle_iso = cycle_dt.isoformat()
                cycle_dir.mkdir(parents=True, exist_ok=True)
                temp_grib_dir = cycle_dir / "temp_grib"
                temp_grib_dir.mkdir(parents=True, exist_ok=True)

                is_main_run = hh in ("00", "06", "12", "18")
                candidate_steps = ICON_MAIN_STEPS if is_main_run else ICON_SHORT_STEPS
                default_target = 120 if is_main_run else 30
                target_max_step = max_steps or getattr(settings, "ICON_MAX_STEPS", default_target)
                if not is_main_run and target_max_step > 30:
                    target_max_step = 30

                steps_to_process = [s for s in candidate_steps if s <= target_max_step]

                logger.info(f"ICON: Sincronizando ciclo activo {cycle_str} (hasta +{target_max_step}h, {len(steps_to_process)} pasos)...")

                available_steps: List[int] = []
                step_entries: List[Dict[str, Any]] = []

                # Cargar manifiesto previo si existe para reanudar pasos existentes
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
                    logger.info(f"ICON: Todos los pasos (+{min(steps_to_process)}h..+{max(steps_to_process)}h) ya están al día en caché para el ciclo {cycle_str}.")
                else:
                    logger.info(f"ICON: Pasos disponibles: {len(available_steps)}/{len(steps_to_process)}. Comprobando disponibilidad del paso +{missing_steps[0]}h en DWD Open Data...")

                # Notificar inicio de sincronización
                self.notify_icon_update(cycle_str, available_steps, status="synchronizing", is_syncing=True)

                prev_raw_grid: Optional[np.ndarray] = None
                # Si reanudamos, cargar la última matriz para el cálculo de intervalos
                if available_steps:
                    last_step = max(available_steps)
                    prev_raw_grid = self._load_grid_file(cycle_dir / f"total_step_{last_step:03d}")

                for step in steps_to_process:
                    total_png = cycle_dir / f"total_step_{step:03d}.png"
                    interval_png = cycle_dir / f"interval_step_{step:03d}.png"
                    total_npz = cycle_dir / f"total_step_{step:03d}.npz"
                    interval_npz = cycle_dir / f"interval_step_{step:03d}.npz"

                    # Si ya está procesado este paso en disco, saltar descarga
                    if total_png.exists() and interval_png.exists() and total_npz.exists() and interval_npz.exists() and step in available_steps:
                        prev_raw_grid = self._load_grid_file(cycle_dir / f"total_step_{step:03d}")
                        continue

                    # Descargar y descomprimir paso GRIB2
                    step_grib = temp_grib_dir / f"icon_step_{step:03d}.grib2"
                    logger.info(f"ICON: Descargando y descomprimiendo paso +{step}h desde DWD Open Data...")
                    downloaded = await asyncio.to_thread(
                        self._download_and_decompress_icon_step, date_str, hh, step, step_grib
                    )

                    if not downloaded:
                        logger.info(f"ICON: Paso +{step}h aún no disponible en DWD para ciclo {cycle_str}.")
                        break
                    logger.info(f"ICON: Paso +{step}h descomprimido con éxito. Procesando ráster...")

                    # Procesar e interpolar cuadrícula
                    grid_2d = await asyncio.to_thread(self._process_grib_to_grid, step_grib)
                    if grid_2d is None:
                        logger.warning(f"ICON: No se pudo procesar matriz para paso +{step}h.")
                        if step_grib.exists():
                            step_grib.unlink()
                        continue

                    # Eliminar archivo temporal descomprimido GRIB2
                    if step_grib.exists():
                        step_grib.unlink()

                    # TOT_PREC en ICON-EU es acumulado continuo desde el paso 000
                    accum_total = np.maximum(grid_2d, 0.0)
                    if prev_raw_grid is not None:
                        interval_precip = np.maximum(accum_total - prev_raw_grid, 0.0)
                    else:
                        interval_precip = accum_total.copy()

                    prev_raw_grid = accum_total.copy()

                    # Guardar matrices NumPy comprimidas .npz
                    np.savez_compressed(total_npz, data=accum_total.astype(np.float32))
                    np.savez_compressed(interval_npz, data=interval_precip.astype(np.float32))

                    # Colorear y guardar imágenes PNG transparentes Web Mercator
                    rgba_total = colorize_precip_array(accum_total)
                    rgba_interval = colorize_precip_array(interval_precip)

                    img_total = Image.fromarray(rgba_total, mode="RGBA")
                    img_total.save(total_png, format="PNG", optimize=True)

                    img_interval = Image.fromarray(rgba_interval, mode="RGBA")
                    img_interval.save(interval_png, format="PNG", optimize=True)

                    # Registrar paso
                    valid_time = cycle_dt + timedelta(hours=step)
                    valid_iso = valid_time.isoformat()
                    madrid_offset = timedelta(hours=2)  # CEST
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
                        "max_interval_mm": round(float(np.nanmax(interval_precip)), 1)
                    })
                    step_entries.sort(key=lambda x: x["step"])

                    # Actualizar manifest.json
                    is_complete = len(available_steps) >= len(steps_to_process)
                    manifest_data = {
                        "model": "DWD ICON-EU",
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

                    # Actualizar caché en memoria
                    mem_total_key = f"{cycle_str}_total_{step:03d}"
                    mem_interval_key = f"{cycle_str}_interval_{step:03d}"
                    self._in_memory_arrays[mem_total_key] = accum_total
                    self._in_memory_arrays[mem_interval_key] = interval_precip

                    # Notificar a los clientes conectados vía SSE
                    self.notify_icon_update(
                        cycle_str, available_steps, step_added=step,
                        status="complete" if is_complete else "ready",
                        is_syncing=not is_complete
                    )

                # Limpieza de carpetas temporales
                if temp_grib_dir.exists():
                    shutil.rmtree(temp_grib_dir, ignore_errors=True)

                # Mantener sólo los 3 ciclos más recientes en disco
                self._prune_old_cycles(keep=3)

                logger.info(f"ICON: Sincronización finalizada para {cycle_str}. Pasos listos: {len(available_steps)}.")

            except Exception as e:
                logger.error(f"Error en sincronización de DWD ICON-EU: {e}", exc_info=True)
            finally:
                self._is_syncing = False
                gc.collect()

    def _prune_old_cycles(self, keep: int = 3):
        """Elimina directorios de corridas antiguas para ahorrar espacio en disco."""
        try:
            dirs = [d for d in self.cache_dir.iterdir() if d.is_dir() and "_" in d.name]
            dirs.sort(key=lambda d: d.name, reverse=True)
            for old_dir in dirs[keep:]:
                logger.info(f"ICON: Purgando ciclo antiguo de disco: {old_dir.name}")
                shutil.rmtree(old_dir, ignore_errors=True)
        except Exception as e:
            logger.warning(f"Error purgando ciclos antiguos ICON: {e}")


# Instancia singleton accesible globalmente
icon_worker = ICONWorker()
