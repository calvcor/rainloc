"""
Weather State Manager: Almacén central del estado meteorológico de RainLoc.
Mantiene el estado en memoria y persiste periódicamente un snapshot en disco.
"""
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional, List

from app.config import settings

logger = logging.getLogger("rainloc-backend.weather_state")

SEVERITY_RANK = {
    "red": 4, "rojo": 4, "extreme": 4,
    "orange": 3, "naranja": 3, "severe": 3,
    "yellow": 2, "amarillo": 2, "moderate": 2,
    "green": 1, "verde": 1, "minor": 1
}

class WeatherStateManager:
    """
    Singleton que centraliza el estado actual del tiempo:
    - Avisos meteorológicos activos (AEMET)
    - Metadatos de sincronización (Last-Modified, ETag, contadores de 304)
    - Próximamente: Radar QPE, Modelos, etc.
    """
    _instance: Optional["WeatherStateManager"] = None

    def __new__(cls) -> "WeatherStateManager":
        if cls._instance is None:
            cls._instance = super(WeatherStateManager, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if getattr(self, "_initialized", False):
            return
        self._initialized = True
        
        self.state_file: Path = settings.WEATHER_STATE_FILE
        
        # Estructura del estado
        self.aemet_warnings: Dict[str, Any] = {
            "type": "FeatureCollection",
            "metadata": {
                "source": "AEMET Meteoalerta (Feed ATOM)",
                "last_check": None,
                "last_modified": None,
                "etag": None,
                "total_checks": 0,
                "saved_requests_304": 0,
                "active_warnings_count": 0,
                "status": "initializing"
            },
            "features": []
        }
        
        # Caché de CAPs individuales: { cap_url: { "feature": dict } }
        self.cached_cap_features: Dict[str, Dict[str, Any]] = {}
        
        self._load_from_disk()

    def _load_from_disk(self) -> None:
        """Carga el último snapshot persistido si existe en disco."""
        if not self.state_file.exists():
            return
        try:
            with open(self.state_file, "r", encoding="utf-8") as f:
                saved = json.load(f)
                if "aemet_warnings" in saved:
                    self.aemet_warnings = saved["aemet_warnings"]
                if "cached_cap_features" in saved:
                    self.cached_cap_features = saved["cached_cap_features"]
            logger.info(f"Estado del tiempo restaurado desde {self.state_file} ({len(self.aemet_warnings.get('features', []))} avisos en feed)")
        except Exception as e:
            logger.error(f"No se pudo cargar el snapshot del estado: {e}")

    def save_to_disk(self) -> None:
        """Persiste un snapshot JSON atómico en disco."""
        try:
            temp_file = self.state_file.with_suffix(".tmp")
            payload = {
                "saved_at": datetime.now(timezone.utc).isoformat(),
                "aemet_warnings": self.aemet_warnings,
                "cached_cap_features": self.cached_cap_features
            }
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            temp_file.replace(self.state_file)
        except Exception as e:
            logger.error(f"Error al guardar snapshot del estado: {e}")

    def get_aemet_warnings_geojson(self, only_active_now: bool = True) -> Dict[str, Any]:
        """
        Devuelve la FeatureCollection de avisos.
        Si only_active_now=True (por defecto), filtra ESTRICTAMENTE los avisos cuyo periodo
        de vigencia (onset <= ahora <= expires) esté en curso en este momento.
        También agrupa y consolida avisos por zona para evitar polígonos duplicados o stackeados.
        """
        all_features = self.aemet_warnings.get("features", [])
        
        if not only_active_now:
            return self.aemet_warnings

        now = datetime.now(timezone.utc)
        active_features: List[Dict[str, Any]] = []

        for f in all_features:
            props = f.get("properties", {})
            onset_str = props.get("onset") or props.get("effective")
            expires_str = props.get("expires")
            if not onset_str or not expires_str:
                continue

            try:
                onset = datetime.fromisoformat(onset_str)
                expires = datetime.fromisoformat(expires_str)
                # Solo avisos en vigor exactamente AHORA
                if onset <= now <= expires:
                    active_features.append(f)
            except Exception:
                continue

        # Consolidar por zona geográfica (area_desc) para evitar polígonos solapados/stackeados
        by_area: Dict[str, List[Dict[str, Any]]] = {}
        for f in active_features:
            area = f.get("properties", {}).get("area_desc", "Zona")
            by_area.setdefault(area, []).append(f)

        consolidated_features: List[Dict[str, Any]] = []

        for area, feats in by_area.items():
            # Ordenar por nivel de severidad más crítico primero (Red > Orange > Yellow)
            feats.sort(
                key=lambda item: SEVERITY_RANK.get(
                    item.get("properties", {}).get("severity", "").lower(), 0
                ),
                reverse=True
            )
            top_feat = feats[0]
            
            # Si hay múltiples avisos para la misma zona (ej. Lluvias + Tormentas):
            if len(feats) > 1:
                combined_events = []
                combined_descriptions = []
                all_warnings_info = []

                for sub in feats:
                    sub_p = sub.get("properties", {})
                    combined_events.append(sub_p.get("event", ""))
                    all_warnings_info.append({
                        "event": sub_p.get("event"),
                        "severity": sub_p.get("severity"),
                        "color": sub_p.get("color"),
                        "headline": sub_p.get("headline"),
                        "description": sub_p.get("description"),
                        "expires": sub_p.get("expires")
                    })
                    if sub_p.get("description"):
                        combined_descriptions.append(f"[{sub_p.get('event', 'Aviso')}]: {sub_p.get('description')}")

                # Clonar propiedades para no mutar el objeto original en caché
                new_props = dict(top_feat.get("properties", {}))
                new_props["stacked_count"] = len(feats)
                new_props["events_list"] = combined_events
                new_props["event"] = " + ".join(dict.fromkeys(combined_events))
                new_props["combined_warnings"] = all_warnings_info
                new_props["description"] = " \n".join(combined_descriptions)

                consolidated_features.append({
                    "type": "Feature",
                    "id": top_feat.get("id"),
                    "geometry": top_feat.get("geometry"),
                    "properties": new_props
                })
            else:
                consolidated_features.append(top_feat)

        return {
            "type": "FeatureCollection",
            "metadata": {
                **self.aemet_warnings.get("metadata", {}),
                "filter": "active_now",
                "timestamp_utc": now.isoformat(),
                "feed_total_warnings": len(all_features),
                "active_now_count": len(consolidated_features)
            },
            "features": consolidated_features
        }

    def get_aemet_status(self) -> Dict[str, Any]:
        """Devuelve los metadatos de sincronización con AEMET."""
        now = datetime.now(timezone.utc)
        all_features = self.aemet_warnings.get("features", [])
        active_now_count = 0
        for f in all_features:
            p = f.get("properties", {})
            o, e = p.get("onset") or p.get("effective"), p.get("expires")
            if o and e:
                try:
                    if datetime.fromisoformat(o) <= now <= datetime.fromisoformat(e):
                        active_now_count += 1
                except Exception:
                    pass

        meta = dict(self.aemet_warnings.get("metadata", {}))
        meta["feed_total_warnings"] = len(all_features)
        meta["active_now_count"] = active_now_count
        return meta

    def record_304_not_modified(self) -> None:
        """Registra una respuesta 304 Not Modified de AEMET."""
        meta = self.aemet_warnings["metadata"]
        meta["last_check"] = datetime.now(timezone.utc).isoformat()
        meta["total_checks"] = meta.get("total_checks", 0) + 1
        meta["saved_requests_304"] = meta.get("saved_requests_304", 0) + 1
        meta["status"] = "synced_304"
        self.save_to_disk()

    def update_aemet_warnings(
        self,
        features: List[Dict[str, Any]],
        last_modified: Optional[str],
        etag: Optional[str]
    ) -> None:
        """Actualiza la lista de avisos cuando el feed ha cambiado (HTTP 200)."""
        meta = self.aemet_warnings["metadata"]
        meta["last_check"] = datetime.now(timezone.utc).isoformat()
        meta["total_checks"] = meta.get("total_checks", 0) + 1
        meta["last_modified"] = last_modified or meta.get("last_modified")
        meta["etag"] = etag or meta.get("etag")
        meta["active_warnings_count"] = len(features)
        meta["status"] = "synced_200"
        
        self.aemet_warnings["features"] = features
        self.save_to_disk()

weather_state_manager = WeatherStateManager()
