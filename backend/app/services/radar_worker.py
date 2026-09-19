"""
RainLoc - Radar Worker & Processor
Ingesta en tiempo casi real y gestión histórica (24 horas) de radar meteorológico
(OPERA Composite y Radares Individuales) desde CloudFerro S3 (openradar-24h)
y notificaciones MQTT (MeteoGate / ORD).
"""
import os
import io
import time
import json
import logging
import asyncio
import threading
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple, Set, AsyncGenerator

import h5py
import numpy as np
from PIL import Image

try:
    import pyproj
    PYPROJ_AVAILABLE = True
except ImportError:
    PYPROJ_AVAILABLE = False

try:
    import paho.mqtt.client as mqtt
    MQTT_AVAILABLE = True
except ImportError:
    MQTT_AVAILABLE = False

from app.config import settings, SPANISH_RADAR_STATIONS

logger = logging.getLogger("rainloc-backend.radar")

# Coordenadas y Grid para el Compuesto de España:
# Cubre Península Ibérica y Baleares: Lat [35.0, 44.5], Lon [-10.0, 5.0]
# CRÍTICO: El grid se muestrea en proyección Web Mercator (EPSG:3857) para que
# Leaflet (L.imageOverlay) pinte la imagen sin distorsión (eliminando el error de hasta 18km).
SPAIN_BBOX = {"lat_min": 35.0, "lat_max": 44.5, "lon_min": -10.0, "lon_max": 5.0}
R_EARTH = 6378137.0
Y_MERC_MIN = R_EARTH * np.log(np.tan(np.pi/4 + np.radians(SPAIN_BBOX["lat_min"])/2))
Y_MERC_MAX = R_EARTH * np.log(np.tan(np.pi/4 + np.radians(SPAIN_BBOX["lat_max"])/2))
GRID_H = 950
GRID_W = 1500

Y_MERC_GRID = np.linspace(Y_MERC_MAX, Y_MERC_MIN, GRID_H)
SPAIN_GRID_LATS = np.degrees(2 * np.arctan(np.exp(Y_MERC_GRID / R_EARTH)) - np.pi/2)
SPAIN_GRID_LONS = np.linspace(SPAIN_BBOX["lon_min"], SPAIN_BBOX["lon_max"], GRID_W)

# Cargar índices precomputados para mapping ultrarrápido sin dependencia de pyproj en runtime
PRECOMPUTED_GRID_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "spain_radar_indices.npz"

if PRECOMPUTED_GRID_FILE.exists():
    _grid_data = np.load(PRECOMPUTED_GRID_FILE)
    SPAIN_COL_IDX = _grid_data["col_idx"]
    SPAIN_ROW_IDX = _grid_data["row_idx"]
    SPAIN_VALID_MASK = _grid_data["valid_mask"]
elif PYPROJ_AVAILABLE:
    OPERA_PROJ_STR = "+proj=laea +lat_0=55.0 +lon_0=10.0 +x_0=1950000.0 +y_0=-2100000.0 +units=m +ellps=WGS84"
    transformer_4326_to_laea = pyproj.Transformer.from_crs("EPSG:4326", OPERA_PROJ_STR, always_xy=True)
    _lon_grid, _lat_grid = np.meshgrid(SPAIN_GRID_LONS, SPAIN_GRID_LATS)
    _x_grid, _y_grid = transformer_4326_to_laea.transform(_lon_grid, _lat_grid)
    SPAIN_COL_IDX = np.round(_x_grid / 1000.0).astype(np.int16)
    SPAIN_ROW_IDX = np.round(-_y_grid / 1000.0).astype(np.int16)
    SPAIN_VALID_MASK = (SPAIN_COL_IDX >= 0) & (SPAIN_COL_IDX < 3800) & (SPAIN_ROW_IDX >= 0) & (SPAIN_ROW_IDX < 4400)
else:
    _lat0 = np.radians(55.0)
    _lon0 = np.radians(10.0)
    _lons_rad, _lats_rad = np.meshgrid(np.radians(SPAIN_GRID_LONS), np.radians(SPAIN_GRID_LATS))
    _dlon = _lons_rad - _lon0
    _kp = np.sqrt(2.0 / (1.0 + np.sin(_lat0) * np.sin(_lats_rad) + np.cos(_lat0) * np.cos(_lats_rad) * np.cos(_dlon)))
    _R = 6378137.0
    _x_grid = _R * _kp * np.cos(_lats_rad) * np.sin(_dlon) + 1950000.0
    _y_grid = _R * _kp * (np.cos(_lat0) * np.sin(_lats_rad) - np.sin(_lat0) * np.cos(_lats_rad) * np.cos(_dlon)) - 2100000.0
    SPAIN_COL_IDX = np.round(_x_grid / 1000.0).astype(np.int16)
    SPAIN_ROW_IDX = np.round(-_y_grid / 1000.0).astype(np.int16)
    SPAIN_VALID_MASK = (SPAIN_COL_IDX >= 0) & (SPAIN_COL_IDX < 3800) & (SPAIN_ROW_IDX >= 0) & (SPAIN_ROW_IDX < 4400)


def colorize_dbz_array(dbz_matrix: np.ndarray) -> np.ndarray:
    """
    Aplica la rampa de color meteorológica oficial a una matriz 2D de dBZ.
    Elimina ruidos atmosféricos y ecos no meteorológicos (< 8 dBZ).
    Retorna matriz RGBA (H, W, 4) uint8.
    """
    h, w = dbz_matrix.shape
    rgba = np.zeros((h, w, 4), dtype=np.uint8)

    # Rango 8 a 15 dBZ (Lluvia débil / llovizna) -> Cyan suave
    m1 = (dbz_matrix >= 8) & (dbz_matrix < 15)
    rgba[m1] = [56, 189, 248, 175]

    # Rango 15 a 25 dBZ (Lluvia ligera) -> Azul medio
    m2 = (dbz_matrix >= 15) & (dbz_matrix < 25)
    rgba[m2] = [2, 132, 199, 200]

    # Rango 25 a 35 dBZ (Lluvia moderada) -> Verde
    m3 = (dbz_matrix >= 25) & (dbz_matrix < 35)
    rgba[m3] = [34, 197, 94, 225]

    # Rango 35 a 45 dBZ (Lluvia fuerte) -> Amarillo intenso
    m4 = (dbz_matrix >= 35) & (dbz_matrix < 45)
    rgba[m4] = [234, 179, 8, 240]

    # Rango 45 a 55 dBZ (Lluvia muy fuerte / granizo) -> Naranja
    m5 = (dbz_matrix >= 45) & (dbz_matrix < 55)
    rgba[m5] = [249, 115, 22, 250]

    # Rango >= 55 dBZ (Tormenta severa / pedrisco) -> Rojo
    m6 = (dbz_matrix >= 55)
    rgba[m6] = [239, 68, 68, 255]

    return rgba


def parse_timestep_info(timestep: str) -> Dict[str, Any]:
    """
    Convierte un identificador de timestep (ej: '20260919T1305' o '1305')
    en fechas formateadas ISO y hora local de Madrid.
    """
    now = datetime.now(timezone.utc)
    if "T" in timestep and len(timestep) >= 13:
        try:
            dt_utc = datetime.strptime(timestep[:13], "%Y%m%dT%H%M").replace(tzinfo=timezone.utc)
        except Exception:
            dt_utc = now
    elif len(timestep) == 4 and timestep.isdigit():
        try:
            h = int(timestep[:2])
            m = int(timestep[2:])
            dt_utc = datetime(now.year, now.month, now.day, h, m, tzinfo=timezone.utc)
            if dt_utc > now + timedelta(minutes=15):
                dt_utc -= timedelta(days=1)
            timestep = dt_utc.strftime("%Y%m%dT%H%M")
        except Exception:
            dt_utc = now
    else:
        dt_utc = now

    madrid_offset = timedelta(hours=2) # Verano / CEST (adaptativo)
    local_dt = dt_utc + madrid_offset
    return {
        "timestep": timestep,
        "valid_time_iso": dt_utc.isoformat(),
        "valid_time_local": local_dt.strftime("%d/%m %H:%M"),
        "time_only_local": local_dt.strftime("%H:%M")
    }


class RadarService:
    """
    Servicio central de gestión de datos de radar.
    Gestiona la ingesta de Compuestos de España y el histórico de 24 horas (~288 fotogramas).
    """
    _instance: Optional["RadarService"] = None

    def __new__(cls) -> "RadarService":
        if cls._instance is None:
            cls._instance = super(RadarService, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if getattr(self, "_initialized", False):
            return
        self._initialized = True

        self.cache_dir: Path = settings.RADAR_CACHE_DIR
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.composites_dir: Path = self.cache_dir / "composites"
        self.composites_dir.mkdir(parents=True, exist_ok=True)

        # Metadatos del estado del radar
        self.state: Dict[str, Any] = {
            "last_updated": None,
            "latest_composite": None,
            "composite_bounds": [[SPAIN_BBOX["lat_min"], SPAIN_BBOX["lon_min"]], [SPAIN_BBOX["lat_max"], SPAIN_BBOX["lon_max"]]],
            "latest_stations": {},
            "mqtt_connected": False,
            "status": "ready"
        }

        # Histórico de fotogramas disponibles en las últimas 24 horas (orden cronológico ascendente)
        self.timeline: List[Dict[str, Any]] = []

        # Cache en memoria de matriz dBZ del compuesto más reciente para inspección instantánea en hover
        self._composite_dbz_grid: Optional[np.ndarray] = None
        # Cache LRU en memoria de matrices históricas recientes (máx 30)
        self._historical_dbz_grids: Dict[str, np.ndarray] = {}

        # Intentar cargar matriz cacheada y timeline del disco
        self._load_cached_state()

        # Worker de sondeo / MQTT
        self._mqtt_client = None
        self._subscribers: Set[asyncio.Queue] = set()
        self._sync_lock = threading.Lock()

    def notify_radar_update(self, timestep: str, bounds: Any = None):
        """Notifica de forma inmediata y thread-safe a todos los clientes SSE conectados de una nueva imagen."""
        ts_info = parse_timestep_info(timestep)
        payload = {
            "event": "radar_update",
            "timestep": timestep,
            "valid_time_iso": ts_info["valid_time_iso"],
            "valid_time_local": ts_info["valid_time_local"],
            "bounds": bounds or self.state.get("composite_bounds"),
            "timeline": self.get_timeline(),
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
        q = asyncio.Queue(maxsize=30)
        q._loop = loop
        self._subscribers.add(q)
        try:
            # Enviar el estado actual y la línea temporal completa inmediatamente al conectar
            initial_payload = {
                "event": "radar_init",
                "timestep": (self.state.get("latest_composite") or {}).get("timestep"),
                "bounds": self.state.get("composite_bounds"),
                "timeline": self.get_timeline(),
                "timestamp": self.state.get("last_updated") or datetime.now(timezone.utc).isoformat()
            }
            yield initial_payload

            while True:
                try:
                    data = await asyncio.wait_for(q.get(), timeout=25.0)
                    yield data
                except asyncio.TimeoutError:
                    yield {"event": "ping", "timestamp": datetime.now(timezone.utc).isoformat()}
        finally:
            self._subscribers.discard(q)

    def _save_state_to_cache(self):
        """Persiste el estado del radar y la línea temporal en disco."""
        try:
            state_file = self.cache_dir / "radar_state.json"
            with open(state_file, "w", encoding="utf-8") as f:
                json.dump(self.state, f, ensure_ascii=False, indent=2)

            timeline_file = self.cache_dir / "radar_timeline.json"
            with open(timeline_file, "w", encoding="utf-8") as f:
                json.dump({"timeline": self.timeline, "updated_at": datetime.now(timezone.utc).isoformat()}, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"No se pudo guardar estado de radar en disco: {e}")

    def _load_cached_state(self):
        """Carga en memoria el estado, timeline y matrices si existen en disco."""
        try:
            state_file = self.cache_dir / "radar_state.json"
            if state_file.exists():
                with open(state_file, "r", encoding="utf-8") as f:
                    cached_state = json.load(f)
                    if isinstance(cached_state, dict):
                        if "latest_composite" in cached_state and cached_state["latest_composite"]:
                            self.state["latest_composite"] = cached_state["latest_composite"]
                        if "last_updated" in cached_state and cached_state["last_updated"]:
                            self.state["last_updated"] = cached_state["last_updated"]

            timeline_file = self.cache_dir / "radar_timeline.json"
            if timeline_file.exists():
                with open(timeline_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.timeline = data.get("timeline", [])

            # Si no hay timeline pero hay imágenes en composites_dir, reconstruirla
            if not self.timeline and self.composites_dir.exists():
                self._rebuild_timeline_from_disk()

            comp_npy = self.cache_dir / "latest_spain_composite.npy"
            if comp_npy.exists():
                self._composite_dbz_grid = np.load(comp_npy)
        except Exception as e:
            logger.warning(f"No se pudo cargar estado/timeline de radar desde disco: {e}")

    def _rebuild_timeline_from_disk(self):
        """Reconstruye la lista cronológica de timesteps inspeccionando el directorio de compuestos."""
        entries = []
        for png_file in self.composites_dir.glob("composite_*.png"):
            ts = png_file.stem.replace("composite_", "")
            info = parse_timestep_info(ts)
            entries.append({
                "timestep": ts,
                "valid_time_iso": info["valid_time_iso"],
                "valid_time_local": info["valid_time_local"],
                "time_only_local": info["time_only_local"],
                "png_url": f"/api/v1/radar/image?timestep={ts}"
            })
        entries.sort(key=lambda x: x["timestep"])
        self.timeline = entries[-288:] # Conservar hasta 24h (288 pasos a 5 min)
        self._save_state_to_cache()

    def get_timeline(self) -> List[Dict[str, Any]]:
        """Retorna la lista cronológica de los fotogramas de radar disponibles en las últimas 24h."""
        latest_ts = (self.state.get("latest_composite") or {}).get("timestep")
        result = []
        for item in self.timeline:
            entry = dict(item)
            entry["is_latest"] = (entry["timestep"] == latest_ts)
            result.append(entry)
        return result

    def get_metadata(self) -> Dict[str, Any]:
        """Retorna metadatos, línea temporal y opciones para la UI."""
        return {
            "status": self.state["status"],
            "last_updated": self.state["last_updated"],
            "modes": [
                {"id": "composite", "name": "Radar Compuesto (España - OPERA)", "description": "Mosaico nacional de reflectividad DBZH"}
            ],
            "latest_composite": self.state["latest_composite"],
            "composite_bounds": self.state["composite_bounds"],
            "timeline": self.get_timeline(),
            "color_scale": [
                {"min_dbz": 5, "max_dbz": 15, "color": "#38bdf8", "label": "5-15 dBZ (Muy débil)"},
                {"min_dbz": 15, "max_dbz": 25, "color": "#0284c9", "label": "15-25 dBZ (Débil)"},
                {"min_dbz": 25, "max_dbz": 35, "color": "#22c55e", "label": "25-35 dBZ (Moderada)"},
                {"min_dbz": 35, "max_dbz": 45, "color": "#eab308", "label": "35-45 dBZ (Fuerte)"},
                {"min_dbz": 45, "max_dbz": 55, "color": "#f97316", "label": "45-55 dBZ (Muy fuerte)"},
                {"min_dbz": 55, "max_dbz": 75, "color": "#ef4444", "label": "≥55 dBZ (Granizo/Severa)"}
            ]
        }

    def get_composite_image_path(self, timestep: Optional[str] = None) -> Optional[Path]:
        """
        Ruta del archivo PNG del compuesto de España solicitado o del más reciente.
        """
        if timestep:
            p = self.composites_dir / f"composite_{timestep}.png"
            if p.exists():
                return p
        
        # Fallback al compuesto más reciente
        p_latest = self.cache_dir / "latest_spain_composite.png"
        return p_latest if p_latest.exists() else None

    def get_station_image_path(self, station_id: str, timestep: Optional[str] = None) -> Optional[Path]:
        """Compatibilidad: retorna el compuesto nacional."""
        return self.get_composite_image_path(timestep=timestep)

    def get_dbz_at_point(self, lat: float, lon: float, mode: str = "composite", station_id: Optional[str] = None, timestep: Optional[str] = None) -> Optional[float]:
        """Consulta el valor en dBZ para las coordenadas (lat, lon) dadas con interpolación de vecino más cercano."""
        if not (SPAIN_BBOX["lat_min"] <= lat <= SPAIN_BBOX["lat_max"] and SPAIN_BBOX["lon_min"] <= lon <= SPAIN_BBOX["lon_max"]):
            return None

        grid = None
        if timestep:
            # Buscar en caché en memoria o cargar desde disco
            grid = self._historical_dbz_grids.get(timestep)
            if grid is None:
                npy_path = self.composites_dir / f"composite_{timestep}.npy"
                if npy_path.exists():
                    try:
                        grid = np.load(npy_path)
                        # Mantener como máximo 30 matrices en memoria
                        if len(self._historical_dbz_grids) >= 30:
                            oldest_k = next(iter(self._historical_dbz_grids))
                            del self._historical_dbz_grids[oldest_k]
                        self._historical_dbz_grids[timestep] = grid
                    except Exception as e:
                        logger.warning(f"Error cargando npy {npy_path}: {e}")
        
        if grid is None:
            grid = self._composite_dbz_grid

        if grid is not None:
            y_pt = R_EARTH * np.log(np.tan(np.pi/4 + np.radians(lat)/2))
            row = int(round((Y_MERC_MAX - y_pt) / (Y_MERC_MAX - Y_MERC_MIN) * (GRID_H - 1)))
            col = int(round((lon - SPAIN_BBOX["lon_min"]) / (SPAIN_BBOX["lon_max"] - SPAIN_BBOX["lon_min"]) * (GRID_W - 1)))
            row = max(0, min(row, grid.shape[0] - 1))
            col = max(0, min(col, grid.shape[1] - 1))
            val = float(grid[row, col])
            return round(val, 1) if val >= 5.0 else None

        return None

    # -------------------------------------------------------------
    # Ingesta desde S3
    # -------------------------------------------------------------
    def _fetch_s3_latest_keys(self, prefix: str, max_keys: int = 1000) -> List[str]:
        """Consulta la API REST de S3 para listar los archivos más recientes."""
        url = f"{settings.ORD_S3_ENDPOINT}/{settings.ORD_S3_BUCKET}?list-type=2&prefix={prefix}&max-keys={max_keys}"
        req = urllib.request.Request(url, headers={"User-Agent": settings.AEMET_USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                root = ET.fromstring(resp.read())
                ns = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}
                return [c.findtext("s3:Key", "", ns) for c in root.findall("s3:Contents", ns)]
        except Exception as e:
            logger.warning(f"Error listando claves S3 ({prefix}): {e}")
            return []

    def _download_s3_file(self, s3_key: str) -> Optional[bytes]:
        """Descarga un fichero de forma pública y sin firma desde S3."""
        url = f"{settings.ORD_S3_ENDPOINT}/{settings.ORD_S3_BUCKET}/{s3_key}"
        req = urllib.request.Request(url, headers={"User-Agent": settings.AEMET_USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=25) as resp:
                return resp.read()
        except Exception as e:
            logger.warning(f"Error descargando {s3_key} desde S3: {e}")
            return None

    # -------------------------------------------------------------
    # Procesamiento de HDF5 (Compuesto de España)
    # -------------------------------------------------------------
    def process_opera_composite(self, h5_bytes: bytes, timestep: str) -> bool:
        """Extrae el mosaico de España del HDF5 de OPERA, genera PNG RGBA georreferenciado y actualiza la línea temporal."""
        try:
            with h5py.File(io.BytesIO(h5_bytes), "r") as f:
                raw_data = f["dataset1"]["data1"]["data"][:]
                what = f["dataset1"]["data1"]["what"].attrs
                gain = float(what.get("gain", 0.5))
                offset = float(what.get("offset", -32.0))
                nodata = float(what.get("nodata", 255.0))
                undetect = float(what.get("undetect", 0.0))

            # Matriz en valores físicos reales de dBZ (ODIM standard)
            dbz_phys = np.where((raw_data == nodata) | (raw_data == undetect), -9999.0, raw_data * gain + offset)
            # Descartar ruido de fondo y ecos no meteorológicos (< 8.0 dBZ)
            dbz_phys = np.where(dbz_phys < 8.0, -9999.0, dbz_phys)

            # Mapeo a la rejilla de España
            sampled_dbz = np.full((len(SPAIN_GRID_LATS), len(SPAIN_GRID_LONS)), -9999.0, dtype=np.float32)
            sampled_dbz[SPAIN_VALID_MASK] = dbz_phys[SPAIN_ROW_IDX[SPAIN_VALID_MASK], SPAIN_COL_IDX[SPAIN_VALID_MASK]]

            # Guardar matriz .npy histórica
            npy_path = self.composites_dir / f"composite_{timestep}.npy"
            np.save(npy_path, sampled_dbz)

            # Generar y guardar imagen PNG RGBA
            rgba = colorize_dbz_array(sampled_dbz)
            img = Image.fromarray(rgba, "RGBA")
            
            out_path = self.composites_dir / f"composite_{timestep}.png"
            img.save(out_path, format="PNG", optimize=True)

            # Actualizar último compuesto si este fotograma es el más reciente o igual
            is_latest_or_newer = True
            if self.timeline:
                latest_existing = max(t["timestep"] for t in self.timeline)
                if timestep < latest_existing:
                    is_latest_or_newer = False

            if is_latest_or_newer:
                self._composite_dbz_grid = sampled_dbz
                try:
                    np.save(self.cache_dir / "latest_spain_composite.npy", sampled_dbz)
                    img.save(self.cache_dir / "latest_spain_composite.png", format="PNG", optimize=True)
                except Exception as e:
                    logger.warning(f"Error actualizando latest_spain_composite: {e}")

                self.state["last_updated"] = datetime.now(timezone.utc).isoformat()
                self.state["latest_composite"] = {
                    "timestep": timestep,
                    "bounds": [[SPAIN_BBOX["lat_min"], SPAIN_BBOX["lon_min"]], [SPAIN_BBOX["lat_max"], SPAIN_BBOX["lon_max"]]],
                    "file": f"composites/composite_{timestep}.png"
                }

            # Actualizar entrada en la línea temporal
            ts_info = parse_timestep_info(timestep)
            # Reemplazar o insertar
            self.timeline = [t for t in self.timeline if t["timestep"] != timestep]
            self.timeline.append({
                "timestep": timestep,
                "valid_time_iso": ts_info["valid_time_iso"],
                "valid_time_local": ts_info["valid_time_local"],
                "time_only_local": ts_info["time_only_local"],
                "png_url": f"/api/v1/radar/image?timestep={timestep}"
            })
            self.timeline.sort(key=lambda x: x["timestep"])
            # Mantener un máximo de 288 fotogramas (24h completas a 5 min)
            if len(self.timeline) > 288:
                self.timeline = self.timeline[-288:]

            self._save_state_to_cache()

            if is_latest_or_newer:
                # Notificar inmediatamente a los clientes SSE
                self.notify_radar_update(timestep, self.state["latest_composite"]["bounds"])

            logger.info(f"Compuesto de España procesado con éxito ({timestep})")
            return True
        except Exception as e:
            logger.error(f"Error procesando compuesto OPERA HDF5 ({timestep}): {e}")
            return False

    # -------------------------------------------------------------
    # Sincronización periódica y Backfill de 24 Horas
    # -------------------------------------------------------------
    async def sync_radar_data(self) -> Dict[str, Any]:
        """Comprueba, descarga y sincroniza los compuestos nacionales disponibles de las últimas 24 horas."""
        with self._sync_lock:
            loop = asyncio.get_running_loop()
            now = datetime.now(timezone.utc)

            # Buscar claves de hoy y de ayer para cubrir las últimas 24h
            today_prefix = now.strftime("%Y/%m/%d/")
            yesterday_prefix = (now - timedelta(days=1)).strftime("%Y/%m/%d/")

            comp_keys = await loop.run_in_executor(None, self._fetch_s3_latest_keys, f"{today_prefix}OPERA/COMP/")
            yesterday_keys = await loop.run_in_executor(None, self._fetch_s3_latest_keys, f"{yesterday_prefix}OPERA/COMP/")
            
            all_comp_keys = yesterday_keys + comp_keys
            dbz_keys = [k for k in all_comp_keys if "DBZH.h5" in k]
            
            if not dbz_keys:
                logger.warning("Radar: No se encontraron claves DBZH en S3.")
                return self.get_metadata()

            # Conservar como máximo las últimas 288 claves (24h a 5 min)
            recent_dbz_keys = dbz_keys[-288:]

            # Identificar qué claves no están aún procesadas en disco
            keys_to_download = []
            for k in recent_dbz_keys:
                ts = k.split("@")[1] if "@" in k else now.strftime("%Y%m%dT%H%M")
                png_file = self.composites_dir / f"composite_{ts}.png"
                if not png_file.exists():
                    keys_to_download.append((k, ts))

            if keys_to_download:
                logger.info(f"Radar: Sincronizando {len(keys_to_download)} fotogramas nuevos/pendientes de las últimas 24h...")
                
                def download_and_process(item):
                    key, ts = item
                    h5_bytes = self._download_s3_file(key)
                    if h5_bytes:
                        self.process_opera_composite(h5_bytes, ts)

                # Procesar en paralelo con ThreadPoolExecutor para backfill ultra-rápido
                await loop.run_in_executor(None, lambda: list(ThreadPoolExecutor(max_workers=5).map(download_and_process, keys_to_download)))

            # Limpiar fotogramas con más de 25 horas de antigüedad
            self._cleanup_old_cache()

            return self.get_metadata()

    def _cleanup_old_cache(self):
        """Elimina fotogramas y archivos con más de 25 horas de antigüedad."""
        try:
            cutoff = time.time() - (25 * 3600)
            for f in self.composites_dir.glob("composite_*.*"):
                if f.stat().st_mtime < cutoff:
                    f.unlink(missing_ok=True)
            for f in self.cache_dir.glob("*.h5"):
                if f.stat().st_mtime < cutoff:
                    f.unlink(missing_ok=True)
        except Exception as e:
            logger.warning(f"Error en limpieza de caché de radar: {e}")

    # -------------------------------------------------------------
    # Conexión MQTT (MeteoGate / ORD)
    # -------------------------------------------------------------
    def start_mqtt_client(self):
        """Arranca el cliente MQTT en segundo plano con reconexión automática."""
        if not MQTT_AVAILABLE:
            logger.warning("paho-mqtt no está instalado; operando en modo polling S3 exclusivo.")
            return

        def on_connect(client, userdata, flags, rc, properties=None):
            if rc == 0:
                logger.info(f"Conectado con éxito al Broker MQTT de MeteoGate/ORD ({settings.ORD_MQTT_HOST}:{settings.ORD_MQTT_PORT})")
                self.state["mqtt_connected"] = True
                client.subscribe([("opera/comp/#", 0), ("openradar/#", 0)])
            else:
                logger.warning(f"Conexión MQTT rechazada con código: {rc}")

        def on_message(client, userdata, msg):
            try:
                payload = json.loads(msg.payload.decode("utf-8"))
                s3_key = payload.get("key") or payload.get("s3_path")
                if s3_key and "OPERA/COMP/" in s3_key and "DBZH.h5" in s3_key:
                    logger.info(f"Notificación MQTT de nuevo compuesto: {s3_key}")
                    threading.Thread(target=self._handle_mqtt_event, args=(s3_key,), daemon=True).start()
            except Exception as e:
                logger.debug(f"Mensaje MQTT procesado ({msg.topic}): {e}")

        def on_disconnect(client, userdata, rc, properties=None):
            self.state["mqtt_connected"] = False
            logger.info(f"MQTT desconectado (rc={rc}). Iniciando reconexión automática...")

        try:
            client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2 if hasattr(mqtt, "CallbackAPIVersion") else None)
            client.username_pw_set(settings.ORD_MQTT_USER, "")
            client.tls_set()
            client.on_connect = on_connect
            client.on_message = on_message
            client.on_disconnect = on_disconnect
            client.reconnect_delay_set(min_delay=5, max_delay=120)
            client.connect_async(settings.ORD_MQTT_HOST, settings.ORD_MQTT_PORT, 60)
            client.loop_start()
            self._mqtt_client = client
        except Exception as e:
            logger.warning(f"No se pudo iniciar cliente MQTT ({settings.ORD_MQTT_HOST}): {e}. El sistema usará el polling S3 de respaldo.")

    def _handle_mqtt_event(self, s3_key: str):
        """Manejador de evento de notificación MQTT para compuestos nacionales."""
        if "OPERA/COMP/" not in s3_key or "DBZH.h5" not in s3_key:
            return

        h5_bytes = self._download_s3_file(s3_key)
        if not h5_bytes:
            return
        timestep = s3_key.split("@")[1] if "@" in s3_key else datetime.now(timezone.utc).strftime("%Y%m%dT%H%M")
        self.process_opera_composite(h5_bytes, timestep)

radar_service = RadarService()
