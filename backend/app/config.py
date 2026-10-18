"""
Configuración centralizada de la aplicación FastAPI para RainLoc
"""
import os
from pathlib import Path
from typing import List, Dict, Any, Optional
try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv(*args, **kwargs):
        pass


BASE_DIR = Path(__file__).resolve().parent.parent.parent
BACKEND_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BACKEND_DIR / "data"
RADAR_CACHE_DIR = DATA_DIR / "radar_cache"
ECMWF_CACHE_DIR = DATA_DIR / "ecmwf_cache"
GFS_CACHE_DIR = DATA_DIR / "gfs_cache"
AROME_CACHE_DIR = DATA_DIR / "arome_cache"
HARMONIE_CACHE_DIR = DATA_DIR / "harmonie_cache"
ICON_CACHE_DIR = DATA_DIR / "icon_cache"
GEM_CACHE_DIR = DATA_DIR / "gem_cache"
UKMO_CACHE_DIR = DATA_DIR / "ukmo_cache"

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
HARMONIE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
ICON_CACHE_DIR.mkdir(parents=True, exist_ok=True)
GEM_CACHE_DIR.mkdir(parents=True, exist_ok=True)
UKMO_CACHE_DIR.mkdir(parents=True, exist_ok=True)

# Catálogo completo de la red de radares meteorológicos de España (AEMET / ORD)
SPANISH_RADAR_STATIONS: Dict[str, Dict[str, Any]] = {
    # Radares actualmente sincronizando en EUMETNET ORD
    "esahr": {"id": "esahr", "name": "Málaga / Alhaurín el Grande", "province": "Málaga", "lat": 36.6133, "lon": -4.6593, "range_km": 240, "short_range_km": 145, "alt_m": 1159, "source": "OPERA", "source_name": "EUMETNET OPERA (S3/MQTT)", "band": "Banda C (5.62 GHz)", "polarization": "Doble Polarización (Dual-Pol)", "doppler": "Doppler", "wmo_code": "8475", "antenna_diam": "4.2 m", "beam": "0.9°", "aemet_code": "ml"},
    "esclg": {"id": "esclg", "name": "Sevilla / El Castillo de las Guardas", "province": "Sevilla", "lat": 37.6875, "lon": -6.3344, "range_km": 250, "short_range_km": 145, "alt_m": 530, "source": "OPERA", "source_name": "EUMETNET OPERA (S3/MQTT)", "band": "Banda C (5.62 GHz)", "polarization": "Polarización Simple (Single-Pol)", "doppler": "Doppler", "wmo_code": "8386", "antenna_diam": "4.5 m", "beam": "0.95°", "aemet_code": "se"},
    "esnjr": {"id": "esnjr", "name": "Almería / Níjar", "province": "Almería", "lat": 36.8324, "lon": -2.0810, "range_km": 240, "short_range_km": 145, "alt_m": 498, "source": "OPERA", "source_name": "EUMETNET OPERA (S3/MQTT)", "band": "Banda C (5.62 GHz)", "polarization": "Doble Polarización (Dual-Pol)", "doppler": "Doppler", "wmo_code": "8489", "antenna_diam": "4.5 m", "beam": "0.95°", "aemet_code": "am"},
    "estjv": {"id": "estjv", "name": "Madrid / Torrejón de Velasco", "province": "Madrid", "lat": 40.1759, "lon": -3.7136, "range_km": 240, "short_range_km": 145, "alt_m": 717, "source": "OPERA", "source_name": "EUMETNET OPERA (S3/MQTT)", "band": "Banda C (5.62 GHz)", "polarization": "Doble Polarización (Dual-Pol)", "doppler": "Doppler", "wmo_code": "8228", "antenna_diam": "4.5 m", "beam": "0.95°", "aemet_code": "ma"},
    "essft": {"id": "essft", "name": "Cáceres / Sierra de Fuentes", "province": "Cáceres", "lat": 39.4288, "lon": -6.2853, "range_km": 240, "short_range_km": 145, "alt_m": 667, "source": "OPERA", "source_name": "EUMETNET OPERA (S3/MQTT)", "band": "Banda C (5.62 GHz)", "polarization": "Doble Polarización (Dual-Pol)", "doppler": "Doppler", "wmo_code": "8262", "antenna_diam": "4.5 m", "beam": "0.95°", "aemet_code": "cc"},
    "esgrm": {"id": "esgrm", "name": "Salamanca / Guadramiro", "province": "Salamanca", "lat": 41.0116, "lon": -6.4777, "range_km": 240, "short_range_km": 145, "alt_m": 793, "source": "OPERA", "source_name": "EUMETNET OPERA (S3/MQTT)", "band": "Banda C (5.62 GHz)", "polarization": "Polarización Simple (Single-Pol)", "doppler": "Doppler", "wmo_code": "8140", "antenna_diam": "4.2 m", "beam": "0.9°", "aemet_code": "sa"},
    "eslid": {"id": "eslid", "name": "Palencia / Autilla del Pino", "province": "Palencia/Valladolid", "lat": 41.9956, "lon": -4.6028, "range_km": 240, "short_range_km": 145, "alt_m": 887, "source": "OPERA", "source_name": "EUMETNET OPERA (S3/MQTT)", "band": "Banda C (5.62 GHz)", "polarization": "Polarización Simple (Single-Pol)", "doppler": "Doppler", "wmo_code": "8072", "antenna_diam": "4.2 m", "beam": "0.9°", "aemet_code": "vd"},
    "essse": {"id": "essse", "name": "País Vasco / Monte Oiz (Bakio)", "province": "Bizkaia/Gipuzkoa", "lat": 43.4033, "lon": -2.8419, "range_km": 240, "short_range_km": 145, "alt_m": 625, "source": "OPERA", "source_name": "EUMETNET OPERA (S3/MQTT)", "band": "Banda C (5.6 GHz)", "polarization": "Polarización Simple (Single-Pol)", "doppler": "Doppler", "wmo_code": "8081", "antenna_diam": "4.2 m", "beam": "0.9°", "aemet_code": "ss"},
    "espdg": {"id": "espdg", "name": "Zaragoza / Perdiguera", "province": "Zaragoza", "lat": 41.7339, "lon": -0.5458, "range_km": 240, "short_range_km": 145, "alt_m": 833, "source": "OPERA", "source_name": "EUMETNET OPERA (S3/MQTT)", "band": "Banda C (5.62 GHz)", "polarization": "Polarización Simple (Single-Pol)", "doppler": "Doppler", "wmo_code": "8162", "antenna_diam": "4.2 m", "beam": "0.9°", "aemet_code": "za"},
    "esgld": {"id": "esgld", "name": "Barcelona / Corbera de Llobregat (Gelida)", "province": "Barcelona", "lat": 41.4081, "lon": 1.8848, "range_km": 240, "short_range_km": 145, "alt_m": 662, "source": "OPERA", "source_name": "EUMETNET OPERA (S3/MQTT)", "band": "Banda C (5.62 GHz)", "polarization": "Doble Polarización (Dual-Pol)", "doppler": "Doppler", "wmo_code": "8179", "antenna_diam": "4.5 m", "beam": "0.95°", "aemet_code": "ba"},
    "esbnv": {"id": "esbnv", "name": "Tenerife / Buenavista del Norte", "province": "Santa Cruz de Tenerife", "lat": 28.3109, "lon": -16.8238, "range_km": 240, "short_range_km": 145, "alt_m": 1367, "source": "OPERA", "source_name": "EUMETNET OPERA (S3/MQTT)", "band": "Banda C (5.62 GHz)", "polarization": "Polarización Simple (Single-Pol)", "doppler": "Doppler", "wmo_code": "60015", "antenna_diam": "4.2 m", "beam": "0.9°", "aemet_code": "ca"},
    "esatn": {"id": "esatn", "name": "Gran Canaria / Artenara", "province": "Las Palmas", "lat": 28.0186, "lon": -15.6144, "range_km": 240, "short_range_km": 145, "alt_m": 1778, "source": "OPERA", "source_name": "EUMETNET OPERA (S3/MQTT)", "band": "Banda C (5.62 GHz)", "polarization": "Polarización Simple (Single-Pol)", "doppler": "Doppler", "wmo_code": "60028", "antenna_diam": "4.2 m", "beam": "0.9°", "aemet_code": "ca"},
    # Radares regionales integrados mediante AEMET OpenData
    "escul": {"id": "escul", "name": "Valencia / Cullera (Muntanya de les Raboses)", "province": "Valencia", "lat": 39.1761, "lon": -0.2521, "range_km": 240, "short_range_km": 145, "alt_m": 247, "source": "AEMET", "source_name": "AEMET OpenData (Regional)", "band": "Banda C (5.62 GHz)", "polarization": "Polarización Simple (Single-Pol)", "doppler": "Doppler", "wmo_code": "8289", "antenna_diam": "4.2 m", "beam": "0.9°", "aemet_code": "va"},
    "esmur": {"id": "esmur", "name": "Murcia / Fortuna (Sierra de la Pila)", "province": "Murcia", "lat": 38.2644, "lon": -1.1897, "range_km": 240, "short_range_km": 145, "alt_m": 1274, "source": "AEMET", "source_name": "AEMET OpenData (Regional)", "band": "Banda C (5.62 GHz)", "polarization": "Polarización Simple (Single-Pol)", "doppler": "Doppler", "wmo_code": "8364", "antenna_diam": "4.2 m", "beam": "0.9°", "aemet_code": "mu"},
    "espma": {"id": "espma", "name": "Mallorca / Llucmajor", "province": "Illes Balears", "lat": 39.3797, "lon": 2.7850, "range_km": 240, "short_range_km": 145, "alt_m": 130, "source": "AEMET", "source_name": "AEMET OpenData (Regional)", "band": "Banda C (5.62 GHz)", "polarization": "Polarización Simple (Single-Pol)", "doppler": "Doppler", "wmo_code": "8308", "antenna_diam": "4.2 m", "beam": "0.9°", "aemet_code": "pm"},
    "espmb": {"id": "espmb", "name": "Mallorca / Llucmajor", "province": "Illes Balears", "lat": 39.3797, "lon": 2.7850, "range_km": 240, "short_range_km": 145, "alt_m": 130, "source": "AEMET", "source_name": "AEMET OpenData (Regional)", "band": "Banda C (5.62 GHz)", "polarization": "Polarización Simple (Single-Pol)", "doppler": "Doppler", "wmo_code": "8308", "antenna_diam": "4.2 m", "beam": "0.9°", "aemet_code": "pm"},
    "escor": {"id": "escor", "name": "A Coruña / Cerceda", "province": "A Coruña", "lat": 43.1689, "lon": -8.5269, "range_km": 240, "short_range_km": 145, "alt_m": 612, "source": "AEMET", "source_name": "AEMET OpenData (Regional)", "band": "Banda C (5.62 GHz)", "polarization": "Polarización Simple (Single-Pol)", "doppler": "Doppler", "wmo_code": "8007", "antenna_diam": "4.2 m", "beam": "0.9°", "aemet_code": "co"}
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
        RIOS_FILE: Path = BASE_DIR / "data" / "rios.geojson"
        RIOS_DATA_FILE: Path = DATA_DIR / "rios.geojson"
        
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

        # SAIH Ebro Open Data API Key
        EBRO_API_KEY: Optional[str] = None

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
        AROME_MAX_STEPS: int = 51  # Pasos hasta +51h (2 días y 3 horas)

        # AEMET HARMONIE-AROME (High-Resolution Convective Model, ~2.5km / 0.025°)
        HARMONIE_CACHE_DIR: Path = HARMONIE_CACHE_DIR
        HARMONIE_POLL_INTERVAL_SECONDS: int = 1800  # 30 minutos (corrida completa)
        HARMONIE_UPDATING_INTERVAL_SECONDS: int = 300  # 5 minutos (corrida en progreso)
        HARMONIE_MAX_STEPS: int = 48  # Pasos hasta +48h (2 días)
        AEMET_HARMONIE_DOWNLOAD_URL: str = "https://www.aemet.es/es/api-eltiempo/modelos/download/harmonie/PB"

        # DWD ICON-EU (Icosahedral Non-hydrostatic Regional Model, ~6.5km / 0.0625°)
        ICON_CACHE_DIR: Path = ICON_CACHE_DIR
        ICON_POLL_INTERVAL_SECONDS: int = 1800  # 30 minutos (corrida completa)
        ICON_UPDATING_INTERVAL_SECONDS: int = 300  # 5 minutos (corrida en progreso)
        ICON_MAX_STEPS: int = 120  # Pasos hasta +120h (5 días)

        # MSC / ECCC GEM-GDPS (Canadian Global Deterministic Prediction System, ~15km / 0.15°)
        GEM_CACHE_DIR: Path = GEM_CACHE_DIR
        GEM_POLL_INTERVAL_SECONDS: int = 1800  # 30 minutos (corrida completa)
        GEM_UPDATING_INTERVAL_SECONDS: int = 300  # 5 minutos (corrida en progreso)
        GEM_MAX_STEPS: int = 240  # Pasos hasta +240h (10 días)

        # UK Met Office Unified Model (Global Deterministic, ~10km / 0.09°)
        UKMO_CACHE_DIR: Path = UKMO_CACHE_DIR
        UKMO_POLL_INTERVAL_SECONDS: int = 1800  # 30 minutos (corrida completa)
        UKMO_UPDATING_INTERVAL_SECONDS: int = 300  # 5 minutos (corrida en progreso)
        UKMO_MAX_STEPS: int = 144  # Pasos hasta +144h (6 días)
        UKMO_S3_BUCKET: str = "met-office-atmospheric-model-data"
        UKMO_S3_REGION: str = "eu-west-2"
        UKMO_S3_PREFIX: str = "global-deterministic-10km"

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
        HARMONIE_CACHE_DIR: Path = HARMONIE_CACHE_DIR
        ICON_CACHE_DIR: Path = ICON_CACHE_DIR
        GEM_CACHE_DIR: Path = GEM_CACHE_DIR
        AEMET_ATOM_URL: str = os.getenv("AEMET_ATOM_URL", "https://www.aemet.es/documentos_d/eltiempo/prediccion/avisos/rss/CAP_AFAE_wah_ATOM.xml")
        AEMET_USER_AGENT: str = "RainLoc-WeatherService/1.0 (+https://github.com/carlosalventosa/RainLoc)"
        AEMET_REFRESH_INTERVAL_SECONDS: int = int(os.getenv("AEMET_REFRESH_INTERVAL_SECONDS", "180"))
        AEMET_API_KEY: Optional[str] = os.getenv("AEMET_API_KEY", None)
        AEMET_OPENDATA_BASE_URL: str = os.getenv("AEMET_OPENDATA_BASE_URL", "https://opendata.aemet.es/opendata/api")
        EBRO_API_KEY: Optional[str] = os.getenv("EBRO_API_KEY", None)
        AEMET_HARMONIE_DOWNLOAD_URL: str = os.getenv("AEMET_HARMONIE_DOWNLOAD_URL", "https://www.aemet.es/es/api-eltiempo/modelos/download/harmonie/PB")
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
        AROME_MAX_STEPS: int = 51
        HARMONIE_POLL_INTERVAL_SECONDS: int = 1800
        HARMONIE_UPDATING_INTERVAL_SECONDS: int = 300
        HARMONIE_MAX_STEPS: int = 48
        ICON_POLL_INTERVAL_SECONDS: int = 1800
        ICON_UPDATING_INTERVAL_SECONDS: int = 300
        ICON_MAX_STEPS: int = 120
        GEM_POLL_INTERVAL_SECONDS: int = 1800
        GEM_UPDATING_INTERVAL_SECONDS: int = 300
        GEM_MAX_STEPS: int = 240
        UKMO_CACHE_DIR: Path = UKMO_CACHE_DIR
        UKMO_POLL_INTERVAL_SECONDS: int = 1800
        UKMO_UPDATING_INTERVAL_SECONDS: int = 300
        UKMO_MAX_STEPS: int = 144
        UKMO_S3_BUCKET: str = "met-office-atmospheric-model-data"
        UKMO_S3_REGION: str = "eu-west-2"
        UKMO_S3_PREFIX: str = "global-deterministic-10km"

settings = Settings()
