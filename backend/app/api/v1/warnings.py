"""
Endpoints para Avisos Meteorológicos Adversos (AEMET Meteoalerta)
Servidos desde el almacén central del estado del tiempo (WeatherStateManager).
"""
from typing import Dict, Any, Optional
from fastapi import APIRouter, BackgroundTasks, Query
from app.services.weather_state import weather_state_manager
from app.services.aemet_atom import aemet_atom_service

router = APIRouter(prefix="/warnings", tags=["Avisos Meteorológicos"])

@router.get("/aemet", summary="Obtener GeoJSON de avisos meteorológicos de AEMET por periodo")
async def get_aemet_warnings(
    period: str = Query(
        "now",
        description="Periodo temporal: 'now' (activos ahora), 'tomorrow' (mañana), 'after_tomorrow' (pasado mañana), o 'all'"
    ),
    only_active_now: Optional[bool] = Query(
        None,
        description="Legacy: Si es True equivale a period='now', si es False equivale a period='all'"
    )
) -> Dict[str, Any]:
    """
    Devuelve la colección de avisos de AEMET en formato GeoJSON FeatureCollection según el periodo seleccionado
    (Activos ahora, Mañana o Pasado) con consolidación automática de avisos solapados por comarca/zona.
    """
    effective_period = period if isinstance(period, str) else "now"
    effective_only_active = only_active_now if isinstance(only_active_now, bool) else None
    return weather_state_manager.get_aemet_warnings_geojson(period=effective_period, only_active_now=effective_only_active)

@router.get("/status", summary="Estado de sincronización del feed ATOM de AEMET")
async def get_aemet_status() -> Dict[str, Any]:
    """
    Devuelve los metadatos de sincronización: última consulta, avisos en feed total,
    avisos activos ahora, y peticiones ahorradas mediante HTTP 304.
    """
    return weather_state_manager.get_aemet_status()

@router.post("/refresh", summary="Forzar comprobación inmediata del feed de AEMET")
async def force_aemet_refresh(background_tasks: BackgroundTasks) -> Dict[str, Any]:
    """
    Lanza una comprobación inmediata del feed en segundo plano respetando If-Modified-Since.
    """
    background_tasks.add_task(aemet_atom_service.check_and_update)
    return {
        "status": "refresh_triggered",
        "message": "Comprobación de avisos lanzada en segundo plano"
    }
