"""
Endpoints de la API para consulta de pluviómetros de AEMET OpenData, AVAMET, Meteocat (XEMA) y unificados.
"""
from typing import Literal, Optional, List, Dict, Any
from fastapi import APIRouter, HTTPException, Query

from app.services.aemet_pluvios_service import aemet_pluvios_service
from app.services.avamet_pluvios_service import avamet_pluvios_service
from app.services.meteocat_pluvios_service import meteocat_pluvios_service
from app.services.hidrosur_pluvios_service import hidrosur_pluvios_service
from app.services.saih_service import saih_service

router = APIRouter(tags=["Pluviómetros"])


# ==========================================
# AEMET OPENDATA
# ==========================================

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


# ==========================================
# AVAMET (METEOXARXA ONLINE)
# ==========================================

@router.get("/avamet/lluvias", summary="Obtener pluviómetros de la red de AVAMET (MeteoXarxa)")
@router.get("/avamet/pluvios", include_in_schema=False)
async def get_avamet_lluvias(
    format: Literal["geojson", "json"] = Query(
        "geojson",
        description="Formato de respuesta: 'geojson' (FeatureCollection) o 'json' (lista plana)",
    )
):
    """
    Devuelve las ~860 estaciones pluviométricas de AVAMET con lluvia acumulada
    en 1h, 4h, 12h y 24h (mm) obtenidas bajo demanda.
    """
    if format == "geojson":
        return await avamet_pluvios_service.get_pluvios_geojson()
    return await avamet_pluvios_service.get_pluvios()


@router.get("/avamet/lluvias/{id_or_code}", summary="Obtener datos de una estación AVAMET específica")
@router.get("/avamet/pluvios/{id_or_code}", include_in_schema=False)
async def get_avamet_pluvio_by_id(id_or_code: str):
    """
    Devuelve los datos de una estación AVAMET según su código (ej. 'c01m038e01').
    """
    await avamet_pluvios_service.ensure_fresh_data()
    pluvio = avamet_pluvios_service.get_pluvio_by_id(id_or_code)
    if not pluvio:
        raise HTTPException(
            status_code=404,
            detail=f"Estación AVAMET con identificador o código '{id_or_code}' no encontrada.",
        )
    return pluvio


@router.post("/avamet/lluvias/sync", summary="Forzar sincronización de pluviómetros AVAMET")
async def sync_avamet_lluvias():
    """
    Fuerza la descarga y actualización de pluviómetros de AVAMET.
    """
    pluvios = avamet_pluvios_service.sync_pluvios_metadata()
    return {
        "status": "success",
        "message": f"Sincronizados {len(pluvios)} pluviómetros de AVAMET correctamente.",
        "total": len(pluvios),
    }


# ==========================================
# METEOCAT (XEMA - DADES OBERTES GENCAT)
# ==========================================

@router.get("/meteocat/lluvias", summary="Obtener pluviómetros de la red de Meteocat (XEMA)")
@router.get("/meteocat/pluvios", include_in_schema=False)
async def get_meteocat_lluvias(
    format: Literal["geojson", "json"] = Query(
        "geojson",
        description="Formato de respuesta: 'geojson' (FeatureCollection) o 'json' (lista plana)",
    )
):
    """
    Devuelve las ~245 estaciones meteorológicas automáticas (XEMA) de Meteocat con lluvia acumulada
    en 1h, 4h, 12h y 24h (mm) obtenidas bajo demanda desde el portal oficial de Dades Obertes.
    """
    if format == "geojson":
        return await meteocat_pluvios_service.get_pluvios_geojson()
    return await meteocat_pluvios_service.get_pluvios()


@router.get("/meteocat/lluvias/{id_or_code}", summary="Obtener datos de una estación Meteocat específica")
@router.get("/meteocat/pluvios/{id_or_code}", include_in_schema=False)
async def get_meteocat_pluvio_by_id(id_or_code: str):
    """
    Devuelve los datos de una estación Meteocat según su código (ej. 'VC').
    """
    await meteocat_pluvios_service.ensure_fresh_data()
    pluvio = meteocat_pluvios_service.get_pluvio_by_id(id_or_code)
    if not pluvio:
        raise HTTPException(
            status_code=404,
            detail=f"Estación Meteocat con código '{id_or_code}' no encontrada.",
        )
    return pluvio


@router.post("/meteocat/lluvias/sync", summary="Forzar sincronización de pluviómetros Meteocat")
async def sync_meteocat_lluvias():
    """
    Fuerza la descarga y actualización de pluviómetros de Meteocat.
    """
    pluvios = meteocat_pluvios_service.sync_pluvios_metadata()
    return {
        "status": "success",
        "message": f"Sincronizados {len(pluvios)} pluviómetros de Meteocat correctamente.",
        "total": len(pluvios),
    }


# ==========================================
# SAIH HIDROSUR (JUNTA DE ANDALUCÍA)
# ==========================================

@router.get("/hidrosur/lluvias", summary="Obtener pluviómetros del S.A.I.H. Hidrosur (Junta de Andalucía)")
@router.get("/hidrosur/pluvios", include_in_schema=False)
async def get_hidrosur_lluvias(
    format: Literal["geojson", "json"] = Query(
        "geojson",
        description="Formato de respuesta: 'geojson' (FeatureCollection) o 'json' (lista plana)",
    )
):
    """
    Devuelve las ~147 estaciones pluviométricas del SAIH Hidrosur (Cuencas Mediterráneas Andaluzas)
    con lluvia acumulada en 1h, 4h, 12h y 24h (mm) obtenidas bajo demanda.
    """
    if format == "geojson":
        return await hidrosur_pluvios_service.get_pluvios_geojson()
    return await hidrosur_pluvios_service.get_pluvios()


@router.get("/hidrosur/lluvias/{id_or_code}", summary="Obtener datos de una estación SAIH Hidrosur específica")
@router.get("/hidrosur/pluvios/{id_or_code}", include_in_schema=False)
async def get_hidrosur_pluvio_by_id(id_or_code: str):
    """
    Devuelve los datos de una estación SAIH Hidrosur según su código o ID (ej. '2' o 'hidrosur_2').
    """
    await hidrosur_pluvios_service.ensure_fresh_data()
    pluvio = hidrosur_pluvios_service.get_pluvio_by_id(id_or_code)
    if not pluvio:
        raise HTTPException(
            status_code=404,
            detail=f"Estación SAIH Hidrosur con identificador o código '{id_or_code}' no encontrada.",
        )
    return pluvio


@router.post("/hidrosur/lluvias/sync", summary="Forzar sincronización de pluviómetros SAIH Hidrosur")
async def sync_hidrosur_lluvias():
    """
    Fuerza la descarga y actualización de pluviómetros del SAIH Hidrosur.
    """
    pluvios = hidrosur_pluvios_service.sync_pluvios_metadata()
    return {
        "status": "success",
        "message": f"Sincronizados {len(pluvios)} pluviómetros de SAIH Hidrosur correctamente.",
        "total": len(pluvios),
    }


# ==========================================
# CATÁLOGO UNIFICADO
# ==========================================

@router.get("/pluvios/todos", summary="Obtener catálogo unificado de pluviómetros (CHJ + AEMET + AVAMET + METEOCAT + HIDROSUR)")
async def get_all_pluvios(
    format: Literal["geojson", "json"] = Query(
        "geojson",
        description="Formato de respuesta: 'geojson' (FeatureCollection) o 'json' (lista plana)",
    ),
    source: Literal["all", "chj", "aemet", "avamet", "meteocat", "hidrosur"] = Query(
        "all",
        description="Filtro de red: 'all' (todas), 'chj' (SAIH Júcar), 'aemet' (AEMET), 'avamet' (AVAMET), 'meteocat' (Meteocat) o 'hidrosur' (SAIH Hidrosur)",
    )
):
    """
    Devuelve las estaciones pluviométricas unificadas de CHJ, AEMET, AVAMET, Meteocat y/o Hidrosur en idéntico formato.
    """
    chj_list = []
    aemet_list = []
    avamet_list = []
    meteocat_list = []
    hidrosur_list = []

    if source in ("all", "chj"):
        chj_list = await saih_service.get_pluvios()
    if source in ("all", "aemet"):
        aemet_list = await aemet_pluvios_service.get_pluvios()
    if source in ("all", "avamet"):
        avamet_list = await avamet_pluvios_service.get_pluvios()
    if source in ("all", "meteocat"):
        meteocat_list = await meteocat_pluvios_service.get_pluvios()
    if source in ("all", "hidrosur"):
        hidrosur_list = await hidrosur_pluvios_service.get_pluvios()

    combined = chj_list + aemet_list + avamet_list + meteocat_list + hidrosur_list

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
