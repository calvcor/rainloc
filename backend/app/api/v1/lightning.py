"""
Endpoints API para Rayos y Descargas Eléctricas en Tiempo Real.
"""

from typing import Optional
import json
from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse
from app.services.lightning_service import lightning_service

router = APIRouter(prefix="/lightning", tags=["Lightning"])

@router.get("/recent", summary="Obtener rayos recientes de los últimos N minutos (máx 15m)")
async def get_recent_lightning(
    minutes: int = Query(15, ge=1, le=15, description="Ventana de tiempo en minutos (1, 5 o 15)"),
    station_id: Optional[str] = Query(None, description="ID opcional de estación de radar para filtrar cobertura (240km)")
):
    """
    Devuelve la lista de descargas eléctricas detectadas en los últimos N minutos (hasta 15m)
    con opción de filtrar por radio de cobertura de la estación de radar indicada.
    """
    return await lightning_service.get_recent_strikes(minutes=minutes, station_id=station_id)

@router.get("/stream", summary="Stream de rayos en tiempo real (Server-Sent Events)")
async def stream_lightning():
    """
    Canal Server-Sent Events (SSE) para recibir impactos de rayo en el momento exacto
    en que son detectados por la red.
    """
    async def event_generator():
        async for strike in lightning_service.subscribe_stream():
            yield f"data: {json.dumps(strike)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )
