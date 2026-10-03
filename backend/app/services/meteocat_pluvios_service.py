"""
Servicio para la integración de datos pluviométricos y meteorológicos de la XEMA (Meteocat / SMC)
a través del portal oficial de Dades Obertes de la Generalitat de Catalunya.
Consulta bajo demanda las observaciones semi-horarias de las últimas 24h, calcula acumulados en 1h, 4h, 12h y 24h,
y genera GeoJSON estandarizado para RainLoc.
"""
import asyncio
import json
import logging
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from app.config import DATA_DIR

logger = logging.getLogger("rainloc-backend.meteocat_pluvios_service")

MADRID_TZ = ZoneInfo("Europe/Madrid")
UTC_TZ = timezone.utc

STATIC_METEOCAT_PLUVIOS_FILE = DATA_DIR / "meteocat_lluvias_estaciones.json"
METEOCAT_PLUVIOS_GEOJSON_FILE = DATA_DIR / "meteocat_lluvias.geojson"
METEOCAT_METADATA_FILE = DATA_DIR / "meteocat_metadata.json"
PUBLIC_DATA_DIR = Path(__file__).parent.parent.parent.parent / "data"

GENCAT_METADATA_URL = "https://analisi.transparenciacatalunya.cat/resource/yqwd-vj5e.json"
GENCAT_OBSERVATIONS_URL = "https://analisi.transparenciacatalunya.cat/resource/nzvn-apee.json"
USER_AGENT = "RainLoc/1.0 (https://rainloc.es; contact@rainloc.es)"

# TTL de frescura de datos: 5 minutos (300 segundos)
SYNC_TTL_SECONDS = 300


class MeteocatPluviosService:
    def __init__(self):
        self._pluvios: List[Dict[str, Any]] = []
        self._pluvios_by_id: Dict[str, Dict[str, Any]] = {}
        self._metadata_by_id: Dict[str, Dict[str, Any]] = {}
        self._last_sync_time: Optional[datetime] = None
        self._last_meta_sync_time: Optional[datetime] = None
        self._sync_lock = asyncio.Lock()

        self._load_from_disk()

    def _load_from_disk(self):
        """Carga las estaciones y metadatos de Meteocat desde los ficheros locales si existen."""
        if METEOCAT_METADATA_FILE.exists():
            try:
                with open(METEOCAT_METADATA_FILE, "r", encoding="utf-8") as f:
                    raw_meta = json.load(f)
                    self._metadata_by_id = {
                        str(k): v for k, v in raw_meta.items()
                        if str(v.get("codi_estat_ema")) == "2" or str(v.get("nom_estat_ema", "")).lower() == "operativa"
                    }
            except Exception as e:
                logger.error(f"Error al leer {METEOCAT_METADATA_FILE}: {e}")

        if STATIC_METEOCAT_PLUVIOS_FILE.exists():
            try:
                with open(STATIC_METEOCAT_PLUVIOS_FILE, "r", encoding="utf-8") as f:
                    raw_pluvios = json.load(f)
                    # Descartar estaciones desmanteladas guardadas en caché antigua de disco
                    self._pluvios = [
                        p for p in raw_pluvios
                        if p.get("codigo") not in ("Y9",) and p.get("id_estacion") not in ("Y9",)
                    ]
                    self._pluvios_by_id = {str(p["id_estacion"]): p for p in self._pluvios if "id_estacion" in p}
                    mtime = datetime.fromtimestamp(STATIC_METEOCAT_PLUVIOS_FILE.stat().st_mtime)
                    self._last_sync_time = mtime
                    logger.info(f"Cargados {len(self._pluvios)} pluviómetros de Meteocat desde caché local.")
            except Exception as e:
                logger.error(f"Error al leer {STATIC_METEOCAT_PLUVIOS_FILE}: {e}")

    def _is_fresh(self) -> bool:
        if not self._last_sync_time or len(self._pluvios) == 0:
            return False
        return (datetime.now() - self._last_sync_time).total_seconds() < SYNC_TTL_SECONDS

    def _fetch_metadata(self) -> Dict[str, Dict[str, Any]]:
        """Descarga el catálogo de metadatos de estaciones XEMA si no está en memoria o está desactualizado (>24h)."""
        if self._metadata_by_id and self._last_meta_sync_time:
            if (datetime.now() - self._last_meta_sync_time).total_seconds() < 86400:
                return self._metadata_by_id

        try:
            params = {
                "$limit": 1000,
                "$where": "codi_estat_ema = '2' or nom_estat_ema = 'Operativa'"
            }
            url = f"{GENCAT_METADATA_URL}?{urllib.parse.urlencode(params)}"
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8", "ignore"))

            meta_map = {}
            for item in data:
                st_id = str(item.get("codi_estacio") or "").strip()
                if st_id:
                    meta_map[st_id] = item

            if meta_map:
                self._metadata_by_id = meta_map
                self._last_meta_sync_time = datetime.now()
                DATA_DIR.mkdir(parents=True, exist_ok=True)
                with open(METEOCAT_METADATA_FILE, "w", encoding="utf-8") as f:
                    json.dump(meta_map, f, ensure_ascii=False, indent=2)
                logger.info(f"Metadatos de {len(meta_map)} estaciones XEMA operativas actualizados.")
        except Exception as e:
            logger.warning(f"No se pudieron descargar metadatos de Meteocat: {e}. Usando metadatos en caché.")

        return self._metadata_by_id

    def sync_pluvios_metadata(self) -> List[Dict[str, Any]]:
        """
        Descarga síncrona de las observaciones de Meteocat / SMC desde Dades Obertes (Gencat).
        Calcula los acumulados en 1h, 4h, 12h y 24h, y guarda los ficheros GeoJSON y JSON locales.
        """
        logger.info("Sincronizando pluviómetros en tiempo real desde Dades Obertes Gencat (Meteocat XEMA)...")

        metadata_by_id = self._fetch_metadata()
        now_utc = datetime.now(timezone.utc)
        since_26h = (now_utc - timedelta(hours=26)).strftime("%Y-%m-%dT%H:%M:%S.000")
        since_2h = (now_utc - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%S.000")

        try:
            # 1. Descargar observaciones de precipitación (Variable 35) de las últimas 26h
            params_prec = {
                "$where": f"codi_variable = '35' and data_lectura >= '{since_26h}'",
                "$limit": 50000,
                "$order": "data_lectura ASC",
            }
            url_prec = f"{GENCAT_OBSERVATIONS_URL}?{urllib.parse.urlencode(params_prec)}"
            req_prec = urllib.request.Request(url_prec, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
            with urllib.request.urlopen(req_prec, timeout=20) as resp:
                prec_records = json.loads(resp.read().decode("utf-8", "ignore"))

            # 2. Descargar variables meteorológicas complementarias de las últimas 2h (temperatura 32, humedad 33, viento 30/31, racha 50, presion 34)
            params_extra = {
                "$where": f"codi_variable in ('30', '31', '32', '33', '34', '50') and data_lectura >= '{since_2h}'",
                "$limit": 15000,
                "$order": "data_lectura DESC",
            }
            url_extra = f"{GENCAT_OBSERVATIONS_URL}?{urllib.parse.urlencode(params_extra)}"
            req_extra = urllib.request.Request(url_extra, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
            with urllib.request.urlopen(req_extra, timeout=15) as resp:
                extra_records = json.loads(resp.read().decode("utf-8", "ignore"))

            # Agrupar variables meteorológicas por estación
            extras_by_st: Dict[str, Dict[str, Any]] = {}
            for r in extra_records:
                st_id = str(r.get("codi_estacio") or "").strip()
                var_id = str(r.get("codi_variable") or "").strip()
                if not st_id or not var_id:
                    continue
                if st_id not in extras_by_st:
                    extras_by_st[st_id] = {}
                if var_id not in extras_by_st[st_id]:
                    extras_by_st[st_id][var_id] = r.get("valor_lectura")

            # Agrupar precipitaciones por estación
            prec_by_st: Dict[str, List[Dict[str, Any]]] = {}
            for r in prec_records:
                st_id = str(r.get("codi_estacio") or "").strip()
                if st_id:
                    prec_by_st.setdefault(st_id, []).append(r)

            pluvios = []
            features = []
            today_str_madrid = datetime.now(MADRID_TZ).strftime("%Y-%m-%d")

            for st_id, meta in metadata_by_id.items():
                try:
                    lat = float(meta.get("latitud", 0))
                    lon = float(meta.get("longitud", 0))
                except (ValueError, TypeError):
                    lat, lon = None, None

                if lat is None or lon is None or (lat == 0 and lon == 0):
                    continue

                st_precs = prec_by_st.get(st_id, [])
                if not st_precs:
                    # Descartar estaciones sin sensor pluviométrico o sin lecturas activas en 24h
                    continue

                st_name = (meta.get("nom_estacio") or st_id).strip()
                municipi = (meta.get("nom_municipi") or "").strip()
                comarca = (meta.get("nom_comarca") or "").strip()
                provincia = (meta.get("nom_provincia") or "").strip()

                altitud = None
                if meta.get("altitud"):
                    try:
                        altitud = float(meta["altitud"])
                    except (ValueError, TypeError):
                        altitud = None

                prec_1h = 0.0
                prec_4h = 0.0
                prec_12h = 0.0
                prec_24h = 0.0
                prec_hoy = 0.0
                ultima_hora_raw = ""

                st_precs.sort(key=lambda x: x["data_lectura"])
                last_dt_str = st_precs[-1]["data_lectura"]
                ultima_hora_raw = last_dt_str

                try:
                    last_dt = datetime.fromisoformat(last_dt_str.replace("Z", ""))
                    if last_dt.tzinfo is None:
                        last_dt = last_dt.replace(tzinfo=timezone.utc)

                    for p in st_precs:
                        p_dt_str = p.get("data_lectura", "")
                        p_dt = datetime.fromisoformat(p_dt_str.replace("Z", ""))
                        if p_dt.tzinfo is None:
                            p_dt = p_dt.replace(tzinfo=timezone.utc)

                        try:
                            val = float(p.get("valor_lectura", 0.0))
                        except (ValueError, TypeError):
                            val = 0.0

                        diff_sec = (last_dt - p_dt).total_seconds()
                        if 0 <= diff_sec <= 3600:
                            prec_1h += val
                        if 0 <= diff_sec <= 4 * 3600:
                            prec_4h += val
                        if 0 <= diff_sec <= 12 * 3600:
                            prec_12h += val
                        if 0 <= diff_sec <= 24 * 3600:
                            prec_24h += val

                        # Lluvia de hoy según fecha local Madrid
                        p_dt_madrid = p_dt.astimezone(MADRID_TZ)
                        if p_dt_madrid.strftime("%Y-%m-%d") == today_str_madrid:
                            prec_hoy += val
                except Exception as err:
                    logger.warning(f"Error procesando acumulados para estación Meteocat {st_id}: {err}")

                formatted_last_hora = ""
                if ultima_hora_raw:
                    try:
                        dt = datetime.fromisoformat(ultima_hora_raw.replace("Z", ""))
                        if dt.tzinfo is None:
                            dt = dt.replace(tzinfo=timezone.utc)
                        formatted_last_hora = dt.astimezone(MADRID_TZ).strftime("%Y-%m-%d %H:%M:%S")
                    except Exception:
                        formatted_last_hora = ultima_hora_raw

                st_extras = extras_by_st.get(st_id, {})
                temp = float(st_extras["32"]) if "32" in st_extras and st_extras["32"] is not None else None
                hrel = float(st_extras["33"]) if "33" in st_extras and st_extras["33"] is not None else None
                wind_ms = float(st_extras["30"]) if "30" in st_extras and st_extras["30"] is not None else None
                wind_kmh = round(wind_ms * 3.6, 1) if wind_ms is not None else None
                wind_dir = float(st_extras["31"]) if "31" in st_extras and st_extras["31"] is not None else None
                racha_ms = float(st_extras["50"]) if "50" in st_extras and st_extras["50"] is not None else None
                racha_kmh = round(racha_ms * 3.6, 1) if racha_ms is not None else None
                presion = float(st_extras["34"]) if "34" in st_extras and st_extras["34"] is not None else None

                pluvio_obj = {
                    "id_estacion": st_id,
                    "codigo": st_id,
                    "nombre": st_name,
                    "tipo": "Pluviómetro",
                    "red": "METEOCAT",
                    "lat": round(lat, 6),
                    "lon": round(lon, 6),
                    "poblacion": municipi or st_name,
                    "municipio": municipi,
                    "comarca": comarca,
                    "provincia": provincia,
                    "altitud": altitud,
                    "estado": meta.get("nom_estat_ema", "Operativa") == "Operativa",
                    "lluvia_1h": round(prec_1h, 1),
                    "precipitacion_1h": round(prec_1h, 1),
                    "fecha_1h": formatted_last_hora,
                    "lluvia_4h": round(prec_4h, 1),
                    "precipitacion_4h": round(prec_4h, 1),
                    "fecha_4h": formatted_last_hora,
                    "lluvia_12h": round(prec_12h, 1),
                    "precipitacion_12h": round(prec_12h, 1),
                    "fecha_12h": formatted_last_hora,
                    "lluvia_24h": round(prec_24h, 1),
                    "precipitacion_24h": round(prec_24h, 1),
                    "fecha_24h": formatted_last_hora,
                    "lluvia_hoy": round(prec_hoy, 1),
                    "temperatura": temp,
                    "humedad": hrel,
                    "viento_vel": wind_kmh,
                    "racha_max": racha_kmh,
                    "direccion_viento": wind_dir,
                    "presion": presion,
                    "ultima_hora": formatted_last_hora,
                    "fuente": "Generalitat de Catalunya - SMC (Dades Obertes)",
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
            with open(STATIC_METEOCAT_PLUVIOS_FILE, "w", encoding="utf-8") as f:
                json.dump(pluvios, f, ensure_ascii=False, indent=2)

            with open(METEOCAT_PLUVIOS_GEOJSON_FILE, "w", encoding="utf-8") as f:
                json.dump(geojson, f, ensure_ascii=False, indent=2)

            if PUBLIC_DATA_DIR.exists():
                with open(PUBLIC_DATA_DIR / "meteocat_lluvias.geojson", "w", encoding="utf-8") as f:
                    json.dump(geojson, f, ensure_ascii=False, indent=2)

            self._pluvios = pluvios
            self._pluvios_by_id = {str(p["id_estacion"]): p for p in pluvios}
            self._last_sync_time = datetime.now()
            logger.info(f"Sincronizados {len(pluvios)} pluviómetros de Meteocat correctamente.")
            return pluvios

        except Exception as e:
            logger.error(f"Error al sincronizar datos de Meteocat: {e}")
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

        if METEOCAT_PLUVIOS_GEOJSON_FILE.exists():
            try:
                with open(METEOCAT_PLUVIOS_GEOJSON_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Error leyendo {METEOCAT_PLUVIOS_GEOJSON_FILE}: {e}")

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


meteocat_pluvios_service = MeteocatPluviosService()
