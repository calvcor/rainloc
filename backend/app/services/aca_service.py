"""
Servicio de integración de datos hidrológicos de la ACA (Agència Catalana de l'Aigua)
Cubre las Cuencas Internas de Cataluña (Conques Internes de Catalunya).
Proporciona en tiempo real:
 - Caudales y niveles en ríos (84 estaciones de aforo).
 - Estado de embalses y presas (11 embalses).
 - Series temporales históricas a resolución de 5 minutos para caudales y volúmenes.
"""

import asyncio
import json
import logging
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger("rainloc.aca_service")

MADRID_TZ = ZoneInfo("Europe/Madrid")
UTC_TZ = timezone.utc
SYNC_TTL_SECONDS = 300  # 5 minutos de frescura en caché

ACA_BASE_URL = "https://aplicacions.aca.gencat.cat/aetr/vishid/v2"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 RainLoc/3.0"

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
PUBLIC_DATA_DIR = Path(__file__).resolve().parent.parent.parent.parent / "public" / "data"

STATIC_ACA_AFOROS_FILE = DATA_DIR / "aca_aforos_estaciones.json"
STATIC_ACA_EMBALSES_FILE = DATA_DIR / "aca_embalses_estaciones.json"

ACA_AFOROS_GEOJSON_FILE = DATA_DIR / "aca_aforos.geojson"
ACA_EMBALSES_GEOJSON_FILE = DATA_DIR / "aca_embalses.geojson"


def _parse_coords(point_str: Optional[str]) -> tuple[Optional[float], Optional[float]]:
    """Parsea una cadena 'lat lon' o 'lat,lon' a (lat, lon) floats."""
    if not point_str or not isinstance(point_str, str):
        return None, None
    parts = point_str.strip().replace(",", " ").split()
    if len(parts) >= 2:
        try:
            lat = float(parts[0])
            lon = float(parts[1])
            return round(lat, 6), round(lon, 6)
        except (ValueError, TypeError):
            return None, None
    return None, None


def _parse_float(val: Any) -> Optional[float]:
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return round(float(val), 3)
    s = str(val).replace(",", ".").replace("hm³", "").replace("m³/s", "").replace("msnm", "").replace("%", "").strip()
    try:
        return round(float(s), 3)
    except (ValueError, TypeError):
        return None


def _format_madrid_iso(iso_utc_str: Optional[str]) -> str:
    """Convierte una marca temporal UTC ISO 8601 a hora local de Madrid 'YYYY-MM-DD HH:mm:ss'."""
    if not iso_utc_str:
        return datetime.now(MADRID_TZ).strftime("%Y-%m-%d %H:%M:%S")
    try:
        clean_str = iso_utc_str.replace("Z", "+00:00")
        if "+0000" in clean_str:
            clean_str = clean_str.replace("+0000", "+00:00")
        dt_utc = datetime.fromisoformat(clean_str)
        dt_madrid = dt_utc.astimezone(MADRID_TZ)
        return dt_madrid.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(iso_utc_str)[:19].replace("T", " ")


class ACAService:
    def __init__(self):
        self._aforos: List[Dict[str, Any]] = []
        self._embalses: List[Dict[str, Any]] = []

        self._aforos_by_id: Dict[str, Dict[str, Any]] = {}
        self._embalses_by_id: Dict[str, Dict[str, Any]] = {}

        self._last_aforos_sync_time: Optional[datetime] = None
        self._last_embalses_sync_time: Optional[datetime] = None

        self._aforos_catalog: Dict[str, Dict[str, Any]] = {}
        self._embalses_catalog: Dict[str, Dict[str, Any]] = {}

        self._sync_lock = asyncio.Lock()
        self._load_cached_files()

    def _load_cached_files(self):
        """Carga datos cacheados desde disco si existen."""
        if STATIC_ACA_AFOROS_FILE.exists():
            try:
                with open(STATIC_ACA_AFOROS_FILE, "r", encoding="utf-8") as f:
                    self._aforos = json.load(f)
                    self._index_aforos()
                    self._last_aforos_sync_time = datetime.fromtimestamp(STATIC_ACA_AFOROS_FILE.stat().st_mtime)
                    logger.info(f"Cargadas {len(self._aforos)} estaciones de caudal de la ACA desde caché.")
            except Exception as e:
                logger.warning(f"Error cargando caché de aforos ACA: {e}")

        if STATIC_ACA_EMBALSES_FILE.exists():
            try:
                with open(STATIC_ACA_EMBALSES_FILE, "r", encoding="utf-8") as f:
                    self._embalses = json.load(f)
                    self._index_embalses()
                    self._last_embalses_sync_time = datetime.fromtimestamp(STATIC_ACA_EMBALSES_FILE.stat().st_mtime)
                    logger.info(f"Cargados {len(self._embalses)} embalses de la ACA desde caché.")
            except Exception as e:
                logger.warning(f"Error cargando caché de embalses ACA: {e}")

    def _index_aforos(self):
        self._aforos_by_id.clear()
        for st in self._aforos:
            id_v = st.get("id_variable")
            if id_v:
                self._aforos_by_id[id_v] = st
                self._aforos_by_id[id_v.upper()] = st
            code = st.get("codigo")
            if code:
                self._aforos_by_id[code] = st
                self._aforos_by_id[code.upper()] = st
            c_short = st.get("codigo_corto")
            if c_short:
                self._aforos_by_id[c_short] = st
                self._aforos_by_id[f"aca_aforo_{c_short}"] = st

    def _index_embalses(self):
        self._embalses_by_id.clear()
        for emb in self._embalses:
            id_e = emb.get("id_estacion")
            if id_e:
                self._embalses_by_id[id_e] = emb
                self._embalses_by_id[id_e.upper()] = emb
            code = emb.get("codigo")
            if code:
                self._embalses_by_id[code] = emb
                self._embalses_by_id[code.upper()] = emb
            c_short = emb.get("codigo_corto")
            if c_short:
                self._embalses_by_id[c_short] = emb
                self._embalses_by_id[f"aca_embalse_{c_short}"] = emb

    # ==========================================
    # 1. AFOROS Y CAUDALES EN RÍOS
    # ==========================================

    def sync_aforos_metadata(self) -> List[Dict[str, Any]]:
        """
        Sincroniza todas las estaciones de aforo de ríos de la ACA:
        - Catálogo estático (metadatos, nombres, cuencas, ríos)
        - Alertas y umbrales de retorno (T2, T5, T10)
        - Lecturas de caudal y nivel en tiempo real
        """
        now_madrid = datetime.now(MADRID_TZ)
        now_iso = now_madrid.strftime("%Y-%m-%d %H:%M:%S")

        # 1. Catálogo
        if not self._aforos_catalog:
            try:
                url_cat = f"{ACA_BASE_URL}/catalog/public/rivergauges?json=false"
                req_cat = urllib.request.Request(url_cat, headers={"User-Agent": USER_AGENT})
                with urllib.request.urlopen(req_cat, timeout=12) as resp:
                    self._aforos_catalog = json.loads(resp.read().decode("utf-8"))
            except Exception as e:
                logger.warning(f"Error descargando catálogo de aforos ACA: {e}")

        # 2. Umbrales de retorno (T2, T5, T10, T25, T50)
        thresholds_by_code: Dict[str, Dict[str, Any]] = {}
        try:
            url_alert = f"{ACA_BASE_URL}/alerts/rivergauges"
            req_alert = urllib.request.Request(url_alert, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req_alert, timeout=12) as resp:
                alerts_data = json.loads(resp.read().decode("utf-8"))
                if isinstance(alerts_data, dict):
                    for code, info in alerts_data.items():
                        if isinstance(info, dict):
                            for k, v in info.items():
                                if isinstance(v, list) and len(v) >= 4:
                                    # v = [caudal_actual, T2, T5, T10, T25, T50]
                                    thresholds_by_code[code] = {
                                        "caudal_alert": _parse_float(v[0]),
                                        "amarillo": _parse_float(v[1]),
                                        "naranja": _parse_float(v[2]),
                                        "rojo": _parse_float(v[3]),
                                    }
                                    break
        except Exception as e:
            logger.warning(f"Error descargando alertas/umbrales ACA: {e}")

        # 3. Lecturas en tiempo real de todas las estaciones
        live_data_by_code: Dict[str, Dict[str, Any]] = {}
        try:
            # Una llamada a una estación existente devuelve el lote entero de 84 estaciones
            sample_code = "171812-001"
            url_live = f"{ACA_BASE_URL}/data/public/rivergauges/{sample_code}"
            req_live = urllib.request.Request(url_live, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req_live, timeout=12) as resp:
                live_batch = json.loads(resp.read().decode("utf-8"))
                if isinstance(live_batch, dict):
                    live_data_by_code = live_batch
        except Exception as e:
            logger.warning(f"Error descargando lecturas en tiempo real de aforos ACA: {e}")

        # 4. Consolidar dataset unificado
        all_codes = set(self._aforos_catalog.keys()) | set(live_data_by_code.keys())
        aforos_list = []

        for code in all_codes:
            meta = self._aforos_catalog.get(code, {})
            live = live_data_by_code.get(code, {})
            thresh = thresholds_by_code.get(code, {})

            # Coordenadas
            loc_str = live.get("location") or meta.get("point")
            lat, lon = _parse_coords(loc_str)
            if lat is None or lon is None:
                continue

            # Valores en tiempo real
            popup = live.get("popup", {})
            q_obj = popup.get("river_flow", {}) if isinstance(popup, dict) else {}
            lvl_obj = popup.get("river_level", {}) if isinstance(popup, dict) else {}

            caudal_val = _parse_float(q_obj.get("value")) if q_obj else thresh.get("caudal_alert")
            nivel_val = _parse_float(lvl_obj.get("value")) if lvl_obj else None
            nivel_unit = lvl_obj.get("unit") or "cm"
            
            # Normalizar nivel a metros si viene en cm
            nivel_m = round(nivel_val / 100.0, 3) if (nivel_val is not None and nivel_unit == "cm") else nivel_val

            raw_time = q_obj.get("time") or lvl_obj.get("time") or live.get("time")
            update_time = _format_madrid_iso(raw_time)

            name = meta.get("name") or live.get("name") or f"Aforament {code}"
            river = meta.get("waterbody") or meta.get("river") or ""
            subbasin = meta.get("subbasin") or meta.get("basin") or "Conques Internes de Catalunya"
            municipality = meta.get("municipality") or ""
            comarca = meta.get("comarca") or ""

            umbrales = {
                "amarillo": thresh.get("amarillo"),
                "naranja": thresh.get("naranja"),
                "rojo": thresh.get("rojo"),
            }

            st_dict = {
                "id_estacion": f"aca_aforo_{code}",
                "codigo": f"ACA_{code}",
                "codigo_corto": code,
                "id_variable": f"aca_aforo_{code}",
                "nombre": name,
                "rio": river,
                "municipio": municipality,
                "poblacion": municipality,
                "comarca": comarca,
                "provincia": meta.get("province", "Catalunya"),
                "cuenca": "Conques Internes de Catalunya",
                "subcuenca": subbasin,
                "lat": lat,
                "lon": lon,
                "red": "ACA",
                "caudal": caudal_val,
                "ultimo_caudal": caudal_val,
                "caudal_actual": caudal_val,
                "lastValue": caudal_val,
                "nivel": nivel_m,
                "ultimo_nivel": nivel_m,
                "nivel_actual": nivel_m,
                "nivel_cm": nivel_val if nivel_unit == "cm" else (round(nivel_val * 100, 1) if nivel_val else None),
                "umbrales": umbrales,
                "aviso": umbrales.get("amarillo"),
                "prealerta": umbrales.get("naranja"),
                "alerta": umbrales.get("rojo"),
                "tipo_umbral": "caudal",
                "unidad_umbrales": "m³/s",
                "unidad_grafica": "m³/s",
                "ultima_hora": update_time,
                "fecha_comunicacion": update_time,
                "fuente": "Agència Catalana de l'Aigua (ACA)",
                "unidad": "m³/s",
                "unidad_caudal": "m³/s",
                "unidad_nivel": "m",
            }
            aforos_list.append(st_dict)

        if aforos_list:
            self._aforos = aforos_list
            self._index_aforos()
            self._last_aforos_sync_time = datetime.now()

            DATA_DIR.mkdir(parents=True, exist_ok=True)
            with open(STATIC_ACA_AFOROS_FILE, "w", encoding="utf-8") as f:
                json.dump(self._aforos, f, ensure_ascii=False, indent=2)

            geojson_data = {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "geometry": {"type": "Point", "coordinates": [st["lon"], st["lat"]]},
                        "properties": st,
                    }
                    for st in self._aforos
                ],
            }
            with open(ACA_AFOROS_GEOJSON_FILE, "w", encoding="utf-8") as f:
                json.dump(geojson_data, f, ensure_ascii=False, indent=2)

            if PUBLIC_DATA_DIR.exists():
                try:
                    with open(PUBLIC_DATA_DIR / "aca_aforos.geojson", "w", encoding="utf-8") as f:
                        json.dump(geojson_data, f, ensure_ascii=False, indent=2)
                except Exception:
                    pass

            logger.info(f"Sincronizados {len(self._aforos)} aforos de caudal de la ACA correctamente.")

        return self._aforos

    async def get_caudales(self) -> List[Dict[str, Any]]:
        if not self._last_aforos_sync_time or (datetime.now() - self._last_aforos_sync_time).total_seconds() >= SYNC_TTL_SECONDS:
            async with self._sync_lock:
                if not self._last_aforos_sync_time or (datetime.now() - self._last_aforos_sync_time).total_seconds() >= SYNC_TTL_SECONDS:
                    await asyncio.to_thread(self.sync_aforos_metadata)
        return self._aforos

    async def get_caudales_geojson(self) -> Dict[str, Any]:
        caudales = await self.get_caudales()
        return {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [st["lon"], st["lat"]]},
                    "properties": st,
                }
                for st in caudales
            ],
        }

    # ==========================================
    # 2. EMBALSES Y PRESAS
    # ==========================================

    def sync_embalses_metadata(self) -> List[Dict[str, Any]]:
        """
        Sincroniza los 11 embalses principales de las Cuencas Internas de Cataluña:
        - Catálogo estático (capacidad máxima, río, comarca, municipio)
        - Lectura en tiempo real (volumen hm³, porcentaje llenado, cota msnm)
        """
        now_madrid = datetime.now(MADRID_TZ)
        now_iso = now_madrid.strftime("%Y-%m-%d %H:%M:%S")

        # 1. Catálogo
        if not self._embalses_catalog:
            try:
                url_cat = f"{ACA_BASE_URL}/catalog/public/reservoir?json=false"
                req_cat = urllib.request.Request(url_cat, headers={"User-Agent": USER_AGENT})
                with urllib.request.urlopen(req_cat, timeout=12) as resp:
                    self._embalses_catalog = json.loads(resp.read().decode("utf-8"))
            except Exception as e:
                logger.warning(f"Error descargando catálogo de embalses ACA: {e}")

        # 2. Lecturas en tiempo real
        live_embalses_by_code: Dict[str, Dict[str, Any]] = {}
        try:
            sample_code = "080581-002"
            url_live = f"{ACA_BASE_URL}/data/public/reservoir/{sample_code}"
            req_live = urllib.request.Request(url_live, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req_live, timeout=12) as resp:
                live_batch = json.loads(resp.read().decode("utf-8"))
                if isinstance(live_batch, dict):
                    live_embalses_by_code = live_batch
        except Exception as e:
            logger.warning(f"Error descargando lecturas en tiempo real de embalses ACA: {e}")

        # 3. Consolidar
        all_codes = set(self._embalses_catalog.keys()) | set(live_embalses_by_code.keys())
        emb_list = []

        for code in all_codes:
            meta = self._embalses_catalog.get(code, {})
            live = live_embalses_by_code.get(code, {})
            add_info = meta.get("addicionalInfo", {}) if isinstance(meta.get("addicionalInfo"), dict) else {}

            loc_str = live.get("location") or meta.get("location") or meta.get("point")
            lat, lon = _parse_coords(loc_str)
            if lat is None or lon is None:
                continue

            popup = live.get("popup", {}) if isinstance(live.get("popup"), dict) else {}
            vol_obj = popup.get("volume", {})
            cap_obj = popup.get("capacity", {})
            lvl_obj = popup.get("level", {})

            vol_actual = _parse_float(vol_obj.get("value"))
            pct_actual = _parse_float(cap_obj.get("value")) if cap_obj else _parse_float(live.get("value"))
            cota_actual = _parse_float(lvl_obj.get("value"))

            cap_max_raw = meta.get("maximumCapacity") or add_info.get("Capacitat màxima embassament")
            cap_max = _parse_float(cap_max_raw)

            # Si falta porcentaje y tenemos volumen y capacidad máxima:
            if pct_actual is None and vol_actual is not None and cap_max and cap_max > 0:
                pct_actual = round((vol_actual / cap_max) * 100, 1)

            # Si falta volumen y tenemos porcentaje y capacidad:
            if vol_actual is None and pct_actual is not None and cap_max and cap_max > 0:
                vol_actual = round((pct_actual / 100.0) * cap_max, 2)

            raw_time = vol_obj.get("time") or cap_obj.get("time") or lvl_obj.get("time") or live.get("time")
            update_time = _format_madrid_iso(raw_time)

            name = meta.get("name") or live.get("name") or f"Embassament {code}"
            subcuenca = add_info.get("Subconca") or add_info.get("Conca") or meta.get("subbasin") or meta.get("basin") or "Cuencas Internas de Cataluña"
            municipio = add_info.get("Terme municipal") or meta.get("municipality") or ""
            provincia = add_info.get("Província") or meta.get("province") or "Catalunya"
            comarca = add_info.get("Comarca") or meta.get("comarca") or ""
            rio = add_info.get("Riu") or meta.get("waterbody") or ""

            st_dict = {
                "id_estacion": f"aca_embalse_{code}",
                "codigo": f"ACA_{code}",
                "codigo_corto": code,
                "id_variable": f"aca_embalse_{code}",
                "id_volumen": f"aca_embalse_{code}_vol",
                "id_cota": f"aca_embalse_{code}_cota",
                "nombre": name,
                "tipo": "Embalse",
                "lat": lat,
                "lon": lon,
                "red": "ACA",
                "cuenca": "Conques Internes de Catalunya",
                "subcuenca": subcuenca,
                "rio": rio,
                "municipio": municipio,
                "poblacion": municipio,
                "comarca": comarca,
                "provincia": provincia,
                "ultimo_volumen": vol_actual,
                "volumen_actual": vol_actual,
                "capacidad": cap_max,
                "capacidad_nmn": cap_max,
                "porcentaje": pct_actual,
                "porcentaje_llenado": pct_actual,
                "cota_actual": cota_actual,
                "ultima_hora": update_time,
                "fecha_comunicacion": update_time,
                "fuente": "Agència Catalana de l'Aigua (ACA)",
                "unidad": "hm³",
                "unidad_volumen": "hm³",
                "unidad_cota": "msnm",
            }
            emb_list.append(st_dict)

        if emb_list:
            self._embalses = emb_list
            self._index_embalses()
            self._last_embalses_sync_time = datetime.now()

            DATA_DIR.mkdir(parents=True, exist_ok=True)
            with open(STATIC_ACA_EMBALSES_FILE, "w", encoding="utf-8") as f:
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
            with open(ACA_EMBALSES_GEOJSON_FILE, "w", encoding="utf-8") as f:
                json.dump(geojson_data, f, ensure_ascii=False, indent=2)

            if PUBLIC_DATA_DIR.exists():
                try:
                    with open(PUBLIC_DATA_DIR / "aca_embalses.geojson", "w", encoding="utf-8") as f:
                        json.dump(geojson_data, f, ensure_ascii=False, indent=2)
                except Exception:
                    pass

            logger.info(f"Sincronizados {len(self._embalses)} embalses de la ACA correctamente.")

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
    # 3. HISTÓRICO Y SERIES TEMPORALES
    # ==========================================

    async def get_history(
        self,
        id_variable: str,
        hours: int = 24,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        variable_type: Optional[str] = None,
        is_embalse: bool = False,
    ) -> Dict[str, Any]:
        """
        Consulta la serie temporal histórica (paso de 5 min) de aforos o embalses en la API de la ACA.
        """
        raw_code = (
            str(id_variable)
            .replace("aca_aforo_", "")
            .replace("aca_embalse_", "")
            .replace("ACA_", "")
            .strip()
        )
        if raw_code.endswith("_vol") or raw_code.endswith("_cota"):
            raw_code = raw_code.rsplit("_", 1)[0]

        now_madrid = datetime.now(MADRID_TZ)
        if end_date:
            try:
                dt_e = datetime.fromisoformat(str(end_date).replace(" ", "T"))
                if dt_e.tzinfo is None:
                    dt_e = dt_e.replace(tzinfo=MADRID_TZ)
                end_dt = dt_e
            except Exception:
                end_dt = now_madrid
        else:
            end_dt = now_madrid

        if start_date:
            try:
                dt_s = datetime.fromisoformat(str(start_date).replace(" ", "T"))
                if dt_s.tzinfo is None:
                    dt_s = dt_s.replace(tzinfo=MADRID_TZ)
                start_dt = dt_s
            except Exception:
                start_dt = end_dt - timedelta(hours=int(hours) if isinstance(hours, (int, float)) else 24)
        else:
            start_dt = end_dt - timedelta(hours=int(hours) if isinstance(hours, (int, float)) else 24)

        start_iso = start_dt.strftime("%Y-%m-%dT%H:%M:%S")
        end_iso = end_dt.strftime("%Y-%m-%dT%H:%M:%S")

        is_emb = (
            is_embalse
            or id_variable.startswith("aca_embalse_")
            or raw_code in self._embalses_by_id
            or raw_code in self._embalses_catalog
        )

        endpoint_type = "reservoir" if is_emb else "rivergauges"
        url = f"{ACA_BASE_URL}/chart/public/{endpoint_type}/{raw_code}?from={start_iso}&to={end_iso}"

        def _fetch():
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            try:
                with urllib.request.urlopen(req, timeout=12) as resp:
                    if resp.status == 200:
                        return json.loads(resp.read().decode("utf-8"))
            except Exception as e:
                logger.warning(f"Error consultando histórico ACA ({endpoint_type}/{raw_code}): {e}")
            return {}

        raw_data = await asyncio.to_thread(_fetch)
        series = []

        if isinstance(raw_data, dict):
            # Para aforos de caudal: buscar la señal de caudal (generalmente CALC...)
            # Para embalses: buscar volumen, capacidad o nivel según variable_type
            target_signal_key = None
            if not is_emb:
                # Elegir la señal con valores de caudal (priorizar CALC...)
                for k in raw_data.keys():
                    if k.startswith("CALC") or "Q" in k or "CAUDAL" in k:
                        target_signal_key = k
                        break
                if not target_signal_key and len(raw_data) > 0:
                    target_signal_key = list(raw_data.keys())[0]
            else:
                # Para embalse:
                meta_emb = self._embalses_catalog.get(raw_code, {})
                sensors = meta_emb.get("sensors", {})
                vt = str(variable_type).lower() if variable_type else "volumen"
                desired_type = "capacity" if "porcent" in vt or "%" in vt else ("absolute_level" if "cota" in vt or "level" in vt else "volume")
                for s_key, s_info in sensors.items():
                    if isinstance(s_info, dict) and s_info.get("type") == desired_type:
                        target_signal_key = s_key
                        break
                if not target_signal_key:
                    for k in raw_data.keys():
                        if desired_type == "volume" and (k.startswith("CALC") or "VOL" in k):
                            target_signal_key = k
                            break
                        elif desired_type == "absolute_level" and ("ANA" in k or "NIV" in k):
                            target_signal_key = k
                            break
                        elif desired_type == "capacity" and ("CAP" in k or k.startswith("CALC")):
                            target_signal_key = k
                            break
                if not target_signal_key and len(raw_data) > 0:
                    target_signal_key = list(raw_data.keys())[0]

            if target_signal_key and target_signal_key in raw_data:
                points_dict = raw_data[target_signal_key]
                if isinstance(points_dict, dict):
                    for t_str, val in points_dict.items():
                        v_float = _parse_float(val)
                        if v_float is not None:
                            madrid_dt_str = _format_madrid_iso(t_str)
                            series.append({
                                "fecha": madrid_dt_str,
                                "valor": v_float,
                                "estado": 0,
                            })

        # Ordenar cronológicamente
        series.sort(key=lambda x: str(x.get("fecha") or ""))

        station_info = self._embalses_by_id.get(raw_code) if is_emb else self._aforos_by_id.get(raw_code)

        return {
            "id_variable": str(id_variable),
            "estacion": station_info,
            "rango": {
                "desde": start_dt.strftime("%Y-%m-%d %H:%M:%S"),
                "hasta": end_dt.strftime("%Y-%m-%d %H:%M:%S"),
                "horas": hours,
            },
            "puntos_totales": len(series),
            "serie": series,
        }


aca_service = ACAService()
