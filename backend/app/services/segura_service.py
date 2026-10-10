"""
Servicio de sincronización y consulta de datos del S.A.I.H. Segura (Confederación Hidrográfica del Segura - CHS / MITECO).
Proporciona datos en tiempo real de pluviómetros (144 estaciones), aforos en ríos/canales (134 estaciones)
y embalses (27 embalses) en las provincias de Murcia, Alicante, Albacete, Jaén, Almería y Granada.
"""

import asyncio
import concurrent.futures
import json
import logging
import re
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
from zoneinfo import ZoneInfo

from pyproj import Transformer

logger = logging.getLogger("rainloc.segura_service")

MADRID_TZ = ZoneInfo("Europe/Madrid")
SYNC_TTL_SECONDS = 300  # 5 minutos de caché

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 RainLoc/3.0"

# ArcGIS REST endpoints oficiales CHS
ARCGIS_PLUVIOS_URL = "https://www.chsegura.es/arcgis/rest/services/DashboardServices/DashDatosBase/MapServer/3/query?where=1%3D1&outFields=*&f=json"
ARCGIS_AFOROS_Q_URL = "https://www.chsegura.es/arcgis/rest/services/VISOR_CHSIC3/VISOR_PUBLICO_ETRS89_v5_Web_Capas_2025/MapServer/10/query?where=1%3D1&outFields=*&f=json"
ARCGIS_AFOROS_N_URL = "https://www.chsegura.es/arcgis/rest/services/VISOR_CHSIC3/VISOR_PUBLICO_ETRS89_v5_Web_Capas_2025/MapServer/11/query?where=1%3D1&outFields=*&f=json"
ARCGIS_EMBALSES_V_URL = "https://www.chsegura.es/arcgis/rest/services/VISOR_CHSIC3/VISOR_PUBLICO_ETRS89_v5_Web_Capas_2025/MapServer/8/query?where=1%3D1&outFields=*&f=json"
ARCGIS_EMBALSES_N_URL = "https://www.chsegura.es/arcgis/rest/services/VISOR_CHSIC3/VISOR_PUBLICO_ETRS89_v5_Web_Capas_2025/MapServer/9/query?where=1%3D1&outFields=*&f=json"

# SAIH Segura Web iVisor endpoints
SAIH_CAUCES_LIVE_URL = "http://saihweb.chsegura.es/apps/ivisor/cauces3.php"
SAIH_EMBALSES_LIVE_URL = "http://saihweb.chsegura.es/apps/ivisor/embalses3.php"
SAIH_CHART_URL = "http://saihweb.chsegura.es/apps/ivisor/graficas/graficaVar.php"

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
PUBLIC_DATA_DIR = Path(__file__).resolve().parent.parent.parent.parent / "public" / "data"

STATIC_SEGURA_PLUVIOS_FILE = DATA_DIR / "segura_lluvias_estaciones.json"
STATIC_SEGURA_AFOROS_FILE = DATA_DIR / "segura_aforos_estaciones.json"
STATIC_SEGURA_EMBALSES_FILE = DATA_DIR / "segura_embalses_estaciones.json"

SEGURA_PLUVIOS_GEOJSON_FILE = DATA_DIR / "segura_lluvias.geojson"
SEGURA_AFOROS_GEOJSON_FILE = DATA_DIR / "segura_aforos.geojson"
SEGURA_EMBALSES_GEOJSON_FILE = DATA_DIR / "segura_embalses.geojson"
SEGURA_CUENCAS_GEOJSON_FILE = DATA_DIR / "segura_subcuencas.geojson"

# Transformador de coordenadas EPSG:25830 (UTM 30N) a EPSG:4326 (WGS84 lon, lat)
transformer = Transformer.from_crs("EPSG:25830", "EPSG:4326", always_xy=True)

# Capacidades oficiales NMN (hm³) de los 27 embalses de la cuenca del Segura
CAPACIDADES_NMN_SEGURA: Dict[str, float] = {
    "01E01": 7.3,    # La Cierva
    "01E02": 26.3,   # Santomera
    "01E03": 0.6,    # Pliego
    "01E04": 0.6,    # Doña Ana
    "01E05": 44.6,   # Algeciras
    "01E06": 11.2,   # José Bautista
    "01E07": 15.0,   # Los Rodeos
    "02E01": 1.5,    # Mayés
    "02E02": 10.0,   # Argos
    "02E03": 21.6,   # Alfonso XIII
    "02E04": 4.0,    # Moro
    "02E05": 29.0,   # Judío
    "02E06": 0.5,    # Cárcabo
    "02E07": 3.2,    # La Risca
    "02E08": 6.0,    # Moratalla
    "02S01": 3.0,    # Ojós
    "03E02": 34.8,   # Talave
    "03E03": 35.8,   # Camarillas
    "03E04": 1.0,    # Los Charcos
    "03E05": 0.5,    # Bayco / Bayovar
    "03E06": 0.5,    # Boquerón
    "04S02": 210.0,  # Fuensanta
    "04S03": 437.0,  # Cenajo
    "05E02": 13.0,   # Valdeinfierno
    "05E03": 26.0,   # Puentes
    "07E01": 246.0,  # La Pedrera
    "07E02": 12.8,   # Crevillente
}


def _point_line_distance(point, start, end):
    if start == end:
        return ((point[0] - start[0]) ** 2 + (point[1] - start[1]) ** 2) ** 0.5
    n = abs((end[1] - start[1]) * point[0] - (end[0] - start[0]) * point[1] + end[0] * start[1] - end[1] * start[0])
    d = ((end[1] - start[1]) ** 2 + (end[0] - start[0]) ** 2) ** 0.5
    return n / d if d > 0 else 0.0


def _ramer_douglas_peucker(points, tolerance=0.0008):
    if len(points) <= 2:
        return points
    dmax = 0.0
    index = 0
    for i in range(1, len(points) - 1):
        d = _point_line_distance(points[i], points[0], points[-1])
        if d > dmax:
            index = i
            dmax = d
    if dmax > tolerance:
        rec1 = _ramer_douglas_peucker(points[:index + 1], tolerance)
        rec2 = _ramer_douglas_peucker(points[index:], tolerance)
        return rec1[:-1] + rec2
    else:
        return [points[0], points[-1]]


def _simplify_geom(geom, tolerance=0.0008):
    if not geom:
        return None
    g_type = geom.get("type")
    coords = geom.get("coordinates", [])
    if g_type == "Polygon":
        new_rings = []
        for ring in coords:
            r = [[round(p[0], 5), round(p[1], 5)] for p in ring]
            if len(r) > 6:
                s = _ramer_douglas_peucker(r[:-1], tolerance)
                s.append(s[0])
                new_rings.append(s)
            else:
                new_rings.append(r)
        return {"type": "Polygon", "coordinates": new_rings}
    elif g_type == "MultiPolygon":
        new_polys = []
        for poly in coords:
            new_rings = []
            for ring in poly:
                r = [[round(p[0], 5), round(p[1], 5)] for p in ring]
                if len(r) > 6:
                    s = _ramer_douglas_peucker(r[:-1], tolerance)
                    s.append(s[0])
                    new_rings.append(s)
                else:
                    new_rings.append(r)
            new_polys.append(new_rings)
        return {"type": "MultiPolygon", "coordinates": new_polys}
    return geom


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


class SeguraService:
    def __init__(self):
        self._pluvios: List[Dict[str, Any]] = []
        self._aforos: List[Dict[str, Any]] = []
        self._embalses: List[Dict[str, Any]] = []

        self._pluvios_by_id: Dict[str, Dict[str, Any]] = {}
        self._aforos_by_id: Dict[str, Dict[str, Any]] = {}
        self._embalses_by_id: Dict[str, Dict[str, Any]] = {}

        # Buffer histórico en memoria de incrementos 5 min para pluviómetros: {cod: [(datetime, mm)]}
        self._pluvio_rolling_buffer: Dict[str, List[tuple]] = {}

        self._last_pluvios_sync_time: Optional[datetime] = None
        self._last_aforos_sync_time: Optional[datetime] = None
        self._last_embalses_sync_time: Optional[datetime] = None

        self._sync_lock = asyncio.Lock()
        self._cuencas_lock = asyncio.Lock()
        self._cuencas_geojson_cache: Optional[Dict[str, Any]] = None
        self._load_cached_files()

    def _load_cached_files(self):
        if STATIC_SEGURA_PLUVIOS_FILE.exists():
            try:
                with open(STATIC_SEGURA_PLUVIOS_FILE, "r", encoding="utf-8") as f:
                    self._pluvios = json.load(f)
                    self._index_pluvios()
            except Exception as e:
                logger.warning(f"Error cargando pluviómetros guardados de Segura: {e}")

        if STATIC_SEGURA_AFOROS_FILE.exists():
            try:
                with open(STATIC_SEGURA_AFOROS_FILE, "r", encoding="utf-8") as f:
                    self._aforos = json.load(f)
                    self._index_aforos()
            except Exception as e:
                logger.warning(f"Error cargando aforos guardados de Segura: {e}")

        if STATIC_SEGURA_EMBALSES_FILE.exists():
            try:
                with open(STATIC_SEGURA_EMBALSES_FILE, "r", encoding="utf-8") as f:
                    self._embalses = json.load(f)
                    self._index_embalses()
            except Exception as e:
                logger.warning(f"Error cargando embalses guardados de Segura: {e}")

    def _index_pluvios(self):
        idx = {}
        for p in self._pluvios:
            if p.get("id_estacion"):
                idx[str(p["id_estacion"])] = p
            if p.get("codigo"):
                idx[str(p["codigo"]).upper()] = p
                idx[str(p["codigo"])] = p
            if p.get("codigo_variable"):
                idx[str(p["codigo_variable"]).upper()] = p
                idx[str(p["codigo_variable"])] = p
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
            if a.get("tag_caudal"):
                idx[str(a["tag_caudal"]).upper()] = a
                idx[str(a["tag_caudal"])] = a
            if a.get("tag_nivel"):
                idx[str(a["tag_nivel"]).upper()] = a
                idx[str(a["tag_nivel"])] = a
        self._aforos_by_id = idx

    def _index_embalses(self):
        idx = {}
        for e in self._embalses:
            if e.get("id_estacion"):
                idx[str(e["id_estacion"])] = e
            if e.get("codigo"):
                idx[str(e["codigo"]).upper()] = e
                idx[str(e["codigo"])] = e
            if e.get("id_volumen"):
                idx[str(e["id_volumen"]).upper()] = e
                idx[str(e["id_volumen"])] = e
            if e.get("id_cota"):
                idx[str(e["id_cota"]).upper()] = e
                idx[str(e["id_cota"])] = e
        self._embalses_by_id = idx

    def _fetch_single_variable_latest(self, code: str) -> Tuple[str, Optional[float], Optional[str]]:
        """Consulta el último valor no nulo de una variable en saihweb.chsegura.es/apps/ivisor/graficas/graficaVar.php."""
        if not code:
            return code, None, None
        now = datetime.now(MADRID_TZ)
        dt_start = now - timedelta(hours=8)
        dfrom_str = dt_start.strftime("%d/%m/%Y %H:%M")
        dto_str = now.strftime("%d/%m/%Y %H:%M")
        url = (
            f"{SAIH_CHART_URL}?puntos={urllib.parse.quote(code)}"
            f"&dfrom={urllib.parse.quote(dfrom_str)}&dto={urllib.parse.quote(dto_str)}&source=I"
        )
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=8) as resp:
                content = resp.read().decode("utf-8", errors="ignore")
            matches = re.findall(r"x:(\d+),\s*y:([-\d\.]+|null)", content)
            valid = [(int(x), float(y)) for x, y in matches if y != "null"]
            if valid:
                last_ts, last_val = valid[-1]
                dt_str = datetime.fromtimestamp(last_ts / 1000.0, tz=MADRID_TZ).strftime("%Y-%m-%d %H:%M:%S")
                return code, round(last_val, 3), dt_str
        except Exception:
            pass
        return code, None, None

    def _fetch_variables_telemetry_parallel(self, codes: Set[str], max_workers: int = 15) -> Dict[str, Tuple[Optional[float], Optional[str]]]:
        """Consulta en paralelo el último valor no nulo de un conjunto de variables."""
        if not codes:
            return {}
        results: Dict[str, Tuple[Optional[float], Optional[str]]] = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_code = {executor.submit(self._fetch_single_variable_latest, c): c for c in codes if c}
            for future in concurrent.futures.as_completed(future_to_code):
                try:
                    c_res, val_res, dt_res = future.result()
                    if val_res is not None:
                        results[c_res] = (val_res, dt_res)
                except Exception:
                    pass
        return results

    # ==========================================
    # 1. PLUVIÓMETROS (CHS)
    # ==========================================

    def sync_pluvios(self) -> List[Dict[str, Any]]:
        """
        Descarga síncrona de los pluviómetros en tiempo real desde el servicio ArcGIS de la CHS.
        Calcula lluvia acumulada continua en 1h, 4h, 12h y 24h.
        """
        logger.info("Sincronizando pluviómetros SAIH Segura desde ArcGIS REST...")
        now = datetime.now(MADRID_TZ)
        now_iso = now.strftime("%Y-%m-%d %H:%M:%S")

        req = urllib.request.Request(ARCGIS_PLUVIOS_URL, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            features_raw = data.get("features", [])
            if not features_raw:
                logger.warning("No se recibieron pluviómetros de la CHS.")
                return self._pluvios

            pluvios = []
            features = []

            for feat in features_raw:
                attrs = feat.get("attributes", {})
                geom = feat.get("geometry", {})

                cod_var = attrs.get("CodVariableHidrologica") or ""
                nombre = attrs.get("DenominacionPtoMedicion") or attrs.get("DenominacionVariable") or ""
                municipio = attrs.get("Municipio") or ""

                x = geom.get("x")
                y = geom.get("y")
                if not x or not y:
                    continue

                lon, lat = transformer.transform(float(x), float(y))

                lluvia_1h = _parse_num(attrs.get("LluviaUltimaHora"))
                lluvia_3h = _parse_num(attrs.get("LluviaUltimas3Horas"))
                lluvia_6h = _parse_num(attrs.get("LluviaUltimas6Horas"))
                lluvia_12h = _parse_num(attrs.get("LluviaUltimas12Horas"))
                lluvia_24h = _parse_num(attrs.get("LluviaUltimas24Horas"))

                # Extraer código de estación (ej. '06A16' de '06A16P01')
                cod_est = cod_var[:5] if len(cod_var) >= 5 else cod_var

                pluv_obj = {
                    "id_estacion": f"segura_pluv_{cod_var.lower()}",
                    "id_variable": cod_var,
                    "codigo": cod_est,
                    "codigo_variable": cod_var,
                    "nombre": nombre,
                    "tipo": "Pluviómetro",
                    "red": "CHS",
                    "cuenca": "Segura",
                    "lat": round(lat, 6),
                    "lon": round(lon, 6),
                    "poblacion": municipio,
                    "municipio": municipio,
                    "provincia": "Murcia / Albacete / Alicante",
                    "subcuenca": "Segura",
                    "estado": True,
                    "lluvia_1h": lluvia_1h if lluvia_1h is not None else 0.0,
                    "precipitacion_1h": lluvia_1h if lluvia_1h is not None else 0.0,
                    "fecha_1h": now_iso,
                    "lluvia_3h": lluvia_3h,
                    "lluvia_4h": None,
                    "precipitacion_4h": None,
                    "fecha_4h": now_iso,
                    "lluvia_6h": lluvia_6h,
                    "lluvia_12h": lluvia_12h,
                    "precipitacion_12h": lluvia_12h,
                    "fecha_12h": now_iso,
                    "lluvia_24h": lluvia_24h if lluvia_24h is not None else 0.0,
                    "precipitacion_24h": lluvia_24h if lluvia_24h is not None else 0.0,
                    "fecha_24h": now_iso,
                    "ultima_hora": now_iso,
                    "fuente": "S.A.I.H. Segura (CHS / MITECO)",
                    "unidad": "mm",
                }

                pluvios.append(pluv_obj)
                features.append({
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [round(lon, 6), round(lat, 6)]},
                    "properties": pluv_obj,
                })

            geojson = {"type": "FeatureCollection", "features": features}

            DATA_DIR.mkdir(parents=True, exist_ok=True)
            with open(STATIC_SEGURA_PLUVIOS_FILE, "w", encoding="utf-8") as f:
                json.dump(pluvios, f, ensure_ascii=False, indent=2)

            with open(SEGURA_PLUVIOS_GEOJSON_FILE, "w", encoding="utf-8") as f:
                json.dump(geojson, f, ensure_ascii=False, indent=2)

            if PUBLIC_DATA_DIR.exists():
                with open(PUBLIC_DATA_DIR / "segura_lluvias.geojson", "w", encoding="utf-8") as f:
                    json.dump(geojson, f, ensure_ascii=False, indent=2)

            self._pluvios = pluvios
            self._index_pluvios()
            self._last_pluvios_sync_time = datetime.now()
            logger.info(f"Sincronizados {len(pluvios)} pluviómetros de SAIH Segura.")
            return pluvios

        except Exception as e:
            logger.error(f"Error al sincronizar pluviómetros CHS: {e}")
            return self._pluvios

    # ==========================================
    # 2. AFOROS (CAUDALES Y NIVELES CHS)
    # ==========================================

    def sync_aforos(self) -> List[Dict[str, Any]]:
        """
        Sincroniza los aforos de ríos y canales combinando los metadatos de ArcGIS
        (Layer 10 caudal, Layer 11 nivel) con las lecturas en tiempo real de saihweb.chsegura.es/cauces3.php
        y telemetría continua de graficaVar.php.
        """
        logger.info("Sincronizando aforos SAIH Segura...")
        now = datetime.now(MADRID_TZ)
        now_iso = now.strftime("%Y-%m-%d %H:%M:%S")

        # 1. Obtener metadatos geoespaciales de ArcGIS (Layer 10 Caudal y Layer 11 Nivel)
        geo_meta_by_code: Dict[str, Dict[str, Any]] = {}

        try:
            req_q = urllib.request.Request(ARCGIS_AFOROS_Q_URL, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req_q, timeout=12) as resp:
                q_data = json.loads(resp.read().decode("utf-8"))
                for feat in q_data.get("features", []):
                    attrs = feat.get("attributes", {})
                    geom = feat.get("geometry", {})
                    cod_pto = (attrs.get("CodPuntoMedicion") or "").strip()
                    cod_clean = cod_pto[:5] if len(cod_pto) >= 5 else cod_pto
                    x = geom.get("x") or attrs.get("X_ETRS89")
                    y = geom.get("y") or attrs.get("Y_ETRS89")
                    if x and y:
                        lon, lat = transformer.transform(float(x), float(y))
                        geo_meta_by_code[cod_clean] = {
                            "lon": round(lon, 6),
                            "lat": round(lat, 6),
                            "nombre": attrs.get("DenominacionPtoMedicion", ""),
                            "cod_caudal": attrs.get("CodVariableHidrologica", ""),
                            "z_ed50": attrs.get("Z_ED50"),
                        }
        except Exception as e:
            logger.warning(f"Error obteniendo metadatos de aforos caudal ArcGIS: {e}")

        # Nivel (Layer 11) para complementar tags de nivel
        try:
            req_n = urllib.request.Request(ARCGIS_AFOROS_N_URL, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req_n, timeout=12) as resp:
                n_data = json.loads(resp.read().decode("utf-8"))
                for feat in n_data.get("features", []):
                    attrs = feat.get("attributes", {})
                    geom = feat.get("geometry", {})
                    cod_pto = (attrs.get("CodPuntoMedicion") or "").strip()
                    cod_clean = cod_pto[:5] if len(cod_pto) >= 5 else cod_pto
                    cod_nivel = attrs.get("CodVariableHidrologica", "")
                    if cod_clean in geo_meta_by_code:
                        geo_meta_by_code[cod_clean]["cod_nivel"] = cod_nivel
                    else:
                        x = geom.get("x") or attrs.get("X_ETRS89")
                        y = geom.get("y") or attrs.get("Y_ETRS89")
                        if x and y:
                            lon, lat = transformer.transform(float(x), float(y))
                            geo_meta_by_code[cod_clean] = {
                                "lon": round(lon, 6),
                                "lat": round(lat, 6),
                                "nombre": attrs.get("DenominacionPtoMedicion", ""),
                                "cod_nivel": cod_nivel,
                                "z_ed50": attrs.get("Z_ED50"),
                            }
        except Exception as e:
            logger.warning(f"Error obteniendo metadatos de aforos nivel ArcGIS: {e}")

        # 2. Descargar lecturas en tiempo real de cauces3.php (tipo=0 cauces, tipo=1 canales, tipo=2 acequias)
        rows = []
        pattern_divs = r"<div[^>]*><a\s+title='([^']*)'\s+href=[^>]*set_punto\('([^']+)'\)[^>]*>&nbsp;([^<]+)</a></div>\s*<div[^>]*title='([^']*)'[^>]*>([^<]*)</div>\s*<div[^>]*title='([^']*)'[^>]*>([^<]*)</div>"
        pattern_links = r"title='([^']+)'\s+href=[^>]*set_punto\('([^']+)'\)[^>]*>&nbsp;([^<]+)</a></div>\s*<div[^>]*>\s*<a[^>]*title='([^']*)'[^>]*>([^<]*)</a></div>\s*<div[^>]*>\s*<a[^>]*title='([^']*)'[^>]*>([^<]*)</a>"

        for t in [0, 1, 2]:
            try:
                url_t = f"{SAIH_CAUCES_LIVE_URL}?tipo={t}"
                req_live = urllib.request.Request(url_t, headers={"User-Agent": USER_AGENT})
                with urllib.request.urlopen(req_live, timeout=10) as resp:
                    html = resp.read().decode("utf-8", errors="ignore")
                parsed = re.findall(pattern_divs, html) or re.findall(pattern_links, html)
                rows.extend(parsed)
            except Exception as err:
                logger.warning(f"Error descargando cauces3.php?tipo={t}: {err}")

        # Si cauces3.php no devolvió valores numéricos, recolectar códigos para consultar telemetría
        telemetry_codes_to_fetch = set()
        for r in rows:
            title_codes, punto_code, raw_name, tag_nivel, val_nivel_str, tag_caudal, val_caudal_str = r
            if _parse_num(val_caudal_str) is None and tag_caudal:
                telemetry_codes_to_fetch.add(tag_caudal.strip())
            if _parse_num(val_nivel_str) is None and tag_nivel:
                telemetry_codes_to_fetch.add(tag_nivel.strip())

        # Consultar telemetría en paralelo para las variables principales
        telemetry_map = self._fetch_variables_telemetry_parallel(telemetry_codes_to_fetch, max_workers=12)

        aforos = []
        features = []
        processed_codes = set()

        for r in rows:
            title_codes, punto_code, raw_name, tag_nivel, val_nivel_str, tag_caudal, val_caudal_str = r
            cod_clean = punto_code.strip()
            processed_codes.add(cod_clean)

            val_nivel = _parse_num(val_nivel_str)
            val_caudal = _parse_num(val_caudal_str)

            # Fallback a telemetría si la tabla devolvió '-'
            dt_reading = now_iso
            if val_caudal is None and tag_caudal and tag_caudal in telemetry_map:
                val_caudal, dt_reading = telemetry_map[tag_caudal]
            if val_nivel is None and tag_nivel and tag_nivel in telemetry_map:
                val_nivel, dt_reading = telemetry_map[tag_nivel]

            # Fallback a caché previo si no se pudo obtener nuevo valor
            if val_caudal is None or val_nivel is None:
                prev = self._aforos_by_id.get(cod_clean) or self._aforos_by_id.get(f"segura_aforo_{cod_clean.lower()}")
                if prev:
                    if val_caudal is None and prev.get("caudal") is not None:
                        val_caudal = prev.get("caudal")
                    if val_nivel is None and prev.get("nivel") is not None:
                        val_nivel = prev.get("nivel")

            # Extraer cota máxima de aviso si viene entre paréntesis en el nombre: ej. "A.Las Juntas(5,62)"
            cota_max = None
            name_clean = raw_name.strip()
            match_cota = re.search(r"\(([\d,.]+)\)", raw_name)
            if match_cota:
                cota_max = _parse_num(match_cota.group(1))
                name_clean = re.sub(r"\s*\([\d,.]+\)", "", raw_name).strip()

            if name_clean.startswith("A."):
                name_clean = f"Aforo en {name_clean[2:].strip()}"
            elif name_clean.startswith("AgAb."):
                name_clean = f"Aguas Abajo de {name_clean[5:].strip()}"

            meta = geo_meta_by_code.get(cod_clean, {})
            lon = meta.get("lon")
            lat = meta.get("lat")
            if lon is None or lat is None:
                continue

            full_name = meta.get("nombre") or name_clean
            id_variable = tag_caudal or meta.get("cod_caudal") or tag_nivel or f"segura_aforo_{cod_clean.lower()}"

            # Umbrales
            umbrales = {}
            if cota_max and cota_max > 0:
                umbrales = {
                    "amarillo": round(cota_max * 0.65, 2),
                    "naranja": round(cota_max * 0.80, 2),
                    "rojo": round(cota_max, 2),
                }

            aforo_obj = {
                "id_variable": id_variable,
                "id_estacion": f"segura_aforo_{cod_clean.lower()}",
                "codigo": cod_clean,
                "nombre": full_name,
                "variable": "Caudal y Nivel",
                "tipo": "Aforo",
                "red": "CHS",
                "cuenca": "Segura",
                "lat": lat,
                "lon": lon,
                "poblacion": "",
                "provincia": "Murcia / Albacete / Alicante",
                "subcuenca": "Segura",
                "ultimo_caudal": val_caudal,
                "caudal": val_caudal,
                "ultimo_nivel": val_nivel,
                "nivel": val_nivel,
                "cota_max": cota_max,
                "tipo_umbral": "nivel",
                "unidad_umbrales": "m",
                "unidad_grafica": "m",
                "tag_caudal": tag_caudal or meta.get("cod_caudal") or "",
                "tag_nivel": tag_nivel or meta.get("cod_nivel") or "",
                "ultima_hora": dt_reading or now_iso,
                "fecha_comunicacion": dt_reading or now_iso,
                "umbrales": umbrales,
                "unidad": "m",
                "unidad_nivel": "m",
                "unidad_caudal": "m³/s",
            }

            aforos.append(aforo_obj)
            features.append({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [round(lon, 6), round(lat, 6)]},
                "properties": aforo_obj,
            })

        # Añadir aforos restantes de ArcGIS que no figuren en la tabla resumen inmediata
        for cod, meta in geo_meta_by_code.items():
            if cod not in processed_codes:
                lon = meta.get("lon")
                lat = meta.get("lat")
                if lon is None or lat is None:
                    continue
                cod_q = meta.get("cod_caudal") or ""
                cod_n = meta.get("cod_nivel") or ""
                id_var = cod_q or cod_n or f"segura_aforo_{cod.lower()}"

                # Intentar leer telemetría o caché
                val_caudal = None
                val_nivel = None
                dt_reading = now_iso
                if cod_q and cod_q in telemetry_map:
                    val_caudal, dt_reading = telemetry_map[cod_q]
                if cod_n and cod_n in telemetry_map:
                    val_nivel, dt_reading = telemetry_map[cod_n]

                if val_caudal is None or val_nivel is None:
                    prev = self._aforos_by_id.get(cod) or self._aforos_by_id.get(f"segura_aforo_{cod.lower()}")
                    if prev:
                        if val_caudal is None and prev.get("caudal") is not None:
                            val_caudal = prev.get("caudal")
                        if val_nivel is None and prev.get("nivel") is not None:
                            val_nivel = prev.get("nivel")

                aforo_obj = {
                    "id_variable": id_var,
                    "id_estacion": f"segura_aforo_{cod.lower()}",
                    "codigo": cod,
                    "nombre": meta.get("nombre") or f"Aforo {cod}",
                    "variable": "Caudal",
                    "tipo": "Aforo",
                    "red": "CHS",
                    "cuenca": "Segura",
                    "lat": lat,
                    "lon": lon,
                    "poblacion": "",
                    "provincia": "Murcia / Albacete / Alicante",
                    "subcuenca": "Segura",
                    "ultimo_caudal": val_caudal,
                    "caudal": val_caudal,
                    "ultimo_nivel": val_nivel,
                    "nivel": val_nivel,
                    "cota_max": None,
                    "tag_caudal": cod_q,
                    "tag_nivel": cod_n,
                    "ultima_hora": dt_reading,
                    "fecha_comunicacion": dt_reading,
                    "umbrales": {},
                    "unidad": "m³/s",
                    "unidad_nivel": "m",
                }
                aforos.append(aforo_obj)
                features.append({
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [round(lon, 6), round(lat, 6)]},
                    "properties": aforo_obj,
                })

        geojson = {"type": "FeatureCollection", "features": features}

        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(STATIC_SEGURA_AFOROS_FILE, "w", encoding="utf-8") as f:
            json.dump(aforos, f, ensure_ascii=False, indent=2)

        with open(SEGURA_AFOROS_GEOJSON_FILE, "w", encoding="utf-8") as f:
            json.dump(geojson, f, ensure_ascii=False, indent=2)

        if PUBLIC_DATA_DIR.exists():
            with open(PUBLIC_DATA_DIR / "segura_aforos.geojson", "w", encoding="utf-8") as f:
                json.dump(geojson, f, ensure_ascii=False, indent=2)

        self._aforos = aforos
        self._index_aforos()
        self._last_aforos_sync_time = datetime.now()
        logger.info(f"Sincronizados {len(aforos)} aforos de SAIH Segura.")
        return aforos

    # ==========================================
    # 3. EMBALSES (PRENDAS Y CAPACIDADES CHS)
    # ==========================================

    def sync_embalses(self) -> List[Dict[str, Any]]:
        """
        Sincroniza los 27 embalses de la cuenca del Segura combinando ArcGIS
        (Layer 8 volumen, Layer 9 nivel) con saihweb.chsegura.es/embalses3.php
        y telemetría continua de graficaVar.php.
        """
        logger.info("Sincronizando embalses SAIH Segura...")
        now = datetime.now(MADRID_TZ)
        now_iso = now.strftime("%Y-%m-%d %H:%M:%S")

        # 1. Metadatos geoespaciales de ArcGIS (Layer 8 Volumen y Layer 9 Nivel)
        emb_geo_meta: Dict[str, Dict[str, Any]] = {}

        try:
            req_v = urllib.request.Request(ARCGIS_EMBALSES_V_URL, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req_v, timeout=12) as resp:
                v_data = json.loads(resp.read().decode("utf-8"))
                for feat in v_data.get("features", []):
                    attrs = feat.get("attributes", {})
                    geom = feat.get("geometry", {})
                    cod_pto = (attrs.get("CodPuntoMedicion") or "").strip()
                    cod_clean = cod_pto[:5] if len(cod_pto) >= 5 else cod_pto
                    x = geom.get("x") or attrs.get("X_ETRS89")
                    y = geom.get("y") or attrs.get("Y_ETRS89")
                    if x and y:
                        lon, lat = transformer.transform(float(x), float(y))
                        emb_geo_meta[cod_clean] = {
                            "lon": round(lon, 6),
                            "lat": round(lat, 6),
                            "nombre": attrs.get("DenominacionPtoMedicion", ""),
                            "cod_volumen": attrs.get("CodVariableHidrologica", ""),
                            "z_ed50": attrs.get("Z_ED50"),
                        }
        except Exception as e:
            logger.warning(f"Error obteniendo metadatos de embalses volumen ArcGIS: {e}")

        try:
            req_n = urllib.request.Request(ARCGIS_EMBALSES_N_URL, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req_n, timeout=12) as resp:
                n_data = json.loads(resp.read().decode("utf-8"))
                for feat in n_data.get("features", []):
                    attrs = feat.get("attributes", {})
                    cod_pto = (attrs.get("CodPuntoMedicion") or "").strip()
                    cod_clean = cod_pto[:5] if len(cod_pto) >= 5 else cod_pto
                    cod_cota = attrs.get("CodVariableHidrologica", "")
                    if cod_clean in emb_geo_meta:
                        emb_geo_meta[cod_clean]["cod_cota"] = cod_cota
        except Exception as e:
            logger.warning(f"Error obteniendo metadatos de embalses nivel ArcGIS: {e}")

        # 2. Descargar lecturas en tiempo real de embalses3.php
        rows = []
        pattern_divs = r"<div[^>]*><a[^>]*title='([^']*)'[^>]*set_punto\('([^']+)'\)[^>]*>&nbsp;([^<]+)</a></div>\s*<div[^>]*title='([^']*)'[^>]*>([^<]*)</div>\s*<div[^>]*title='([^']*)'[^>]*>([^<]*)</div>\s*<div[^>]*>([^<]*)</div>"
        pattern_links = r"title='([^']+)'\s+href=[^>]*set_punto\('([^']+)'\)[^>]*>&nbsp;([^<]+)</a></div>\s*<div[^>]*><a[^>]*title='([^']*)'[^>]*>([^<]*)</a></div>\s*<div[^>]*><a[^>]*title='([^']*)'[^>]*>([^<]*)</a></div>\s*<div[^>]*>([^<]*)</div>"

        try:
            req_live = urllib.request.Request(SAIH_EMBALSES_LIVE_URL, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req_live, timeout=12) as resp:
                html = resp.read().decode("utf-8", errors="ignore")
            rows = re.findall(pattern_divs, html) or re.findall(pattern_links, html)
        except Exception as e:
            logger.warning(f"Error descargando embalses3.php: {e}")

        # Recolectar variables de embalses para telemetría
        telemetry_codes_to_fetch = set()
        for cod_clean, meta in emb_geo_meta.items():
            if meta.get("cod_volumen"):
                telemetry_codes_to_fetch.add(meta["cod_volumen"])
            if meta.get("cod_cota"):
                telemetry_codes_to_fetch.add(meta["cod_cota"])

        for r in rows:
            title_code, punto_code, raw_name, tag_cota, val_cota_str, tag_vol, val_vol_str, val_pct_str = r
            if tag_cota:
                telemetry_codes_to_fetch.add(tag_cota.strip())
            if tag_vol:
                telemetry_codes_to_fetch.add(tag_vol.strip())

        # Consultar telemetría en paralelo para las variables de embalses
        telemetry_map = self._fetch_variables_telemetry_parallel(telemetry_codes_to_fetch, max_workers=12)

        embalses = []
        features = []
        processed_codes = set()

        for r in rows:
            title_code, punto_code, raw_name, tag_cota, val_cota_str, tag_vol, val_vol_str, val_pct_str = r
            cod_clean = punto_code.strip()
            processed_codes.add(cod_clean)

            val_cota = _parse_num(val_cota_str)
            val_vol = _parse_num(val_vol_str)
            val_pct = _parse_num(val_pct_str)

            meta = emb_geo_meta.get(cod_clean, {})
            cod_vol_var = meta.get("cod_volumen") or tag_vol or f"{cod_clean}B01"
            cod_cota_var = meta.get("cod_cota") or tag_cota or f"{cod_clean}C12"

            dt_reading = now_iso
            if val_vol is None and cod_vol_var in telemetry_map:
                val_vol, dt_reading = telemetry_map[cod_vol_var]
            if val_cota is None and cod_cota_var in telemetry_map:
                val_cota, dt_reading = telemetry_map[cod_cota_var]

            # Fallback a caché previo si no se pudo obtener nuevo valor
            if val_vol is None or val_cota is None:
                prev = self._embalses_by_id.get(cod_clean) or self._embalses_by_id.get(f"segura_emb_{cod_clean.lower()}")
                if prev:
                    if val_vol is None and prev.get("volumen_actual") is not None:
                        val_vol = prev.get("volumen_actual")
                    if val_cota is None and prev.get("cota_actual") is not None:
                        val_cota = prev.get("cota_actual")

            # Extraer cota NMN de la cadena ej. "E.Fuensanta (67,92)"
            cota_nmn = None
            name_clean = raw_name.strip()
            match_cota = re.search(r"\(([\d,.]+)\)", raw_name)
            if match_cota:
                cota_nmn = _parse_num(match_cota.group(1))
                name_clean = re.sub(r"\s*\([\d,.]+\)", "", raw_name).strip()

            if name_clean.startswith("E."):
                name_clean = f"Embalse de {name_clean[2:].strip()}"

            lon = meta.get("lon")
            lat = meta.get("lat")
            if lon is None or lat is None:
                continue

            full_name = meta.get("nombre") or name_clean

            # Capacidad oficial NMN
            capacidad_nmn = CAPACIDADES_NMN_SEGURA.get(cod_clean)
            if capacidad_nmn is None and val_vol is not None and val_pct is not None and val_pct > 0:
                capacidad_nmn = round((val_vol / val_pct) * 100, 2)

            if val_vol is not None and capacidad_nmn and capacidad_nmn > 0:
                val_pct = round((val_vol / capacidad_nmn) * 100, 1)

            emb_obj = {
                "id_estacion": f"segura_emb_{cod_clean.lower()}",
                "codigo": cod_clean,
                "nombre": full_name,
                "tipo": "Embalse",
                "red": "CHS",
                "cuenca": "Segura",
                "lat": lat,
                "lon": lon,
                "poblacion": "",
                "provincia": "Murcia / Albacete / Alicante",
                "subcuenca": "Segura",
                "id_volumen": cod_vol_var,
                "id_cota": cod_cota_var,
                "id_caudal_in": "",
                "id_caudal_out": "",
                "id_caudal_rio": "",
                "volumen_actual": val_vol,
                "capacidad_nmn": capacidad_nmn,
                "porcentaje_llenado": val_pct,
                "cota_actual": val_cota,
                "cota_vertido": cota_nmn,
                "caudal_recibido": None,
                "caudal_salida": None,
                "caudal_salida_rio": None,
                "umbrales_salida_rio": {},
                "ultima_hora": dt_reading,
                "fecha_comunicacion": dt_reading,
                "unidad_volumen": "hm³",
                "unidad_cota": "m",
                "unidad_caudal": "m³/s",
            }
            embalses.append(emb_obj)
            features.append({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [round(lon, 6), round(lat, 6)]},
                "properties": emb_obj,
            })

        # Completar embalses restantes de ArcGIS si no vinieron en la tabla
        for cod_clean, meta in emb_geo_meta.items():
            if cod_clean not in processed_codes:
                lon = meta.get("lon")
                lat = meta.get("lat")
                if lon is None or lat is None:
                    continue
                cod_vol_var = meta.get("cod_volumen") or f"{cod_clean}B01"
                cod_cota_var = meta.get("cod_cota") or f"{cod_clean}C12"

                val_vol = None
                val_cota = None
                dt_reading = now_iso
                if cod_vol_var in telemetry_map:
                    val_vol, dt_reading = telemetry_map[cod_vol_var]
                if cod_cota_var in telemetry_map:
                    val_cota, dt_reading = telemetry_map[cod_cota_var]

                if val_vol is None or val_cota is None:
                    prev = self._embalses_by_id.get(cod_clean) or self._embalses_by_id.get(f"segura_emb_{cod_clean.lower()}")
                    if prev:
                        if val_vol is None and prev.get("volumen_actual") is not None:
                            val_vol = prev.get("volumen_actual")
                        if val_cota is None and prev.get("cota_actual") is not None:
                            val_cota = prev.get("cota_actual")

                capacidad_nmn = CAPACIDADES_NMN_SEGURA.get(cod_clean)
                val_pct = round((val_vol / capacidad_nmn) * 100, 1) if (val_vol is not None and capacidad_nmn and capacidad_nmn > 0) else None

                emb_obj = {
                    "id_estacion": f"segura_emb_{cod_clean.lower()}",
                    "codigo": cod_clean,
                    "nombre": meta.get("nombre") or f"Embalse {cod_clean}",
                    "tipo": "Embalse",
                    "red": "CHS",
                    "cuenca": "Segura",
                    "lat": lat,
                    "lon": lon,
                    "poblacion": "",
                    "provincia": "Murcia / Albacete / Alicante",
                    "subcuenca": "Segura",
                    "id_volumen": cod_vol_var,
                    "id_cota": cod_cota_var,
                    "id_caudal_in": "",
                    "id_caudal_out": "",
                    "id_caudal_rio": "",
                    "volumen_actual": val_vol,
                    "capacidad_nmn": capacidad_nmn,
                    "porcentaje_llenado": val_pct,
                    "cota_actual": val_cota,
                    "cota_vertido": None,
                    "caudal_recibido": None,
                    "caudal_salida": None,
                    "caudal_salida_rio": None,
                    "umbrales_salida_rio": {},
                    "ultima_hora": dt_reading,
                    "fecha_comunicacion": dt_reading,
                    "unidad_volumen": "hm³",
                    "unidad_cota": "m",
                    "unidad_caudal": "m³/s",
                }
                embalses.append(emb_obj)
                features.append({
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [round(lon, 6), round(lat, 6)]},
                    "properties": emb_obj,
                })

        geojson = {"type": "FeatureCollection", "features": features}

        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(STATIC_SEGURA_EMBALSES_FILE, "w", encoding="utf-8") as f:
            json.dump(embalses, f, ensure_ascii=False, indent=2)

        with open(SEGURA_EMBALSES_GEOJSON_FILE, "w", encoding="utf-8") as f:
            json.dump(geojson, f, ensure_ascii=False, indent=2)

        if PUBLIC_DATA_DIR.exists():
            with open(PUBLIC_DATA_DIR / "segura_embalses.geojson", "w", encoding="utf-8") as f:
                json.dump(geojson, f, ensure_ascii=False, indent=2)

        self._embalses = embalses
        self._index_embalses()
        self._last_embalses_sync_time = datetime.now()
        logger.info(f"Sincronizados {len(embalses)} embalses de SAIH Segura.")
        return embalses

    # ==========================================
    # GETTERS CON FRESHNESS BAJO DEMANDA
    # ==========================================

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
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, self.sync_pluvios)
        except Exception as e:
            logger.warning(f"Error en sync background pluvios Segura: {e}")
        finally:
            self._bg_pluvios_syncing = False

    async def ensure_fresh_pluvios_data(self):
        now = datetime.now()
        is_stale = (
            self._last_pluvios_sync_time is None
            or (now - self._last_pluvios_sync_time).total_seconds() >= SYNC_TTL_SECONDS
            or len(self._pluvios) == 0
        )
        if len(self._pluvios) > 0:
            if is_stale:
                self._trigger_bg_pluvios_sync()
            return

        async with self._sync_lock:
            if len(self._pluvios) == 0:
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, self.sync_pluvios)

    async def get_pluvios(self, auto_sync: bool = True) -> List[Dict[str, Any]]:
        if auto_sync:
            await self.ensure_fresh_pluvios_data()
        return self._pluvios

    async def get_pluvios_geojson(self, auto_sync: bool = True) -> Dict[str, Any]:
        pluvios = await self.get_pluvios(auto_sync=auto_sync)
        features = [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [p["lon"], p["lat"]]},
                "properties": p,
            }
            for p in pluvios
            if p.get("lat") is not None and p.get("lon") is not None
        ]
        return {"type": "FeatureCollection", "features": features}

    def get_pluvio_by_id(self, id_or_code: str) -> Optional[Dict[str, Any]]:
        key = str(id_or_code).strip().upper()
        return (
            self._pluvios_by_id.get(key)
            or self._pluvios_by_id.get(str(id_or_code))
            or self._pluvios_by_id.get(key.replace("SEGURA_PLUV_", ""))
            or self._pluvios_by_id.get(str(id_or_code).replace("segura_pluv_", ""))
        )

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
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, self.sync_aforos)
        except Exception as e:
            logger.warning(f"Error en sync background aforos Segura: {e}")
        finally:
            self._bg_aforos_syncing = False

    async def ensure_fresh_aforos_data(self):
        now = datetime.now()
        is_stale = (
            self._last_aforos_sync_time is None
            or (now - self._last_aforos_sync_time).total_seconds() >= SYNC_TTL_SECONDS
            or len(self._aforos) == 0
        )
        if len(self._aforos) > 0:
            if is_stale:
                self._trigger_bg_aforos_sync()
            return

        async with self._sync_lock:
            if len(self._aforos) == 0:
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, self.sync_aforos)

    async def get_caudales(self, auto_sync: bool = True) -> List[Dict[str, Any]]:
        if auto_sync:
            await self.ensure_fresh_aforos_data()
        return self._aforos

    async def get_caudales_geojson(self, auto_sync: bool = True) -> Dict[str, Any]:
        aforos = await self.get_caudales(auto_sync=auto_sync)
        features = [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [a["lon"], a["lat"]]},
                "properties": a,
            }
            for a in aforos
            if a.get("lat") is not None and a.get("lon") is not None
        ]
        return {"type": "FeatureCollection", "features": features}

    def get_caudal_by_id(self, id_or_code: str) -> Optional[Dict[str, Any]]:
        key = str(id_or_code).strip().upper()
        return (
            self._aforos_by_id.get(key)
            or self._aforos_by_id.get(str(id_or_code))
            or self._aforos_by_id.get(key.replace("SEGURA_AFORO_", ""))
            or self._aforos_by_id.get(str(id_or_code).replace("segura_aforo_", ""))
        )

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
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, self.sync_embalses)
        except Exception as e:
            logger.warning(f"Error en sync background embalses Segura: {e}")
        finally:
            self._bg_embalses_syncing = False

    async def ensure_fresh_embalses_data(self):
        now = datetime.now()
        is_stale = (
            self._last_embalses_sync_time is None
            or (now - self._last_embalses_sync_time).total_seconds() >= SYNC_TTL_SECONDS
            or len(self._embalses) == 0
        )
        if len(self._embalses) > 0:
            if is_stale:
                self._trigger_bg_embalses_sync()
            return

        async with self._sync_lock:
            if len(self._embalses) == 0:
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, self.sync_embalses)

    async def get_embalses(self, auto_sync: bool = True) -> List[Dict[str, Any]]:
        if auto_sync:
            await self.ensure_fresh_embalses_data()
        return self._embalses

    async def get_embalses_geojson(self, auto_sync: bool = True) -> Dict[str, Any]:
        embalses = await self.get_embalses(auto_sync=auto_sync)
        features = [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [e["lon"], e["lat"]]},
                "properties": e,
            }
            for e in embalses
            if e.get("lat") is not None and e.get("lon") is not None
        ]
        return {"type": "FeatureCollection", "features": features}

    def get_embalse_by_id(self, id_or_code: str) -> Optional[Dict[str, Any]]:
        key = str(id_or_code).strip().upper()
        return (
            self._embalses_by_id.get(key)
            or self._embalses_by_id.get(str(id_or_code))
            or self._embalses_by_id.get(key.replace("SEGURA_EMB_", ""))
            or self._embalses_by_id.get(str(id_or_code).replace("segura_emb_", ""))
        )

    # ==========================================
    # 4. SERIES TEMPORALES HISTÓRICAS (CHS)
    # ==========================================

    async def get_history(
        self,
        id_variable: str,
        hours: int = 24,
        is_embalse: bool = False,
        variable_type: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Consulta la serie temporal continua (pasos de 5 minutos) de un aforo, embalse o pluviómetro CHS.
        Formato de llamada iVisor: graficaVar.php?puntos={COD}&dfrom={DD/MM/YYYY HH:mm}&dto={DD/MM/YYYY HH:mm}&source=I
        """
        raw_id = str(id_variable).strip()

        # Localizar estación en aforos, embalses o pluviómetros
        st_info = (
            self.get_caudal_by_id(raw_id)
            or self.get_embalse_by_id(raw_id)
            or self.get_pluvio_by_id(raw_id)
        )

        target_code = raw_id
        var_type_req = (variable_type or "").lower().strip()
        ret_unit = "m³/s"

        if st_info:
            if st_info.get("tipo") == "Aforo":
                if var_type_req in ("caudal", "q"):
                    target_code = st_info.get("tag_caudal") or target_code
                    ret_unit = "m³/s"
                elif var_type_req in ("nivel", "altura", "h") or st_info.get("unidad_grafica") == "m":
                    target_code = st_info.get("tag_nivel") or st_info.get("tag_caudal") or target_code
                    ret_unit = "m"
                else:
                    if st_info.get("tag_caudal"):
                        target_code = st_info.get("tag_caudal")
                        ret_unit = "m³/s"
                    elif st_info.get("tag_nivel"):
                        target_code = st_info.get("tag_nivel")
                        ret_unit = "m"
                    else:
                        target_code = st_info.get("tag_caudal") or target_code
                        ret_unit = "m³/s"
            elif st_info.get("tipo") == "Embalse":
                if var_type_req in ("nivel", "cota", "h"):
                    target_code = st_info.get("id_cota") or target_code
                    ret_unit = "m"
                else:
                    target_code = st_info.get("id_volumen") or target_code
                    ret_unit = "hm³"
            elif st_info.get("tipo") == "Pluviómetro":
                target_code = st_info.get("codigo_variable") or target_code
                ret_unit = "mm"

        # Limpiar prefijos internos si quedaron
        target_code = (
            target_code.replace("segura_aforo_", "")
            .replace("segura_emb_", "")
            .replace("segura_pluv_", "")
            .upper()
        )

        now = datetime.now(MADRID_TZ)
        if start_date and end_date:
            try:
                dt_start = datetime.strptime(start_date, "%Y-%m-%d %H:%M:%S")
                dt_end = datetime.strptime(end_date, "%Y-%m-%d %H:%M:%S")
            except Exception:
                dt_end = now
                dt_start = now - timedelta(hours=hours)
        else:
            dt_end = now
            dt_start = now - timedelta(hours=int(hours) if isinstance(hours, (int, float)) else 24)

        dfrom_str = dt_start.strftime("%d/%m/%Y %H:%M")
        dto_str = dt_end.strftime("%d/%m/%Y %H:%M")

        url = (
            f"{SAIH_CHART_URL}?puntos={urllib.parse.quote(target_code)}"
            f"&dfrom={urllib.parse.quote(dfrom_str)}&dto={urllib.parse.quote(dto_str)}&source=I"
        )

        def _fetch_sync():
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=12) as resp:
                content = resp.read().decode("utf-8", errors="ignore")

            # Extraer data: [{x:1790942400000, y:0.00, y0:0.00, ac:0.00, fb:'1', pr:'A'}, ...]
            match = re.search(r"data:\s*(\[\s*\{.*?\}\s*\])", content, re.DOTALL)
            if not match:
                return []

            raw_json = match.group(1)
            fixed = re.sub(r"(\b[a-zA-Z0-9_]+\b)\s*:", r'"\1":', raw_json)
            fixed = fixed.replace("'", '"')
            return json.loads(fixed)

        loop = asyncio.get_running_loop()
        try:
            points_raw = await loop.run_in_executor(None, _fetch_sync)
        except Exception as e:
            logger.error(f"Error consultando histórico CHS para {target_code}: {e}")
            points_raw = []

        serie = []
        for pt in points_raw:
            ts_ms = pt.get("x")
            val_y = pt.get("y")
            if ts_ms is not None:
                dt_pt = datetime.fromtimestamp(ts_ms / 1000.0, tz=MADRID_TZ)
                val_float = round(float(val_y), 3) if val_y is not None else None
                serie.append({
                    "fecha": dt_pt.strftime("%Y-%m-%d %H:%M:%S"),
                    "valor": val_float,
                    "estado": 1 if pt.get("fb") == "1" else 0,
                })

        serie.sort(key=lambda item: item["fecha"])

        return {
            "id_variable": str(id_variable),
            "codigo_saih": target_code,
            "estacion": st_info,
            "unidad": ret_unit,
            "rango": {
                "desde": dt_start.strftime("%Y-%m-%d %H:%M:%S"),
                "hasta": dt_end.strftime("%Y-%m-%d %H:%M:%S"),
                "horas": hours,
            },
            "puntos_totales": len(serie),
            "serie": serie,
        }

    def _download_and_process_cuencas_geojson(self) -> Dict[str, Any]:
        """Descarga dinámicamente desde el ArcGIS REST oficial de la CHS las subcuencas del Segura, las optimiza y guarda en disco."""
        url = "https://www.chsegura.es/arcgis/rest/services/CHSApps/SaihSubcuencasPrevisiones/MapServer/3/query?where=1%3D1&outFields=*&f=geojson&outSR=4326"
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=15) as res:
            data = json.loads(res.read().decode("utf-8"))

        features = []
        for f in data.get("features", []):
            props = f.get("properties", {})
            nom = props.get("NOMBRE") or ""
            if not nom or nom.lower().startswith("etieueta") or props.get("EtiquetaFecha") == 1:
                continue
            code = props.get("CODIGO") or f"CHS_{props.get('OBJECTID')}"
            area = props.get("SUPERFICIE")
            clean_nom = (
                nom.title()
                .replace("Rbla.", "Rambla")
                .replace("E.", "Embalse")
                .replace("Mi", "Margen Izq.")
                .replace("Md", "Margen Der.")
                .replace("Rio", "Río")
                .strip()
            )

            geom = _simplify_geom(f.get("geometry"), tolerance=0.0008)
            features.append({
                "type": "Feature",
                "id": f"CHS_{code}",
                "properties": {
                    "id": f"CHS_{code}",
                    "NomSistExp": clean_nom,
                    "Subsistema": clean_nom,
                    "Sistema": clean_nom,
                    "Demarcacion": "Demarcación Hidrográfica del Segura (CHS)",
                    "codigo_saih": code,
                    "Area km2": area,
                    "Superf km2": area,
                    "demarcacion": "Segura",
                },
                "geometry": geom,
            })

        fc = {"type": "FeatureCollection", "features": features}

        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(SEGURA_CUENCAS_GEOJSON_FILE, "w", encoding="utf-8") as f:
            json.dump(fc, f, ensure_ascii=False)

        if PUBLIC_DATA_DIR.exists():
            with open(PUBLIC_DATA_DIR / "segura_subcuencas.geojson", "w", encoding="utf-8") as f:
                json.dump(fc, f, ensure_ascii=False)

        return fc

    async def get_cuencas_geojson(self) -> Dict[str, Any]:
        """Obtiene el GeoJSON de las subcuencas/subsistemas de la cuenca del Segura."""
        if self._cuencas_geojson_cache is not None:
            return self._cuencas_geojson_cache

        if SEGURA_CUENCAS_GEOJSON_FILE.exists():
            try:
                with open(SEGURA_CUENCAS_GEOJSON_FILE, "r", encoding="utf-8") as f:
                    self._cuencas_geojson_cache = json.load(f)
                    return self._cuencas_geojson_cache
            except Exception as e:
                logger.warning(f"Error leyendo segura_subcuencas.geojson local: {e}")

        async with self._cuencas_lock:
            if self._cuencas_geojson_cache is not None:
                return self._cuencas_geojson_cache
            loop = asyncio.get_running_loop()
            try:
                self._cuencas_geojson_cache = await loop.run_in_executor(
                    None, self._download_and_process_cuencas_geojson
                )
            except Exception as e:
                logger.error(f"Error descargando subcuencas CHS desde ArcGIS: {e}")
                self._cuencas_geojson_cache = {"type": "FeatureCollection", "features": []}

            return self._cuencas_geojson_cache


segura_service = SeguraService()
