import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

from app.config import settings
from app.api.v1 import api_v1_router
from app.services.scheduler import background_scheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("rainloc-backend")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Inicio: Arrancar el planificador en segundo plano
    logger.info("Iniciando servicios de fondo de RainLoc...")
    background_scheduler.start()
    yield
    # Parada: Detener tareas de fondo ordenadamente
    logger.info("Deteniendo servicios de fondo de RainLoc...")
    background_scheduler.stop()

app = FastAPI(
    title=settings.PROJECT_NAME,
    description="Backend API para monitorización hidrológica y meteorológica en cuencas de la Comunitat Valenciana.",
    version=settings.VERSION,
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
    swagger_ui_parameters={
        "syntaxHighlight.theme": "monokai",
        "syntaxHighlight.activated": False,
        "defaultModelsExpandDepth": -1
    }
)

# Gzip compression for responses > 1KB
app.add_middleware(GZipMiddleware, minimum_size=1024)

# CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_v1_router)

@app.get("/health", tags=["Health"])
async def health_check():
    return {
        "status": "healthy",
        "app": settings.PROJECT_NAME,
        "version": settings.VERSION
    }

@app.get("/", tags=["Root"])
async def root():
    return {
        "message": "RainLoc API está activa. Consulta la documentación interactiva en /docs",
        "api_v1": "/api/v1"
    }
