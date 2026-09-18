"""
Endpoints para Avisos Meteorológicos Adversos (AEMET Meteoalerta)
Servidos desde el almacén central del estado del tiempo (WeatherStateManager).
"""
from typing import Dict, Any
from fastapi import APIRouter, BackgroundTasks, Query
from app.services.weather_state import weather_state_manager
from app.services.aemet_atom import aemet_atom_service

router = APIRouter(prefix="/warnings", tags=["Avisos Meteorológicos"])

@router.get("/aemet", summary="Obtener GeoJSON de avisos meteorológicos activos de AEMET")
async def get_aemet_warnings(
    only_active_now: bool = Query(
        True,
        description="Si es True, devuelve exclusivamente los avisos vigentes en este instante preciso (onset <= ahora <= expires) y consolida avisos solapados"
    )
) -> Dict[str, Any]:
    """
    Devuelve la colección de avisos de AEMET en formato GeoJSON FeatureCollection.
    Por defecto filtra estrictamente los avisos ACTIVOS AHORA MISMO, evitando mostrar
    avisos futuros de mañana o pasado mañana o avisos caducados.
    """
    return weather_state_manager.get_aemet_warnings_geojson(only_active_now=only_active_now)

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
