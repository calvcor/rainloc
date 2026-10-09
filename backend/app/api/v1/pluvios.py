"""
Endpoints de la API para consulta de pluviómetros de AEMET OpenData, AVAMET, Meteocat (XEMA) y unificados.
"""
from typing import Literal, Optional, List, Dict, Any
from fastapi import APIRouter, HTTPException, Query

from app.services.aemet_pluvios_service import aemet_pluvios_service
from app.services.avamet_pluvios_service import avamet_pluvios_service
from app.services.meteocat_pluvios_service import meteocat_pluvios_service
from app.services.hidrosur_pluvios_service import hidrosur_pluvios_service
from app.services.segura_service import segura_service
from app.services.duero_service import duero_service
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
# SAIH SEGURA (CONFEDERACIÓN HIDROGRÁFICA DEL SEGURA)
# ==========================================

@router.get("/segura/lluvias", summary="Obtener pluviómetros del S.A.I.H. Segura (CHS)")
@router.get("/segura/pluvios", include_in_schema=False)
async def get_segura_lluvias(
    format: Literal["geojson", "json"] = Query(
        "geojson",
        description="Formato de respuesta: 'geojson' (FeatureCollection) o 'json' (lista plana)",
    )
):
    """
    Devuelve las ~144 estaciones pluviométricas del SAIH Segura (CHS / MITECO)
    con lluvia acumulada en 1h, 4h, 12h y 24h (mm) obtenidas bajo demanda.
    """
    if format == "geojson":
        return await segura_service.get_pluvios_geojson()
    return await segura_service.get_pluvios()


@router.get("/segura/lluvias/{id_or_code}", summary="Obtener datos de una estación SAIH Segura específica")
@router.get("/segura/pluvios/{id_or_code}", include_in_schema=False)
async def get_segura_pluvio_by_id(id_or_code: str):
    """
    Devuelve los datos de una estación SAIH Segura según su código o variable (ej. '06A16P01').
    """
    await segura_service.ensure_fresh_pluvios_data()
    pluvio = segura_service.get_pluvio_by_id(id_or_code)
    if not pluvio:
        raise HTTPException(
            status_code=404,
            detail=f"Estación SAIH Segura con identificador o código '{id_or_code}' no encontrada.",
        )
    return pluvio


@router.post("/segura/lluvias/sync", summary="Forzar sincronización de pluviómetros SAIH Segura")
async def sync_segura_lluvias():
    """
    Fuerza la descarga y actualización de pluviómetros del SAIH Segura.
    """
    pluvios = segura_service.sync_pluvios()
    return {
        "status": "success",
        "message": f"Sincronizados {len(pluvios)} pluviómetros de SAIH Segura correctamente.",
        "total": len(pluvios),
    }


# ==========================================
# SAIH DUERO (CONFEDERACIÓN HIDROGRÁFICA DEL DUERO)
# ==========================================

@router.get("/duero/lluvias", summary="Obtener pluviómetros del S.A.I.H. Duero (CHD)")
@router.get("/duero/pluvios", include_in_schema=False)
async def get_duero_lluvias(
    format: Literal["geojson", "json"] = Query(
        "geojson",
        description="Formato de respuesta: 'geojson' (FeatureCollection) o 'json' (lista plana)",
    )
):
    """
    Devuelve las ~221 estaciones pluviométricas del SAIH Duero (CHD / MITECO)
    con lluvia en 1h y temperatura obtenidas bajo demanda.
    """
    if format == "geojson":
        return await duero_service.get_pluvios_geojson()
    return await duero_service.get_pluvios()


@router.get("/duero/lluvias/{id_or_code}", summary="Obtener datos de una estación SAIH Duero específica")
@router.get("/duero/pluvios/{id_or_code}", include_in_schema=False)
async def get_duero_pluvio_by_id(id_or_code: str):
    """
    Devuelve los datos de una estación SAIH Duero según su código o ID (ej. 'PL551').
    """
    await duero_service.ensure_fresh_data()
    pluvio = duero_service._pluvios_by_id.get(str(id_or_code).strip()) or duero_service._pluvios_by_id.get(str(id_or_code).strip().upper())
    if not pluvio:
        raise HTTPException(
            status_code=404,
            detail=f"Estación SAIH Duero con identificador o código '{id_or_code}' no encontrada.",
        )
    return pluvio


@router.post("/duero/lluvias/sync", summary="Forzar sincronización de pluviómetros SAIH Duero")
async def sync_duero_lluvias():
    """
    Fuerza la descarga y actualización de pluviómetros del SAIH Duero.
    """
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, duero_service.sync_all)
    return {
        "status": "success",
        "message": f"Sincronizados {len(duero_service._pluvios)} pluviómetros de SAIH Duero correctamente.",
        "total": len(duero_service._pluvios),
    }


# ==========================================
# CATÁLOGO UNIFICADO
# ==========================================

@router.get("/pluvios/todos", summary="Obtener catálogo unificado de pluviómetros (CHJ + AEMET + AVAMET + METEOCAT + HIDROSUR + SEGURA + DUERO)")
async def get_all_pluvios(
    format: Literal["geojson", "json"] = Query(
        "geojson",
        description="Formato de respuesta: 'geojson' (FeatureCollection) o 'json' (lista plana)",
    ),
    source: Literal["all", "chj", "aemet", "avamet", "meteocat", "hidrosur", "segura", "duero"] = Query(
        "all",
        description="Filtro de red: 'all' (todas), 'chj' (SAIH Júcar), 'aemet' (AEMET), 'avamet' (AVAMET), 'meteocat' (Meteocat), 'hidrosur' (SAIH Hidrosur), 'segura' (SAIH Segura) o 'duero' (SAIH Duero)",
    )
):
    """
    Devuelve las estaciones pluviométricas unificadas de CHJ, AEMET, AVAMET, Meteocat, Hidrosur, Segura y/o Duero en idéntico formato.
    """
    if source == "all":
        combined = (
            await saih_service.get_pluvios()
            + await aemet_pluvios_service.get_pluvios()
            + await avamet_pluvios_service.get_pluvios()
            + await meteocat_pluvios_service.get_pluvios()
        )
    elif source == "chj":
        await saih_service.ensure_fresh_pluvios_data()
        combined = saih_service._pluvios
    elif source == "aemet":
        combined = await aemet_pluvios_service.get_pluvios()
    elif source == "avamet":
        combined = await avamet_pluvios_service.get_pluvios()
    elif source == "meteocat":
        combined = await meteocat_pluvios_service.get_pluvios()
    elif source == "hidrosur":
        combined = await hidrosur_pluvios_service.get_pluvios()
    elif source == "segura":
        combined = await segura_service.get_pluvios()
    elif source == "duero":
        combined = await duero_service.get_pluvios()
    else:
        combined = []

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

