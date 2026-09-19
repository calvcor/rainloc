"""
Weather prediction model API endpoints (ECMWF IFS, NOAA GFS, AROME, ICON-D2).
Proporciona acceso a metadatos, rásteres Web Mercator (PNG) y consultas
instantáneas en punto geográfico para los modelos de predicción numérica ECMWF IFS y NOAA GFS.
"""
import json
import asyncio
import logging
from fastapi import APIRouter, Query, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from typing import Dict, Any, Optional

from app.services.ecmwf_worker import ecmwf_worker
from app.services.gfs_worker import gfs_worker
from app.services.arome_worker import arome_worker

logger = logging.getLogger("rainloc-backend.models-api")

router = APIRouter(prefix="/models", tags=["NWP Models"])


@router.get("/ecmwf/stream", summary="Stream de actualizaciones del modelo ECMWF IFS en tiempo real (SSE)")
async def stream_ecmwf():
    """
    Canal Server-Sent Events (SSE) para notificar inmediatamente a los clientes
    en cuanto el servidor descarga y procesa nuevos pasos o un ciclo completo de ECMWF IFS.
    """
    async def event_generator():
        async for event in ecmwf_worker.subscribe_stream():
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


@router.get("/ecmwf/metadata", summary="Metadatos y pasos disponibles del ciclo ECMWF IFS")
async def get_ecmwf_metadata() -> Dict[str, Any]:
    """
    Devuelve los metadatos del ciclo operativo actual de ECMWF IFS,
    los pasos disponibles (ej: +3h, +6h, ..., +72h), fechas ISO y URLs de los rásteres.
    """
    meta = ecmwf_worker.get_metadata()
    return meta


@router.get("/ecmwf/image", summary="Ráster PNG transparente del modelo ECMWF IFS")
async def get_ecmwf_image(
    step: int = Query(..., description="Paso de pronóstico en horas (3, 6, 9, ..., 72)"),
    type: str = Query("total", description="Tipo de mapa: 'total' (acumulado) o 'interval' (3 horas)")
):
    """
    Sirve la imagen PNG georreferenciada en proyección Web Mercator
    lista para ser consumida directamente por Leaflet (L.imageOverlay).
    """
    img_path = ecmwf_worker.get_image_path(step=step, layer_type=type)
    if not img_path or not img_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Imagen para el paso +{step}h ({type}) no encontrada o aún no generada."
        )

    return FileResponse(
        img_path,
        media_type="image/png",
        headers={
            "Cache-Control": "public, max-age=3600",
            "Access-Control-Allow-Origin": "*"
        }
    )


@router.get("/ecmwf/value-at", summary="Consulta instantánea de precipitación en coordenadas lat/lon")
async def get_ecmwf_value_at(
    lat: float = Query(..., description="Latitud WGS84"),
    lon: float = Query(..., description="Longitud WGS84"),
    step: int = Query(..., description="Paso de pronóstico en horas (3, 6, 9, ..., 72)"),
    type: str = Query("total", description="Tipo de mapa: 'total' (acumulado) o 'interval' (3 horas)")
) -> Dict[str, Any]:
    """
    Consulta en O(1) (<1ms) el valor en milímetros (mm) de la predicción ECMWF
    para las coordenadas indicadas.
    """
    val = ecmwf_worker.get_value_at(lat=lat, lon=lon, step=step, layer_type=type)
    return {
        "model": "ECMWF IFS",
        "lat": lat,
        "lon": lon,
        "step": step,
        "type": type,
        "value_mm": val,
        "unit": "mm"
    }


@router.post("/ecmwf/sync", summary="Forzar sincronización de ECMWF IFS en background")
async def sync_ecmwf_forecast(
    background_tasks: BackgroundTasks,
    max_steps: int = Query(240, description="Número máximo de horas a sincronizar (hasta +240h / 10 días)")
) -> Dict[str, Any]:
    """
    Dispara la sincronización asíncrona de los pasos del modelo ECMWF IFS.
    """
    logger.info(f"API: Petición manual para comprobar/sincronizar ECMWF IFS (hasta +{max_steps}h)")
    background_tasks.add_task(ecmwf_worker.sync_ecmwf_forecast, max_steps)
    return {
        "status": "synchronization_started",
        "max_steps": max_steps,
        "message": "La descarga y procesado de ECMWF IFS ha comenzado en segundo plano."
    }


# =========================================================================
# NOAA GFS (Global Forecast System, 0.25°) Endpoints
# =========================================================================

@router.get("/gfs/stream", summary="Stream de actualizaciones del modelo NOAA GFS en tiempo real (SSE)")
async def stream_gfs():
    """
    Canal Server-Sent Events (SSE) para notificar inmediatamente a los clientes
    en cuanto el servidor descarga y procesa nuevos pasos o un ciclo completo de NOAA GFS.
    """
    async def event_generator():
        async for event in gfs_worker.subscribe_stream():
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


@router.get("/gfs/metadata", summary="Metadatos y pasos disponibles del ciclo NOAA GFS")
async def get_gfs_metadata() -> Dict[str, Any]:
    """
    Devuelve los metadatos del ciclo operativo actual de NOAA GFS,
    los pasos disponibles (ej: +3h, +6h, ..., +384h), fechas ISO y URLs de los rásteres.
    """
    meta = gfs_worker.get_metadata()
    return meta


@router.get("/gfs/image", summary="Ráster PNG transparente del modelo NOAA GFS")
async def get_gfs_image(
    step: int = Query(..., description="Paso de pronóstico en horas (3, 6, 9, ..., 384)"),
    type: str = Query("total", description="Tipo de mapa: 'total' (acumulado) o 'interval' (3 horas)")
):
    """
    Sirve la imagen PNG georreferenciada en proyección Web Mercator
    lista para ser consumida directamente por Leaflet (L.imageOverlay).
    """
    img_path = gfs_worker.get_image_path(step=step, layer_type=type)
    if not img_path or not img_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Imagen GFS para el paso +{step}h ({type}) no encontrada o aún no generada."
        )

    return FileResponse(
        img_path,
        media_type="image/png",
        headers={
            "Cache-Control": "public, max-age=3600",
            "Access-Control-Allow-Origin": "*"
        }
    )


@router.get("/gfs/value-at", summary="Consulta instantánea de precipitación GFS en coordenadas lat/lon")
async def get_gfs_value_at(
    lat: float = Query(..., description="Latitud WGS84"),
    lon: float = Query(..., description="Longitud WGS84"),
    step: int = Query(..., description="Paso de pronóstico en horas (3, 6, 9, ..., 384)"),
    type: str = Query("total", description="Tipo de mapa: 'total' (acumulado) o 'interval' (3 horas)")
) -> Dict[str, Any]:
    """
    Consulta en O(1) (<1ms) el valor en milímetros (mm) de la predicción GFS
    para las coordenadas indicadas.
    """
    val = gfs_worker.get_value_at(lat=lat, lon=lon, step=step, layer_type=type)
    return {
        "model": "NOAA GFS",
        "lat": lat,
        "lon": lon,
        "step": step,
        "type": type,
        "value_mm": val,
        "unit": "mm"
    }


@router.post("/gfs/sync", summary="Forzar sincronización de NOAA GFS en background")
async def sync_gfs_forecast(
    background_tasks: BackgroundTasks,
    max_steps: int = Query(384, description="Número máximo de horas a sincronizar (hasta +384h / 16 días)")
) -> Dict[str, Any]:
    """
    Dispara la sincronización asíncrona de los pasos del modelo NOAA GFS.
    """
    logger.info(f"API: Petición manual para comprobar/sincronizar NOAA GFS (hasta +{max_steps}h)")
    background_tasks.add_task(gfs_worker.sync_gfs_forecast, max_steps)
    return {
        "status": "synchronization_started",
        "max_steps": max_steps,
        "message": "La descarga y procesado de NOAA GFS ha comenzado en segundo plano."
    }


# =========================================================================
# Météo-France / AEMET AROME (1.3 km) Endpoints
# =========================================================================

@router.get("/arome/stream", summary="Stream de actualizaciones del modelo AROME en tiempo real (SSE)")
async def stream_arome():
    """
    Canal Server-Sent Events (SSE) para notificar inmediatamente a los clientes
    en cuanto el servidor descarga y procesa nuevos pasos o un ciclo completo de AROME.
    """
    async def event_generator():
        async for event in arome_worker.subscribe_stream():
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


@router.get("/arome/metadata", summary="Metadatos y pasos disponibles del ciclo AROME")
async def get_arome_metadata() -> Dict[str, Any]:
    """
    Devuelve los metadatos del ciclo operativo actual de AROME,
    los pasos disponibles (ej: +1h, +2h, ..., +48h), fechas ISO y estado de actualización.
    """
    meta = arome_worker.get_metadata()
    return meta


@router.get("/arome/image", summary="Ráster PNG transparente del modelo AROME")
async def get_arome_image(
    step: int = Query(..., description="Paso de pronóstico en horas (1, 2, ..., 48)"),
    type: str = Query("total", description="Tipo de mapa: 'total' (acumulado) o 'interval' (1 hora)")
):
    """
    Sirve la imagen PNG georreferenciada en proyección Web Mercator
    lista para ser consumida directamente por Leaflet (L.imageOverlay).
    """
    img_path = arome_worker.get_image_path(step=step, layer_type=type)
    if not img_path or not img_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Imagen AROME para el paso +{step}h ({type}) no encontrada o aún no generada."
        )

    return FileResponse(
        img_path,
        media_type="image/png",
        headers={
            "Cache-Control": "public, max-age=3600",
            "Access-Control-Allow-Origin": "*"
        }
    )


@router.get("/arome/value-at", summary="Consulta instantánea de precipitación AROME en coordenadas lat/lon")
async def get_arome_value_at(
    lat: float = Query(..., description="Latitud WGS84"),
    lon: float = Query(..., description="Longitud WGS84"),
    step: int = Query(..., description="Paso de pronóstico en horas (1, 2, ..., 48)"),
    type: str = Query("total", description="Tipo de mapa: 'total' (acumulado) o 'interval' (1 hora)")
) -> Dict[str, Any]:
    """
    Consulta en O(1) (<1ms) el valor en milímetros (mm) de la predicción AROME
    para las coordenadas indicadas.
    """
    val = arome_worker.get_value_at(lat=lat, lon=lon, step=step, layer_type=type)
    return {
        "model": "Météo-France AROME",
        "lat": lat,
        "lon": lon,
        "step": step,
        "type": type,
        "value_mm": val,
        "unit": "mm"
    }


@router.post("/arome/sync", summary="Forzar sincronización de AROME en background")
async def sync_arome_forecast(
    background_tasks: BackgroundTasks,
    max_steps: int = Query(48, description="Número máximo de horas a sincronizar (hasta +48h / 2 días)")
) -> Dict[str, Any]:
    """
    Dispara la sincronización asíncrona de los pasos del modelo AROME.
    """
    logger.info(f"API: Petición manual para comprobar/sincronizar Météo-France AROME (hasta +{max_steps}h)")
    background_tasks.add_task(arome_worker.sync_arome_forecast, max_steps)
    return {
        "status": "synchronization_started",
        "max_steps": max_steps,
        "message": "La descarga y procesado de AROME ha comenzado en segundo plano."
    }


@router.get("/icon-d2", summary="ICON-D2 Model (DWD)")
async def get_icon_d2_model() -> Dict[str, Any]:
    return {
        "model": "ICON-D2",
        "description": "Modelo convectivo del Deutscher Wetterdienst (2.2 km)",
        "status": "ready_for_ingestion",
        "parameters": [
            {"id": "accumulated_rain", "name": "Precipitación acumulada (mm)", "unit": "mm"},
            {"id": "wind_gusts", "name": "Rachas de viento", "unit": "km/h"}
        ],
        "timesteps": []
    }
