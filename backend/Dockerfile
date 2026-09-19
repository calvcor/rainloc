FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Instalar dependencias del sistema requeridas para pyproj, h5py y librerías científicas (eccodes)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libproj-dev \
    proj-data \
    proj-bin \
    libhdf5-dev \
    libeccodes-dev \
    libeccodes0 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Instalar dependencias de Python
COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r ./backend/requirements.txt

# Copiar código del backend y cartografía base requerida
COPY backend/ ./backend/
COPY subsistemas.optimized.geojson ./subsistemas.optimized.geojson
COPY subsistemas.geojson ./subsistemas.geojson
COPY ccaa.geojson ./ccaa.geojson

WORKDIR /app/backend

# Crear directorios de datos y cachés
RUN mkdir -p data/radar_cache data/ecmwf_cache

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
  CMD curl -f http://localhost:8000/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
