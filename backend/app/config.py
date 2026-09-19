"""
Configuración centralizada de la aplicación FastAPI para RainLoc
"""
import os
from pathlib import Path
from typing import List, Dict, Any

BASE_DIR = Path(__file__).resolve().parent.parent.parent
BACKEND_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BACKEND_DIR / "data"
RADAR_CACHE_DIR = DATA_DIR / "radar_cache"
ECMWF_CACHE_DIR = DATA_DIR / "ecmwf_cache"
GFS_CACHE_DIR = DATA_DIR / "gfs_cache"
AROME_CACHE_DIR = DATA_DIR / "arome_cache"

# Asegurar directorios
DATA_DIR.mkdir(parents=True, exist_ok=True)
RADAR_CACHE_DIR.mkdir(parents=True, exist_ok=True)
ECMWF_CACHE_DIR.mkdir(parents=True, exist_ok=True)
GFS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
AROME_CACHE_DIR.mkdir(parents=True, exist_ok=True)

# Catálogo de radares españoles (estaciones individuales en openradar-24h)
SPANISH_RADAR_STATIONS: Dict[str, Dict[str, Any]] = {
    "esbnv": {"name": "Valencia / Cullera (esbnv)", "province": "Valencia", "lat": 39.1864, "lon": -0.2520, "range_km": 240, "alt_m": 236},
    "espma": {"name": "Murcia / Cabezo Plata (espma)", "province": "Murcia", "lat": 37.9940, "lon": -0.9940, "range_km": 240, "alt_m": 460},
    "esalm": {"name": "Almería / Níjar (esalm)", "province": "Almería", "lat": 36.8660, "lon": -2.0830, "range_km": 240, "alt_m": 460},
    "esahr": {"name": "Málaga / Alhaurín (esahr)", "province": "Málaga", "lat": 36.6190, "lon": -4.6640, "range_km": 240, "alt_m": 1100},
    "esclg": {"name": "Sevilla / El Castillo (esclg)", "province": "Sevilla", "lat": 37.6890, "lon": -6.3330, "range_km": 250, "alt_m": 686},
    "esatn": {"name": "Madrid / Attalaya (esatn)", "province": "Madrid", "lat": 40.1780, "lon": -3.7120, "range_km": 240, "alt_m": 680},
    "esgld": {"name": "Zaragoza / La Ginebrosa (esgld)", "province": "Zaragoza", "lat": 41.7280, "lon": -0.9230, "range_km": 240, "alt_m": 350},
    "eslid": {"name": "Barcelona / Puig d'Arques (eslid)", "province": "Girona/Barcelona", "lat": 41.8890, "lon": 2.9970, "range_km": 240, "alt_m": 535},
    "espdg": {"name": "Mallorca / Randa (espdg)", "province": "Illes Balears", "lat": 39.5290, "lon": 2.9230, "range_km": 240, "alt_m": 543},
    "essft": {"name": "Cáceres / Sta. Marina (essft)", "province": "Cáceres", "lat": 39.4210, "lon": -6.3140, "range_km": 240, "alt_m": 508},
    "essse": {"name": "San Sebastián / Igueldo (essse)", "province": "Gipuzkoa", "lat": 43.3080, "lon": -2.0400, "range_km": 240, "alt_m": 370},
    "estjv": {"name": "A Coruña / Monte Xesteiras (estjv)", "province": "A Coruña", "lat": 42.6180, "lon": -8.5360, "range_km": 240, "alt_m": 500},
    "esast": {"name": "Asturias / Picos Europa (esast)", "province": "Asturias", "lat": 43.1880, "lon": -5.9250, "range_km": 240, "alt_m": 1780},
    "estde": {"name": "Tenerife / Cruz de Gala (estde)", "province": "Santa Cruz de Tenerife", "lat": 28.3109, "lon": -16.8238, "range_km": 240, "alt_m": 1340},
    "eslpa": {"name": "Gran Canaria / Pico Gorra (eslpa)", "province": "Las Palmas", "lat": 27.9600, "lon": -15.5860, "range_km": 240, "alt_m": 1940}
}

try:
    from pydantic_settings import BaseSettings

    class Settings(BaseSettings):
        PROJECT_NAME: str = "RainLoc GIS Backend"
        VERSION: str = "1.0.0"
        API_V1_PREFIX: str = "/api/v1"
        HOST: str = "0.0.0.0"
        PORT: int = 8000
        DEBUG: bool = True
        CORS_ORIGINS: List[str] = ["*"]
        
        # Archivos de geometrías
        SUBSISTEMAS_OPTIMIZED_FILE: Path = BASE_DIR / "subsistemas.optimized.geojson"
        SUBSISTEMAS_FILE: Path = BASE_DIR / "subsistemas.geojson"
        CUENCAS_FILE: Path = BASE_DIR / "data" / "cuencas.geojson"
        CCAA_FILE: Path = BASE_DIR / "ccaa.geojson"
        CCAA_DATA_FILE: Path = BASE_DIR / "data" / "ccaa.geojson"
        
        # Almacenamiento del estado del tiempo y caché
        WEATHER_STATE_FILE: Path = DATA_DIR / "weather_state.json"
        RADAR_CACHE_DIR: Path = RADAR_CACHE_DIR
        
        # Feed ATOM de Avisos AEMET
        AEMET_ATOM_URL: str = "https://www.aemet.es/documentos_d/eltiempo/prediccion/avisos/rss/CAP_AFAE_wah_ATOM.xml"
        AEMET_USER_AGENT: str = "RainLoc-WeatherService/1.0 (+https://github.com/carlosalventosa/RainLoc)"
        AEMET_REFRESH_INTERVAL_SECONDS: int = 180  # 3 minutos
        
        # Radar ORD (Open Radar Data / CloudFerro S3)
        ORD_S3_ENDPOINT: str = "https://s3.waw3-1.cloudferro.com"
        ORD_S3_BUCKET: str = "openradar-24h"
        ORD_MQTT_HOST: str = "api.openradardata.eumetnet.eu"
        ORD_MQTT_PORT: int = 8884
        ORD_MQTT_USER: str = "everyone"
        RADAR_POLL_INTERVAL_SECONDS: int = 300  # 5 minutos
        RADAR_CACHE_TTL_HOURS: int = 24

        # ECMWF IFS Open Data
        ECMWF_CACHE_DIR: Path = ECMWF_CACHE_DIR
        ECMWF_POLL_INTERVAL_SECONDS: int = 1800  # 30 minutos (corrida completa)
        ECMWF_UPDATING_INTERVAL_SECONDS: int = 300  # 5 minutos (corrida en progreso)
        ECMWF_MAX_STEPS: int = 240  # Pasos hasta +240h (10 días)

        # NOAA GFS (Global Forecast System) Open Data
        GFS_CACHE_DIR: Path = GFS_CACHE_DIR
        GFS_POLL_INTERVAL_SECONDS: int = 1800  # 30 minutos (corrida completa)
        GFS_UPDATING_INTERVAL_SECONDS: int = 300  # 5 minutos (corrida en progreso)
        GFS_MAX_STEPS: int = 384  # Pasos hasta +384h (16 días)

        # Météo-France / AEMET AROME (High-Resolution Convective Model, ~1.3-2.5km)
        AROME_CACHE_DIR: Path = AROME_CACHE_DIR
        AROME_POLL_INTERVAL_SECONDS: int = 1800  # 30 minutos (corrida completa)
        AROME_UPDATING_INTERVAL_SECONDS: int = 300  # 5 minutos (corrida en progreso)
        AROME_MAX_STEPS: int = 48  # Pasos hasta +48h (2 días)

        class Config:
            env_file = ".env"
            env_file_encoding = "utf-8"
            case_sensitive = True

except ImportError:
    class Settings:
        PROJECT_NAME: str = "RainLoc GIS Backend"
        VERSION: str = "1.0.0"
        API_V1_PREFIX: str = "/api/v1"
        HOST: str = os.getenv("HOST", "0.0.0.0")
        PORT: int = int(os.getenv("PORT", "8000"))
        DEBUG: bool = os.getenv("DEBUG", "True").lower() in ("true", "1")
        CORS_ORIGINS: List[str] = ["*"]
        SUBSISTEMAS_OPTIMIZED_FILE: Path = BASE_DIR / "subsistemas.optimized.geojson"
        SUBSISTEMAS_FILE: Path = BASE_DIR / "subsistemas.geojson"
        CUENCAS_FILE: Path = BASE_DIR / "data" / "cuencas.geojson"
        WEATHER_STATE_FILE: Path = DATA_DIR / "weather_state.json"
        RADAR_CACHE_DIR: Path = RADAR_CACHE_DIR
        ECMWF_CACHE_DIR: Path = ECMWF_CACHE_DIR
        GFS_CACHE_DIR: Path = GFS_CACHE_DIR
        AROME_CACHE_DIR: Path = AROME_CACHE_DIR
        AEMET_ATOM_URL: str = os.getenv("AEMET_ATOM_URL", "https://www.aemet.es/documentos_d/eltiempo/prediccion/avisos/rss/CAP_AFAE_wah_ATOM.xml")
        AEMET_USER_AGENT: str = "RainLoc-WeatherService/1.0 (+https://github.com/carlosalventosa/RainLoc)"
        AEMET_REFRESH_INTERVAL_SECONDS: int = int(os.getenv("AEMET_REFRESH_INTERVAL_SECONDS", "180"))
        ORD_S3_ENDPOINT: str = "https://s3.waw3-1.cloudferro.com"
        ORD_S3_BUCKET: str = "openradar-24h"
        ORD_MQTT_HOST: str = os.getenv("ORD_MQTT_HOST", "api.openradardata.eumetnet.eu")
        ORD_MQTT_PORT: int = int(os.getenv("ORD_MQTT_PORT", "8884"))
        ORD_MQTT_USER: str = os.getenv("ORD_MQTT_USER", "everyone")
        RADAR_POLL_INTERVAL_SECONDS: int = int(os.getenv("RADAR_POLL_INTERVAL_SECONDS", "300"))
        RADAR_CACHE_TTL_HOURS: int = 24
        ECMWF_POLL_INTERVAL_SECONDS: int = 1800
        ECMWF_UPDATING_INTERVAL_SECONDS: int = 300
        ECMWF_MAX_STEPS: int = 240
        GFS_POLL_INTERVAL_SECONDS: int = 1800
        GFS_UPDATING_INTERVAL_SECONDS: int = 300
        GFS_MAX_STEPS: int = 384
        AROME_POLL_INTERVAL_SECONDS: int = 1800
        AROME_UPDATING_INTERVAL_SECONDS: int = 300
        AROME_MAX_STEPS: int = 48

settings = Settings()
