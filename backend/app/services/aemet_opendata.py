"""
RainLoc - AEMET OpenData Fallback Service
Módulo cliente seguro para consulta y decodificación de radares regionales de AEMET OpenData
(usado como fallback para estaciones sin volcado polar en EUMETNET ORD, como Cullera 'va').
"""
import io
import json
import logging
import math
import time
import threading
import urllib.request
from typing import Optional, Dict, Any, List, Tuple
from datetime import datetime, timezone

import numpy as np
from PIL import Image

from app.config import (
    settings,
    SPANISH_RADAR_STATIONS,
    SPAIN_BBOX,
    RADAR_SHORT_RANGE_MAX_KM
)

logger = logging.getLogger("rainloc-backend.aemet_opendata")

# Mapeo exacto y calibrado de índices de la paleta oficial de radar AEMET a valores dBZ (puntos medios de intervalos de 6 dBZ)
# El índice 10 (amarillo, 51.0 dBZ) se procesa dinámicamente para conservar la lluvia fuerte y filtrar líneas de mapa
AEMET_INDEX_TO_DBZ: Dict[int, float] = {
    # Trazas / lluvia muy débil (6 a 12 dBZ)
    11: 7.0,
    12: 7.0,
    13: 9.0,
    14: 9.0,
    # Lluvia débil (12 a 18 dBZ, Bloque 1 azul oscuro #0000fc)
    16: 15.0,
    17: 15.0,
    18: 15.0,
    # Lluvia débil a moderada (18 a 24 dBZ, Bloque 2 azul cielo #0094fc)
    20: 18.0,
    21: 18.0,
    22: 21.0,
    23: 21.0,
    24: 24.0,
    25: 24.0,
    # Lluvia moderada baja (24 a 30 dBZ, Bloque 3 cian #00fcfc)
    26: 27.0,
    29: 27.0,
    # Lluvia moderada (30 a 36 dBZ, Bloque 4 verde oliva #438323)
    6:  33.0,
    # Lluvia moderada a fuerte (36 a 42 dBZ, Bloque 5 verde medio #00c000)
    7:  39.0,
    # Lluvia fuerte (42 a 48 dBZ, Bloque 6 verde claro #00ff00)
    8:  45.0,
    # Lluvia muy fuerte / núcleos convectivos (48 a 54 dBZ, Bloque 7 amarillo #ffff00)
    10: 51.0,
    # Tormenta fuerte (54 a 60 dBZ, Bloque 8 ámbar/dorado #ffbb00)
    9:  57.0,
    # Tormenta muy fuerte (60 a 66 dBZ, Bloque 9 naranja intenso #ff7f00)
    4:  63.0,
    # Tormenta severa / pedrisco (66 a 72 dBZ, Bloque 10 rojo intenso #ff0000)
    3:  69.0,
    # Granizo destructivo / núcleos severos (> 72 dBZ, Bloque 11 púrpura/magenta #c8005a)
    5:  75.0,
}


class AemetOpenDataService:
    def __init__(self):
        self.base_url = settings.AEMET_OPENDATA_BASE_URL
        self._last_download_time: Dict[str, float] = {}
        self._cache_bytes: Dict[str, bytes] = {}
        self._cache_time: Dict[str, float] = {}
        self._lock = threading.Lock()

    @property
    def is_configured(self) -> bool:
        """Indica si hay una clave de API de AEMET configurada."""
        return bool(settings.AEMET_API_KEY and len(settings.AEMET_API_KEY.strip()) > 10)

    def _get_fallback_bytes(self, aemet_code: str, max_age_sec: float = 900.0) -> Optional[bytes]:
        """
        Obtiene la última imagen válida desde memoria o disco solo si es reciente (<= 15 min).
        Evita mantener ecos estáticos / congelados de tormentas pasadas.
        """
        now = time.time()
        if aemet_code in self._cache_bytes and self._cache_bytes[aemet_code]:
            if (now - self._cache_time.get(aemet_code, 0.0)) <= max_age_sec:
                return self._cache_bytes[aemet_code]
            else:
                del self._cache_bytes[aemet_code]
                if aemet_code in self._cache_time:
                    del self._cache_time[aemet_code]

        try:
            disk_p = settings.RADAR_CACHE_DIR / f"aemet_regional_{aemet_code}.gif"
            if disk_p.exists():
                mtime = disk_p.stat().st_mtime
                if (now - mtime) <= max_age_sec:
                    data = disk_p.read_bytes()
                    if data and len(data) > 500:
                        self._cache_bytes[aemet_code] = data
                        self._cache_time[aemet_code] = mtime
                        return data
                else:
                    # Eliminar archivo obsoleto en disco para evitar que reaparezca
                    disk_p.unlink(missing_ok=True)
        except Exception:
            pass
        return None

    def fetch_regional_radar_gif(self, aemet_code: str, min_interval_sec: float = 300.0, max_age_sec: float = 900.0) -> Optional[bytes]:
        """
        Descarga la última imagen GIF del radar regional desde AEMET OpenData.
        Aplica proxy seguro desde el servidor, control thread-safe de rate-limits, persistencia en disco y fallback reciente.
        """
        if not self.is_configured:
            return self._get_fallback_bytes(aemet_code, max_age_sec=max_age_sec)

        with self._lock:
            # Comprobar intervalo mínimo entre descargas para no saturar la API de AEMET
            now_ts = time.time()
            last_ts = self._last_download_time.get(aemet_code, 0.0)
            if (now_ts - last_ts) < min_interval_sec:
                cached = self._get_fallback_bytes(aemet_code, max_age_sec=max_age_sec)
                if cached:
                    return cached

            api_key = settings.AEMET_API_KEY.strip()
            url = f"{self.base_url}/red/radar/regional/{aemet_code}?api_key={api_key}"

            try:
                req = urllib.request.Request(
                    url,
                    headers={
                        "User-Agent": settings.AEMET_USER_AGENT,
                        "Cache-Control": "no-cache",
                        "Accept": "application/json"
                    }
                )
                with urllib.request.urlopen(req, timeout=12) as resp:
                    if resp.status != 200:
                        logger.warning(f"AEMET OpenData HTTP {resp.status} al consultar radar {aemet_code}")
                        self._last_download_time[aemet_code] = now_ts - (min_interval_sec - 60.0) # Reintento tras 60s
                        return self._get_fallback_bytes(aemet_code, max_age_sec=max_age_sec)
                    data = json.loads(resp.read().decode("utf-8", "ignore"))

                datos_url = data.get("datos")
                if not datos_url:
                    logger.warning(f"AEMET OpenData no devolvió URL de datos para radar {aemet_code}: {data.get('descripcion')}")
                    self._last_download_time[aemet_code] = now_ts - (min_interval_sec - 60.0)
                    return self._get_fallback_bytes(aemet_code, max_age_sec=max_age_sec)

                # Descargar imagen GIF desde la URL temporal devuelta
                img_req = urllib.request.Request(datos_url, headers={"User-Agent": settings.AEMET_USER_AGENT})
                with urllib.request.urlopen(img_req, timeout=15) as img_resp:
                    gif_bytes = img_resp.read()
                    if gif_bytes and len(gif_bytes) > 500:
                        self._last_download_time[aemet_code] = time.time()
                        self._cache_bytes[aemet_code] = gif_bytes
                        self._cache_time[aemet_code] = time.time()
                        try:
                            disk_p = settings.RADAR_CACHE_DIR / f"aemet_regional_{aemet_code}.gif"
                            disk_p.write_bytes(gif_bytes)
                        except Exception:
                            pass
                        return gif_bytes
                    return self._get_fallback_bytes(aemet_code, max_age_sec=max_age_sec)

            except Exception as e:
                logger.warning(f"Error en AEMET OpenData para radar {aemet_code}: {e}")
                self._last_download_time[aemet_code] = now_ts - (min_interval_sec - 60.0)
                return self._get_fallback_bytes(aemet_code, max_age_sec=max_age_sec)

    def decode_and_blend_station(
        self,
        st_id: str,
        gif_bytes: bytes,
        short_grid: np.ndarray,
        coverage_mask: np.ndarray,
        max_km: Optional[float] = None
    ) -> bool:
        """
        Decodifica la imagen GIF del radar regional de AEMET, extrae únicamente los
        píxeles meteorológicos reales (descartando las líneas de mapa y leyendas)
        y los reproyecta a la rejilla nacional de RainLoc (1500x950 Web Mercator).
        Por defecto utiliza el alcance nominal completo del radar regional (240 km).
        """
        st = SPANISH_RADAR_STATIONS.get(st_id)
        if not st:
            return False

        if max_km is None:
            max_km = float(st.get("range_km", 240.0))

        try:
            img = Image.open(io.BytesIO(gif_bytes))
            arr = np.array(img, dtype=np.uint8)

            # La zona de radar es exactamente 480x480 (filas 0 a 480).
            # A partir de la fila 480 se encuentra la barra de leyenda y logos.
            radar_h = min(480, arr.shape[0])
            radar_w = min(480, arr.shape[1])
            radar_arr = arr[:radar_h, :radar_w].copy()

            # Separar el índice 10 (amarillo, 51.0 dBZ) de las líneas de mapa
            # Si un píxel 10 tiene vecinos con eco de precipitación, es lluvia convectiva fuerte
            is_10 = (radar_arr == 10)
            if np.any(is_10):
                rain_src_mask = np.isin(radar_arr, [3, 4, 5, 6, 7, 8, 9, 11, 12, 13, 14, 16, 17, 18, 20, 21, 22, 23, 24, 25, 26, 29])
                has_rain_neighbor = np.zeros_like(is_10, dtype=bool)
                for dy in (-1, 0, 1):
                    for dx in (-1, 0, 1):
                        if dy == 0 and dx == 0:
                            continue
                        has_rain_neighbor |= np.roll(np.roll(rain_src_mask, dy, axis=0), dx, axis=1)
                radar_arr = np.where(is_10 & (~has_rain_neighbor), 0, radar_arr)

            # El centro exacto del radar se sitúa en (240, 240)
            c_y, c_x = 240.0, 240.0
            radius_px = 230.0  # El radio de 240 km ocupa 230 píxeles
            km_per_px = 240.0 / radius_px

            # Bounding box nacional de RainLoc
            out_h, out_w = short_grid.shape
            lat_min, lat_max = SPAIN_BBOX["lat_min"], SPAIN_BBOX["lat_max"]
            lon_min, lon_max = SPAIN_BBOX["lon_min"], SPAIN_BBOX["lon_max"]

            st_lat, st_lon = st["lat"], st["lon"]
            deg_lat_km = 111.139
            deg_lon_km = 111.139 * math.cos(math.radians(st_lat))

            # Proyección Web Mercator precisa
            r_earth = 6378137.0
            y_merc_min = r_earth * math.log(math.tan(math.pi / 4 + math.radians(lat_min) / 2))
            y_merc_max = r_earth * math.log(math.tan(math.pi / 4 + math.radians(lat_max) / 2))
            y_merc_grid = np.linspace(y_merc_max, y_merc_min, out_h)
            grid_lats = np.degrees(2 * np.arctan(np.exp(y_merc_grid / r_earth)) - math.pi / 2)
            grid_lons = np.linspace(lon_min, lon_max, out_w)

            # Subrejilla delimitada al radio de cobertura de la estación para máxima eficiencia
            delta_lat = (max_km / deg_lat_km) * 1.05
            delta_lon = (max_km / deg_lon_km) * 1.05

            sub_y = np.where((grid_lats >= st_lat - delta_lat) & (grid_lats <= st_lat + delta_lat))[0]
            sub_x = np.where((grid_lons >= st_lon - delta_lon) & (grid_lons <= st_lon + delta_lon))[0]

            if len(sub_y) == 0 or len(sub_x) == 0:
                return False

            lats_sub = grid_lats[sub_y]
            lons_sub = grid_lons[sub_x]
            lons_2d, lats_2d = np.meshgrid(lons_sub, lats_sub)

            dy_km = (lats_2d - st_lat) * deg_lat_km
            dx_km = (lons_2d - st_lon) * deg_lon_km
            dist_km = np.sqrt(dx_km * dx_km + dy_km * dy_km)

            in_range = dist_km <= max_km
            src_x = np.round(c_x + dx_km / km_per_px).astype(np.int32)
            src_y = np.round(c_y - dy_km / km_per_px).astype(np.int32)
            valid_src = in_range & (src_x >= 0) & (src_x < radar_w) & (src_y >= 0) & (src_y < radar_h)

            # Tabla de conversión estricta de índices de reflectividad meteorológica (descarta completamente bordes, costas y textos)
            lut = np.full(256, -9999.0, dtype=np.float32)
            for idx, dbz_val in AEMET_INDEX_TO_DBZ.items():
                lut[idx] = dbz_val

            # Mapeo inverso continuo directo (sin huecos / sin bandas verticales)
            dbz_sub = np.full(lats_2d.shape, -9999.0, dtype=np.float32)
            dbz_sub[valid_src] = lut[radar_arr[src_y[valid_src], src_x[valid_src]]]

            # Actualizar rejilla de salida y máscara de cobertura
            for r_i, gy in enumerate(sub_y):
                row_in_range = in_range[r_i]
                if np.any(row_in_range):
                    coverage_mask[gy, sub_x[row_in_range]] = True
                
                row_dbz = dbz_sub[r_i]
                rain_mask = row_dbz >= 5.0
                if np.any(rain_mask):
                    target_cols = sub_x[rain_mask]
                    short_grid[gy, target_cols] = np.maximum(short_grid[gy, target_cols], row_dbz[rain_mask])

            pixels_mapped = int(np.sum(dbz_sub >= 5.0))
            logger.info(f"AEMET OpenData: Radar {st['name']} decodificado limpiamente ({pixels_mapped} celdas de lluvia real en rejilla Web Mercator).")
            return True

        except Exception as e:
            logger.warning(f"Error decodificando GIF de AEMET OpenData para {st_id}: {e}")
            return False


# Instancia singleton del servicio AEMET OpenData
aemet_opendata_service = AemetOpenDataService()
