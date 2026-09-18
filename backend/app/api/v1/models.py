"""
Weather prediction model API endpoints (AROME, ICON-D2, ECMWF stubs).
Ready to connect to real GRIB2 / NetCDF / Open-Meteo sources when specified.
"""
from fastapi import APIRouter
from typing import Dict, Any

router = APIRouter(prefix="/models", tags=["NWP Models"])


@router.get("/arome", summary="AROME-HD High Resolution Model (AEMET/Météo-France)")
async def get_arome_model() -> Dict[str, Any]:
    return {
        "model": "AROME",
        "description": "Modelo de resolución muy alta (1.3 - 2.5 km)",
        "status": "ready_for_ingestion",
        "parameters": [
            {"id": "accumulated_rain", "name": "Precipitación acumulada (mm)", "unit": "mm"},
            {"id": "rain_rate", "name": "Intensidad instantánea (mm/h)", "unit": "mm/h"},
            {"id": "cape", "name": "CAPE (Inestabilidad convectiva)", "unit": "J/kg"}
        ],
        "timesteps": []
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


@router.get("/ecmwf", summary="ECMWF IFS Model (Open Data / HRES)")
async def get_ecmwf_model() -> Dict[str, Any]:
    return {
        "model": "ECMWF IFS",
        "description": "Modelo global de referencia a medio plazo (0.1° / ~9 km)",
        "status": "ready_for_ingestion",
        "parameters": [
            {"id": "total_precipitation", "name": "Precipitación acumulada total", "unit": "mm"},
            {"id": "efi_rain", "name": "Índice de Precipitación Extrema (EFI)", "unit": "index"}
        ],
        "timesteps": []
    }
