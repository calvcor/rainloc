"""
Endpoints API para monitorización de caudales en tiempo real y series temporales del SAIH Júcar (CHJ).
"""
from typing import Optional, Literal
from fastapi import APIRouter, HTTPException, Query
from app.services.saih_service import saih_service

router = APIRouter(prefix="/saih", tags=["SAIH Júcar - Caudales"])


@router.get("/caudales", summary="Obtener catálogo y mediciones de estaciones de caudal")
@router.get("/aforos", include_in_schema=False)
async def get_caudales(
    format: Literal["geojson", "json"] = Query(
        "geojson",
        description="Formato de respuesta: 'geojson' (FeatureCollection) o 'json' (lista plana)",
    )
):
    """
    Devuelve todas las estaciones de caudal registradas en el SAIH con sus coordenadas WGS84,
    último caudal circulante (m³/s), umbrales de alerta y subcuenca.
    """
    if format == "geojson":
        return await saih_service.get_stations_geojson()
    return await saih_service.get_stations()


@router.get("/caudales/{id_variable}", summary="Obtener metadatos de una estación de caudal")
@router.get("/aforos/{id_variable}", include_in_schema=False)
async def get_caudal_station_by_id(id_variable: str):
    """
    Devuelve los detalles y umbrales de una estación concreta según su ID de variable.
    """
    station = saih_service.get_station_by_id(id_variable)
    if not station:
        raise HTTPException(
            status_code=404,
            detail=f"Estación de caudal con id_variable '{id_variable}' no encontrada en el catálogo.",
        )
    return station


@router.get(
    "/caudales/{id_variable}/history",
    summary="Consultar serie temporal de caudal de una estación",
)
@router.get("/aforos/{id_variable}/history", include_in_schema=False)
async def get_caudal_history(
    id_variable: str,
    hours: int = Query(24, ge=1, le=720, description="Número de horas hacia atrás a consultar"),
    start_date: Optional[str] = Query(
        None,
        description="Fecha inicio personalizada (formato: YYYY-MM-DD HH:mm:ss)",
        examples=["2026-09-17 00:00:00"],
    ),
    end_date: Optional[str] = Query(
        None,
        description="Fecha fin personalizada (formato: YYYY-MM-DD HH:mm:ss)",
        examples=["2026-09-18 19:00:00"],
    ),
):
    """
    Obtiene la serie temporal (pasos de 5 minutos) de caudal registrada por la estación
    consultando dinámicamente la API del SAIH Júcar.
    """
    return await saih_service.get_history(
        id_variable=id_variable,
        hours=hours,
        start_date=start_date,
        end_date=end_date,
    )


@router.post("/sync", summary="Sincronizar catálogo estático de caudales desde saih.chj.es")
async def sync_saih_caudales():
    """
    Fuerza la re-sincronización y actualización del fichero de estaciones de caudal
    descargando la última versión desde el portal oficial del SAIH.
    """
    stations = saih_service.sync_static_metadata()
    return {
        "status": "success",
        "message": f"Sincronizadas {len(stations)} estaciones de caudal correctamente.",
        "total": len(stations),
    }


# ==========================================
# ENDPOINTS DE EMBALSES
# ==========================================

@router.get("/embalses", summary="Obtener catálogo y estado de embalses CHJ")
async def get_embalses(
    format: Literal["geojson", "json"] = Query(
        "geojson",
        description="Formato de respuesta: 'geojson' (FeatureCollection) o 'json' (lista plana)",
    )
):
    """
    Devuelve los 25 embalses del SAIH Júcar con volumen actual (hm³), capacidad total NMN,
    porcentaje de llenado, cota (m.s.n.m.), caudales de entrada/salida y coordenadas WGS84.
    """
    if format == "geojson":
        return await saih_service.get_embalses_geojson()
    return await saih_service.get_embalses()


@router.get("/embalses/{id_or_code}", summary="Obtener metadatos detallados de un embalse")
async def get_embalse_by_id_or_code(id_or_code: str):
    """
    Devuelve la ficha y parámetros de un embalse según su código (ej. '7E04') o ID de estación.
    """
    emb = saih_service.get_embalse_by_id(id_or_code)
    if not emb:
        raise HTTPException(
            status_code=404,
            detail=f"Embalse con identificador o código '{id_or_code}' no encontrado en el catálogo.",
        )
    return emb


@router.get(
    "/embalses/{id_or_code}/history",
    summary="Consultar serie temporal de un embalse (volumen, cota o caudal)",
)
async def get_embalse_history(
    id_or_code: str,
    variable_type: Literal["volumen", "cota", "caudal_salida_rio", "caudal_recibido"] = Query(
        "volumen",
        description="Tipo de variable a consultar: 'volumen' (hm³), 'cota' (m.s.n.m.), 'caudal_salida_rio' (m³/s) o 'caudal_recibido' (m³/s)"
    ),
    hours: int = Query(24, ge=1, le=720, description="Número de horas hacia atrás a consultar"),
    start_date: Optional[str] = Query(
        None,
        description="Fecha inicio personalizada (formato: YYYY-MM-DD HH:mm:ss)",
        examples=["2026-09-17 00:00:00"],
    ),
    end_date: Optional[str] = Query(
        None,
        description="Fecha fin personalizada (formato: YYYY-MM-DD HH:mm:ss)",
        examples=["2026-09-18 19:00:00"],
    ),
):
    """
    Obtiene la serie temporal histórica (pasos de 5 minutos) para el embalse consultando
    la API del SAIH Júcar.
    """
    emb = saih_service.get_embalse_by_id(id_or_code)
    if not emb:
        # Intentar consultar directamente como id_variable si es numérico
        target_var_id = id_or_code
    else:
        if variable_type == "cota":
            target_var_id = emb.get("id_cota") or emb.get("id_volumen")
        elif variable_type == "caudal_salida_rio":
            target_var_id = emb.get("id_caudal_rio") or emb.get("id_volumen")
        elif variable_type == "caudal_recibido":
            target_var_id = emb.get("id_caudal_in") or emb.get("id_volumen")
        else:
            target_var_id = emb.get("id_volumen")

    if not target_var_id:
        raise HTTPException(
            status_code=404,
            detail=f"No se encontró ID de variable para el tipo '{variable_type}' en el embalse '{id_or_code}'.",
        )

    res = await saih_service.get_history(
        id_variable=str(target_var_id),
        hours=hours,
        start_date=start_date,
        end_date=end_date,
    )
    if emb:
        res["embalse"] = emb
        res["tipo_variable"] = variable_type
    return res


@router.post("/embalses/sync", summary="Sincronizar catálogo de embalses desde saih.chj.es")
async def sync_saih_embalses():
    """
    Fuerza la re-sincronización y actualización del fichero de embalses
    descargando la última versión desde el portal oficial del SAIH.
    """
    embalses = saih_service.sync_embalses_metadata()
    return {
        "status": "success",
        "message": f"Sincronizados {len(embalses)} embalses correctamente.",
        "total": len(embalses),
    }


# ==========================================
# ENDPOINTS DE LLUVIAS (PLUVIÓMETROS)
# ==========================================

@router.get("/lluvias", summary="Obtener catálogo y mediciones de pluviómetros CHJ")
@router.get("/pluvios", include_in_schema=False)
async def get_lluvias(
    format: Literal["geojson", "json"] = Query(
        "geojson",
        description="Formato de respuesta: 'geojson' (FeatureCollection) o 'json' (lista plana)",
    )
):
    """
    Devuelve las 182 estaciones pluviométricas del SAIH Júcar con lluvia acumulada
    en 1h, 4h, 12h y 24h (mm) y coordenadas WGS84.
    """
    if format == "geojson":
        return await saih_service.get_pluvios_geojson()
    return await saih_service.get_pluvios()


@router.get("/lluvias/{id_or_code}", summary="Obtener datos de un pluviómetro específico")
@router.get("/pluvios/{id_or_code}", include_in_schema=False)
async def get_pluvio_by_id_or_code(id_or_code: str):
    """
    Devuelve los datos de un pluviómetro según su código (ej. '8P06') o ID de estación remota.
    """
    pluvio = saih_service.get_pluvio_by_id(id_or_code)
    if not pluvio:
        raise HTTPException(
            status_code=404,
            detail=f"Pluviómetro con identificador o código '{id_or_code}' no encontrado en el catálogo.",
        )
    return pluvio


@router.post("/lluvias/sync", summary="Sincronizar catálogo de pluviómetros desde saih.chj.es/mapa-lluvias")
async def sync_saih_lluvias():
    """
    Fuerza la re-sincronización y actualización del fichero de pluviómetros
    descargando los últimos registros desde saih.chj.es.
    """
    pluvios = saih_service.sync_pluvios_metadata()
    return {
        "status": "success",
        "message": f"Sincronizados {len(pluvios)} pluviómetros correctamente.",
        "total": len(pluvios),
    }
