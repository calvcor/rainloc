"""
Servicio de sincronización y consulta de datos del S.A.I.H. Guadalquivir (Confederación Hidrográfica del Guadalquivir - CHG / MITECO)
Proporciona datos en tiempo real de pluviómetros, estaciones de aforo (ríos/canales) y embalses en las provincias de
Jaén, Córdoba, Granada, Sevilla, Huelva, Málaga, Ciudad Real, Badajoz, etc.
"""

import asyncio
import base64
import html
import http.cookiejar
import json
import logging
import math
import re
import ssl
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger("rainloc.guadalquivir_service")

MADRID_TZ = ZoneInfo("Europe/Madrid")
SYNC_TTL_SECONDS = 300  # 5 minutos de caché

GUADALQUIVIR_BASE_URL = "https://www.chguadalquivir.es/saih"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 RainLoc/3.0"

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
PUBLIC_DATA_DIR = Path(__file__).resolve().parent.parent.parent.parent / "public" / "data"

STATIC_GUADALQUIVIR_PLUVIOS_FILE = DATA_DIR / "guadalquivir_lluvias_estaciones.json"
STATIC_GUADALQUIVIR_AFOROS_FILE = DATA_DIR / "guadalquivir_aforos_estaciones.json"
STATIC_GUADALQUIVIR_EMBALSES_FILE = DATA_DIR / "guadalquivir_embalses_estaciones.json"
GUADALQUIVIR_META_FILE = DATA_DIR / "guadalquivir_metadata.json"

GUADALQUIVIR_PLUVIOS_GEOJSON_FILE = DATA_DIR / "guadalquivir_lluvias.geojson"
GUADALQUIVIR_AFOROS_GEOJSON_FILE = DATA_DIR / "guadalquivir_aforos.geojson"
GUADALQUIVIR_EMBALSES_GEOJSON_FILE = DATA_DIR / "guadalquivir_embalses.geojson"


def _parse_num(val_str: Optional[str]) -> Optional[float]:
    if not val_str:
        return None
    cleaned = (
        str(val_str)
        .replace("&nbsp;", "")
        .replace("\xa0", "")
        .replace("hm³", "")
        .replace("m³/s", "")
        .replace("m.l.a", "")
        .replace("m", "")
        .replace("l/m²", "")
        .replace("mm", "")
        .replace("*", "")
        .strip()
    )
    if not cleaned or cleaned == "--" or cleaned == "-":
        return None
    cleaned = cleaned.replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return None


class GuadalquivirService:
    def __init__(self):
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

    def _load_cached_files(self):
        if GUADALQUIVIR_META_FILE.exists():
            try:
                with open(GUADALQUIVIR_META_FILE, "r", encoding="utf-8") as f:
                    self._metadata_by_code = json.load(f)
            except Exception as e:
                logger.warning(f"Error cargando metadatos guardados de Guadalquivir: {e}")

        if STATIC_GUADALQUIVIR_PLUVIOS_FILE.exists():
            try:
                with open(STATIC_GUADALQUIVIR_PLUVIOS_FILE, "r", encoding="utf-8") as f:
                    self._pluvios = json.load(f)
                    self._index_pluvios()
            except Exception as e:
                logger.warning(f"Error cargando pluviómetros de Guadalquivir: {e}")

        if STATIC_GUADALQUIVIR_AFOROS_FILE.exists():
            try:
                with open(STATIC_GUADALQUIVIR_AFOROS_FILE, "r", encoding="utf-8") as f:
                    self._aforos = json.load(f)
                    self._index_aforos()
            except Exception as e:
                logger.warning(f"Error cargando aforos de Guadalquivir: {e}")

        if STATIC_GUADALQUIVIR_EMBALSES_FILE.exists():
            try:
                with open(STATIC_GUADALQUIVIR_EMBALSES_FILE, "r", encoding="utf-8") as f:
                    self._embalses = json.load(f)
                    self._index_embalses()
            except Exception as e:
                logger.warning(f"Error cargando embalses de Guadalquivir: {e}")

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
            if p.get("sensor_code"):
                idx[str(p["sensor_code"])] = p
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
            if a.get("sensor_code"):
                idx[str(a["sensor_code"])] = a
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
            if e.get("sensor_code"):
                idx[str(e["sensor_code"])] = e
        self._embalses_by_id = idx

    # ==========================================
    # 0. METADATOS CARTOGRÁFICOS (GeoDatos.aspx)
    # ==========================================

    def sync_all_metadata(self) -> Dict[str, Dict[str, Any]]:
        """Descarga e indexa todas las estaciones desde GeoDatos.aspx?tipo=0..7"""
        meta_map: Dict[str, Dict[str, Any]] = {}
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        # tipo=0: Embalses, tipo=2: Aforos, tipo=7: Pluviómetros, etc.
        for t in range(8):
            url = f"{GUADALQUIVIR_BASE_URL}/GeoDatos.aspx?tipo={t}"
            try:
                req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
                with urllib.request.urlopen(req, context=ctx, timeout=12) as resp:
                    raw_xml = resp.read().decode("utf-8", "ignore")

                m = re.search(r"<puntos>(.*?)</puntos>", raw_xml, re.DOTALL)
                if m:
                    geojson = json.loads(m.group(1))
                    for f in geojson.get("features", []):
                        props = f.get("properties", {})
                        geom = f.get("geometry", {})
                        coords = geom.get("coordinates", [])
                        punto = str(props.get("Punto", "")).strip()
                        code_m = re.match(r"^([A-Z][0-9]{2,3})\b", punto)
                        if code_m and len(coords) == 2:
                            code = code_m.group(1)
                            clean_name = re.sub(r"^[A-Z][0-9]{2,3}\s*", "", punto).strip()
                            cap_val = _parse_num(props.get("Capacidad"))
                            meta_map[code] = {
                                "code": code,
                                "nombre": clean_name or punto,
                                "punto_completo": punto,
                                "lon": float(coords[0]),
                                "lat": float(coords[1]),
                                "capacidad": cap_val,
                                "municipio": str(props.get("Municipio", "")).strip(),
                                "provincia": str(props.get("Provincia", "")).strip(),
                                "tipo_num": t
                            }
            except Exception as e:
                logger.warning(f"Error descargando GeoDatos tipo {t} de Guadalquivir: {e}")

        if meta_map:
            self._metadata_by_code = meta_map
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            with open(GUADALQUIVIR_META_FILE, "w", encoding="utf-8") as f:
                json.dump(self._metadata_by_code, f, ensure_ascii=False, indent=2)

        return self._metadata_by_code

    # ==========================================
    # 1. PLUVIÓMETROS (LluviaTabla.aspx)
    # ==========================================

    def sync_pluvios_metadata(self) -> List[Dict[str, Any]]:
        """Descarga la tabla de lluvias de LluviaTabla.aspx y fusiona con metadatos"""
        if not self._metadata_by_code:
            self.sync_all_metadata()

        metadata = self._metadata_by_code
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        url = f"{GUADALQUIVIR_BASE_URL}/LluviaTabla.aspx"
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, context=ctx, timeout=15) as resp:
                page_html = resp.read().decode("utf-8", "ignore")
        except Exception as e:
            logger.error(f"Error descargando lluvias de Guadalquivir: {e}")
            return self._pluvios

        rows = re.findall(r'<tr class="filasGridView">(.*?)</tr>', page_html, re.DOTALL)
        now_madrid = datetime.now(MADRID_TZ)
        now_iso = now_madrid.strftime("%Y-%m-%d %H:%M:%S")

        pluvios_list = []
        for r in rows:
            cols = [html.unescape(re.sub(r"<[^>]+>", "", c).strip()) for c in re.findall(r"<td[^>]*>(.*?)</td>", r, re.DOTALL)]
            if len(cols) >= 7:
                nombre_raw = cols[1].strip()
                code_m = re.match(r"^([A-Z][0-9]{2,3})\b", nombre_raw)
                if not code_m:
                    continue
                code = code_m.group(1)

                sen_m = re.search(r"IniciaCurva\('([^']+)'\)", r)
                sensor_code = sen_m.group(1) if sen_m else f"{code}_202"

                r1h = _parse_num(cols[2]) or 0.0
                r_prev = _parse_num(cols[3]) or 0.0
                r12h = _parse_num(cols[4]) or 0.0
                rhoy = _parse_num(cols[5]) or 0.0
                rayer = _parse_num(cols[6]) or 0.0

                # El SAIH Guadalquivir publica: 1h, Hora Anterior, 12h móviles, Hoy y Ayer.
                # NO publica ventana móvil de 4h ni de 24h. No inventamos datos:
                r4h = None
                r24h = None

                # Obtener coordenadas de metadatos
                meta = metadata.get(code, {})
                lat = meta.get("lat")
                lon = meta.get("lon")

                clean_name = meta.get("nombre") or re.sub(r"^[A-Z][0-9]{2,3}\s*", "", nombre_raw).strip()
                provincia = meta.get("provincia") or ""

                if not provincia:
                    prov_m = re.search(r"\(([A-Z]{2})\)", nombre_raw)
                    if prov_m:
                        prov_code = prov_m.group(1)
                        prov_map = {"SE": "Sevilla", "CO": "Córdoba", "JA": "Jaén", "GR": "Granada", "HU": "Huelva", "MA": "Málaga", "BA": "Badajoz", "CR": "Ciudad Real"}
                        provincia = prov_map.get(prov_code, prov_code)

                if lat is None or lon is None:
                    continue

                pluvio_dict = {
                    "id_estacion": f"guadal_pluv_{code}",
                    "codigo": f"GUADAL_{code}",
                    "codigo_corto": code,
                    "sensor_code": sensor_code,
                    "nombre": f"{code} {clean_name}",
                    "municipio": meta.get("municipio", ""),
                    "provincia": provincia,
                    "subcuenca": "Cuenca Hidrográfica del Guadalquivir",
                    "lat": lat,
                    "lon": lon,
                    "red": "GUADALQUIVIR",
                    "estado": True,
                    "lluvia_1h": r1h,
                    "precipitacion_1h": r1h,
                    "fecha_1h": now_iso,
                    "lluvia_4h": None,
                    "precipitacion_4h": None,
                    "fecha_4h": now_iso,
                    "lluvia_12h": r12h,
                    "precipitacion_12h": r12h,
                    "fecha_12h": now_iso,
                    "lluvia_24h": None,
                    "precipitacion_24h": None,
                    "fecha_24h": now_iso,
                    "lluvia_hoy": rhoy,
                    "lluvia_ayer": rayer,
                    "lluvia_hora_anterior": r_prev,
                    "ultima_hora": now_iso,
                    "fuente": "S.A.I.H. Guadalquivir (CHG / MITECO)",
                    "unidad": "mm"
                }
                pluvios_list.append(pluvio_dict)

        if pluvios_list:
            self._pluvios = pluvios_list
            self._index_pluvios()
            self._last_pluvios_sync_time = datetime.now()

            DATA_DIR.mkdir(parents=True, exist_ok=True)
            with open(STATIC_GUADALQUIVIR_PLUVIOS_FILE, "w", encoding="utf-8") as f:
                json.dump(self._pluvios, f, ensure_ascii=False, indent=2)

            geojson_data = {
                "type": "FeatureCollection",
                "features": [
                    {"type": "Feature", "geometry": {"type": "Point", "coordinates": [p["lon"], p["lat"]]}, "properties": p}
                    for p in self._pluvios
                ]
            }
            with open(GUADALQUIVIR_PLUVIOS_GEOJSON_FILE, "w", encoding="utf-8") as f:
                json.dump(geojson_data, f, ensure_ascii=False, indent=2)

            if PUBLIC_DATA_DIR.exists():
                try:
                    with open(PUBLIC_DATA_DIR / "guadalquivir_lluvias.geojson", "w", encoding="utf-8") as f:
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
                {"type": "Feature", "geometry": {"type": "Point", "coordinates": [p["lon"], p["lat"]]}, "properties": p}
                for p in pluvios
            ]
        }

    # ==========================================
    # 2. AFOROS Y CAUDALES (AforosTabla.aspx)
    # ==========================================

    def sync_aforos_metadata(self) -> List[Dict[str, Any]]:
        """Descarga niveles y caudales de AforosTabla.aspx y evalúa umbrales de seguridad"""
        if not self._metadata_by_code:
            self.sync_all_metadata()

        metadata = self._metadata_by_code
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        url = f"{GUADALQUIVIR_BASE_URL}/AforosTabla.aspx"
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, context=ctx, timeout=15) as resp:
                page_html = resp.read().decode("utf-8", "ignore")
        except Exception as e:
            logger.error(f"Error descargando aforos de Guadalquivir: {e}")
            return self._aforos

        rows = re.findall(r'<tr[^>]*>(.*?)</tr>', page_html, re.DOTALL)
        now_madrid = datetime.now(MADRID_TZ)
        now_iso = now_madrid.strftime("%Y-%m-%d %H:%M:%S")

        aforos_list = []
        for r in rows:
            tds = re.findall(r"<td[^>]*>(.*?)</td>", r, re.DOTALL)
            clean_tds = [html.unescape(re.sub(r"<[^>]+>", "", t).strip()) for t in tds]
            if len(clean_tds) >= 11:
                nombre_raw = clean_tds[0].strip()
                code_m = re.match(r"^([A-Z][0-9]{2,3})\b", nombre_raw)
                if not code_m:
                    continue
                code = code_m.group(1)

                provincia = clean_tds[1].strip()
                nivel_m = _parse_num(clean_tds[2])
                cota_m = _parse_num(clean_tds[4])
                caudal_m3s = _parse_num(clean_tds[6])

                # Umbrales oficiales de la CHG en m.l.a (metros de nivel)
                aviso_val = _parse_num(clean_tds[8])
                prealerta_val = _parse_num(clean_tds[9])
                alerta_val = _parse_num(clean_tds[10])

                sen_m = re.search(r"IniciaCurva\('([A-Z][0-9]{2,3}_[0-9A-Za-z_]+)'\)", r)
                sensor_code = sen_m.group(1) if sen_m else f"{code}_107"

                meta = metadata.get(code, {})
                lat = meta.get("lat")
                lon = meta.get("lon")

                if lat is None or lon is None:
                    continue

                clean_name = meta.get("nombre") or re.sub(r"^[A-Z][0-9]{2,3}\s*", "", nombre_raw).strip()

                rio_name = ""
                if "Río " in clean_name or "Rio " in clean_name:
                    rio_m = re.search(r"R[íi]o\s+([A-Za-zÁÉÍÓÚáéíóúñ]+)", clean_name)
                    if rio_m:
                        rio_name = f"Río {rio_m.group(1)}"

                effective_nivel = nivel_m if nivel_m is not None else cota_m

                st_dict = {
                    "id_estacion": f"guadal_aforo_{code}",
                    "codigo": f"GUADAL_{code}",
                    "codigo_corto": code,
                    "id_variable": f"guadal_aforo_{code}",
                    "sensor_code": sensor_code,
                    "nombre": f"{code} {clean_name}",
                    "rio": rio_name,
                    "municipio": meta.get("municipio", ""),
                    "provincia": provincia or meta.get("provincia", ""),
                    "subcuenca": "Cuenca Hidrográfica del Guadalquivir",
                    "lat": lat,
                    "lon": lon,
                    "red": "GUADALQUIVIR",
                    "caudal": caudal_m3s,
                    "ultimo_caudal": caudal_m3s,
                    "caudal_actual": caudal_m3s,
                    "lastValue": caudal_m3s,
                    "nivel": effective_nivel,
                    "ultimo_nivel": effective_nivel,
                    "nivel_actual": effective_nivel,
                    "cota": cota_m,
                    "cota_actual": cota_m,
                    "umbrales": {
                        "amarillo": aviso_val,
                        "naranja": prealerta_val,
                        "rojo": alerta_val,
                        "aviso": aviso_val,
                        "prealerta": prealerta_val,
                        "alerta": alerta_val,
                    },
                    "aviso": aviso_val,
                    "prealerta": prealerta_val,
                    "alerta": alerta_val,
                    "tipo_umbral": "nivel",
                    "unidad_umbrales": "m",
                    "ultima_hora": now_iso,
                    "fecha_comunicacion": now_iso,
                    "fuente": "S.A.I.H. Guadalquivir (CHG / MITECO)",
                    "unidad": "m³/s",
                    "unidad_grafica": "m"  # Los umbrales de CHG se evalúan en metros de nivel
                }
                aforos_list.append(st_dict)

        if aforos_list:
            self._aforos = aforos_list
            self._index_aforos()
            self._last_aforos_sync_time = datetime.now()

            DATA_DIR.mkdir(parents=True, exist_ok=True)
            with open(STATIC_GUADALQUIVIR_AFOROS_FILE, "w", encoding="utf-8") as f:
                json.dump(self._aforos, f, ensure_ascii=False, indent=2)

            geojson_data = {
                "type": "FeatureCollection",
                "features": [
                    {"type": "Feature", "geometry": {"type": "Point", "coordinates": [a["lon"], a["lat"]]}, "properties": a}
                    for a in self._aforos
                ]
            }
            with open(GUADALQUIVIR_AFOROS_GEOJSON_FILE, "w", encoding="utf-8") as f:
                json.dump(geojson_data, f, ensure_ascii=False, indent=2)

            if PUBLIC_DATA_DIR.exists():
                try:
                    with open(PUBLIC_DATA_DIR / "guadalquivir_aforos.geojson", "w", encoding="utf-8") as f:
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
                {"type": "Feature", "geometry": {"type": "Point", "coordinates": [a["lon"], a["lat"]]}, "properties": a}
                for a in aforos
            ]
        }

    # ==========================================
    # 3. EMBALSES Y PRESAS (Embal*.aspx)
    # ==========================================

    def sync_embalses_metadata(self) -> List[Dict[str, Any]]:
        """Descarga y calcula el volumen, capacidad y porcentaje de todos los embalses"""
        if not self._metadata_by_code:
            self.sync_all_metadata()

        metadata = self._metadata_by_code
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        pages = ["EmbalJA.aspx", "EmbalCO.aspx", "EmbalGR.aspx", "EmbalSE.aspx"]
        now_madrid = datetime.now(MADRID_TZ)
        now_iso = now_madrid.strftime("%Y-%m-%d %H:%M:%S")

        emb_list = []
        for p in pages:
            url = f"{GUADALQUIVIR_BASE_URL}/{p}"
            try:
                req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
                with urllib.request.urlopen(req, context=ctx, timeout=15) as resp:
                    page_html = resp.read().decode("utf-8", "ignore")
            except Exception as e:
                logger.warning(f"Error descargando embalses de {p}: {e}")
                continue

            tables = re.findall(r'<table id="[^"]*_(E[0-9]+)_tabla"[^>]*>(.*?)</table>', page_html, re.DOTALL)
            for code, tbl in tables:
                caption_m = re.search(r"<caption[^>]*>(.*?)</caption>", tbl, re.DOTALL)
                caption = html.unescape(caption_m.group(1).strip()) if caption_m else code

                volumen: Optional[float] = None
                vol_sensor: Optional[str] = None
                caudal_out: Optional[float] = None
                caudal_sensor: Optional[str] = None

                trs = re.findall(r"<tr[^>]*>(.*?)</tr>", tbl, re.DOTALL)
                for tr in trs:
                    tds = re.findall(r"<td[^>]*>(.*?)</td>", tr, re.DOTALL)
                    if len(tds) >= 3:
                        txt0 = html.unescape(re.sub(r"<[^>]+>", "", tds[0])).strip().lower()
                        txt2 = html.unescape(re.sub(r"<[^>]+>", "", tds[2])).strip()
                        sen_m = re.search(r"IniciaCurva\('([^']+)'\)", tr)
                        sensor = sen_m.group(1) if sen_m else None

                        if "volumen" in txt0:
                            volumen = _parse_num(txt2)
                            vol_sensor = sensor
                        elif "caudal" in txt0:
                            caudal_out = _parse_num(txt2)
                            caudal_sensor = sensor

                meta = metadata.get(code, {})
                lat = meta.get("lat")
                lon = meta.get("lon")
                capacidad = meta.get("capacidad")

                if lat is None or lon is None:
                    continue

                pct: Optional[float] = None
                if volumen is not None and capacidad and capacidad > 0:
                    pct = round((volumen / capacidad) * 100.0, 1)

                clean_name = meta.get("nombre") or re.sub(r"^E[0-9]+\s*", "", caption).strip()

                st_dict = {
                    "id_estacion": f"guadal_emb_{code}",
                    "codigo": f"GUADAL_{code}",
                    "codigo_corto": code,
                    "id_volumen": f"guadal_emb_{code}_vol",
                    "sensor_code": vol_sensor or f"{code}_101",
                    "sensor_caudal": caudal_sensor,
                    "nombre": f"Embalse de {clean_name}" if not clean_name.lower().startswith("embalse") else clean_name,
                    "tipo": "Embalse",
                    "red": "GUADALQUIVIR",
                    "fuente": "S.A.I.H. Guadalquivir (CHG / MITECO)",
                    "lat": lat,
                    "lon": lon,
                    "volumen": volumen,
                    "ultimo_volumen": volumen,
                    "volumen_actual": volumen,
                    "capacidad": capacidad,
                    "capacidad_nmn": capacidad,
                    "porcentaje": pct,
                    "porcentaje_llenado": pct,
                    "caudal": caudal_out,
                    "caudal_salida": caudal_out,
                    "caudal_salida_rio": caudal_out,
                    "municipio": meta.get("municipio", ""),
                    "provincia": meta.get("provincia", ""),
                    "subcuenca": "Cuenca Hidrográfica del Guadalquivir",
                    "ultima_hora": now_iso,
                    "fecha_comunicacion": now_iso,
                    "unidad": "hm³",
                    "unidad_volumen": "hm³",
                    "unidad_caudal": "m³/s"
                }
                emb_list.append(st_dict)

        if emb_list:
            self._embalses = emb_list
            self._index_embalses()
            self._last_embalses_sync_time = datetime.now()

            DATA_DIR.mkdir(parents=True, exist_ok=True)
            with open(STATIC_GUADALQUIVIR_EMBALSES_FILE, "w", encoding="utf-8") as f:
                json.dump(self._embalses, f, ensure_ascii=False, indent=2)

            geojson_data = {
                "type": "FeatureCollection",
                "features": [
                    {"type": "Feature", "geometry": {"type": "Point", "coordinates": [e["lon"], e["lat"]]}, "properties": e}
                    for e in self._embalses
                ]
            }
            with open(GUADALQUIVIR_EMBALSES_GEOJSON_FILE, "w", encoding="utf-8") as f:
                json.dump(geojson_data, f, ensure_ascii=False, indent=2)

            if PUBLIC_DATA_DIR.exists():
                try:
                    with open(PUBLIC_DATA_DIR / "guadalquivir_embalses.geojson", "w", encoding="utf-8") as f:
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
                {"type": "Feature", "geometry": {"type": "Point", "coordinates": [e["lon"], e["lat"]]}, "properties": e}
                for e in embalses
            ]
        }

    # ==========================================
    # 4. SERIES TEMPORALES HISTÓRICAS (saihhist4.aspx)
    # ==========================================

    def _get_opener(self):
        if not hasattr(self, "_opener") or self._opener is None:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            self._cookie_jar = http.cookiejar.CookieJar()
            self._opener = urllib.request.build_opener(
                urllib.request.HTTPSHandler(context=ctx),
                urllib.request.HTTPCookieProcessor(self._cookie_jar)
            )
        return self._opener

    def _ensure_session(self, is_embalse: bool = False):
        opener = self._get_opener()
        referer = f"{GUADALQUIVIR_BASE_URL}/{'EmbalJA.aspx' if is_embalse else 'AforosTabla.aspx'}"
        req = urllib.request.Request(referer, headers={"User-Agent": USER_AGENT})
        try:
            with opener.open(req, timeout=10) as resp:
                _ = resp.read(2048)
        except Exception as e:
            logger.warning(f"Error inicializando sesión Guadalquivir: {e}")

    def _fetch_series_from_web(self, sensor_code: str, hours: int = 24, is_embalse: bool = False) -> List[Dict[str, Any]]:
        """
        Descarga la serie temporal histórica de saihhist4.aspx para un sensor de Guadalquivir,
        calculando el parámetro de días (dia) necesario para abarcar el rango de horas solicitado.
        """
        if not sensor_code:
            return []

        if not hasattr(self, "_cookie_jar") or len(self._cookie_jar) == 0:
            self._ensure_session(is_embalse)

        opener = self._get_opener()
        
        # Calcular el número de días necesarios (dia >= 1)
        req_hours = int(hours) if isinstance(hours, (int, float)) and hours > 0 else 24
        dia = max(1, math.ceil(req_hours / 24.0))

        # El formato oficial de parámetro es "sensor,dia,ano" (ano=0 para modo días)
        param = f"{sensor_code},{dia},0"
        b_code = base64.b64encode(param.encode("utf-8")).decode("ascii")
        url = f"{GUADALQUIVIR_BASE_URL}/saihhist4.aspx?b={b_code}&k=0x8"
        referer = f"{GUADALQUIVIR_BASE_URL}/{'EmbalJA.aspx' if is_embalse else 'AforosTabla.aspx'}"

        headers = {
            "User-Agent": USER_AGENT,
            "Referer": referer
        }
        req = urllib.request.Request(url, headers=headers)

        try:
            with opener.open(req, timeout=12) as resp:
                content = resp.read().decode("utf-8", "ignore")

            # Si la respuesta es vacía o muy corta, refrescar sesión e intentar una vez más
            if len(content) < 60:
                self._ensure_session(is_embalse)
                with opener.open(req, timeout=12) as resp:
                    content = resp.read().decode("utf-8", "ignore")

            root = ET.fromstring(content)
            x_el = root.find(".//x")
            y_el = root.find(".//y")
            if x_el is None or y_el is None or not x_el.text or not y_el.text:
                return []

            timestamps_raw = x_el.text.strip().split(";")
            values_raw = y_el.text.strip().split(";")

            points = []
            for t_str, v_str in zip(timestamps_raw, values_raw):
                if not t_str or not v_str:
                    continue
                try:
                    ts = int(t_str)
                    if ts > 10000000000:
                        ts = ts / 1000.0
                    dt = datetime.fromtimestamp(ts, MADRID_TZ)
                    iso_date = dt.strftime("%Y-%m-%d %H:%M:%S")
                except Exception:
                    continue

                clean_v = v_str.replace(",", ".").strip()
                try:
                    val_float = round(float(clean_v), 3)
                except ValueError:
                    val_float = None

                points.append({
                    "fecha": iso_date,
                    "valor": val_float,
                    "estado": 1,
                    "volumen": val_float if is_embalse else None,
                    "caudal": val_float if not is_embalse else None
                })

            points.sort(key=lambda p: p["fecha"])
            return points

        except Exception as e:
            logger.error(f"Error al descargar serie temporal Guadalquivir ({sensor_code}, dia={dia}): {e}")
            return []

    async def get_history(
        self,
        id_or_code: str,
        hours: int = 24,
        is_embalse: bool = False
    ) -> Dict[str, Any]:
        """
        Consulta la serie temporal histórica para un aforo o embalse de Guadalquivir.
        Devuelve el formato estándar compatible con RainLoc y SAIH CHJ/Hidrosur.
        """
        sensor_code = None
        station_name = str(id_or_code)
        unidad = "hm³" if is_embalse else "m"
        station_obj = None

        if is_embalse:
            await self.get_embalses()
            emb = (
                self._embalses_by_id.get(str(id_or_code).upper())
                or self._embalses_by_id.get(str(id_or_code))
            )
            if emb:
                sensor_code = emb.get("sensor_code")
                station_name = emb.get("nombre", station_name)
                station_obj = emb
        else:
            await self.get_caudales()
            aforo = (
                self._aforos_by_id.get(str(id_or_code).upper())
                or self._aforos_by_id.get(str(id_or_code))
            )
            if aforo:
                sensor_code = aforo.get("sensor_code")
                station_name = aforo.get("nombre", station_name)
                station_obj = aforo

        if not sensor_code:
            clean_code = str(id_or_code).replace("GUADAL_", "").replace("guadal_", "").replace("emb_", "").replace("aforo_", "").upper()
            if clean_code.startswith("E"):
                sensor_code = f"{clean_code}_101"
                is_embalse = True
                unidad = "hm³"
            elif clean_code.startswith("A"):
                sensor_code = f"{clean_code}_107"
                is_embalse = False
                unidad = "m"
            else:
                sensor_code = str(id_or_code)

        req_hours = int(hours) if isinstance(hours, (int, float)) and hours > 0 else 24
        dia = max(1, math.ceil(req_hours / 24.0))

        cache_key = f"{sensor_code}_{'emb' if is_embalse else 'aforo'}"
        now = datetime.now(MADRID_TZ)

        if not hasattr(self, "_history_series_cache"):
            self._history_series_cache = {}

        cached = self._history_series_cache.get(cache_key)
        if cached and (now - cached["ts"]).total_seconds() < 300 and cached.get("dia", 1) >= dia:
            full_series = cached["data"]
        else:
            full_series = await asyncio.to_thread(self._fetch_series_from_web, sensor_code, req_hours, is_embalse)
            if full_series:
                self._history_series_cache[cache_key] = {"ts": now, "dia": dia, "data": full_series}

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
            "red": "GUADALQUIVIR",
            "fuente": "S.A.I.H. Guadalquivir (CHG / MITECO)",
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


guadalquivir_service = GuadalquivirService()
