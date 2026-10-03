"""
Servicio para la integración de datos pluviométricos y meteorológicos del S.A.I.H. Hidrosur
(Demarcación Hidrográfica de las Cuencas Mediterráneas Andaluzas / Junta de Andalucía).
Consulta bajo demanda la telemetría oficial de precipitaciones en tiempo real (1h, 4h, 12h y 24h),
correlaciona con los metadatos geográficos de las ~147 estaciones y genera GeoJSON estandarizado para RainLoc.
"""
import asyncio
import json
import logging
import re
import html
import urllib.request
import ssl
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from app.config import DATA_DIR

logger = logging.getLogger("rainloc-backend.hidrosur_pluvios_service")

MADRID_TZ = ZoneInfo("Europe/Madrid")
UTC_TZ = timezone.utc

STATIC_HIDROSUR_PLUVIOS_FILE = DATA_DIR / "hidrosur_lluvias_estaciones.json"
HIDROSUR_PLUVIOS_GEOJSON_FILE = DATA_DIR / "hidrosur_lluvias.geojson"
HIDROSUR_METADATA_FILE = DATA_DIR / "hidrosur_metadata.json"
PUBLIC_DATA_DIR = Path(__file__).parent.parent.parent.parent / "data"

HIDROSUR_METADATA_URL = "https://www.redhidrosurmedioambiente.es/saih/assets/visorSAIH/capas/Pluviometricas.json"
HIDROSUR_RESUMEN_URL = "https://www.redhidrosurmedioambiente.es/saih/resumen/precipitacion"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

# TTL de frescura de datos: 5 minutos (300 segundos)
SYNC_TTL_SECONDS = 300


class HidrosurPluviosService:
    def __init__(self):
        self._pluvios: List[Dict[str, Any]] = []
        self._pluvios_by_id: Dict[str, Dict[str, Any]] = {}
        self._metadata_by_id: Dict[str, Dict[str, Any]] = {}
        self._history_buffer: Dict[str, List[Dict[str, Any]]] = {}  # {station_cod: [{'ts': dt, 'r1h': float}]}
        self._last_sync_time: Optional[datetime] = None
        self._last_meta_sync_time: Optional[datetime] = None
        self._sync_lock = asyncio.Lock()

        self._load_from_disk()

    def _load_from_disk(self):
        """Carga las estaciones y metadatos de Hidrosur desde los ficheros locales si existen."""
        if HIDROSUR_METADATA_FILE.exists():
            try:
                with open(HIDROSUR_METADATA_FILE, "r", encoding="utf-8") as f:
                    self._metadata_by_id = json.load(f)
            except Exception as e:
                logger.error(f"Error al leer {HIDROSUR_METADATA_FILE}: {e}")

        if STATIC_HIDROSUR_PLUVIOS_FILE.exists():
            try:
                with open(STATIC_HIDROSUR_PLUVIOS_FILE, "r", encoding="utf-8") as f:
                    self._pluvios = json.load(f)
                    self._pluvios_by_id = {str(p["id_estacion"]): p for p in self._pluvios if "id_estacion" in p}
                    mtime = datetime.fromtimestamp(STATIC_HIDROSUR_PLUVIOS_FILE.stat().st_mtime)
                    self._last_sync_time = mtime
                    logger.info(f"Cargados {len(self._pluvios)} pluviómetros de SAIH Hidrosur desde caché local.")
            except Exception as e:
                logger.error(f"Error al leer {STATIC_HIDROSUR_PLUVIOS_FILE}: {e}")

    def _is_fresh(self) -> bool:
        if not self._last_sync_time or len(self._pluvios) == 0:
            return False
        return (datetime.now() - self._last_sync_time).total_seconds() < SYNC_TTL_SECONDS

    def _fetch_metadata(self) -> Dict[str, Dict[str, Any]]:
        """Descarga el catálogo de metadatos de estaciones pluviométricas de Hidrosur si no está en memoria o tiene >24h."""
        if self._metadata_by_id and self._last_meta_sync_time:
            if (datetime.now() - self._last_meta_sync_time).total_seconds() < 86400:
                return self._metadata_by_id

        try:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE

            req = urllib.request.Request(HIDROSUR_METADATA_URL, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, context=ctx, timeout=15) as resp:
                meta_geojson = json.loads(resp.read().decode("utf-8", "ignore"))

            meta_map = {}
            for feat in meta_geojson.get("features", []):
                props = feat.get("properties", {})
                geom = feat.get("geometry", {})
                coords = geom.get("coordinates") or [props.get("Longitud"), props.get("Latitud")]

                cod = str(props.get("COD_") or props.get("OBJECTID") or "").strip()
                if not cod:
                    continue

                lat = float(coords[1]) if len(coords) > 1 and coords[1] is not None else props.get("Latitud")
                lon = float(coords[0]) if len(coords) > 0 and coords[0] is not None else props.get("Longitud")

                clean_name = str(props.get("Nombre") or f"Estación {cod}").strip()

                meta_map[cod] = {
                    "cod": cod,
                    "nombre": clean_name,
                    "lat": lat,
                    "lon": lon,
                    "cota": props.get("Cota"),
                    "municipio": props.get("Municipio", ""),
                    "provincia": props.get("Provincia", ""),
                    "subsistema": props.get("Subsistema", ""),
                    "sistema": props.get("Sistema", ""),
                    "rio": props.get("Río", "").strip(),
                    "tipo_estacion": props.get("Tipo_de_Es", "Pluviométrica"),
                    "estado": props.get("Estado", "activa") == "activa"
                }

            if meta_map:
                self._metadata_by_id = meta_map
                self._last_meta_sync_time = datetime.now()
                DATA_DIR.mkdir(parents=True, exist_ok=True)
                with open(HIDROSUR_METADATA_FILE, "w", encoding="utf-8") as f:
                    json.dump(meta_map, f, ensure_ascii=False, indent=2)
                logger.info(f"Metadatos de {len(meta_map)} estaciones SAIH Hidrosur actualizados.")
        except Exception as e:
            logger.warning(f"No se pudieron descargar metadatos de SAIH Hidrosur: {e}. Usando metadatos en caché.")

        return self._metadata_by_id

    def sync_pluvios_metadata(self) -> List[Dict[str, Any]]:
        """
        Descarga la tabla en tiempo real de Hidrosur, computa acumulados de 1h, 4h, 12h y 24h
        y actualiza los ficheros en disco.
        """
        metadata = self._fetch_metadata()

        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        req = urllib.request.Request(HIDROSUR_RESUMEN_URL, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, context=ctx, timeout=15) as resp:
                page_html = resp.read().decode("utf-8", "ignore")
        except Exception as e:
            logger.error(f"Error al descargar resumen en tiempo real de SAIH Hidrosur: {e}")
            return self._pluvios

        # Parsear tabla HTML
        rows = re.findall(r"<tr[^>]*>(.*?)</tr>", page_html, re.DOTALL)
        
        def parse_num(val_str: Optional[str]) -> float:
            if not val_str:
                return 0.0
            val_clean = val_str.replace("mm", "").replace(",", ".").strip().lower()
            if val_clean in ("n/d", "-", "", "null", "none"):
                return 0.0
            try:
                return round(float(val_clean), 2)
            except:
                return 0.0

        now_madrid = datetime.now(MADRID_TZ)
        now_str = now_madrid.strftime("%d-%m-%Y %H:%M")
        now_iso = now_madrid.strftime("%Y-%m-%d %H:%M:%S")

        matched_stations: Dict[str, Dict[str, Any]] = {}

        for r in rows:
            cols = [html.unescape(re.sub(r"<[^>]+>", "", c).strip()) for c in re.findall(r"<td[^>]*>(.*?)</td>", r, re.DOTALL)]
            if len(cols) >= 8:
                # Estructura: [Número, Nombre, Provincia, Última hora, Hora anterior, Acumulado 12h, Acumulado 24h, Acumulado Hoy, Acumulado Ayer, ...]
                cod = cols[0].strip()
                nombre_tabla = cols[1].strip()
                provincia_tabla = cols[2].strip()
                r1h = parse_num(cols[3])
                r_prev = parse_num(cols[4])
                r12h = parse_num(cols[5])
                r24h = parse_num(cols[6])
                rhoy = parse_num(cols[7])
                rayer = parse_num(cols[8]) if len(cols) > 8 else 0.0

                chart_m = re.search(r"grafica/([a-zA-Z0-9_]+)", r)
                sensor_code = chart_m.group(1) if chart_m else f"{cod.zfill(3)}P01"

                # Buscar metadatos
                meta = metadata.get(cod)
                if not meta:
                    # Búsqueda por coincidencia de nombre
                    clean_target = re.sub(r"\s*\(.*?\)\s*", "", nombre_tabla).strip().upper()
                    for m_cod, m_val in metadata.items():
                        m_name_clean = re.sub(r"\s*\(.*?\)\s*", "", m_val["nombre"]).strip().upper()
                        if clean_target == m_name_clean or clean_target in m_name_clean:
                            meta = m_val
                            break

                if not meta or meta.get("lat") is None or meta.get("lon") is None:
                    continue

                # Actualizar buffer histórico para cálculo de 4h
                station_key = meta["cod"]
                if station_key not in self._history_buffer:
                    self._history_buffer[station_key] = []

                # Mantener solo lecturas de las últimas 4 horas
                cutoff = now_madrid - timedelta(hours=4, minutes=15)
                self._history_buffer[station_key] = [
                    item for item in self._history_buffer[station_key] if item["ts"] > cutoff
                ]
                self._history_buffer[station_key].append({"ts": now_madrid, "r1h": r1h})

                # Cálculo de 4h: suma de las últimas horas registradas en ventana móvil
                # Si el buffer tiene pocas muestras (arranque en frío), estimar con (r1h + r_prev)
                # acotado consistentemente entre r1h y r12h / r24h.
                buf_sum = sum(item["r1h"] for item in self._history_buffer[station_key])
                estimated_4h = max(r1h, r1h + r_prev, buf_sum)
                r4h = round(min(max(r1h, estimated_4h), max(r12h, r24h, r1h)), 2)

                st_dict = {
                    "id_estacion": f"hidrosur_{meta['cod']}",
                    "codigo": meta["cod"],
                    "sensor_code": sensor_code,
                    "nombre": meta["nombre"],
                    "tipo": "Pluviómetro",
                    "red": "HIDROSUR",
                    "lat": meta["lat"],
                    "lon": meta["lon"],
                    "altitud": meta.get("cota"),
                    "poblacion": meta.get("municipio") or meta["nombre"],
                    "municipio": meta.get("municipio") or "",
                    "provincia": meta.get("provincia") or provincia_tabla,
                    "subsistema": meta.get("subsistema") or "",
                    "sistema": meta.get("sistema") or "",
                    "rio": meta.get("rio") or "",
                    "estado": meta.get("estado", True),
                    "lluvia_1h": r1h,
                    "precipitacion_1h": r1h,
                    "fecha_1h": now_iso,
                    "lluvia_4h": r4h,
                    "precipitacion_4h": r4h,
                    "fecha_4h": now_iso,
                    "lluvia_12h": r12h,
                    "precipitacion_12h": r12h,
                    "fecha_12h": now_iso,
                    "lluvia_24h": r24h,
                    "precipitacion_24h": r24h,
                    "fecha_24h": now_iso,
                    "lluvia_hoy": rhoy,
                    "lluvia_ayer": rayer,
                    "ultima_hora": now_iso,
                    "fuente": "Junta de Andalucía - S.A.I.H. Hidrosur",
                    "unidad": "mm"
                }

                matched_stations[meta["cod"]] = st_dict

        # Para las estaciones de metadatos que no aparezcan en la tabla instantánea,
        # incorporarlas con valores a 0.0 o estado inactivo para mantener el catálogo completo de 147 puntos.
        for cod, meta in metadata.items():
            if cod not in matched_stations and meta.get("lat") is not None and meta.get("lon") is not None:
                st_dict = {
                    "id_estacion": f"hidrosur_{meta['cod']}",
                    "codigo": meta["cod"],
                    "sensor_code": f"{cod.zfill(3)}P01",
                    "nombre": meta["nombre"],
                    "tipo": "Pluviómetro",
                    "red": "HIDROSUR",
                    "lat": meta["lat"],
                    "lon": meta["lon"],
                    "altitud": meta.get("cota"),
                    "poblacion": meta.get("municipio") or meta["nombre"],
                    "municipio": meta.get("municipio") or "",
                    "provincia": meta.get("provincia") or "",
                    "subsistema": meta.get("subsistema") or "",
                    "sistema": meta.get("sistema") or "",
                    "rio": meta.get("rio") or "",
                    "estado": meta.get("estado", True),
                    "lluvia_1h": 0.0,
                    "precipitacion_1h": 0.0,
                    "fecha_1h": now_iso,
                    "lluvia_4h": 0.0,
                    "precipitacion_4h": 0.0,
                    "fecha_4h": now_iso,
                    "lluvia_12h": 0.0,
                    "precipitacion_12h": 0.0,
                    "fecha_12h": now_iso,
                    "lluvia_24h": 0.0,
                    "precipitacion_24h": 0.0,
                    "fecha_24h": now_iso,
                    "lluvia_hoy": 0.0,
                    "lluvia_ayer": 0.0,
                    "ultima_hora": now_iso,
                    "fuente": "Junta de Andalucía - S.A.I.H. Hidrosur",
                    "unidad": "mm"
                }
                matched_stations[cod] = st_dict

        pluvios_list = list(matched_stations.values())

        if len(pluvios_list) > 0:
            self._pluvios = pluvios_list
            self._pluvios_by_id = {str(p["id_estacion"]): p for p in self._pluvios}
            for p in self._pluvios:
                self._pluvios_by_id[str(p["codigo"])] = p
            self._last_sync_time = datetime.now()

            # Guardar JSON y GeoJSON en disco
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            with open(STATIC_HIDROSUR_PLUVIOS_FILE, "w", encoding="utf-8") as f:
                json.dump(self._pluvios, f, ensure_ascii=False, indent=2)

            geojson_data = {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "geometry": {"type": "Point", "coordinates": [p["lon"], p["lat"]]},
                        "properties": p,
                    }
                    for p in self._pluvios
                ],
            }
            with open(HIDROSUR_PLUVIOS_GEOJSON_FILE, "w", encoding="utf-8") as f:
                json.dump(geojson_data, f, ensure_ascii=False, indent=2)

            # Copiar a directorio público estático si existe
            if PUBLIC_DATA_DIR.exists():
                try:
                    with open(PUBLIC_DATA_DIR / "hidrosur_lluvias.geojson", "w", encoding="utf-8") as f:
                        json.dump(geojson_data, f, ensure_ascii=False, indent=2)
                except Exception as e:
                    logger.warning(f"No se pudo copiar a {PUBLIC_DATA_DIR}: {e}")

            logger.info(f"Sincronizados con éxito {len(self._pluvios)} pluviómetros de SAIH Hidrosur.")

        return self._pluvios

    async def ensure_fresh_data(self):
        """Garantiza datos frescos de SAIH Hidrosur (< 5 min) bajo demanda."""
        if self._is_fresh():
            return
        async with self._sync_lock:
            if self._is_fresh():
                return
            await asyncio.to_thread(self.sync_pluvios_metadata)

    async def get_pluvios(self) -> List[Dict[str, Any]]:
        """Obtiene la lista plana de pluviómetros de SAIH Hidrosur."""
        await self.ensure_fresh_data()
        return self._pluvios

    async def get_pluvios_geojson(self) -> Dict[str, Any]:
        """Obtiene el FeatureCollection GeoJSON de pluviómetros de SAIH Hidrosur."""
        pluvios = await self.get_pluvios()
        features = [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [p["lon"], p["lat"]]},
                "properties": p,
            }
            for p in pluvios
        ]
        return {"type": "FeatureCollection", "features": features}

    def get_pluvio_by_id(self, id_or_code: str) -> Optional[Dict[str, Any]]:
        """Busca un pluviómetro de SAIH Hidrosur por su id_estacion o código."""
        return self._pluvios_by_id.get(str(id_or_code))


hidrosur_pluvios_service = HidrosurPluviosService()
