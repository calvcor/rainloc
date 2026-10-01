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

# Mapeo exacto de índices de paleta de la imagen GIF de AEMET a valores dBZ
# Descarta completamente fondos (0, 1, 2), bordes CCAA/provinciales (10) y textos (42)
AEMET_INDEX_TO_DBZ: Dict[int, float] = {
    13: 10.0,  # Azul marino (6-12 dBZ / lluvia muy débil)
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
        self._lock = threading.Lock()

    @property
    def is_configured(self) -> bool:
        """Indica si hay una clave de API de AEMET configurada."""
        return bool(settings.AEMET_API_KEY and len(settings.AEMET_API_KEY.strip()) > 10)

    def _get_fallback_bytes(self, aemet_code: str) -> Optional[bytes]:
        """Obtiene la última imagen válida desde memoria o disco."""
        if aemet_code in self._cache_bytes and self._cache_bytes[aemet_code]:
            return self._cache_bytes[aemet_code]
        try:
            disk_p = settings.RADAR_CACHE_DIR / f"aemet_regional_{aemet_code}.gif"
            if disk_p.exists():
                data = disk_p.read_bytes()
                if data and len(data) > 500:
                    self._cache_bytes[aemet_code] = data
                    return data
        except Exception:
            pass
        return None

    def fetch_regional_radar_gif(self, aemet_code: str, min_interval_sec: float = 300.0) -> Optional[bytes]:
        """
        Descarga la última imagen GIF del radar regional desde AEMET OpenData.
        Aplica proxy seguro desde el servidor, control thread-safe de rate-limits, persistencia en disco y fallback ininterrumpido.
        """
        if not self.is_configured:
            return self._get_fallback_bytes(aemet_code)

        with self._lock:
            # Comprobar intervalo mínimo entre descargas para no saturar la API de AEMET
            now_ts = time.time()
            last_ts = self._last_download_time.get(aemet_code, 0.0)
            if (now_ts - last_ts) < min_interval_sec:
                cached = self._get_fallback_bytes(aemet_code)
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
                        return self._get_fallback_bytes(aemet_code)
                    data = json.loads(resp.read().decode("utf-8", "ignore"))

                datos_url = data.get("datos")
                if not datos_url:
                    logger.warning(f"AEMET OpenData no devolvió URL de datos para radar {aemet_code}: {data.get('descripcion')}")
                    self._last_download_time[aemet_code] = now_ts - (min_interval_sec - 60.0)
                    return self._get_fallback_bytes(aemet_code)

                # Descargar imagen GIF desde la URL temporal devuelta
                img_req = urllib.request.Request(datos_url, headers={"User-Agent": settings.AEMET_USER_AGENT})
                with urllib.request.urlopen(img_req, timeout=15) as img_resp:
                    gif_bytes = img_resp.read()
                    if gif_bytes and len(gif_bytes) > 500:
                        self._last_download_time[aemet_code] = time.time()
                        self._cache_bytes[aemet_code] = gif_bytes
                        try:
                            disk_p = settings.RADAR_CACHE_DIR / f"aemet_regional_{aemet_code}.gif"
                            disk_p.write_bytes(gif_bytes)
                        except Exception:
                            pass
                        return gif_bytes
                    return self._get_fallback_bytes(aemet_code)

            except Exception as e:
                logger.warning(f"Error en AEMET OpenData para radar {aemet_code}: {e}")
                self._last_download_time[aemet_code] = now_ts - (min_interval_sec - 60.0)
                return self._get_fallback_bytes(aemet_code)

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

            # Generar tabla de conversión dinámica basada en los colores RGB de la paleta del GIF
            raw_pal = img.getpalette()
            lut = np.full(256, -9999.0, dtype=np.float32)
            if raw_pal and len(raw_pal) >= 3:
                palette_rgb = np.array(raw_pal, dtype=np.uint8).reshape(-1, 3)
                for idx in range(min(256, len(palette_rgb))):
                    r, g, b = int(palette_rgb[idx, 0]), int(palette_rgb[idx, 1]), int(palette_rgb[idx, 2])
                    # Descartar fondos, bordes de relieve y textos
                    if (r == 0 and g == 0 and b == 0) or (r == 127 and g == 127 and b == 127) or (r == 255 and g == 255 and b == 255) or (r == 0 and g == 43 and b == 102):
                        continue
                    
                    # Granizo / Púrpura (>60 dBZ)
                    if r > 180 and b > 180 and g < 120:
                        lut[idx] = 66.0
                    # Rojo (55-60 dBZ)
                    elif r > 200 and g < 70 and b < 70:
                        lut[idx] = 60.0
                    # Naranja intenso (50-55 dBZ)
                    elif r > 230 and 100 <= g < 180 and b < 70:
                        lut[idx] = 54.0
                    # Amarillo (45-50 dBZ)
                    elif r > 220 and g > 200 and b < 100:
                        lut[idx] = 48.0
                    # Verde brillante / verde medio (35-45 dBZ)
                    elif g > 160 and r < 130 and b < 130:
                        lut[idx] = 40.0
                    # Verde oscuro / oliva (28-35 dBZ)
                    elif 100 <= g <= 160 and r < 100 and b < 100:
                        lut[idx] = 32.0
                    # Cyan (22-28 dBZ)
                    elif b > 200 and g > 180 and r < 100:
                        lut[idx] = 25.0
                    # Azul claro / cielo (16-22 dBZ)
                    elif b > 200 and 100 <= g < 180 and r < 100:
                        lut[idx] = 18.0
                    # Azul oscuro / marino (8-16 dBZ)
                    elif b > 120 and g < 120 and r < 100:
                        lut[idx] = 12.0
            else:
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
                rain_mask = row_dbz >= 8.0
                if np.any(rain_mask):
                    target_cols = sub_x[rain_mask]
                    short_grid[gy, target_cols] = np.maximum(short_grid[gy, target_cols], row_dbz[rain_mask])

            pixels_mapped = int(np.sum(dbz_sub >= 8.0))
            logger.info(f"AEMET OpenData: Radar {st['name']} decodificado limpiamente ({pixels_mapped} celdas de lluvia real en rejilla Web Mercator).")
            return True

        except Exception as e:
            logger.warning(f"Error decodificando GIF de AEMET OpenData para {st_id}: {e}")
            return False


# Instancia singleton del servicio AEMET OpenData
aemet_opendata_service = AemetOpenDataService()
