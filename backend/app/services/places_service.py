"""
Servicio de Búsqueda Geográfica de Lugares (Places Search Service)
Base de datos SQLite embebida con FTS5 (Full-Text Search)
Incluye: Municipios, Comarcas, Cuencas, Ríos, Embalses, Cimas y Estaciones de RainLoc.
"""

import os
import re
import json
import sqlite3
import logging
import unicodedata
import urllib.request
import zipfile
import io
from pathlib import Path
from typing import List, Dict, Any, Optional

logger = logging.getLogger("rainloc-backend.places")

# Ruta a la base de datos SQLite
DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
DB_PATH = DATA_DIR / "places.db"

# Códigos CCAA GeoNames en España
CCAA_MAP = {
    "51": "Andalucía",
    "52": "Aragón",
    "34": "Asturias",
    "07": "Illes Balears",
    "53": "Canarias",
    "39": "Cantabria",
    "54": "Castilla-La Mancha",
    "55": "Castilla y León",
    "56": "Catalunya",
    "60": "Comunitat Valenciana",
    "57": "Extremadura",
    "58": "Galicia",
    "29": "Comunidad de Madrid",
    "31": "Región de Murcia",
    "32": "Comunidad Foral de Navarra",
    "59": "País Vasco",
    "27": "La Rioja",
    "CE": "Ceuta",
    "ML": "Melilla"
}

# Provincias Comunitat Valenciana
CV_PROVINCIAS = {
    "V": "Valencia",
    "A": "Alicante",
    "CS": "Castellón"
}

def normalize_text(text: str) -> str:
    """Normaliza texto eliminando acentos, diacríticos y caracteres especiales."""
    if not text:
        return ""
    text = text.lower()
    # Descomponer caracteres Unicode y quitar marcas de acento
    nfkd = unicodedata.normalize('NFKD', text)
    clean = "".join([c for c in nfkd if not unicodedata.combining(c)])
    # Limpiar signos de puntuación innecesarios
    clean = re.sub(r"[^\w\s\-']", " ", clean)
    return re.sub(r"\s+", " ", clean).strip()

class PlacesService:
    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self._last_files_check_time = 0
        self._last_files_mtime = 0
        self._ensure_initialized()

    def _get_connection(self) -> sqlite3.Connection:
        """Obtiene una conexión optimizada a SQLite (solo lectura si es posible)."""
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        return conn

    def _get_local_files_mtime_sum(self) -> float:
        """Calcula la suma de fechas de modificación de todos los datasets locales de RainLoc."""
        total_mtime = 0.0
        try:
            subsistemas_path = DATA_DIR.parent / "subsistemas.geojson"
            if subsistemas_path.exists():
                total_mtime += subsistemas_path.stat().st_mtime

            for pattern in ("*_embalses.geojson", "*_aforos.geojson", "*_lluvias.geojson"):
                for p in DATA_DIR.glob(pattern):
                    total_mtime += p.stat().st_mtime
        except Exception:
            pass
        return total_mtime

    def _ensure_initialized(self):
        """Verifica si la base de datos existe; si no, la construye. Si ya existe, sincroniza las estaciones locales al iniciar."""
        if not self.db_path.exists() or os.path.getsize(self.db_path) < 10000:
            logger.info("Base de datos de lugares no encontrada o incompleta. Construyendo base de datos...")
            self.build_database()
        else:
            # Sincronizar automáticamente estaciones, aforos y embalses locales en cada reinicio
            self.sync_local_entities(force=True)

    def build_database(self):
        """Construye la base de datos SQLite consolidando GeoNames, Cuencas, Embalses y Estaciones."""
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        temp_db = self.db_path.with_suffix(".tmp.db")
        if temp_db.exists():
            temp_db.unlink()

        conn = sqlite3.connect(str(temp_db))
        cur = conn.cursor()

        # Tabla principal
        cur.execute("""
        CREATE TABLE places (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            alt_name TEXT,
            name_norm TEXT NOT NULL,
            category TEXT NOT NULL,
            subcategory TEXT,
            province TEXT,
            community TEXT,
            lat REAL NOT NULL,
            lon REAL NOT NULL,
            zoom INTEGER NOT NULL DEFAULT 13,
            importance INTEGER NOT NULL DEFAULT 50,
            extra_info TEXT
        );
        """)

        # Tabla de búsqueda FTS5 universalmente compatible
        cur.execute("""
        CREATE VIRTUAL TABLE places_fts USING fts5(
            name,
            alt_name,
            name_norm,
            category,
            province,
            community,
            tokenize = 'unicode61'
        );
        """)

        logger.info("Descargando e indexando toponimia geográfica oficial de España (GeoNames)...")
        inserted_ids = set()
        places_to_insert = []
        fts_to_insert = []

        # 1. Procesar GeoNames ES.zip
        try:
            req = urllib.request.Request(
                'https://download.geonames.org/export/dump/ES.zip',
                headers={'User-Agent': 'RainLoc-GIS/3.0'}
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                zip_data = resp.read()

            with zipfile.ZipFile(io.BytesIO(zip_data)) as z:
                with z.open('ES.txt') as f:
                    for line_bytes in f:
                        line = line_bytes.decode('utf-8', errors='ignore')
                        parts = line.split('\t')
                        if len(parts) < 15:
                            continue

                        gid = parts[0]
                        name = parts[1].strip()
                        asciiname = parts[2].strip()
                        alt_names_raw = parts[3].strip()
                        try:
                            lat = float(parts[4])
                            lon = float(parts[5])
                        except ValueError:
                            continue

                        fclass = parts[6]
                        fcode = parts[7]
                        admin1 = parts[10]
                        community = CCAA_MAP.get(admin1, "")

                        # Filtrar y clasificar categorías relevantes
                        category = None
                        subcategory = fcode
                        zoom = 13
                        importance = 40

                        # A: Unidades Administrativas (CCAA, Provincias, Municipios)
                        if fclass == 'A':
                            if fcode == 'ADM1':
                                category = 'region'
                                importance = 95
                                zoom = 8
                            elif fcode == 'ADM2':
                                category = 'province'
                                importance = 90
                                zoom = 10
                            elif fcode in ('ADM3', 'ADM4'):
                                category = 'municipality'
                                importance = 75
                                zoom = 13

                        # P: Núcleos de población (Ciudades, pueblos, pedanías)
                        elif fclass == 'P':
                            category = 'municipality'
                            if fcode == 'PPLC':  # Capital de país
                                importance = 100
                                zoom = 12
                            elif fcode == 'PPLA':  # Capital de CCAA
                                importance = 95
                                zoom = 13
                            elif fcode == 'PPLA2':  # Capital de provincia
                                importance = 90
                                zoom = 13
                            elif fcode in ('PPLA3', 'PPL'):  # Municipio / Población
                                importance = 70
                                zoom = 13
                            else:
                                importance = 50
                                zoom = 14

                        # H: Hidrografía (Ríos, embalses, lagos, ramblas)
                        elif fclass == 'H':
                            if fcode in ('STM', 'STMI', 'CRK', 'WAD'):
                                category = 'river'
                                importance = 65
                                zoom = 12
                            elif fcode in ('RSV', 'LK', 'PND', 'DAM'):
                                category = 'reservoir'
                                importance = 70
                                zoom = 14

                        # T: Orografía (Montañas, cimas, sierras, puertos)
                        elif fclass == 'T':
                            if fcode in ('PK', 'MT', 'MTS', 'RDGE'):
                                category = 'mountain'
                                importance = 55
                                zoom = 13
                            elif fcode in ('PASS', 'GAP', 'VAL'):
                                category = 'mountain'
                                importance = 50
                                zoom = 13

                        # L: Parques naturales y áreas protegidas
                        elif fclass == 'L' and fcode in ('PRK', 'RES', 'RESN', 'AREA'):
                            category = 'place'
                            importance = 50
                            zoom = 12

                        if not category:
                            continue

                        # Prioridad especial a la Comunitat Valenciana
                        if admin1 == "60":
                            importance += 10

                        # Procesar nombres alternativos
                        alt_list = [a.strip() for a in alt_names_raw.split(',') if a.strip()] if alt_names_raw else []
                        # Quedarnos con hasta 5 nombres alternativos relevantes
                        alt_name = ", ".join(alt_list[:5]) if alt_list else (asciiname if asciiname != name else None)

                        # Crear texto normalizado para búsqueda completa
                        norm_parts = [name, asciiname] + alt_list[:6]
                        name_norm = normalize_text(" ".join(norm_parts))

                        place_id = f"geo_{gid}"
                        if place_id in inserted_ids:
                            continue
                        inserted_ids.add(place_id)

                        places_to_insert.append((
                            place_id, name, alt_name, name_norm, category, subcategory,
                            "", community, lat, lon, zoom, importance, json.dumps({"geoname_id": gid})
                        ))
                        fts_to_insert.append((name, alt_name or "", name_norm, category, "", community))

            logger.info(f"Toponimia GeoNames cargada: {len(places_to_insert)} elementos.")
        except Exception as e:
            logger.error(f"Error descargando o procesando GeoNames: {e}")

        # 2. Agregar Ríos, Barrancos y Ramblas principales curados con máxima precisión
        rios_curados = [
            ("Rambla del Poyo", "Rambla de Poio / Torrente / Torrent / Paiporta", 39.423, -0.428, "Valencia", "Comunitat Valenciana", 95, 12),
            ("Barranco del Carraixet", "Barranc del Carraixet / Moncada / Tavernes", 39.525, -0.345, "Valencia", "Comunitat Valenciana", 92, 12),
            ("Río Turia", "Riu Túria / Guadalaviar", 39.521, -0.485, "Valencia", "Comunitat Valenciana", 95, 12),
            ("Río Júcar", "Riu Xúquer", 39.155, -0.412, "Valencia", "Comunitat Valenciana", 95, 11),
            ("Río Magro", "Riu Magre / Utiel / Requena / Real / Carlet / Algemesí", 39.312, -0.598, "Valencia", "Comunitat Valenciana", 94, 12),
            ("Río Palancia", "Riu Palància / Segorbe / Sagunto / Sagunt", 39.735, -0.425, "Castellón", "Comunitat Valenciana", 93, 12),
            ("Río Serpis", "Riu Serpis / Riu d'Alcoi / Gandia", 38.932, -0.255, "Alicante", "Comunitat Valenciana", 93, 12),
            ("Río Mijares", "Riu Millars / Montanejos / Vila-real", 40.045, -0.285, "Castellón", "Comunitat Valenciana", 93, 12),
            ("Río Vinalopó", "Riu Vinalopó / Elda / Novelda / Elche / Elx", 38.412, -0.745, "Alicante", "Comunitat Valenciana", 93, 12),
            ("Río Clariano", "Riu Clariano / Ontinyent", 38.835, -0.585, "Valencia", "Comunitat Valenciana", 90, 13),
            ("Río Albaida", "Riu d'Albaida / Manuel / Xàtiva", 38.985, -0.512, "Valencia", "Comunitat Valenciana", 90, 13),
            ("Río Cabriel", "Riu Cabriol / Cofrentes / Villatoya", 39.385, -1.255, "Valencia", "Comunitat Valenciana", 92, 12),
            ("Río Escalona", "Riu Escalona / Navarrés / Bicorp", 39.025, -0.725, "Valencia", "Comunitat Valenciana", 88, 13),
            ("Río Sellent", "Riu Sellent / Bolbaite / Chella", 39.045, -0.655, "Valencia", "Comunitat Valenciana", 88, 13),
            ("Río Cérvol", "Riu Cérvol / Vinaròs / Morella", 40.525, 0.385, "Castellón", "Comunitat Valenciana", 88, 12),
            ("Río Bergantes", "Riu Bergantes / Morella / Zorita", 40.715, -0.155, "Castellón", "Comunitat Valenciana", 88, 12),
            ("Río Bohílgues", "Río Bohílgues / Ademuz", 40.085, -1.275, "Valencia", "Comunitat Valenciana", 88, 13),
            ("Río Girona", "Riu Girona / Beniarbeig / Els Poblets", 38.825, 0.055, "Alicante", "Comunitat Valenciana", 89, 13),
            ("Río Gorgos", "Riu Gorgos / Riu de Xaló / Xàbia / Jávea", 38.745, 0.085, "Alicante", "Comunitat Valenciana", 89, 13),
            ("Río Bullent", "Riu Bullent / Pego / Oliva", 38.885, -0.095, "Valencia", "Comunitat Valenciana", 88, 13),
            ("Río Segura", "Río Segura / Murcia / Orihuela / Guardamar", 38.085, -0.925, "Alicante", "Región de Murcia", 95, 11),
            ("Río Mundo", "Río Mundo / Riópar / Hellín", 38.485, -1.725, "Albacete", "Castilla-La Mancha", 92, 12),
            ("Río Guadalentín", "Río Guadalentín / Sangonera / Lorca", 37.715, -1.685, "Murcia", "Región de Murcia", 92, 12),
            ("Río Ebro", "Río Ebro / Riu Ebre / Tortosa / Deltebre", 40.785, 0.525, "Tarragona", "Catalunya", 95, 10),
            ("Río Guadalquivir", "Río Guadalquivir / Sevilla / Córdoba", 37.385, -5.995, "Sevilla", "Andalucía", 95, 10)
        ]

        for nom_v, nom_alt, lat, lon, prov, comm, imp, zoom in rios_curados:
            rid = f"rio_curado_{normalize_text(nom_v).replace(' ', '_')}"
            norm = normalize_text(f"{nom_v} {nom_alt} rio riu rambla barranc barranco afluente")
            places_to_insert.append((
                rid, nom_v, nom_alt, norm, 'river', 'rio_principal',
                prov, comm, lat, lon, zoom, imp,
                json.dumps({"type": "river_main"})
            ))
            fts_to_insert.append((nom_v, nom_alt, norm, 'river', prov, comm))

        # 3. Agregar Comarcas de la Comunitat Valenciana explícitas con máxima relevancia
        comarcas_cv = [
            ("L'Horta Sud", "Huerta Sur", 39.42, -0.42, "Valencia", 88),
            ("L'Horta Nord", "Huerta Norte", 39.54, -0.37, "Valencia", 88),
            ("València Ciutat", "Valencia Ciudad", 39.47, -0.38, "Valencia", 98),
            ("La Ribera Alta", "Ribera Alta", 39.18, -0.52, "Valencia", 88),
            ("La Ribera Baixa", "Ribera Baja", 39.23, -0.30, "Valencia", 88),
            ("La Safor", "Safor", 38.97, -0.18, "Valencia", 88),
            ("La Costera", "Costera", 38.98, -0.58, "Valencia", 88),
            ("La Vall d'Albaida", "Valle de Albaida", 38.83, -0.53, "Valencia", 88),
            ("El Camp de Túria", "Campo de Turia", 39.63, -0.57, "Valencia", 88),
            ("El Camp de Morvedre", "Campo de Murviedro", 39.68, -0.28, "Valencia", 88),
            ("La Hoya de Buñol", "Foia de Bunyol", 39.42, -0.79, "Valencia", 88),
            ("La Plana de Utiel-Requena", "Requena-Utiel", 39.52, -1.18, "Valencia", 88),
            ("Los Serranos", "Els Serrans", 39.75, -0.92, "Valencia", 88),
            ("El Rincón de Ademuz", "Racó d'Ademús", 40.06, -1.28, "Valencia", 88),
            ("La Plana Alta", "Plana Alta", 40.08, 0.05, "Castellón", 88),
            ("La Plana Baixa", "Plana Baja", 39.87, -0.16, "Castellón", 88),
            ("L'Alcalatén", "Alcalatén", 40.16, -0.23, "Castellón", 88),
            ("L'Alt Maestrat", "Alto Maestrazgo", 40.35, -0.05, "Castellón", 88),
            ("El Baix Maestrat", "Bajo Maestrazgo", 40.48, 0.35, "Castellón", 88),
            ("Els Ports", "Los Puertos de Morella", 40.62, -0.10, "Castellón", 88),
            ("L'Alt Palància", "Alto Palancia", 39.85, -0.52, "Castellón", 88),
            ("L'Alt Millars", "Alto Mijares", 40.07, -0.45, "Castellón", 88),
            ("L'Alacantí", "Campo de Alicante", 38.40, -0.48, "Alicante", 88),
            ("El Baix Vinalopó", "Bajo Vinalopó", 38.25, -0.68, "Alicante", 88),
            ("El Vinalopó Mitjà", "Vinalopó Medio", 38.48, -0.77, "Alicante", 88),
            ("L'Alt Vinalopó", "Alto Vinalopó", 38.63, -0.87, "Alicante", 88),
            ("La Marina Alta", "Marina Alta", 38.78, 0.05, "Alicante", 88),
            ("La Marina Baixa", "Marina Baja", 38.58, -0.18, "Alicante", 88),
            ("La Vega Baja", "El Baix Segura", 38.08, -0.80, "Alicante", 88),
            ("L'Alcoià", "Hoya de Alcoy", 38.69, -0.52, "Alicante", 88),
            ("El Comtat", "Condado de Cocentaina", 38.75, -0.38, "Alicante", 88)
        ]

        for nom_v, nom_c, lat, lon, prov, imp in comarcas_cv:
            cid = f"comarca_{normalize_text(nom_v).replace(' ', '_')}"
            norm = normalize_text(f"{nom_v} {nom_c} comarca")
            places_to_insert.append((
                cid, nom_v, nom_c, norm, 'region', 'comarca_cv',
                prov, 'Comunitat Valenciana', lat, lon, 11, imp,
                json.dumps({"type": "comarca"})
            ))
            fts_to_insert.append((nom_v, nom_c, norm, 'region', prov, 'Comunitat Valenciana'))

        # 3. Incorporar datasets locales (Cuencas, Embalses, Aforos, Estaciones)
        loc_places, loc_fts = self._extract_local_entities(inserted_ids)
        places_to_insert.extend(loc_places)
        fts_to_insert.extend(loc_fts)

        # Insertar todo en bloques
        cur.executemany("""
        INSERT OR REPLACE INTO places (
            id, name, alt_name, name_norm, category, subcategory,
            province, community, lat, lon, zoom, importance, extra_info
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """, places_to_insert)

        cur.executemany("""
        INSERT INTO places_fts (name, alt_name, name_norm, category, province, community)
        VALUES (?, ?, ?, ?, ?, ?);
        """, fts_to_insert)

        # Crear índices para velocidad instantánea
        cur.execute("CREATE INDEX IF NOT EXISTS idx_places_category ON places(category);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_places_importance ON places(importance DESC);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_places_name_norm ON places(name_norm);")

        conn.commit()
        conn.close()

        # Reemplazo atómico del archivo DB
        if self.db_path.exists():
            self.db_path.unlink()
        temp_db.rename(self.db_path)
        self._last_files_mtime = self._get_local_files_mtime_sum()
        logger.info(f"¡Base de datos de lugares compilada con éxito! Total lugares: {len(places_to_insert)}")

    def _extract_local_entities(self, inserted_ids: Optional[set] = None):
        """Extrae todas las entidades locales de RainLoc: Cuencas, Embalses, Aforos y Estaciones de Lluvia."""
        if inserted_ids is None:
            inserted_ids = set()

        places_to_insert = []
        fts_to_insert = []

        # 1. Cuencas y Subsistemas Hidrográficos (subsistemas.geojson)
        subsistemas_candidates = [
            DATA_DIR.parent.parent / "subsistemas.geojson",
            DATA_DIR.parent.parent / "subsistemas.optimized.geojson",
            DATA_DIR.parent / "subsistemas.geojson",
            DATA_DIR / "subsistemas.geojson",
            Path("/app/subsistemas.geojson"),
            Path("/app/subsistemas.optimized.geojson")
        ]
        subsistemas_path = next((p for p in subsistemas_candidates if p.exists()), None)
        if subsistemas_path:
            try:
                with open(subsistemas_path, 'r', encoding='utf-8') as f:
                    sub_data = json.load(f)
                for feat in sub_data.get("features", []):
                    props = feat.get("properties", {})
                    nom_sis = props.get("NomSistExp", "")
                    subsistema = props.get("Subsistema", "")
                    if not subsistema:
                        continue

                    geom = feat.get("geometry", {})
                    coords = geom.get("coordinates", [])
                    lat, lon = self._calculate_centroid(coords, geom.get("type"))
                    if not lat or not lon:
                        continue

                    sid = f"cuenca_{normalize_text(subsistema).replace(' ', '_')}"
                    if sid in inserted_ids:
                        continue
                    inserted_ids.add(sid)

                    full_name = f"Cuenca {subsistema}"
                    alt = f"Sistema {nom_sis}" if nom_sis and nom_sis != subsistema else None
                    norm = normalize_text(f"{full_name} {alt or ''} cuenca subsistema demarcacion")

                    places_to_insert.append((
                        sid, full_name, alt, norm, 'cuenca', 'chj',
                        'CHJ', 'Comunitat Valenciana', lat, lon, 10, 85,
                        json.dumps({"sistema": nom_sis, "area_km2": props.get("Superf km2")})
                    ))
                    fts_to_insert.append((full_name, alt or "", norm, 'cuenca', 'CHJ', 'Comunitat Valenciana'))
            except Exception as e:
                logger.error(f"Error procesando subsistemas.geojson: {e}")

        # 2. Embalses locales (saih_embalses, ebro_embalses, etc.)
        embalse_files = list(DATA_DIR.glob("*_embalses.geojson"))
        for emb_file in embalse_files:
            try:
                with open(emb_file, 'r', encoding='utf-8') as f:
                    emb_data = json.load(f)
                for feat in emb_data.get("features", []):
                    props = feat.get("properties", {})
                    geom = feat.get("geometry", {})
                    coords = geom.get("coordinates", [])
                    if not coords or len(coords) < 2:
                        continue
                    lon, lat = coords[0], coords[1]
                    name = props.get("nombre") or props.get("name") or "Embalse"
                    pob = props.get("poblacion") or ""
                    prov = props.get("provincia") or ""
                    code = props.get("codigo") or props.get("id_estacion") or ""

                    eid = f"emb_{normalize_text(name).replace(' ', '_')}_{code}"
                    if eid in inserted_ids:
                        continue
                    inserted_ids.add(eid)

                    norm = normalize_text(f"{name} embalse panta presa {pob} {prov} {code}")
                    places_to_insert.append((
                        eid, name.title(), f"{pob} ({prov})" if pob else prov,
                        norm, 'reservoir', 'embalse_saih', prov, '', lat, lon, 14, 80,
                        json.dumps(props)
                    ))
                    fts_to_insert.append((name.title(), pob, norm, 'reservoir', prov, ''))
            except Exception as e:
                logger.error(f"Error leyendo {emb_file.name}: {e}")

        # 3. Aforos y Ríos locales
        aforo_files = list(DATA_DIR.glob("*_aforos.geojson"))
        for afo_file in aforo_files:
            try:
                with open(afo_file, 'r', encoding='utf-8') as f:
                    afo_data = json.load(f)
                for feat in afo_data.get("features", []):
                    props = feat.get("properties", {})
                    geom = feat.get("geometry", {})
                    coords = geom.get("coordinates", [])
                    if not coords or len(coords) < 2:
                        continue
                    lon, lat = coords[0], coords[1]
                    name = props.get("nombre") or props.get("name") or "Aforo"
                    variable = props.get("variable") or ""
                    cuenca = props.get("subcuenca") or props.get("cuenca") or ""
                    prov = props.get("provincia") or ""
                    code = props.get("codigo") or props.get("id_estacion") or ""

                    aid = f"afo_{normalize_text(name).replace(' ', '_')}_{code}"
                    if aid in inserted_ids:
                        continue
                    inserted_ids.add(aid)

                    norm = normalize_text(f"{name} {variable} {cuenca} {prov} {code} aforo caudal rio")
                    places_to_insert.append((
                        aid, name.title(), f"Aforo en {cuenca}" if cuenca else variable,
                        norm, 'river', 'aforo', prov, '', lat, lon, 14, 75,
                        json.dumps(props)
                    ))
                    fts_to_insert.append((name.title(), variable, norm, 'river', prov, ''))
            except Exception as e:
                logger.error(f"Error leyendo {afo_file.name}: {e}")

        # 4. Estaciones de Lluvia de RainLoc (AVAMET, AEMET, SAIH, Meteocat...)
        pluvio_files = list(DATA_DIR.glob("*_lluvias.geojson"))
        for pluv_file in pluvio_files:
            network = pluv_file.stem.split('_')[0].upper()
            try:
                with open(pluv_file, 'r', encoding='utf-8') as f:
                    p_data = json.load(f)
                for feat in p_data.get("features", []):
                    props = feat.get("properties", {})
                    geom = feat.get("geometry", {})
                    coords = geom.get("coordinates", [])
                    if not coords or len(coords) < 2:
                        continue
                    lon, lat = coords[0], coords[1]
                    name = props.get("nombre") or props.get("name") or props.get("station_name") or "Estación"
                    prov = props.get("provincia") or props.get("province") or ""
                    code = props.get("id") or props.get("codigo") or props.get("indicativo") or ""

                    pid = f"st_{network.lower()}_{code}_{normalize_text(name).replace(' ', '_')}"
                    if pid in inserted_ids:
                        continue
                    inserted_ids.add(pid)

                    display_name = f"{name} ({network})"
                    norm = normalize_text(f"{name} {network} estacion pluvio {prov} {code}")
                    places_to_insert.append((
                        pid, display_name, f"Estación {network} - {prov}",
                        norm, 'station', network.lower(), prov, '', lat, lon, 14, 60,
                        json.dumps({"network": network, "code": code})
                    ))
                    fts_to_insert.append((display_name, prov, norm, 'station', prov, ''))
            except Exception as e:
                logger.error(f"Error leyendo {pluv_file.name}: {e}")

        return places_to_insert, fts_to_insert

    def sync_local_entities(self, force: bool = False) -> int:
        """
        Sincroniza dinámicamente cualquier cambio en los archivos GeoJSON locales de RainLoc
        (nuevas estaciones de lluvia, nuevos aforos de caudal, nuevos embalses, cuencas).
        """
        if not self.db_path.exists():
            return 0

        current_mtime = self._get_local_files_mtime_sum()
        if not force and current_mtime <= self._last_files_mtime and self._last_files_mtime > 0:
            return 0

        logger.info("Detectados cambios o nueva sincronización en datasets locales de RainLoc...")
        places_to_insert, _ = self._extract_local_entities()
        if not places_to_insert:
            return 0

        conn = sqlite3.connect(str(self.db_path))
        cur = conn.cursor()
        try:
            # Eliminar entidades locales dinámicas anteriores
            cur.execute("DELETE FROM places WHERE id LIKE 'cuenca_%' OR id LIKE 'emb_%' OR id LIKE 'afo_%' OR id LIKE 'st_%';")

            # Reinsertar actualizadas
            cur.executemany("""
            INSERT OR REPLACE INTO places (
                id, name, alt_name, name_norm, category, subcategory,
                province, community, lat, lon, zoom, importance, extra_info
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """, places_to_insert)

            # Reconstruir tabla FTS5 para sincronizar índices
            cur.execute("DELETE FROM places_fts;")
            cur.execute("""
            INSERT INTO places_fts (rowid, name, alt_name, name_norm, category, province, community)
            SELECT rowid, name, coalesce(alt_name, ''), name_norm, category, coalesce(province, ''), coalesce(community, '')
            FROM places;
            """)

            conn.commit()
            self._last_files_mtime = current_mtime
            logger.info(f"✅ Sincronizados {len(places_to_insert)} lugares locales (estaciones, aforos, embalses, cuencas).")
            return len(places_to_insert)
        except Exception as e:
            logger.error(f"Error sincronizando entidades locales: {e}")
            return 0
        finally:
            conn.close()

    def check_auto_sync(self):
        """Comprueba periódicamente si hay nuevos datos locales para sincronizar."""
        import time
        now = time.time()
        if now - self._last_files_check_time > 45:
            self._last_files_check_time = now
            self.sync_local_entities(force=False)

    def _calculate_centroid(self, coords: Any, geom_type: str):
        """Calcula el centroide aproximado de geometrías GeoJSON."""
        try:
            flat_pts = []
            def extract_pts(c):
                if isinstance(c[0], (int, float)):
                    flat_pts.append(c)
                else:
                    for sub in c:
                        extract_pts(sub)
            extract_pts(coords)
            if not flat_pts:
                return None, None
            avg_lon = sum(p[0] for p in flat_pts) / len(flat_pts)
            avg_lat = sum(p[1] for p in flat_pts) / len(flat_pts)
            return avg_lat, avg_lon
        except Exception:
            return None, None

    def search(
        self,
        query: str,
        category: Optional[str] = None,
        limit: int = 10,
        min_chars: int = 2
    ) -> List[Dict[str, Any]]:
        """
        Realiza una búsqueda inteligente por prefijo, normalización fonética y ranking de importancia.
        """
        raw_query = query.strip()
        if not raw_query or len(raw_query) < min_chars:
            return []

        clean_q = normalize_text(raw_query)
        words = clean_q.split()
        if not words:
            return []

        # Construir consulta FTS5 con prefijos en cada palabra sobre las columnas toponímicas
        # Ejemplo: "tous panta" -> "{name alt_name name_norm} : \"tous\"* \"panta\"*"
        prefix_words = " ".join([f'"{w}"*' for w in words])
        fts_query = f'{{name alt_name name_norm}} : {prefix_words}'

        conn = self._get_connection()
        cur = conn.cursor()

        try:
            # Query híbrida FTS5 + ordenación por relevancia e importancia
            params = [fts_query]
            cat_filter = ""
            if category:
                cat_filter = "AND p.category = ?"
                params.append(category)

            # Extraemos los mejores candidatos coincidentes
            sql = f"""
            SELECT 
                p.id,
                p.name,
                p.alt_name,
                p.category,
                p.subcategory,
                p.province,
                p.community,
                p.lat,
                p.lon,
                p.zoom,
                p.importance,
                p.name_norm,
                bm25(places_fts) as text_rank
            FROM places_fts
            JOIN places p ON places_fts.rowid = p.rowid
            WHERE places_fts MATCH ? {cat_filter}
            ORDER BY p.importance DESC, bm25(places_fts) ASC
            LIMIT 100;
            """

            cur.execute(sql, params)
            rows = cur.fetchall()

            results = []
            seen_coords = set()

            for r in rows:
                name = r['name']
                alt_name = r['alt_name'] or ''
                name_norm = r['name_norm']
                cat = r['category']
                importance = r['importance']
                text_rank = r['text_rank']
                lat_r = round(r['lat'], 3)
                lon_r = round(r['lon'], 3)

                # Deduplicación de puntos casi idénticos con el mismo nombre y categoría
                coord_key = (name.lower(), cat, lat_r, lon_r)
                if coord_key in seen_coords:
                    continue
                seen_coords.add(coord_key)

                # Puntuación base
                score = importance - (text_rank * 6)
                name_clean = normalize_text(name)
                alt_clean = normalize_text(alt_name)
                name_tokens = name_clean.split()
                alt_tokens = alt_clean.split()
                all_tokens = name_tokens + alt_tokens

                # 1. Coincidencia exacta de todo el nombre principal o secundario
                if clean_q == name_clean or clean_q == alt_clean:
                    score += 160
                # 2. Coincidencia exacta de una palabra completa en el nombre principal (ej. "Poyo" en "Rambla del Poyo")
                elif any(clean_q == t for t in name_tokens):
                    score += 130
                # 3. Coincidencia exacta de palabra en nombres alternativos
                elif any(clean_q == t for t in alt_tokens):
                    score += 95
                # 4. El nombre principal empieza por la query
                elif name_clean.startswith(clean_q):
                    score += 75
                # 5. Alguna palabra empieza por la query (prefijo)
                elif any(t.startswith(clean_q) for t in all_tokens):
                    score += 50
                # 6. La query está contenida en el texto normalizado
                elif clean_q in name_norm:
                    score += 20

                # Bonificación especial por ámbito prioritario (Comunitat Valenciana)
                if r['community'] == 'Comunitat Valenciana':
                    score += 25

                # Bonus por relevancia de categoría
                if cat in ('municipality', 'region', 'cuenca'):
                    score += 15
                elif cat in ('reservoir', 'river'):
                    score += 12

                category_labels = {
                    'municipality': 'Municipio',
                    'region': 'Comarca / Región',
                    'cuenca': 'Cuenca Hidrográfica',
                    'river': 'Río / Barranco / Rambla',
                    'reservoir': 'Embalse / Presa',
                    'station': 'Estación Meteorológica',
                    'mountain': 'Cima / Sierra',
                    'place': 'Población / Paraje',
                    'province': 'Provincia'
                }

                category_icons = {
                    'municipality': '🏛️',
                    'region': '🗺️',
                    'cuenca': '🌐',
                    'river': '💧',
                    'reservoir': '🏞️',
                    'station': '📡',
                    'mountain': '⛰️',
                    'place': '📍',
                    'province': '📍'
                }

                results.append({
                    "id": r['id'],
                    "name": r['name'],
                    "alt_name": r['alt_name'],
                    "category": r['category'],
                    "category_label": category_labels.get(r['category'], 'Lugar'),
                    "icon": category_icons.get(r['category'], '📍'),
                    "province": r['province'],
                    "community": r['community'],
                    "lat": round(r['lat'], 6),
                    "lon": round(r['lon'], 6),
                    "zoom": r['zoom'],
                    "score": score
                })

            # Ordenar por puntuación final descendente
            results.sort(key=lambda x: x['score'], reverse=True)
            return results[:limit]

        except Exception as e:
            logger.error(f"Error ejecutando búsqueda: {e}")
            return []
        finally:
            conn.close()

# Instancia singleton
places_service = PlacesService()
