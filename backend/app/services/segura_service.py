"""
Servicio de sincronización y consulta de datos del S.A.I.H. Segura (Confederación Hidrográfica del Segura - CHS / MITECO).
Proporciona datos en tiempo real de pluviómetros (144 estaciones), aforos en ríos/canales (134 estaciones)
y embalses (27 embalses) en las provincias de Murcia, Alicante, Albacete, Jaén, Almería y Granada.
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

# Transformador de coordenadas EPSG:25830 (UTM 30N) a EPSG:4326 (WGS84 lon, lat)
transformer = Transformer.from_crs("EPSG:25830", "EPSG:4326", always_xy=True)


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

            for f in features_raw:
                try:
                    attrs = f.get("attributes", {})
                    geom = f.get("geometry", {})

                    x = geom.get("x")
                    y = geom.get("y")
                    if x is None or y is None:
                        continue

                    lon, lat = transformer.transform(float(x), float(y))

                    cod_var = (attrs.get("CodVariableHidrologica") or "").strip()
                    nombre = (attrs.get("DenominacionPtoMedicion") or "").strip()
                    municipio = (attrs.get("Municipio") or "").strip()

                    # Código de estación base (ej. '06A16' de '06A16P01')
                    cod_estacion = cod_var[:5] if len(cod_var) >= 5 else cod_var

                    lluvia_1h = _parse_num(attrs.get("LluviaUltimaHora")) or 0.0
                    lluvia_3h = _parse_num(attrs.get("LluviaUltimas3Horas")) or 0.0
                    lluvia_6h = _parse_num(attrs.get("LluviaUltimas6Horas")) or 0.0
                    lluvia_12h = _parse_num(attrs.get("LluviaUltimas12Horas")) or 0.0
                    lluvia_24h = _parse_num(attrs.get("LluviaUltimas24Horas")) or 0.0

                    # Gestión del acumulado exacto de 4h:
                    # 1) Registrar lectura de 1h / 5min en buffer rodante
                    # 2) Sumar intervalo [T - 4h, T] si hay buffer; si no, interpolar entre 3h y 6h
                    if cod_var not in self._pluvio_rolling_buffer:
                        self._pluvio_rolling_buffer[cod_var] = []

                    # Limpiar lecturas anteriores a 24h
                    cutoff_time = now - timedelta(hours=24)
                    self._pluvio_rolling_buffer[cod_var] = [
                        (t, v) for (t, v) in self._pluvio_rolling_buffer[cod_var] if t >= cutoff_time
                    ]

                    # Si tenemos ventana en buffer de 4h
                    cutoff_4h = now - timedelta(hours=4)
                    buf_4h_vals = [v for (t, v) in self._pluvio_rolling_buffer[cod_var] if t >= cutoff_4h]

                    if buf_4h_vals and len(buf_4h_vals) >= 12:
                        lluvia_4h = round(sum(buf_4h_vals), 2)
                    else:
                        # Interpolación ponderada entre 3h y 6h: 3h + (6h - 3h)/3
                        if lluvia_6h >= lluvia_3h:
                            lluvia_4h = round(lluvia_3h + (lluvia_6h - lluvia_3h) * (1.0 / 3.0), 2)
                        else:
                            lluvia_4h = lluvia_3h

                    pluvio_obj = {
                        "id_estacion": f"segura_pluv_{cod_var.lower()}",
                        "id_variable": cod_var,
                        "codigo": cod_estacion,
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
                        "lluvia_1h": round(lluvia_1h, 2),
                        "precipitacion_1h": round(lluvia_1h, 2),
                        "fecha_1h": now_iso,
                        "lluvia_3h": round(lluvia_3h, 2),
                        "lluvia_4h": round(lluvia_4h, 2),
                        "precipitacion_4h": round(lluvia_4h, 2),
                        "fecha_4h": now_iso,
                        "lluvia_6h": round(lluvia_6h, 2),
                        "lluvia_12h": round(lluvia_12h, 2),
                        "precipitacion_12h": round(lluvia_12h, 2),
                        "fecha_12h": now_iso,
                        "lluvia_24h": round(lluvia_24h, 2),
                        "precipitacion_24h": round(lluvia_24h, 2),
                        "fecha_24h": now_iso,
                        "ultima_hora": now_iso,
                        "unidad": "mm",
                    }
                    pluvios.append(pluvio_obj)

                    features.append({
                        "type": "Feature",
                        "geometry": {"type": "Point", "coordinates": [round(lon, 6), round(lat, 6)]},
                        "properties": pluvio_obj,
                    })
                except Exception as err:
                    logger.warning(f"Error parseando pluviómetro CHS {f}: {err}")
                    continue

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
        (Layer 10 caudal, Layer 11 nivel) con las lecturas en tiempo real de saihweb.chsegura.es/cauces3.php.
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
                    # normalizar código: '01A01A1' -> '01A01'
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

        # 2. Descargar lecturas en tiempo real de cauces3.php
        try:
            req_live = urllib.request.Request(SAIH_CAUCES_LIVE_URL, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req_live, timeout=12) as resp:
                html = resp.read().decode("utf-8", errors="ignore")

            # Patrón de extracción de filas en cauces3.php
            # Regex busca enlaces a set_punto, graficar(nivel) y graficar(caudal)
            rows = re.findall(
                r"title='([^']+)'\s+href=[^>]*set_punto\('([^']+)'\)[^>]*>&nbsp;([^<]+)</a></div>\s*<div[^>]*>\s*<a[^>]*title='([^']*)'[^>]*>([^<]*)</a></div>\s*<div[^>]*>\s*<a[^>]*title='([^']*)'[^>]*>([^<]*)</a>",
                html,
            )

            aforos = []
            features = []
            processed_codes = set()

            for r in rows:
                title_codes, punto_code, raw_name, tag_nivel, val_nivel_str, tag_caudal, val_caudal_str = r
                cod_clean = punto_code.strip()
                processed_codes.add(cod_clean)

                val_nivel = _parse_num(val_nivel_str)
                val_caudal = _parse_num(val_caudal_str)

                # Extraer cota máxima de aviso si viene entre paréntesis en el nombre: ej. "A.Las Juntas(5,62)"
                cota_max = None
                name_clean = raw_name.strip()
                match_cota = re.search(r"\(([\d,.]+)\)", raw_name)
                if match_cota:
                    cota_max = _parse_num(match_cota.group(1))
                    name_clean = re.sub(r"\s*\([\d,.]+\)", "", raw_name).strip()

                # Limpieza de prefijos comunes para visualización bonita
                if name_clean.startswith("A."):
                    name_clean = f"Aforo en {name_clean[2:].strip()}"
                elif name_clean.startswith("AgAb."):
                    name_clean = f"Aguas Abajo de {name_clean[5:].strip()}"

                meta = geo_meta_by_code.get(cod_clean, {})
                lon = meta.get("lon")
                lat = meta.get("lat")
                if lon is None or lat is None:
                    # Coordenadas por defecto aproximadas de la cuenca si no estuviese en el mapa base
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
                    "lat": lon,  # Corregido abajo en el geojson
                    "lon": lat,
                    "poblacion": "",
                    "provincia": "Murcia / Albacete / Alicante",
                    "subcuenca": "Segura",
                    "ultimo_caudal": val_caudal,
                    "caudal": val_caudal,
                    "ultimo_nivel": val_nivel,
                    "nivel": val_nivel,
                    "cota_max": cota_max,
                    "tag_caudal": tag_caudal or meta.get("cod_caudal") or "",
                    "tag_nivel": tag_nivel or meta.get("cod_nivel") or "",
                    "ultima_hora": now_iso,
                    "fecha_comunicacion": now_iso,
                    "umbrales": umbrales,
                    "unidad": "m³/s",
                    "unidad_nivel": "m",
                }
                # Fix lat/lon mapping
                aforo_obj["lat"] = lat
                aforo_obj["lon"] = lon

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
                        "ultimo_caudal": None,
                        "caudal": None,
                        "ultimo_nivel": None,
                        "nivel": None,
                        "cota_max": None,
                        "tag_caudal": cod_q,
                        "tag_nivel": cod_n,
                        "ultima_hora": now_iso,
                        "fecha_comunicacion": now_iso,
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

        except Exception as e:
            logger.error(f"Error sincronizando aforos CHS: {e}")
            return self._aforos

    # ==========================================
    # 3. EMBALSES (PRENDAS Y CAPACIDADES CHS)
    # ==========================================

    def sync_embalses(self) -> List[Dict[str, Any]]:
        """
        Sincroniza los 27 embalses de la cuenca del Segura combinando ArcGIS
        (Layer 8 volumen, Layer 9 nivel) y saihweb.chsegura.es/embalses3.php.
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
        try:
            req_live = urllib.request.Request(SAIH_EMBALSES_LIVE_URL, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req_live, timeout=12) as resp:
                html = resp.read().decode("utf-8", errors="ignore")

            rows = re.findall(
                r"title='([^']+)'\s+href=[^>]*set_punto\('([^']+)'\)[^>]*>&nbsp;([^<]+)</a></div>\s*<div[^>]*><a[^>]*title='([^']*)'[^>]*>([^<]*)</a></div>\s*<div[^>]*><a[^>]*title='([^']*)'[^>]*>([^<]*)</a></div>\s*<div[^>]*>([^<]*)</div>",
                html,
            )

            embalses = []
            features = []

            for r in rows:
                title_code, punto_code, raw_name, tag_cota, val_cota_str, tag_vol, val_vol_str, val_pct_str = r
                cod_clean = punto_code.strip()

                val_cota = _parse_num(val_cota_str)
                val_vol = _parse_num(val_vol_str)
                val_pct = _parse_num(val_pct_str)

                # Extraer cota NMN de la cadena ej. "E.Fuensanta (67,92)"
                cota_nmn = None
                name_clean = raw_name.strip()
                match_cota = re.search(r"\(([\d,.]+)\)", raw_name)
                if match_cota:
                    cota_nmn = _parse_num(match_cota.group(1))
                    name_clean = re.sub(r"\s*\([\d,.]+\)", "", raw_name).strip()

                if name_clean.startswith("E."):
                    name_clean = f"Embalse de {name_clean[2:].strip()}"

                meta = emb_geo_meta.get(cod_clean, {})
                lon = meta.get("lon")
                lat = meta.get("lat")
                if lon is None or lat is None:
                    continue

                full_name = meta.get("nombre") or name_clean

                # Capacidad total estimada a partir de vol_actual y %
                capacidad_nmn = None
                if val_vol is not None and val_pct is not None and val_pct > 0:
                    capacidad_nmn = round((val_vol / val_pct) * 100, 2)

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
                    "id_volumen": tag_vol or meta.get("cod_volumen") or "",
                    "id_cota": tag_cota or meta.get("cod_cota") or "",
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
                    "ultima_hora": now_iso,
                    "fecha_comunicacion": now_iso,
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

        except Exception as e:
            logger.error(f"Error sincronizando embalses CHS: {e}")
            return self._embalses

    # ==========================================
    # GETTERS CON FRESHNESS BAJO DEMANDA
    # ==========================================

    async def ensure_fresh_pluvios_data(self):
        now = datetime.now()
        is_stale = (
            self._last_pluvios_sync_time is None
            or (now - self._last_pluvios_sync_time).total_seconds() >= SYNC_TTL_SECONDS
            or len(self._pluvios) == 0
        )
        if is_stale:
            async with self._sync_lock:
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

    async def ensure_fresh_aforos_data(self):
        now = datetime.now()
        is_stale = (
            self._last_aforos_sync_time is None
            or (now - self._last_aforos_sync_time).total_seconds() >= SYNC_TTL_SECONDS
            or len(self._aforos) == 0
        )
        if is_stale:
            async with self._sync_lock:
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

    async def ensure_fresh_embalses_data(self):
        now = datetime.now()
        is_stale = (
            self._last_embalses_sync_time is None
            or (now - self._last_embalses_sync_time).total_seconds() >= SYNC_TTL_SECONDS
            or len(self._embalses) == 0
        )
        if is_stale:
            async with self._sync_lock:
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

        if st_info:
            if st_info.get("tipo") == "Aforo":
                if var_type_req in ("nivel", "altura", "h"):
                    target_code = st_info.get("tag_nivel") or st_info.get("id_variable") or target_code
                else:
                    target_code = st_info.get("tag_caudal") or st_info.get("id_variable") or target_code
            elif st_info.get("tipo") == "Embalse":
                if var_type_req in ("nivel", "cota", "h"):
                    target_code = st_info.get("id_cota") or target_code
                else:
                    target_code = st_info.get("id_volumen") or target_code
            elif st_info.get("tipo") == "Pluviómetro":
                target_code = st_info.get("codigo_variable") or target_code

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
            # Reemplazar claves sin comillas a JSON válido
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
            "rango": {
                "desde": dt_start.strftime("%Y-%m-%d %H:%M:%S"),
                "hasta": dt_end.strftime("%Y-%m-%d %H:%M:%S"),
                "horas": hours,
            },
            "puntos_totales": len(serie),
            "serie": serie,
        }


segura_service = SeguraService()
