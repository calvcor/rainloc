"""
Endpoints REST para Radar Meteorológico (Compuesto de España y Línea Temporal 24 Horas)
"""
import json
from pathlib import Path
from typing import Dict, Any, Optional, List
from fastapi import APIRouter, HTTPException, Query, BackgroundTasks
from fastapi.responses import FileResponse, StreamingResponse

from app.services.radar_worker import radar_service

router = APIRouter(prefix="/radar", tags=["Radar y Acumulados"])


@router.get("/metadata", summary="Metadatos de radar, modos y línea temporal disponible")
async def get_radar_metadata() -> Dict[str, Any]:
    """
    Devuelve los metadatos globales de radar: línea temporal disponible (últimas 24h a 5m),
    coordenadas de bounds geográficos, modos y escala dBZ.
    """
    return radar_service.get_metadata()


@router.get("/stations", summary="Catálogo de estaciones de radar y parámetros de cobertura")
async def get_radar_stations() -> Dict[str, Any]:
    """
    Retorna el catálogo verificado de radares de la red AEMET/ORD con sus coordenadas, radios y nombres.
    """
    meta = radar_service.get_metadata()
    return {
        "stations": meta.get("stations", {}),
        "short_range_max_km": meta.get("short_range_max_km", 145.0),
        "latest_stations": radar_service.state.get("latest_stations", {})
    }


@router.get("/timeline", summary="Lista cronológica de fotogramas de radar de las últimas 24 horas")
async def get_radar_timeline() -> List[Dict[str, Any]]:
    """
    Devuelve la lista ordenada de fotogramas disponibles en las últimas 24 horas (~288 fotogramas a 5 min),
    con sus URLs, timestamps ISO y horas locales de Madrid.
    """
    return radar_service.get_timeline()


@router.get("/image", summary="Obtener imagen PNG transparente del radar")
async def get_radar_image(
    mode: str = Query("mixed", description="Modo: 'mixed' (compuesto mixto), 'short_range' (corto alcance), 'long_range' (largo alcance), 'composite' o 'single'"),
    station_id: Optional[str] = Query(None, description="Identificador de la estación"),
    timestep: Optional[str] = Query(None, description="Identificador del fotograma (ej: 20260919T1305)")
):
    """
    Retorna la imagen PNG RGBA con fondo transparente lista para Leaflet L.imageOverlay.
    Soporta los modos 'mixed' (recomendado), 'short_range', 'long_range' y consultar fotogramas históricos de las últimas 24 horas.
    """
    if mode in ("mixed", "composite", "short_range", "long_range"):
        img_path = radar_service.get_composite_image_path(mode=mode, timestep=timestep)
        if not img_path or not img_path.exists():
            raise HTTPException(status_code=404, detail="Fotograma de radar no encontrado o en proceso de descarga")
        
        # Si se solicita un timestep histórico concreto, cachear a largo plazo en Cloudflare / navegador
        is_historical = bool(timestep and timestep in img_path.name and not img_path.name.startswith("latest_"))
        cache_control = "public, max-age=86400, s-maxage=86400" if is_historical else "public, max-age=30, s-maxage=30"

        return FileResponse(
            img_path,
            media_type="image/png",
            headers={
                "Cache-Control": cache_control,
                "Access-Control-Allow-Origin": "*"
            }
        )
    elif mode == "single":
        if not station_id:
            raise HTTPException(status_code=400, detail="station_id es obligatorio en modo 'single'")
        img_path = radar_service.get_station_image_path(station_id, timestep=timestep)
        if not img_path or not img_path.exists():
            raise HTTPException(status_code=404, detail=f"Imagen no disponible para el radar {station_id}")
        return FileResponse(
            img_path,
            media_type="image/png",
            headers={
                "Cache-Control": "public, max-age=30, s-maxage=30",
                "Access-Control-Allow-Origin": "*"
            }
        )
    else:
        raise HTTPException(status_code=400, detail="Modo no válido. Usa 'mixed', 'short_range', 'long_range' o 'single'")


@router.get("/value-at", summary="Obtener valor de reflectividad dBZ puntual")
async def get_radar_value_at(
    lat: float = Query(..., description="Latitud en WGS84"),
    lon: float = Query(..., description="Longitud en WGS84"),
    mode: str = Query("composite"),
    station_id: Optional[str] = Query(None),
    timestep: Optional[str] = Query(None, description="Identificador del fotograma (ej: 20260919T1305)")
) -> Dict[str, Any]:
    """
    Consulta el valor físico en dBZ para un punto geográfico e instante temporal seleccionado (para el Inspector Multi-Capa en hover).
    """
    val = radar_service.get_dbz_at_point(lat, lon, mode, station_id, timestep=timestep)
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
        "timestep": timestep,
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
