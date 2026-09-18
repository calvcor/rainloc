"""
Servicio de sincronización inteligente para el Feed ATOM / CAP de AEMET Meteoalerta.
Implementa peticiones condicionales If-Modified-Since y parseo a formato GeoJSON.
"""
import asyncio
import logging
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from typing import Dict, Any, Optional, List, Tuple
from datetime import datetime, timezone

from app.config import settings
from app.services.weather_state import weather_state_manager

logger = logging.getLogger("rainloc-backend.aemet_atom")

# Namespaces XML estándar
NS_ATOM = {"atom": "http://www.w3.org/2005/Atom"}
NS_CAP = {"cap": "urn:oasis:names:tc:emergency:cap:1.2"}

# Mapeo de severidad y colores para visualización en mapa
SEVERITY_COLORS = {
    "yellow": "#FACC15",
    "amarillo": "#FACC15",
    "moderate": "#FACC15",
    "orange": "#FB923C",
    "naranja": "#FB923C",
    "severe": "#FB923C",
    "red": "#EF4444",
    "rojo": "#EF4444",
    "extreme": "#EF4444",
    "green": "#22C55E",
    "verde": "#22C55E",
    "minor": "#22C55E"
}

def parse_cap_xml(xml_bytes: bytes, cap_url: str) -> Optional[Dict[str, Any]]:
    """
    Convierte un documento XML en estándar CAP 1.2 a una Feature GeoJSON.
    Extrae polígonos (<polygon>), severidad, vigencia y descripción.
    """
    try:
        root = ET.fromstring(xml_bytes)
        identifier = root.findtext("cap:identifier", "", NS_CAP)
        sent = root.findtext("cap:sent", "", NS_CAP)
        msg_type = root.findtext("cap:msgType", "Alert", NS_CAP)
        
        # Buscar el bloque <info> en español si existe, o el primero disponible
        target_info = None
        for info in root.findall("cap:info", NS_CAP):
            if info.findtext("cap:language", "", NS_CAP).startswith("es"):
                target_info = info
                break
        if target_info is None:
            target_info = root.find("cap:info", NS_CAP)

        if target_info is None:
            return None

        event = target_info.findtext("cap:event", "Aviso meteorológico", NS_CAP)
        urgency = target_info.findtext("cap:urgency", "", NS_CAP)
        severity = target_info.findtext("cap:severity", "Moderate", NS_CAP)
        certainty = target_info.findtext("cap:certainty", "", NS_CAP)
        headline = target_info.findtext("cap:headline", "", NS_CAP)
        description = target_info.findtext("cap:description", "", NS_CAP)
        instruction = target_info.findtext("cap:instruction", "", NS_CAP)
        effective = target_info.findtext("cap:effective", "", NS_CAP)
        onset = target_info.findtext("cap:onset", "", NS_CAP)
        expires = target_info.findtext("cap:expires", "", NS_CAP)

        # Determinar color de nivel
        level_color = "#FACC15"
        for key, color in SEVERITY_COLORS.items():
            if key in severity.lower() or key in headline.lower() or key in event.lower():
                level_color = color
                break

        # Extraer geometría del área
        area = target_info.find("cap:area", NS_CAP)
        area_desc = area.findtext("cap:areaDesc", "", NS_CAP) if area is not None else ""
        
        polygon_str = area.findtext("cap:polygon", "", NS_CAP) if area is not None else ""
        
        geometry = None
        if polygon_str and polygon_str.strip():
            # CAP especifica los pares como: "lat,lon lat,lon ..."
            # GeoJSON requiere: [[lon, lat], [lon, lat], ...]
            coords: List[List[float]] = []
            for pair in polygon_str.strip().split():
                if "," in pair:
                    try:
                        lat_str, lon_str = pair.split(",")
                        coords.append([round(float(lon_str), 5), round(float(lat_str), 5)])
                    except ValueError:
                        continue
            
            if coords:
                # Asegurar cierre de anillo poligonal
                if coords[0] != coords[-1]:
                    coords.append(coords[0])
                geometry = {
                    "type": "Polygon",
                    "coordinates": [coords]
                }

        feature = {
            "type": "Feature",
            "id": identifier or cap_url,
            "geometry": geometry,
            "properties": {
                "source": "AEMET Meteoalerta",
                "cap_url": cap_url,
                "identifier": identifier,
                "sent": sent,
                "msg_type": msg_type,
                "event": event,
                "severity": severity,
                "color": level_color,
                "headline": headline,
                "description": description,
                "instruction": instruction,
                "area_desc": area_desc,
                "effective": effective,
                "onset": onset,
                "expires": expires
            }
        }
        return feature
    except Exception as e:
        logger.error(f"Error parseando CAP {cap_url}: {e}")
        return None


class AemetAtomService:
    """Servicio cliente para sincronizar periódicamente el feed ATOM de AEMET."""

    def __init__(self):
        self.atom_url = settings.AEMET_ATOM_URL
        self.user_agent = settings.AEMET_USER_AGENT
        self.timeout = 15

    def _sync_fetch_feed(self) -> Tuple[int, Optional[bytes], Optional[str], Optional[str]]:
        """
        Realiza la petición HTTP condicional al Feed ATOM utilizando If-Modified-Since y ETag.
        Retorna (status_code, content_bytes, last_modified, etag)
        """
        meta = weather_state_manager.get_aemet_status()
        last_modified = meta.get("last_modified")
        etag = meta.get("etag")

        req = urllib.request.Request(self.atom_url, headers={
            "User-Agent": self.user_agent,
            "Accept": "application/atom+xml, application/xml, text/xml"
        })

        if last_modified:
            req.add_header("If-Modified-Since", last_modified)
        if etag:
            req.add_header("If-None-Match", etag)

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                status = resp.status
                content = resp.read()
                new_last_modified = resp.headers.get("Last-Modified")
                new_etag = resp.headers.get("ETag")
                return status, content, new_last_modified, new_etag
        except urllib.error.HTTPError as e:
            if e.code == 304:
                return 304, None, last_modified, etag
            logger.warning(f"Error HTTP consultando feed AEMET: {e.code} {e.reason}")
            return e.code, None, last_modified, etag
        except Exception as e:
            logger.error(f"Error de red consultando feed AEMET: {e}")
            return 500, None, last_modified, etag

    def _sync_fetch_cap_file(self, cap_url: str) -> Optional[bytes]:
        """Descarga un fichero CAP individual."""
        req = urllib.request.Request(cap_url, headers={"User-Agent": self.user_agent})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return resp.read()
        except Exception as e:
            logger.warning(f"No se pudo descargar CAP {cap_url}: {e}")
            return None

    async def check_and_update(self) -> Dict[str, Any]:
        """
        Ejecuta la comprobación asíncrona del feed sin bloquear el bucle de eventos.
        Si hay cambios, descarga los CAPs necesarios y actualiza WeatherStateManager.
        """
        loop = asyncio.get_running_loop()
        status_code, content, last_mod, etag = await loop.run_in_executor(None, self._sync_fetch_feed)

        if status_code == 304:
            logger.info("Feed AEMET: HTTP 304 Not Modified. Datos vigentes, 0 bytes transferidos.")
            weather_state_manager.record_304_not_modified()
            return {"status": "not_modified", "code": 304}

        if status_code != 200 or not content:
            logger.warning(f"Feed AEMET: Respuesta no satisfactoria (status={status_code})")
            return {"status": "error", "code": status_code}

        logger.info(f"Feed AEMET: HTTP 200 OK. Novedades detectadas (Last-Modified: {last_mod}). Procesando...")

        # Parsear entradas del Feed ATOM
        try:
            root = ET.fromstring(content)
            entries = root.findall("atom:entry", NS_ATOM)
        except Exception as e:
            logger.error(f"Error al parsear el XML ATOM de AEMET: {e}")
            return {"status": "parse_error", "error": str(e)}

        cap_links: List[str] = []
        for entry in entries:
            link = entry.find("atom:link", NS_ATOM)
            if link is not None and "href" in link.attrib:
                href = link.attrib["href"]
                if href.endswith(".xml"):
                    cap_links.append(href)

        logger.info(f"Entradas en feed ATOM: {len(cap_links)}. Verificando caché de CAPs...")

        # Obtener/Actualizar CAPs individuales reutilizando caché en memoria
        new_features: List[Dict[str, Any]] = []
        cached_caps = weather_state_manager.cached_cap_features

        for cap_url in cap_links:
            # Si ya tenemos el CAP parseado en memoria, reutilizamos su geometría
            if cap_url in cached_caps and cached_caps[cap_url].get("feature"):
                new_features.append(cached_caps[cap_url]["feature"])
                continue

            # Descargar CAP nuevo
            cap_bytes = await loop.run_in_executor(None, self._sync_fetch_cap_file, cap_url)
            if cap_bytes:
                feat = parse_cap_xml(cap_bytes, cap_url)
                if feat:
                    cached_caps[cap_url] = {"feature": feat}
                    new_features.append(feat)

        # Limpiar entradas de caché huérfanas que ya no están en el feed actual
        current_urls_set = set(cap_links)
        obsolete_urls = [u for u in cached_caps if u not in current_urls_set]
        for u in obsolete_urls:
            del cached_caps[u]

        # Guardar en el WeatherStateManager
        weather_state_manager.update_aemet_warnings(
            features=new_features,
            last_modified=last_mod,
            etag=etag
        )

        logger.info(f"Avisos AEMET actualizados con éxito: {len(new_features)} avisos vigentes.")
        return {
            "status": "updated",
            "code": 200,
            "warnings_count": len(new_features),
            "last_modified": last_mod
        }

aemet_atom_service = AemetAtomService()
