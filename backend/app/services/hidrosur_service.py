"""
Servicio integral para el S.A.I.H. Hidrosur (Junta de Andalucía / Cuencas Andaluzas).
Gestiona:
1. Pluviómetros en tiempo real (~147 estaciones con acumulados 1h, 4h, 12h, 24h, hoy y ayer).
2. Aforos / Caudales en ríos (~38 estaciones con caudal m³/s, nivel m, umbrales y series históricas).
3. Embalses y presas (~21 embalses con volumen hm³, capacidad, % llenado y series históricas).
4. Series temporales bajo demanda para gráficas de evolución interactiva (12h, 24h, 48h, 7d).
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

logger = logging.getLogger("rainloc-backend.hidrosur_service")

MADRID_TZ = ZoneInfo("Europe/Madrid")
UTC_TZ = timezone.utc

STATIC_HIDROSUR_PLUVIOS_FILE = DATA_DIR / "hidrosur_lluvias_estaciones.json"
HIDROSUR_PLUVIOS_GEOJSON_FILE = DATA_DIR / "hidrosur_lluvias.geojson"
STATIC_HIDROSUR_AFOROS_FILE = DATA_DIR / "hidrosur_aforos_estaciones.json"
HIDROSUR_AFOROS_GEOJSON_FILE = DATA_DIR / "hidrosur_aforos.geojson"
STATIC_HIDROSUR_EMBALSES_FILE = DATA_DIR / "hidrosur_embalses_estaciones.json"
HIDROSUR_EMBALSES_GEOJSON_FILE = DATA_DIR / "hidrosur_embalses.geojson"
HIDROSUR_METADATA_FILE = DATA_DIR / "hidrosur_metadata.json"

PUBLIC_DATA_DIR = Path(__file__).parent.parent.parent.parent / "data"

HIDROSUR_PLUVIOS_META_URL = "https://www.redhidrosurmedioambiente.es/saih/assets/visorSAIH/capas/Pluviometricas.json"
HIDROSUR_AFOROS_META_URL = "https://www.redhidrosurmedioambiente.es/saih/assets/visorSAIH/capas/Aforos.json"
HIDROSUR_EMBALSES_META_URL = "https://www.redhidrosurmedioambiente.es/saih/assets/visorSAIH/capas/Embalses_pto.json"

HIDROSUR_RESUMEN_PLUVIOS_URL = "https://www.redhidrosurmedioambiente.es/saih/resumen/precipitacion"
HIDROSUR_RESUMEN_AFOROS_URL = "https://www.redhidrosurmedioambiente.es/saih/resumen/rios"
HIDROSUR_RESUMEN_EMBALSES_URL = "https://www.redhidrosurmedioambiente.es/saih/resumen/embalses"

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
SYNC_TTL_SECONDS = 300


def _parse_num(val_str: Optional[str]) -> float:
    if not val_str:
        return 0.0
    s = (
        str(val_str)
        .replace("*", "")
        .replace("mm", "")
        .replace("m³/s", "")
        .replace("m3/s", "")
        .replace("m", "")
        .replace("%", "")
        .replace("hm³", "")
        .replace("hm3", "")
        .strip()
    )
    if s.lower() in ("n/d", "-", "--", "", "null", "none", "\xa0"):
        return 0.0
    if "." in s and "," in s:
        s = s.replace(".", "").replace(",", ".")
    else:
        s = s.replace(",", ".")
    m = re.search(r"[-+]?\d+(?:\.\d+)?", s)
    if m:
        try:
            return round(float(m.group(0)), 3)
        except ValueError:
            return 0.0
    return 0.0


class HidrosurService:
    def __init__(self):
        self._pluvios: List[Dict[str, Any]] = []
        self._pluvios_by_id: Dict[str, Dict[str, Any]] = {}

        self._aforos: List[Dict[str, Any]] = []
        self._aforos_by_id: Dict[str, Dict[str, Any]] = {}

        self._embalses: List[Dict[str, Any]] = []
        self._embalses_by_id: Dict[str, Dict[str, Any]] = {}

        self._metadata_pluvios: Dict[str, Dict[str, Any]] = {}
        self._metadata_aforos: Dict[str, Dict[str, Any]] = {}
        self._metadata_embalses: Dict[str, Dict[str, Any]] = {}

        self._history_buffer: Dict[str, List[Dict[str, Any]]] = {}
        self._history_series_cache: Dict[str, Dict[str, Any]] = {}  # {cache_key: {'ts': dt, 'data': dict}}

        self._last_pluvios_sync_time: Optional[datetime] = None
        self._last_aforos_sync_time: Optional[datetime] = None
        self._last_embalses_sync_time: Optional[datetime] = None

        self._sync_lock = asyncio.Lock()
        self._load_from_disk()

    def _load_from_disk(self):
        """Carga estaciones y metadatos de Hidrosur desde caché en disco."""
        # Pluviómetros
        if STATIC_HIDROSUR_PLUVIOS_FILE.exists():
            try:
                with open(STATIC_HIDROSUR_PLUVIOS_FILE, "r", encoding="utf-8") as f:
                    self._pluvios = json.load(f)
                    self._index_pluvios()
                    self._last_pluvios_sync_time = datetime.fromtimestamp(STATIC_HIDROSUR_PLUVIOS_FILE.stat().st_mtime)
            except Exception as e:
                logger.error(f"Error al leer {STATIC_HIDROSUR_PLUVIOS_FILE}: {e}")

        # Aforos
        if STATIC_HIDROSUR_AFOROS_FILE.exists():
            try:
                with open(STATIC_HIDROSUR_AFOROS_FILE, "r", encoding="utf-8") as f:
                    self._aforos = json.load(f)
                    self._index_aforos()
                    self._last_aforos_sync_time = datetime.fromtimestamp(STATIC_HIDROSUR_AFOROS_FILE.stat().st_mtime)
            except Exception as e:
                logger.error(f"Error al leer {STATIC_HIDROSUR_AFOROS_FILE}: {e}")

        # Embalses
        if STATIC_HIDROSUR_EMBALSES_FILE.exists():
            try:
                with open(STATIC_HIDROSUR_EMBALSES_FILE, "r", encoding="utf-8") as f:
                    self._embalses = json.load(f)
                    self._index_embalses()
                    self._last_embalses_sync_time = datetime.fromtimestamp(STATIC_HIDROSUR_EMBALSES_FILE.stat().st_mtime)
            except Exception as e:
                logger.error(f"Error al leer {STATIC_HIDROSUR_EMBALSES_FILE}: {e}")

    def _index_pluvios(self):
        idx = {}
        for p in self._pluvios:
            if "id_estacion" in p:
                idx[str(p["id_estacion"])] = p
            if "codigo" in p:
                idx[str(p["codigo"])] = p
            if "sensor_code" in p:
                idx[str(p["sensor_code"]).upper()] = p
        self._pluvios_by_id = idx

    def _index_aforos(self):
        idx = {}
        for a in self._aforos:
            if "id_variable" in a:
                idx[str(a["id_variable"])] = a
            if "id_estacion" in a:
                idx[str(a["id_estacion"])] = a
            if "codigo" in a:
                idx[str(a["codigo"])] = a
            if "sensor_code" in a:
                idx[str(a["sensor_code"]).upper()] = a
        self._aforos_by_id = idx

    def _index_embalses(self):
        idx = {}
        for e in self._embalses:
            if "id_estacion" in e:
                idx[str(e["id_estacion"])] = e
            if "codigo" in e:
                idx[str(e["codigo"]).upper()] = e
            if "id_volumen" in e:
                idx[str(e["id_volumen"])] = e
            if "sensor_code" in e:
                idx[str(e["sensor_code"]).upper()] = e
        self._embalses_by_id = idx

    # ==========================================
    # METADATOS
    # ==========================================

    def _fetch_metadata(self, url: str) -> Dict[str, Dict[str, Any]]:
        try:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
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
                    "aviso": _parse_num(str(props.get("Aviso", ""))),
                    "prealerta": _parse_num(str(props.get("Prealerta", ""))),
                    "alerta": _parse_num(str(props.get("Alerta", ""))),
                    "max_nivel": _parse_num(str(props.get("Max_Nivel", ""))),
                    "tipo_estacion": props.get("Tipo_de_Es", "Estación"),
                    "estado": props.get("Estado", "activa") == "activa"
                }
            return meta_map
        except Exception as e:
            logger.warning(f"Error descargando metadatos desde {url}: {e}")
            return {}

    # ==========================================
    # 1. PLUVIÓMETROS
    # ==========================================

    def sync_pluvios_metadata(self) -> List[Dict[str, Any]]:
        if not self._metadata_pluvios:
            self._metadata_pluvios = self._fetch_metadata(HIDROSUR_PLUVIOS_META_URL)

        metadata = self._metadata_pluvios
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        req = urllib.request.Request(HIDROSUR_RESUMEN_PLUVIOS_URL, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, context=ctx, timeout=15) as resp:
                page_html = resp.read().decode("utf-8", "ignore")
        except Exception as e:
            logger.error(f"Error al descargar resumen de lluvias de Hidrosur: {e}")
            return self._pluvios

        rows = re.findall(r"<tr[^>]*>(.*?)</tr>", page_html, re.DOTALL)
        now_madrid = datetime.now(MADRID_TZ)
        now_iso = now_madrid.strftime("%Y-%m-%d %H:%M:%S")

        matched_stations: Dict[str, Dict[str, Any]] = {}

        for r in rows:
            cols = [html.unescape(re.sub(r"<[^>]+>", "", c).strip()) for c in re.findall(r"<td[^>]*>(.*?)</td>", r, re.DOTALL)]
            if len(cols) >= 8:
                cod = cols[0].strip()
                nombre_tbl = cols[1].strip()
                provincia_tbl = cols[2].strip()
                r1h = _parse_num(cols[3])
                r_prev = _parse_num(cols[4])
                r12h = _parse_num(cols[5])
                r24h = _parse_num(cols[6])
                rhoy = _parse_num(cols[7])
                rayer = _parse_num(cols[8]) if len(cols) > 8 else 0.0

                chart_m = re.search(r"grafica/([a-zA-Z0-9_]+)", r)
                sensor_code = chart_m.group(1) if chart_m else f"{cod.zfill(3)}P01"

                meta = metadata.get(cod)
                if not meta:
                    clean_target = re.sub(r"\s*\(.*?\)\s*", "", nombre_tbl).strip().upper()
                    for m_cod, m_val in metadata.items():
                        m_name_clean = re.sub(r"\s*\(.*?\)\s*", "", m_val["nombre"]).strip().upper()
                        if clean_target == m_name_clean or clean_target in m_name_clean:
                            meta = m_val
                            break

                if not meta or meta.get("lat") is None or meta.get("lon") is None:
                    continue

                station_key = meta["cod"]
                if station_key not in self._history_buffer:
                    self._history_buffer[station_key] = []

                cutoff = now_madrid - timedelta(hours=4, minutes=15)
                self._history_buffer[station_key] = [
                    item for item in self._history_buffer[station_key] if item["ts"] > cutoff
                ]
                self._history_buffer[station_key].append({"ts": now_madrid, "r1h": r1h})

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
                    "provincia": meta.get("provincia") or provincia_tbl,
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

        # Incorporar estaciones restantes del catálogo
        for cod, meta in metadata.items():
            if cod not in matched_stations and meta.get("lat") is not None and meta.get("lon") is not None:
                matched_stations[cod] = {
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

        self._pluvios = list(matched_stations.values())
        self._index_pluvios()
        self._last_pluvios_sync_time = datetime.now()

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

        if PUBLIC_DATA_DIR.exists():
            try:
                with open(PUBLIC_DATA_DIR / "hidrosur_lluvias.geojson", "w", encoding="utf-8") as f:
                    json.dump(geojson_data, f, ensure_ascii=False, indent=2)
            except Exception:
                pass

        return self._pluvios

    def _trigger_bg_pluvios_sync(self):
        try:
            loop = asyncio.get_running_loop()
            if not getattr(self, "_bg_pluvios_syncing", False):
                self._bg_pluvios_syncing = True
                loop.create_task(self._do_bg_pluvios_sync())
        except RuntimeError:
            pass

    async def _do_bg_pluvios_sync(self):
        try:
            async with self._sync_lock:
                await asyncio.to_thread(self.sync_pluvios_metadata)
        except Exception as e:
            logger.warning(f"Error en sync background pluvios Hidrosur: {e}")
        finally:
            self._bg_pluvios_syncing = False

    async def get_pluvios(self) -> List[Dict[str, Any]]:
        is_stale = not self._last_pluvios_sync_time or (datetime.now() - self._last_pluvios_sync_time).total_seconds() >= SYNC_TTL_SECONDS
        if len(self._pluvios) > 0:
            if is_stale:
                self._trigger_bg_pluvios_sync()
            return self._pluvios

        async with self._sync_lock:
            if len(self._pluvios) == 0:
                await asyncio.to_thread(self.sync_pluvios_metadata)
        return self._pluvios

    async def get_pluvios_geojson(self) -> Dict[str, Any]:
        pluvios = await self.get_pluvios()
        return {
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature", "geometry": {"type": "Point", "coordinates": [p["lon"], p["lat"]]}, "properties": p}
                for p in pluvios
            ]
        }

    # ==========================================
    # 2. AFOROS (CAUDALES EN RÍOS)
    # ==========================================

    def sync_aforos_metadata(self) -> List[Dict[str, Any]]:
        if not self._metadata_aforos:
            self._metadata_aforos = self._fetch_metadata(HIDROSUR_AFOROS_META_URL)

        metadata = self._metadata_aforos
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        req = urllib.request.Request(HIDROSUR_RESUMEN_AFOROS_URL, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, context=ctx, timeout=15) as resp:
                page_html = resp.read().decode("utf-8", "ignore")
        except Exception as e:
            logger.error(f"Error al descargar resumen de aforos de Hidrosur: {e}")
            return self._aforos

        rows = re.findall(r"<tr[^>]*>(.*?)</tr>", page_html, re.DOTALL)
        now_madrid = datetime.now(MADRID_TZ)
        now_iso = now_madrid.strftime("%Y-%m-%d %H:%M:%S")

        aforos_list = []
        for r in rows:
            cols = [html.unescape(re.sub(r"<[^>]+>", "", c).strip()) for c in re.findall(r"<td[^>]*>(.*?)</td>", r, re.DOTALL)]
            if len(cols) >= 8:
                cod = cols[0].strip()
                nombre_tbl = cols[1].strip()
                nivel = _parse_num(cols[2])
                caudal = _parse_num(cols[3])
                chart_m = re.search(r"grafica/([a-zA-Z0-9_]+)", r)
                sensor_code = chart_m.group(1) if chart_m else f"{cod.zfill(3)}R02"

                meta = metadata.get(cod) or {}
                lat = meta.get("lat")
                lon = meta.get("lon")

                if lat is None or lon is None:
                    continue

                st_dict = {
                    "id_variable": f"hidrosur_{cod}_caudal",
                    "id_estacion": f"hidrosur_{cod}",
                    "codigo": f"HIDRO_{cod}",
                    "sensor_code": sensor_code,
                    "nombre": meta.get("nombre") or nombre_tbl,
                    "rio": meta.get("rio") or nombre_tbl,
                    "variable": "Caudal en Río",
                    "tipo": "Aforo",
                    "red": "HIDROSUR",
                    "fuente": "S.A.I.H. Hidrosur (Junta de Andalucía)",
                    "lat": lat,
                    "lon": lon,
                    "altitud": meta.get("cota"),
                    "municipio": meta.get("municipio", ""),
                    "poblacion": meta.get("municipio") or meta.get("nombre", ""),
                    "provincia": meta.get("provincia", ""),
                    "subcuenca": meta.get("subsistema", ""),
                    "sistema": meta.get("sistema", ""),
                    "ultimo_caudal": caudal,
                    "ultimo_nivel": nivel,
                    "caudal": caudal,
                    "nivel": nivel,
                    "unidad_umbrales": "m",
                    "tipo_umbral": "nivel",
                    "umbrales": {
                        "amarillo": meta.get("aviso") or 0.0,
                        "naranja": meta.get("prealerta") or 0.0,
                        "rojo": meta.get("alerta") or 0.0
                    },
                    "max_historico_nivel": meta.get("max_nivel"),
                    "ultima_hora": now_iso,
                    "fecha_comunicacion": now_iso,
                    "unidad": "m³/s",
                    "unidad_grafica": "m"
                }
                aforos_list.append(st_dict)

        if aforos_list:
            self._aforos = aforos_list
            self._index_aforos()
            self._last_aforos_sync_time = datetime.now()

            DATA_DIR.mkdir(parents=True, exist_ok=True)
            with open(STATIC_HIDROSUR_AFOROS_FILE, "w", encoding="utf-8") as f:
                json.dump(self._aforos, f, ensure_ascii=False, indent=2)

            geojson_data = {
                "type": "FeatureCollection",
                "features": [
                    {"type": "Feature", "geometry": {"type": "Point", "coordinates": [a["lon"], a["lat"]]}, "properties": a}
                    for a in self._aforos
                ]
            }
            with open(HIDROSUR_AFOROS_GEOJSON_FILE, "w", encoding="utf-8") as f:
                json.dump(geojson_data, f, ensure_ascii=False, indent=2)

        return self._aforos

    def _trigger_bg_aforos_sync(self):
        try:
            loop = asyncio.get_running_loop()
            if not getattr(self, "_bg_aforos_syncing", False):
                self._bg_aforos_syncing = True
                loop.create_task(self._do_bg_aforos_sync())
        except RuntimeError:
            pass

    async def _do_bg_aforos_sync(self):
        try:
            async with self._sync_lock:
                await asyncio.to_thread(self.sync_aforos_metadata)
        except Exception as e:
            logger.warning(f"Error en sync background aforos Hidrosur: {e}")
        finally:
            self._bg_aforos_syncing = False

    async def get_caudales(self) -> List[Dict[str, Any]]:
        is_stale = not self._last_aforos_sync_time or (datetime.now() - self._last_aforos_sync_time).total_seconds() >= SYNC_TTL_SECONDS
        if len(self._aforos) > 0:
            if is_stale:
                self._trigger_bg_aforos_sync()
            return self._aforos

        async with self._sync_lock:
            if len(self._aforos) == 0:
                await asyncio.to_thread(self.sync_aforos_metadata)
        return self._aforos

    async def get_caudales_geojson(self) -> Dict[str, Any]:
        aforos = await self.get_caudales()
        return {
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature", "geometry": {"type": "Point", "coordinates": [a["lon"], a["lat"]]}, "properties": a}
                for a in aforos
            ]
        }

    # ==========================================
    # 3. EMBALSES Y PRESAS
    # ==========================================

    def sync_embalses_metadata(self) -> List[Dict[str, Any]]:
        if not self._metadata_embalses:
            self._metadata_embalses = self._fetch_metadata(HIDROSUR_EMBALSES_META_URL)

        metadata = self._metadata_embalses
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        req = urllib.request.Request(HIDROSUR_RESUMEN_EMBALSES_URL, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, context=ctx, timeout=15) as resp:
                page_html = resp.read().decode("utf-8", "ignore")
        except Exception as e:
            logger.error(f"Error al descargar resumen de embalses de Hidrosur: {e}")
            return self._embalses

        rows = re.findall(r"<tr[^>]*>(.*?)</tr>", page_html, re.DOTALL)
        now_madrid = datetime.now(MADRID_TZ)
        now_iso = now_madrid.strftime("%Y-%m-%d %H:%M:%S")

        emb_list = []
        for r in rows:
            cols = [html.unescape(re.sub(r"<[^>]+>", "", c).strip()) for c in re.findall(r"<td[^>]*>(.*?)</td>", r, re.DOTALL)]
            if len(cols) >= 6:
                cod = cols[0].strip()
                nombre_tbl = cols[1].strip()
                pct = _parse_num(cols[2])
                capacidad = _parse_num(cols[3])
                pluv_ano = _parse_num(cols[4]) if len(cols) > 4 else None
                volumen = _parse_num(cols[5]) if len(cols) > 5 else None
                var_semana = _parse_num(cols[6]) if len(cols) > 6 else 0.0
                vol_sem_ant = _parse_num(cols[7]) if len(cols) > 7 else None
                var_ano = _parse_num(cols[8]) if len(cols) > 8 else None
                vol_ano_ant = _parse_num(cols[9]) if len(cols) > 9 else None

                chart_m = re.search(r"grafica/([a-zA-Z0-9_]+)", r)
                sensor_code = chart_m.group(1) if chart_m else f"{cod.zfill(3)}E01"

                meta = metadata.get(cod)
                if not meta:
                    clean_target = (
                        re.sub(r"\s*\(.*?\)\s*", "", nombre_tbl)
                        .replace("EMBALSE", "")
                        .replace("DE", "")
                        .replace("DEL", "")
                        .replace("LA", "")
                        .replace("LOS", "")
                        .replace("EL", "")
                        .replace("-", " ")
                        .strip()
                        .upper()
                    )
                    for m_cod, m_val in metadata.items():
                        m_name_clean = (
                            re.sub(r"\s*\(.*?\)\s*", "", m_val["nombre"])
                            .replace("EMBALSE", "")
                            .replace("DE", "")
                            .replace("DEL", "")
                            .replace("LA", "")
                            .replace("LOS", "")
                            .replace("EL", "")
                            .replace("-", " ")
                            .strip()
                            .upper()
                        )
                        if clean_target and (clean_target == m_name_clean or clean_target in m_name_clean or m_name_clean in clean_target):
                            meta = m_val
                            break

                if not meta:
                    meta = {}

                lat = meta.get("lat")
                lon = meta.get("lon")

                if lat is None or lon is None:
                    continue

                st_dict = {
                    "id_estacion": f"hidrosur_emb_{cod}",
                    "codigo": f"HIDRO_{cod}",
                    "id_volumen": f"hidrosur_emb_{cod}_vol",
                    "sensor_code": sensor_code,
                    "nombre": meta.get("nombre") or nombre_tbl,
                    "rio": meta.get("rio") or "",
                    "tipo": "Embalse",
                    "red": "HIDROSUR",
                    "fuente": "S.A.I.H. Hidrosur (Junta de Andalucía)",
                    "lat": lat,
                    "lon": lon,
                    "cota_actual": meta.get("cota"),
                    "capacidad_nmn": capacidad,
                    "volumen_actual": volumen,
                    "porcentaje_llenado": pct,
                    "variacion_24h": var_semana,
                    "variacion_semana": var_semana,
                    "pluviometria_anual": pluv_ano,
                    "volumen_semana_ant": vol_sem_ant,
                    "variacion_ano": var_ano,
                    "volumen_ano_ant": vol_ano_ant,
                    "municipio": meta.get("municipio", ""),
                    "poblacion": meta.get("municipio") or meta.get("nombre", ""),
                    "provincia": meta.get("provincia", ""),
                    "subcuenca": meta.get("subsistema", ""),
                    "sistema": meta.get("sistema", ""),
                    "ultima_hora": now_iso,
                    "fecha_comunicacion": now_iso
                }
                emb_list.append(st_dict)

        if emb_list:
            self._embalses = emb_list
            self._index_embalses()
            self._last_embalses_sync_time = datetime.now()

            DATA_DIR.mkdir(parents=True, exist_ok=True)
            with open(STATIC_HIDROSUR_EMBALSES_FILE, "w", encoding="utf-8") as f:
                json.dump(self._embalses, f, ensure_ascii=False, indent=2)

            geojson_data = {
                "type": "FeatureCollection",
                "features": [
                    {"type": "Feature", "geometry": {"type": "Point", "coordinates": [e["lon"], e["lat"]]}, "properties": e}
                    for e in self._embalses
                ]
            }
            with open(HIDROSUR_EMBALSES_GEOJSON_FILE, "w", encoding="utf-8") as f:
                json.dump(geojson_data, f, ensure_ascii=False, indent=2)

        return self._embalses

    def _trigger_bg_embalses_sync(self):
        try:
            loop = asyncio.get_running_loop()
            if not getattr(self, "_bg_embalses_syncing", False):
                self._bg_embalses_syncing = True
                loop.create_task(self._do_bg_embalses_sync())
        except RuntimeError:
            pass

    async def _do_bg_embalses_sync(self):
        try:
            async with self._sync_lock:
                await asyncio.to_thread(self.sync_embalses_metadata)
        except Exception as e:
            logger.warning(f"Error en sync background embalses Hidrosur: {e}")
        finally:
            self._bg_embalses_syncing = False

    async def get_embalses(self) -> List[Dict[str, Any]]:
        is_stale = not self._last_embalses_sync_time or (datetime.now() - self._last_embalses_sync_time).total_seconds() >= SYNC_TTL_SECONDS
        if len(self._embalses) > 0:
            if is_stale:
                self._trigger_bg_embalses_sync()
            return self._embalses

        async with self._sync_lock:
            if len(self._embalses) == 0:
                await asyncio.to_thread(self.sync_embalses_metadata)
        return self._embalses

    async def get_embalses_geojson(self) -> Dict[str, Any]:
        embalses = await self.get_embalses()
        return {
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature", "geometry": {"type": "Point", "coordinates": [e["lon"], e["lat"]]}, "properties": e}
                for e in embalses
            ]
        }

    # ==========================================
    # 4. SERIES TEMPORALES HISTÓRICAS
    # ==========================================

    def _fetch_series_from_web(self, sensor_code: str, is_embalse: bool = False) -> List[Dict[str, Any]]:
        """Descarga el array de las últimas 48h desde la gráfica de Hidrosur."""
        suffix = "/calculada" if is_embalse else ""
        url = f"https://www.redhidrosurmedioambiente.es/saih/mapa/tiempo/real/grafica/{sensor_code}{suffix}"

        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})

        try:
            with urllib.request.urlopen(req, context=ctx, timeout=12) as resp:
                content = resp.read().decode("utf-8", errors="ignore")

            m_labels = re.search(r"var\s+labels\s*=\s*(\[.*?\]);", content)
            m_serie = re.search(r"var\s+serie1\s*=\s*(\[.*?\]);", content)
            if not m_labels or not m_serie:
                return []

            labels_raw = m_labels.group(1).replace(r"\/", "/")
            labels = json.loads(labels_raw)
            series = json.loads(m_serie.group(1))

            points = []
            for l_str, val in zip(labels, series):
                d_m = re.match(r"(\d{2})/(\d{2})/(\d{2})\s+(\d{2}):(\d{2})", l_str)
                if d_m:
                    day, month, yr, hh, mm = d_m.groups()
                    full_yr = f"20{yr}" if len(yr) == 2 else yr
                    iso_date = f"{full_yr}-{month}-{day} {hh}:{mm}:00"
                else:
                    iso_date = l_str

                num_val = None
                if val is not None and val != "n/d" and val != "":
                    try:
                        num_val = round(float(val), 3)
                    except:
                        num_val = None

                points.append({
                    "fecha": iso_date,
                    "valor": num_val,
                    "caudal": num_val if not is_embalse else None,
                    "volumen": num_val if is_embalse else None
                })
            return points
        except Exception as e:
            logger.error(f"Error al descargar serie temporal para sensor {sensor_code}: {e}")
            return []

    async def get_history(
        self,
        id_or_code: str,
        hours: int = 24,
        is_embalse: bool = False
    ) -> Dict[str, Any]:
        """
        Consulta la serie temporal histórica para un aforo o embalse de Hidrosur.
        Devuelve el formato estándar compatible con RainLoc y SAIH CHJ/Guadalquivir.
        """
        sensor_code = None
        station_name = str(id_or_code)
        unidad = "hm³" if is_embalse else "m"
        station_obj = None

        if is_embalse:
            await self.get_embalses()
            emb = self._embalses_by_id.get(str(id_or_code).upper()) or self._embalses_by_id.get(str(id_or_code))
            if emb:
                sensor_code = emb.get("sensor_code")
                station_name = emb.get("nombre", station_name)
                station_obj = emb
        else:
            await self.get_caudales()
            aforo = self._aforos_by_id.get(str(id_or_code).upper()) or self._aforos_by_id.get(str(id_or_code))
            if aforo:
                sensor_code = aforo.get("sensor_code")
                station_name = aforo.get("nombre", station_name)
                station_obj = aforo

        if not sensor_code:
            clean_digits = re.sub(r"\D", "", str(id_or_code))
            if clean_digits:
                sensor_code = f"{clean_digits.zfill(3)}{'E01' if is_embalse else 'R02'}"
            else:
                sensor_code = str(id_or_code)

        req_hours = int(hours) if isinstance(hours, (int, float)) and hours > 0 else 24
        cache_key = f"{sensor_code}_{'emb' if is_embalse else 'aforo'}"
        now = datetime.now(MADRID_TZ)

        # Caché de 5 minutos para series temporales
        cached = self._history_series_cache.get(cache_key)
        if cached and (now - cached["ts"]).total_seconds() < 300:
            full_series = cached["data"]
        else:
            full_series = await asyncio.to_thread(self._fetch_series_from_web, sensor_code, is_embalse)
            if full_series:
                self._history_series_cache[cache_key] = {"ts": now, "data": full_series}

        cutoff = now - timedelta(hours=req_hours)
        filtered_series = [
            p for p in full_series
            if datetime.strptime(p["fecha"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=MADRID_TZ) >= cutoff
        ] if full_series else []

        if not filtered_series and full_series:
            filtered_series = full_series

        valid_vals = [p["valor"] for p in filtered_series if p["valor"] is not None]
        min_val = min(valid_vals) if valid_vals else 0.0
        max_val = max(valid_vals) if valid_vals else 0.0
        last_val = valid_vals[-1] if valid_vals else 0.0
        last_date = filtered_series[-1]["fecha"] if filtered_series else now.strftime("%Y-%m-%d %H:%M:%S")

        return {
            "id_variable": str(id_or_code),
            "estacion": station_obj,
            "nombre": station_name,
            "sensor_code": sensor_code,
            "red": "HIDROSUR",
            "fuente": "S.A.I.H. Hidrosur (Junta de Andalucía)",
            "total_puntos": len(filtered_series),
            "puntos_totales": len(filtered_series),
            "horas_solicitadas": req_hours,
            "rango": {
                "desde": cutoff.strftime("%Y-%m-%d %H:%M:%S"),
                "hasta": now.strftime("%Y-%m-%d %H:%M:%S"),
                "horas": req_hours,
            },
            "serie": filtered_series,
            "datos": filtered_series,
            "min_valor": min_val,
            "max_valor": max_val,
            "ultimo_valor": last_val,
            "fecha_ultimo": last_date,
            "unidad": unidad
        }


hidrosur_service = HidrosurService()
# Alias para compatibilidad con código existente
hidrosur_pluvios_service = hidrosur_service
