"""
Endpoints de Cuencas y Subsistemas Hidrográficos de la CHJ
"""
import json
from pathlib import Path
from typing import Optional, Dict, Any, List
from fastapi import APIRouter, HTTPException, Response
from app.config import settings

router = APIRouter(prefix="/cuencas", tags=["Cuencas y Subsistemas"])

# Cache en memoria del GeoJSON procesado y optimizado
_cuencas_cache: Optional[Dict[str, Any]] = None

def get_cuencas_data() -> Dict[str, Any]:
    """Carga y mantiene en memoria el GeoJSON de cuencas/subsistemas priorizando la versión optimizada."""
    global _cuencas_cache
    if _cuencas_cache is not None:
        return _cuencas_cache

    # 1. Priorizar el archivo optimizado
    target_file = None
    for candidate in [settings.SUBSISTEMAS_OPTIMIZED_FILE, settings.SUBSISTEMAS_FILE, settings.CUENCAS_FILE]:
        if candidate and candidate.exists():
            target_file = candidate
            break

    if not target_file:
        raise HTTPException(
            status_code=404,
            detail="Archivo de cuencas no encontrado en el servidor."
        )

    try:
        with open(target_file, "r", encoding="utf-8") as f:
            data = json.load(f)
            _cuencas_cache = data
            return _cuencas_cache
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al leer datos de cuencas: {str(e)}")

# Cache en memoria de CCAA GeoJSON
_ccaa_cache: Optional[Dict[str, Any]] = None

def get_ccaa_data() -> Dict[str, Any]:
    """Carga y mantiene en memoria el GeoJSON de límites de Comunidades Autónomas de España."""
    global _ccaa_cache
    if _ccaa_cache is not None:
        return _ccaa_cache

    candidates = [
        getattr(settings, "CCAA_DATA_FILE", None),
        getattr(settings, "CCAA_FILE", None),
        getattr(settings, "BASE_DIR", Path(".")) / "ccaa.geojson",
        getattr(settings, "BASE_DIR", Path(".")) / "data" / "ccaa.geojson",
        Path("/app/ccaa.geojson"),
        Path.cwd() / "ccaa.geojson",
        Path.cwd() / "data" / "ccaa.geojson",
        Path.cwd().parent / "ccaa.geojson",
        Path(__file__).resolve().parent.parent.parent.parent / "ccaa.geojson",
        Path(__file__).resolve().parent.parent.parent.parent / "data" / "ccaa.geojson"
    ]
    for candidate in candidates:
        if candidate and candidate.exists():
            try:
                with open(candidate, "r", encoding="utf-8") as f:
                    _ccaa_cache = json.load(f)
                    return _ccaa_cache
            except Exception:
                pass

    raise HTTPException(status_code=404, detail="Archivo GeoJSON de Comunidades Autónomas no encontrado.")

@router.get("/ccaa", summary="Obtener GeoJSON de límites de Comunidades Autónomas de España")
async def get_ccaa_boundaries():
    """Devuelve la FeatureCollection de las Comunidades Autónomas de España sin relleno."""
    data = get_ccaa_data()
    return Response(
        content=json.dumps(data, ensure_ascii=False, separators=(",", ":")),
        media_type="application/geo+json",
        headers={
            "Cache-Control": "public, max-age=86400",
            "X-Feature-Count": str(len(data.get("features", [])))
        }
    )

@router.get("", summary="Obtener GeoJSON de cuencas y subsistemas CHJ")
async def get_all_cuencas():
    """
    Devuelve la FeatureCollection con geometrías optimizadas y propiedades de los 
    subsistemas de explotación de la CHJ, comprimible vía gzip.
    """
    data = get_cuencas_data()
    return Response(
        content=json.dumps(data, ensure_ascii=False, separators=(",", ":")),
        media_type="application/geo+json",
        headers={
            "Cache-Control": "public, max-age=86400",
            "X-Feature-Count": str(len(data.get("features", [])))
        }
    )

@router.get("/sistemas", summary="Resumen estadístico por Sistema de Explotación")
async def get_sistemas_summary():
    """Devuelve el listado de sistemas de explotación con su número de subsistemas y superficie total."""
    data = get_cuencas_data()
    sistemas: Dict[str, Dict[str, Any]] = {}

    for f in data.get("features", []):
        props = f.get("properties", {})
        sys_name = props.get("NomSistExp", "Sin clasificar")
        area = float(props.get("Superf km2") or props.get("Area km2") or 0.0)

        if sys_name not in sistemas:
            sistemas[sys_name] = {
                "sistema": sys_name,
                "subsistemas_count": 0,
                "superficie_total_km2": 0.0,
                "subsistemas": []
            }

        sistemas[sys_name]["subsistemas_count"] += 1
        sistemas[sys_name]["superficie_total_km2"] = round(sistemas[sys_name]["superficie_total_km2"] + area, 2)
        sistemas[sys_name]["subsistemas"].append({
            "id": f.get("id"),
            "nombre": props.get("Subsistema"),
            "superficie_km2": area
        })

    return {"total_sistemas": len(sistemas), "sistemas": list(sistemas.values())}

@router.get("/{feature_id}", summary="Obtener una cuenca específica por su identificador")
async def get_cuenca_by_id(feature_id: str):
    """Devuelve la geometría y atributos de un subsistema individual."""
    data = get_cuencas_data()
    for f in data.get("features", []):
        if str(f.get("id")) == str(feature_id):
            return f

    raise HTTPException(status_code=404, detail=f"Cuenca con ID {feature_id} no encontrada")
