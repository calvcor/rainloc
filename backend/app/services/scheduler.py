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
from app.services.ecmwf_worker import ecmwf_worker
from app.services.gfs_worker import gfs_worker

logger = logging.getLogger("rainloc-backend.scheduler")

class BackgroundScheduler:
    """Controlador de las tareas asíncronas en background para refrescar avisos, radar, rayos y modelos NWP (ECMWF, GFS)."""

    def __init__(self):
        self._aemet_task: Optional[asyncio.Task] = None
        self._radar_task: Optional[asyncio.Task] = None
        self._ecmwf_task: Optional[asyncio.Task] = None
        self._gfs_task: Optional[asyncio.Task] = None
        self._running: bool = False
        self.aemet_interval: int = settings.AEMET_REFRESH_INTERVAL_SECONDS
        self.radar_interval: int = settings.RADAR_POLL_INTERVAL_SECONDS
        self.ecmwf_interval: int = getattr(settings, "ECMWF_POLL_INTERVAL_SECONDS", 1800)
        self.gfs_interval: int = getattr(settings, "GFS_POLL_INTERVAL_SECONDS", 1800)

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

    async def _ecmwf_loop(self):
        # Primera comprobación y sincronización de ECMWF al iniciar
        try:
            await ecmwf_worker.sync_ecmwf_forecast()
        except Exception as e:
            logger.error(f"Error inicial sincronizando ECMWF IFS: {e}")

        while self._running:
            try:
                await asyncio.sleep(self.ecmwf_interval)
                if self._running:
                    await ecmwf_worker.sync_ecmwf_forecast()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error en bucle de ECMWF IFS: {e}")

    async def _gfs_loop(self):
        # Primera comprobación y sincronización de GFS al iniciar
        try:
            await gfs_worker.sync_gfs_forecast()
        except Exception as e:
            logger.error(f"Error inicial sincronizando NOAA GFS: {e}")

        while self._running:
            try:
                await asyncio.sleep(self.gfs_interval)
                if self._running:
                    await gfs_worker.sync_gfs_forecast()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error en bucle de NOAA GFS: {e}")

    def start(self):
        if not self._running:
            self._running = True
            self._aemet_task = asyncio.create_task(self._aemet_loop())
            self._radar_task = asyncio.create_task(self._radar_loop())
            self._ecmwf_task = asyncio.create_task(self._ecmwf_loop())
            self._gfs_task = asyncio.create_task(self._gfs_loop())
            # Iniciar cliente MQTT de radar en segundo plano
            radar_service.start_mqtt_client()
            # Iniciar conexión WebSocket de rayos en segundo plano
            lightning_service.start()
            logger.info("BackgroundScheduler activado (AEMET + Radar ORD + Rayos + ECMWF IFS + NOAA GFS).")

    def stop(self):
        if self._running:
            self._running = False
            if self._aemet_task and not self._aemet_task.done():
                self._aemet_task.cancel()
            if self._radar_task and not self._radar_task.done():
                self._radar_task.cancel()
            if self._ecmwf_task and not self._ecmwf_task.done():
                self._ecmwf_task.cancel()
            if self._gfs_task and not self._gfs_task.done():
                self._gfs_task.cancel()
            lightning_service.stop()
            logger.info("BackgroundScheduler detenido.")

background_scheduler = BackgroundScheduler()

