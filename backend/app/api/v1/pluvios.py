"""
Endpoints de la API para consulta de pluviómetros de AEMET OpenData y unificados.
"""
from typing import Literal, Optional, List, Dict, Any
from fastapi import APIRouter, HTTPException, Query

from app.services.aemet_pluvios_service import aemet_pluvios_service
from app.services.saih_service import saih_service

router = APIRouter(tags=["Pluviómetros"])


@router.get("/aemet/lluvias", summary="Obtener pluviómetros de la red de AEMET OpenData")
@router.get("/aemet/pluvios", include_in_schema=False)
async def get_aemet_lluvias(
    format: Literal["geojson", "json"] = Query(
        "geojson",
        description="Formato de respuesta: 'geojson' (FeatureCollection) o 'json' (lista plana)",
    )
):
    """
    Devuelve las ~850 estaciones meteorológicas automáticas (EMA) de AEMET con lluvia acumulada
    en 1h, 4h, 12h y 24h (mm) calculadas bajo demanda.
    """
    if format == "geojson":
        return await aemet_pluvios_service.get_pluvios_geojson()
    return await aemet_pluvios_service.get_pluvios()


@router.get("/aemet/lluvias/{id_or_code}", summary="Obtener datos de una estación AEMET específica")
@router.get("/aemet/pluvios/{id_or_code}", include_in_schema=False)
async def get_aemet_pluvio_by_id(id_or_code: str):
    """
    Devuelve los datos de una estación AEMET según su indicativo (ej. '8414A').
    """
    await aemet_pluvios_service.ensure_fresh_data()
    pluvio = aemet_pluvios_service.get_pluvio_by_id(id_or_code)
    if not pluvio:
        raise HTTPException(
            status_code=404,
            detail=f"Estación AEMET con indicativo '{id_or_code}' no encontrada.",
        )
    return pluvio


@router.post("/aemet/lluvias/sync", summary="Forzar sincronización de observaciones AEMET")
async def sync_aemet_lluvias():
    """
    Fuerza la descarga y recálculo de acumulados de AEMET OpenData.
    """
    pluvios = aemet_pluvios_service.sync_pluvios_metadata()
    return {
        "status": "success",
        "message": f"Sincronizados {len(pluvios)} pluviómetros de AEMET correctamente.",
        "total": len(pluvios),
    }


@router.get("/pluvios/todos", summary="Obtener catálogo unificado de pluviómetros (CHJ + AEMET)")
async def get_all_pluvios(
    format: Literal["geojson", "json"] = Query(
        "geojson",
        description="Formato de respuesta: 'geojson' (FeatureCollection) o 'json' (lista plana)",
    ),
    source: Literal["all", "chj", "aemet"] = Query(
        "all",
        description="Filtro de red: 'all' (ambas), 'chj' (SAIH Júcar) o 'aemet' (AEMET)",
    )
):
    """
    Devuelve las estaciones pluviométricas unificadas de CHJ y/o AEMET en idéntico formato.
    """
    chj_list = []
    aemet_list = []

    if source in ("all", "chj"):
        chj_list = await saih_service.get_pluvios()
    if source in ("all", "aemet"):
        aemet_list = await aemet_pluvios_service.get_pluvios()

    combined = chj_list + aemet_list

    if format == "json":
        return combined

    features = [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [p["lon"], p["lat"]]},
            "properties": p,
        }
        for p in combined
    ]
    return {"type": "FeatureCollection", "features": features}
