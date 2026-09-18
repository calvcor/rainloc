"""
RainLoc - ECMWF IFS Open Data Worker & Pipeline
Ingesta secuencial y progresiva de previsiones de precipitación acumulada del modelo ECMWF IFS.
Recorte geoespacial a Península Ibérica y Baleares, conversión de unidades (m -> mm),
cálculo de acumulados totales e intervalos trihorarios, generación de imágenes PNG transparentes
Web Mercator y matrices .npy para consultas instantáneas en el cliente.
"""
import os
import gc
import json
import time
import shutil
import logging
import asyncio
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional, List, Tuple, Set, AsyncGenerator

import numpy as np
from PIL import Image
from scipy.interpolate import RegularGridInterpolator

try:
    from ecmwf.opendata import Client
    ECMWF_CLIENT_AVAILABLE = True
except ImportError:
    ECMWF_CLIENT_AVAILABLE = False

try:
    import xarray as xr
    XARRAY_AVAILABLE = True
except ImportError:
    XARRAY_AVAILABLE = False

from app.config import settings

logger = logging.getLogger("rainloc-backend.ecmwf")

# Bounding Box y Cuadrícula Web Mercator idéntica al radar para superposición perfecta en Leaflet
SPAIN_BBOX = {"lat_min": 35.0, "lat_max": 44.5, "lon_min": -10.0, "lon_max": 5.0}
R_EARTH = 6378137.0
Y_MERC_MIN = R_EARTH * np.log(np.tan(np.pi / 4 + np.radians(SPAIN_BBOX["lat_min"]) / 2))
Y_MERC_MAX = R_EARTH * np.log(np.tan(np.pi / 4 + np.radians(SPAIN_BBOX["lat_max"]) / 2))
GRID_H = 950
GRID_W = 1500

Y_MERC_GRID = np.linspace(Y_MERC_MAX, Y_MERC_MIN, GRID_H)
SPAIN_GRID_LATS = np.degrees(2 * np.arctan(np.exp(Y_MERC_GRID / R_EARTH)) - np.pi / 2)
SPAIN_GRID_LONS = np.linspace(SPAIN_BBOX["lon_min"], SPAIN_BBOX["lon_max"], GRID_W)

# Pasos estándar de predicción ECMWF (cada 3 horas hasta +144h y cada 6 horas hasta +240h / 10 días)
ECMWF_STEPS = list(range(3, 147, 3)) + list(range(150, 246, 6))

# Fuentes de ECMWF Open Data en orden de prioridad
DATA_SOURCES = ["ecmwf", "aws", "azure"]


def colorize_precip_array(precip_mm: np.ndarray) -> np.ndarray:
    """
    Rampa de color meteorológica oficial para precipitación acumulada/intervalos (mm).
    Valores < 0.1 mm son transparentes.
    Retorna matriz RGBA uint8 de forma (H, W, 4).
    """
    h, w = precip_mm.shape
    rgba = np.zeros((h, w, 4), dtype=np.uint8)

    # 0.1 - 1.0 mm: Celeste muy suave
    m1 = (precip_mm >= 0.1) & (precip_mm < 1.0)
    rgba[m1] = [186, 230, 253, 160]

    # 1.0 - 3.0 mm: Azul cielo
    m2 = (precip_mm >= 1.0) & (precip_mm < 3.0)
    rgba[m2] = [56, 189, 248, 185]

    # 3.0 - 10.0 mm: Azul intenso
    m3 = (precip_mm >= 3.0) & (precip_mm < 10.0)
    rgba[m3] = [2, 132, 199, 210]

    # 10.0 - 20.0 mm: Verde claro
    m4 = (precip_mm >= 10.0) & (precip_mm < 20.0)
    rgba[m4] = [74, 222, 128, 225]

    # 20.0 - 40.0 mm: Verde bosque
    m5 = (precip_mm >= 20.0) & (precip_mm < 40.0)
    rgba[m5] = [22, 163, 74, 235]

    # 40.0 - 70.0 mm: Amarillo intenso
    m6 = (precip_mm >= 40.0) & (precip_mm < 70.0)
    rgba[m6] = [250, 204, 21, 245]

    # 70.0 - 100.0 mm: Naranja
    m7 = (precip_mm >= 70.0) & (precip_mm < 100.0)
    rgba[m7] = [249, 115, 22, 250]

    # 100.0 - 150.0 mm: Rojo
    m8 = (precip_mm >= 100.0) & (precip_mm < 150.0)
    rgba[m8] = [239, 68, 68, 255]

    # 150.0 - 250.0 mm: Magenta / Púrpura
    m9 = (precip_mm >= 150.0) & (precip_mm < 250.0)
    rgba[m9] = [217, 70, 239, 255]

    # >= 250.0 mm: Blanco / Púrpura brillante
    m10 = (precip_mm >= 250.0)
    rgba[m10] = [255, 255, 255, 255]

    return rgba


class ECMWFWorker:
    """Worker para la descarga, transformación y servicio de datos de ECMWF IFS Open Data."""

    def __init__(self):
        self.cache_dir: Path = settings.ECMWF_CACHE_DIR
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.current_manifest: Optional[Dict[str, Any]] = None
        self._sync_lock = asyncio.Lock()
        self._is_syncing: bool = False
        self._in_memory_arrays: Dict[str, np.ndarray] = {}
        self._subscribers: Set[asyncio.Queue] = set()
        self._load_latest_manifest_from_disk()

    def notify_ecmwf_update(self, cycle_str: str, available_steps: List[int], step_added: Optional[int] = None, status: str = "ready", is_syncing: bool = False):
        """Notifica de forma inmediata y thread-safe a todos los clientes SSE conectados."""
        max_step = max(available_steps) if available_steps else 0
        run_str = ""
        if "_" in cycle_str:
            parts = cycle_str.split("_")
            if len(parts) > 1:
                run_str = parts[1].lower()
        if not run_str and len(cycle_str) >= 2:
            run_str = cycle_str[-2:].lower()
        
        payload = {
            "event": "ecmwf_update",
            "cycle_str": cycle_str,
            "run": run_str or "00z",
            "step_added": step_added,
            "available_steps": available_steps,
            "max_step": max_step,
            "is_complete": (max_step >= 240),
            "is_updating": is_syncing or (max_step < 240 and status != "complete"),
            "status": status,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        dead_subscribers = set()
        for q in list(self._subscribers):
            try:
                loop = getattr(q, "_loop", None)
                if loop and loop.is_running():
                    loop.call_soon_threadsafe(q.put_nowait, payload)
                else:
                    q.put_nowait(payload)
            except Exception:
                dead_subscribers.add(q)
        for q in dead_subscribers:
            self._subscribers.discard(q)

    async def subscribe_stream(self) -> AsyncGenerator[Dict[str, Any], None]:
        """Generador asíncrono para clientes SSE con keep-alive periódico."""
        loop = asyncio.get_running_loop()
        q = asyncio.Queue(maxsize=20)
        q._loop = loop
        self._subscribers.add(q)
        try:
            # Enviar el estado actual inmediatamente al conectar
            current_meta = self.get_metadata()
            initial_payload = {
                "event": "ecmwf_init",
                "cycle_str": current_meta.get("cycle_str"),
                "run": current_meta.get("run"),
                "available_steps": current_meta.get("available_steps", []),
                "max_step": current_meta.get("max_step", 0),
                "is_complete": current_meta.get("is_complete", False),
                "is_updating": current_meta.get("is_updating", False),
                "status": current_meta.get("status", "empty"),
                "timestamp": current_meta.get("updated_at") or datetime.now(timezone.utc).isoformat()
            }
            yield initial_payload

            while True:
                try:
                    payload = await asyncio.wait_for(q.get(), timeout=25.0)
                    yield payload
                except asyncio.TimeoutError:
                    yield {"event": "ping"}
        finally:
            self._subscribers.discard(q)

    def _load_latest_manifest_from_disk(self):
        """Intenta cargar el último manifiesto existente en el disco al arrancar."""
        try:
            cycle_dirs = [d for d in self.cache_dir.iterdir() if d.is_dir() and (d / "manifest.json").exists()]
            if not cycle_dirs:
                return
            cycle_dirs.sort(key=lambda d: d.name, reverse=True)
            latest_dir = cycle_dirs[0]
            manifest_file = latest_dir / "manifest.json"
            with open(manifest_file, "r", encoding="utf-8") as f:
                self.current_manifest = json.load(f)
                logger.info(f"ECMWF: Manifiesto cargado desde disco para ciclo {self.current_manifest.get('cycle_str')}")
        except Exception as e:
            logger.warning(f"ECMWF: No se pudo cargar manifiesto previo: {e}")

    def get_metadata(self) -> Dict[str, Any]:
        """Devuelve los metadatos del ciclo actual disponible con run y estado de actualización."""
        if self.current_manifest:
            meta = dict(self.current_manifest)
            meta["is_syncing"] = self._is_syncing
            
            # Formatear run (00z, 06z, 12z, 18z)
            cycle_iso = meta.get("cycle")
            cycle_str = meta.get("cycle_str", "")
            run_str = ""
            if cycle_iso:
                try:
                    dt = datetime.fromisoformat(cycle_iso.replace("Z", "+00:00"))
                    run_str = f"{dt.hour:02d}z"
                except Exception:
                    pass
            if not run_str and "_" in cycle_str:
                parts = cycle_str.split("_")
                if len(parts) > 1:
                    run_str = parts[1].lower()
            
            meta["run"] = run_str or "00z"
            avail = meta.get("available_steps", [])
            max_step = max(avail) if avail else 0
            meta["max_step"] = max_step
            meta["is_complete"] = (max_step >= 240)
            meta["is_updating"] = self._is_syncing or (max_step < 240 and meta.get("status") != "complete")
            return meta

        return {
            "model": "ECMWF IFS",
            "cycle": None,
            "cycle_str": None,
            "run": None,
            "status": "synchronizing" if self._is_syncing else "empty",
            "available_steps": [],
            "max_step": 0,
            "is_complete": False,
            "is_updating": self._is_syncing,
            "bbox": SPAIN_BBOX,
            "steps": [],
            "updated_at": None,
            "is_syncing": self._is_syncing
        }

    def get_image_path(self, step: int, layer_type: str = "total") -> Optional[Path]:
        """Devuelve la ruta a la imagen PNG generada."""
        if not self.current_manifest:
            return None
        cycle_str = self.current_manifest.get("cycle_str")
        if not cycle_str:
            return None

        clean_type = "interval" if layer_type.lower() == "interval" else "total"
        img_path = self.cache_dir / cycle_str / f"{clean_type}_step_{step:02d}.png"
        if img_path.exists():
            return img_path
        return None

    def get_value_at(self, lat: float, lon: float, step: int, layer_type: str = "total") -> Optional[float]:
        """
        Consulta en tiempo O(1) el valor de precipitación (mm) en una coordenada específica.
        """
        if not (SPAIN_BBOX["lat_min"] <= lat <= SPAIN_BBOX["lat_max"] and
                SPAIN_BBOX["lon_min"] <= lon <= SPAIN_BBOX["lon_max"]):
            return None

        if not self.current_manifest:
            return None

        cycle_str = self.current_manifest.get("cycle_str")
        if not cycle_str:
            return None

        clean_type = "interval" if layer_type.lower() == "interval" else "total"
        cache_key = f"{cycle_str}_{clean_type}_{step:02d}"

        matrix = self._in_memory_arrays.get(cache_key)
        if matrix is None:
            npy_path = self.cache_dir / cycle_str / f"{clean_type}_step_{step:02d}.npy"
            if not npy_path.exists():
                return None
            try:
                matrix = np.load(npy_path)
                self._in_memory_arrays[cache_key] = matrix
            except Exception as e:
                logger.error(f"Error cargando matriz .npy {npy_path}: {e}")
                return None

        # Convertir coordenada lat/lon a fila/columna en la cuadrícula Web Mercator
        try:
            y_merc = R_EARTH * np.log(np.tan(np.pi / 4 + np.radians(lat) / 2))
            row = int(np.round((Y_MERC_MAX - y_merc) / (Y_MERC_MAX - Y_MERC_MIN) * (GRID_H - 1)))
            col = int(np.round((lon - SPAIN_BBOX["lon_min"]) / (SPAIN_BBOX["lon_max"] - SPAIN_BBOX["lon_min"]) * (GRID_W - 1)))

            if 0 <= row < GRID_H and 0 <= col < GRID_W:
                val = float(matrix[row, col])
                return round(val, 1)
        except Exception:
            pass

        return None

    def _purge_old_cycles(self, keep_cycle_str: str):
        """Rolling cache: Elimina ciclos antiguos de disco para mantener espacio."""
        try:
            for item in self.cache_dir.iterdir():
                if item.is_dir() and item.name != keep_cycle_str:
                    logger.info(f"ECMWF: Purgando ciclo anterior {item.name}")
                    shutil.rmtree(item, ignore_errors=True)
            
            # Limpiar memoria de matrices antiguas
            keys_to_delete = [k for k in self._in_memory_arrays.keys() if not k.startswith(keep_cycle_str)]
            for k in keys_to_delete:
                del self._in_memory_arrays[k]
            gc.collect()
        except Exception as e:
            logger.warning(f"ECMWF: Error purgando ciclos anteriores: {e}")

    def _detect_latest_cycle(self) -> Optional[datetime]:
        """Detecta la fecha y hora del ciclo más reciente publicado en ECMWF Open Data."""
        for src in DATA_SOURCES:
            try:
                client = Client(source=src)
                latest_dt = client.latest(type="fc", param="tp")
                if latest_dt:
                    logger.info(f"ECMWF: Ciclo más reciente detectado ({src}): {latest_dt}")
                    return latest_dt
            except Exception as e:
                logger.debug(f"ECMWF: Error detectando ciclo en fuente {src}: {e}")
        return None

    def _download_step_grib(self, client: Any, cycle_dt: datetime, step: int, target_file: Path) -> bool:
        """Intenta descargar el archivo GRIB2 de un paso específico probando fuentes con reintentos."""
        for src in DATA_SOURCES:
            try:
                c = Client(source=src)
                c.retrieve(
                    date=cycle_dt.date(),
                    time=cycle_dt.hour,
                    step=step,
                    type="fc",
                    param="tp",
                    target=str(target_file)
                )
                if target_file.exists() and target_file.stat().st_size > 1000:
                    return True
            except Exception as e:
                err_str = str(e).lower()
                if "not found" in err_str or "404" in err_str:
                    logger.debug(f"ECMWF: Paso {step} aún no generado/disponible en {src} (404/NotFound).")
                else:
                    logger.warning(f"ECMWF: Fallo descargando paso {step} de {src}: {e}")
        return False

    def _process_grib_to_mercator(self, grib_path: Path) -> Optional[np.ndarray]:
        """
        Lee el binario GRIB2 con cfgrib, extrae 'tp', convierte m -> mm,
        recorta a la Península Ibérica e interpola a la malla Web Mercator (950, 1500).
        """
        ds = None
        try:
            ds = xr.open_dataset(grib_path, engine="cfgrib")
            if "tp" not in ds:
                return None

            tp_data = ds["tp"].values * 1000.0  # metros -> milímetros
            lats = ds.latitude.values
            lons = ds.longitude.values

            # Manejo de coordenadas de longitud si vienen en formato 0..360
            if np.any(lons > 180):
                lons = np.where(lons > 180, lons - 360, lons)
                sort_lon_idx = np.argsort(lons)
                lons = lons[sort_lon_idx]
                tp_data = tp_data[:, sort_lon_idx]

            # Asegurar orden ascendente de latitudes para RegularGridInterpolator
            if lats[0] > lats[-1]:
                lats = lats[::-1]
                tp_data = tp_data[::-1, :]

            # Recorte previo para optimizar interpolación
            lat_mask = (lats >= (SPAIN_BBOX["lat_min"] - 1.5)) & (lats <= (SPAIN_BBOX["lat_max"] + 1.5))
            lon_mask = (lons >= (SPAIN_BBOX["lon_min"] - 1.5)) & (lons <= (SPAIN_BBOX["lon_max"] + 1.5))

            sub_lats = lats[lat_mask]
            sub_lons = lons[lon_mask]
            sub_data = tp_data[lat_mask][:, lon_mask]

            interp = RegularGridInterpolator(
                (sub_lats, sub_lons),
                sub_data,
                method="linear",
                bounds_error=False,
                fill_value=0.0
            )

            mesh_lat, mesh_lon = np.meshgrid(SPAIN_GRID_LATS, SPAIN_GRID_LONS, indexing="ij")
            grid_pts = np.stack([mesh_lat.ravel(), mesh_lon.ravel()], axis=-1)
            res_matrix = interp(grid_pts).reshape((GRID_H, GRID_W)).astype(np.float32)

            # Reemplazar posibles NaNs o valores negativos por 0.0
            res_matrix = np.nan_to_num(res_matrix, nan=0.0, posinf=0.0, neginf=0.0)
            res_matrix = np.maximum(0.0, res_matrix)

            return res_matrix
        except Exception as e:
            logger.error(f"ECMWF: Error procesando GRIB2 {grib_path}: {e}")
            return None
        finally:
            if ds is not None:
                try:
                    ds.close()
                except Exception:
                    pass

    async def sync_ecmwf_forecast(self, max_steps: int = 240) -> Dict[str, Any]:
        """
        Ejecuta la sincronización progresiva del modelo ECMWF IFS.
        Descarga paso a paso, calcula totales e intervalos, guarda PNGs/.npy
        y elimina de inmediato los ficheros binarios .grib2 temporales.
        """
        if not (ECMWF_CLIENT_AVAILABLE and XARRAY_AVAILABLE):
            logger.error("ECMWF: Dependencias ecmwf-opendata o xarray/cfgrib no disponibles.")
            return {"error": "Missing scientific dependencies (ecmwf-opendata, xarray, cfgrib)"}

        async with self._sync_lock:
            self._is_syncing = True
            try:
                loop = asyncio.get_running_loop()
                result = await loop.run_in_executor(None, self._sync_worker_sync, max_steps)
                return result
            finally:
                self._is_syncing = False

    def _sync_worker_sync(self, max_steps: int) -> Dict[str, Any]:
        """Lógica sincrónica ejecutada en el ThreadPool."""
        t_start = time.time()
        logger.info("ECMWF: Comprobando nuevo ciclo del modelo IFS...")

        latest_dt = self._detect_latest_cycle()
        if not latest_dt:
            logger.warning("ECMWF: No se pudo determinar el ciclo más reciente.")
            return self.get_metadata()

        cycle_str = latest_dt.strftime("%Y%m%d_%Hz")
        cycle_dir = self.cache_dir / cycle_str
        cycle_dir.mkdir(parents=True, exist_ok=True)

        # Cargar manifiesto existente si ya teníamos datos de este ciclo
        manifest_file = cycle_dir / "manifest.json"
        manifest_data: Dict[str, Any] = {
            "model": "ECMWF IFS (Open Data)",
            "cycle": latest_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "cycle_str": cycle_str,
            "status": "processing",
            "resolution": "0.25° (~25 km)",
            "bbox": SPAIN_BBOX,
            "steps": [],
            "available_steps": [],
            "updated_at": datetime.now(timezone.utc).isoformat()
        }

        if manifest_file.exists():
            try:
                with open(manifest_file, "r", encoding="utf-8") as f:
                    manifest_data = json.load(f)
            except Exception:
                pass

        available_steps = set(manifest_data.get("available_steps", []))
        steps_to_process = [s for s in ECMWF_STEPS if s <= max_steps]

        client = Client(source="ecmwf")
        last_total_matrix: Optional[np.ndarray] = None
        new_steps_downloaded = 0

        for idx, step in enumerate(steps_to_process):
            total_png_path = cycle_dir / f"total_step_{step:02d}.png"
            total_npy_path = cycle_dir / f"total_step_{step:02d}.npy"
            interval_png_path = cycle_dir / f"interval_step_{step:02d}.png"
            interval_npy_path = cycle_dir / f"interval_step_{step:02d}.npy"

            # Si ya está completamente procesado en disco, cargamos la matriz total para el siguiente delta
            if step in available_steps and total_png_path.exists() and total_npy_path.exists() and interval_png_path.exists():
                try:
                    last_total_matrix = np.load(total_npy_path)
                    continue
                except Exception:
                    pass

            # Descargar archivo GRIB2 temporal para este step
            tmp_grib = cycle_dir / f"tmp_{cycle_str}_step{step:02d}.grib2"
            try:
                logger.info(f"ECMWF: Descargando paso +{step}h del ciclo {cycle_str}...")
                ok = self._download_step_grib(client, latest_dt, step, tmp_grib)
                if not ok:
                    logger.info(f"ECMWF: Paso +{step}h aún no disponible en el supercomputador. Deteniendo pipeline progresivo.")
                    break

                # Procesar a matriz Web Mercator
                total_matrix = self._process_grib_to_mercator(tmp_grib)
                if total_matrix is None:
                    logger.warning(f"ECMWF: No se pudo procesar matriz para paso +{step}h")
                    break

                # Calcular intervalo: ΔP_t = P_t - P_{prev_step}
                prev_step = steps_to_process[idx - 1] if idx > 0 else None
                delta_h = step if prev_step is None else (step - prev_step)

                if prev_step is None or last_total_matrix is None:
                    if prev_step is not None:
                        prev_npy = cycle_dir / f"total_step_{prev_step:02d}.npy"
                        if prev_npy.exists():
                            try:
                                last_total_matrix = np.load(prev_npy)
                                interval_matrix = np.maximum(0.0, total_matrix - last_total_matrix)
                            except Exception:
                                interval_matrix = total_matrix.copy()
                        else:
                            interval_matrix = total_matrix.copy()
                    else:
                        interval_matrix = total_matrix.copy()
                else:
                    interval_matrix = np.maximum(0.0, total_matrix - last_total_matrix)

                # Generar imágenes PNG RGBA transparentes
                total_rgba = colorize_precip_array(total_matrix)
                interval_rgba = colorize_precip_array(interval_matrix)

                Image.fromarray(total_rgba, "RGBA").save(total_png_path, format="PNG", optimize=True)
                Image.fromarray(interval_rgba, "RGBA").save(interval_png_path, format="PNG", optimize=True)

                # Guardar matrices float16/float32 .npy
                np.save(total_npy_path, total_matrix.astype(np.float32))
                np.save(interval_npy_path, interval_matrix.astype(np.float32))

                # Guardar en caché de memoria para consultas 0ms
                self._in_memory_arrays[f"{cycle_str}_total_{step:02d}"] = total_matrix
                self._in_memory_arrays[f"{cycle_str}_interval_{step:02d}"] = interval_matrix

                last_total_matrix = total_matrix

                # Calcular fecha de validez
                valid_dt = latest_dt + timedelta(hours=step)
                step_info = {
                    "step": step,
                    "delta_hours": delta_h,
                    "valid_time_iso": valid_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "valid_time_local": valid_dt.strftime("%d/%m %H:00 UTC"),
                    "total_png": f"/api/v1/models/ecmwf/image?step={step}&type=total",
                    "interval_png": f"/api/v1/models/ecmwf/image?step={step}&type=interval",
                    "max_total_mm": round(float(np.max(total_matrix)), 1),
                    "max_interval_mm": round(float(np.max(interval_matrix)), 1)
                }

                # Actualizar lista de pasos en el manifiesto
                available_steps.add(step)
                # Reemplazar o insertar info del paso
                manifest_data["steps"] = [s for s in manifest_data.get("steps", []) if s.get("step") != step]
                manifest_data["steps"].append(step_info)
                manifest_data["steps"].sort(key=lambda s: s["step"])
                manifest_data["available_steps"] = sorted(list(available_steps))
                manifest_data["updated_at"] = datetime.now(timezone.utc).isoformat()
                manifest_data["status"] = "ready" if len(available_steps) > 0 else "processing"

                # Guardar manifiesto incremental en disco
                with open(manifest_file, "w", encoding="utf-8") as f:
                    json.dump(manifest_data, f, indent=2)

                self.current_manifest = manifest_data
                new_steps_downloaded += 1

                # Notificar a los clientes vía SSE del nuevo paso disponible
                self.notify_ecmwf_update(
                    cycle_str=cycle_str,
                    available_steps=manifest_data["available_steps"],
                    step_added=step,
                    status=manifest_data["status"]
                )

            finally:
                # OBLIGATORIO: Eliminación inmediata del binario GRIB2 descargado y sus índices cfgrib
                if tmp_grib.exists():
                    try:
                        os.unlink(tmp_grib)
                        logger.debug(f"ECMWF: Eliminado binario temporal {tmp_grib.name}")
                    except Exception as e:
                        logger.warning(f"ECMWF: No se pudo eliminar binario temporal {tmp_grib}: {e}")
                
                # Eliminar ficheros de índice .idx generados por cfgrib
                for idx_file in cycle_dir.glob(f"{tmp_grib.name}*.idx"):
                    try:
                        os.unlink(idx_file)
                    except Exception:
                        pass

        # Purga de ciclos anteriores (Rolling Cache)
        self._purge_old_cycles(cycle_str)

        if new_steps_downloaded > 0:
            self.notify_ecmwf_update(
                cycle_str=cycle_str,
                available_steps=manifest_data.get("available_steps", []),
                status="ready"
            )

        elapsed = time.time() - t_start
        logger.info(f"ECMWF: Sincronización completada en {elapsed:.1f}s. Pasos disponibles: {sorted(list(available_steps))} (Nuevos: {new_steps_downloaded})")
        return self.get_metadata()


ecmwf_worker = ECMWFWorker()
