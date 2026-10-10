"""
Servicio para la integración de datos pluviométricos y meteorológicos de la red de AEMET OpenData.
Consulta las observaciones convencionales de las últimas 24h (EMA), calcula la precipitación
acumulada en ventanas de 1h, 4h, 12h y 24h, y genera GeoJSON unificado con el mismo formato del SAIH.
"""
import asyncio
import json
import logging
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from app.config import settings, DATA_DIR

logger = logging.getLogger("rainloc-backend.aemet_pluvios_service")

MADRID_TZ = ZoneInfo("Europe/Madrid")
UTC_TZ = timezone.utc

STATIC_AEMET_PLUVIOS_FILE = DATA_DIR / "aemet_lluvias_estaciones.json"
AEMET_PLUVIOS_GEOJSON_FILE = DATA_DIR / "aemet_lluvias.geojson"
PUBLIC_DATA_DIR = Path(__file__).parent.parent.parent.parent / "data"

# TTL de frescura de datos: 5 minutos (300 segundos)
SYNC_TTL_SECONDS = 300


def _parse_iso_date(dt_str: str) -> Optional[datetime]:
    """Parsea fecha ISO de AEMET con soporte para +0000, Z, etc."""
    if not dt_str:
        return None
    dt_clean = dt_str.strip()
    try:
        if dt_clean.endswith("+0000") or dt_clean.endswith("-0000"):
            dt_clean = dt_clean[:-5] + "+00:00"
        elif dt_clean.endswith("Z"):
            dt_clean = dt_clean[:-1] + "+00:00"
        return datetime.fromisoformat(dt_clean)
    except Exception:
        try:
            # Fallback a parseo simple sin zona horaria
            return datetime.strptime(dt_str[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
        except Exception:
            return None


class AemetPluviosService:
    def __init__(self):
        self._pluvios: List[Dict[str, Any]] = []
        self._pluvios_by_id: Dict[str, Dict[str, Any]] = {}
        self._last_sync_time: Optional[datetime] = None
        self._sync_lock = asyncio.Lock()

        self._load_from_disk()

    def _load_from_disk(self):
        """Carga las estaciones de lluvia de AEMET desde el fichero local si existe."""
        if STATIC_AEMET_PLUVIOS_FILE.exists():
            try:
                with open(STATIC_AEMET_PLUVIOS_FILE, "r", encoding="utf-8") as f:
                    self._pluvios = json.load(f)
                    self._pluvios_by_id = {str(p["id_estacion"]): p for p in self._pluvios if "id_estacion" in p}
                    mtime = datetime.fromtimestamp(STATIC_AEMET_PLUVIOS_FILE.stat().st_mtime)
                    self._last_sync_time = mtime
                    logger.info(f"Cargados {len(self._pluvios)} pluviómetros de AEMET desde caché local.")
            except Exception as e:
                logger.error(f"Error al leer {STATIC_AEMET_PLUVIOS_FILE}: {e}")

    def _is_fresh(self) -> bool:
        if not self._last_sync_time or len(self._pluvios) == 0:
            return False
        return (datetime.now() - self._last_sync_time).total_seconds() < SYNC_TTL_SECONDS

    def sync_pluvios_metadata(self) -> List[Dict[str, Any]]:
        """
        Descarga síncrona de las observaciones convencionales de AEMET OpenData (últimas 24h),
        calcula los acumulados en 1h, 4h, 12h y 24h, y guarda los ficheros GeoJSON y JSON locales.
        """
        api_key = (settings.AEMET_API_KEY or "").strip()
        if not api_key or len(api_key) < 10:
            logger.warning("AEMET_API_KEY no configurada o inválida. Usando datos locales de AEMET.")
            return self._pluvios

        logger.info("Sincronizando pluviómetros en tiempo real desde AEMET OpenData...")
        url = f"{settings.AEMET_OPENDATA_BASE_URL}/observacion/convencional/todas?api_key={api_key}"
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": settings.AEMET_USER_AGENT,
                "Accept": "application/json",
            }
        )

        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                if resp.status != 200:
                    logger.warning(f"AEMET OpenData HTTP {resp.status} al consultar observaciones.")
                    return self._pluvios
                body = json.loads(resp.read().decode("utf-8", "ignore"))

            datos_url = body.get("datos")
            if not datos_url:
                logger.warning(f"AEMET OpenData no devolvió URL de datos: {body.get('descripcion')}")
                return self._pluvios

            # Descargar el JSON con las observaciones de las ~850 estaciones
            d_req = urllib.request.Request(datos_url, headers={"User-Agent": settings.AEMET_USER_AGENT})
            with urllib.request.urlopen(d_req, timeout=25) as d_resp:
                raw_records = json.loads(d_resp.read().decode("utf-8", "ignore"))

            if not isinstance(raw_records, list) or len(raw_records) == 0:
                logger.warning("AEMET OpenData devolvió lista de observaciones vacía.")
                return self._pluvios

            # Agrupar registros por estación (idema)
            stations_records: Dict[str, List[Dict[str, Any]]] = {}
            for r in raw_records:
                idema = str(r.get("idema") or "").strip()
                if not idema:
                    continue
                if idema not in stations_records:
                    stations_records[idema] = []
                stations_records[idema].append(r)

            pluvios = []
            features = []

            for idema, records in stations_records.items():
                try:
                    # Parsear fechas y ordenar cronológicamente
                    parsed_records = []
                    for r in records:
                        dt = _parse_iso_date(r.get("fint", ""))
                        if dt:
                            parsed_records.append((dt, r))

                    if not parsed_records:
                        continue

                    parsed_records.sort(key=lambda x: x[0])
                    latest_dt, latest_r = parsed_records[-1]

                    lat = latest_r.get("lat")
                    lon = latest_r.get("lon")
                    if lat is None or lon is None:
                        continue

                    lat = float(lat)
                    lon = float(lon)
                    alt = float(latest_r.get("alt", 0.0)) if latest_r.get("alt") is not None else None

                    ubi = (latest_r.get("ubi") or "").strip()
                    # Limpiar nombre si viene en mayúsculas sostenidas
                    nombre = ubi.title() if ubi.isupper() else ubi

                    # Calcular precipitación acumulada en ventanas de tiempo (1h, 4h, 12h, 24h)
                    def _calc_sum_since(hours: float) -> float:
                        cutoff = latest_dt - timedelta(hours=hours)
                        prec_sum = 0.0
                        for dt, rec in parsed_records:
                            if dt >= cutoff:
                                p_val = rec.get("prec")
                                if p_val is not None:
                                    try:
                                        val = float(p_val)
                                        if val > 0:
                                            prec_sum += val
                                    except (ValueError, TypeError):
                                        pass
                        return round(prec_sum, 2)

                    lluvia_1h = _calc_sum_since(1.0)
                    lluvia_4h = _calc_sum_since(4.0)
                    lluvia_12h = _calc_sum_since(12.0)
                    lluvia_24h = _calc_sum_since(24.0)

                    # Formatear fecha en hora local oficial (Madrid)
                    local_dt = latest_dt.astimezone(MADRID_TZ)
                    ultima_hora_str = local_dt.strftime("%Y-%m-%d %H:%M:%S")

                    pluvio_obj = {
                        "id_estacion": idema,
                        "codigo": idema,
                        "nombre": nombre,
                        "tipo": "Pluviómetro",
                        "red": "AEMET",
                        "lat": round(lat, 6),
                        "lon": round(lon, 6),
                        "altitud": alt,
                        "poblacion": nombre,
                        "provincia": latest_r.get("provincia") or "",
                        "estado": True,
                        "lluvia_1h": lluvia_1h,
                        "precipitacion_1h": lluvia_1h,
                        "fecha_1h": ultima_hora_str,
                        "lluvia_4h": lluvia_4h,
                        "precipitacion_4h": lluvia_4h,
                        "fecha_4h": ultima_hora_str,
                        "lluvia_12h": lluvia_12h,
                        "precipitacion_12h": lluvia_12h,
                        "fecha_12h": ultima_hora_str,
                        "lluvia_24h": lluvia_24h,
                        "precipitacion_24h": lluvia_24h,
                        "fecha_24h": ultima_hora_str,
                        "temperatura": latest_r.get("ta"),
                        "humedad": latest_r.get("hr"),
                        "viento_vel": latest_r.get("vv"),
                        "racha_max": latest_r.get("vmax"),
                        "direccion_viento": latest_r.get("dv"),
                        "presion": latest_r.get("pres") or latest_r.get("pres_nmar"),
                        "ultima_hora": ultima_hora_str,
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

                except Exception as err:
                    logger.warning(f"Error procesando estación AEMET {idema}: {err}")
                    continue

            geojson = {
                "type": "FeatureCollection",
                "features": features,
            }

            DATA_DIR.mkdir(parents=True, exist_ok=True)
            with open(STATIC_AEMET_PLUVIOS_FILE, "w", encoding="utf-8") as f:
                json.dump(pluvios, f, ensure_ascii=False, indent=2)

            with open(AEMET_PLUVIOS_GEOJSON_FILE, "w", encoding="utf-8") as f:
                json.dump(geojson, f, ensure_ascii=False, indent=2)

            # Guardar también en public data si existe
            if PUBLIC_DATA_DIR.exists():
                with open(PUBLIC_DATA_DIR / "aemet_lluvias.geojson", "w", encoding="utf-8") as f:
                    json.dump(geojson, f, ensure_ascii=False, indent=2)

            self._pluvios = pluvios
            self._pluvios_by_id = {str(p["id_estacion"]): p for p in pluvios}
            self._last_sync_time = datetime.now()
            logger.info(f"Sincronizados {len(pluvios)} pluviómetros de AEMET correctamente.")
            return pluvios

        except Exception as e:
            logger.error(f"Error al sincronizar observaciones de AEMET: {e}")
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
            logger.warning(f"Error en sync background AEMET pluvios: {e}")
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

        if AEMET_PLUVIOS_GEOJSON_FILE.exists():
            try:
                with open(AEMET_PLUVIOS_GEOJSON_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Error leyendo {AEMET_PLUVIOS_GEOJSON_FILE}: {e}")

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
        return self._pluvios_by_id.get(key) or self._pluvios_by_id.get(str(id_or_code))


aemet_pluvios_service = AemetPluviosService()
