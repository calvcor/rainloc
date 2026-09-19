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

# Mapeo exacto de índices de paleta de la imagen GIF de AEMET a valores dBZ
# Descarta completamente fondos (0, 1, 2), bordes CCAA/provinciales (10) y textos (42)
AEMET_INDEX_TO_DBZ: Dict[int, float] = {
    16: 12.0,  # Azul oscuro (12-18 dBZ)
    23: 18.0,  # Azul cielo (18-24 dBZ)
    26: 24.0,  # Cian (24-30 dBZ)
    6:  30.0,  # Verde oliva (30-36 dBZ)
    7:  36.0,  # Verde medio (36-42 dBZ)
    8:  42.0,  # Verde claro (42-48 dBZ)
    9:  48.0,  # Amarillo/Naranja (48-54 dBZ)
    4:  54.0,  # Naranja fuerte (54-60 dBZ)
    3:  60.0,  # Rojo (60-66 dBZ)
    5:  66.0,  # Púrpura / Granizo (>66 dBZ)
}


class AemetOpenDataService:
    def __init__(self):
        self.base_url = settings.AEMET_OPENDATA_BASE_URL
        self._last_download_time: Dict[str, float] = {}
        self._cache_bytes: Dict[str, bytes] = {}

    @property
    def is_configured(self) -> bool:
        """Indica si hay una clave de API de AEMET configurada."""
        return bool(settings.AEMET_API_KEY and len(settings.AEMET_API_KEY.strip()) > 10)

    def fetch_regional_radar_gif(self, aemet_code: str, min_interval_sec: float = 240.0) -> Optional[bytes]:
        """
        Descarga la última imagen GIF del radar regional desde AEMET OpenData.
        Aplica proxy seguro desde el servidor, control de rate-limits y caché en memoria (mínimo 4 min entre peticiones).
        """
        if not self.is_configured:
            return None

        # Caché en memoria para no saturar la API de AEMET (AEMET solo actualiza radares cada 10-15 min)
        now_ts = time.time()
        last_ts = self._last_download_time.get(aemet_code, 0.0)
        if (now_ts - last_ts) < min_interval_sec and aemet_code in self._cache_bytes:
            return self._cache_bytes[aemet_code]

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
                    return self._cache_bytes.get(aemet_code)
                data = json.loads(resp.read().decode("utf-8", "ignore"))

            datos_url = data.get("datos")
            if not datos_url:
                logger.warning(f"AEMET OpenData no devolvió URL de datos para radar {aemet_code}: {data.get('descripcion')}")
                return self._cache_bytes.get(aemet_code)

            # Descargar imagen GIF desde la URL temporal devuelta
            img_req = urllib.request.Request(datos_url, headers={"User-Agent": settings.AEMET_USER_AGENT})
            with urllib.request.urlopen(img_req, timeout=15) as img_resp:
                gif_bytes = img_resp.read()
                if gif_bytes:
                    self._last_download_time[aemet_code] = time.time()
                    self._cache_bytes[aemet_code] = gif_bytes
                return gif_bytes

        except Exception as e:
            logger.warning(f"Error en AEMET OpenData para radar {aemet_code}: {e}")
            return self._cache_bytes.get(aemet_code)

    def decode_and_blend_station(
        self,
        st_id: str,
        gif_bytes: bytes,
        short_grid: np.ndarray,
        coverage_mask: np.ndarray,
        max_km: float = RADAR_SHORT_RANGE_MAX_KM
    ) -> bool:
        """
        Decodifica la imagen GIF del radar regional de AEMET, extrae únicamente los
        píxeles meteorológicos reales (descartando las líneas de mapa y leyendas)
        y los reproyecta a la rejilla nacional de RainLoc (1500x950 Web Mercator).
        """
        st = SPANISH_RADAR_STATIONS.get(st_id)
        if not st:
            return False

        try:
            img = Image.open(io.BytesIO(gif_bytes))
            arr = np.array(img, dtype=np.uint8)

            # La zona de radar es exactamente 480x480 (filas 0 a 480).
            # A partir de la fila 480 se encuentra la barra de leyenda y logos.
            radar_h = min(480, arr.shape[0])
            radar_w = min(480, arr.shape[1])
            radar_arr = arr[:radar_h, :radar_w]

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

            pixels_mapped = 0

            # Iterar solo por los píxeles con índices de reflectividad válidos
            for idx, dbz_val in AEMET_INDEX_TO_DBZ.items():
                ys, xs = np.where(radar_arr == idx)
                for y, x in zip(ys, xs):
                    dy_km = (c_y - y) * km_per_px
                    dx_km = (x - c_x) * km_per_px
                    dist_km = math.sqrt(dx_km * dx_km + dy_km * dy_km)
                    if dist_km > max_km:
                        continue

                    # Coordenadas geográficas
                    p_lat = st_lat + (dy_km / deg_lat_km)
                    p_lon = st_lon + (dx_km / deg_lon_km)

                    if not (lat_min <= p_lat <= lat_max and lon_min <= p_lon <= lon_max):
                        continue

                    # Proyección Web Mercator
                    iy = int((lat_max - p_lat) / (lat_max - lat_min) * (out_h - 1))
                    ix = int((p_lon - lon_min) / (lon_max - lon_min) * (out_w - 1))

                    if 0 <= iy < out_h and 0 <= ix < out_w:
                        short_grid[iy, ix] = max(short_grid[iy, ix], dbz_val)
                        coverage_mask[iy, ix] = True
                        pixels_mapped += 1

            # También marcar la máscara de cobertura general del radar
            # para que la zona de Cullera tenga cobertura activa de corto alcance
            y_indices, x_indices = np.ogrid[:out_h, :out_w]
            grid_lats = lat_max - (y_indices / (out_h - 1)) * (lat_max - lat_min)
            grid_lons = lon_min + (x_indices / (out_w - 1)) * (lon_max - lon_min)
            d_lat = (grid_lats - st_lat) * deg_lat_km
            d_lon = (grid_lons - st_lon) * deg_lon_km
            dist_matrix_km = np.sqrt(d_lat * d_lat + d_lon * d_lon)
            in_range = dist_matrix_km <= max_km
            coverage_mask[in_range] = True

            logger.info(f"AEMET OpenData: Radar {st['name']} decodificado limpiamente ({pixels_mapped} celdas de lluvia real).")
            return True

        except Exception as e:
            logger.warning(f"Error decodificando GIF de AEMET OpenData para {st_id}: {e}")
            return False


# Instancia singleton del servicio AEMET OpenData
aemet_opendata_service = AemetOpenDataService()
