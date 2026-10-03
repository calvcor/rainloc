"""
Router de API v1 para Búsqueda Geográfica de Lugares y Toponimia
"""

import logging
from typing import Optional, List
from fastapi import APIRouter, Query, HTTPException
from pydantic import BaseModel, Field

from app.services.places_service import places_service

logger = logging.getLogger("rainloc-backend.api.search")

router = APIRouter(prefix="/search", tags=["Búsqueda Geográfica"])

class PlaceSearchResult(BaseModel):
    id: str = Field(..., description="Identificador único del lugar")
    name: str = Field(..., description="Nombre principal")
    alt_name: Optional[str] = Field(None, description="Nombres alternativos o información de ubicación")
    category: str = Field(..., description="Categoría (municipality, region, cuenca, river, reservoir, station, mountain, place)")
    category_label: str = Field(..., description="Etiqueta legible en español")
    icon: str = Field(..., description="Icono visual representativo")
    province: Optional[str] = Field(None, description="Provincia")
    community: Optional[str] = Field(None, description="Comunidad Autónoma")
    lat: float = Field(..., description="Latitud WGS84")
    lon: float = Field(..., description="Longitud WGS84")
    zoom: int = Field(..., description="Nivel de zoom recomendado para la vista de mapa")
    score: float = Field(..., description="Puntuación de relevancia")

class SearchResponse(BaseModel):
    query: str
    total: int
    results: List[PlaceSearchResult]

@router.get("", response_model=SearchResponse)
async def search_places(
    q: str = Query(..., min_length=2, max_length=100, description="Texto de búsqueda (municipio, río, embalse, comarca, estación...)"),
    category: Optional[str] = Query(None, description="Filtrar por categoría (ej. municipality, river, reservoir, station, region, cuenca)"),
    limit: int = Query(10, ge=1, le=30, description="Número máximo de resultados a devolver")
):
    """
    Busca localidades, ríos, embalses, cuencas hidrográficas y estaciones meteorológicas
    en la base de datos de toponimia oficial de España y Comunitat Valenciana.
    """
    try:
        cat_str = category if isinstance(category, str) else None
        limit_num = limit if isinstance(limit, int) else 10
        results = places_service.search(query=q, category=cat_str, limit=limit_num)
        return SearchResponse(
            query=q,
            total=len(results),
            results=results
        )
    except Exception as e:
        logger.error(f"Error en endpoint de búsqueda: {e}")
        raise HTTPException(status_code=500, detail="Error interno procesando la búsqueda geográfica.")

@router.post("/sync", tags=["Búsqueda Geográfica"])
async def sync_places_database(force: bool = Query(False, description="Forzar re-escaneo de todos los datasets locales")):
    """
    Sincroniza y actualiza dinámicamente en SQLite todas las estaciones meteorológicas,
    aforos de caudal, embalses y cuencas a partir de los archivos GeoJSON locales.
    """
    try:
        updated_count = places_service.sync_local_entities(force=force)
        return {
            "status": "success",
            "message": f"Sincronización completada: {updated_count} entidades locales actualizadas.",
            "updated_entities": updated_count
        }
    except Exception as e:
        logger.error(f"Error sincronizando base de datos de lugares: {e}")
        raise HTTPException(status_code=500, detail=str(e))
