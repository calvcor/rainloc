"""
Configuración centralizada de la aplicación FastAPI para RainLoc
"""
import os
from pathlib import Path
from typing import List, Dict, Any, Optional
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent.parent
BACKEND_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BACKEND_DIR / "data"
RADAR_CACHE_DIR = DATA_DIR / "radar_cache"
ECMWF_CACHE_DIR = DATA_DIR / "ecmwf_cache"
GFS_CACHE_DIR = DATA_DIR / "gfs_cache"
AROME_CACHE_DIR = DATA_DIR / "arome_cache"

# Cargar variables de entorno desde .env (en backend/ o en la raíz del proyecto)
if (BACKEND_DIR / ".env").exists():
    load_dotenv(BACKEND_DIR / ".env", override=False)
if (BASE_DIR / ".env").exists():
    load_dotenv(BASE_DIR / ".env", override=False)

# Asegurar directorios
DATA_DIR.mkdir(parents=True, exist_ok=True)
RADAR_CACHE_DIR.mkdir(parents=True, exist_ok=True)
ECMWF_CACHE_DIR.mkdir(parents=True, exist_ok=True)
GFS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
AROME_CACHE_DIR.mkdir(parents=True, exist_ok=True)

# Catálogo completo de la red de radares meteorológicos de España (AEMET / ORD)
SPANISH_RADAR_STATIONS: Dict[str, Dict[str, Any]] = {
    # Radares actualmente sincronizando en EUMETNET ORD
    "esahr": {"name": "Málaga / Alhaurín", "province": "Málaga", "lat": 36.6134, "lon": -4.6593, "range_km": 240, "alt_m": 1159, "aemet_code": "ma"},
    "esclg": {"name": "Sevilla / El Castillo", "province": "Sevilla", "lat": 37.6887, "lon": -6.3331, "range_km": 250, "alt_m": 531, "aemet_code": "se"},
    "esnjr": {"name": "Almería / Níjar", "province": "Almería", "lat": 36.8324, "lon": -2.0821, "range_km": 240, "alt_m": 499, "aemet_code": "am"},
    "estjv": {"name": "Madrid / Torrejón de Velasco", "province": "Madrid", "lat": 40.1759, "lon": -3.7137, "range_km": 240, "alt_m": 717, "aemet_code": "to"},
    "essft": {"name": "Cáceres / Sierra Fuentes", "province": "Cáceres", "lat": 39.4288, "lon": -6.2853, "range_km": 240, "alt_m": 667, "aemet_code": "cc"},
    "esgrm": {"name": "Salamanca / Guadramiro", "province": "Salamanca", "lat": 41.0116, "lon": -6.4777, "range_km": 240, "alt_m": 793, "aemet_code": "sa"},
    "eslid": {"name": "Valladolid", "province": "Valladolid", "lat": 41.9956, "lon": -4.6028, "range_km": 240, "alt_m": 887, "aemet_code": "vd"},
    "essse": {"name": "País Vasco / Monte Oiz", "province": "Bizkaia/Gipuzkoa", "lat": 43.4033, "lon": -2.8419, "range_km": 240, "alt_m": 625, "aemet_code": "ss"},
    "espdg": {"name": "Zaragoza / Perdiguera", "province": "Zaragoza", "lat": 41.7340, "lon": -0.5459, "range_km": 240, "alt_m": 835, "aemet_code": "za"},
    "esgld": {"name": "Barcelona / Gelida", "province": "Barcelona", "lat": 41.4082, "lon": 1.8849, "range_km": 240, "alt_m": 662, "aemet_code": "ba"},
    "esbnv": {"name": "Tenerife / Buenavista", "province": "Santa Cruz de Tenerife", "lat": 28.3109, "lon": -16.8238, "range_km": 240, "alt_m": 1367, "aemet_code": "ca"},
    "esatn": {"name": "Gran Canaria / Artenara", "province": "Las Palmas", "lat": 28.0188, "lon": -15.6145, "range_km": 240, "alt_m": 1777, "aemet_code": "ca"},
    # Radares en proceso de modernización o volcado a ORD (disponibles en AEMET OpenData)
    "escul": {"name": "Valencia / Cullera", "province": "Valencia", "lat": 39.1864, "lon": -0.2520, "range_km": 240, "alt_m": 236, "aemet_code": "va"},
    "espma": {"name": "Murcia / Cabezo de la Plata", "province": "Murcia", "lat": 37.9940, "lon": -0.9940, "range_km": 240, "alt_m": 460, "aemet_code": "pm"},
    "espmb": {"name": "Mallorca / Puig de Randa", "province": "Illes Balears", "lat": 39.5290, "lon": 2.9230, "range_km": 240, "alt_m": 543, "aemet_code": "pm"},
    "esast": {"name": "Asturias / El Vidural", "province": "Asturias", "lat": 43.3420, "lon": -6.5210, "range_km": 240, "alt_m": 850, "aemet_code": "as"},
    "escor": {"name": "A Coruña / Lousame", "province": "A Coruña", "lat": 42.8360, "lon": -8.8470, "range_km": 240, "alt_m": 680, "aemet_code": "co"}
}

# Parámetros de radar y bounding box geográfico nacional
SPAIN_BBOX: Dict[str, float] = {"lat_min": 35.0, "lat_max": 44.5, "lon_min": -10.0, "lon_max": 5.0}
RADAR_SHORT_RANGE_MAX_KM = 145.0  # Radio máximo efectivo para priorizar corto alcance (DBZH+VRADH)
RADAR_SHORT_RANGE_BLEND_KM = 135.0 # Radio de inicio de transición suave hacia largo alcance

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
        
        # AEMET OpenData API Key (para fallback de radares regionales como Cullera, Murcia, etc.)
        AEMET_API_KEY: Optional[str] = None
        AEMET_OPENDATA_BASE_URL: str = "https://opendata.aemet.es/opendata/api"

        # Radar ORD (Open Radar Data / CloudFerro S3)
        ORD_S3_ENDPOINT: str = "https://s3.waw3-1.cloudferro.com"
        ORD_S3_BUCKET: str = "openradar-24h"
        ORD_MQTT_HOST: str = "api.openradardata.eumetnet.eu"
        ORD_MQTT_PORT: int = 8884
        ORD_MQTT_USER: str = "everyone"
        RADAR_POLL_INTERVAL_SECONDS: int = 60  # 1 minuto
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
        AEMET_API_KEY: Optional[str] = os.getenv("AEMET_API_KEY", None)
        AEMET_OPENDATA_BASE_URL: str = os.getenv("AEMET_OPENDATA_BASE_URL", "https://opendata.aemet.es/opendata/api")
        ORD_S3_ENDPOINT: str = "https://s3.waw3-1.cloudferro.com"
        ORD_S3_BUCKET: str = "openradar-24h"
        ORD_MQTT_HOST: str = os.getenv("ORD_MQTT_HOST", "api.openradardata.eumetnet.eu")
        ORD_MQTT_PORT: int = int(os.getenv("ORD_MQTT_PORT", "8884"))
        ORD_MQTT_USER: str = os.getenv("ORD_MQTT_USER", "everyone")
        RADAR_POLL_INTERVAL_SECONDS: int = int(os.getenv("RADAR_POLL_INTERVAL_SECONDS", "60"))
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
