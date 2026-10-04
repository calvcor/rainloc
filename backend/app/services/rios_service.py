"""
Servicio para la descarga automática, procesamiento y cacheo del GeoJSON de Ríos de España
Fuente oficial: CartoBase ANE / SIANE - Instituto Geográfico Nacional (IGN / CNIG)
"""
import json
import logging
import os
import struct
import sqlite3
import tempfile
import urllib.request
from pathlib import Path
from typing import Dict, Any, Optional

from app.config import settings

logger = logging.getLogger("rainloc.rios")

# Mapeo de códigos hidrográficos Pfafstetter del IGN a nombres de ríos principales y secundarios
PFAFSTETTER_RIVER_NAMES = {
    # Grandes ríos peninsulares (t_rio = 1)
    "10094": "Río Guadalquivir",
    "1006": "Río Tajo",
    "1008": "Río Guadiana",
    "1004": "Río Duero",
    "2004": "Río Ebro",
    "10038": "Río Miño",
    "2002": "Río Júcar",
    "20016": "Río Segura",
    "10034": "Río Eo",
    # Cuencas y afluentes secundarios destacados (t_rio = 2)
    "100374": "Río Nalón",
    "1003742": "Río Narcea",
    "100376": "Río Navia",
    "1003778": "Río Tambre",
    "100378": "Río Ulla",
    "100384": "Río Sil",
    "1003844": "Río Cabe",
    "100412": "Río Támega",
    "100414": "Río Tua",
    "100416": "Río Sabor",
    "1004176": "Río Águeda",
    "100418": "Río Huebra",
    "1004184": "Río Yeltes",
    "10042": "Río Tormes",
    "10044": "Río Esla",
    "100442": "Río Tera",
    "100444": "Río Órbigo",
    "1004442": "Río Tuerto",
    "100446": "Río Cea",
    "100452": "Río Valderaduey",
    "100458": "Río Zapardiel",
    "10046": "Río Adaja",
    "100462": "Río Eresma",
    "1004624": "Río Voltoya",
    "10048": "Río Pisuerga",
    "100482": "Río Esgueva",
    "100484": "Río Carrión",
    "100486": "Río Arlanza",
    "1004862": "Río Arlanzón",
    "100492": "Río Cega",
    "100494": "Río Duratón",
    "100496": "Río Riaza",
    "100636": "Río Salor",
    "100638": "Río Sever",
    "10064": "Río Alagón",
    "10065422": "Río Almonte",
    "10066": "Río Tiétar",
    "100672": "Río Alberche",
    "100674": "Río Guadarrama",
    "100676": "Río Perales",
    "10068": "Río Jarama",
    "100682": "Río Tajuña",
    "100686": "Río Henares",
    "100688": "Río Manzanares",
    "100692": "Río Guadiela",
    "100696": "Río Gallo",
    "100814": "Rivera de Chanza",
    "10082": "Río Ardila",
    "100836": "Río Matachel",
    "10084": "Río Zújar",
    "100854": "Río Jabalón",
    "100858": "Río Bullaque",
    "10086": "Río Gigüela",
    "100864": "Río Riánsares",
    "10088": "Río Záncara",
    "10092": "Río Odiel",
    "100922": "Río Tinto",
    "1009414": "Río Corbones",
    "1009416": "Río Viar",
    "1009418": "Río Guadaíra",
    "100942": "Río Genil",
    "1009432": "Río Bembézar",
    "1009434": "Río Guadiato",
    "1009436": "Río Jándula",
    "100944": "Río Yeguas",
    "100946": "Río Guadalimar",
    "1009462": "Río Guadalén",
    "100948": "Río Guadiana Menor",
    "10096": "Río Guadalete",
    "200116": "Río Guadiaro",
    "20012": "Río Guadalhorce",
    "200134": "Río Guadalfeo",
    "20014": "Río Almanzora",
    "200162": "Río Guadalentín",
    "200168": "Río Mundo",
    "20022": "Riu Magre",
    "20026": "Río Cabriel",
    "200292": "Río Valdemembra",
    "20034": "Río Turia",
    "20038": "Río Mijares",
    "200418": "Río Matarraña",
    "20042": "Río Segre",
    "200422": "Río Cinca",
    "2004224": "Río Alcanadre",
    "2004226": "Río Ésera",
    "200424": "la Noguera Ribagorçana",
    "200426": "la Noguera Pallaresa",
    "200434": "Río Guadalope",
    "200436": "Río Martín",
    "200438": "Río Aguasvivas",
    "20044": "Río Gállego",
    "200452": "Río Huerva",
    "20046": "Río Jalón",
    "200464": "Río Jiloca",
    "20048": "Río Aragón",
    "200482": "Río Arga",
    "200492": "Río Ega",
    "20052": "el Llobregat",
    "20054": "el Ter",
    "200552": "el Fluvià"
}

IGN_SIANE_GPKG_URL = "https://raw.githubusercontent.com/rOpenSpain/mapSpain/sianedata/dist/se89_3_hidro_rio_l_x.gpkg"

class RiosService:
    def __init__(self):
        self._cached_geojson: Optional[Dict[str, Any]] = None

    def _parse_gpkg_geom(self, blob: bytes) -> Optional[Dict[str, Any]]:
        """Extrae la geometría LineString o MultiLineString desde el formato binario GeoPackage/WKB."""
        if not blob or len(blob) < 8:
            return None
        if blob[:2] != b'GP':
            return None

        flags = blob[3]
        envelope_indicator = (flags >> 1) & 0x07
        envelope_sizes = {0: 0, 1: 32, 2: 48, 3: 48, 4: 64}
        header_len = 8 + envelope_sizes.get(envelope_indicator, 0)
        wkb = blob[header_len:]
        if len(wkb) < 5:
            return None

        byte_order = '<' if wkb[0] == 1 else '>'
        wkb_type = struct.unpack(f'{byte_order}I', wkb[1:5])[0]
        geom_type = wkb_type % 1000

        if geom_type == 2:  # LineString
            num_points = struct.unpack(f'{byte_order}I', wkb[5:9])[0]
            coords = []
            offset = 9
            for _ in range(num_points):
                lon, lat = struct.unpack(f'{byte_order}2d', wkb[offset:offset + 16])
                coords.append([round(lon, 5), round(lat, 5)])
                offset += 16
            return {'type': 'LineString', 'coordinates': coords}

        elif geom_type == 5:  # MultiLineString
            num_lines = struct.unpack(f'{byte_order}I', wkb[5:9])[0]
            multi_coords = []
            offset = 9
            for _ in range(num_lines):
                sub_endian = '<' if wkb[offset] == 1 else '>'
                sub_num_points = struct.unpack(f'{sub_endian}I', wkb[offset + 5:offset + 9])[0]
                offset += 9
                line_coords = []
                for _ in range(sub_num_points):
                    lon, lat = struct.unpack(f'{sub_endian}2d', wkb[offset:offset + 16])
                    line_coords.append([round(lon, 5), round(lat, 5)])
                    offset += 16
                multi_coords.append(line_coords)
            return {'type': 'MultiLineString', 'coordinates': multi_coords}

        return None

    def _download_and_convert_ign_rivers(self) -> Dict[str, Any]:
        """Descarga el GeoPackage oficial de CartoBase ANE / IGN y lo convierte a GeoJSON optimizado."""
        logger.info(f"Descargando datos oficiales de hidrografía del IGN desde {IGN_SIANE_GPKG_URL}...")
        tmp_gpkg = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".gpkg", delete=False) as tf:
                tmp_gpkg = tf.name

            req = urllib.request.Request(
                IGN_SIANE_GPKG_URL,
                headers={"User-Agent": "RainLoc-HydrologyService/1.0"}
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                with open(tmp_gpkg, "wb") as f_out:
                    f_out.write(resp.read())

            conn = sqlite3.connect(tmp_gpkg)
            cursor = conn.cursor()
            cursor.execute(
                "SELECT fid, geom, id_rio, pers_hidro, orig_hidro, ubicacion, t_rio, rotulo, st_length_ "
                "FROM se89_3_hidro_rio_l_x"
            )
            rows = cursor.fetchall()

            features = []
            for row in rows:
                fid, geom_blob, id_rio, pers_hidro, orig_hidro, ubicacion, t_rio, rotulo, st_length_ = row
                geom = self._parse_gpkg_geom(geom_blob)
                if not geom:
                    continue

                # Resolver nombre del río (rótulo oficial IGN o mapeo Pfafstetter)
                nombre = (rotulo or "").strip()
                if not nombre and id_rio:
                    id_clean = str(id_rio).strip()
                    nombre = PFAFSTETTER_RIVER_NAMES.get(id_clean, "")
                    if not nombre:
                        # Buscar coincidencia por prefijo más largo
                        for prefix_len in range(len(id_clean), 3, -1):
                            prefix = id_clean[:prefix_len]
                            if prefix in PFAFSTETTER_RIVER_NAMES:
                                nombre = PFAFSTETTER_RIVER_NAMES[prefix]
                                break

                props = {
                    "id": fid,
                    "nombre": nombre,
                    "t_rio": t_rio,  # 1: Principal, 2: Secundario, 3: Afluente/Tributario, 4: Rambla/Arroyo
                    "permanente": pers_hidro == 1,
                    "longitud_grados": round(st_length_, 5) if st_length_ else None
                }

                features.append({
                    "type": "Feature",
                    "geometry": geom,
                    "properties": props
                })

            conn.close()

            geojson_data = {
                "type": "FeatureCollection",
                "crs": {
                    "type": "name",
                    "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}
                },
                "features": features
            }

            logger.info(f"Convertidos con éxito {len(features)} tramos de ríos de España desde el IGN.")
            return geojson_data

        finally:
            if tmp_gpkg and os.path.exists(tmp_gpkg):
                try:
                    os.unlink(tmp_gpkg)
                except Exception:
                    pass

    def get_rios_data(self) -> Dict[str, Any]:
        """
        Obtiene el GeoJSON de ríos de España.
        Si no existe en disco, se descarga automáticamente una única vez desde la fuente oficial del IGN
        y se persiste en data/rios.geojson para futuras peticiones.
        """
        if self._cached_geojson is not None:
            return self._cached_geojson

        # 1. Comprobar si ya existe en disco
        candidates = [
            getattr(settings, "RIOS_DATA_FILE", None),
            getattr(settings, "RIOS_FILE", None),
            getattr(settings, "BASE_DIR", Path(".")) / "data" / "rios.geojson",
            getattr(settings, "BASE_DIR", Path(".")) / "rios.geojson",
            Path.cwd() / "data" / "rios.geojson",
            Path.cwd() / "rios.geojson",
            Path.cwd().parent / "data" / "rios.geojson",
            Path(__file__).resolve().parent.parent.parent.parent / "data" / "rios.geojson"
        ]

        for path in candidates:
            if path and path.exists():
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        if data and data.get("features"):
                            logger.info(f"Cargado GeoJSON de ríos desde archivo local: {path}")
                            self._cached_geojson = data
                            return self._cached_geojson
                except Exception as e:
                    logger.warning(f"No se pudo leer archivo de ríos existente en {path}: {e}")

        # 2. Si no existe, descargar dinámicamente y persistir
        logger.info("Archivo local de ríos no encontrado. Iniciando descarga dinámica desde el IGN...")
        try:
            data = self._download_and_convert_ign_rivers()

            # Guardar en data/rios.geojson (frontend/shared) y backend/data/rios.geojson
            save_targets = [
                getattr(settings, "RIOS_DATA_FILE", None),
                getattr(settings, "RIOS_FILE", None),
                getattr(settings, "BASE_DIR", Path(".")) / "data" / "rios.geojson"
            ]

            saved = False
            for target in save_targets:
                if target:
                    try:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        with open(target, "w", encoding="utf-8") as f:
                            json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
                        logger.info(f"GeoJSON de ríos guardado correctamente en {target}")
                        saved = True
                    except Exception as e:
                        logger.error(f"Error al guardar ríos en {target}: {e}")

            self._cached_geojson = data
            return self._cached_geojson

        except Exception as e:
            logger.error(f"Error durante la descarga y conversión de ríos del IGN: {e}")
            raise e

rios_service = RiosService()
