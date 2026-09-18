"""
Scheduler en segundo plano para tareas periódicas de RainLoc (AEMET ATOM, Radar ORD, etc.)
"""
import asyncio
import logging
from typing import Optional
from app.config import settings
from app.services.aemet_atom import aemet_atom_service
from app.services.radar_worker import radar_service
from app.services.lightning_service import lightning_service

logger = logging.getLogger("rainloc-backend.scheduler")

class BackgroundScheduler:
    """Controlador de las tareas asíncronas en background para refrescar avisos, radar y rayos."""

    def __init__(self):
        self._aemet_task: Optional[asyncio.Task] = None
        self._radar_task: Optional[asyncio.Task] = None
        self._running: bool = False
        self.aemet_interval: int = settings.AEMET_REFRESH_INTERVAL_SECONDS
        self.radar_interval: int = settings.RADAR_POLL_INTERVAL_SECONDS

    async def _aemet_loop(self):
        try:
            await aemet_atom_service.check_and_update()
        except Exception as e:
            logger.error(f"Error inicial en comprobación de AEMET: {e}")

        while self._running:
            try:
                await asyncio.sleep(self.aemet_interval)
                if self._running:
                    await aemet_atom_service.check_and_update()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error en bucle AEMET: {e}")

    async def _radar_loop(self):
        # Primera descarga / procesado de radar al iniciar
        try:
            await radar_service.sync_radar_data()
        except Exception as e:
            logger.error(f"Error inicial sincronizando radar: {e}")

        while self._running:
            try:
                await asyncio.sleep(self.radar_interval)
                if self._running:
                    await radar_service.sync_radar_data()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error en bucle de radar: {e}")

    def start(self):
        if not self._running:
            self._running = True
            self._aemet_task = asyncio.create_task(self._aemet_loop())
            self._radar_task = asyncio.create_task(self._radar_loop())
            # Iniciar cliente MQTT de radar en segundo plano
            radar_service.start_mqtt_client()
            # Iniciar conexión WebSocket de rayos en segundo plano
            lightning_service.start()
            logger.info("BackgroundScheduler activado (AEMET + Radar ORD + Rayos Blitzortung).")

    def stop(self):
        if self._running:
            self._running = False
            if self._aemet_task and not self._aemet_task.done():
                self._aemet_task.cancel()
            if self._radar_task and not self._radar_task.done():
                self._radar_task.cancel()
            lightning_service.stop()
            logger.info("BackgroundScheduler detenido.")

background_scheduler = BackgroundScheduler()

