"""
Servicio de integración de datos hidrológicos y meteorológicos del SAIH Ebro (Confederación Hidrográfica del Ebro - CHE / MITECO)
Proporciona datos en tiempo real de pluviómetros (331 estaciones), embalses (61 embalses) y aforos en ríos (270 estaciones).
Implementa modo dual de bajo consumo (Zero Waste):
 - Modo Estándar (sin API Key): 3 peticiones HTTP en total (331 pluvios, 61 embalses, aforos principales).
 - Modo Completo (con API Key): 4 peticiones HTTP en total (consulta masiva Open Data de toda la cuenca).
"""

import asyncio
import json
import logging
import os
import re
import ssl
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger("rainloc.ebro_service")

MADRID_TZ = ZoneInfo("Europe/Madrid")
SYNC_TTL_SECONDS = 300  # 5 minutos de caché en memoria

EBRO_BASE_URL = "https://www.saihebro.com"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 RainLoc/3.0"

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
PUBLIC_DATA_DIR = Path(__file__).resolve().parent.parent.parent.parent / "public" / "data"

STATIC_EBRO_PLUVIOS_FILE = DATA_DIR / "ebro_lluvias_estaciones.json"
STATIC_EBRO_AFOROS_FILE = DATA_DIR / "ebro_aforos_estaciones.json"
STATIC_EBRO_EMBALSES_FILE = DATA_DIR / "ebro_embalses_estaciones.json"
EBRO_META_FILE = DATA_DIR / "ebro_metadata.json"

EBRO_PLUVIOS_GEOJSON_FILE = DATA_DIR / "ebro_lluvias.geojson"
EBRO_AFOROS_GEOJSON_FILE = DATA_DIR / "ebro_aforos.geojson"
EBRO_EMBALSES_GEOJSON_FILE = DATA_DIR / "ebro_embalses.geojson"


def _parse_num(val: Any) -> Optional[float]:
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val)
    cleaned = (
        str(val)
        .replace("&nbsp;", "")
        .replace("\xa0", "")
        .replace("hm³", "")
        .replace("m³/s", "")
        .replace("msnm", "")
        .replace("m", "")
        .replace("l/m²", "")
        .replace("mm", "")
        .replace("%", "")
        .replace("*", "")
        .strip()
    )
    if not cleaned or cleaned in ("--", "-"):
        return None
    cleaned = cleaned.replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return None


class EbroService:
    def __init__(self):
        self._api_key: Optional[str] = os.getenv("EBRO_API_KEY", "").strip() or None
        self._pluvios: List[Dict[str, Any]] = []
        self._aforos: List[Dict[str, Any]] = []
        self._embalses: List[Dict[str, Any]] = []

        self._metadata_by_code: Dict[str, Dict[str, Any]] = {}

        self._pluvios_by_id: Dict[str, Dict[str, Any]] = {}
        self._aforos_by_id: Dict[str, Dict[str, Any]] = {}
        self._embalses_by_id: Dict[str, Dict[str, Any]] = {}

        self._last_pluvios_sync_time: Optional[datetime] = None
        self._last_aforos_sync_time: Optional[datetime] = None
        self._last_embalses_sync_time: Optional[datetime] = None

        self._sync_lock = asyncio.Lock()
        self._load_cached_files()

    def set_api_key(self, api_key: Optional[str]):
        """Permite configurar o actualizar la API Key del SAIH Ebro en tiempo de ejecución."""
        self._api_key = str(api_key).strip() if api_key else None

    def _get_ssl_context(self) -> ssl.SSLContext:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx

    def _load_cached_files(self):
        if EBRO_META_FILE.exists():
            try:
                with open(EBRO_META_FILE, "r", encoding="utf-8") as f:
                    self._metadata_by_code = json.load(f)
            except Exception as e:
                logger.warning(f"Error cargando metadatos de Ebro: {e}")

        if STATIC_EBRO_PLUVIOS_FILE.exists():
            try:
                with open(STATIC_EBRO_PLUVIOS_FILE, "r", encoding="utf-8") as f:
                    self._pluvios = json.load(f)
                    self._index_pluvios()
            except Exception as e:
                logger.warning(f"Error cargando pluviómetros guardados de Ebro: {e}")

        if STATIC_EBRO_AFOROS_FILE.exists():
            try:
                with open(STATIC_EBRO_AFOROS_FILE, "r", encoding="utf-8") as f:
                    self._aforos = json.load(f)
                    self._index_aforos()
            except Exception as e:
                logger.warning(f"Error cargando aforos guardados de Ebro: {e}")

        if STATIC_EBRO_EMBALSES_FILE.exists():
            try:
                with open(STATIC_EBRO_EMBALSES_FILE, "r", encoding="utf-8") as f:
                    self._embalses = json.load(f)
                    self._index_embalses()
            except Exception as e:
                logger.warning(f"Error cargando embalses guardados de Ebro: {e}")

    def _index_pluvios(self):
        idx = {}
        for p in self._pluvios:
            if p.get("id_estacion"):
                idx[str(p["id_estacion"])] = p
            if p.get("codigo"):
                idx[str(p["codigo"]).upper()] = p
                idx[str(p["codigo"])] = p
            if p.get("codigo_corto"):
                idx[str(p["codigo_corto"]).upper()] = p
                idx[str(p["codigo_corto"])] = p
        self._pluvios_by_id = idx

    def _index_aforos(self):
        idx = {}
        for a in self._aforos:
            if a.get("id_estacion"):
                idx[str(a["id_estacion"])] = a
            if a.get("id_variable"):
                idx[str(a["id_variable"])] = a
            if a.get("codigo"):
                idx[str(a["codigo"]).upper()] = a
                idx[str(a["codigo"])] = a
            if a.get("codigo_corto"):
                idx[str(a["codigo_corto"]).upper()] = a
                idx[str(a["codigo_corto"])] = a
            if a.get("tag_caudal"):
                idx[str(a["tag_caudal"])] = a
            if a.get("tag_nivel"):
                idx[str(a["tag_nivel"])] = a
        self._aforos_by_id = idx

    def _index_embalses(self):
        idx = {}
        for e in self._embalses:
            if e.get("id_estacion"):
                idx[str(e["id_estacion"])] = e
            if e.get("id_volumen"):
                idx[str(e["id_volumen"])] = e
            if e.get("codigo"):
                idx[str(e["codigo"]).upper()] = e
                idx[str(e["codigo"])] = e
            if e.get("codigo_corto"):
                idx[str(e["codigo_corto"]).upper()] = e
                idx[str(e["codigo_corto"])] = e
        self._embalses_by_id = idx

    # ==========================================
    # 0. METADATOS Y COORDENADAS (Auto-descarga si no existen)
    # ==========================================

    def sync_all_metadata(self) -> Dict[str, Dict[str, Any]]:
        """Descarga e indexa automáticamente el catálogo de metadatos y coordenadas de la CHE."""
        logger.info("Descargando catálogo de metadatos y coordenadas de SAIH Ebro...")
        from concurrent.futures import ThreadPoolExecutor
        from pyproj import Transformer

        ctx = self._get_ssl_context()
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "application/json, text/plain, */*",
            "X-Requested-With": "XMLHttpRequest",
        }
        trans30 = Transformer.from_crs("EPSG:25830", "EPSG:4326", always_xy=True)

        all_codes = set()
        try:
            req = urllib.request.Request(f"{EBRO_BASE_URL}/api/datos-historicos/getEstaciones?tipoConsolidado=quinceminutal", headers=headers)
            with urllib.request.urlopen(req, context=ctx, timeout=10) as resp:
                est_list = json.loads(resp.read().decode("utf-8"))
            for e in est_list:
                all_codes.add(e.get("id"))
        except Exception as err:
            logger.warning(f"Error obteniendo lista de estaciones de Ebro: {err}")

        # Añadir subcuencas aforos
        try:
            req2 = urllib.request.Request(f"{EBRO_BASE_URL}/api/pluviometrias/getTablaPluviometrias", headers=headers)
            with urllib.request.urlopen(req2, context=ctx, timeout=10) as resp:
                pluvs = json.loads(resp.read().decode("utf-8"))
            for p in pluvs:
                all_codes.add(p.get("codigo"))
        except Exception:
            pass

        meta_result = {}

        def _fetch_one(code):
            if not code:
                return code, None
            try:
                url = f"{EBRO_BASE_URL}/api/ficha/procesarTablaInfoGeneral?estacion={code}"
                req_f = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req_f, context=ctx, timeout=10) as resp_f:
                    data = json.loads(resp_f.read().decode("utf-8"))
                html_info = data.get("INFO_GENERAL", "")
                lat_m = re.search(r"data-lat=[\"']([^\"']+)[\"']", html_info)
                lng_m = re.search(r"data-lng=[\"']([^\"']+)[\"']", html_info)
                desc_m = re.search(r"<td>Descripci(?:ó|o)n</td>\s*<td>(.*?)</td>", html_info)
                rio_m = re.search(r"<td>R(?:í|i)o</td>\s*<td>(.*?)</td>", html_info)
                pob_m = re.search(r"<td>Poblaci(?:ó|o)n</td>\s*<td>(.*?)</td>", html_info)
                prov_m = re.search(r"<td>Provincia</td>\s*<td>(.*?)</td>", html_info)
                ca_m = re.search(r"<td>Comunidad Aut(?:ó|o)noma</td>\s*<td>(.*?)</td>", html_info)
                vol_tot_m = re.search(r"<td>Volumen total</td>\s*<td>(.*?)</td>", html_info)
                cota_m_m = re.search(r"<td>Cota m(?:í|i)nima</td>\s*<td>(.*?)</td>", html_info)
                cota_cor_m = re.search(r"<td>Cota coronaci(?:ó|o)n</td>\s*<td>(.*?)</td>", html_info)
                cota_nmn_m = re.search(r"<td>Cota N\.M\.N\.</td>\s*<td>(.*?)</td>", html_info)

                lat = float(lat_m.group(1)) if lat_m else None
                lng = float(lng_m.group(1)) if lng_m else None
                vol_tot = _parse_num(vol_tot_m.group(1)) if vol_tot_m else None

                return code, {
                    "code": code,
                    "nombre": desc_m.group(1).strip() if desc_m else code,
                    "rio": rio_m.group(1).strip() if rio_m else "",
                    "poblacion": pob_m.group(1).strip() if pob_m else "",
                    "provincia": prov_m.group(1).strip() if prov_m else "",
                    "comunidad_autonoma": ca_m.group(1).strip() if ca_m else "",
                    "lat": lat,
                    "lon": lng,
                    "capacidad_total": vol_tot,
                    "cota_minima": _parse_num(cota_m_m.group(1)) if cota_m_m else None,
                    "cota_coronacion": _parse_num(cota_cor_m.group(1)) if cota_cor_m else None,
                    "cota_nmn": _parse_num(cota_nmn_m.group(1)) if cota_nmn_m else None,
                }
            except Exception:
                return code, None

        with ThreadPoolExecutor(max_workers=20) as ex:
            results = ex.map(_fetch_one, list(all_codes))
            for code, meta in results:
                if meta and meta.get("lat") is not None and meta.get("lon") is not None:
                    meta_result[code] = meta

        if meta_result:
            self._metadata_by_code = meta_result
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            with open(EBRO_META_FILE, "w", encoding="utf-8") as f:
                json.dump(self._metadata_by_code, f, ensure_ascii=False, indent=2)
            logger.info(f"Guardados metadatos de {len(meta_result)} estaciones de SAIH Ebro.")

        return self._metadata_by_code

    # ==========================================
    # 1. PLUVIÓMETROS (1 petición a la CHE)
    # ==========================================

    def sync_pluvios_metadata(self) -> List[Dict[str, Any]]:
        """
        Descarga la tabla completa de pluviometrías del SAIH Ebro (331 estaciones).
        Requiere exactamente 1 sola petición HTTP.
        """
        if not self._metadata_by_code:
            self.sync_all_metadata()

        url = f"{EBRO_BASE_URL}/api/pluviometrias/getTablaPluviometrias"
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json, text/plain, */*",
                "X-Requested-With": "XMLHttpRequest",
            },
        )
        ctx = self._get_ssl_context()

        try:
            with urllib.request.urlopen(req, context=ctx, timeout=12) as resp:
                raw_data = json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            logger.error(f"Error descargando pluviometrías de SAIH Ebro: {e}")
            return self._pluvios

        now_madrid = datetime.now(MADRID_TZ)
        now_iso = now_madrid.strftime("%Y-%m-%d %H:%M:%S")

        pluvios_list = []
        for item in raw_data:
            code = str(item.get("codigo", "")).strip()
            if not code:
                continue

            meta = self._metadata_by_code.get(code, {})
            lat = meta.get("lat")
            lon = meta.get("lon")

            if lat is None or lon is None:
                continue

            r1h = round(_parse_num(item.get("col1")) or 0.0, 1)
            rhoy = round(_parse_num(item.get("col2")) or 0.0, 1)
            r24h = round(_parse_num(item.get("col3")) or 0.0, 1)
            rayer = round(_parse_num(item.get("col4")) or 0.0, 1)
            rmes = round(_parse_num(item.get("col5")) or 0.0, 1)
            rano = round(_parse_num(item.get("col6")) or 0.0, 1)

            # Valores reales medidos por la CHE (sin inventar proporciones ficticias)
            eff_24h = r24h
            eff_12h = None
            eff_4h = None

            clean_name = meta.get("nombre") or item.get("nombre") or item.get("nombreCorto") or code
            provincia = meta.get("provincia") or item.get("provincia") or ""
            subcuenca = item.get("zona") or meta.get("comunidad_autonoma") or "Cuenca del Ebro"

            pluvio_dict = {
                "id_estacion": f"ebro_pluv_{code}",
                "codigo": f"EBRO_{code}",
                "codigo_corto": code,
                "nombre": f"{code} {clean_name}" if not clean_name.startswith(code) else clean_name,
                "municipio": meta.get("poblacion", ""),
                "provincia": provincia,
                "subcuenca": subcuenca,
                "lat": lat,
                "lon": lon,
                "red": "EBRO",
                "estado": True,
                "lluvia_1h": r1h,
                "precipitacion_1h": r1h,
                "fecha_1h": now_iso,
                "lluvia_4h": eff_4h,
                "precipitacion_4h": eff_4h,
                "fecha_4h": now_iso,
                "lluvia_12h": eff_12h,
                "precipitacion_12h": eff_12h,
                "fecha_12h": now_iso,
                "lluvia_24h": eff_24h,
                "precipitacion_24h": eff_24h,
                "fecha_24h": now_iso,
                "lluvia_hoy": rhoy,
                "lluvia_ayer": rayer,
                "lluvia_mes": rmes,
                "lluvia_ano": rano,
                "ultima_hora": now_iso,
                "fuente": "SAIH Ebro (Confederación Hidrográfica del Ebro - CHE)",
                "unidad": "mm",
            }
            pluvios_list.append(pluvio_dict)

        if pluvios_list:
            self._pluvios = pluvios_list
            self._index_pluvios()
            self._last_pluvios_sync_time = datetime.now()

            DATA_DIR.mkdir(parents=True, exist_ok=True)
            with open(STATIC_EBRO_PLUVIOS_FILE, "w", encoding="utf-8") as f:
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
            with open(EBRO_PLUVIOS_GEOJSON_FILE, "w", encoding="utf-8") as f:
                json.dump(geojson_data, f, ensure_ascii=False, indent=2)

            if PUBLIC_DATA_DIR.exists():
                try:
                    with open(PUBLIC_DATA_DIR / "ebro_lluvias.geojson", "w", encoding="utf-8") as f:
                        json.dump(geojson_data, f, ensure_ascii=False, indent=2)
                except Exception:
                    pass

        return self._pluvios

    async def get_pluvios(self) -> List[Dict[str, Any]]:
        if not self._last_pluvios_sync_time or (datetime.now() - self._last_pluvios_sync_time).total_seconds() >= SYNC_TTL_SECONDS:
            async with self._sync_lock:
                if not self._last_pluvios_sync_time or (datetime.now() - self._last_pluvios_sync_time).total_seconds() >= SYNC_TTL_SECONDS:
                    await asyncio.to_thread(self.sync_pluvios_metadata)
        return self._pluvios

    async def get_pluvios_geojson(self) -> Dict[str, Any]:
        pluvios = await self.get_pluvios()
        return {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [p["lon"], p["lat"]]},
                    "properties": p,
                }
                for p in pluvios
            ],
        }

    # ==========================================
    # 2. EMBALSES (1 petición a la CHE)
    # ==========================================

    def sync_embalses_metadata(self) -> List[Dict[str, Any]]:
        """
        Descarga los volúmenes embalsados de toda la cuenca del Ebro (61 embalses).
        Requiere exactamente 1 sola petición HTTP.
        """
        if not self._metadata_by_code:
            self.sync_all_metadata()

        url = f"{EBRO_BASE_URL}/api/principal/getVolumenesEmbalsados"
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json, text/plain, */*",
                "X-Requested-With": "XMLHttpRequest",
            },
        )
        ctx = self._get_ssl_context()

        try:
            with urllib.request.urlopen(req, context=ctx, timeout=12) as resp:
                raw_data = json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            logger.error(f"Error descargando embalses de SAIH Ebro: {e}")
            return self._embalses

        volumenes_dict = raw_data.get("volumenes", {})
        now_madrid = datetime.now(MADRID_TZ)
        now_iso = now_madrid.strftime("%Y-%m-%d %H:%M:%S")

        emb_list = []
        for code, emb_obj in volumenes_dict.items():
            if not code.startswith("E"):
                continue

            data_points = emb_obj.get("data", [])
            if not data_points or len(data_points) < 2:
                continue

            # data[0]: max histórico, data[1]: actual, data[2]: año anterior, data[3]: media 5 años, data[4]: mín histórico
            actual_info = data_points[1]
            pct_actual = _parse_num(actual_info.get("y"))
            vol_actual = _parse_num(actual_info.get("volumen"))

            meta = self._metadata_by_code.get(code, {})
            lat = meta.get("lat")
            lon = meta.get("lon")

            if lat is None or lon is None:
                continue

            capacidad_total = meta.get("capacidad_total")
            if not capacidad_total and vol_actual is not None and pct_actual and pct_actual > 0:
                capacidad_total = round((vol_actual / (pct_actual / 100.0)), 2)

            nombre_raw = emb_obj.get("zona") or meta.get("nombre") or code
            clean_name = re.sub(r"^Embalse\s+(?:de\s+la|de\s+los|de\s+las|del|de\s+l'|de\s+|d')\s*", "", nombre_raw, flags=re.IGNORECASE).strip()

            st_dict = {
                "id_estacion": f"ebro_emb_{code}",
                "codigo": f"EBRO_{code}",
                "codigo_corto": code,
                "id_volumen": f"ebro_emb_{code}_vol",
                "nombre": f"Embalse de {clean_name}",
                "tipo": "Embalse",
                "red": "EBRO",
                "fuente": "SAIH Ebro (Confederación Hidrográfica del Ebro - CHE)",
                "lat": lat,
                "lon": lon,
                "volumen": vol_actual,
                "ultimo_volumen": vol_actual,
                "volumen_actual": vol_actual,
                "capacidad": capacidad_total,
                "capacidad_nmn": capacidad_total,
                "porcentaje": pct_actual,
                "porcentaje_llenado": pct_actual,
                "cota_actual": meta.get("cota_nmn"),
                "cota_minima": meta.get("cota_minima"),
                "cota_coronacion": meta.get("cota_coronacion"),
                "municipio": meta.get("poblacion", ""),
                "provincia": meta.get("provincia", ""),
                "subcuenca": "Cuenca Hidrográfica del Ebro",
                "ultima_hora": emb_obj.get("fecha") or now_iso,
                "fecha_comunicacion": now_iso,
                "unidad": "hm³",
                "unidad_volumen": "hm³",
            }
            emb_list.append(st_dict)

        if emb_list:
            self._embalses = emb_list
            self._index_embalses()
            self._last_embalses_sync_time = datetime.now()

            DATA_DIR.mkdir(parents=True, exist_ok=True)
            with open(STATIC_EBRO_EMBALSES_FILE, "w", encoding="utf-8") as f:
                json.dump(self._embalses, f, ensure_ascii=False, indent=2)

            geojson_data = {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "geometry": {"type": "Point", "coordinates": [e["lon"], e["lat"]]},
                        "properties": e,
                    }
                    for e in self._embalses
                ],
            }
            with open(EBRO_EMBALSES_GEOJSON_FILE, "w", encoding="utf-8") as f:
                json.dump(geojson_data, f, ensure_ascii=False, indent=2)

            if PUBLIC_DATA_DIR.exists():
                try:
                    with open(PUBLIC_DATA_DIR / "ebro_embalses.geojson", "w", encoding="utf-8") as f:
                        json.dump(geojson_data, f, ensure_ascii=False, indent=2)
                except Exception:
                    pass

        return self._embalses

    async def get_embalses(self) -> List[Dict[str, Any]]:
        if not self._last_embalses_sync_time or (datetime.now() - self._last_embalses_sync_time).total_seconds() >= SYNC_TTL_SECONDS:
            async with self._sync_lock:
                if not self._last_embalses_sync_time or (datetime.now() - self._last_embalses_sync_time).total_seconds() >= SYNC_TTL_SECONDS:
                    await asyncio.to_thread(self.sync_embalses_metadata)
        return self._embalses

    async def get_embalses_geojson(self) -> Dict[str, Any]:
        embalses = await self.get_embalses()
        return {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [e["lon"], e["lat"]]},
                    "properties": e,
                }
                for e in embalses
            ],
        }

    # ==========================================
    # 3. AFOROS Y CAUDALES (Modo Dual: 1 petición sin clave / 2 peticiones masivas con clave)
    # ==========================================

    def sync_aforos_metadata(self) -> List[Dict[str, Any]]:
        """
        Sincroniza los aforos de la CHE.
        - Si hay EBRO_API_KEY configurada: Hace 2 consultas masivas Open Data (QRIO y NRIO) para los 270 aforos.
        - Si no hay API Key: Hace 1 sola petición al mapa de aforos principales de toda la cuenca (HG).
        """
        if not self._metadata_by_code:
            self.sync_all_metadata()

        now_madrid = datetime.now(MADRID_TZ)
        now_iso = now_madrid.strftime("%Y-%m-%d %H:%M:%S")
        today_str = now_madrid.strftime("%Y-%m-%d")

        ctx = self._get_ssl_context()
        aforos_list = []

        # Descargar umbrales de aviso oficiales de la CHE
        umbrales_by_station = {}
        try:
            url_umb = f"{EBRO_BASE_URL}/api/info/getTablaUmbrales"
            req_umb = urllib.request.Request(
                url_umb,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "application/json, text/plain, */*",
                    "X-Requested-With": "XMLHttpRequest",
                },
            )
            with urllib.request.urlopen(req_umb, context=ctx, timeout=10) as resp_umb:
                raw_umb = json.loads(resp_umb.read().decode("utf-8"))
            if isinstance(raw_umb, list):
                for u in raw_umb:
                    cod_u = u.get("codigo", "")
                    m_code = re.match(r"^([A-Z][0-9]{3})", cod_u)
                    if m_code:
                        c_id = m_code.group(1)
                        col1 = _parse_num(u.get("col1"))
                        col2 = _parse_num(u.get("col2"))
                        col3 = _parse_num(u.get("col3"))
                        umbrales_by_station[c_id] = {
                            "amarillo": col1,
                            "naranja": col2,
                            "rojo": col3,
                            "aviso": col1,
                            "prealerta": col2,
                            "alerta": col3,
                        }
        except Exception as e:
            logger.warning(f"Error descargando tabla de umbrales de Ebro: {e}")

        # Opción B: Con API Key Open Data (2 peticiones: QRIO y NRIO)
        if self._api_key:
            try:
                url_q = f"{EBRO_BASE_URL}/datos/apiopendata?tipo_senal=QRIO&inicio={today_str}&apikey={self._api_key}"
                url_n = f"{EBRO_BASE_URL}/datos/apiopendata?tipo_senal=NRIO&inicio={today_str}&apikey={self._api_key}"
                req_q = urllib.request.Request(url_q, headers={"User-Agent": USER_AGENT})
                req_n = urllib.request.Request(url_n, headers={"User-Agent": USER_AGENT})

                with urllib.request.urlopen(req_q, context=ctx, timeout=15) as resp_q:
                    q_data = json.loads(resp_q.read().decode("utf-8"))
                with urllib.request.urlopen(req_n, context=ctx, timeout=15) as resp_n:
                    n_data = json.loads(resp_n.read().decode("utf-8"))

                # Indexar lecturas por código de estación (ej. A003)
                caudales_map = {}
                if isinstance(q_data, list):
                    for item in q_data:
                        tag = str(item.get("tag", item.get("senal", "")))
                        code_m = re.match(r"^([A-Z][0-9]{3})", tag)
                        if code_m:
                            code = code_m.group(1)
                            caudales_map[code] = {
                                "caudal": _parse_num(item.get("valor")),
                                "tag": tag,
                                "fecha": item.get("fecha", now_iso),
                            }

                niveles_map = {}
                if isinstance(n_data, list):
                    for item in n_data:
                        tag = str(item.get("tag", item.get("senal", "")))
                        code_m = re.match(r"^([A-Z][0-9]{3})", tag)
                        if code_m:
                            code = code_m.group(1)
                            niveles_map[code] = {
                                "nivel": _parse_num(item.get("valor")),
                                "tag": tag,
                                "fecha": item.get("fecha", now_iso),
                            }

                all_codes = set(caudales_map.keys()) | set(niveles_map.keys())
                for code in all_codes:
                    meta = self._metadata_by_code.get(code, {})
                    lat = meta.get("lat")
                    lon = meta.get("lon")
                    if lat is None or lon is None:
                        continue

                    q_info = caudales_map.get(code, {})
                    n_info = niveles_map.get(code, {})
                    caudal_val = q_info.get("caudal")
                    nivel_val = n_info.get("nivel")

                    clean_name = meta.get("nombre") or code
                    rio = meta.get("rio") or ""
                    umb = umbrales_by_station.get(code, {})

                    st_dict = {
                        "id_estacion": f"ebro_aforo_{code}",
                        "codigo": f"EBRO_{code}",
                        "codigo_corto": code,
                        "id_variable": f"ebro_aforo_{code}",
                        "tag_caudal": q_info.get("tag", f"{code}_QRIO"),
                        "tag_nivel": n_info.get("tag", f"{code}_NRIO"),
                        "nombre": f"{code} {clean_name}" if not clean_name.startswith(code) else clean_name,
                        "rio": rio,
                        "municipio": meta.get("poblacion", ""),
                        "provincia": meta.get("provincia", ""),
                        "subcuenca": "Cuenca Hidrográfica del Ebro",
                        "lat": lat,
                        "lon": lon,
                        "red": "EBRO",
                        "caudal": caudal_val,
                        "ultimo_caudal": caudal_val,
                        "caudal_actual": caudal_val,
                        "lastValue": caudal_val,
                        "nivel": nivel_val,
                        "ultimo_nivel": nivel_val,
                        "nivel_actual": nivel_val,
                        "umbrales": umb,
                        "aviso": umb.get("amarillo"),
                        "prealerta": umb.get("naranja"),
                        "alerta": umb.get("rojo"),
                        "tipo_umbral": "nivel",
                        "unidad_umbrales": "m",
                        "unidad_grafica": "m",
                        "ultima_hora": q_info.get("fecha") or n_info.get("fecha") or now_iso,
                        "fecha_comunicacion": now_iso,
                        "fuente": "SAIH Ebro (Confederación Hidrográfica del Ebro - CHE)",
                        "unidad": "m",
                        "unidad_nivel": "m",
                        "unidad_caudal": "m³/s",
                    }
                    aforos_list.append(st_dict)

            except Exception as e:
                logger.warning(f"Error consultando OpenData con API Key en Ebro: {e}. Pasando a modo público HG.")

        # Opción A: Modo Público Ligero (1 petición al mapa macro HG)
        if not aforos_list:
            try:
                url_hg = f"{EBRO_BASE_URL}/api/mapa/getDatosMapa?slug=mapa-aforos-HG-toda-la-cuenca"
                req_hg = urllib.request.Request(
                    url_hg,
                    headers={
                        "User-Agent": USER_AGENT,
                        "Accept": "application/json, text/plain, */*",
                        "X-Requested-With": "XMLHttpRequest",
                    },
                )
                with urllib.request.urlopen(req_hg, context=ctx, timeout=12) as resp:
                    mapa_data = json.loads(resp.read().decode("utf-8"))

                for item in mapa_data.get("DATOS", []):
                    code = str(item.get("CW_REMOTA_TXT", "")).strip()
                    if not code:
                        continue

                    meta = self._metadata_by_code.get(code, {})
                    lat = meta.get("lat")
                    lon = meta.get("lon")
                    if lat is None or lon is None:
                        continue

                    caudal_val = None
                    nivel_val = None
                    tag_caudal = None
                    tag_nivel = None
                    fecha_val = now_iso

                    for t in item.get("TAGS", []):
                        ts = t.get("LS_TIPO_SENAL")
                        val = _parse_num(t.get("VALOR"))
                        if ts == "QRIO":
                            caudal_val = val
                            tag_caudal = t.get("LS_TAG_TXT")
                            fecha_val = t.get("ULTIMA_FECHA", fecha_val)
                        elif ts == "NRIO":
                            nivel_val = val
                            tag_nivel = t.get("LS_TAG_TXT")
                            fecha_val = t.get("ULTIMA_FECHA", fecha_val)

                    clean_name = meta.get("nombre") or item.get("LR_NOMBRE_CORTO") or code
                    umb = umbrales_by_station.get(code, {})

                    st_dict = {
                        "id_estacion": f"ebro_aforo_{code}",
                        "codigo": f"EBRO_{code}",
                        "codigo_corto": code,
                        "id_variable": f"ebro_aforo_{code}",
                        "tag_caudal": tag_caudal or f"{code}_QRIO",
                        "tag_nivel": tag_nivel or f"{code}_NRIO",
                        "nombre": f"{code} {clean_name}" if not clean_name.startswith(code) else clean_name,
                        "rio": meta.get("rio", ""),
                        "municipio": meta.get("poblacion", ""),
                        "provincia": meta.get("provincia", ""),
                        "subcuenca": "Cuenca Hidrográfica del Ebro",
                        "lat": lat,
                        "lon": lon,
                        "red": "EBRO",
                        "caudal": caudal_val,
                        "ultimo_caudal": caudal_val,
                        "caudal_actual": caudal_val,
                        "lastValue": caudal_val,
                        "nivel": nivel_val,
                        "ultimo_nivel": nivel_val,
                        "nivel_actual": nivel_val,
                        "umbrales": umb,
                        "aviso": umb.get("amarillo"),
                        "prealerta": umb.get("naranja"),
                        "alerta": umb.get("rojo"),
                        "tipo_umbral": "nivel",
                        "unidad_umbrales": "m",
                        "unidad_grafica": "m",
                        "ultima_hora": fecha_val,
                        "fecha_comunicacion": now_iso,
                        "fuente": "SAIH Ebro (Confederación Hidrográfica del Ebro - CHE)",
                        "unidad": "m",
                        "unidad_nivel": "m",
                        "unidad_caudal": "m³/s",
                    }
                    aforos_list.append(st_dict)

            except Exception as e:
                logger.error(f"Error descargando aforos públicos HG de SAIH Ebro: {e}")

        if aforos_list:
            self._aforos = aforos_list
            self._index_aforos()
            self._last_aforos_sync_time = datetime.now()

            DATA_DIR.mkdir(parents=True, exist_ok=True)
            with open(STATIC_EBRO_AFOROS_FILE, "w", encoding="utf-8") as f:
                json.dump(self._aforos, f, ensure_ascii=False, indent=2)

            geojson_data = {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "geometry": {"type": "Point", "coordinates": [a["lon"], a["lat"]]},
                        "properties": a,
                    }
                    for a in self._aforos
                ],
            }
            with open(EBRO_AFOROS_GEOJSON_FILE, "w", encoding="utf-8") as f:
                json.dump(geojson_data, f, ensure_ascii=False, indent=2)

            if PUBLIC_DATA_DIR.exists():
                try:
                    with open(PUBLIC_DATA_DIR / "ebro_aforos.geojson", "w", encoding="utf-8") as f:
                        json.dump(geojson_data, f, ensure_ascii=False, indent=2)
                except Exception:
                    pass

        return self._aforos

    async def get_caudales(self) -> List[Dict[str, Any]]:
        if not self._last_aforos_sync_time or (datetime.now() - self._last_aforos_sync_time).total_seconds() >= SYNC_TTL_SECONDS:
            async with self._sync_lock:
                if not self._last_aforos_sync_time or (datetime.now() - self._last_aforos_sync_time).total_seconds() >= SYNC_TTL_SECONDS:
                    await asyncio.to_thread(self.sync_aforos_metadata)
        return self._aforos

    async def get_caudales_geojson(self) -> Dict[str, Any]:
        aforos = await self.get_caudales()
        return {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [a["lon"], a["lat"]]},
                    "properties": a,
                }
                for a in aforos
            ],
        }

    # ==========================================
    # 4. SERIES TEMPORALES
    # ==========================================

    def _fetch_series_with_key(self, tag: str, start_date_str: str) -> List[Dict[str, Any]]:
        """Descarga la serie temporal histórica mediante la API Open Data oficial."""
        if not self._api_key or not tag:
            return []

        ctx = self._get_ssl_context()
        url = f"{EBRO_BASE_URL}/datos/apiopendata?senal={urllib.parse.quote(tag)}&inicio={urllib.parse.quote(start_date_str)}&apikey={self._api_key}"
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})

        try:
            with urllib.request.urlopen(req, context=ctx, timeout=12) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            if isinstance(data, list):
                points = []
                for p in data:
                    v = _parse_num(p.get("valor"))
                    points.append({
                        "fecha": p.get("fecha", ""),
                        "valor": v,
                        "estado": 1,
                        "caudal": v,
                    })
                points.sort(key=lambda x: str(x.get("fecha") or ""))
                return points
        except Exception as e:
            logger.warning(f"Error descargando serie Open Data Ebro ({tag}): {e}")
        return []

    def _fetch_sparkline_series(self, tag: str, tipo_tag: str = "QRIO") -> List[Dict[str, Any]]:
        """
        Descarga la minigráfica quinceminutal de tendencia rápida (últimas 24h)
        desde la API pública sin necesidad de autenticación.
        """
        if not tag:
            return []

        ctx = self._get_ssl_context()
        url = f"{EBRO_BASE_URL}/api/ficha/getDatosMinigraficaSenal?tag={urllib.parse.quote(tag)}&tipoTag={urllib.parse.quote(tipo_tag)}"
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json, text/plain, */*",
                "X-Requested-With": "XMLHttpRequest",
            },
        )

        try:
            with urllib.request.urlopen(req, context=ctx, timeout=10) as resp:
                raw_list = json.loads(resp.read().decode("utf-8"))

            if isinstance(raw_list, list) and raw_list:
                now_madrid = datetime.now(MADRID_TZ)
                n_points = len(raw_list)
                points = []
                for i, val in enumerate(raw_list):
                    # Cada punto en la minigráfica representa un paso de 15 minutos
                    dt_point = now_madrid - timedelta(minutes=15 * (n_points - 1 - i))
                    iso_date = dt_point.strftime("%Y-%m-%d %H:%M:%S")
                    v = _parse_num(val)
                    points.append({
                        "fecha": iso_date,
                        "valor": v,
                        "estado": 1,
                        "caudal": v,
                    })
                points.sort(key=lambda x: str(x.get("fecha") or ""))
                return points
        except Exception as e:
            logger.warning(f"Error descargando minigráfica Ebro ({tag}): {e}")
        return []

    def _resolve_station_tags(self, code: str, is_embalse: bool = False) -> Dict[str, str]:
        """Consulta los tags reales de las señales de la estación en procesarTablaValoresActuales."""
        tags = {}
        ctx = self._get_ssl_context()
        url = f"{EBRO_BASE_URL}/api/ficha/procesarTablaValoresActuales?estacion={code}"
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json, text/plain, */*",
                "X-Requested-With": "XMLHttpRequest",
            },
        )
        try:
            with urllib.request.urlopen(req, context=ctx, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            html = data.get("VALORES_ACTUALES", "")
            for m in re.finditer(r"tag=([A-Z0-9]+)&(?:amp;)?tipoTag=([A-Z0-9]+)", html):
                tag_id = m.group(1)
                tipo = m.group(2)
                tags[tipo] = tag_id
        except Exception as e:
            logger.warning(f"Error resolviendo tags de estación {code}: {e}")
        return tags

    async def get_history(
        self,
        id_or_code: str,
        hours: int = 24,
        is_embalse: bool = False,
        variable_type: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Consulta la serie temporal para una estación de caudal o embalse de SAIH Ebro.
        Por defecto en aforos consulta el Nivel (NRIO, en metros), coincidiendo con los umbrales de peligro oficiales.
        """
        clean_id = (
            str(id_or_code)
            .replace("ebro_aforo_", "")
            .replace("ebro_emb_", "")
            .replace("ebro_pluv_", "")
            .replace("EBRO_", "")
            .strip()
        )
        station_obj = None
        tag = None
        tipo_tag = "VEMBA" if is_embalse else "NRIO"

        if is_embalse:
            await self.get_embalses()
            station_obj = self._embalses_by_id.get(clean_id) or self._embalses_by_id.get(clean_id.upper())
            if variable_type == "cota":
                tipo_tag = "NEMBA"
            elif variable_type == "porcentaje":
                tipo_tag = "PORCE"
            else:
                tipo_tag = "VEMBA"
        else:
            await self.get_caudales()
            station_obj = self._aforos_by_id.get(clean_id) or self._aforos_by_id.get(clean_id.upper())
            if variable_type == "caudal":
                tipo_tag = "QRIO"
                if station_obj:
                    tag = station_obj.get("tag_caudal")
            else:
                tipo_tag = "NRIO"
                if station_obj:
                    tag = station_obj.get("tag_nivel") or station_obj.get("tag_caudal")

        # Si no tenemos el tag exacto, resolverlo dinámicamente
        if not tag:
            tags_map = await asyncio.to_thread(self._resolve_station_tags, clean_id, is_embalse)
            if is_embalse:
                tag = tags_map.get("VEMBA") or tags_map.get("NEMBA") or tags_map.get("PORCE")
                if "VEMBA" in tags_map:
                    tipo_tag = "VEMBA"
                elif "NEMBA" in tags_map:
                    tipo_tag = "NEMBA"
            else:
                if tipo_tag == "QRIO":
                    tag = tags_map.get("QRIO") or tags_map.get("NRIO")
                    if "QRIO" in tags_map:
                        tipo_tag = "QRIO"
                    elif "NRIO" in tags_map:
                        tipo_tag = "NRIO"
                else:
                    tag = tags_map.get("NRIO") or tags_map.get("QRIO")
                    if "NRIO" in tags_map:
                        tipo_tag = "NRIO"
                    elif "QRIO" in tags_map:
                        tipo_tag = "QRIO"

        now = datetime.now(MADRID_TZ)
        start_dt = now - timedelta(hours=int(hours) if isinstance(hours, (int, float)) else 24)
        start_date_str = start_dt.strftime("%Y-%m-%d")

        points = []
        if self._api_key and tag:
            points = await asyncio.to_thread(self._fetch_series_with_key, tag, start_date_str)

        if not points and tag:
            points = await asyncio.to_thread(self._fetch_sparkline_series, tag, tipo_tag)

        # Ajustar nombres de campos según sea embalse o caudal/nivel
        unit_str = "hm³" if is_embalse else ("m³/s" if tipo_tag == "QRIO" else "m")
        var_type_str = "volumen" if is_embalse else ("caudal" if tipo_tag == "QRIO" else "nivel")

        if is_embalse:
            for p in points:
                p["volumen"] = p.get("valor")
        else:
            if tipo_tag == "QRIO":
                for p in points:
                    p["caudal"] = p.get("valor")
            else:
                for p in points:
                    p["nivel"] = p.get("valor")

        return {
            "id_variable": str(id_or_code),
            "estacion": station_obj,
            "tipo_variable": var_type_str,
            "unidad": unit_str,
            "rango": {
                "desde": start_dt.strftime("%Y-%m-%d %H:%M:%S"),
                "hasta": now.strftime("%Y-%m-%d %H:%M:%S"),
                "horas": hours,
            },
            "puntos_totales": len(points),
            "serie": points,
        }


ebro_service = EbroService()
