"""
Servicio de sincronización y consulta de datos del S.A.I.H. Duero (Confederación Hidrográfica del Duero - CHD / MITECO).
Proporciona datos en tiempo real de:
 - Aforos en ríos y canales (184 estaciones con caudal m³/s y nivel m).
 - Embalses y presas (36 embalses con volumen hm³, nivel msnm y porcentaje %).
 - Pluviómetros y estaciones meteorológicas (221 estaciones con lluvia horaria mm y temperatura ºC).
 - Series temporales históricas horarias a demanda (12h, 24h, 48h, 7 días / 168h hasta 90 días).
"""

import asyncio
import json
import logging
import re
import subprocess
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger("rainloc.duero_service")

MADRID_TZ = ZoneInfo("Europe/Madrid")
SYNC_TTL_SECONDS = 300  # 5 minutos de frescura en caché
HISTORY_CACHE_TTL = 300  # 5 minutos para series históricas

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 RainLoc/3.0"
DUERO_BASE_URL = "https://www.saihduero.es"
RISR_LIVE_URL = f"{DUERO_BASE_URL}/datos-tiempo-real/risr"
EMBALSES_LIVE_URL = f"{DUERO_BASE_URL}/situacion-embalses"

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
PUBLIC_DATA_DIR = Path(__file__).resolve().parent.parent.parent.parent / "public" / "data"

STATIC_DUERO_AFOROS_FILE = DATA_DIR / "duero_aforos_estaciones.json"
STATIC_DUERO_EMBALSES_FILE = DATA_DIR / "duero_embalses_estaciones.json"
STATIC_DUERO_PLUVIOS_FILE = DATA_DIR / "duero_lluvias_estaciones.json"

DUERO_AFOROS_GEOJSON_FILE = DATA_DIR / "duero_aforos.geojson"
DUERO_EMBALSES_GEOJSON_FILE = DATA_DIR / "duero_embalses.geojson"
DUERO_PLUVIOS_GEOJSON_FILE = DATA_DIR / "duero_lluvias.geojson"


def _fetch_url(url: str, timeout: int = 20) -> str:
    """Descarga el contenido de una URL de forma robusta evitando bloqueos de chunked encoding."""
    try:
        proc = subprocess.run(
            ["curl", "-s", "--compressed", "-m", str(timeout), "-A", USER_AGENT, url],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.stdout:
            return proc.stdout
    except Exception:
        pass
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Connection": "close"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="ignore")
    except Exception as e:
        logger.warning(f"Error descargando {url}: {e}")
        return ""


def _parse_num(val_str: Optional[str]) -> Optional[float]:
    """Limpia cadenas de texto numéricas con unidades (ej. '1.037,88 m.s.n.m.', '0,18 m3/s', '38,1 %')."""
    if not val_str:
        return None
    s = (
        str(val_str)
        .replace("&nbsp;", "")
        .replace("\xa0", "")
        .replace("l/m2 en 1h", "")
        .replace("l/m2", "")
        .replace("m.s.n.m.", "")
        .replace("msnm", "")
        .replace("m3/s", "")
        .replace("m³/s", "")
        .replace("hm3", "")
        .replace("hm³", "")
        .replace("ºC", "")
        .replace("°C", "")
        .replace("%", "")
        .replace("m", "")
        .replace("*", "")
        .strip()
    )
    if not s or s in ("--", "-", "N/D", "null"):
        return None
    # Eliminar separador de miles con punto si hay coma decimal (ej. 1.288,4 -> 1288.4)
    if "." in s and "," in s:
        s = s.replace(".", "").replace(",", ".")
    else:
        s = s.replace(",", ".")
    try:
        return round(float(s), 3)
    except (ValueError, TypeError):
        return None


def _format_date(date_str: Optional[str], time_str: Optional[str]) -> str:
    """Convierte fecha tipo '04 oct' y '16:20' a ISO 'YYYY-MM-DD HH:mm:ss' con el año actual."""
    now = datetime.now(MADRID_TZ)
    if not date_str or not time_str:
        return now.strftime("%Y-%m-%d %H:%M:%S")

    months = {
        "ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6,
        "jul": 7, "ago": 8, "sep": 9, "oct": 10, "nov": 11, "dic": 12
    }

    try:
        parts = date_str.strip().lower().split()
        day = int(parts[0])
        month = months.get(parts[1][:3], now.month)
        t_parts = time_str.strip().split(":")
        hour = int(t_parts[0])
        minute = int(t_parts[1]) if len(t_parts) > 1 else 0
        year = now.year

        # Ajuste de cambio de año si estamos en enero y el dato es de diciembre
        if now.month == 1 and month == 12:
            year -= 1

        dt = datetime(year, month, day, hour, minute, 0, tzinfo=MADRID_TZ)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return now.strftime("%Y-%m-%d %H:%M:%S")


class DueroService:
    """Controlador y sincronizador de los datos hidrológicos y meteorológicos del SAIH Duero."""

    def __init__(self):
        self._aforos: List[Dict[str, Any]] = []
        self._embalses: List[Dict[str, Any]] = []
        self._pluvios: List[Dict[str, Any]] = []

        self._aforos_by_id: Dict[str, Dict[str, Any]] = {}
        self._embalses_by_id: Dict[str, Dict[str, Any]] = {}
        self._pluvios_by_id: Dict[str, Dict[str, Any]] = {}

        self._last_sync_time: Optional[datetime] = None
        self._sync_lock = asyncio.Lock()

        self._history_cache: Dict[str, tuple[float, Dict[str, Any]]] = {}
        self._history_cache_lock = asyncio.Lock()

        self._load_from_disk()

    def _load_from_disk(self):
        """Carga datos guardados en disco si existen para arranque inmediato."""
        if STATIC_DUERO_AFOROS_FILE.exists():
            try:
                with open(STATIC_DUERO_AFOROS_FILE, "r", encoding="utf-8") as f:
                    self._aforos = json.load(f)
                    self._index_aforos()
            except Exception as e:
                logger.warning(f"Error cargando aforos de Duero desde disco: {e}")

        if STATIC_DUERO_EMBALSES_FILE.exists():
            try:
                with open(STATIC_DUERO_EMBALSES_FILE, "r", encoding="utf-8") as f:
                    self._embalses = json.load(f)
                    self._index_embalses()
            except Exception as e:
                logger.warning(f"Error cargando embalses de Duero desde disco: {e}")

        if STATIC_DUERO_PLUVIOS_FILE.exists():
            try:
                with open(STATIC_DUERO_PLUVIOS_FILE, "r", encoding="utf-8") as f:
                    self._pluvios = json.load(f)
                    self._index_pluvios()
            except Exception as e:
                logger.warning(f"Error cargando pluviómetros de Duero desde disco: {e}")

        if STATIC_DUERO_AFOROS_FILE.exists():
            try:
                mtime = datetime.fromtimestamp(STATIC_DUERO_AFOROS_FILE.stat().st_mtime, tz=MADRID_TZ)
                self._last_sync_time = mtime
            except Exception:
                pass

    def _index_aforos(self):
        idx = {}
        for a in self._aforos:
            if a.get("id_variable"):
                idx[str(a["id_variable"]).strip()] = a
                idx[str(a["id_variable"]).strip().upper()] = a
            if a.get("id_estacion"):
                idx[str(a["id_estacion"]).strip()] = a
                idx[str(a["id_estacion"]).strip().upper()] = a
            if a.get("codigo"):
                idx[str(a["codigo"]).strip()] = a
                idx[str(a["codigo"]).strip().upper()] = a
        self._aforos_by_id = idx

    def _index_embalses(self):
        idx = {}
        for e in self._embalses:
            if e.get("id_estacion"):
                idx[str(e["id_estacion"]).strip()] = e
                idx[str(e["id_estacion"]).strip().upper()] = e
            if e.get("codigo"):
                idx[str(e["codigo"]).strip()] = e
                idx[str(e["codigo"]).strip().upper()] = e
            if e.get("id_variable"):
                idx[str(e["id_variable"]).strip()] = e
                idx[str(e["id_variable"]).strip().upper()] = e
        self._embalses_by_id = idx

    def _index_pluvios(self):
        idx = {}
        for p in self._pluvios:
            if p.get("id_estacion"):
                idx[str(p["id_estacion"]).strip()] = p
                idx[str(p["id_estacion"]).strip().upper()] = p
            if p.get("codigo"):
                idx[str(p["codigo"]).strip()] = p
                idx[str(p["codigo"]).strip().upper()] = p
            if p.get("id_variable"):
                idx[str(p["id_variable"]).strip()] = p
                idx[str(p["id_variable"]).strip().upper()] = p
        self._pluvios_by_id = idx

    def _is_fresh(self) -> bool:
        if not self._last_sync_time or len(self._aforos) == 0:
            return False
        return (datetime.now(MADRID_TZ) - self._last_sync_time).total_seconds() < SYNC_TTL_SECONDS

    # ==========================================
    # SINCRONIZACIÓN CENTRALIZADA
    # ==========================================

    def sync_all(self):
        """
        Descarga y sincroniza en un solo paso los datos en tiempo real de SAIH Duero:
        - Aforos en ríos (datosEA)
        - Embalses (datosEM y boletín situacion-embalses)
        - Pluviómetros (datosPL)
        """
        logger.info("Sincronizando datos en tiempo real de SAIH Duero (CHD)...")
        now_dt = datetime.now(MADRID_TZ)
        now_iso = now_dt.strftime("%Y-%m-%d %H:%M:%S")

        # 1. Descarga de RISR
        html_risr = _fetch_url(RISR_LIVE_URL, timeout=10)
        if not html_risr:
            logger.error(f"Error descargando datos RISR de Duero desde {RISR_LIVE_URL}")
            return

        pattern = re.compile(r'var\s+(datos[A-Z0-9_]+)\s*=\s*new\s+Array\s*\((.*?)\);', re.DOTALL)
        matches = pattern.findall(html_risr)

        raw_data: Dict[str, List[Dict[str, str]]] = {}
        for name, body in matches:
            items = []
            for m in re.finditer(r'\{\s*(.*?)\s*\}', body, re.DOTALL):
                obj_text = m.group(1)
                obj = {}
                for kv in re.finditer(r'([a-zA-Z0-9_]+)\s*:\s*(?:[\'\"]([^\'\"]*)[\'\"]|([0-9\.\-]+))', obj_text):
                    k = kv.group(1)
                    v = kv.group(2) if kv.group(2) is not None else kv.group(3)
                    obj[k] = v
                if obj.get("id"):
                    items.append(obj)
            raw_data[name] = items

        # 2. Descarga de situacion-embalses (capacidades y balance hídrico)
        capacidades_map: Dict[str, float] = {}
        html_emb = _fetch_url(EMBALSES_LIVE_URL, timeout=10)
        if html_emb:
            # Parsear filas de la tabla de embalses
            for row in re.finditer(r'<tr[^>]*>(.*?)</tr>', html_emb, re.DOTALL):
                cells = [re.sub(r'<[^>]+>', ' ', c).strip() for c in re.findall(r'<t[dh][^>]*>(.*?)</t[dh]>', row.group(1), re.DOTALL)]
                if len(cells) >= 3:
                    nombre_emb = cells[0].strip().lower()
                    cap_val = _parse_num(cells[1])
                    if cap_val and cap_val > 0:
                        capacidades_map[nombre_emb] = cap_val

        # -------------------------------------------------------------
        # Procesar AFOROS (datosEA)
        # -------------------------------------------------------------
        aforos_list = []
        for ea in raw_data.get("datosEA", []):
            st_id = str(ea.get("id", "")).strip()
            if not st_id:
                continue
            lat = _parse_num(ea.get("lat"))
            lng = _parse_num(ea.get("lng"))
            if lat is None or lng is None:
                continue

            nivel = _parse_num(ea.get("n"))
            caudal = _parse_num(ea.get("q"))
            fecha_iso = _format_date(ea.get("date"), ea.get("time"))
            station_name = str(ea.get("station", "")).strip()
            river_name = str(ea.get("river", "")).strip()
            status_str = str(ea.get("status", "normal")).lower()

            aforo_obj = {
                "id_estacion": f"duero_aforo_{st_id.lower()}",
                "id_variable": st_id,
                "codigo": st_id,
                "codigo_estacion": st_id,
                "nombre": station_name,
                "nombre_completo": f"{station_name} ({river_name})" if river_name else station_name,
                "rio": f"Río {river_name}" if river_name and not river_name.lower().startswith("río") else (river_name or "Cuenca del Duero"),
                "subcuenca": river_name or "Duero",
                "cuenca": "Duero",
                "red": "CHD",
                "lat": lat,
                "lon": lng,
                "caudal": caudal,
                "ultimo_caudal": caudal,
                "nivel": nivel,
                "ultimo_nivel": nivel,
                "fecha": fecha_iso,
                "ultima_hora": fecha_iso,
                "fecha_comunicacion": fecha_iso,
                "timestamp": int(datetime.fromisoformat(fecha_iso).timestamp()) if fecha_iso else int(now_dt.timestamp()),
                "estado": status_str == "normal",
                "estado_alerta": status_str,
                "status": status_str,
                "q_status": str(ea.get("q_status", "variable_normal")),
                "n_status": str(ea.get("n_status", "variable_normal")),
                "umbrales": {},
                "tendencia": "estable",
                "fuente": "S.A.I.H. Duero (CHD / MITECO)",
                "unidad": "m³/s",
                "unidad_caudal": "m³/s",
                "unidad_nivel": "m",
                "tipo": "Aforo",
            }
            aforos_list.append(aforo_obj)

        self._aforos = aforos_list
        self._index_aforos()

        # -------------------------------------------------------------
        # Procesar EMBALSES (datosEM)
        # -------------------------------------------------------------
        embalses_list = []
        for em in raw_data.get("datosEM", []):
            st_id = str(em.get("id", "")).strip()
            if not st_id:
                continue
            lat = _parse_num(em.get("lat"))
            lng = _parse_num(em.get("lng"))
            if lat is None or lng is None:
                continue

            cota = _parse_num(em.get("n"))
            volumen = _parse_num(em.get("v"))
            porcentaje = _parse_num(em.get("v_p"))
            fecha_iso = _format_date(em.get("date"), em.get("time"))
            station_name = str(em.get("station", "")).strip()
            river_name = str(em.get("river", "")).strip()

            # Buscar capacidad en el mapa del boletín o calcular por porcentaje
            capacidad = None
            st_clean = station_name.lower().replace("embalse de", "").replace("presa de", "").strip()
            for k, cap in capacidades_map.items():
                if k in st_clean or st_clean in k:
                    capacidad = cap
                    break
            if capacidad is None and volumen is not None and porcentaje and porcentaje > 0:
                capacidad = round((volumen / porcentaje) * 100.0, 2)

            embalse_obj = {
                "id_estacion": f"duero_emb_{st_id.lower()}",
                "id_variable": st_id,
                "codigo": st_id,
                "codigo_estacion": st_id,
                "nombre": station_name,
                "nombre_embalse": station_name,
                "rio": f"Río {river_name}" if river_name and not river_name.lower().startswith("río") else (river_name or "Cuenca del Duero"),
                "subcuenca": river_name or "Duero",
                "cuenca": "Duero",
                "red": "CHD",
                "lat": lat,
                "lon": lng,
                "volumen": volumen,
                "volumen_actual": volumen,
                "capacidad": capacidad,
                "capacidad_nmn": capacidad,
                "porcentaje": porcentaje,
                "porcentaje_llenado": porcentaje,
                "porcentaje_volumen": porcentaje,
                "nivel": cota,
                "cota": cota,
                "cota_actual": cota,
                "fecha": fecha_iso,
                "ultima_hora": fecha_iso,
                "timestamp": int(datetime.fromisoformat(fecha_iso).timestamp()) if fecha_iso else int(now_dt.timestamp()),
                "fuente": "S.A.I.H. Duero (CHD / MITECO)",
                "unidad_volumen": "hm³",
                "unidad_cota": "msnm",
                "unidad_nivel": "msnm",
                "tipo": "Embalse",
            }
            embalses_list.append(embalse_obj)

        self._embalses = embalses_list
        self._index_embalses()

        # -------------------------------------------------------------
        # Procesar PLUVIÓMETROS (datosPL)
        # -------------------------------------------------------------
        pluvios_list = []
        for pl in raw_data.get("datosPL", []):
            st_id = str(pl.get("id", "")).strip()
            if not st_id:
                continue
            lat = _parse_num(pl.get("lat"))
            lng = _parse_num(pl.get("lng"))
            if lat is None or lng is None:
                continue

            lluvia_1h = _parse_num(pl.get("p")) or 0.0
            temp = _parse_num(pl.get("t"))
            fecha_iso = _format_date(pl.get("date"), pl.get("time"))
            station_name = str(pl.get("station", "")).strip()
            river_name = str(pl.get("river", "")).strip()

            pluvio_obj = {
                "id_estacion": f"duero_pluv_{st_id.lower()}",
                "id_variable": st_id,
                "codigo": st_id,
                "nombre": station_name,
                "rio": river_name,
                "cuenca": "Duero",
                "subcuenca": river_name or "Duero",
                "red": "CHD",
                "lat": lat,
                "lon": lng,
                "lluvia_1h": lluvia_1h,
                "precipitacion_1h": lluvia_1h,
                "precipitacion_4h": None,
                "precipitacion_12h": None,
                "precipitacion_24h": None,
                "temperatura": temp,
                "fecha": fecha_iso,
                "fecha_1h": fecha_iso,
                "ultima_hora": fecha_iso,
                "estado": True,
                "fuente": "S.A.I.H. Duero (CHD / MITECO)",
                "unidad": "mm"
            }
            pluvios_list.append(pluvio_obj)

        self._pluvios = pluvios_list
        self._index_pluvios()

        self._last_sync_time = now_dt

        # Guardar ficheros locales
        self._save_to_disk()
        logger.info(
            f"Sincronización Duero completada: {len(self._aforos)} aforos, {len(self._embalses)} embalses, {len(self._pluvios)} pluviómetros."
        )

    def _save_to_disk(self):
        """Guarda los JSON y GeoJSON generados en el directorio de datos."""
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        PUBLIC_DATA_DIR.mkdir(parents=True, exist_ok=True)

        # 1. Aforos
        try:
            with open(STATIC_DUERO_AFOROS_FILE, "w", encoding="utf-8") as f:
                json.dump(self._aforos, f, ensure_ascii=False, indent=2)
            geo_aforos = {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "geometry": {"type": "Point", "coordinates": [a["lon"], a["lat"]]},
                        "properties": a
                    }
                    for a in self._aforos
                ]
            }
            with open(DUERO_AFOROS_GEOJSON_FILE, "w", encoding="utf-8") as f:
                json.dump(geo_aforos, f, ensure_ascii=False)
            with open(PUBLIC_DATA_DIR / "duero_aforos.geojson", "w", encoding="utf-8") as f:
                json.dump(geo_aforos, f, ensure_ascii=False)
        except Exception as e:
            logger.warning(f"Error guardando ficheros de aforos de Duero: {e}")

        # 2. Embalses
        try:
            with open(STATIC_DUERO_EMBALSES_FILE, "w", encoding="utf-8") as f:
                json.dump(self._embalses, f, ensure_ascii=False, indent=2)
            geo_embalses = {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "geometry": {"type": "Point", "coordinates": [e["lon"], e["lat"]]},
                        "properties": e
                    }
                    for e in self._embalses
                ]
            }
            with open(DUERO_EMBALSES_GEOJSON_FILE, "w", encoding="utf-8") as f:
                json.dump(geo_embalses, f, ensure_ascii=False)
            with open(PUBLIC_DATA_DIR / "duero_embalses.geojson", "w", encoding="utf-8") as f:
                json.dump(geo_embalses, f, ensure_ascii=False)
        except Exception as e:
            logger.warning(f"Error guardando ficheros de embalses de Duero: {e}")

        # 3. Pluviómetros
        try:
            with open(STATIC_DUERO_PLUVIOS_FILE, "w", encoding="utf-8") as f:
                json.dump(self._pluvios, f, ensure_ascii=False, indent=2)
            geo_pluvios = {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "geometry": {"type": "Point", "coordinates": [p["lon"], p["lat"]]},
                        "properties": p
                    }
                    for p in self._pluvios
                ]
            }
            with open(DUERO_PLUVIOS_GEOJSON_FILE, "w", encoding="utf-8") as f:
                json.dump(geo_pluvios, f, ensure_ascii=False)
            with open(PUBLIC_DATA_DIR / "duero_lluvias.geojson", "w", encoding="utf-8") as f:
                json.dump(geo_pluvios, f, ensure_ascii=False)
        except Exception as e:
            logger.warning(f"Error guardando ficheros de pluviómetros de Duero: {e}")

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
                await loop.run_in_executor(None, self.sync_all)
        except Exception as e:
            logger.warning(f"Error en sync background Duero: {e}")
        finally:
            self._bg_syncing = False

    async def ensure_fresh_data(self, force: bool = False):
        if len(self._aforos) > 0 and len(self._embalses) > 0:
            if not self._is_fresh() or force:
                self._trigger_bg_sync()
            return

        async with self._sync_lock:
            if not self._is_fresh() or force:
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, self.sync_all)

    # ==========================================
    # GETTERS DE COLECCIONES Y GEOJSON
    # ==========================================

    async def get_caudales(self) -> List[Dict[str, Any]]:
        await self.ensure_fresh_data()
        return self._aforos

    async def get_caudales_geojson(self) -> Dict[str, Any]:
        await self.ensure_fresh_data()
        return {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [a["lon"], a["lat"]]},
                    "properties": a
                }
                for a in self._aforos
            ]
        }

    async def get_embalses(self) -> List[Dict[str, Any]]:
        await self.ensure_fresh_data()
        return self._embalses

    async def get_embalses_geojson(self) -> Dict[str, Any]:
        await self.ensure_fresh_data()
        return {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [e["lon"], e["lat"]]},
                    "properties": e
                }
                for e in self._embalses
            ]
        }

    async def get_pluvios(self) -> List[Dict[str, Any]]:
        await self.ensure_fresh_data()
        return self._pluvios

    async def get_pluvios_geojson(self) -> Dict[str, Any]:
        await self.ensure_fresh_data()
        return {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [p["lon"], p["lat"]]},
                    "properties": p
                }
                for p in self._pluvios
            ]
        }

    # ==========================================
    # HISTÓRICOS Y SERIES TEMPORALES
    # ==========================================

    async def get_history(
        self,
        id_variable: str,
        hours: int = 48,
        is_embalse: bool = False,
        variable_type: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Consulta la serie temporal horaria de una estación de aforo, embalse o pluviómetro de SAIH Duero.
        Extrae la gráfica interactiva pública desde saihduero.es/risr/{id}/historico/{token}.
        """
        st_id = (
            str(id_variable)
            .replace("duero_aforo_", "")
            .replace("duero_emb_", "")
            .replace("duero_pluv_", "")
            .replace("DUERO_AFORO_", "")
            .replace("DUERO_EMB_", "")
            .replace("DUERO_PLUV_", "")
            .strip()
            .upper()
        )

        var_type = (variable_type or ("volumen" if is_embalse else "caudal")).lower()
        cache_key = f"{st_id}_{var_type}_{hours}"
        now_ts = datetime.now(MADRID_TZ).timestamp()

        async with self._history_cache_lock:
            if cache_key in self._history_cache:
                ts, res = self._history_cache[cache_key]
                if (now_ts - ts) < HISTORY_CACHE_TTL:
                    return res

        loop = asyncio.get_running_loop()
        res = await loop.run_in_executor(
            None,
            self._fetch_history_sync,
            st_id,
            var_type,
            hours,
            start_date,
            end_date
        )

        async with self._history_cache_lock:
            self._history_cache[cache_key] = (now_ts, res)

        return res

    def _fetch_history_sync(
        self,
        st_id: str,
        var_type: str,
        hours: int,
        start_date: Optional[str],
        end_date: Optional[str]
    ) -> Dict[str, Any]:
        """Extracción síncrona de la serie temporal horaria desde la web de SAIH Duero."""
        clean_id = str(st_id).strip()
        station_info = self._aforos_by_id.get(clean_id) or self._embalses_by_id.get(clean_id) or self._pluvios_by_id.get(clean_id)
        if not station_info:
            for prefix in ["duero_aforo_", "duero_emb_", "duero_pluvio_", "duero_"]:
                if clean_id.lower().startswith(prefix):
                    c = clean_id[len(prefix):]
                    station_info = self._aforos_by_id.get(c) or self._embalses_by_id.get(c) or self._pluvios_by_id.get(c)
                    if station_info:
                        break

        if station_info:
            real_code = station_info.get("id_variable") or station_info.get("codigo") or clean_id
            st_name = station_info.get("nombre") or clean_id
        else:
            real_code = clean_id
            st_name = clean_id

        # 1. Obtener la página principal de la estación para descubrir los enlaces de histórico
        station_url = f"{DUERO_BASE_URL}/risr/{real_code}"
        html = _fetch_url(station_url, timeout=20)
        if not html:
            logger.warning(f"Error consultando estación {real_code} de Duero")
            return {
                "id_variable": real_code,
                "nombre": st_name,
                "red": "CHD",
                "cuenca": "Duero",
                "variable": var_type.capitalize(),
                "unidad": "m³/s",
                "total_puntos": 0,
                "puntos_totales": 0,
                "serie": [],
                "datos": []
            }

        # Mapear variables disponibles a sus URLs de histórico
        var_map = {}
        for m in re.finditer(r'<tr[^>]*>(.*?)</tr>', html, re.DOTALL):
            row_text = m.group(1)
            if "historico/" in row_text:
                cells = [re.sub(r'<[^>]+>', ' ', c).strip() for c in re.findall(r'<td[^>]*>(.*?)</td>', row_text, re.DOTALL)]
                links = re.findall(r'href=[\"\']([^\"\']*?historico/[^\"\']+)[\"\']', row_text)
                if cells and links:
                    var_name = cells[0].strip().lower()
                    var_map[var_name] = links[0].strip()

        target_href = None
        target_var_name = "Caudal"
        target_unit = "m³/s"

        if "vol" in var_type or "embalse" in var_type:
            # 1. Priorizar volumen embalsado (excluyendo porcentaje)
            for k, href in var_map.items():
                if ("volumen embalsado" in k or "volumen" in k) and "porcentaje" not in k and "%" not in k:
                    target_href = href
                    target_var_name = "Volumen"
                    target_unit = "hm³"
                    break
            if not target_href:
                for k, href in var_map.items():
                    if "volumen" in k:
                        target_href = href
                        target_var_name = "Volumen"
                        target_unit = "hm³"
                        break
        elif "entrante" in var_type or "recibido" in var_type or "caudal_in" in var_type:
            for k, href in var_map.items():
                if "entrante" in k or "recibido" in k:
                    target_href = href
                    target_var_name = "Caudal Entrante"
                    target_unit = "m³/s"
                    break
        elif "vertido" in var_type or "aliviado" in var_type or "salida" in var_type or "caudal_out" in var_type:
            for k, href in var_map.items():
                if "vertido" in k or "aliviado" in k or "salida" in k:
                    target_href = href
                    target_var_name = "Caudal Vertido"
                    target_unit = "m³/s"
                    break
        elif "nivel" in var_type or "level" in var_type or "n" in var_type or "cota" in var_type:
            for k, href in var_map.items():
                if k == "nivel" or "cota" in k:
                    target_href = href
                    target_var_name = "Cota / Nivel"
                    target_unit = "m"
                    break
            if not target_href:
                for k, href in var_map.items():
                    if "nivel" in k:
                        target_href = href
                        target_var_name = "Nivel"
                        target_unit = "m"
                        break
        elif "caudal" in var_type or "flow" in var_type or "q" in var_type:
            for k, href in var_map.items():
                if "caudal" in k and "vertido" not in k and "medio" not in k:
                    target_href = href
                    target_var_name = "Caudal"
                    target_unit = "m³/s"
                    break
            if not target_href:
                for k, href in var_map.items():
                    if "caudal" in k:
                        target_href = href
                        target_var_name = "Caudal"
                        target_unit = "m³/s"
                        break
        elif "pluv" in var_type or "precip" in var_type or "lluv" in var_type or "rain" in var_type:
            for k, href in var_map.items():
                if "pluvio" in k or "precip" in k or "lluvia" in k:
                    target_href = href
                    target_var_name = "Precipitación"
                    target_unit = "mm"
                    break

        if not target_href and var_map:
            first_k = list(var_map.keys())[0]
            target_href = var_map[first_k]
            target_var_name = first_k.capitalize()

        if not target_href:
            return {
                "id_variable": st_id,
                "nombre": st_name,
                "red": "CHD",
                "cuenca": "Duero",
                "variable": target_var_name,
                "unidad": target_unit,
                "total_puntos": 0,
                "puntos_totales": 0,
                "serie": [],
                "datos": []
            }

        hist_url = f"{DUERO_BASE_URL}/{target_href}" if not target_href.startswith("http") else target_href
        hist_html = _fetch_url(hist_url, timeout=25)
        if not hist_html:
            logger.warning(f"Error descargando histórico {hist_url}")
            return {
                "id_variable": real_code,
                "nombre": st_name,
                "red": "CHD",
                "cuenca": "Duero",
                "variable": target_var_name,
                "unidad": target_unit,
                "total_puntos": 0,
                "puntos_totales": 0,
                "serie": [],
                "datos": []
            }

        m = re.search(r'var\s+chartData\s*=\s*\[(.*?)\];', hist_html, re.DOTALL)
        if not m:
            return {
                "id_variable": real_code,
                "nombre": st_name,
                "red": "CHD",
                "cuenca": "Duero",
                "variable": target_var_name,
                "unidad": target_unit,
                "total_puntos": 0,
                "puntos_totales": 0,
                "serie": [],
                "datos": []
            }

        now = datetime.now(MADRID_TZ)
        cutoff = now - timedelta(hours=hours)

        data_points = []
        for item in re.finditer(r'\{d:[\"\']([0-9\/\:\s]+)[\"\'],\s*v:([0-9\.\-]+)\}', m.group(1)):
            d_str = item.group(1).strip()
            v_float = float(item.group(2))
            try:
                dt = datetime.strptime(d_str, "%d/%m/%Y %H:%M").replace(tzinfo=MADRID_TZ)
                if dt >= cutoff:
                    data_points.append({
                        "fecha": dt.strftime("%Y-%m-%d %H:%M:%S"),
                        "valor": round(v_float, 3),
                        "timestamp": int(dt.timestamp())
                    })
            except Exception:
                pass

        data_points.sort(key=lambda x: x["timestamp"])

        return {
            "id_variable": real_code,
            "id_estacion": f"duero_emb_{real_code.lower()}" if (real_code.startswith("EM") or "emb" in real_code.lower()) else f"duero_aforo_{real_code.lower()}",
            "codigo": real_code,
            "nombre": st_name,
            "red": "CHD",
            "cuenca": "Duero",
            "variable": target_var_name,
            "unidad": target_unit,
            "rango": {
                "desde": cutoff.strftime("%Y-%m-%d %H:%M:%S"),
                "hasta": now.strftime("%Y-%m-%d %H:%M:%S"),
                "horas": hours,
            },
            "horas_solicitadas": hours,
            "total_puntos": len(data_points),
            "puntos_totales": len(data_points),
            "serie": data_points,
            "datos": data_points
        }


# Instancia singleton del servicio para todo RainLoc
duero_service = DueroService()
