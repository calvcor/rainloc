"""
RainLoc - Basin Hydrology & On-The-Fly Model Volume Integration Service
Calcula en tiempo real (al vuelo) la precipitación media (mm), volumen acumulado e incremental (hm³)
y puntos máximos para cualquier cuenca o subsistema hidrográfico de la CHJ sobre los modelos ECMWF IFS, GFS y AROME.
"""
import io
import json
import logging
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple

import numpy as np
from PIL import Image, ImageDraw

from app.config import settings, BASE_DIR
from app.services.ecmwf_worker import ecmwf_worker
from app.services.gfs_worker import gfs_worker
from app.services.arome_worker import arome_worker
from app.services.icon_worker import icon_worker

logger = logging.getLogger("rainloc-backend.basin-hydrology")

# Rejilla estándar RainLoc (Web Mercator 950 x 1500)
SPAIN_BBOX = {"lat_min": 35.0, "lat_max": 44.5, "lon_min": -10.0, "lon_max": 5.0}
R_EARTH = 6378137.0
Y_MERC_MIN = R_EARTH * np.log(np.tan(np.pi / 4 + np.radians(SPAIN_BBOX["lat_min"]) / 2))
Y_MERC_MAX = R_EARTH * np.log(np.tan(np.pi / 4 + np.radians(SPAIN_BBOX["lat_max"]) / 2))
GRID_H, GRID_W = 950, 1500

Y_MERC_GRID = np.linspace(Y_MERC_MAX, Y_MERC_MIN, GRID_H)
SPAIN_GRID_LATS = np.degrees(2 * np.arctan(np.exp(Y_MERC_GRID / R_EARTH)) - np.pi / 2)


class BasinHydrologyService:
    """Servicio de cálculo hidrológico espacial sobre modelos de predicción numérica (NWP)."""
    _instance: Optional["BasinHydrologyService"] = None

    def __new__(cls) -> "BasinHydrologyService":
        if cls._instance is None:
            cls._instance = super(BasinHydrologyService, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if getattr(self, "_initialized", False):
            return
        self._initialized = True
        self.basins: Dict[str, Dict[str, Any]] = {}
        self._precompute_basin_masks()

    def _lonlat_to_grid_xy(self, lon: float, lat: float) -> Tuple[float, float]:
        """Convierte coordenadas geográficas WGS84 a píxel flotante (col, row) en la rejilla."""
        col = (lon - SPAIN_BBOX["lon_min"]) / (SPAIN_BBOX["lon_max"] - SPAIN_BBOX["lon_min"]) * (GRID_W - 1)
        y_m = R_EARTH * np.log(np.tan(np.pi / 4 + np.radians(lat) / 2))
        row = (Y_MERC_MAX - y_m) / (Y_MERC_MAX - Y_MERC_MIN) * (GRID_H - 1)
        return (col, row)

    def _precompute_basin_masks(self):
        """Carga y rasteriza todos los subsistemas hidrográficos para consultas ultra-rápidas O(1)."""
        # Calcular pesos de área por fila considerando curvatura esférica
        dx_merc = (R_EARTH * np.radians(SPAIN_BBOX["lon_max"] - SPAIN_BBOX["lon_min"])) / GRID_W
        dy_merc = (Y_MERC_MAX - Y_MERC_MIN) / GRID_H
        row_areas_m2 = (np.cos(np.radians(SPAIN_GRID_LATS)) ** 2) * dx_merc * dy_merc

        # Localizar archivo GeoJSON
        geojson_candidates = [
            getattr(settings, "SUBSISTEMAS_OPTIMIZED_FILE", None),
            getattr(settings, "SUBSISTEMAS_FILE", None),
            BASE_DIR / "subsistemas.optimized.geojson",
            BASE_DIR / "subsistemas.geojson",
            BASE_DIR / "backend" / "subsistemas.optimized.geojson",
            BASE_DIR / "backend" / "subsistemas.geojson"
        ]

        geojson_path = None
        for p in geojson_candidates:
            if p and p.exists():
                geojson_path = p
                break

        if not geojson_path:
            logger.warning("BasinHydrology: No se encontró archivo GeoJSON de subsistemas.")
            return

        try:
            with open(geojson_path, "r", encoding="utf-8") as f:
                fc = json.load(f)

            features = fc.get("features", [])
            for feat in features:
                props = feat.get("properties", {})
                basin_id = str(feat.get("id") or props.get("id") or "")
                basin_name = props.get("Subsistema") or props.get("name") or f"Cuenca {basin_id}"
                sist_name = props.get("NomSistExp") or "Sistema CHJ"
                superf_km2 = float(props.get("Superf km2") or props.get("Area km2") or 0.0)

                geom = feat.get("geometry", {})
                g_type = geom.get("type")
                coords = geom.get("coordinates", [])

                img = Image.new("1", (GRID_W, GRID_H), 0)
                draw = ImageDraw.Draw(img)

                if g_type == "Polygon":
                    for ring in coords:
                        pts = [self._lonlat_to_grid_xy(p[0], p[1]) for p in ring]
                        if len(pts) >= 3:
                            draw.polygon(pts, fill=1)
                elif g_type == "MultiPolygon":
                    for poly in coords:
                        for ring in poly:
                            pts = [self._lonlat_to_grid_xy(p[0], p[1]) for p in ring]
                            if len(pts) >= 3:
                                draw.polygon(pts, fill=1)

                mask = np.array(img, dtype=bool)
                rows, cols = np.where(mask)
                if len(rows) == 0:
                    continue

                raw_weights = row_areas_m2[rows]
                raw_sum_m2 = float(np.sum(raw_weights))
                official_area_m2 = (superf_km2 * 1e6) if superf_km2 > 0 else raw_sum_m2

                # Normalizar pesos para coincidencia perfecta con el área oficial
                scale_factor = official_area_m2 / max(1.0, raw_sum_m2)
                norm_weights = raw_weights * scale_factor

                self.basins[basin_id] = {
                    "id": basin_id,
                    "name": basin_name,
                    "system": sist_name,
                    "area_km2": superf_km2 or round(raw_sum_m2 / 1e6, 2),
                    "area_m2": official_area_m2,
                    "rows": rows,
                    "cols": cols,
                    "weights": norm_weights.astype(np.float32),
                    "pixel_count": len(rows)
                }

            logger.info(f"BasinHydrology: {len(self.basins)} cuencas/subsistemas precalculados y listos para inferencia.")
        except Exception as e:
            logger.error(f"BasinHydrology: Error inicializando máscaras de cuencas: {e}")

    def list_basins(self) -> List[Dict[str, Any]]:
        """Devuelve el catálogo de cuencas/subsistemas disponibles con sus nombres y áreas."""
        result = []
        for b_id, b_info in self.basins.items():
            result.append({
                "id": b_id,
                "name": b_info["name"],
                "system": b_info["system"],
                "area_km2": b_info["area_km2"],
                "pixel_count": b_info["pixel_count"]
            })
        result.sort(key=lambda x: (x["system"], x["name"]))
        return result

    def get_basin_info(self, basin_id: str) -> Optional[Dict[str, Any]]:
        """Busca una cuenca por ID exacto, ID numérico ('1' -> '1-0'), nombre exacto o nombre parcial."""
        if not basin_id:
            return None
        basin_str = str(basin_id).strip()
        if basin_str in self.basins:
            return self.basins[basin_str]

        clean_id = basin_str.lower()
        # 1. Coincidencia directa por id o nombre completo
        for b_id, b_info in self.basins.items():
            if b_id.lower() == clean_id or b_info["name"].lower() == clean_id:
                return b_info

        # 2. Coincidencia por ID numérico (ej: "1" coincide con "1-0" o "1")
        for b_id, b_info in self.basins.items():
            if b_id.split("-")[0].lower() == clean_id:
                return b_info

        # 3. Coincidencia por subcadena en el nombre del subsistema
        for b_id, b_info in self.basins.items():
            if clean_id in b_info["name"].lower():
                return b_info

        return None

    def calculate_basin_hydrograph(self, model_key: str, basin_id: str) -> Dict[str, Any]:
        """
        Calcula al vuelo el hidrograma de aportación hídrica (hm³) y precipitación (mm)
        para todos los pasos disponibles del modelo seleccionado.
        """
        basin = self.get_basin_info(basin_id)
        if not basin:
            return {"error": f"Cuenca '{basin_id}' no encontrada", "basin_id": basin_id}

        model_clean = model_key.lower().strip()
        worker = None
        model_name = "Modelo Meteorológico"

        if "ecmwf" in model_clean:
            worker = ecmwf_worker
            model_name = "ECMWF IFS (0.25°)"
        elif "gfs" in model_clean:
            worker = gfs_worker
            model_name = "NOAA GFS (0.25°)"
        elif "arome" in model_clean:
            worker = arome_worker
            model_name = "Météo-France AROME (~1.3-2.5 km)"
        elif "icon" in model_clean:
            worker = icon_worker
            model_name = "DWD ICON-EU (6.5 km)"
        else:
            worker = ecmwf_worker
            model_name = "ECMWF IFS (0.25°)"

        meta = worker.get_metadata()
        steps_info = meta.get("steps", [])
        avail_steps = meta.get("available_steps", [])
        cycle_str = meta.get("cycle_str", "")
        run_str = meta.get("run", "")

        rows = basin["rows"]
        cols = basin["cols"]
        weights = basin["weights"]
        total_area_m2 = basin["area_m2"]

        series = []
        max_total_vol_hm3 = 0.0
        peak_interval_hm3 = 0.0
        peak_step = None
        peak_time_local = ""

        # Matrices acumuladas
        last_step_total_mm = 0.0

        for s_info in steps_info:
            step = s_info.get("step")
            if step is None or step not in avail_steps:
                continue

            # Obtener matriz total acumulada
            tot_matrix = worker.get_matrix(step=step, layer_type="total")
            if tot_matrix is None:
                continue

            basin_tot_vals = tot_matrix[rows, cols]
            # Precipitación media acumulada ponderada
            avg_tot_mm = float(np.sum(basin_tot_vals * weights) / total_area_m2)
            max_tot_mm = float(np.max(basin_tot_vals))
            # Volumen total acumulado: P (mm) * Area (m2) / 10^9 = hm3
            tot_vol_hm3 = round(float(avg_tot_mm * total_area_m2 / 1e9), 2)

            # Obtener matriz intervalar (3h / 1h)
            int_matrix = worker.get_matrix(step=step, layer_type="interval")
            if int_matrix is not None:
                basin_int_vals = int_matrix[rows, cols]
                avg_int_mm = float(np.sum(basin_int_vals * weights) / total_area_m2)
                max_int_mm = float(np.max(basin_int_vals))
            else:
                avg_int_mm = max(0.0, avg_tot_mm - last_step_total_mm)
                max_int_mm = avg_int_mm

            int_vol_hm3 = round(float(avg_int_mm * total_area_m2 / 1e9), 2)
            last_step_total_mm = avg_tot_mm

            if int_vol_hm3 > peak_interval_hm3:
                peak_interval_hm3 = int_vol_hm3
                peak_step = step
                peak_time_local = s_info.get("valid_time_local", "")

            if tot_vol_hm3 > max_total_vol_hm3:
                max_total_vol_hm3 = tot_vol_hm3

            series.append({
                "step": step,
                "delta_hours": s_info.get("delta_hours", 3),
                "valid_time_iso": s_info.get("valid_time_iso", ""),
                "valid_time_local": s_info.get("valid_time_local", f"+{step}h"),
                "total_vol_hm3": tot_vol_hm3,
                "interval_vol_hm3": int_vol_hm3,
                "avg_total_mm": round(avg_tot_mm, 1),
                "avg_interval_mm": round(avg_int_mm, 1),
                "max_point_mm": round(max_tot_mm, 1),
                "max_point_interval_mm": round(max_int_mm, 1),
                "is_fallback": bool(s_info.get("is_fallback", False))
            })

        return {
            "basin_id": basin["id"],
            "basin_name": basin["name"],
            "system_name": basin["system"],
            "area_km2": basin["area_km2"],
            "model_key": model_clean,
            "model_name": model_name,
            "cycle_str": cycle_str,
            "run": run_str,
            "total_accumulated_hm3": max_total_vol_hm3,
            "peak_interval_hm3": peak_interval_hm3,
            "peak_step": peak_step,
            "peak_time_local": peak_time_local,
            "steps_count": len(series),
            "series": series
        }


basin_hydrology_service = BasinHydrologyService()
