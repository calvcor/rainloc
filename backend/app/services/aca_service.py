"""
Servicio de integración de datos hidrológicos de la ACA (Agència Catalana de l'Aigua)
Cubre las Cuencas Internas de Cataluña (Conques Internes de Catalunya).
Proporciona en tiempo real:
 - Caudales y niveles en ríos (84 estaciones de aforo).
 - Estado de embalses y presas (11 embalses).
 - Series temporales históricas para caudales y volúmenes.
Incorpora arquitectura híbrida resiliente:
 1. Conexión de baja latencia con el visor AETR oficial de la ACA (aplicacions.aca.gencat.cat).
 2. Fallback automático y sincronización en tiempo real con la API Open Data oficial de la Generalitat de Catalunya:
    - Rius i cabals: https://analisi.transparenciacatalunya.cat/resource/hprm-xuw7.json
    - Embassaments: https://analisi.transparenciacatalunya.cat/resource/gn9e-3qhr.json
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
SOCRATA_AFOROS_URL = "https://analisi.transparenciacatalunya.cat/resource/hprm-xuw7.json"
SOCRATA_EMBALSES_URL = "https://analisi.transparenciacatalunya.cat/resource/gn9e-3qhr.json"

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 RainLoc/3.0"

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
PUBLIC_DATA_DIR = Path(__file__).resolve().parent.parent.parent.parent / "data"

STATIC_ACA_AFOROS_FILE = DATA_DIR / "aca_aforos_estaciones.json"
STATIC_ACA_EMBALSES_FILE = DATA_DIR / "aca_embalses_estaciones.json"

ACA_AFOROS_GEOJSON_FILE = DATA_DIR / "aca_aforos.geojson"
ACA_EMBALSES_GEOJSON_FILE = DATA_DIR / "aca_embalses.geojson"

SOCRATA_EMBALSES_MAP: Dict[str, str] = {
    "170600-001": "Embassament de Darnius Boadella (Darnius)",
    "080581-002": "Embassament de Foix (Castellet i la Gornal)",
    "082687-001": "Embassament de la Baells (Cercs)",
    "081419-003": "Embassament de la Llosa del Cavall (Navès)",
    "430537-001": "Embassament de Riudecanyes",
    "250753-004": "Embassament de Sant Ponç (Clariana de Cardener)",
    "083036-001": "Embassament de Sau (Vilanova de Sau)",
    "430496-001": "Embassament de Siurana (Cornudella de Montsant)",
    "171169-001": "Embassament de Susqueda (Osor)",
}


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


def _format_madrid_iso(iso_str: Optional[str]) -> str:
    """Convierte una marca temporal a formato local 'YYYY-MM-DD HH:mm:ss'."""
    if not iso_str:
        return datetime.now(MADRID_TZ).strftime("%Y-%m-%d %H:%M:%S")
    clean = str(iso_str).strip()
    if "T" in clean:
        clean = clean.split(".")[0].replace("T", " ")
        if len(clean) == 10:
            clean += " 00:00:00"
        return clean[:19]
    return clean[:19]


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

        self._socrata_aforos_map: Dict[str, str] = {}
        self._sync_lock = asyncio.Lock()
        self._load_cached_files()
        self._load_socrata_mapping()

    def _load_socrata_mapping(self):
        mapping_file = DATA_DIR / "aca_socrata_mapping.json"
        if mapping_file.exists():
            try:
                with open(mapping_file, "r", encoding="utf-8") as f:
                    self._socrata_aforos_map = json.load(f)
            except Exception as e:
                logger.warning(f"Error cargando aca_socrata_mapping.json: {e}")

    def _get_socrata_aforo_code(self, raw_code: str) -> Optional[str]:
        if raw_code in self._socrata_aforos_map:
            return self._socrata_aforos_map[raw_code]
        clean = raw_code.replace("ACA_", "").replace("aca_aforo_", "")
        if clean in self._socrata_aforos_map:
            return self._socrata_aforos_map[clean]
        # Búsqueda por estación
        st = self._aforos_by_id.get(clean) or self._aforos_by_id.get(raw_code)
        if st and st.get("codigo_corto") in self._socrata_aforos_map:
            return self._socrata_aforos_map[st["codigo_corto"]]
        return None

    def _get_socrata_embalse_name(self, raw_code: str) -> Optional[str]:
        clean = raw_code.replace("ACA_", "").replace("aca_embalse_", "").replace("_vol", "").replace("_cota", "")
        if clean in SOCRATA_EMBALSES_MAP:
            return SOCRATA_EMBALSES_MAP[clean]
        emb = self._embalses_by_id.get(clean) or self._embalses_by_id.get(raw_code)
        if emb:
            c_short = emb.get("codigo_corto")
            if c_short in SOCRATA_EMBALSES_MAP:
                return SOCRATA_EMBALSES_MAP[c_short]
            lname = str(emb.get("nombre", "")).lower()
            for sname in SOCRATA_EMBALSES_MAP.values():
                key = lname.replace("embassament de ", "").replace("embassament del ", "").split("(")[0].strip()
                if key and key in sname.lower():
                    return sname
        return None

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

    def _fetch_socrata_aforos_readings(self) -> Dict[str, Dict[str, Any]]:
        """Descarga lecturas en tiempo real de caudales y niveles desde el portal Open Data de la Generalitat."""
        url = f"{SOCRATA_AFOROS_URL}?$order=dia%20DESC&$limit=400"
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        readings_by_code: Dict[str, Dict[str, Any]] = {}
        try:
            with urllib.request.urlopen(req, timeout=6) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                for item in data:
                    c = item.get("codi_estacio")
                    if not c:
                        continue
                    if c not in readings_by_code:
                        readings_by_code[c] = {}
                    vtype = item.get("tipus_variable")
                    val = _parse_float(item.get("valor"))
                    dia = item.get("dia")
                    if vtype == "Cabal riu" and "caudal" not in readings_by_code[c]:
                        readings_by_code[c]["caudal"] = val
                        readings_by_code[c]["time"] = dia
                    elif vtype == "Nivell riu" and "nivel" not in readings_by_code[c]:
                        readings_by_code[c]["nivel"] = val
                        if "time" not in readings_by_code[c]:
                            readings_by_code[c]["time"] = dia
        except Exception as e:
            logger.warning(f"Error descargando datos de aforos ACA desde Socrata Open Data: {e}")
        return readings_by_code

    def _fetch_socrata_embalses_readings(self) -> Dict[str, Dict[str, Any]]:
        """Descarga lecturas de volumen, cota y llenado de embalses desde el portal Open Data de la Generalitat."""
        url = f"{SOCRATA_EMBALSES_URL}?$order=dia%20DESC&$limit=50"
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        readings_by_name: Dict[str, Dict[str, Any]] = {}
        try:
            with urllib.request.urlopen(req, timeout=6) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                for item in data:
                    name = item.get("estaci")
                    if name and name not in readings_by_name:
                        readings_by_name[name] = {
                            "vol": _parse_float(item.get("volum_embassat")),
                            "pct": _parse_float(item.get("percentatge_volum_embassat")),
                            "cota": _parse_float(item.get("nivell_absolut")),
                            "time": item.get("dia"),
                        }
        except Exception as e:
            logger.warning(f"Error descargando datos de embalses ACA desde Socrata Open Data: {e}")
        return readings_by_name

    # ==========================================
    # 1. AFOROS Y CAUDALES EN RÍOS
    # ==========================================

    def sync_aforos_metadata(self) -> List[Dict[str, Any]]:
        """
        Sincroniza todas las estaciones de aforo de ríos de la ACA de manera resiliente:
        1. Intenta consulta rápida al visor AETR v2.
        2. Si no responde, recurre a Open Data Generalitat (hprm-xuw7).
        3. Preserva siempre los últimos datos válidos para evitar caídas a 'None' o puntos grises.
        """
        now_madrid = datetime.now(MADRID_TZ)
        now_iso = now_madrid.strftime("%Y-%m-%d %H:%M:%S")
        now_ts = int(now_madrid.timestamp())

        # 1. Intentar AETR v2 con timeout estricto de 2.5s
        live_data_by_code: Dict[str, Dict[str, Any]] = {}
        try:
            sample_code = "171812-001"
            url_live = f"{ACA_BASE_URL}/data/public/rivergauges/{sample_code}?_t={now_ts}"
            req_live = urllib.request.Request(url_live, headers={"User-Agent": USER_AGENT, "Cache-Control": "no-cache"})
            with urllib.request.urlopen(req_live, timeout=2.5) as resp:
                live_batch = json.loads(resp.read().decode("utf-8"))
                if isinstance(live_batch, dict):
                    live_data_by_code = live_batch
        except Exception as e:
            logger.debug(f"AETR v2 aforos no disponible ({e}), utilizando fallback Open Data Generalitat.")

        # 2. Si AETR no respondió o está incompleto, consultar Socrata
        socrata_readings = {}
        if not live_data_by_code:
            socrata_readings = self._fetch_socrata_aforos_readings()

        # Si tenemos lista base en caché, actualizar sobre ella para conservar metadatos y coordenadas
        existing_list = list(self._aforos)
        if not existing_list and STATIC_ACA_AFOROS_FILE.exists():
            try:
                with open(STATIC_ACA_AFOROS_FILE, "r", encoding="utf-8") as f:
                    existing_list = json.load(f)
            except Exception:
                pass

        if not existing_list:
            logger.warning("No hay lista base de aforos ACA disponible para sincronizar.")
            return self._aforos

        updated_list = []
        for st in existing_list:
            st_dict = dict(st)
            code_short = st_dict.get("codigo_corto") or st_dict.get("codigo", "").replace("ACA_", "")

            new_caudal = None
            new_nivel = None
            new_time = None

            # A) Desde lote AETR v2 si llegó
            if live_data_by_code and code_short in live_data_by_code:
                live_entry = live_data_by_code[code_short]
                popup = live_entry.get("popup", {}) if isinstance(live_entry.get("popup"), dict) else {}
                q_obj = popup.get("river_flow", {}) if isinstance(popup.get("river_flow"), dict) else {}
                lvl_obj = popup.get("river_level", {}) if isinstance(popup.get("river_level"), dict) else {}

                raw_q = _parse_float(q_obj.get("value")) if q_obj else None
                raw_lvl = _parse_float(lvl_obj.get("value")) if lvl_obj else None
                if raw_q is not None:
                    new_caudal = raw_q
                if raw_lvl is not None:
                    new_nivel = round(raw_lvl / 100.0, 3) if lvl_obj.get("unit") == "cm" else raw_lvl
                raw_t = q_obj.get("time") or lvl_obj.get("time") or live_entry.get("time")
                if raw_t:
                    new_time = _format_madrid_iso(raw_t)

            # B) Desde fallback Socrata
            if (new_caudal is None or new_nivel is None) and socrata_readings:
                s_code = self._get_socrata_aforo_code(code_short)
                if s_code and s_code in socrata_readings:
                    s_data = socrata_readings[s_code]
                    if new_caudal is None and "caudal" in s_data:
                        new_caudal = s_data["caudal"]
                    if new_nivel is None and "nivel" in s_data:
                        new_nivel = s_data["nivel"]
                    if not new_time and "time" in s_data:
                        new_time = _format_madrid_iso(s_data["time"])

            # C) Asignar valores nuevos O conservar el último valor previo si no hay nueva lectura
            if new_caudal is not None:
                st_dict["caudal"] = new_caudal
                st_dict["ultimo_caudal"] = new_caudal
                st_dict["caudal_actual"] = new_caudal
                st_dict["lastValue"] = new_caudal

            if new_nivel is not None:
                st_dict["nivel"] = new_nivel
                st_dict["ultimo_nivel"] = new_nivel
                st_dict["nivel_actual"] = new_nivel
                st_dict["nivel_cm"] = round(new_nivel * 100, 1)

            if new_time:
                st_dict["ultima_hora"] = new_time
                st_dict["fecha_comunicacion"] = new_time
            elif not st_dict.get("ultima_hora"):
                st_dict["ultima_hora"] = now_iso
                st_dict["fecha_comunicacion"] = now_iso

            st_dict["cuenca"] = "Conques Internes de Catalunya"
            st_dict["fuente"] = "Agència Catalana de l'Aigua (ACA)"
            updated_list.append(st_dict)

        if updated_list:
            self._aforos = updated_list
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

            PUBLIC_DATA_DIR.mkdir(parents=True, exist_ok=True)
            try:
                with open(PUBLIC_DATA_DIR / "aca_aforos.geojson", "w", encoding="utf-8") as f:
                    json.dump(geojson_data, f, ensure_ascii=False, indent=2)
                with open(PUBLIC_DATA_DIR / "aca_aforos_estaciones.json", "w", encoding="utf-8") as f:
                    json.dump(self._aforos, f, ensure_ascii=False, indent=2)
            except Exception as pe:
                logger.debug(f"No se pudo escribir en PUBLIC_DATA_DIR: {pe}")

            logger.info(f"Sincronizados {len(self._aforos)} aforos de caudal de la ACA correctamente.")

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
            logger.warning(f"Error en sync background aforos ACA: {e}")
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
        Sincroniza los 11 embalses principales de las Cuencas Internas de Cataluña de manera resiliente:
        1. Intenta visor AETR v2.
        2. Recurre a Open Data Generalitat (gn9e-3qhr).
        3. Preserva valores anteriores para evitar puntos grises.
        """
        now_madrid = datetime.now(MADRID_TZ)
        now_iso = now_madrid.strftime("%Y-%m-%d %H:%M:%S")

        # 1. Intentar AETR v2 con timeout estricto de 2.5s
        live_embalses_by_code: Dict[str, Dict[str, Any]] = {}
        try:
            sample_code = "080581-002"
            url_live = f"{ACA_BASE_URL}/data/public/reservoir/{sample_code}"
            req_live = urllib.request.Request(url_live, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req_live, timeout=2.5) as resp:
                live_batch = json.loads(resp.read().decode("utf-8"))
                if isinstance(live_batch, dict):
                    live_embalses_by_code = live_batch
        except Exception as e:
            logger.debug(f"AETR v2 embalses no disponible ({e}), utilizando fallback Open Data Generalitat.")

        # 2. Fallback Socrata
        socrata_embalses = {}
        if not live_embalses_by_code:
            socrata_embalses = self._fetch_socrata_embalses_readings()

        existing_emb = list(self._embalses)
        if not existing_emb and STATIC_ACA_EMBALSES_FILE.exists():
            try:
                with open(STATIC_ACA_EMBALSES_FILE, "r", encoding="utf-8") as f:
                    existing_emb = json.load(f)
            except Exception:
                pass

        if not existing_emb:
            logger.warning("No hay lista base de embalses ACA disponible para sincronizar.")
            return self._embalses

        updated_emb = []
        for emb in existing_emb:
            emb_dict = dict(emb)
            code_short = emb_dict.get("codigo_corto") or emb_dict.get("codigo", "").replace("ACA_", "")

            new_vol = None
            new_pct = None
            new_cota = None
            new_time = None

            # A) Desde AETR v2
            if live_embalses_by_code and code_short in live_embalses_by_code:
                live = live_embalses_by_code[code_short]
                popup = live.get("popup", {}) if isinstance(live.get("popup"), dict) else {}
                vol_obj = popup.get("volume", {})
                cap_obj = popup.get("capacity", {})
                lvl_obj = popup.get("level", {})
                new_vol = _parse_float(vol_obj.get("value"))
                new_pct = _parse_float(cap_obj.get("value")) if cap_obj else _parse_float(live.get("value"))
                new_cota = _parse_float(lvl_obj.get("value"))
                raw_t = vol_obj.get("time") or cap_obj.get("time") or lvl_obj.get("time") or live.get("time")
                if raw_t:
                    new_time = _format_madrid_iso(raw_t)

            # B) Desde fallback Socrata
            if (new_vol is None or new_pct is None) and socrata_embalses:
                s_name = self._get_socrata_embalse_name(code_short)
                if s_name and s_name in socrata_embalses:
                    s_info = socrata_embalses[s_name]
                    if new_vol is None and s_info.get("vol") is not None:
                        new_vol = s_info["vol"]
                    if new_pct is None and s_info.get("pct") is not None:
                        new_pct = s_info["pct"]
                    if new_cota is None and s_info.get("cota") is not None:
                        new_cota = s_info["cota"]
                    if not new_time and s_info.get("time"):
                        new_time = _format_madrid_iso(s_info["time"])

            # C) Asignar valores calculando porcentajes si procede
            cap_max = _parse_float(emb_dict.get("capacidad") or emb_dict.get("capacidad_nmn"))
            if new_pct is None and new_vol is not None and cap_max and cap_max > 0:
                new_pct = round((new_vol / cap_max) * 100, 1)
            elif new_vol is None and new_pct is not None and cap_max and cap_max > 0:
                new_vol = round((new_pct / 100.0) * cap_max, 2)

            if new_vol is not None:
                emb_dict["volumen_actual"] = new_vol
                emb_dict["ultimo_volumen"] = new_vol
            if new_pct is not None:
                emb_dict["porcentaje"] = new_pct
                emb_dict["porcentaje_llenado"] = new_pct
            if new_cota is not None:
                emb_dict["cota_actual"] = new_cota

            if new_time:
                emb_dict["ultima_hora"] = new_time
                emb_dict["fecha_comunicacion"] = new_time
            elif not emb_dict.get("ultima_hora"):
                emb_dict["ultima_hora"] = now_iso
                emb_dict["fecha_comunicacion"] = now_iso

            emb_dict["cuenca"] = "Conques Internes de Catalunya"
            emb_dict["fuente"] = "Agència Catalana de l'Aigua (ACA)"
            updated_emb.append(emb_dict)

        if updated_emb:
            self._embalses = updated_emb
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

            PUBLIC_DATA_DIR.mkdir(parents=True, exist_ok=True)
            try:
                with open(PUBLIC_DATA_DIR / "aca_embalses.geojson", "w", encoding="utf-8") as f:
                    json.dump(geojson_data, f, ensure_ascii=False, indent=2)
                with open(PUBLIC_DATA_DIR / "aca_embalses_estaciones.json", "w", encoding="utf-8") as f:
                    json.dump(self._embalses, f, ensure_ascii=False, indent=2)
            except Exception as pe:
                logger.debug(f"No se pudo escribir en PUBLIC_DATA_DIR: {pe}")

            logger.info(f"Sincronizados {len(self._embalses)} embalses de la ACA correctamente.")

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
            logger.warning(f"Error en sync background embalses ACA: {e}")
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
        Consulta la serie temporal histórica de aforos o embalses en la API de la ACA.
        Con fallback automático a Open Data Generalitat de Catalunya si AETR no responde.
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
            or self._get_socrata_embalse_name(raw_code) is not None
        )

        endpoint_type = "reservoir" if is_emb else "rivergauges"
        now_ts = int(now_madrid.timestamp())
        url = f"{ACA_BASE_URL}/chart/public/{endpoint_type}/{raw_code}?from={start_iso}&to={end_iso}&_t={now_ts}"

        def _fetch_aetr():
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Cache-Control": "no-cache"})
            try:
                with urllib.request.urlopen(req, timeout=2.0) as resp:
                    if resp.status == 200:
                        return json.loads(resp.read().decode("utf-8"))
            except Exception:
                pass
            return {}

        raw_data = await asyncio.to_thread(_fetch_aetr)
        series = []
        effective_unit = "m³/s" if not is_emb else "hm³"

        if isinstance(raw_data, dict) and raw_data:
            target_signal_key = None
            if not is_emb:
                if variable_type == "nivel":
                    for k in raw_data.keys():
                        if "ANA" in k or "NIV" in k or "LEVEL" in k.upper():
                            target_signal_key = k
                            break
                if not target_signal_key:
                    for k in raw_data.keys():
                        if k.startswith("CALC") or "Q" in k or "CAUDAL" in k or "FLOW" in k.upper():
                            target_signal_key = k
                            break
                if not target_signal_key and len(raw_data) > 0:
                    target_signal_key = list(raw_data.keys())[0]
            else:
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
                meta_st = self._embalses_catalog.get(raw_code, {}) if is_emb else self._aforos_catalog.get(raw_code, {})
                sensors = meta_st.get("sensors", {}) if isinstance(meta_st, dict) else {}
                sensor_meta = sensors.get(target_signal_key, {}) if isinstance(sensors, dict) else {}
                sensor_unit = sensor_meta.get("unit") or ""
                sensor_type = sensor_meta.get("type") or ""

                is_liters = sensor_unit == "l/s" or "channel_flow" in sensor_type
                is_cm = sensor_unit == "cm" or "level" in sensor_type

                if is_emb:
                    vt = str(variable_type).lower() if variable_type else "volumen"
                    if "cota" in vt or "level" in vt or "absolute_level" in sensor_type:
                        effective_unit = "m"
                    elif "porcent" in vt or "%" in vt or "capacity" in sensor_type:
                        effective_unit = "%"
                    else:
                        effective_unit = "hm³"
                else:
                    if variable_type == "nivel" or (is_cm and not is_liters and not str(target_signal_key).startswith("CALC")):
                        effective_unit = "m"
                    else:
                        effective_unit = "m³/s"

                if isinstance(points_dict, dict):
                    for t_str, val in points_dict.items():
                        v_float = _parse_float(val)
                        if v_float is not None:
                            if is_liters:
                                v_float = round(v_float / 1000.0, 4)
                            elif effective_unit == "m" and is_cm:
                                v_float = round(v_float / 100.0, 3)
                            madrid_dt_str = _format_madrid_iso(t_str)
                            series.append({
                                "fecha": madrid_dt_str,
                                "valor": v_float,
                                "estado": 0,
                            })

        # ========================================================
        # Fallback a Open Data Generalitat si AETR no dio puntos
        # ========================================================
        if not series:
            def _fetch_socrata_series():
                pts = []
                eff_u = "m³/s"
                limit_recs = max(30, min(120, int(hours // 24) * 2 + 30))
                try:
                    if is_emb:
                        s_name = self._get_socrata_embalse_name(raw_code)
                        if s_name:
                            vt = str(variable_type).lower() if variable_type else "volumen"
                            if "cota" in vt or "level" in vt:
                                val_field = "nivell_absolut"
                                eff_u = "msnm"
                            elif "porcent" in vt or "%" in vt:
                                val_field = "percentatge_volum_embassat"
                                eff_u = "%"
                            else:
                                val_field = "volum_embassat"
                                eff_u = "hm³"

                            url_e = f"{SOCRATA_EMBALSES_URL}?estaci={urllib.parse.quote(s_name)}&$order=dia%20DESC&$limit={limit_recs}"
                            req_e = urllib.request.Request(url_e, headers={"User-Agent": USER_AGENT})
                            with urllib.request.urlopen(req_e, timeout=5) as r:
                                records = json.loads(r.read().decode("utf-8"))
                                records.reverse()
                                for rec in records:
                                    vf = _parse_float(rec.get(val_field))
                                    if vf is not None:
                                        pts.append({
                                            "fecha": _format_madrid_iso(rec.get("dia")),
                                            "valor": vf,
                                            "estado": 0,
                                        })
                    else:
                        s_code = self._get_socrata_aforo_code(raw_code)
                        if s_code:
                            v_filter = "Nivell riu" if variable_type == "nivel" else "Cabal riu"
                            eff_u = "m" if variable_type == "nivel" else "m³/s"
                            url_q = f"{SOCRATA_AFOROS_URL}?codi_estacio={urllib.parse.quote(s_code)}&tipus_variable={urllib.parse.quote(v_filter)}&$order=dia%20DESC&$limit={limit_recs}"
                            req_q = urllib.request.Request(url_q, headers={"User-Agent": USER_AGENT})
                            with urllib.request.urlopen(req_q, timeout=5) as r:
                                records = json.loads(r.read().decode("utf-8"))
                                records.reverse()
                                for rec in records:
                                    vf = _parse_float(rec.get("valor"))
                                    if vf is not None:
                                        pts.append({
                                            "fecha": _format_madrid_iso(rec.get("dia")),
                                            "valor": vf,
                                            "estado": 0,
                                        })
                except Exception as err:
                    logger.warning(f"Error consultando serie Open Data para {raw_code}: {err}")
                return pts, eff_u

            s_pts, s_u = await asyncio.to_thread(_fetch_socrata_series)
            if s_pts:
                series = s_pts
                effective_unit = s_u

        # Ordenar cronológicamente
        series.sort(key=lambda x: str(x.get("fecha") or ""))

        station_info = self._embalses_by_id.get(raw_code) if is_emb else self._aforos_by_id.get(raw_code)

        return {
            "id_variable": str(id_variable),
            "estacion": station_info,
            "unidad": effective_unit,
            "rango": {
                "desde": start_dt.strftime("%Y-%m-%d %H:%M:%S"),
                "hasta": end_dt.strftime("%Y-%m-%d %H:%M:%S"),
                "horas": hours,
            },
            "puntos_totales": len(series),
            "serie": series,
        }


aca_service = ACAService()
