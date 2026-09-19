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
from app.services.basin_hydrology import basin_hydrology_service

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

    cur_cycle = ecmwf_worker.current_manifest.get("cycle_str") if ecmwf_worker.current_manifest else ""
    is_current = bool(cur_cycle and cur_cycle in str(img_path))
    cache_control = "public, max-age=86400, s-maxage=86400" if is_current else "no-cache, no-store, must-revalidate, max-age=0, s-maxage=0"

    return FileResponse(
        img_path,
        media_type="image/png",
        headers={
            "Cache-Control": cache_control,
            "Access-Control-Allow-Origin": "*"
        }
    )


@router.get("/ecmwf/value-at", summary="Consulta instantánea de precipitación en coordenadas lat/lon")
async def get_ecmwf_value_at(
    lat: float = Query(..., description="Latitud WGS84"),
    lon: float = Query(..., description="Longitud WGS84"),
    step: int = Query(..., description="Paso de pronóstico en horas (3, 6, 9, ..., 240)"),
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


@router.get("/ecmwf/max-at", summary="Obtener punto y valor de máxima precipitación de ECMWF IFS")
async def get_ecmwf_max_at(
    step: int = Query(..., description="Paso de pronóstico en horas"),
    type: str = Query("total", description="Tipo de mapa: 'total' o 'interval'")
) -> Dict[str, Any]:
    res = ecmwf_worker.get_max_point(step=step, layer_type=type)
    if not res:
        return {"model": "ECMWF IFS", "step": step, "type": type, "lat": None, "lon": None, "value_mm": 0.0}
    return {
        "model": "ECMWF IFS",
        "step": step,
        "type": type,
        **res,
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

    cur_cycle = gfs_worker.current_manifest.get("cycle_str") if gfs_worker.current_manifest else ""
    is_current = bool(cur_cycle and cur_cycle in str(img_path))
    cache_control = "public, max-age=86400, s-maxage=86400" if is_current else "no-cache, no-store, must-revalidate, max-age=0, s-maxage=0"

    return FileResponse(
        img_path,
        media_type="image/png",
        headers={
            "Cache-Control": cache_control,
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


@router.get("/gfs/max-at", summary="Obtener punto y valor de máxima precipitación de NOAA GFS")
async def get_gfs_max_at(
    step: int = Query(..., description="Paso de pronóstico en horas"),
    type: str = Query("total", description="Tipo de mapa: 'total' o 'interval'")
) -> Dict[str, Any]:
    res = gfs_worker.get_max_point(step=step, layer_type=type)
    if not res:
        return {"model": "NOAA GFS", "step": step, "type": type, "lat": None, "lon": None, "value_mm": 0.0}
    return {
        "model": "NOAA GFS",
        "step": step,
        "type": type,
        **res,
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

    cur_cycle = arome_worker.current_manifest.get("cycle_str") if arome_worker.current_manifest else ""
    is_current = bool(cur_cycle and cur_cycle in str(img_path))
    cache_control = "public, max-age=86400, s-maxage=86400" if is_current else "no-cache, no-store, must-revalidate, max-age=0, s-maxage=0"

    return FileResponse(
        img_path,
        media_type="image/png",
        headers={
            "Cache-Control": cache_control,
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


@router.get("/arome/max-at", summary="Obtener punto y valor de máxima precipitación de Météo-France AROME")
async def get_arome_max_at(
    step: int = Query(..., description="Paso de pronóstico en horas"),
    type: str = Query("total", description="Tipo de mapa: 'total' o 'interval'")
) -> Dict[str, Any]:
    res = arome_worker.get_max_point(step=step, layer_type=type)
    if not res:
        return {"model": "Météo-France AROME", "step": step, "type": type, "lat": None, "lon": None, "value_mm": 0.0}
    return {
        "model": "Météo-France AROME",
        "step": step,
        "type": type,
        **res,
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


# =========================================================================
# Cuencas Hidrográficas & Cálculo Hidrológico Espacial al Vuelo (hm³)
# =========================================================================

@router.get("/basins", summary="Catálogo de cuencas y subsistemas precalculados")
async def list_model_basins() -> Dict[str, Any]:
    """
    Devuelve la lista de cuencas y subsistemas hidrográficos precomputados con su ID,
    nombre oficial, sistema de explotación y superficie oficial en km².
    """
    basins = basin_hydrology_service.list_basins()
    return {
        "count": len(basins),
        "basins": basins
    }


@router.get("/{model}/basin-hydrograph", summary="Hidrograma y volumen acumulado al vuelo para una cuenca")
async def get_model_basin_hydrograph(
    model: str,
    basin_id: str = Query(..., description="ID o nombre del subsistema de la cuenca (ej: '1', '2', 'TURIA')")
) -> Dict[str, Any]:
    """
    Calcula al vuelo (<2ms) la serie completa de precipitación media ponderada (mm),
    volumen hídrico acumulado e intervalar (hm³) y puntos de máxima precipitación
    sobre la cuenca especificada para todos los pasos temporales del modelo (ECMWF, GFS o AROME).
    """
    result = basin_hydrology_service.calculate_basin_hydrograph(model_key=model, basin_id=basin_id)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@router.get("/{model}/basin-volume", summary="Volumen y precipitación en cuenca para un paso específico")
async def get_model_basin_volume(
    model: str,
    basin_id: str = Query(..., description="ID o nombre del subsistema de la cuenca"),
    step: int = Query(..., description="Paso de pronóstico en horas"),
    type: str = Query("total", description="Tipo de cálculo: 'total' (acumulado) o 'interval' (intervalo)")
) -> Dict[str, Any]:
    """
    Calcula al vuelo (<0.2ms) los valores hidrológicos de una cuenca en un único paso temporal.
    """
    hydro = basin_hydrology_service.calculate_basin_hydrograph(model_key=model, basin_id=basin_id)
    if "error" in hydro:
        raise HTTPException(status_code=404, detail=hydro["error"])

    series = hydro.get("series", [])
    step_match = next((s for s in series if s["step"] == step), None)
    if not step_match:
        raise HTTPException(status_code=404, detail=f"Paso +{step}h no disponible para el modelo {model}")

    clean_type = "interval" if type.lower() == "interval" else "total"
    vol_hm3 = step_match["interval_vol_hm3"] if clean_type == "interval" else step_match["total_vol_hm3"]
    avg_mm = step_match["avg_interval_mm"] if clean_type == "interval" else step_match["avg_total_mm"]
    max_mm = step_match["max_point_interval_mm"] if clean_type == "interval" else step_match["max_point_mm"]

    return {
        "model": hydro["model_name"],
        "model_key": hydro["model_key"],
        "cycle_str": hydro["cycle_str"],
        "basin_id": hydro["basin_id"],
        "basin_name": hydro["basin_name"],
        "system_name": hydro["system_name"],
        "area_km2": hydro["area_km2"],
        "step": step,
        "type": clean_type,
        "valid_time_iso": step_match["valid_time_iso"],
        "valid_time_local": step_match["valid_time_local"],
        "volume_hm3": vol_hm3,
        "avg_precip_mm": avg_mm,
        "max_point_mm": max_mm,
        "total_accumulated_hm3": hydro["total_accumulated_hm3"]
    }

