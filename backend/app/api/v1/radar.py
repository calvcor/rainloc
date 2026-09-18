"""
Endpoints REST para Radar Meteorológico (Compuesto de España y Radares Individuales)
"""
import json
from pathlib import Path
from typing import Dict, Any, Optional
from fastapi import APIRouter, HTTPException, Query, BackgroundTasks
from fastapi.responses import FileResponse, StreamingResponse

from app.services.radar_worker import radar_service

router = APIRouter(prefix="/radar", tags=["Radar y Acumulados"])

@router.get("/metadata", summary="Metadatos de radar, modos y timesteps disponibles")
async def get_radar_metadata() -> Dict[str, Any]:
    """
    Devuelve los metadatos globales de radar: timesteps disponibles del compuesto
    de España, coordenadas de bounds geográficos, estaciones individuales y escala dBZ.
    """
    return radar_service.get_metadata()

@router.get("/image", summary="Obtener imagen PNG transparente del radar")
async def get_radar_image(
    mode: str = Query("composite", description="Modo: 'composite' (España) o 'single' (estación)"),
    station_id: Optional[str] = Query(None, description="Identificador de la estación (ej: esbnv, esahr, esclg)")
):
    """
    Retorna la imagen PNG RGBA con fondo transparente lista para Leaflet L.imageOverlay.
    """
    if mode == "composite":
        img_path = radar_service.get_composite_image_path()
        if not img_path or not img_path.exists():
            raise HTTPException(status_code=404, detail="Compuesto de radar aún no generado o en proceso de descarga")
        return FileResponse(img_path, media_type="image/png")
    elif mode == "single":
        if not station_id:
            raise HTTPException(status_code=400, detail="station_id es obligatorio en modo 'single'")
        img_path = radar_service.get_station_image_path(station_id)
        if not img_path or not img_path.exists():
            raise HTTPException(status_code=404, detail=f"Imagen no disponible para el radar {station_id}")
        return FileResponse(img_path, media_type="image/png")
    else:
        raise HTTPException(status_code=400, detail="Modo no válido. Usa 'composite' o 'single'")

@router.get("/value-at", summary="Obtener valor de reflectividad dBZ puntual")
async def get_radar_value_at(
    lat: float = Query(..., description="Latitud en WGS84"),
    lon: float = Query(..., description="Longitud en WGS84"),
    mode: str = Query("composite"),
    station_id: Optional[str] = Query(None)
) -> Dict[str, Any]:
    """
    Consulta el valor físico en dBZ para un punto geográfico (para el Inspector Multi-Capa en hover).
    """
    val = radar_service.get_dbz_at_point(lat, lon, mode, station_id)
    intensity = "Sin eco"
    if val is not None:
        if val >= 55:
            intensity = "Muy Fuerte / Granizo"
        elif val >= 45:
            intensity = "Muy Fuerte"
        elif val >= 35:
            intensity = "Fuerte"
        elif val >= 25:
            intensity = "Moderada"
        elif val >= 15:
            intensity = "Ligera"
        elif val >= 8:
            intensity = "Débil"

    return {
        "lat": lat,
        "lon": lon,
        "dbz": val,
        "rain_intensity": intensity
    }

@router.get("/stream", summary="Stream de actualizaciones de radar en tiempo real (Server-Sent Events)")
async def stream_radar():
    """
    Canal Server-Sent Events (SSE) para notificar inmediatamente a los clientes
    en cuanto el servidor descarga y procesa un nuevo compuesto de radar.
    """
    async def event_generator():
        async for event in radar_service.subscribe_stream():
            if event.get("event") == "ping":
                yield ": ping\n\n"
            else:
                yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )

@router.post("/refresh", summary="Forzar comprobación inmediata de nuevos datos de radar")
async def refresh_radar(background_tasks: BackgroundTasks) -> Dict[str, Any]:
    """
    Lanza una comprobación inmediata de radar en segundo plano.
    """
    background_tasks.add_task(radar_service.sync_radar_data)
    return {
        "status": "refresh_triggered",
        "message": "Comprobación de radar lanzada en segundo plano"
    }
