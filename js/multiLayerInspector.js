/**
 * RainLoc - Inspector Multi-Capa en Hover
 * Detecta simultáneamente todas las capas activas bajo el cursor
 * y genera un tooltip unificado consolidado por secciones.
 */

import { CONFIG } from "./config.js";
import { StorageManager } from "./storage.js";

export class MultiLayerInspector {
  constructor(mapManager, cuencasLayer, layerManager, uiManager) {
    this.mapManager = mapManager;
    this.map = mapManager.map;
    this.cuencasLayer = cuencasLayer;
    this.layerManager = layerManager;
    this.uiManager = uiManager;

    // Instancia única de tooltip flotante global
    this.unifiedTooltip = L.tooltip({
      sticky: false,
      direction: "auto",
      className: "rainloc-unified-tooltip",
      offset: [15, 10],
      opacity: 0.98
    });

    this._lastLatLng = null;
    this._hoverThrottle = null;
    this._currentHoveredCuenca = null;

    // Cache y debounce para consultas puntuales de reflectividad dBZ
    this._lastRadarLookup = null;
    this._dbzDebounceTimer = null;

    // Cache espacial e in-flight tracker para consultas de precisión milimétrica en modelos (ECMWF, GFS, AROME)
    this._modelValuesCache = new Map();
    this._modelRequestsInFlight = new Set();

    // Estado para modo móvil táctil persistente (Tap-to-Inspect)
    this.inspectionMarker = null;
    this._activeMobileLatLng = null;
  }

  /**
   * Obtiene la reflectividad en dBZ instantáneamente (0ms) a partir del pixel en el canvas de la imagen raster
   */
  _getInstantRadarPixel(lat, lng) {
    if (!this.layerManager || !this.layerManager.radarCanvasData) return null;
    const { ctx, width, height, bounds } = this.layerManager.radarCanvasData;
    if (!bounds || bounds.length < 2) return null;

    const latMin = Math.min(bounds[0][0], bounds[1][0]);
    const latMax = Math.max(bounds[0][0], bounds[1][0]);
    const lonMin = Math.min(bounds[0][1], bounds[1][1]);
    const lonMax = Math.max(bounds[0][1], bounds[1][1]);

    if (lat < latMin || lat > latMax || lng < lonMin || lng > lonMax) return null;

    // Coordenadas normalizadas a píxeles usando proyección Web Mercator idéntica a Leaflet ImageOverlay
    const yPt = Math.log(Math.tan(Math.PI / 4 + (lat * Math.PI / 360)));
    const yMin = Math.log(Math.tan(Math.PI / 4 + (latMin * Math.PI / 360)));
    const yMax = Math.log(Math.tan(Math.PI / 4 + (latMax * Math.PI / 360)));

    const x = Math.floor(((lng - lonMin) / (lonMax - lonMin)) * width);
    const y = Math.floor(((yMax - yPt) / (yMax - yMin)) * height);

    if (x < 0 || x >= width || y < 0 || y >= height) return null;

    try {
      const pixel = ctx.getImageData(x, y, 1, 1).data;
      const r = pixel[0], g = pixel[1], b = pixel[2], a = pixel[3];

      // Si el píxel es transparente o sin reflectividad (<30 opacidad), no hay precipitación
      if (a < 30) return null;

      // Paleta meteorológica oficial de RainLoc (valores dBZ, etiquetas y colores exactos)
      const palette = [
        { minDbz: 55, dbz: 55, label: 'Muy Fuerte / Granizo', rgb: [239, 68, 68], color: '#ef4444' },
        { minDbz: 45, dbz: 45, label: 'Muy Fuerte', rgb: [249, 115, 22], color: '#f97316' },
        { minDbz: 35, dbz: 35, label: 'Fuerte', rgb: [234, 179, 8], color: '#eab308' },
        { minDbz: 25, dbz: 25, label: 'Moderada', rgb: [34, 197, 94], color: '#22c55e' },
        { minDbz: 15, dbz: 15, label: 'Ligera', rgb: [2, 132, 199], color: '#0284c7' },
        { minDbz: 8,  dbz: 10, label: 'Débil', rgb: [56, 189, 248], color: '#38bdf8' }
      ];

      let bestMatch = palette[palette.length - 1];
      let minDistance = Infinity;

      for (const p of palette) {
        const dr = r - p.rgb[0];
        const dg = g - p.rgb[1];
        const db = b - p.rgb[2];
        const dist = dr * dr + dg * dg + db * db;
        if (dist < minDistance) {
          minDistance = dist;
          bestMatch = p;
        }
      }

      return {
        dbz: bestMatch.dbz,
        badgeColor: bestMatch.color,
        rain_intensity: bestMatch.label
      };

    } catch (e) {
      return null;
    }
  }

  /**
   * Obtiene la precipitación en mm instantáneamente (0ms) a partir del pixel en el canvas de ECMWF IFS
   */
  _getInstantEcmwfPixel(lat, lng) {
    if (!this.layerManager || !this.layerManager.ecmwfCanvasData) return null;
    const { ctx, width, height, bounds, step, type, validText } = this.layerManager.ecmwfCanvasData;
    if (!bounds || bounds.length < 2) return null;

    const latMin = Math.min(bounds[0][0], bounds[1][0]);
    const latMax = Math.max(bounds[0][0], bounds[1][0]);
    const lonMin = Math.min(bounds[0][1], bounds[1][1]);
    const lonMax = Math.max(bounds[0][1], bounds[1][1]);

    if (lat < latMin || lat > latMax || lng < lonMin || lng > lonMax) return null;

    const yPt = Math.log(Math.tan(Math.PI / 4 + (lat * Math.PI / 360)));
    const yMin = Math.log(Math.tan(Math.PI / 4 + (latMin * Math.PI / 360)));
    const yMax = Math.log(Math.tan(Math.PI / 4 + (latMax * Math.PI / 360)));

    const x = Math.floor(((lng - lonMin) / (lonMax - lonMin)) * width);
    const y = Math.floor(((yMax - yPt) / (yMax - yMin)) * height);

    if (x < 0 || x >= width || y < 0 || y >= height) return null;

    try {
      const pixel = ctx.getImageData(x, y, 1, 1).data;
      const r = pixel[0], g = pixel[1], b = pixel[2], a = pixel[3];

      if (a < 30) {
        return { mm: 0.0, label: 'Sin precipitación (<0.1 mm)', color: '#94a3b8', step, type, validText };
      }

      const palette = [
        { minMm: 250, mm: 250, label: '> 250 mm (Extrema)', rgb: [255, 255, 255], color: '#ffffff' },
        { minMm: 150, mm: 180, label: '150 - 250 mm (Torrencial)', rgb: [217, 70, 239], color: '#d946ef' },
        { minMm: 100, mm: 120, label: '100 - 150 mm (Muy Fuerte)', rgb: [239, 68, 68], color: '#ef4444' },
        { minMm: 70, mm: 85, label: '70 - 100 mm (Muy Fuerte)', rgb: [249, 115, 22], color: '#f97316' },
        { minMm: 40, mm: 55, label: '40 - 70 mm (Fuerte)', rgb: [250, 204, 21], color: '#facc15' },
        { minMm: 20, mm: 30, label: '20 - 40 mm (Moderada)', rgb: [22, 163, 74], color: '#16a34a' },
        { minMm: 10, mm: 15, label: '10 - 20 mm (Moderada)', rgb: [74, 222, 128], color: '#4ade80' },
        { minMm: 3, mm: 6, label: '3 - 10 mm (Ligera)', rgb: [2, 132, 199], color: '#0284c7' },
        { minMm: 1, mm: 2, label: '1 - 3 mm (Débil)', rgb: [56, 189, 248], color: '#38bdf8' },
        { minMm: 0.1, mm: 0.5, label: '0.1 - 1 mm (Muy Débil)', rgb: [186, 230, 253], color: '#bae6fd' }
      ];

      let bestMatch = palette[palette.length - 1];
      let minDistance = Infinity;

      for (const p of palette) {
        const dr = r - p.rgb[0];
        const dg = g - p.rgb[1];
        const db = b - p.rgb[2];
        const dist = dr * dr + dg * dg + db * db;
        if (dist < minDistance) {
          minDistance = dist;
          bestMatch = p;
        }
      }

      return {
        mm: bestMatch.mm,
        label: bestMatch.label,
        color: bestMatch.color,
        step,
        type,
        validText
      };
    } catch (e) {
      return null;
    }
  }

  /**
   * Obtiene la precipitación en mm instantáneamente (0ms) a partir del pixel en el canvas de NOAA GFS
   */
  _getInstantGfsPixel(lat, lng) {
    if (!this.layerManager || !this.layerManager.gfsCanvasData) return null;
    const { ctx, width, height, bounds, step, type, validText } = this.layerManager.gfsCanvasData;
    if (!bounds || bounds.length < 2) return null;

    const latMin = Math.min(bounds[0][0], bounds[1][0]);
    const latMax = Math.max(bounds[0][0], bounds[1][0]);
    const lonMin = Math.min(bounds[0][1], bounds[1][1]);
    const lonMax = Math.max(bounds[0][1], bounds[1][1]);

    if (lat < latMin || lat > latMax || lng < lonMin || lng > lonMax) return null;

    const yPt = Math.log(Math.tan(Math.PI / 4 + (lat * Math.PI / 360)));
    const yMin = Math.log(Math.tan(Math.PI / 4 + (latMin * Math.PI / 360)));
    const yMax = Math.log(Math.tan(Math.PI / 4 + (latMax * Math.PI / 360)));

    const x = Math.floor(((lng - lonMin) / (lonMax - lonMin)) * width);
    const y = Math.floor(((yMax - yPt) / (yMax - yMin)) * height);

    if (x < 0 || x >= width || y < 0 || y >= height) return null;

    try {
      const pixel = ctx.getImageData(x, y, 1, 1).data;
      const r = pixel[0], g = pixel[1], b = pixel[2], a = pixel[3];

      if (a < 30) {
        return { mm: 0.0, label: 'Sin precipitación (<0.1 mm)', color: '#94a3b8', step, type, validText };
      }

      const palette = [
        { minMm: 250, mm: 250, label: '> 250 mm (Extrema)', rgb: [255, 255, 255], color: '#ffffff' },
        { minMm: 150, mm: 180, label: '150 - 250 mm (Torrencial)', rgb: [217, 70, 239], color: '#d946ef' },
        { minMm: 100, mm: 120, label: '100 - 150 mm (Muy Fuerte)', rgb: [239, 68, 68], color: '#ef4444' },
        { minMm: 70, mm: 85, label: '70 - 100 mm (Muy Fuerte)', rgb: [249, 115, 22], color: '#f97316' },
        { minMm: 40, mm: 55, label: '40 - 70 mm (Fuerte)', rgb: [250, 204, 21], color: '#facc15' },
        { minMm: 20, mm: 30, label: '20 - 40 mm (Moderada)', rgb: [22, 163, 74], color: '#16a34a' },
        { minMm: 10, mm: 15, label: '10 - 20 mm (Moderada)', rgb: [74, 222, 128], color: '#4ade80' },
        { minMm: 3, mm: 6, label: '3 - 10 mm (Ligera)', rgb: [2, 132, 199], color: '#0284c7' },
        { minMm: 1, mm: 2, label: '1 - 3 mm (Débil)', rgb: [56, 189, 248], color: '#38bdf8' },
        { minMm: 0.1, mm: 0.5, label: '0.1 - 1 mm (Muy Débil)', rgb: [186, 230, 253], color: '#bae6fd' }
      ];

      let bestMatch = palette[palette.length - 1];
      let minDistance = Infinity;

      for (const p of palette) {
        const dr = r - p.rgb[0];
        const dg = g - p.rgb[1];
        const db = b - p.rgb[2];
        const dist = dr * dr + dg * dg + db * db;
        if (dist < minDistance) {
          minDistance = dist;
          bestMatch = p;
        }
      }

      return {
        mm: bestMatch.mm,
        label: bestMatch.label,
        color: bestMatch.color,
        step,
        type,
        validText
      };
    } catch (e) {
      return null;
    }
  }

  /**
   * Obtiene la precipitación en mm instantáneamente (0ms) a partir del pixel en el canvas de Météo-France / AEMET AROME
   */
  _getInstantAromePixel(lat, lng) {
    if (!this.layerManager || !this.layerManager.aromeCanvasData) return null;
    const { ctx, width, height, bounds, step, type, validText } = this.layerManager.aromeCanvasData;
    if (!bounds || bounds.length < 2) return null;

    const latMin = Math.min(bounds[0][0], bounds[1][0]);
    const latMax = Math.max(bounds[0][0], bounds[1][0]);
    const lonMin = Math.min(bounds[0][1], bounds[1][1]);
    const lonMax = Math.max(bounds[0][1], bounds[1][1]);

    if (lat < latMin || lat > latMax || lng < lonMin || lng > lonMax) return null;

    const yPt = Math.log(Math.tan(Math.PI / 4 + (lat * Math.PI / 360)));
    const yMin = Math.log(Math.tan(Math.PI / 4 + (latMin * Math.PI / 360)));
    const yMax = Math.log(Math.tan(Math.PI / 4 + (latMax * Math.PI / 360)));

    const x = Math.floor(((lng - lonMin) / (lonMax - lonMin)) * width);
    const y = Math.floor(((yMax - yPt) / (yMax - yMin)) * height);

    if (x < 0 || x >= width || y < 0 || y >= height) return null;

    try {
      const pixel = ctx.getImageData(x, y, 1, 1).data;
      const r = pixel[0], g = pixel[1], b = pixel[2], a = pixel[3];

      if (a < 30) {
        return { mm: 0.0, label: 'Sin precipitación (<0.1 mm)', color: '#94a3b8', step, type, validText };
      }

      const palette = [
        { minMm: 250, mm: 250, label: '> 250 mm (Extrema)', rgb: [255, 255, 255], color: '#ffffff' },
        { minMm: 150, mm: 180, label: '150 - 250 mm (Torrencial)', rgb: [217, 70, 239], color: '#d946ef' },
        { minMm: 100, mm: 120, label: '100 - 150 mm (Muy Fuerte)', rgb: [239, 68, 68], color: '#ef4444' },
        { minMm: 70, mm: 85, label: '70 - 100 mm (Muy Fuerte)', rgb: [249, 115, 22], color: '#f97316' },
        { minMm: 40, mm: 55, label: '40 - 70 mm (Fuerte)', rgb: [250, 204, 21], color: '#facc15' },
        { minMm: 20, mm: 30, label: '20 - 40 mm (Moderada)', rgb: [22, 163, 74], color: '#16a34a' },
        { minMm: 10, mm: 15, label: '10 - 20 mm (Moderada)', rgb: [74, 222, 128], color: '#4ade80' },
        { minMm: 3, mm: 6, label: '3 - 10 mm (Ligera)', rgb: [2, 132, 199], color: '#0284c7' },
        { minMm: 1, mm: 2, label: '1 - 3 mm (Débil)', rgb: [56, 189, 248], color: '#38bdf8' },
        { minMm: 0.1, mm: 0.5, label: '0.1 - 1 mm (Muy Débil)', rgb: [186, 230, 253], color: '#bae6fd' }
      ];

      let bestMatch = palette[palette.length - 1];
      let minDistance = Infinity;

      for (const p of palette) {
        const dr = r - p.rgb[0];
        const dg = g - p.rgb[1];
        const db = b - p.rgb[2];
        const dist = dr * dr + dg * dg + db * db;
        if (dist < minDistance) {
          minDistance = dist;
          bestMatch = p;
        }
      }

      return {
        mm: bestMatch.mm,
        label: bestMatch.label,
        color: bestMatch.color,
        step,
        type,
        validText
      };
    } catch (e) {
      return null;
    }
  }

  /**
   * Obtiene la precipitación en mm instantáneamente (0ms) a partir del pixel en el canvas de DWD ICON-EU
   */
  _getInstantIconPixel(lat, lng) {
    if (!this.layerManager || !this.layerManager.iconCanvasData) return null;
    const { ctx, width, height, bounds, step, type, validText } = this.layerManager.iconCanvasData;
    if (!bounds || bounds.length < 2) return null;

    const latMin = Math.min(bounds[0][0], bounds[1][0]);
    const latMax = Math.max(bounds[0][0], bounds[1][0]);
    const lonMin = Math.min(bounds[0][1], bounds[1][1]);
    const lonMax = Math.max(bounds[0][1], bounds[1][1]);

    if (lat < latMin || lat > latMax || lng < lonMin || lng > lonMax) return null;

    const yPt = Math.log(Math.tan(Math.PI / 4 + (lat * Math.PI / 360)));
    const yMin = Math.log(Math.tan(Math.PI / 4 + (latMin * Math.PI / 360)));
    const yMax = Math.log(Math.tan(Math.PI / 4 + (latMax * Math.PI / 360)));

    const x = Math.floor(((lng - lonMin) / (lonMax - lonMin)) * width);
    const y = Math.floor(((yMax - yPt) / (yMax - yMin)) * height);

    if (x < 0 || x >= width || y < 0 || y >= height) return null;

    try {
      const pixel = ctx.getImageData(x, y, 1, 1).data;
      const r = pixel[0], g = pixel[1], b = pixel[2], a = pixel[3];

      if (a < 30) {
        return { mm: 0.0, label: 'Sin precipitación (<0.1 mm)', color: '#94a3b8', step, type, validText };
      }

      const palette = [
        { minMm: 250, mm: 250, label: '> 250 mm (Extrema)', rgb: [255, 255, 255], color: '#ffffff' },
        { minMm: 150, mm: 180, label: '150 - 250 mm (Torrencial)', rgb: [217, 70, 239], color: '#d946ef' },
        { minMm: 100, mm: 120, label: '100 - 150 mm (Muy Fuerte)', rgb: [239, 68, 68], color: '#ef4444' },
        { minMm: 70, mm: 85, label: '70 - 100 mm (Muy Fuerte)', rgb: [249, 115, 22], color: '#f97316' },
        { minMm: 40, mm: 55, label: '40 - 70 mm (Fuerte)', rgb: [250, 204, 21], color: '#facc15' },
        { minMm: 20, mm: 30, label: '20 - 40 mm (Moderada)', rgb: [22, 163, 74], color: '#16a34a' },
        { minMm: 10, mm: 15, label: '10 - 20 mm (Moderada)', rgb: [74, 222, 128], color: '#4ade80' },
        { minMm: 3, mm: 6, label: '3 - 10 mm (Ligera)', rgb: [2, 132, 199], color: '#0284c7' },
        { minMm: 1, mm: 2, label: '1 - 3 mm (Débil)', rgb: [56, 189, 248], color: '#38bdf8' },
        { minMm: 0.1, mm: 0.5, label: '0.1 - 1 mm (Muy Débil)', rgb: [186, 230, 253], color: '#bae6fd' }
      ];

      let bestMatch = palette[palette.length - 1];
      let minDistance = Infinity;

      for (const p of palette) {
        const dr = r - p.rgb[0];
        const dg = g - p.rgb[1];
        const db = b - p.rgb[2];
        const dist = dr * dr + dg * dg + db * db;
        if (dist < minDistance) {
          minDistance = dist;
          bestMatch = p;
        }
      }

      return {
        mm: bestMatch.mm,
        label: bestMatch.label,
        color: bestMatch.color,
        step,
        type,
        validText
      };
    } catch (e) {
      return null;
    }
  }

  /**
   * Obtiene la precipitación en mm instantáneamente (0ms) a partir del pixel en el canvas de GEM-GDPS
   */
  _getInstantGemPixel(lat, lng) {
    if (!this.layerManager || !this.layerManager.gemCanvasData) return null;
    const { ctx, width, height, bounds, step, type, validText } = this.layerManager.gemCanvasData;
    if (!bounds || bounds.length < 2) return null;

    const latMin = Math.min(bounds[0][0], bounds[1][0]);
    const latMax = Math.max(bounds[0][0], bounds[1][0]);
    const lonMin = Math.min(bounds[0][1], bounds[1][1]);
    const lonMax = Math.max(bounds[0][1], bounds[1][1]);

    if (lat < latMin || lat > latMax || lng < lonMin || lng > lonMax) return null;

    const yPt = Math.log(Math.tan(Math.PI / 4 + (lat * Math.PI / 360)));
    const yMin = Math.log(Math.tan(Math.PI / 4 + (latMin * Math.PI / 360)));
    const yMax = Math.log(Math.tan(Math.PI / 4 + (latMax * Math.PI / 360)));

    const x = Math.floor(((lng - lonMin) / (lonMax - lonMin)) * width);
    const y = Math.floor(((yMax - yPt) / (yMax - yMin)) * height);

    if (x < 0 || x >= width || y < 0 || y >= height) return null;

    try {
      const pixel = ctx.getImageData(x, y, 1, 1).data;
      const r = pixel[0], g = pixel[1], b = pixel[2], a = pixel[3];

      if (a < 30) {
        return { mm: 0.0, label: 'Sin precipitación (<0.1 mm)', color: '#94a3b8', step, type, validText };
      }

      const palette = [
        { minMm: 250, mm: 250, label: '> 250 mm (Extrema)', rgb: [255, 255, 255], color: '#ffffff' },
        { minMm: 150, mm: 180, label: '150 - 250 mm (Torrencial)', rgb: [217, 70, 239], color: '#d946ef' },
        { minMm: 100, mm: 120, label: '100 - 150 mm (Muy Fuerte)', rgb: [239, 68, 68], color: '#ef4444' },
        { minMm: 70, mm: 85, label: '70 - 100 mm (Muy Fuerte)', rgb: [249, 115, 22], color: '#f97316' },
        { minMm: 40, mm: 55, label: '40 - 70 mm (Fuerte)', rgb: [250, 204, 21], color: '#facc15' },
        { minMm: 20, mm: 30, label: '20 - 40 mm (Moderada)', rgb: [22, 163, 74], color: '#16a34a' },
        { minMm: 10, mm: 15, label: '10 - 20 mm (Moderada)', rgb: [74, 222, 128], color: '#4ade80' },
        { minMm: 3, mm: 6, label: '3 - 10 mm (Ligera)', rgb: [2, 132, 199], color: '#0284c7' },
        { minMm: 1, mm: 2, label: '1 - 3 mm (Débil)', rgb: [56, 189, 248], color: '#38bdf8' },
        { minMm: 0.1, mm: 0.5, label: '0.1 - 1 mm (Muy Débil)', rgb: [186, 230, 253], color: '#bae6fd' }
      ];

      let bestMatch = palette[palette.length - 1];
      let minDistance = Infinity;

      for (const p of palette) {
        const dr = r - p.rgb[0];
        const dg = g - p.rgb[1];
        const db = b - p.rgb[2];
        const dist = dr * dr + dg * dg + db * db;
        if (dist < minDistance) {
          minDistance = dist;
          bestMatch = p;
        }
      }

      return {
        mm: bestMatch.mm,
        label: bestMatch.label,
        color: bestMatch.color,
        step,
        type,
        validText
      };
    } catch (e) {
      return null;
    }
  }

  _debouncedFetchDbz(lat, lng, mode, stationId) {
    if (this._dbzDebounceTimer) clearTimeout(this._dbzDebounceTimer);
    this._dbzDebounceTimer = setTimeout(async () => {
      try {
        const url = `${CONFIG.apiBaseUrl}/radar/value-at?lat=${lat.toFixed(4)}&lon=${lng.toFixed(4)}&mode=${mode}&station_id=${stationId || ''}`;
        const resp = await fetch(url);
        if (resp.ok) {
          const data = await resp.json();
          if (data.dbz !== null && data.dbz !== undefined) {
            this._lastRadarLookup = { lat, lng, dbz: data.dbz, rain_intensity: data.rain_intensity };
            // Forzar refresco inmediato si el cursor sigue en la misma celda (< 0.005° ~ 500m)
            if (this._lastLatLng && Math.abs(this._lastLatLng.lat - lat) < 0.005 && Math.abs(this._lastLatLng.lng - lng) < 0.005) {
              this._inspectPoint(this._lastLatLng);
            }
          }
        }
      } catch (e) {
        // Silencioso en caso de error de red
      }
    }, 40);
  }

  _getModelCacheKey(modelKey, lat, lng, step, type) {
    const snap = (modelKey === 'arome') ? 0.01 : ((modelKey === 'icon' || modelKey === 'gem') ? 0.02 : 0.04);
    const sLat = (Math.round(lat / snap) * snap).toFixed(3);
    const sLng = (Math.round(lng / snap) * snap).toFixed(3);
    return `${modelKey}_${step}_${type}_${sLat}_${sLng}`;
  }

  async _fetchModelValue(modelKey, lat, lng, step, type, cacheKey, fallbackMm) {
    if (this._modelRequestsInFlight.has(cacheKey)) return;
    this._modelRequestsInFlight.add(cacheKey);

    try {
      const url = `${CONFIG.apiBaseUrl}/models/${modelKey}/value-at?lat=${lat.toFixed(4)}&lon=${lng.toFixed(4)}&step=${step}&type=${type}`;
      const resp = await fetch(url);
      if (resp.ok) {
        const data = await resp.json();
        const val = (data && data.value_mm !== null && data.value_mm !== undefined)
          ? Number(data.value_mm)
          : (fallbackMm !== undefined ? fallbackMm : 0.0);
        this._modelValuesCache.set(cacheKey, val);
      } else {
        this._modelValuesCache.set(cacheKey, fallbackMm !== undefined ? fallbackMm : 0.0);
      }
    } catch (e) {
      this._modelValuesCache.set(cacheKey, fallbackMm !== undefined ? fallbackMm : 0.0);
    } finally {
      this._modelRequestsInFlight.delete(cacheKey);
      if (this._lastLatLng) {
        this._inspectPoint(this._lastLatLng);
      }
    }
  }

  _getModelIntensityMeta(mm) {
    if (mm === null || mm === undefined || isNaN(mm)) return { label: 'Sin datos', color: '#94a3b8' };
    if (mm >= 250) return { label: '> 250 mm (Extrema)', color: '#ffffff' };
    if (mm >= 150) return { label: '150 - 250 mm (Torrencial)', color: '#d946ef' };
    if (mm >= 100) return { label: '100 - 150 mm (Muy Fuerte)', color: '#ef4444' };
    if (mm >= 70) return { label: '70 - 100 mm (Muy Fuerte)', color: '#f97316' };
    if (mm >= 40) return { label: '40 - 70 mm (Fuerte)', color: '#facc15' };
    if (mm >= 20) return { label: '20 - 40 mm (Moderada)', color: '#16a34a' };
    if (mm >= 10) return { label: '10 - 20 mm (Moderada)', color: '#4ade80' };
    if (mm >= 3) return { label: '3 - 10 mm (Ligera)', color: '#0284c7' };
    if (mm >= 1) return { label: '1 - 3 mm (Débil)', color: '#38bdf8' };
    if (mm >= 0.1) return { label: '0.1 - 1 mm (Muy Débil)', color: '#bae6fd' };
    return { label: 'Sin precipitación (<0.1 mm)', color: '#94a3b8' };
  }





  _isTouchDevice() {
    return (
      window.matchMedia('(pointer: coarse)').matches ||
      ('ontouchstart' in window) ||
      (navigator.maxTouchPoints > 0)
    );
  }

  init() {
    if (!this.map) return;

    // Escuchar movimiento del cursor global sobre el mapa (Escritorio / Ratón)
    this.map.on("mousemove", (e) => this._onMouseMove(e));
    this.map.on("mouseout", (e) => this._onMouseOut(e));

    // Escuchar toque / click sobre el mapa para modo interactivo persistente (Móvil, iPad, Tablets, PC)
    this.map.on("click", (e) => this._onMapClick(e));

    // Inicializar eventos de la tarjeta móvil / tablet inferior
    this._initMobileSheetEvents();
  }

  setCuencasLayer(cuencasLayer) {
    this.cuencasLayer = cuencasLayer;
  }

  setLayerManager(layerManager) {
    this.layerManager = layerManager;
  }

  _onMouseMove(e) {
    // Si el evento proviene de un toque en pantalla táctil (iPad/tablet/móvil), ignorar hover sintético
    if (e.originalEvent && (e.originalEvent.pointerType === 'touch' || e.originalEvent.touches)) {
      return;
    }
    if (this._isTouchDevice() && (!e.originalEvent || e.originalEvent.pointerType !== 'mouse')) {
      return;
    }

    this._lastLatLng = e.latlng;

    // Throttle ligero (16ms ~ 60fps) para máxima suavidad sin sobrecargar la CPU
    if (this._hoverThrottle) return;
    this._hoverThrottle = requestAnimationFrame(() => {
      this._inspectPoint(this._lastLatLng, false);
      this._hoverThrottle = null;
    });
  }

  _onMouseOut(e) {
    // Si el evento proviene de touch o es un dispositivo táctil, no cerrar abruptamente
    if (e && e.originalEvent && (e.originalEvent.pointerType === 'touch' || e.originalEvent.touches)) {
      return;
    }
    if (this._isTouchDevice() && (!e || !e.originalEvent || e.originalEvent.pointerType !== 'mouse')) {
      return;
    }

    this._closeTooltip();
    if (!this.inspectionMarker) {
      if (this.uiManager) {
        this.uiManager.resetHoverInfo();
      }
      this._clearCuencaHover();
    }
  }

  _onMapClick(e) {
    if (!e || !e.latlng) return;

    // Si se hizo click en un marcador interactivo o elemento con su propio modal, ignorar
    if (e.originalEvent && (e.originalEvent._caudalMarkerClicked || e.originalEvent._embalseMarkerClicked || e.originalEvent._stopInspector || e.originalEvent._stopBasinClick)) {
      return;
    }
    if (e.originalEvent && e.originalEvent.target) {
      const el = e.originalEvent.target;
      if (
        (el.classList && (el.classList.contains('caudal-marker') || el.classList.contains('embalse-marker') || el.classList.contains('saih-lluvia-marker'))) ||
        (el.closest && (el.closest('.caudal-marker') || el.closest('.embalse-marker') || el.closest('.saih-lluvia-marker') || el.closest('.mobile-inspector-sheet') || el.closest('.leaflet-popup')))
      ) {
        return;
      }
    }

    // Inspección puntual persistente con marcador y tarjeta
    this.inspectAtLatLng(e.latlng, true);
  }

  inspectAtLatLng(latlng, isExplicit = false) {
    if (!latlng) return;
    this._inspectPoint(latlng, isExplicit);
  }

  /**
   * Realiza la intersección geométrica del punto con todas las capas activas
   */
  _inspectPoint(latlng, isExplicit = false) {
    if (!latlng || !this.map) return;

    const lat = latlng.lat;
    const lng = latlng.lng;
    const sections = [];

    // 1. Inspeccionar Capa de Cuencas (si está visible)
    let cuencaFound = null;
    if (this.cuencasLayer && this.cuencasLayer.isVisible && this.cuencasLayer.geoJsonLayer) {
      this.cuencasLayer.geoJsonLayer.eachLayer((layer) => {
        if (cuencaFound) return;
        const feature = layer.feature;
        if (feature && feature.geometry && this._isPointInGeometry(lat, lng, feature.geometry)) {
          cuencaFound = { feature, layer };
        }
      });
    }

    if (cuencaFound) {
      const props = cuencaFound.feature.properties || {};
      const sistema = props.NomSistExp || "Demarcación CHJ";
      const subsistema = props.Subsistema || `Subsistema ${cuencaFound.feature.id || ""}`;
      const color = CONFIG.systemColors[sistema] || CONFIG.systemColors["Default"] || "#38bdf8";
      const rawSuperf = props["Superf km2"] || props["Area km2"] || props.Superficie;
      const superfText = rawSuperf ? `${Number(rawSuperf).toLocaleString("es-ES", { maximumFractionDigits: 1 })} km²` : "";
      // Si hay un modelo de predicción activo en el mapa, consultar volumen previsto en cuenca
      const activeModel = this.layerManager ? this.layerManager.getActivePredictionModel() : null;
      const isModelOnMap = this.layerManager && (this.layerManager.isLayerOnMap("ecmwf_ifs") || this.layerManager.isLayerOnMap("gfs_0p25") || this.layerManager.isLayerOnMap("arome_precip"));
      
      let hydroDetails = null;
      if (isModelOnMap && activeModel && this.layerManager) {
        const shortModelNames = {
          ecmwf: 'ECMWF IFS',
          gfs: 'NOAA GFS',
          arome: 'AROME'
        };
        const shortModel = shortModelNames[activeModel] || activeModel.toUpperCase();
        const cacheKey = `${activeModel}_${cuencaFound.feature.id}`;
        if (this.layerManager._basinHydroCache && this.layerManager._basinHydroCache.has(cacheKey)) {
          const hData = this.layerManager._basinHydroCache.get(cacheKey);
          if (hData) {
            hydroDetails = `
              <div style="display: flex; justify-content: space-between; align-items: center; margin-top: 4px; background: rgba(0,0,0,0.3); padding: 4px 8px; border-radius: 6px;">
                <span style="color: #94a3b8; font-size: 0.72rem;">Volumen previsto (${shortModel}):</span>
                <span style="font-weight: 700; color: #38bdf8; font-size: 0.82rem;">${hData.total_accumulated_hm3.toFixed(2)} <span style="font-size: 0.68rem; font-weight: 400; color: #94a3b8;">hm³</span></span>
              </div>
            `;
          }
        } else {
          // Precalentar caché en background
          this.layerManager.fetchBasinHydrograph(activeModel, cuencaFound.feature.id);
        }
      }

      let detailsContent = superfText ? `Superficie: ${superfText}` : '';
      if (hydroDetails) {
        detailsContent = detailsContent ? `${detailsContent}${hydroDetails}` : hydroDetails;
      }

      sections.push({
        type: "cuenca",
        title: "Cuenca Hidrográfica",
        headerColor: color,
        icon: "🌊",
        name: subsistema,
        badge: sistema,
        badgeBg: color,
        details: detailsContent,
        basinId: cuencaFound.feature.id,
        basinProps: props,
        featureLayer: cuencaFound.layer
      });

      // Actualizar estilo hover de la cuenca
      this._applyCuencaHover(cuencaFound.layer, color);

      // Notificar al HUD inferior
      if (this.uiManager) {
        this.uiManager.updateHoverInfo(props);
      }
    } else {
      this._clearCuencaHover();
      if (this.uiManager) {
        this.uiManager.resetHoverInfo();
      }
    }

    // 2. Inspeccionar Capas Temáticas Activas en el Mapa (AEMET, Radar, SAIH, Modelos)
    if (this.layerManager) {
      // 2.1 Avisos AEMET
      if (this.layerManager.isLayerOnMap("aemet_warnings")) {
        const aemetGroup = this.layerManager.layers["aemet_warnings"];
        if (aemetGroup) {
          const matchingWarnings = [];
          aemetGroup.eachLayer((child) => {
            // child puede ser L.GeoJSON
            if (child.eachLayer) {
              child.eachLayer((subLayer) => {
                const feat = subLayer.feature;
                if (feat && feat.geometry && this._isPointInGeometry(lat, lng, feat.geometry)) {
                  matchingWarnings.push(feat.properties);
                }
              });
            }
          });

          if (matchingWarnings.length > 0) {
            // Desempaquetar avisos individuales si venían consolidados
            const allIndividualWarnings = [];
            matchingWarnings.forEach((prop) => {
              if (Array.isArray(prop.combined_warnings) && prop.combined_warnings.length > 0) {
                prop.combined_warnings.forEach((subW) => {
                  allIndividualWarnings.push({
                    ...subW,
                    area_desc: subW.area_desc || prop.area_desc || ""
                  });
                });
              } else {
                allIndividualWarnings.push(prop);
              }
            });

            // Deduplicar avisos idénticos conservando distintos acumulados (ej: 1h vs 12h)
            const seenKeys = new Set();
            const uniqueWarnings = [];
            for (const w of allIndividualWarnings) {
              const key = `${w.identifier || ''}|${w.event || ''}|${w.description || ''}|${w.area_desc || ''}|${w.onset || ''}|${w.expires || ''}`;
              if (!seenKeys.has(key)) {
                seenKeys.add(key);
                uniqueWarnings.push(w);
              }
            }

            // Ordenar por severidad (Rojo > Naranja > Amarillo > Verde)
            const severityOrder = {
              'extreme': 4, 'red': 4, 'rojo': 4,
              'severe': 3, 'orange': 3, 'naranja': 3,
              'moderate': 2, 'yellow': 2, 'amarillo': 2,
              'minor': 1, 'green': 1, 'verde': 1
            };
            uniqueWarnings.sort((a, b) => {
              const rankA = severityOrder[(a.severity || '').toLowerCase()] || 0;
              const rankB = severityOrder[(b.severity || '').toLowerCase()] || 0;
              return rankB - rankA;
            });

            const currentPeriod = this.layerManager.currentAemetPeriod || 'now';
            let periodLabel = "Activo";
            if (currentPeriod === 'tomorrow') {
              periodLabel = "Mañana";
            } else if (currentPeriod === 'after_tomorrow') {
              periodLabel = "Pasado";
            }

            uniqueWarnings.forEach((w) => {
              const event = w.event || w.headline || "Aviso Meteorológico";
              const sev = w.severity || "Aviso";
              const color = w.color || "#f59e0b";
              const area = w.area_desc || "";
              const desc = (w.description || "").trim();
              const prob = (w.probability || "").trim();

              // Formateo horario inteligente (Inicio - Fin)
              let timeDetail = "";
              if (w.onset || w.expires) {
                const onsetDate = w.onset ? new Date(w.onset) : null;
                const expDate = w.expires ? new Date(w.expires) : null;
                
                const fmtTime = (d) => {
                  try {
                    return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
                  } catch (e) {
                    return "";
                  }
                };
                const fmtDate = (d) => {
                  try {
                    return d.toLocaleDateString([], { day: "2-digit", month: "2-digit" });
                  } catch (e) {
                    return "";
                  }
                };

                if (onsetDate && expDate && !isNaN(onsetDate.getTime()) && !isNaN(expDate.getTime())) {
                  const sameDay = onsetDate.toDateString() === expDate.toDateString();
                  if (sameDay) {
                    timeDetail = `${fmtTime(onsetDate)} - ${fmtTime(expDate)}`;
                  } else {
                    timeDetail = `${fmtDate(onsetDate)} ${fmtTime(onsetDate)} → ${fmtDate(expDate)} ${fmtTime(expDate)}`;
                  }
                } else if (expDate && !isNaN(expDate.getTime())) {
                  timeDetail = `Hasta ${fmtTime(expDate)}`;
                }
              }

              // Normalizar etiqueta de severidad
              let sevLabel = sev;
              const sevLower = (sev || '').toLowerCase();
              if (sevLower === 'extreme' || sevLower === 'red' || sevLower === 'rojo') {
                sevLabel = 'Nivel Rojo';
              } else if (sevLower === 'severe' || sevLower === 'orange' || sevLower === 'naranja') {
                sevLabel = 'Nivel Naranja';
              } else if (sevLower === 'moderate' || sevLower === 'yellow' || sevLower === 'amarillo') {
                sevLabel = 'Nivel Amarillo';
              } else if (sevLower === 'minor' || sevLower === 'green' || sevLower === 'verde') {
                sevLabel = 'Nivel Verde';
              }

              const isBrightBadge = ['#facc15', '#f59e0b', '#fb923c', '#ffffff', '#22c55e'].includes((color || '').toLowerCase());
              const badgeTextColor = isBrightBadge ? '#0f172a' : '#ffffff';

              // Construir bloque de detalles enriquecido
              let detailsHtml = `
                <div style="display:flex; flex-direction:column; gap:3px; margin-top:2px;">
                  <div style="display:flex; justify-content:space-between; align-items:center; gap:6px; font-size:0.72rem; color:#cbd5e1;">
                    <span>📍 <strong>${area}</strong></span>
                    ${timeDetail ? `<span style="color:#94a3b8; font-size:0.68rem; font-weight:600; background:rgba(255,255,255,0.06); padding:1px 5px; border-radius:3px;">🕒 ${timeDetail}</span>` : ""}
                  </div>
              `;

              if (desc) {
                detailsHtml += `
                  <div style="background: rgba(0,0,0,0.38); border-left: 3px solid ${color}; padding: 4px 7px; border-radius: 4px; font-size: 0.74rem; color: #f8fafc; line-height: 1.35; margin-top: 2px;">
                    ${desc}
                  </div>
                `;
              }

              if (prob) {
                detailsHtml += `
                  <div style="display:flex; justify-content:space-between; align-items:center; font-size:0.69rem; color:#94a3b8; margin-top:1px; padding:0 1px;">
                    <span>Probabilidad del fenómeno:</span>
                    <span style="font-weight:700; color:#38bdf8;">${prob}</span>
                  </div>
                `;
              }

              detailsHtml += `</div>`;

              sections.push({
                type: "warning",
                title: `AEMET Meteoalerta (${periodLabel})`,
                headerColor: color,
                icon: "⚠️",
                name: event,
                badge: sevLabel,
                badgeBg: color,
                badgeColor: badgeTextColor,
                details: detailsHtml
              });
            });
          }
        }
      }

      // 2.2 Radar Meteorológico
      if (this.layerManager.isLayerOnMap("radar")) {
        const bounds = this.layerManager.currentRadarBounds;

        let inRadarArea = false;
        if (bounds) {
          const latMin = Math.min(bounds[0][0], bounds[1][0]);
          const latMax = Math.max(bounds[0][0], bounds[1][0]);
          const lonMin = Math.min(bounds[0][1], bounds[1][1]);
          const lonMax = Math.max(bounds[0][1], bounds[1][1]);
          if (lat >= latMin && lat <= latMax && lng >= lonMin && lng <= lonMax) {
            inRadarArea = true;
          }
        }

        if (inRadarArea) {
          // 1. Detección instantánea en el cliente desde el raster canvas (0ms de latencia)
          // Esto garantiza que el valor y color mostrados coinciden exactamente con el píxel visual bajo el cursor
          const instantPixel = this._getInstantRadarPixel(lat, lng);

          // 2. Comprobar si hay valor float de alta precisión en caché para la misma celda de cuadrícula
          // (tolerancia fina < 0.005° ~ 500m para evitar usar valores de celdas adyacentes)
          const cached = this._lastRadarLookup &&
            Math.abs(this._lastRadarLookup.lat - lat) < 0.005 &&
            Math.abs(this._lastRadarLookup.lng - lng) < 0.005;

          if (instantPixel || cached) {
            const dbzVal = (cached && this._lastRadarLookup.dbz !== null && this._lastRadarLookup.dbz !== undefined)
              ? this._lastRadarLookup.dbz
              : (instantPixel ? instantPixel.dbz : 0);

            const label = (cached && this._lastRadarLookup.rain_intensity)
              ? this._lastRadarLookup.rain_intensity
              : (instantPixel ? instantPixel.rain_intensity : 'Sin lluvia');

            const badgeBg = instantPixel ? instantPixel.badgeColor : '#38bdf8';

            let stMode = 'Compuesto Mixto (Corto 0.5º + Largo)';
            if (this.layerManager.currentRadarMode === 'short_range') {
              stMode = 'Corto Alcance 0.5º (Doppler ≤145km)';
            } else if (this.layerManager.currentRadarMode === 'long_range') {
              stMode = 'Largo Alcance (OPERA / 250km)';
            } else if (this.layerManager.currentRadarMode === 'single' && this.layerManager.currentRadarStationId) {
              stMode = `Estación: ${CONFIG.radarStations[this.layerManager.currentRadarStationId]?.name || this.layerManager.currentRadarStationId} (0.5º)`;
            }

            sections.push({
              type: "radar",
              title: "Radar Meteorológico (AEMET/OPERA)",
              headerColor: "#0284c7",
              icon: "📡",
              name: `Intensidad: ${dbzVal.toFixed(1)} dBZ`,
              badge: `${dbzVal.toFixed(0)} dBZ`,
              badgeBg: badgeBg,
              badgeColor: "#ffffff",
              details: `
                <div style="font-size: 0.76rem; color: #f8fafc; font-weight: 600;">
                  <span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:${badgeBg};margin-right:4px;"></span>
                  ${label}
                </div>
                <div style="font-size: 0.70rem; color: #94a3b8; margin-top: 2px;">Producto: ${stMode}</div>
              `
            });

            // Disparar consulta de calibración fina con debounce si no está en cache
            if (!cached) {
              this._debouncedFetchDbz(lat, lng, "composite", "");
            }
          }
        }
      }

      // 2.3 Caudales en Ríos (SAIH Júcar)
      if (this.layerManager.isLayerOnMap("saih_caudales")) {
        const caudalesGroup = this.layerManager.layers["saih_caudales"];
        if (caudalesGroup) {
          let closestStation = null;
          let minPixDist = this._isTouchDevice() ? 32 : 20; // Tolerancia ampliada en táctil (móviles, iPad, tablets)

          caudalesGroup.eachLayer((child) => {
            const checkLayer = (l) => {
              if (l.getLatLng && l.feature && l.feature.properties) {
                const ptPix = this.map.latLngToContainerPoint(l.getLatLng());
                const mousePix = this.map.latLngToContainerPoint(latlng);
                const pixDist = Math.hypot(ptPix.x - mousePix.x, ptPix.y - mousePix.y);
                if (pixDist <= minPixDist) {
                  minPixDist = pixDist;
                  closestStation = l.feature.properties;
                }
              }
            };

            if (child.eachLayer) {
              child.eachLayer(checkLayer);
            } else {
              checkLayer(child);
            }
          });

          if (closestStation) {
            const rawCaudal = closestStation.ultimo_caudal !== undefined ? closestStation.ultimo_caudal : (closestStation.caudal !== undefined ? closestStation.caudal : closestStation.lastValue);
            const caudal = (rawCaudal !== null && rawCaudal !== undefined && rawCaudal !== '' && !isNaN(Number(rawCaudal))) ? Number(rawCaudal) : null;
            const umbrales = closestStation.umbrales || {};
            const uAmarillo = umbrales.amarillo ? Number(umbrales.amarillo) : null;
            const uNaranja = umbrales.naranja ? Number(umbrales.naranja) : null;
            const uRojo = umbrales.rojo ? Number(umbrales.rojo) : null;

            let badgeBg = "#10b981";
            let alertLevel = "Normal";
            if (caudal !== null) {
              if (uRojo !== null && caudal >= uRojo) {
                badgeBg = "#ef4444";
                alertLevel = "🔴 Umbral Rojo";
              } else if (uNaranja !== null && caudal >= uNaranja) {
                badgeBg = "#f97316";
                alertLevel = "🟠 Umbral Naranja";
              } else if (uAmarillo !== null && caudal >= uAmarillo) {
                badgeBg = "#f59e0b";
                alertLevel = "🟡 Umbral Amarillo";
              }
            } else {
              badgeBg = "#94a3b8";
              alertLevel = "Sin dato";
            }

            const caudalText = caudal !== null ? `${caudal.toFixed(2)} m³/s` : "-- m³/s";
            const horaText = closestStation.ultima_hora ? `· ${closestStation.ultima_hora}` : '';
            const thList = [];
            if (uAmarillo) thList.push(`🟡 ${uAmarillo}`);
            if (uNaranja) thList.push(`🟠 ${uNaranja}`);
            if (uRojo) thList.push(`🔴 ${uRojo}`);
            const thText = thList.length > 0 ? thList.join(' · ') + ' m³/s' : 'En estudio';
            const metaLoc = `${closestStation.poblacion || '--'} (${closestStation.provincia || ''}) · ${closestStation.subcuenca || ''}`;

            sections.push({
              type: "caudal",
              title: "Caudal en Río (SAIH)",
              headerColor: "#0284c7",
              icon: "💧",
              name: `${closestStation.nombre}`,
              badge: `${alertLevel}`,
              badgeBg: badgeBg,
              badgeColor: "#ffffff",
              station: closestStation,
              details: `
                <div class="unified-caudal-block">
                  <div style="color:#cbd5e1; font-size:0.75rem;">${closestStation.variable || 'Caudal'}</div>
                  <div style="margin-top:2px; font-size:0.82rem;">Caudal: <strong style="color:${badgeBg}; font-size:1.08em;">${caudalText}</strong> <span style="color:#94a3b8; font-size:0.72rem;">${horaText}</span></div>
                  <div style="font-size:0.72rem; color:#cbd5e1; margin-top:2px;">Umbrales: <span>${thText}</span></div>
                  <div style="font-size:0.70rem; color:#94a3b8; margin-top:2px; border-top:1px solid rgba(255,255,255,0.08); padding-top:2px;">📍 ${metaLoc} · Cód: ${closestStation.codigo || '--'}</div>
                </div>
              `
            });
          }
        }
      }

      // 2.4 Embalses y Presas (SAIH Júcar)
      if (this.layerManager.isLayerOnMap("saih_embalses")) {
        const embalsesGroup = this.layerManager.layers["saih_embalses"];
        if (embalsesGroup) {
          let closestEmbalse = null;
          let minPixDist = this._isTouchDevice() ? 36 : 24; // Tolerancia más amplia para embalses (iconos más grandes)

          embalsesGroup.eachLayer((child) => {
            const checkLayer = (l) => {
              if (l.getLatLng && l.feature && l.feature.properties) {
                const ptPix = this.map.latLngToContainerPoint(l.getLatLng());
                const mousePix = this.map.latLngToContainerPoint(latlng);
                const pixDist = Math.hypot(ptPix.x - mousePix.x, ptPix.y - mousePix.y);
                if (pixDist <= minPixDist) {
                  minPixDist = pixDist;
                  closestEmbalse = l.feature.properties;
                }
              }
            };

            if (child.eachLayer) {
              child.eachLayer(checkLayer);
            } else {
              checkLayer(child);
            }
          });

          if (closestEmbalse) {
            const vol = closestEmbalse.volumen_actual !== undefined && closestEmbalse.volumen_actual !== null
              ? Number(closestEmbalse.volumen_actual)
              : (closestEmbalse.volumen_actual_hm3 !== undefined && closestEmbalse.volumen_actual_hm3 !== null
                  ? Number(closestEmbalse.volumen_actual_hm3)
                  : (closestEmbalse.volumen !== undefined && closestEmbalse.volumen !== null
                      ? Number(closestEmbalse.volumen)
                      : null));

            const cap = closestEmbalse.capacidad_nmn !== undefined && closestEmbalse.capacidad_nmn !== null
              ? Number(closestEmbalse.capacidad_nmn)
              : (closestEmbalse.capacidad_total_hm3 !== undefined && closestEmbalse.capacidad_total_hm3 !== null
                  ? Number(closestEmbalse.capacidad_total_hm3)
                  : (closestEmbalse.capacidad !== undefined && closestEmbalse.capacidad !== null
                      ? Number(closestEmbalse.capacidad)
                      : null));

            const pct = closestEmbalse.porcentaje_llenado !== undefined && closestEmbalse.porcentaje_llenado !== null
              ? Number(closestEmbalse.porcentaje_llenado)
              : (closestEmbalse.porcentaje !== undefined && closestEmbalse.porcentaje !== null
                  ? Number(closestEmbalse.porcentaje)
                  : (vol !== null && cap ? (vol / cap * 100) : null));

            const cota = closestEmbalse.cota_actual !== undefined && closestEmbalse.cota_actual !== null ? Number(closestEmbalse.cota_actual) : null;
            const qIn = closestEmbalse.caudal_recibido !== undefined && closestEmbalse.caudal_recibido !== null ? Number(closestEmbalse.caudal_recibido) : null;
            const qOut = closestEmbalse.caudal_salida !== undefined && closestEmbalse.caudal_salida !== null ? Number(closestEmbalse.caudal_salida) : null;

            let pctColor = "#10b981";
            if (pct !== null) {
              if (pct >= 70) pctColor = "#ef4444";
              else if (pct >= 35) pctColor = "#f59e0b";
              else pctColor = "#10b981";
            }

            const volText = vol !== null ? `${vol.toFixed(2)} hm³` : "-- hm³";
            const capText = cap !== null ? `${cap.toFixed(2)} hm³` : "-- hm³";
            const pctText = pct !== null ? `${pct.toFixed(1)}%` : "--%";
            const horaText = closestEmbalse.ultima_hora ? `· ${String(closestEmbalse.ultima_hora).replace('T', ' ').substring(0, 16)}` : '';
            const cotaText = cota !== null ? `Cota: ${cota.toFixed(2)} m` : '';
            const metaLoc = `${closestEmbalse.poblacion || '--'} (${closestEmbalse.provincia || ''}) · ${closestEmbalse.subcuenca || ''}`;

            sections.push({
              type: "embalse",
              title: "Embalse / Presa (SAIH)",
              headerColor: "#6366f1",
              icon: "🏞️",
              name: `${closestEmbalse.nombre}`,
              badge: `${pctText}`,
              badgeBg: pctColor,
              badgeColor: "#ffffff",
              station: closestEmbalse,
              details: `
                <div class="unified-embalse-block">
                  <div style="display:flex; justify-content:space-between; align-items:center; margin-top:2px;">
                    <div>Volumen: <strong style="color:#e2e8f0; font-size:1.05em;">${volText}</strong> <span style="color:#94a3b8; font-size:0.72rem;">/ ${capText}</span></div>
                    <div style="font-weight:700; color:${pctColor}; font-size:0.95rem;">${pctText}</div>
                  </div>
                  ${cotaText || qIn !== null || qOut !== null ? `
                    <div style="font-size:0.72rem; color:#94a3b8; margin-top:2px; display:flex; justify-content:space-between;">
                      <span>${cotaText}</span>
                      ${qIn !== null || qOut !== null ? `<span>Entrada: ${qIn !== null ? qIn.toFixed(2) : '--'} | Salida: ${qOut !== null ? qOut.toFixed(2) : '--'} m³/s</span>` : ''}
                    </div>
                  ` : ''}
                  <div style="font-size:0.70rem; color:#94a3b8; margin-top:2px; border-top:1px solid rgba(255,255,255,0.08); padding-top:2px;">📍 ${metaLoc} · Cód: ${closestEmbalse.codigo || '--'} ${horaText}</div>
                </div>
              `
            });
          }
        }
      }

      // 2.5 Pluviómetros / Lluvia Acumulada (SAIH Júcar)
      if (this.layerManager.isLayerOnMap("saih_lluvias")) {
        const lluviasGroup = this.layerManager.layers["saih_lluvias"];
        if (lluviasGroup) {
          let closestPluvio = null;
          let minPixDist = this._isTouchDevice() ? 28 : 18; // Tolerancia fina para pluviómetros

          lluviasGroup.eachLayer((child) => {
            const checkLayer = (l) => {
              if (l.getLatLng && l.feature && l.feature.properties) {
                const ptPix = this.map.latLngToContainerPoint(l.getLatLng());
                const mousePix = this.map.latLngToContainerPoint(latlng);
                const pixDist = Math.hypot(ptPix.x - mousePix.x, ptPix.y - mousePix.y);
                if (pixDist <= minPixDist) {
                  minPixDist = pixDist;
                  closestPluvio = l.feature.properties;
                }
              }
            };

            if (child.eachLayer) {
              child.eachLayer(checkLayer);
            } else {
              checkLayer(child);
            }
          });

          if (closestPluvio) {
            const r1h = closestPluvio.lluvia_1h !== undefined && closestPluvio.lluvia_1h !== null
              ? Number(closestPluvio.lluvia_1h)
              : (closestPluvio.precipitacion_1h !== undefined && closestPluvio.precipitacion_1h !== null ? Number(closestPluvio.precipitacion_1h) : 0);
            const r4h = closestPluvio.lluvia_4h !== undefined && closestPluvio.lluvia_4h !== null
              ? Number(closestPluvio.lluvia_4h)
              : (closestPluvio.precipitacion_4h !== undefined && closestPluvio.precipitacion_4h !== null ? Number(closestPluvio.precipitacion_4h) : 0);
            const r12h = closestPluvio.lluvia_12h !== undefined && closestPluvio.lluvia_12h !== null
              ? Number(closestPluvio.lluvia_12h)
              : (closestPluvio.precipitacion_12h !== undefined && closestPluvio.precipitacion_12h !== null ? Number(closestPluvio.precipitacion_12h) : 0);
            const r24h = closestPluvio.lluvia_24h !== undefined && closestPluvio.lluvia_24h !== null
              ? Number(closestPluvio.lluvia_24h)
              : (closestPluvio.precipitacion_24h !== undefined && closestPluvio.precipitacion_24h !== null ? Number(closestPluvio.precipitacion_24h) : 0);

            let badgeBg = "#38bdf8";
            let badgeText = `${r24h > 0 ? `${r24h.toFixed(1)} mm (24h)` : (r1h > 0 ? `${r1h.toFixed(1)} mm (1h)` : '0.0 mm')}`;
            if (r24h >= 100 || r1h >= 20) {
              badgeBg = "#ef4444";
              badgeText = `🔴 ${r24h >= 100 ? `${r24h.toFixed(1)} mm (24h)` : `${r1h.toFixed(1)} mm (1h)`}`;
            } else if (r24h >= 60 || r1h >= 10) {
              badgeBg = "#f97316";
              badgeText = `🟠 ${r24h >= 60 ? `${r24h.toFixed(1)} mm (24h)` : `${r1h.toFixed(1)} mm (1h)`}`;
            } else if (r24h >= 30 || r1h >= 5) {
              badgeBg = "#f59e0b";
              badgeText = `🟡 ${r24h >= 30 ? `${r24h.toFixed(1)} mm (24h)` : `${r1h.toFixed(1)} mm (1h)`}`;
            } else if (r24h >= 10) {
              badgeBg = "#0284c7";
              badgeText = `🔵 ${r24h.toFixed(1)} mm (24h)`;
            } else if (r24h > 0 || r1h > 0) {
              badgeBg = "#38bdf8";
            } else {
              badgeBg = "#64748b";
              badgeText = "0.0 mm";
            }

            const locParts = [];
            if (closestPluvio.poblacion) locParts.push(closestPluvio.poblacion);
            if (closestPluvio.provincia) locParts.push(`(${closestPluvio.provincia})`);
            if (closestPluvio.subcuenca) locParts.push(`· ${closestPluvio.subcuenca}`);
            const metaLoc = locParts.length > 0 ? locParts.join(' ') : '--';
            const horaRaw = closestPluvio.fecha_1h || closestPluvio.fecha_24h || closestPluvio.ultima_hora || '';
            const horaText = horaRaw ? `· ${String(horaRaw).replace('T', ' ').substring(0, 16)}` : '';

            sections.push({
              type: "lluvia",
              title: "Pluviómetro (SAIH)",
              headerColor: "#0284c7",
              icon: "🌧️",
              name: `${closestPluvio.nombre}`,
              badge: badgeText,
              badgeBg: badgeBg,
              badgeColor: "#ffffff",
              station: closestPluvio,
              details: `
                <div class="unified-lluvia-block">
                  <div style="font-size:0.72rem; color:#cbd5e1; margin-bottom:2px;">Lluvia acumulada:</div>
                  <div style="display: grid; grid-template-columns: repeat(4, 1fr); gap: 4px; margin-top: 4px; background: rgba(0,0,0,0.3); padding: 5px 6px; border-radius: 6px; text-align: center;">
                    <div>
                      <div style="font-size:0.65rem; color:#94a3b8;">1 hora</div>
                      <div style="font-size:0.80rem; font-weight:700; color:${r1h > 0 ? '#38bdf8' : '#e2e8f0'};">${r1h.toFixed(1)}<span style="font-size:0.62rem; font-weight:400; color:#94a3b8;"> mm</span></div>
                    </div>
                    <div>
                      <div style="font-size:0.65rem; color:#94a3b8;">4 horas</div>
                      <div style="font-size:0.80rem; font-weight:700; color:${r4h > 0 ? '#38bdf8' : '#e2e8f0'};">${r4h.toFixed(1)}<span style="font-size:0.62rem; font-weight:400; color:#94a3b8;"> mm</span></div>
                    </div>
                    <div>
                      <div style="font-size:0.65rem; color:#94a3b8;">12 horas</div>
                      <div style="font-size:0.80rem; font-weight:700; color:${r12h > 0 ? '#38bdf8' : '#e2e8f0'};">${r12h.toFixed(1)}<span style="font-size:0.62rem; font-weight:400; color:#94a3b8;"> mm</span></div>
                    </div>
                    <div>
                      <div style="font-size:0.65rem; color:#94a3b8;">24 horas</div>
                      <div style="font-size:0.80rem; font-weight:700; color:${r24h > 0 ? '#38bdf8' : '#e2e8f0'};">${r24h.toFixed(1)}<span style="font-size:0.62rem; font-weight:400; color:#94a3b8;"> mm</span></div>
                    </div>
                  </div>
                  <div style="font-size:0.70rem; color:#94a3b8; margin-top:4px; border-top:1px solid rgba(255,255,255,0.08); padding-top:2px;">📍 ${metaLoc} · Cód: ${closestPluvio.codigo || '--'} ${horaText}</div>
                </div>
              `
            });
          }
        }
      }

      // 2.6 Modelos Numéricos (ECMWF IFS, GFS, AROME)
      if (this.layerManager.isLayerOnMap("ecmwf_ifs")) {
        const ecmwfPixel = this._getInstantEcmwfPixel(latlng.lat, latlng.lng);
        const step = (this.layerManager && this.layerManager.currentEcmwfStep) || 3;
        const type = (this.layerManager && this.layerManager.currentEcmwfType) || 'total';
        const typeLabel = type === 'total' ? 'Acumulado Total' : 'Intervalo (3h)';
        const cacheKey = this._getModelCacheKey('ecmwf', latlng.lat, latlng.lng, step, type);
        const hasCachedVal = this._modelValuesCache.has(cacheKey);

        if (ecmwfPixel || hasCachedVal) {
          const isZeroRain = !hasCachedVal && ecmwfPixel && ecmwfPixel.mm === 0;
          const validText = (ecmwfPixel && ecmwfPixel.validText) || `+${step}h`;
          let displayValHtml = '';
          let labelHtml = '';
          let badgeBg = '#059669';

          if (hasCachedVal) {
            const exactMm = this._modelValuesCache.get(cacheKey);
            const meta = this._getModelIntensityMeta(exactMm);
            badgeBg = meta.color;
            const displayMmStr = exactMm > 0 ? (exactMm < 1 ? exactMm.toFixed(2) : exactMm.toFixed(1)) : '0.0';
            displayValHtml = `<span style="font-size: 0.90rem; font-weight: 700; color: ${meta.color};">${displayMmStr} <span style="font-size: 0.70rem; font-weight: 400; color: #94a3b8;">mm</span></span>`;
            labelHtml = `<span style="display: inline-block; width: 8px; height: 8px; border-radius: 50%; background: ${meta.color}; margin-right: 4px;"></span>${meta.label}`;
          } else if (isZeroRain) {
            badgeBg = '#94a3b8';
            displayValHtml = `<span style="font-size: 0.90rem; font-weight: 700; color: #94a3b8;">0.0 <span style="font-size: 0.70rem; font-weight: 400;">mm</span></span>`;
            labelHtml = `<span style="display: inline-block; width: 8px; height: 8px; border-radius: 50%; background: #94a3b8; margin-right: 4px;"></span>Sin precipitación (<0.1 mm)`;
          } else {
            badgeBg = ecmwfPixel ? ecmwfPixel.color : '#059669';
            displayValHtml = `<span class="inspector-loading-val"><span class="inspector-spinner"></span> Obteniendo...</span>`;
            labelHtml = `<span style="display: inline-block; width: 8px; height: 8px; border-radius: 50%; background: ${badgeBg}; margin-right: 4px;"></span>${ecmwfPixel ? ecmwfPixel.label : 'Consultando modelo...'}`;
            this._fetchModelValue('ecmwf', latlng.lat, latlng.lng, step, type, cacheKey, ecmwfPixel ? ecmwfPixel.mm : 0.0);
          }

          sections.push({
            type: "model",
            title: "ECMWF IFS (25 km)",
            headerColor: "#059669",
            icon: "🇪🇺",
            name: `${typeLabel} (+${step}h)`,
            badge: validText,
            badgeBg: badgeBg,
            details: `
              <div style="display: flex; align-items: center; justify-content: space-between; margin-top: 4px; background: rgba(0,0,0,0.3); padding: 5px 8px; border-radius: 6px;">
                <span style="font-size: 0.75rem; color: #94a3b8;">Lluvia prevista:</span>
                ${displayValHtml}
              </div>
              <div style="font-size: 0.70rem; color: #94a3b8; margin-top: 3px;">
                ${labelHtml}
              </div>
            `
          });
        }
      }

      if (this.layerManager.isLayerOnMap("gfs_0p25")) {
        const gfsPixel = this._getInstantGfsPixel(latlng.lat, latlng.lng);
        const step = (this.layerManager && this.layerManager.currentGfsStep) || 3;
        const type = (this.layerManager && this.layerManager.currentGfsType) || 'total';
        const typeLabel = type === 'total' ? 'Acumulado Total' : 'Intervalo (3h)';
        const cacheKey = this._getModelCacheKey('gfs', latlng.lat, latlng.lng, step, type);
        const hasCachedVal = this._modelValuesCache.has(cacheKey);

        if (gfsPixel || hasCachedVal) {
          const isZeroRain = !hasCachedVal && gfsPixel && gfsPixel.mm === 0;
          const validText = (gfsPixel && gfsPixel.validText) || `+${step}h`;
          let displayValHtml = '';
          let labelHtml = '';
          let badgeBg = '#2563eb';

          if (hasCachedVal) {
            const exactMm = this._modelValuesCache.get(cacheKey);
            const meta = this._getModelIntensityMeta(exactMm);
            badgeBg = meta.color;
            const displayMmStr = exactMm > 0 ? (exactMm < 1 ? exactMm.toFixed(2) : exactMm.toFixed(1)) : '0.0';
            displayValHtml = `<span style="font-size: 0.90rem; font-weight: 700; color: ${meta.color};">${displayMmStr} <span style="font-size: 0.70rem; font-weight: 400; color: #94a3b8;">mm</span></span>`;
            labelHtml = `<span style="display: inline-block; width: 8px; height: 8px; border-radius: 50%; background: ${meta.color}; margin-right: 4px;"></span>${meta.label}`;
          } else if (isZeroRain) {
            badgeBg = '#94a3b8';
            displayValHtml = `<span style="font-size: 0.90rem; font-weight: 700; color: #94a3b8;">0.0 <span style="font-size: 0.70rem; font-weight: 400;">mm</span></span>`;
            labelHtml = `<span style="display: inline-block; width: 8px; height: 8px; border-radius: 50%; background: #94a3b8; margin-right: 4px;"></span>Sin precipitación (<0.1 mm)`;
          } else {
            badgeBg = gfsPixel ? gfsPixel.color : '#2563eb';
            displayValHtml = `<span class="inspector-loading-val"><span class="inspector-spinner"></span> Obteniendo...</span>`;
            labelHtml = `<span style="display: inline-block; width: 8px; height: 8px; border-radius: 50%; background: ${badgeBg}; margin-right: 4px;"></span>${gfsPixel ? gfsPixel.label : 'Consultando modelo...'}`;
            this._fetchModelValue('gfs', latlng.lat, latlng.lng, step, type, cacheKey, gfsPixel ? gfsPixel.mm : 0.0);
          }

          sections.push({
            type: "model",
            title: "NOAA GFS (25 km)",
            headerColor: "#2563eb",
            icon: "🇺🇸",
            name: `${typeLabel} (+${step}h)`,
            badge: validText,
            badgeBg: badgeBg,
            details: `
              <div style="display: flex; align-items: center; justify-content: space-between; margin-top: 4px; background: rgba(0,0,0,0.3); padding: 5px 8px; border-radius: 6px;">
                <span style="font-size: 0.75rem; color: #94a3b8;">Lluvia prevista:</span>
                ${displayValHtml}
              </div>
              <div style="font-size: 0.70rem; color: #94a3b8; margin-top: 3px;">
                ${labelHtml}
              </div>
            `
          });
        }
      }

      if (this.layerManager.isLayerOnMap("arome_precip")) {
        const aromePixel = this._getInstantAromePixel(latlng.lat, latlng.lng);
        const step = (this.layerManager && this.layerManager.currentAromeStep) || 1;
        const type = (this.layerManager && this.layerManager.currentAromeType) || 'total';
        const typeLabel = type === 'total' ? 'Acumulado Total' : 'Intervalo (1h)';
        const cacheKey = this._getModelCacheKey('arome', latlng.lat, latlng.lng, step, type);
        const hasCachedVal = this._modelValuesCache.has(cacheKey);

        if (aromePixel || hasCachedVal) {
          const isZeroRain = !hasCachedVal && aromePixel && aromePixel.mm === 0;
          const validText = (aromePixel && aromePixel.validText) || `+${step}h`;
          let displayValHtml = '';
          let labelHtml = '';
          let badgeBg = '#8b5cf6';

          if (hasCachedVal) {
            const exactMm = this._modelValuesCache.get(cacheKey);
            const meta = this._getModelIntensityMeta(exactMm);
            badgeBg = meta.color;
            const displayMmStr = exactMm > 0 ? (exactMm < 1 ? exactMm.toFixed(2) : exactMm.toFixed(1)) : '0.0';
            displayValHtml = `<span style="font-size: 0.90rem; font-weight: 700; color: ${meta.color};">${displayMmStr} <span style="font-size: 0.70rem; font-weight: 400; color: #94a3b8;">mm</span></span>`;
            labelHtml = `<span style="display: inline-block; width: 8px; height: 8px; border-radius: 50%; background: ${meta.color}; margin-right: 4px;"></span>${meta.label}`;
          } else if (isZeroRain) {
            badgeBg = '#94a3b8';
            displayValHtml = `<span style="font-size: 0.90rem; font-weight: 700; color: #94a3b8;">0.0 <span style="font-size: 0.70rem; font-weight: 400;">mm</span></span>`;
            labelHtml = `<span style="display: inline-block; width: 8px; height: 8px; border-radius: 50%; background: #94a3b8; margin-right: 4px;"></span>Sin precipitación (<0.1 mm)`;
          } else {
            badgeBg = aromePixel ? aromePixel.color : '#8b5cf6';
            displayValHtml = `<span class="inspector-loading-val"><span class="inspector-spinner"></span> Obteniendo...</span>`;
            labelHtml = `<span style="display: inline-block; width: 8px; height: 8px; border-radius: 50%; background: ${badgeBg}; margin-right: 4px;"></span>${aromePixel ? aromePixel.label : 'Consultando modelo...'}`;
            this._fetchModelValue('arome', latlng.lat, latlng.lng, step, type, cacheKey, aromePixel ? aromePixel.mm : 0.0);
          }

          sections.push({
            type: "model",
            title: "Météo-France AROME (1.3 km)",
            headerColor: "#8b5cf6",
            icon: "🇫🇷",
            name: `${typeLabel} (+${step}h)`,
            badge: validText,
            badgeBg: badgeBg,
            details: `
              <div style="display: flex; align-items: center; justify-content: space-between; margin-top: 4px; background: rgba(0,0,0,0.3); padding: 5px 8px; border-radius: 6px;">
                <span style="font-size: 0.75rem; color: #94a3b8;">Lluvia prevista:</span>
                ${displayValHtml}
              </div>
              <div style="font-size: 0.70rem; color: #94a3b8; margin-top: 3px;">
                ${labelHtml}
              </div>
            `
          });
        }
      }

      if (this.layerManager.isLayerOnMap("icon_eu")) {
        const iconPixel = this._getInstantIconPixel(latlng.lat, latlng.lng);
        const step = (this.layerManager && this.layerManager.currentIconStep) || 1;
        const type = (this.layerManager && this.layerManager.currentIconType) || 'total';
        const typeLabel = type === 'total' ? 'Acumulado Total' : (step > 78 ? 'Intervalo (3h)' : 'Intervalo (1h)');
        const cacheKey = this._getModelCacheKey('icon', latlng.lat, latlng.lng, step, type);
        const hasCachedVal = this._modelValuesCache.has(cacheKey);

        if (iconPixel || hasCachedVal) {
          const isZeroRain = !hasCachedVal && iconPixel && iconPixel.mm === 0;
          const validText = (iconPixel && iconPixel.validText) || `+${step}h`;
          let displayValHtml = '';
          let labelHtml = '';
          let badgeBg = '#0284c7';

          if (hasCachedVal) {
            const exactMm = this._modelValuesCache.get(cacheKey);
            const meta = this._getModelIntensityMeta(exactMm);
            badgeBg = meta.color;
            const displayMmStr = exactMm > 0 ? (exactMm < 1 ? exactMm.toFixed(2) : exactMm.toFixed(1)) : '0.0';
            displayValHtml = `<span style="font-size: 0.90rem; font-weight: 700; color: ${meta.color};">${displayMmStr} <span style="font-size: 0.70rem; font-weight: 400; color: #94a3b8;">mm</span></span>`;
            labelHtml = `<span style="display: inline-block; width: 8px; height: 8px; border-radius: 50%; background: ${meta.color}; margin-right: 4px;"></span>${meta.label}`;
          } else if (isZeroRain) {
            badgeBg = '#94a3b8';
            displayValHtml = `<span style="font-size: 0.90rem; font-weight: 700; color: #94a3b8;">0.0 <span style="font-size: 0.70rem; font-weight: 400;">mm</span></span>`;
            labelHtml = `<span style="display: inline-block; width: 8px; height: 8px; border-radius: 50%; background: #94a3b8; margin-right: 4px;"></span>Sin precipitación (<0.1 mm)`;
          } else {
            badgeBg = iconPixel ? iconPixel.color : '#0284c7';
            displayValHtml = `<span class="inspector-loading-val"><span class="inspector-spinner"></span> Obteniendo...</span>`;
            labelHtml = `<span style="display: inline-block; width: 8px; height: 8px; border-radius: 50%; background: ${badgeBg}; margin-right: 4px;"></span>${iconPixel ? iconPixel.label : 'Consultando modelo...'}`;
            this._fetchModelValue('icon', latlng.lat, latlng.lng, step, type, cacheKey, iconPixel ? iconPixel.mm : 0.0);
          }

          sections.push({
            type: "model",
            title: "DWD ICON-EU (6.5 km)",
            headerColor: "#0284c7",
            icon: "🇩🇪",
            name: `${typeLabel} (+${step}h)`,
            badge: validText,
            badgeBg: badgeBg,
            details: `
              <div style="display: flex; align-items: center; justify-content: space-between; margin-top: 4px; background: rgba(0,0,0,0.3); padding: 5px 8px; border-radius: 6px;">
                <span style="font-size: 0.75rem; color: #94a3b8;">Lluvia prevista:</span>
                ${displayValHtml}
              </div>
              <div style="font-size: 0.70rem; color: #94a3b8; margin-top: 3px;">
                ${labelHtml}
              </div>
            `
          });
        }
      }

      if (this.layerManager.isLayerOnMap("gem_gdps")) {
        const gemPixel = this._getInstantGemPixel(latlng.lat, latlng.lng);
        const step = (this.layerManager && this.layerManager.currentGemStep) || 3;
        const type = (this.layerManager && this.layerManager.currentGemType) || 'total';
        const typeLabel = type === 'total' ? 'Acumulado Total' : 'Intervalo (3h)';
        const cacheKey = this._getModelCacheKey('gem', latlng.lat, latlng.lng, step, type);
        const hasCachedVal = this._modelValuesCache.has(cacheKey);

        if (gemPixel || hasCachedVal) {
          const isZeroRain = !hasCachedVal && gemPixel && gemPixel.mm === 0;
          const validText = (gemPixel && gemPixel.validText) || `+${step}h`;
          let displayValHtml = '';
          let labelHtml = '';
          let badgeBg = '#e11d48';

          if (hasCachedVal) {
            const exactMm = this._modelValuesCache.get(cacheKey);
            const meta = this._getModelIntensityMeta(exactMm);
            badgeBg = meta.color;
            const displayMmStr = exactMm > 0 ? (exactMm < 1 ? exactMm.toFixed(2) : exactMm.toFixed(1)) : '0.0';
            displayValHtml = `<span style="font-size: 0.90rem; font-weight: 700; color: ${meta.color};">${displayMmStr} <span style="font-size: 0.70rem; font-weight: 400; color: #94a3b8;">mm</span></span>`;
            labelHtml = `<span style="display: inline-block; width: 8px; height: 8px; border-radius: 50%; background: ${meta.color}; margin-right: 4px;"></span>${meta.label}`;
          } else if (isZeroRain) {
            badgeBg = '#94a3b8';
            displayValHtml = `<span style="font-size: 0.90rem; font-weight: 700; color: #94a3b8;">0.0 <span style="font-size: 0.70rem; font-weight: 400;">mm</span></span>`;
            labelHtml = `<span style="display: inline-block; width: 8px; height: 8px; border-radius: 50%; background: #94a3b8; margin-right: 4px;"></span>Sin precipitación (<0.1 mm)`;
          } else {
            badgeBg = gemPixel ? gemPixel.color : '#e11d48';
            displayValHtml = `<span class="inspector-loading-val"><span class="inspector-spinner"></span> Obteniendo...</span>`;
            labelHtml = `<span style="display: inline-block; width: 8px; height: 8px; border-radius: 50%; background: ${badgeBg}; margin-right: 4px;"></span>${gemPixel ? gemPixel.label : 'Consultando modelo...'}`;
            this._fetchModelValue('gem', latlng.lat, latlng.lng, step, type, cacheKey, gemPixel ? gemPixel.mm : 0.0);
          }

          sections.push({
            type: "model",
            title: "MSC GEM-GDPS (15 km)",
            headerColor: "#e11d48",
            icon: "🇨🇦",
            name: `${typeLabel} (+${step}h)`,
            badge: validText,
            badgeBg: badgeBg,
            details: `
              <div style="display: flex; align-items: center; justify-content: space-between; margin-top: 4px; background: rgba(0,0,0,0.3); padding: 5px 8px; border-radius: 6px;">
                <span style="font-size: 0.75rem; color: #94a3b8;">Lluvia prevista:</span>
                ${displayValHtml}
              </div>
              <div style="font-size: 0.70rem; color: #94a3b8; margin-top: 3px;">
                ${labelHtml}
              </div>
            `
          });
        }
      }
    }

    // Si hay secciones coincidentes, renderizar tooltip unificado o tarjeta persistente
    if (sections.length > 0) {
      if (this._isTouchDevice() || isExplicit) {
        this._renderMobileInspectorSheet(latlng, sections);
        this._setInspectionMarker(latlng);
        this._closeTooltip();
      } else {
        this._renderUnifiedTooltip(latlng, sections);
      }
    } else {
      this._closeTooltip();
      if ((this._isTouchDevice() || isExplicit) && isExplicit) {
        this.closeMobileInspector();
      }
    }
  }

  _renderMobileInspectorSheet(latlng, sections) {
    const sheet = document.getElementById('mobile-inspector-sheet');
    const counterEl = document.getElementById('mobile-inspector-counter');
    const coordsEl = document.getElementById('mobile-inspector-coords');
    const bodyEl = document.getElementById('mobile-inspector-body');

    if (!sheet || !bodyEl) return;

    this._activeMobileLatLng = latlng;

    if (counterEl) {
      counterEl.textContent = `${sections.length} ${sections.length === 1 ? 'capa detectada' : 'capas superpuestas'}`;
    }

    if (coordsEl) {
      coordsEl.textContent = `${latlng.lat.toFixed(4)}°, ${latlng.lng.toFixed(4)}°`;
    }

    let html = '';
    sections.forEach((sec) => {
      const headerColor = sec.headerColor || sec.badgeBg || '#38bdf8';
      const badgeBg = sec.badgeBg || 'rgba(56, 189, 248, 0.18)';
      const badgeColor = sec.badgeColor || '#fff';

      let actionsHtml = '';
      if (sec.type === 'cuenca' && sec.basinId) {
        actionsHtml = `
          <div class="mobile-inspector-actions-row">
            <button type="button" class="mobile-inspector-btn-action btn-action-primary" data-action="hydro" data-basin-id="${sec.basinId}">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><path d="M12 2.69l5.66 5.66a8 8 0 1 1-11.31 0z"></path></svg>
              Ver Hidrograma (hm³)
            </button>
            <button type="button" class="mobile-inspector-btn-action" data-action="zoom-basin" data-basin-id="${sec.basinId}">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"></circle><line x1="21" y1="21" x2="16.65" y2="16.65"></line></svg>
              Centrar Cuenca
            </button>
          </div>
        `;
      } else if (sec.type === 'caudal' && sec.station) {
        actionsHtml = `
          <div class="mobile-inspector-actions-row">
            <button type="button" class="mobile-inspector-btn-action btn-action-primary" data-action="caudal-modal" data-station-code="${sec.station.codigo || ''}">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"></polyline></svg>
              Ver Gráfica de Caudal
            </button>
          </div>
        `;
      } else if (sec.type === 'embalse' && sec.station) {
        actionsHtml = `
          <div class="mobile-inspector-actions-row">
            <button type="button" class="mobile-inspector-btn-action btn-action-primary" data-action="embalse-modal" data-station-code="${sec.station.codigo || ''}">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><path d="M3 15v4c0 1.1.9 2 2 2h14a2 2 0 0 0 2-2v-4M17 9l-5 5-5-5M12 12.8V2.5"/></svg>
              Ver Evolución Embalse
            </button>
          </div>
        `;
      }

      html += `
        <div class="mobile-inspector-section section-${sec.type}" style="border-left-color: ${headerColor};">
          <div class="mobile-inspector-sec-header">
            <div class="mobile-inspector-sec-title-group">
              <span class="mobile-inspector-sec-icon">${sec.icon}</span>
              <span class="mobile-inspector-sec-title" style="color: ${headerColor};">${sec.title}</span>
            </div>
            ${sec.badge ? `<span class="mobile-inspector-sec-badge" style="background:${badgeBg}; color:${badgeColor};">${sec.badge}</span>` : ''}
          </div>
          <div class="mobile-inspector-sec-name">${sec.name}</div>
          ${sec.details ? `<div class="mobile-inspector-sec-details">${sec.details}</div>` : ''}
          ${actionsHtml}
        </div>
      `;
    });

    bodyEl.innerHTML = html;

    // Vincular interactividad a los botones de acción contextuales
    bodyEl.querySelectorAll('button[data-action]').forEach((btn) => {
      btn.addEventListener('click', (ev) => {
        ev.stopPropagation();
        const action = btn.getAttribute('data-action');
        if (action === 'hydro') {
          const basinId = btn.getAttribute('data-basin-id');
          const cuencaSec = sections.find((s) => s.type === 'cuenca' && s.basinId == basinId);
          const activeModel = this.layerManager ? this.layerManager.getActivePredictionModel() : 'ecmwf';
          if (this.layerManager && basinId) {
            this.layerManager.openBasinHydroModal(basinId, cuencaSec ? cuencaSec.basinProps : {}, activeModel);
          }
        } else if (action === 'zoom-basin') {
          const basinId = btn.getAttribute('data-basin-id');
          const cuencaSec = sections.find((s) => s.type === 'cuenca' && s.basinId == basinId);
          if (cuencaSec && cuencaSec.featureLayer && this.mapManager) {
            this.mapManager.fitBounds(cuencaSec.featureLayer.getBounds());
          }
        } else if (action === 'caudal-modal') {
          const caudalSec = sections.find((s) => s.type === 'caudal' && s.station);
          if (caudalSec && caudalSec.station && this.layerManager) {
            this.layerManager.openCaudalHistoryModal(caudalSec.station, 24);
          }
        } else if (action === 'embalse-modal') {
          const embalseSec = sections.find((s) => s.type === 'embalse' && s.station);
          if (embalseSec && embalseSec.station && this.layerManager) {
            this.layerManager.openEmbalseHistoryModal(embalseSec.station, 24);
          }
        }
      });
    });

    sheet.style.display = 'flex';
    requestAnimationFrame(() => {
      sheet.classList.add('is-open');
    });
  }

  _initMobileSheetEvents() {
    const closeBtn = document.getElementById('btn-close-mobile-inspector');
    if (closeBtn) {
      closeBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        this.closeMobileInspector();
      });
    }

    const sheet = document.getElementById('mobile-inspector-sheet');
    const handleBar = sheet ? sheet.querySelector('.mobile-inspector-handle-bar') : null;
    if (sheet && handleBar) {
      let touchStartY = 0;
      let touchDiffY = 0;

      handleBar.addEventListener('touchstart', (e) => {
        if (e.touches && e.touches[0]) {
          touchStartY = e.touches[0].clientY;
          touchDiffY = 0;
        }
      }, { passive: true });

      handleBar.addEventListener('touchmove', (e) => {
        if (e.touches && e.touches[0]) {
          touchDiffY = e.touches[0].clientY - touchStartY;
          if (touchDiffY > 0) {
            sheet.style.transform = `translateY(${touchDiffY}px)`;
          }
        }
      }, { passive: true });

      handleBar.addEventListener('touchend', () => {
        if (touchDiffY > 45) {
          this.closeMobileInspector();
        } else {
          sheet.style.transform = '';
        }
        touchStartY = 0;
        touchDiffY = 0;
      });
    }
  }

  _setInspectionMarker(latlng) {
    if (!latlng || !this.map) return;

    if (!this.inspectionMarker) {
      const inspectIcon = L.divIcon({
        className: 'rainloc-inspect-marker-wrapper',
        html: `
          <div class="rainloc-inspect-pin">
            <div class="inspect-pin-pulse"></div>
            <div class="inspect-pin-dot"></div>
          </div>
        `,
        iconSize: [32, 32],
        iconAnchor: [16, 16]
      });

      this.inspectionMarker = L.marker(latlng, {
        icon: inspectIcon,
        interactive: true,
        zIndexOffset: 1200
      }).addTo(this.map);

      this.inspectionMarker.on('click', (e) => {
        if (e.originalEvent) {
          e.originalEvent._stopInspector = true;
          L.DomEvent.stopPropagation(e);
        }
        this.closeMobileInspector();
      });
    } else {
      this.inspectionMarker.setLatLng(latlng);
      if (!this.map.hasLayer(this.inspectionMarker)) {
        this.inspectionMarker.addTo(this.map);
      }
    }
  }

  _removeInspectionMarker() {
    if (this.inspectionMarker && this.map.hasLayer(this.inspectionMarker)) {
      this.map.removeLayer(this.inspectionMarker);
    }
  }

  closeMobileInspector() {
    const sheet = document.getElementById('mobile-inspector-sheet');
    if (sheet) {
      sheet.classList.remove('is-open');
      sheet.style.transform = '';
      setTimeout(() => {
        sheet.style.display = 'none';
      }, 220);
    }
    this._removeInspectionMarker();
    this._clearCuencaHover();
    if (this.uiManager) {
      this.uiManager.resetHoverInfo();
    }
    this._activeMobileLatLng = null;
  }

  _renderUnifiedTooltip(latlng, sections) {
    const isMultiLayer = sections.length > 1;

    let html = `<div class="unified-tooltip-container ${isMultiLayer ? "is-multi-layer" : ""}">`;

    if (isMultiLayer) {
      html += `
        <div class="unified-tooltip-counter">
          <span class="counter-dot"></span>
          <span>${sections.length} capas superpuestas en este punto</span>
        </div>
      `;
    }

    sections.forEach((sec, idx) => {
      const isDivider = idx > 0;
      html += `
        ${isDivider ? '<div class="unified-section-divider"></div>' : ''}
        <div class="unified-tooltip-section section-${sec.type}">
          <div class="unified-section-header">
            <span class="unified-section-title" style="color: ${sec.headerColor || "#38bdf8"};">
              ${sec.icon} ${sec.title}
            </span>
            ${sec.badge ? `<span class="unified-section-badge" style="background:${sec.badgeBg || "rgba(255,255,255,0.1)"}; color:${sec.badgeColor || "#fff"};">${sec.badge}</span>` : ""}
          </div>
          <div class="unified-section-name">${sec.name}</div>
          ${sec.details ? `<div class="unified-section-details">${sec.details}</div>` : ""}
        </div>
      `;
    });

    html += `</div>`;

    this.unifiedTooltip
      .setLatLng(latlng)
      .setContent(html);

    if (!this.map.hasLayer(this.unifiedTooltip)) {
      this.unifiedTooltip.addTo(this.map);
    }
  }

  _closeTooltip() {
    if (this.map.hasLayer(this.unifiedTooltip)) {
      this.map.removeLayer(this.unifiedTooltip);
    }
  }

  _applyCuencaHover(layer, systemColor) {
    if (this._currentHoveredCuenca === layer) return;
    this._clearCuencaHover();

    this._currentHoveredCuenca = layer;
    if (this.cuencasLayer && typeof this.cuencasLayer.setHoverHighlight === 'function' && layer && layer.feature) {
      this.cuencasLayer.setHoverHighlight(layer.feature, systemColor);
    } else if (layer && layer.setStyle) {
      const isFav = layer.feature && StorageManager.isFavorite(layer.feature.id);
      layer.setStyle({
        ...CONFIG.styles.cuencaHover,
        color: isFav ? '#fbbf24' : systemColor,
        opacity: 1.0,
        fillColor: systemColor
      });
    }
  }

  _clearCuencaHover() {
    if (this._currentHoveredCuenca) {
      if (this.cuencasLayer && typeof this.cuencasLayer.clearHoverHighlight === 'function') {
        this.cuencasLayer.clearHoverHighlight();
      } else if (this.cuencasLayer && this.cuencasLayer.getFeatureStyle) {
        const originalStyle = this.cuencasLayer.getFeatureStyle(this._currentHoveredCuenca.feature);
        this._currentHoveredCuenca.setStyle(originalStyle);
      }
      this._currentHoveredCuenca = null;
    }
  }

  /**
   * Algoritmo Ray-Casting (Point-in-Polygon) optimizado
   */
  _isPointInGeometry(lat, lng, geometry) {
    if (!geometry || !geometry.coordinates) return false;
    const type = geometry.type;

    if (type === "Polygon") {
      return this._isPointInPolygon(lat, lng, geometry.coordinates);
    } else if (type === "MultiPolygon") {
      for (const poly of geometry.coordinates) {
        if (this._isPointInPolygon(lat, lng, poly)) return true;
      }
    }
    return false;
  }

  _isPointInPolygon(lat, lng, rings) {
    if (!rings || rings.length === 0) return false;
    // Anillo exterior
    const outerRing = rings[0];
    if (!this._pointInRing(lat, lng, outerRing)) return false;

    // Si tiene anillos interiores (agujeros), no debe estar dentro de ellos
    for (let i = 1; i < rings.length; i++) {
      if (this._pointInRing(lat, lng, rings[i])) {
        return false;
      }
    }
    return true;
  }

  _pointInRing(lat, lng, ring) {
    let inside = false;
    for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
      const xi = ring[i][0], yi = ring[i][1];
      const xj = ring[j][0], yj = ring[j][1];

      const intersect = ((yi > lat) !== (yj > lat)) &&
        (lng < (xj - xi) * (lat - yi) / (yj - yi) + xi);
      if (intersect) inside = !inside;
    }
    return inside;
  }
}
