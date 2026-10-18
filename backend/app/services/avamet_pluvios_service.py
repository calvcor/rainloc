"""
Servicio para la integración de datos pluviométricos y meteorológicos de AVAMET (MeteoXarxa Online - MXO).
Consulta bajo demanda la tabla de precipitaciones acumuladas (1h, 4h, 12h, 24h) y el catálogo de metadatos,
generando un GeoJSON con el mismo formato estándar de RainLoc.
"""
import asyncio
import json
import logging
import re
import html
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from app.config import DATA_DIR

logger = logging.getLogger("rainloc-backend.avamet_pluvios_service")

def clean_str(val: Any) -> str:
    """Limpia cadenas decodificando entidades HTML y normalizando comillas/apóstrofes."""
    if not val or not isinstance(val, str):
        return ""
    text = html.unescape(val)
    text = text.replace('′', "'").replace('’', "'").replace('`', "'").replace('´', "'")
    return text.strip()

MADRID_TZ = ZoneInfo("Europe/Madrid")

STATIC_AVAMET_PLUVIOS_FILE = DATA_DIR / "avamet_lluvias_estaciones.json"
AVAMET_PLUVIOS_GEOJSON_FILE = DATA_DIR / "avamet_lluvias.geojson"
AVAMET_METADATA_FILE = DATA_DIR / "avamet_metadata.json"
PUBLIC_DATA_DIR = Path(__file__).parent.parent.parent.parent / "data"

AVAMET_BASE_URL = "https://www.avamet.org"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

# TTL de frescura de datos de lluvia: 5 minutos (300 segundos)
SYNC_TTL_SECONDS = 300
# TTL de frescura de metadatos geográficos (2.4 MB): 24 horas (86400 segundos)
METADATA_SYNC_INTERVAL_SECONDS = 86400
# Umbral mínimo de estaciones esperadas para validar la sincronización
MIN_STATIONS_THRESHOLD = 500


class AvametPluviosService:
    def __init__(self):
        self._pluvios: List[Dict[str, Any]] = []
        self._pluvios_by_id: Dict[str, Dict[str, Any]] = {}
        self._metadata_by_id: Dict[str, Dict[str, Any]] = {}
        self._last_sync_time: Optional[datetime] = None
        self._last_meta_sync_time: Optional[datetime] = None
        self._sync_lock = asyncio.Lock()

        self._load_from_disk()

    def _load_from_disk(self):
        """Carga las estaciones y metadatos de AVAMET desde los ficheros locales si existen."""
        # 1. Cargar catálogo acumulativo de metadatos (coordenadas, altitud, municipio)
        if AVAMET_METADATA_FILE.exists():
            try:
                with open(AVAMET_METADATA_FILE, "r", encoding="utf-8") as f:
                    self._metadata_by_id = json.load(f)
                    mtime = datetime.fromtimestamp(AVAMET_METADATA_FILE.stat().st_mtime)
                    self._last_meta_sync_time = mtime
                    logger.info(f"Cargados metadatos de {len(self._metadata_by_id)} estaciones de AVAMET desde {AVAMET_METADATA_FILE} (actualizado: {mtime.strftime('%Y-%m-%d %H:%M')}).")
            except Exception as e:
                logger.error(f"Error al leer {AVAMET_METADATA_FILE}: {e}")

        # 2. Cargar estaciones de lluvia previas
        if STATIC_AVAMET_PLUVIOS_FILE.exists():
            try:
                with open(STATIC_AVAMET_PLUVIOS_FILE, "r", encoding="utf-8") as f:
                    self._pluvios = json.load(f)
                    self._pluvios_by_id = {str(p["id_estacion"]): p for p in self._pluvios if "id_estacion" in p}
                    mtime = datetime.fromtimestamp(STATIC_AVAMET_PLUVIOS_FILE.stat().st_mtime)
                    self._last_sync_time = mtime
                    logger.info(f"Cargados {len(self._pluvios)} pluviómetros de AVAMET desde caché local.")

                    # Respaldar metadatos desde las estaciones locales si el catálogo estaba vacío
                    if not self._metadata_by_id:
                        for p in self._pluvios:
                            sid = str(p.get("id_estacion", "")).strip()
                            if sid and p.get("lat") and p.get("lon"):
                                self._metadata_by_id[sid] = {
                                    "esta": sid,
                                    "muni": p.get("poblacion") or p.get("nombre"),
                                    "lati": str(p["lat"]),
                                    "logi": str(p["lon"]),
                                    "msnm": str(p.get("altitud")) if p.get("altitud") is not None else None,
                                    "webcam": p.get("webcam")
                                }
            except Exception as e:
                logger.error(f"Error al leer {STATIC_AVAMET_PLUVIOS_FILE}: {e}")

    def _is_fresh(self) -> bool:
        if not self._last_sync_time or len(self._pluvios) == 0:
            return False
        return (datetime.now() - self._last_sync_time).total_seconds() < SYNC_TTL_SECONDS

    def _should_refresh_metadata(self, force: bool = False) -> bool:
        """Determina si es necesario refrescar el catálogo de 2.4 MB de metadatos (1 vez cada 24h o si no hay datos)."""
        if force or not self._metadata_by_id or not self._last_meta_sync_time:
            return True
        return (datetime.now() - self._last_meta_sync_time).total_seconds() >= METADATA_SYNC_INTERVAL_SECONDS

    def sync_pluvios_metadata(self, force_metadata: bool = False) -> List[Dict[str, Any]]:
        """
        Descarga síncrona de los datos de AVAMET.
        - El catálogo de metadatos (coordenadas, altitud, municipio) se descarga como máximo una vez al día (24h).
        - En cada ciclo periódico (5 min) solo se descarga la tabla ligera de precipitaciones acumuladas (1h, 4h, 12h, 24h).
        """
        logger.info("Sincronizando pluviómetros de AVAMET...")

        try:
            # 1. Actualizar el catálogo de metadatos geográficos (2.4 MB) solo 1 vez cada 24 horas (o si no existe)
            if self._should_refresh_metadata(force=force_metadata):
                logger.info("Catálogo de metadatos de AVAMET caducado (>24h) o ausente. Descargando mxo-meteoxarxaonline.html (2.4 MB)...")
                meta_url = f"{AVAMET_BASE_URL}/mxo-meteoxarxaonline.html"
                req_meta = urllib.request.Request(meta_url, headers={"User-Agent": USER_AGENT})
                try:
                    with urllib.request.urlopen(req_meta, timeout=20) as resp:
                        html_meta = resp.read().decode("utf-8", "ignore")

                    m_meta = re.search(r"var data\s*=\s*(\[.*?\]);", html_meta, re.DOTALL)
                    if m_meta:
                        raw_meta = json.loads(m_meta.group(1))
                        updated_count = 0
                        for st in raw_meta:
                            sid = st.get("esta")
                            if sid:
                                clean_sid = str(sid).strip()
                                self._metadata_by_id[clean_sid] = st
                                updated_count += 1
                        
                        if updated_count > 0:
                            DATA_DIR.mkdir(parents=True, exist_ok=True)
                            with open(AVAMET_METADATA_FILE, "w", encoding="utf-8") as f:
                                json.dump(self._metadata_by_id, f, ensure_ascii=False, indent=2)
                            self._last_meta_sync_time = datetime.now()
                            logger.info(f"Metadatos de AVAMET actualizados con éxito: {updated_count} estaciones parseadas, {len(self._metadata_by_id)} en catálogo permanente.")
                    else:
                        logger.warning("No se encontró el patrón 'var data = [...]' en mxo-meteoxarxaonline.html. Se usará el catálogo previo.")
                except Exception as meta_err:
                    logger.warning(f"No se pudieron descargar metadatos frescos de AVAMET ({meta_err}). Se usará el catálogo permanente en memoria ({len(self._metadata_by_id)} estaciones).")
            else:
                elapsed_hrs = round((datetime.now() - self._last_meta_sync_time).total_seconds() / 3600, 1)
                logger.debug(f"Catálogo de metadatos de AVAMET vigente ({len(self._metadata_by_id)} estaciones, sync hace {elapsed_hrs}h). Omitiendo descarga pesada de 2.4 MB.")

            # 2. Descargar tabla de precipitación acumulada por intervalos (territorio completo 'pv')
            prec_url = f"{AVAMET_BASE_URL}/mxo-mxo-prec.php?territori=pv"
            req_prec = urllib.request.Request(prec_url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req_prec, timeout=20) as resp:
                html_prec = resp.read().decode("utf-8", "ignore")

            rows = re.findall(r"<tr[^>]*>(.*?)</tr>", html_prec, re.DOTALL | re.IGNORECASE)
            pluvios = []
            features = []
            current_comarca = ""
            seen_ids = set()

            for r in rows:
                comarca_match = re.search(r"class=[\"\x27]rComarca[\"\x27][^>]*>(.*?)</td>", r, re.IGNORECASE)
                if comarca_match:
                    current_comarca = clean_str(re.sub(r"<[^>]+>", "", comarca_match.group(1)))
                    continue

                id_match = re.search(r"mxo_i\.php\?id=([a-zA-Z0-9_-]+)", r)
                if not id_match:
                    continue
                st_id = id_match.group(1).strip()
                if st_id in seen_ids:
                    continue

                tds = re.findall(r"<td[^>]*>(.*?)</td>", r, re.DOTALL | re.IGNORECASE)
                if len(tds) < 11:
                    continue

                seen_ids.add(st_id)

                def _clean_val(td_html):
                    txt = re.sub(r"<[^>]+>", "", td_html).strip().replace(".", "").replace(",", ".")
                    try:
                        return round(float(txt), 2)
                    except Exception:
                        return 0.0

                st_name = clean_str(re.sub(r"<[^>]+>", "", tds[0]))
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

                meta = self._metadata_by_id.get(st_id, {})
                lat, lon = None, None
                try:
                    if meta.get("lati") and meta.get("logi"):
                        lat = float(meta["lati"])
                        lon = float(meta["logi"])
                except Exception:
                    lat, lon = None, None

                # Respaldo secundario desde estaciones previas si no estaba en metadatos
                if (lat is None or lon is None) and st_id in self._pluvios_by_id:
                    prev_p = self._pluvios_by_id[st_id]
                    lat = prev_p.get("lat")
                    lon = prev_p.get("lon")
                    if not meta:
                        meta = {
                            "muni": prev_p.get("poblacion"),
                            "msnm": prev_p.get("altitud"),
                            "temp": prev_p.get("temperatura"),
                            "hrel": prev_p.get("humedad"),
                            "vent": prev_p.get("viento_vel"),
                            "vent_max": prev_p.get("racha_max"),
                            "webcam": prev_p.get("webcam"),
                        }

                if lat is None or lon is None:
                    continue

                alt_str = meta.get("msnm")
                alt_float = None
                if alt_str is not None:
                    try:
                        alt_float = float(str(alt_str).replace(".", "").replace(",", "."))
                    except Exception:
                        alt_float = None

                poblacion = clean_str(meta.get("muni")) or st_name

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

            # Salvaguarda anti-vaciado: si por cualquier anomalía la cantidad de estaciones procesadas es anormalmente baja
            min_expected = max(300, int(len(self._pluvios) * 0.5)) if self._pluvios else MIN_STATIONS_THRESHOLD
            if len(pluvios) < min_expected:
                logger.warning(
                    f"AVAMET: Sincronización incompleta o anómala ({len(pluvios)} estaciones procesadas < mínimo esperado {min_expected}). "
                    f"Se aborta la sobrescritura en disco para proteger los {len(self._pluvios)} pluviómetros previos."
                )
                return self._pluvios

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

    def _trigger_bg_sync(self):
        try:
            loop = asyncio.get_running_loop()
            if not getattr(self, "_bg_syncing", False):
                self._bg_syncing = True
                loop.create_task(self._do_bg_sync())
        except RuntimeError:
            pass

    async def _do_bg_sync(self):
        try:
            async with self._sync_lock:
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, self.sync_pluvios_metadata)
        except Exception as e:
            logger.warning(f"Error en sync background AVAMET pluvios: {e}")
        finally:
            self._bg_syncing = False

    async def ensure_fresh_data(self, force: bool = False):
        """Garantiza frescura bajo demanda con TTL de 5 minutos sin bloquear peticiones si ya hay datos."""
        if len(self._pluvios) > 0:
            if not self._is_fresh() or force:
                self._trigger_bg_sync()
            return

        async with self._sync_lock:
            if len(self._pluvios) == 0 or force:
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, self.sync_pluvios_metadata)

    def is_revalidating(self) -> bool:
        if getattr(self, "_bg_syncing", False):
            return True
        return not self._is_fresh()

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
