"""
Servicio de Ingesta y Streaming de Rayos en Tiempo Real (Blitzortung Live).
Mantiene una conexión persistente por WebSocket, decodifica las descargas eléctricas
y almacena un búfer rodante en memoria de los últimos 60 minutos para España y alrededores.
"""

import asyncio
import json
import logging
import time
from collections import deque
from typing import Any, AsyncGenerator, Dict, List, Optional, Set
import math
import websockets

logger = logging.getLogger("rainloc-backend.lightning")

def haversine_distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calcula la distancia ortodrómica en kilómetros entre dos coordenadas geográficas."""
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2.0) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
         math.sin(dlon / 2.0) ** 2)
    return 2.0 * R * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))


# Límites geográficos de cobertura (Península Ibérica, Baleares, Canarias y cuenca Mediterráneo Occidental/Atlántico)
SPAIN_BBOX = {
    "lat_min": 26.0,
    "lat_max": 46.0,
    "lon_min": -20.0,
    "lon_max": 12.0
}

# Servidores WebSocket de respaldo de Blitzortung
BLITZORTUNG_SERVERS = [
    "wss://ws1.blitzortung.org",
    "wss://ws7.blitzortung.org",
    "wss://ws8.blitzortung.org"
]

def decode_blitzortung(data_str: str) -> str:
    """
    Decodifica el mensaje comprimido/ofuscado (variante LZW) emitido por el WebSocket de Blitzortung.
    """
    if not data_str:
        return ""
    e: Dict[int, str] = {}
    d = list(data_str)
    c = d[0]
    f = c
    g = [c]
    h = 256
    o = h
    for i in range(1, len(d)):
        a_code = ord(d[i])
        a_val = d[i] if h > a_code else (e[a_code] if a_code in e else f + c)
        g.append(a_val)
        c = a_val[0]
        e[o] = f + c
        o += 1
        f = a_val
    return "".join(g)


class LightningService:
    def __init__(self):
        self._running = False
        self._task: Optional[asyncio.Task] = None
        # Búfer rodante de rayos (máximo 60 minutos)
        # Cada elemento: dict con { "time": float, "lat": float, "lon": float, "pol": int, "stations": int, "delay": float }
        self._strikes: deque = deque()
        self._subscribers: Set[asyncio.Queue] = set()
        self._lock = asyncio.Lock()
        self._last_received_time: Optional[float] = None
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    def start(self):
        """Inicia el servicio en segundo plano si no está activo."""
        if not self._running:
            self._running = True
            self._task = asyncio.create_task(self._worker_loop())
            logger.info("Servicio de Rayos en Tiempo Real iniciado.")

    def stop(self):
        """Detiene el servicio y cancela la tarea."""
        if self._running:
            self._running = False
            if self._task and not self._task.done():
                self._task.cancel()
            self._connected = False
            logger.info("Servicio de Rayos en Tiempo Real detenido.")

    async def _worker_loop(self):
        """Bucle principal de conexión y escucha con reconexión automática."""
        server_idx = 0
        while self._running:
            server_uri = BLITZORTUNG_SERVERS[server_idx % len(BLITZORTUNG_SERVERS)]
            server_idx += 1
            try:
                logger.info(f"Conectando a stream de rayos Blitzortung ({server_uri})...")
                async with websockets.connect(
                    server_uri,
                    ping_interval=20,
                    ping_timeout=20,
                    close_timeout=5
                ) as ws:
                    # Enviar mensaje de handshake obligatorio
                    await ws.send(json.dumps({"a": 111}))
                    self._connected = True
                    logger.info(f"Conexión WebSocket de rayos establecida con éxito ({server_uri}).")

                    while self._running:
                        msg = await ws.recv()
                        if isinstance(msg, bytes):
                            msg = msg.decode("utf-8", errors="ignore")
                        
                        try:
                            decoded_str = decode_blitzortung(msg)
                            raw_strike = json.loads(decoded_str)
                            await self._process_strike(raw_strike)
                        except Exception as parse_err:
                            logger.debug(f"Error decodificando impacto de rayo: {parse_err}")

            except asyncio.CancelledError:
                break
            except Exception as conn_err:
                self._connected = False
                logger.warning(f"Desconexión del stream de rayos ({server_uri}): {conn_err}. Reintentando en 5s...")
                await asyncio.sleep(5)

    async def _process_strike(self, data: Dict[str, Any]):
        """Procesa y almacena un rayo detectado si entra en el área geográfica de interés."""
        lat = float(data.get("lat", 0.0))
        lon = float(data.get("lon", 0.0))

        # Filtrar por coordenadas de España / Península Ibérica / Baleares / Canarias
        if not (SPAIN_BBOX["lat_min"] <= lat <= SPAIN_BBOX["lat_max"] and
                SPAIN_BBOX["lon_min"] <= lon <= SPAIN_BBOX["lon_max"]):
            return

        # Tiempo en segundos epoch
        raw_time = data.get("time", 0)
        time_sec = float(raw_time) / 1e9 if raw_time > 1e12 else float(raw_time)
        now_sec = time.time()
        if time_sec <= 0:
            time_sec = now_sec

        strike = {
            "time": time_sec,
            "lat": round(lat, 4),
            "lon": round(lon, 4),
            "pol": int(data.get("pol", 0)), # 0 = negativo, 1 = positivo
            "stations": len(data.get("sig", [])) if "sig" in data else 0,
            "delay": float(data.get("delay", 0.0))
        }

        async with self._lock:
            self._strikes.append(strike)
            self._last_received_time = now_sec
            self._prune_old_strikes(now_sec)

        # Notificar a los clientes conectados por SSE
        await self._broadcast_strike(strike)

    def _prune_old_strikes(self, now_sec: float):
        """Elimina del búfer los rayos que superen los 15 minutos (900 segundos)."""
        cutoff = now_sec - 900
        while self._strikes and self._strikes[0]["time"] < cutoff:
            self._strikes.popleft()

    async def _broadcast_strike(self, strike: Dict[str, Any]):
        """Emite el nuevo rayo a todos los clientes suscritos vía SSE."""
        if not self._subscribers:
            return
        dead_subscribers = []
        for q in self._subscribers:
            try:
                q.put_nowait(strike)
            except asyncio.QueueFull:
                pass
            except Exception:
                dead_subscribers.append(q)
        for q in dead_subscribers:
            self._subscribers.discard(q)

    async def get_recent_strikes(self, minutes: int = 15, station_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Obtiene los rayos registrados en los últimos N minutos (máximo 15m),
        con opción de filtrar solo aquellos dentro de la cobertura de un radar (240km).
        """
        from app.services.radar_worker import SPANISH_RADAR_STATIONS
        now_sec = time.time()
        mins = max(1, min(15, minutes))
        cutoff = now_sec - (mins * 60)
        cutoff_1m = now_sec - 60
        cutoff_5m = now_sec - 300

        async with self._lock:
            self._prune_old_strikes(now_sec)
            filtered = [
                {
                    **s,
                    "age_sec": round(now_sec - s["time"], 1)
                }
                for s in self._strikes
                if s["time"] >= cutoff
            ]

        # Filtrado geográfico si se solicita para una estación individual (radio 240km)
        if station_id and station_id in SPANISH_RADAR_STATIONS:
            st = SPANISH_RADAR_STATIONS[station_id]
            st_lat, st_lon = st["lat"], st["lon"]
            st_range = float(st.get("range_km", 240.0))
            filtered = [
                s for s in filtered
                if haversine_distance_km(st_lat, st_lon, s["lat"], s["lon"]) <= st_range
            ]

        # Estadísticas dentro de los 15 minutos máximos
        total = len(filtered)
        count_1m = sum(1 for s in filtered if s["time"] >= cutoff_1m)
        count_5m = sum(1 for s in filtered if s["time"] >= cutoff_5m)
        rate_min = count_1m if mins <= 1 else round(count_5m / 5.0, 1)

        latest_time = filtered[-1]["time"] if filtered else self._last_received_time

        return {
            "status": "online" if self._connected else "reconnecting",
            "minutes_window": mins,
            "station_id": station_id,
            "total_strikes": total,
            "rate_per_min": rate_min,
            "breakdown": {
                "last_1m": count_1m,
                "last_5m": count_5m,
                "last_15m": total
            },
            "latest_timestamp": latest_time,
            "strikes": filtered
        }

    async def subscribe_stream(self) -> AsyncGenerator[Dict[str, Any], None]:
        """Generador asíncrono para Server-Sent Events (SSE)."""
        queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._subscribers.add(queue)
        try:
            while self._running:
                strike = await queue.get()
                yield strike
        finally:
            self._subscribers.discard(queue)


lightning_service = LightningService()
