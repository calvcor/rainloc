"""
Servicio para la integración de datos hidrológicos del SAIH Júcar (CHJ).
Gestiona el catálogo de caudales y embalses, la caché con TTL bajo demanda y consultas temporales.
"""
import asyncio
import json
import logging
import re
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from pyproj import Transformer

logger = logging.getLogger("rainloc-backend.saih_service")

DATA_DIR = Path(__file__).parent.parent.parent / "data"
STATIC_STATIONS_FILE = DATA_DIR / "saih_aforos_estaciones.json"
GEOJSON_FILE = DATA_DIR / "saih_aforos.geojson"

STATIC_EMBALSES_FILE = DATA_DIR / "saih_embalses_estaciones.json"
EMBALSES_GEOJSON_FILE = DATA_DIR / "saih_embalses.geojson"

STATIC_PLUVIOS_FILE = DATA_DIR / "saih_lluvias_estaciones.json"
PLUVIOS_GEOJSON_FILE = DATA_DIR / "saih_lluvias.geojson"

SAIH_BASE_URL = "https://saih.chj.es"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

# TTL de frescura de datos: 5 minutos (300 segundos)
SYNC_TTL_SECONDS = 300

# Transformador de coordenadas EPSG:25830 (UTM 30N) a EPSG:4326 (WGS84 lon, lat)
transformer = Transformer.from_crs("EPSG:25830", "EPSG:4326", always_xy=True)


class SAIHService:
    def __init__(self):
        self._stations: List[Dict[str, Any]] = []
        self._stations_by_id: Dict[str, Dict[str, Any]] = {}
        self._last_sync_time: Optional[datetime] = None
        self._sync_lock = asyncio.Lock()

        self._embalses: List[Dict[str, Any]] = []
        self._embalses_by_id: Dict[str, Dict[str, Any]] = {}
        self._last_embalses_sync_time: Optional[datetime] = None
        self._embalses_sync_lock = asyncio.Lock()

        self._pluvios: List[Dict[str, Any]] = []
        self._pluvios_by_id: Dict[str, Dict[str, Any]] = {}
        self._last_pluvios_sync_time: Optional[datetime] = None
        self._pluvios_sync_lock = asyncio.Lock()

        self._load_stations_from_disk()
        self._load_embalses_from_disk()
        self._load_pluvios_from_disk()

    # ==========================================
    # CAUDALES (AFOROS)
    # ==========================================

    def _load_stations_from_disk(self):
        """Carga las estaciones de caudal desde el fichero local si existe."""
        if STATIC_STATIONS_FILE.exists():
            try:
                with open(STATIC_STATIONS_FILE, "r", encoding="utf-8") as f:
                    self._stations = json.load(f)
                    self._stations_by_id = {st["id_variable"]: st for st in self._stations}
                    mtime = datetime.fromtimestamp(STATIC_STATIONS_FILE.stat().st_mtime)
                    self._last_sync_time = mtime
                    logger.info(f"Cargadas {len(self._stations)} estaciones de caudal desde fichero local.")
                    return
            except Exception as e:
                logger.error(f"Error al leer {STATIC_STATIONS_FILE}: {e}")

    def sync_static_metadata(self) -> List[Dict[str, Any]]:
        """
        Descarga síncrona de la página de caudales del SAIH, extrae las 79 estaciones,
        reproyecta las coordenadas a WGS84 y guarda los ficheros locales.
        """
        logger.info("Sincronizando caudales en tiempo real desde saih.chj.es...")
        url = f"{SAIH_BASE_URL}/mapa-aforos"
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})

        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                html = resp.read().decode("utf-8")

            match = re.search(r"let aforos\s*=\s*(\[.*?\]);", html, re.DOTALL)
            if not match:
                logger.error("No se encontró la variable 'let aforos' en el HTML del SAIH.")
                return self._stations

            raw_data = json.loads(match.group(1))
            stations = []
            features = []

            for item in raw_data:
                try:
                    x = float(item["fldNCoordGPSLat"])
                    y = float(item["fldNCoordGPSLon"])
                    lon, lat = transformer.transform(x, y)

                    caudal_val = item.get("lastValue")
                    if caudal_val is not None:
                        try:
                            caudal_val = round(float(caudal_val), 3)
                        except Exception:
                            caudal_val = None

                    st_data = {
                        "id_variable": str(item["idVariable"]),
                        "codigo": item.get("fldTCodigo", ""),
                        "nombre": item.get("fldTNombre", ""),
                        "variable": item.get("fldTNombreVariable", ""),
                        "tipo": item.get("fldTTipo", "Af"),
                        "id_estacion": str(item.get("idEstacionRemota", "")),
                        "lat": round(lat, 6),
                        "lon": round(lon, 6),
                        "poblacion": item.get("fldTPoblacion", ""),
                        "provincia": item.get("fldTProvincia", ""),
                        "subcuenca": item.get("fldTSubCuenca", ""),
                        "ultimo_caudal": caudal_val,
                        "caudal": caudal_val,
                        "ultima_hora": item.get("lastValueFecha", ""),
                        "umbrales": {
                            "amarillo": item.get("fldFUmbralBajo"),
                            "naranja": item.get("fldFUmbralMedio"),
                            "rojo": item.get("fldFUmbralAlto"),
                        },
                        "tiene_ficha": item.get("tieneFicha", False),
                        "unidad": "m³/s",
                    }
                    stations.append(st_data)
                    features.append({
                        "type": "Feature",
                        "geometry": {
                            "type": "Point",
                            "coordinates": [round(lon, 6), round(lat, 6)],
                        },
                        "properties": st_data,
                    })
                except Exception as err:
                    logger.warning(f"Error procesando estación {item.get('fldTNombre')}: {err}")

            DATA_DIR.mkdir(parents=True, exist_ok=True)
            with open(STATIC_STATIONS_FILE, "w", encoding="utf-8") as f:
                json.dump(stations, f, ensure_ascii=False, indent=2)

            geojson_data = {"type": "FeatureCollection", "features": features}
            with open(GEOJSON_FILE, "w", encoding="utf-8") as f:
                json.dump(geojson_data, f, ensure_ascii=False, indent=2)

            # Sincronizar también en la raíz estática del frontend si existe
            frontend_geojson = Path(__file__).parent.parent.parent.parent / "data" / "saih_aforos.geojson"
            if frontend_geojson.parent.exists():
                with open(frontend_geojson, "w", encoding="utf-8") as f:
                    json.dump(geojson_data, f, ensure_ascii=False, indent=2)

            self._stations = stations
            self._stations_by_id = {st["id_variable"]: st for st in self._stations}
            self._last_sync_time = datetime.now()
            logger.info(f"Sincronización SAIH caudales completada: {len(stations)} estaciones actualizadas.")
            return self._stations

        except Exception as e:
            logger.error(f"Error al sincronizar caudales SAIH: {e}")
            return self._stations

    async def ensure_fresh_data(self, force: bool = False):
        """Garantiza que los datos de caudales tengan menos de 5 minutos de antigüedad."""
        now = datetime.now()
        is_stale = (
            self._last_sync_time is None
            or (now - self._last_sync_time).total_seconds() >= SYNC_TTL_SECONDS
            or len(self._stations) == 0
        )

        if is_stale or force:
            async with self._sync_lock:
                now_check = datetime.now()
                if (
                    self._last_sync_time is None
                    or (now_check - self._last_sync_time).total_seconds() >= SYNC_TTL_SECONDS
                    or len(self._stations) == 0
                    or force
                ):
                    loop = asyncio.get_running_loop()
                    await loop.run_in_executor(None, self.sync_static_metadata)

    async def get_stations(self, auto_sync: bool = True) -> List[Dict[str, Any]]:
        if auto_sync:
            await self.ensure_fresh_data()
        return self._stations

    async def get_stations_geojson(self, auto_sync: bool = True) -> Dict[str, Any]:
        if auto_sync:
            await self.ensure_fresh_data()

        if GEOJSON_FILE.exists():
            try:
                with open(GEOJSON_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Error leyendo {GEOJSON_FILE}: {e}")

        features = [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [st["lon"], st["lat"]]},
                "properties": st,
            }
            for st in self._stations
        ]
        return {"type": "FeatureCollection", "features": features}

    def get_station_by_id(self, id_variable: str) -> Optional[Dict[str, Any]]:
        return self._stations_by_id.get(str(id_variable))

    # ==========================================
    # EMBALSES
    # ==========================================

    def _load_embalses_from_disk(self):
        """Carga los embalses desde el fichero local si existe."""
        if STATIC_EMBALSES_FILE.exists():
            try:
                with open(STATIC_EMBALSES_FILE, "r", encoding="utf-8") as f:
                    self._embalses = json.load(f)
                    self._index_embalses()
                    mtime = datetime.fromtimestamp(STATIC_EMBALSES_FILE.stat().st_mtime)
                    self._last_embalses_sync_time = mtime
                    logger.info(f"Cargados {len(self._embalses)} embalses desde fichero local.")
                    return
            except Exception as e:
                logger.error(f"Error al leer {STATIC_EMBALSES_FILE}: {e}")

    def _index_embalses(self):
        """Indexa embalses por id_estacion, codigo, id_volumen e id_cota para búsqueda rápida."""
        idx = {}
        for emb in self._embalses:
            if emb.get("id_estacion"):
                idx[str(emb["id_estacion"])] = emb
            if emb.get("codigo"):
                idx[str(emb["codigo"]).upper()] = emb
            if emb.get("id_volumen"):
                idx[str(emb["id_volumen"])] = emb
            if emb.get("id_cota"):
                idx[str(emb["id_cota"])] = emb
        self._embalses_by_id = idx

    def sync_embalses_metadata(self) -> List[Dict[str, Any]]:
        """
        Descarga síncrona de la página de embalses del SAIH, extrae los 25 embalses,
        reproyecta las coordenadas a WGS84 y guarda los ficheros locales.
        """
        logger.info("Sincronizando embalses en tiempo real desde saih.chj.es...")
        url = f"{SAIH_BASE_URL}/mapa-embalses"
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})

        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                html = resp.read().decode("utf-8")

            match = re.search(r"let embalses\s*=\s*(\[.*?\]);", html, re.DOTALL)
            if not match:
                logger.error("No se encontró la variable 'let embalses' en el HTML del SAIH.")
                return self._embalses

            raw_data = json.loads(match.group(1))
            embalses = []
            features = []

            for item in raw_data:
                try:
                    x = float(item["fldNCoordGPSLat"])
                    y = float(item["fldNCoordGPSLon"])
                    lon, lat = transformer.transform(x, y)

                    vol_val = item.get("valorVolumenEmbalse")
                    vol_cap = item.get("fldFVolumenNMN")
                    vol_float = round(float(vol_val), 3) if vol_val is not None else None
                    cap_float = round(float(vol_cap), 2) if vol_cap is not None else None
                    pct_val = round((vol_float / cap_float) * 100, 1) if (vol_float is not None and cap_float and cap_float > 0) else None

                    cota_val = item.get("valorCotaEmbalse")
                    cota_vertido = item.get("fldFCotaVertido")
                    cota_float = round(float(cota_val), 2) if cota_val is not None else None
                    cota_vert_float = round(float(cota_vertido), 2) if cota_vertido is not None else None

                    caudal_in = item.get("valorCaudalRecibido")
                    caudal_out = item.get("valorCaudalSalida")
                    caudal_rio = item.get("valorCaudalSalidaRio")

                    emb_data = {
                        "id_estacion": str(item.get("idEstacionRemota", "")),
                        "codigo": item.get("fldTCodigo", ""),
                        "nombre": item.get("fldTNombre", ""),
                        "tipo": "Embalse",
                        "lat": round(lat, 6),
                        "lon": round(lon, 6),
                        "poblacion": item.get("fldTPoblacion", ""),
                        "provincia": item.get("fldTProvincia", ""),
                        "subcuenca": item.get("fldTSubCuenca", ""),
                        "id_volumen": str(item.get("idVolumenEmbalse", "")),
                        "id_cota": str(item.get("idCotaEmbalse", "")),
                        "id_caudal_in": str(item.get("idCaudalRecibido", "")),
                        "id_caudal_out": str(item.get("idCaudalSalida", "")),
                        "id_caudal_rio": str(item.get("idCaudalSalidaRio", "")),
                        "volumen_actual": vol_float,
                        "capacidad_nmn": cap_float,
                        "porcentaje_llenado": pct_val,
                        "cota_actual": cota_float,
                        "cota_vertido": cota_vert_float,
                        "caudal_recibido": round(float(caudal_in), 2) if caudal_in is not None else None,
                        "caudal_salida": round(float(caudal_out), 2) if caudal_out is not None else None,
                        "caudal_salida_rio": round(float(caudal_rio), 2) if caudal_rio is not None else None,
                        "umbrales_salida_rio": {
                            "amarillo": item.get("umbralBajoCaudalSalidaRio"),
                            "naranja": item.get("umbralMedioCaudalSalidaRio"),
                            "rojo": item.get("umbralAltoCaudalSalidaRio"),
                        },
                        "ultima_hora": item.get("fechaComunicacionVolumenEmbalse") or item.get("fechaComunicacionVol") or "",
                        "unidad_volumen": "hm³",
                        "unidad_cota": "m.s.n.m.",
                        "unidad_caudal": "m³/s",
                    }
                    embalses.append(emb_data)
                    features.append({
                        "type": "Feature",
                        "geometry": {
                            "type": "Point",
                            "coordinates": [round(lon, 6), round(lat, 6)],
                        },
                        "properties": emb_data,
                    })
                except Exception as err:
                    logger.warning(f"Error procesando embalse {item.get('fldTNombre')}: {err}")

            DATA_DIR.mkdir(parents=True, exist_ok=True)
            with open(STATIC_EMBALSES_FILE, "w", encoding="utf-8") as f:
                json.dump(embalses, f, ensure_ascii=False, indent=2)

            geojson_data = {"type": "FeatureCollection", "features": features}
            with open(EMBALSES_GEOJSON_FILE, "w", encoding="utf-8") as f:
                json.dump(geojson_data, f, ensure_ascii=False, indent=2)

            frontend_geojson = Path(__file__).parent.parent.parent.parent / "data" / "saih_embalses.geojson"
            if frontend_geojson.parent.exists():
                with open(frontend_geojson, "w", encoding="utf-8") as f:
                    json.dump(geojson_data, f, ensure_ascii=False, indent=2)

            self._embalses = embalses
            self._index_embalses()
            self._last_embalses_sync_time = datetime.now()
            logger.info(f"Sincronización SAIH embalses completada: {len(embalses)} embalses actualizados.")
            return self._embalses

        except Exception as e:
            logger.error(f"Error al sincronizar embalses SAIH: {e}")
            return self._embalses

    async def ensure_fresh_embalses_data(self, force: bool = False):
        """Garantiza que los datos de embalses tengan menos de 5 minutos de antigüedad."""
        now = datetime.now()
        is_stale = (
            self._last_embalses_sync_time is None
            or (now - self._last_embalses_sync_time).total_seconds() >= SYNC_TTL_SECONDS
            or len(self._embalses) == 0
        )

        if is_stale or force:
            async with self._embalses_sync_lock:
                now_check = datetime.now()
                if (
                    self._last_embalses_sync_time is None
                    or (now_check - self._last_embalses_sync_time).total_seconds() >= SYNC_TTL_SECONDS
                    or len(self._embalses) == 0
                    or force
                ):
                    loop = asyncio.get_running_loop()
                    await loop.run_in_executor(None, self.sync_embalses_metadata)

    async def get_embalses(self, auto_sync: bool = True) -> List[Dict[str, Any]]:
        if auto_sync:
            await self.ensure_fresh_embalses_data()
        return self._embalses

    async def get_embalses_geojson(self, auto_sync: bool = True) -> Dict[str, Any]:
        if auto_sync:
            await self.ensure_fresh_embalses_data()

        if EMBALSES_GEOJSON_FILE.exists():
            try:
                with open(EMBALSES_GEOJSON_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Error leyendo {EMBALSES_GEOJSON_FILE}: {e}")

        features = [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [emb["lon"], emb["lat"]]},
                "properties": emb,
            }
            for emb in self._embalses
        ]
        return {"type": "FeatureCollection", "features": features}

    def get_embalse_by_id(self, id_or_code: str) -> Optional[Dict[str, Any]]:
        key = str(id_or_code).strip().upper()
        return self._embalses_by_id.get(key) or self._embalses_by_id.get(str(id_or_code))

    # ==========================================
    # LLUVIAS (PLUVIÓMETROS)
    # ==========================================

    def _load_pluvios_from_disk(self):
        """Carga las estaciones de lluvia desde el fichero local si existe."""
        if STATIC_PLUVIOS_FILE.exists():
            try:
                with open(STATIC_PLUVIOS_FILE, "r", encoding="utf-8") as f:
                    self._pluvios = json.load(f)
                    self._pluvios_by_id = {}
                    for p in self._pluvios:
                        if "id_estacion" in p:
                            self._pluvios_by_id[str(p["id_estacion"])] = p
                        if "codigo" in p:
                            self._pluvios_by_id[str(p["codigo"]).upper()] = p
                    mtime = datetime.fromtimestamp(STATIC_PLUVIOS_FILE.stat().st_mtime)
                    self._last_pluvios_sync_time = mtime
                    logger.info(f"Cargados {len(self._pluvios)} pluviómetros desde fichero local.")
                    return
            except Exception as e:
                logger.error(f"Error al leer {STATIC_PLUVIOS_FILE}: {e}")

    def _is_pluvios_fresh(self) -> bool:
        if not self._last_pluvios_sync_time:
            return False
        return (datetime.now() - self._last_pluvios_sync_time).total_seconds() < SYNC_TTL_SECONDS

    def sync_pluvios_metadata(self) -> List[Dict[str, Any]]:
        """
        Descarga síncrona de la página de lluvias del SAIH (https://saih.chj.es/mapa-lluvias),
        extrae los pluviómetros, reproyecta coordenadas EPSG:25830 a EPSG:4326 y guarda ficheros.
        """
        logger.info("Sincronizando pluviómetros en tiempo real desde saih.chj.es/mapa-lluvias...")
        url = f"{SAIH_BASE_URL}/mapa-lluvias"
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})

        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                html = resp.read().decode("utf-8")

            match = re.search(r"let estaciones\s*=\s*(\[.*?\]);", html, re.DOTALL)
            if not match:
                logger.error("No se encontró la variable 'let estaciones' en saih.chj.es/mapa-lluvias.")
                return self._pluvios

            raw_data = json.loads(match.group(1))
            pluvios = []
            features = []

            for item in raw_data:
                try:
                    x = float(item["fldNCoordGPSLat"])
                    y = float(item["fldNCoordGPSLon"])
                    lon, lat = transformer.transform(x, y)

                    def _safe_float(v):
                        if v is None:
                            return 0.0
                        try:
                            return round(float(v), 2)
                        except (ValueError, TypeError):
                            return 0.0

                    lluvia_1h = _safe_float(item.get("lluvia_1h"))
                    lluvia_4h = _safe_float(item.get("lluvia_4h"))
                    lluvia_12h = _safe_float(item.get("lluvia_12h"))
                    lluvia_24h = _safe_float(item.get("lluvia_24h"))

                    codigo = (item.get("fldTCodigo") or "").strip()
                    nombre = (item.get("fldTNombre") or "").strip()
                    poblacion = (item.get("fldTPoblacion") or "").strip()
                    provincia = (item.get("fldTProvincia") or "").strip()
                    id_estacion = str(item.get("idEstacionRemota") or "")

                    pluvio_obj = {
                        "id_estacion": id_estacion,
                        "codigo": codigo,
                        "nombre": nombre,
                        "tipo": "Pluviómetro",
                        "lat": round(lat, 6),
                        "lon": round(lon, 6),
                        "poblacion": poblacion,
                        "provincia": provincia,
                        "estado": bool(item.get("fldTEstado", True)),
                        "lluvia_1h": lluvia_1h,
                        "fecha_1h": item.get("fecha_1h"),
                        "lluvia_4h": lluvia_4h,
                        "fecha_4h": item.get("fecha_4h"),
                        "lluvia_12h": lluvia_12h,
                        "fecha_12h": item.get("fecha_12h"),
                        "lluvia_24h": lluvia_24h,
                        "fecha_24h": item.get("fecha_24h"),
                        "unidad": "mm",
                    }
                    pluvios.append(pluvio_obj)

                    feature = {
                        "type": "Feature",
                        "geometry": {
                            "type": "Point",
                            "coordinates": [round(lon, 6), round(lat, 6)],
                        },
                        "properties": pluvio_obj,
                    }
                    features.append(feature)
                except Exception as e:
                    logger.warning(f"Error parseando pluviómetro {item.get('fldTCodigo')}: {e}")
                    continue

            geojson = {
                "type": "FeatureCollection",
                "features": features,
            }

            DATA_DIR.mkdir(parents=True, exist_ok=True)
            with open(STATIC_PLUVIOS_FILE, "w", encoding="utf-8") as f:
                json.dump(pluvios, f, ensure_ascii=False, indent=2)

            with open(PLUVIOS_GEOJSON_FILE, "w", encoding="utf-8") as f:
                json.dump(geojson, f, ensure_ascii=False, indent=2)

            # Guardar también en ./data/saih_lluvias.geojson para fallback directo
            public_data_dir = DATA_DIR.parent.parent / "data"
            public_data_dir.mkdir(parents=True, exist_ok=True)
            with open(public_data_dir / "saih_lluvias.geojson", "w", encoding="utf-8") as f:
                json.dump(geojson, f, ensure_ascii=False, indent=2)

            self._pluvios = pluvios
            self._pluvios_by_id = {}
            for p in pluvios:
                if "id_estacion" in p:
                    self._pluvios_by_id[str(p["id_estacion"])] = p
                if "codigo" in p:
                    self._pluvios_by_id[str(p["codigo"]).upper()] = p

            self._last_pluvios_sync_time = datetime.now()
            logger.info(f"Sincronizados {len(pluvios)} pluviómetros correctamente.")
            return pluvios

        except Exception as e:
            logger.error(f"Error sincronizando pluviómetros del SAIH: {e}")
            return self._pluvios

    async def ensure_fresh_pluvios_data(self):
        if not self._is_pluvios_fresh():
            async with self._pluvios_sync_lock:
                if not self._is_pluvios_fresh():
                    loop = asyncio.get_running_loop()
                    await loop.run_in_executor(None, self.sync_pluvios_metadata)

    async def get_pluvios(self, auto_sync: bool = True) -> List[Dict[str, Any]]:
        if auto_sync:
            await self.ensure_fresh_pluvios_data()
        return self._pluvios

    async def get_pluvios_geojson(self, auto_sync: bool = True) -> Dict[str, Any]:
        if auto_sync:
            await self.ensure_fresh_pluvios_data()

        if PLUVIOS_GEOJSON_FILE.exists():
            try:
                with open(PLUVIOS_GEOJSON_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Error leyendo {PLUVIOS_GEOJSON_FILE}: {e}")

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

    # ==========================================
    # SERIES TEMPORALES
    # ==========================================

    async def get_history(
        self,
        id_variable: str,
        hours: int = 24,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Consulta la API temporal del SAIH para una variable dada.
        Formato fechas API: YYYY-MM-DD HH:mm:ss
        """
        now = datetime.now()
        if not end_date or not isinstance(end_date, str):
            end_dt = now
            end_date_str = end_dt.strftime("%Y-%m-%d %H:%M:%S")
        else:
            end_date_str = str(end_date)

        if not start_date or not isinstance(start_date, str):
            start_dt = now - timedelta(hours=int(hours) if isinstance(hours, (int, float)) else 24)
            start_date_str = start_dt.strftime("%Y-%m-%d %H:%M:%S")
        else:
            start_date_str = str(start_date)

        encoded_start = urllib.parse.quote(start_date_str)
        encoded_end = urllib.parse.quote(end_date_str)
        url = f"{SAIH_BASE_URL}/admin/variables/valor/{id_variable}/{encoded_start}/{encoded_end}"

        def _fetch():
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status != 200:
                    return None
                return json.loads(resp.read().decode("utf-8"))

        loop = asyncio.get_running_loop()
        try:
            raw_series = await loop.run_in_executor(None, _fetch)
        except Exception as e:
            logger.error(f"Error consultando histórico para variable {id_variable}: {e}")
            raw_series = None

        station_info = self.get_station_by_id(id_variable) or self.get_embalse_by_id(id_variable)

        series = []
        if raw_series and isinstance(raw_series, list):
            for entry in raw_series:
                series.append({
                    "fecha": entry.get("fecha"),
                    "valor": entry.get("valor"),
                    "estado": entry.get("estado", 0),
                })

        return {
            "id_variable": str(id_variable),
            "estacion": station_info,
            "rango": {
                "desde": start_date_str,
                "hasta": end_date_str,
                "horas": hours,
            },
            "puntos_totales": len(series),
            "serie": series,
        }


saih_service = SAIHService()
