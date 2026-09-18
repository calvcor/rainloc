# RainLoc - Backend API

Servidor backend desarrollado en Python con **FastAPI** para la plataforma GIS RainLoc.

## Estructura del Backend
```
backend/
├── app/
│   ├── api/
│   │   └── v1/
│   │       ├── __init__.py      # Router centralizador v1
│   │       ├── cuencas.py       # Endpoints GeoJSON cuencas CHJ
│   │       ├── warnings.py      # Avisos AEMET (preparado para datos)
│   │       ├── radar.py         # Radar meteorológico y acumulados hoy
│   │       └── models.py        # Modelos NWP (AROME, ICON, ECMWF)
│   ├── config.py            # Configuración, CORS y paths
│   └── main.py              # Factoría FastAPI y middlewares
├── requirements.txt        # Dependencias de producción
└── run.py                 # Script de arranque con Uvicorn
```

## Puesta en marcha

### 1. Crear entorno virtual e instalar dependencias
```bash
cd backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Arrancar el servidor
```bash
python run.py
```
El servidor estará disponible en: `http://localhost:8000`

### 3. Documentación interactiva (Swagger / OpenAPI)
- **Swagger UI**: [http://localhost:8000/docs](http://localhost:8000/docs)
- **ReDoc**: [http://localhost:8000/redoc](http://localhost:8000/redoc)

## Endpoints Principales disponibles (v1)

- `GET /health` — Estado del servicio.
- `GET /api/v1/cuencas` — GeoJSON completo de los subsistemas CHJ.
- `GET /api/v1/cuencas/sistemas` — Resumen agrupado por Sistema de Explotación.
- `GET /api/v1/cuencas/{id}` — Geometría y datos de un subsistema específico.
- `GET /api/v1/warnings/aemet` — Avisos meteorológicos (contrato listo).
- `GET /api/v1/radar/metadata` — Pasos temporales del radar.
- `GET /api/v1/radar/accumulated/today` — Acumulados de precipitación.
- `GET /api/v1/models/arome` — Modelo de alta resolución AROME.
- `GET /api/v1/models/icon-d2` — Modelo convectivo ICON-D2.
- `GET /api/v1/models/ecmwf` — Modelo global ECMWF IFS.
# rainloc
