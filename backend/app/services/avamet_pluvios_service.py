"""
Servicio para la integración de datos pluviométricos y meteorológicos de AVAMET (MeteoXarxa Online - MXO).
Consulta bajo demanda la tabla de precipitaciones acumuladas (1h, 4h, 12h, 24h) y el catálogo de metadatos,
generando un GeoJSON con el mismo formato estándar de RainLoc.
"""
import asyncio
import json
import logging
import re
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from app.config import DATA_DIR

logger = logging.getLogger("rainloc-backend.avamet_pluvios_service")

MADRID_TZ = ZoneInfo("Europe/Madrid")

STATIC_AVAMET_PLUVIOS_FILE = DATA_DIR / "avamet_lluvias_estaciones.json"
AVAMET_PLUVIOS_GEOJSON_FILE = DATA_DIR / "avamet_lluvias.geojson"
PUBLIC_DATA_DIR = Path(__file__).parent.parent.parent.parent / "data"

AVAMET_BASE_URL = "https://www.avamet.org"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

# TTL de frescura de datos: 5 minutos (300 segundos)
SYNC_TTL_SECONDS = 300


class AvametPluviosService:
    def __init__(self):
        self._pluvios: List[Dict[str, Any]] = []
        self._pluvios_by_id: Dict[str, Dict[str, Any]] = {}
        self._last_sync_time: Optional[datetime] = None
        self._sync_lock = asyncio.Lock()

        self._load_from_disk()

    def _load_from_disk(self):
        """Carga las estaciones de lluvia de AVAMET desde el fichero local si existe."""
        if STATIC_AVAMET_PLUVIOS_FILE.exists():
            try:
                with open(STATIC_AVAMET_PLUVIOS_FILE, "r", encoding="utf-8") as f:
                    self._pluvios = json.load(f)
                    self._pluvios_by_id = {str(p["id_estacion"]): p for p in self._pluvios if "id_estacion" in p}
                    mtime = datetime.fromtimestamp(STATIC_AVAMET_PLUVIOS_FILE.stat().st_mtime)
                    self._last_sync_time = mtime
                    logger.info(f"Cargados {len(self._pluvios)} pluviómetros de AVAMET desde caché local.")
            except Exception as e:
                logger.error(f"Error al leer {STATIC_AVAMET_PLUVIOS_FILE}: {e}")

    def _is_fresh(self) -> bool:
        if not self._last_sync_time or len(self._pluvios) == 0:
            return False
        return (datetime.now() - self._last_sync_time).total_seconds() < SYNC_TTL_SECONDS

    def sync_pluvios_metadata(self) -> List[Dict[str, Any]]:
        """
        Descarga síncrona de las páginas de AVAMET (metadatos y tabla de lluvia acumulada),
        extrae los acumulados en 1h, 4h, 12h y 24h, y guarda los ficheros GeoJSON y JSON locales.
        """
        logger.info("Sincronizando pluviómetros en tiempo real desde avamet.org/mxo-mxo-prec.php...")

        try:
            # 1. Descargar catálogo de metadatos geográficos (lat, lon, muni, altitud)
            meta_url = f"{AVAMET_BASE_URL}/mxo-meteoxarxaonline.html"
            req_meta = urllib.request.Request(meta_url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req_meta, timeout=15) as resp:
                html_meta = resp.read().decode("utf-8", "ignore")

            m_meta = re.search(r"var data\s*=\s*(\[.*?\]);", html_meta, re.DOTALL)
            metadata_by_id = {}
            if m_meta:
                try:
                    for st in json.loads(m_meta.group(1)):
                        sid = st.get("esta")
                        if sid:
                            metadata_by_id[str(sid).strip()] = st
                except Exception as err:
                    logger.warning(f"Error parseando JSON de metadatos AVAMET: {err}")

            # 2. Descargar tabla de precipitación acumulada por intervalos
            prec_url = f"{AVAMET_BASE_URL}/mxo-mxo-prec.php"
            req_prec = urllib.request.Request(prec_url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req_prec, timeout=15) as resp:
                html_prec = resp.read().decode("utf-8", "ignore")

            rows = re.findall(r"<tr[^>]*>(.*?)</tr>", html_prec, re.DOTALL | re.IGNORECASE)
            pluvios = []
            features = []
            current_comarca = ""

            for r in rows:
                comarca_match = re.search(r"class=[\"\x27]rComarca[\"\x27][^>]*>(.*?)</td>", r, re.IGNORECASE)
                if comarca_match:
                    current_comarca = re.sub(r"<[^>]+>", "", comarca_match.group(1)).strip()
                    continue

                id_match = re.search(r"mxo_i\.php\?id=([a-zA-Z0-9_-]+)", r)
                if not id_match:
                    continue
                st_id = id_match.group(1).strip()

                tds = re.findall(r"<td[^>]*>(.*?)</td>", r, re.DOTALL | re.IGNORECASE)
                if len(tds) < 11:
                    continue

                def _clean_val(td_html):
                    txt = re.sub(r"<[^>]+>", "", td_html).strip().replace(".", "").replace(",", ".")
                    try:
                        return round(float(txt), 2)
                    except Exception:
                        return 0.0

                st_name = re.sub(r"<[^>]+>", "", tds[0]).strip()
                prec_hoy = _clean_val(tds[1])
                prec_mes = _clean_val(tds[4]) if len(tds) > 4 else 0.0
                prec_any = _clean_val(tds[5]) if len(tds) > 5 else 0.0
                prec_1h = _clean_val(tds[6]) if len(tds) > 6 else 0.0
                prec_4h = _clean_val(tds[7]) if len(tds) > 7 else 0.0
                prec_8h = _clean_val(tds[8]) if len(tds) > 8 else 0.0
                prec_12h = _clean_val(tds[9]) if len(tds) > 9 else 0.0
                prec_24h = _clean_val(tds[10]) if len(tds) > 10 else 0.0

                title_match = re.search(r"title=[\"\x27]([\d\- :]+)[\"\x27]", r)
                ultima_hora = title_match.group(1).strip() if title_match else ""

                meta = metadata_by_id.get(st_id, {})
                try:
                    lat = float(meta["lati"]) if meta.get("lati") else None
                    lon = float(meta["logi"]) if meta.get("logi") else None
                except Exception:
                    lat, lon = None, None

                if lat is None or lon is None:
                    continue

                alt_str = meta.get("msnm")
                alt_float = None
                if alt_str:
                    try:
                        alt_float = float(str(alt_str).replace(".", "").replace(",", "."))
                    except Exception:
                        alt_float = None

                poblacion = meta.get("muni") or st_name

                pluvio_obj = {
                    "id_estacion": st_id,
                    "codigo": st_id,
                    "nombre": st_name,
                    "tipo": "Pluviómetro",
                    "red": "AVAMET",
                    "lat": round(lat, 6),
                    "lon": round(lon, 6),
                    "poblacion": poblacion,
                    "comarca": current_comarca,
                    "altitud": alt_float,
                    "estado": True,
                    "lluvia_1h": prec_1h,
                    "precipitacion_1h": prec_1h,
                    "fecha_1h": ultima_hora or meta.get("data_ini") or "",
                    "lluvia_4h": prec_4h,
                    "precipitacion_4h": prec_4h,
                    "fecha_4h": ultima_hora or meta.get("data_ini") or "",
                    "lluvia_12h": prec_12h,
                    "precipitacion_12h": prec_12h,
                    "fecha_12h": ultima_hora or meta.get("data_ini") or "",
                    "lluvia_24h": prec_24h,
                    "precipitacion_24h": prec_24h,
                    "fecha_24h": ultima_hora or meta.get("data_ini") or "",
                    "lluvia_hoy": prec_hoy,
                    "lluvia_mes": prec_mes,
                    "lluvia_any": prec_any,
                    "temperatura": meta.get("temp"),
                    "humedad": meta.get("hrel"),
                    "viento_vel": meta.get("vent"),
                    "racha_max": meta.get("vent_max"),
                    "webcam": meta.get("webcam"),
                    "ultima_hora": ultima_hora or meta.get("data_ini") or "",
                    "unidad": "mm",
                }
                pluvios.append(pluvio_obj)

                features.append({
                    "type": "Feature",
                    "geometry": {
                        "type": "Point",
                        "coordinates": [round(lon, 6), round(lat, 6)],
                    },
                    "properties": pluvio_obj,
                })

            geojson = {
                "type": "FeatureCollection",
                "features": features,
            }

            DATA_DIR.mkdir(parents=True, exist_ok=True)
            with open(STATIC_AVAMET_PLUVIOS_FILE, "w", encoding="utf-8") as f:
                json.dump(pluvios, f, ensure_ascii=False, indent=2)

            with open(AVAMET_PLUVIOS_GEOJSON_FILE, "w", encoding="utf-8") as f:
                json.dump(geojson, f, ensure_ascii=False, indent=2)

            # Guardar también en public data si existe
            if PUBLIC_DATA_DIR.exists():
                with open(PUBLIC_DATA_DIR / "avamet_lluvias.geojson", "w", encoding="utf-8") as f:
                    json.dump(geojson, f, ensure_ascii=False, indent=2)

            self._pluvios = pluvios
            self._pluvios_by_id = {str(p["id_estacion"]): p for p in pluvios}
            self._last_sync_time = datetime.now()
            logger.info(f"Sincronizados {len(pluvios)} pluviómetros de AVAMET correctamente.")
            return pluvios

        except Exception as e:
            logger.error(f"Error al sincronizar datos de AVAMET: {e}")
            return self._pluvios

    async def ensure_fresh_data(self, force: bool = False):
        """Garantiza frescura bajo demanda con TTL de 5 minutos."""
        if not self._is_fresh() or force:
            async with self._sync_lock:
                if not self._is_fresh() or force:
                    loop = asyncio.get_running_loop()
                    await loop.run_in_executor(None, self.sync_pluvios_metadata)

    async def get_pluvios(self, auto_sync: bool = True) -> List[Dict[str, Any]]:
        if auto_sync:
            await self.ensure_fresh_data()
        return self._pluvios

    async def get_pluvios_geojson(self, auto_sync: bool = True) -> Dict[str, Any]:
        if auto_sync:
            await self.ensure_fresh_data()

        if AVAMET_PLUVIOS_GEOJSON_FILE.exists():
            try:
                with open(AVAMET_PLUVIOS_GEOJSON_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Error leyendo {AVAMET_PLUVIOS_GEOJSON_FILE}: {e}")

        features = [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [p["lon"], p["lat"]]},
                "properties": p,
            }
            for p in self._pluvios
        ]
        return {"type": "FeatureCollection", "features": features}

    def get_pluvio_by_id(self, id_or_code: str) -> Optional[Dict[str, Any]]:
        key = str(id_or_code).strip().upper()
        for p in self._pluvios:
            if str(p.get("id_estacion", "")).upper() == key or str(p.get("codigo", "")).upper() == key:
                return p
        return None


avamet_pluvios_service = AvametPluviosService()
