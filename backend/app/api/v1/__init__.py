from fastapi import APIRouter
from app.api.v1.cuencas import router as cuencas_router
from app.api.v1.warnings import router as warnings_router
from app.api.v1.radar import router as radar_router
from app.api.v1.models import router as models_router
from app.api.v1.lightning import router as lightning_router
from app.api.v1.saih import router as saih_router

api_v1_router = APIRouter(prefix="/api/v1")

api_v1_router.include_router(cuencas_router)
api_v1_router.include_router(warnings_router)
api_v1_router.include_router(radar_router)
api_v1_router.include_router(models_router)
api_v1_router.include_router(lightning_router)
api_v1_router.include_router(saih_router)
