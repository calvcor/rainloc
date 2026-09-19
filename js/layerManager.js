/**
 * RainLoc - Gestor de Capas Temáticas (Tiempo Real & Predicción)
 * Control de activación simultánea, opacidades individuales e integración con Leaflet.
 */

import { CONFIG, formatMadridDateTime, formatMadridTime, formatEcmwfTimestamp, formatGfsTimestamp } from './config.js';
import { StorageManager } from './storage.js';

export class LayerManager {
  constructor(mapManager) {
    this.mapManager = mapManager;
    this.map = mapManager.map;
    this.layers = {}; // Instancias Leaflet de cada capa overlay
    this.layerStates = {}; // { id: { active: boolean, opacity: number } }
    
    const prefs = StorageManager.load();
    this.lightningWindowMinutes = Math.min(15, Math.max(1, prefs.lightningWindow || 15));
    this.showRadarLightning = Boolean(prefs.showRadarLightning);
    this.lightningEventSource = null;
    this.radarEventSource = null;
    this.ecmwfEventSource = null;
    this.gfsEventSource = null;
    this.lightningGroup = L.layerGroup();
    this.currentRadarMode = 'composite';

    // ECMWF IFS NWP Model State
    this.currentEcmwfStep = 3;
    this.currentEcmwfType = 'total'; // 'total' | 'interval'
    this.ecmwfMetadata = null;
    this.ecmwfPlaybackInterval = null;
    this.isEcmwfPlaying = false;
    this.ecmwfCanvasData = null;
    this.currentEcmwfOverlay = null;
    this._ecmwfStepRequestId = 0;
    this._ecmwfImageCache = new Map();

    // NOAA GFS NWP Model State
    this.currentGfsStep = 3;
    this.currentGfsType = 'total'; // 'total' | 'interval'
    this.gfsMetadata = null;
    this.gfsPlaybackInterval = null;
    this.isGfsPlaying = false;
    this.gfsCanvasData = null;
    this.currentGfsOverlay = null;
    this._gfsStepRequestId = 0;
    this._gfsImageCache = new Map();
  }

  /**
   * Inicializa las capas de Tiempo Real y Predicción
   */
  init() {
    const prefs = StorageManager.load();
    const savedActive = prefs.activeLayers || {};
    const savedOpacities = prefs.layerOpacities || {};

    // Inicializar capas de Tiempo Real
    CONFIG.overlayLayers.realtime.forEach(def => {
      this._registerLayerDefinition(def, savedActive[def.id], savedOpacities[def.id]);
    });

    // Inicializar capas de Predicción
    CONFIG.overlayLayers.prediction.forEach(def => {
      this._registerLayerDefinition(def, savedActive[def.id], savedOpacities[def.id]);
    });

    // Iniciar conexión SSE en tiempo real para el radar (actualización instantánea push)
    this._startRadarSSE();

    // Iniciar conexión SSE en tiempo real para el modelo ECMWF IFS (aviso reactivo de nuevos pasos)
    this._startEcmwfSSE();

    // Iniciar conexión SSE en tiempo real para el modelo NOAA GFS (aviso reactivo de nuevos pasos)
    this._startGfsSSE();

    // Iniciar autorrefresco periódico de capas SAIH y AEMET (por defecto cada 5 min = 300s)
    const refreshSec = (prefs.autoRefreshInterval !== undefined && prefs.autoRefreshInterval > 0) ? prefs.autoRefreshInterval : 300;
    this.startAutoRefresh(refreshSec);
  }

  /**
   * Registra y monta la capa Leaflet para una definición de capa
   */
  _registerLayerDefinition(def, isSavedActive, savedOpacity) {
    const prefs = StorageManager.load();
    const currentTab = prefs.activeTab || 'realtime';

    let isActive = (isSavedActive !== undefined) ? Boolean(isSavedActive) : def.defaultActive;
    const opacity = (savedOpacity !== undefined) ? parseFloat(savedOpacity) : def.defaultOpacity;

    // Regla: En predicción solo 1 modelo puede estar activo
    if (def.type === 'prediction' && isActive) {
      const alreadyHasPred = Object.values(this.layerStates).some(s => s.type === 'prediction' && s.active);
      if (alreadyHasPred) {
        isActive = false;
        StorageManager.setLayerActive(def.id, false);
      }
    }

    this.layerStates[def.id] = {
      id: def.id,
      name: def.name,
      active: isActive,
      opacity: opacity,
      type: def.type
    };

    // Crear la instancia Leaflet correspondiente
    const leafletLayer = this._createLeafletLayer(def, opacity);
    this.layers[def.id] = leafletLayer;

    // Montar en el mapa según la pestaña activa actual
    if (leafletLayer && isActive) {
      if (currentTab === 'realtime' && def.type === 'realtime') {
        leafletLayer.addTo(this.map);
      } else if (currentTab === 'prediction' && def.type === 'prediction') {
        leafletLayer.addTo(this.map);
      }
    }
  }

  /**
   * Genera la capa Leaflet para cada feed temático
   */
  _createLeafletLayer(def, opacity) {
    const layerGroup = L.layerGroup();

    if (def.id === 'aemet_warnings') {
      this._loadAemetWarnings(layerGroup, opacity);
    } 
    else if (def.id === 'radar') {
      this._loadRadarLayer(layerGroup, opacity);
    }
    else if (def.id === 'saih_caudales') {
      this._loadCaudalesLayer(layerGroup, opacity);
    }
    else if (def.id === 'saih_embalses') {
      this._loadEmbalsesLayer(layerGroup, opacity);
    }
    else if (def.id === 'saih_lluvias') {
      this._loadLluviasLayer(layerGroup, opacity);
    }
    else if (def.id === 'ecmwf_ifs') {
      this._loadEcmwfLayer(layerGroup, opacity);
    }
    else if (def.id === 'gfs_0p25') {
      this._loadGfsLayer(layerGroup, opacity);
    }
    else if (def.id.startsWith('arome') || def.id.startsWith('icon')) {
      const demoModel = L.rectangle([[38.5, -1.2], [40.2, 0.4]], {
        color: def.color,
        weight: 1.5,
        fillColor: def.color,
        fillOpacity: opacity * 0.35,
        dashArray: '6, 6'
      });
      layerGroup.addLayer(demoModel);
    }

    return layerGroup;
  }

  /**
   * Comprueba si una capa específica está actualmente montada y visible en el mapa Leaflet
   */
  isLayerOnMap(layerId) {
    const layer = this.layers[layerId];
    return Boolean(layer && this.map && this.map.hasLayer(layer));
  }

  _showLayerOnMap(layerId) {
    const layer = this.layers[layerId];
    if (layer && this.map && !this.map.hasLayer(layer)) {
      this.map.addLayer(layer);
    }
  }

  _hideLayerFromMap(layerId) {
    const layer = this.layers[layerId];
    if (layer && this.map && this.map.hasLayer(layer)) {
      this.map.removeLayer(layer);
    }
  }

  /**
   * Gestiona el cambio de pestaña entre Tiempo Real y Predicción
   * - Al ir a Predicción: oculta todas las capas de tiempo real y muestra la predicción activa (si hay).
   * - Al volver a Tiempo Real: oculta la predicción y restaura todas las capas de tiempo real con su configuración previa.
   */
  onTabChange(tabId) {
    if (tabId === 'prediction') {
      // 1. Ocultar del mapa todas las capas de tiempo real
      CONFIG.overlayLayers.realtime.forEach(def => {
        this._hideLayerFromMap(def.id);
      });
      if (this.lightningGroup && this.map.hasLayer(this.lightningGroup)) {
        this.map.removeLayer(this.lightningGroup);
      }

      // 2. Mostrar la capa de predicción activa (garantizando exclusividad de una única predicción)
      let activePredId = null;
      CONFIG.overlayLayers.prediction.forEach(def => {
        const state = this.layerStates[def.id];
        if (state && state.active && !activePredId) {
          activePredId = def.id;
          this._showLayerOnMap(def.id);
        } else {
          this._hideLayerFromMap(def.id);
          if (state && state.active) {
            state.active = false;
            StorageManager.setLayerActive(def.id, false);
            if (this.uiManager) this.uiManager.updateLayerCardActiveState(def.id, false);
          }
        }
      });
    } else {
      // 1. Ocultar del mapa cualquier modelo de predicción
      CONFIG.overlayLayers.prediction.forEach(def => {
        this._hideLayerFromMap(def.id);
      });
      if (this.isEcmwfPlaying) {
        this.pauseEcmwfPlayback();
      }
      if (this.isGfsPlaying) {
        this.pauseGfsPlayback();
      }

      // 2. Restaurar y reactivar en el mapa todas las capas de tiempo real configuradas como activas
      CONFIG.overlayLayers.realtime.forEach(def => {
        const state = this.layerStates[def.id];
        if (state && state.active) {
          this._showLayerOnMap(def.id);
          if (def.id === 'radar' && this.showRadarLightning && this.lightningGroup) {
            if (!this.map.hasLayer(this.lightningGroup)) {
              this.map.addLayer(this.lightningGroup);
            }
          }
        } else {
          this._hideLayerFromMap(def.id);
        }
        if (this.uiManager && state) {
          this.uiManager.updateLayerCardActiveState(def.id, state.active);
        }
      });
    }
  }

  /**
   * Conmuta la visibilidad de una capa (activar/desactivar)
   * Aplica reglas de negocio:
   * 1. Las capas de predicción son mutuamente excluyentes (solo una a la vez).
   * 2. Al activar una predicción se ocultan todas las capas de tiempo real.
   * 3. Al activar una capa de tiempo real se oculta cualquier predicción.
   */
  toggleLayer(layerId, active) {
    const isPrediction = CONFIG.overlayLayers.prediction.some(p => p.id === layerId);
    const isRealtime = CONFIG.overlayLayers.realtime.some(r => r.id === layerId);

    if (isPrediction) {
      if (active) {
        // Regla 1: Desactivar del mapa todas las capas de tiempo real
        CONFIG.overlayLayers.realtime.forEach(r => {
          this._hideLayerFromMap(r.id);
        });
        if (this.lightningGroup && this.map.hasLayer(this.lightningGroup)) {
          this.map.removeLayer(this.lightningGroup);
        }

        // Regla 2: Solo una predicción activa a la vez
        CONFIG.overlayLayers.prediction.forEach(p => {
          if (p.id !== layerId) {
            this._hideLayerFromMap(p.id);
            if (this.layerStates[p.id]) this.layerStates[p.id].active = false;
            StorageManager.setLayerActive(p.id, false);
            if (this.uiManager) this.uiManager.updateLayerCardActiveState(p.id, false);
            if (p.id === 'ecmwf_ifs' && this.isEcmwfPlaying) {
              this.pauseEcmwfPlayback();
            }
            if (p.id === 'gfs_0p25' && this.isGfsPlaying) {
              this.pauseGfsPlayback();
            }
          }
        });

        // Activar la predicción seleccionada
        this._showLayerOnMap(layerId);
        if (this.layerStates[layerId]) this.layerStates[layerId].active = true;
        StorageManager.setLayerActive(layerId, true);
        if (this.uiManager) this.uiManager.updateLayerCardActiveState(layerId, true);
      } else {
        this._hideLayerFromMap(layerId);
        if (this.layerStates[layerId]) this.layerStates[layerId].active = false;
        StorageManager.setLayerActive(layerId, false);
        if (this.uiManager) this.uiManager.updateLayerCardActiveState(layerId, false);
        if (layerId === 'ecmwf_ifs' && this.isEcmwfPlaying) {
          this.pauseEcmwfPlayback();
        }
        if (layerId === 'gfs_0p25' && this.isGfsPlaying) {
          this.pauseGfsPlayback();
        }
      }
      return;
    }

    if (isRealtime) {
      if (active) {
        // Ocultar cualquier modelo de predicción
        CONFIG.overlayLayers.prediction.forEach(p => {
          this._hideLayerFromMap(p.id);
          if (this.layerStates[p.id]) this.layerStates[p.id].active = false;
          StorageManager.setLayerActive(p.id, false);
          if (this.uiManager) this.uiManager.updateLayerCardActiveState(p.id, false);
          if (p.id === 'ecmwf_ifs' && this.isEcmwfPlaying) {
            this.pauseEcmwfPlayback();
          }
          if (p.id === 'gfs_0p25' && this.isGfsPlaying) {
            this.pauseGfsPlayback();
          }
        });

        this._showLayerOnMap(layerId);
      } else {
        this._hideLayerFromMap(layerId);
      }

      if (this.layerStates[layerId]) {
        this.layerStates[layerId].active = active;
      }
      StorageManager.setLayerActive(layerId, active);
      if (this.uiManager) this.uiManager.updateLayerCardActiveState(layerId, active);

      // Efectos secundarios de capas SAIH y Radar
      if (layerId === 'saih_caudales') {
        if (active) {
          if (!this._caudalesPollInterval) {
            this._caudalesPollInterval = setInterval(() => {
              if (this.layerStates['saih_caudales'] && this.layerStates['saih_caudales'].active) {
                const op = this.layerStates['saih_caudales'].opacity || 0.95;
                this._loadCaudalesLayer(this.layers['saih_caudales'], op);
              }
            }, 5 * 60 * 1000);
          }
        } else {
          if (this._caudalesPollInterval) {
            clearInterval(this._caudalesPollInterval);
            this._caudalesPollInterval = null;
          }
        }
      }

      if (layerId === 'radar') {
        if (active) {
          if (this.showRadarLightning) {
            if (!this.map.hasLayer(this.lightningGroup)) {
              this.lightningGroup.addTo(this.map);
            }
            this.reloadLightningLayer();
            this._startLightningSSE();
          }
        } else {
          if (this.map.hasLayer(this.lightningGroup)) {
            this.map.removeLayer(this.lightningGroup);
          }
          this._stopLightningSSE();
        }
      }
    }
  }

  /**
   * Modifica la opacidad de una capa temática individual
   * @param {string} layerId 
   * @param {number} opacity 0.0 a 1.0
   */
  setLayerOpacity(layerId, opacity) {
    const op = parseFloat(opacity);
    const layer = this.layers[layerId];

    if (this.layerStates[layerId]) {
      this.layerStates[layerId].opacity = op;
    }

    StorageManager.setLayerOpacity(layerId, op);

    if (layer) {
      layer.eachLayer((subLayer) => {
        if (subLayer.setOpacity) {
          subLayer.setOpacity(op);
        } else if (subLayer.setStyle) {
          subLayer.setStyle({
            opacity: op,
            fillOpacity: op * 0.5
          });
        }
      });
    }
  }

  /**
   * Obtiene el estado actual de una capa
   * @param {string} layerId 
   */
  getLayerState(layerId) {
    return this.layerStates[layerId] || null;
  }

  /**
   * Carga asíncrona de avisos AEMET en vivo desde el Backend API
   */
  async _loadAemetWarnings(layerGroup, initialOpacity) {
    const apiUrl = `${CONFIG.apiBaseUrl}/warnings/aemet?_t=${Date.now()}`;
    try {
      const resp = await fetch(apiUrl);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const geojson = await resp.json();
      
      if (geojson && Array.isArray(geojson.features)) {
        layerGroup.clearLayers();
        if (geojson.features.length > 0) {
          const warningLayer = L.geoJSON(geojson, {
            pane: 'warningsPane',
            style: (feature) => {
              const color = feature.properties.color || '#FACC15';
              return {
                color: color,
                weight: 2.5,
                opacity: 0.95,
                fillColor: color,
                fillOpacity: initialOpacity * 0.45,
                dashArray: '4, 4'
              };
            }
          });
          layerGroup.addLayer(warningLayer);
          if (this.uiManager && this.uiManager.updateLayerTimestamp) {
            this.uiManager.updateLayerTimestamp('aemet_warnings', `Vigencia: <strong>${formatMadridDateTime(new Date())}</strong>`);
          }
        } else {
          // 0 avisos meteorológicos en vigor en este momento
          if (this.uiManager && this.uiManager.updateLayerTimestamp) {
            this.uiManager.updateLayerTimestamp('aemet_warnings', `Sin avisos activos ahora`);
          }
        }
        return;
      }
    } catch (err) {
      console.warn('No se pudo conectar al API de AEMET:', err);
      layerGroup.clearLayers();
      if (this.uiManager && this.uiManager.updateLayerTimestamp) {
        this.uiManager.updateLayerTimestamp('aemet_warnings', `<span style="color:#94a3b8;">Sin conexión AEMET</span>`);
      }
    }
  }

  /**
   * Carga la capa de Radar (Compuesto España o Estación Única) mediante L.imageOverlay
   */
  async _loadRadarLayer(layerGroup, opacity) {
    try {
      // 1. Obtener metadatos del radar (con cache busting para forzar metadatos actualizados)
      const metaResp = await fetch(`${CONFIG.apiBaseUrl}/radar/metadata?_t=${Date.now()}`);
      if (!metaResp.ok) throw new Error(`HTTP ${metaResp.status}`);
      const metadata = await metaResp.json();
      this.radarMetadata = metadata;

      // Actualizar fecha y hora exacta de la captura en la UI (en zona horaria Europe/Madrid)
      const ts = metadata.latest_composite && metadata.latest_composite.timestep;
      const timestepText = ts ? formatMadridDateTime(ts) : formatMadridDateTime(new Date());

      if (this.uiManager && this.uiManager.updateLayerTimestamp) {
        this.uiManager.updateLayerTimestamp('radar', `Captura: <strong>${timestepText}</strong>`);
      }

      // Determinar bounds e URL de la imagen del compuesto nacional
      const bounds = metadata.composite_bounds || [[35.0, -10.0], [44.5, 5.0]];
      const imgUrl = `${CONFIG.apiBaseUrl}/radar/image?mode=composite&_t=${Date.now()}`;

      layerGroup.clearLayers();

      // 1. Imagen raster de reflectividad con renderizado nítido de píxeles/celdas
      const imageOverlay = L.imageOverlay(imgUrl, bounds, {
        pane: 'radarPane',
        opacity: opacity,
        interactive: false,
        crossOrigin: 'anonymous',
        className: 'radar-raster-overlay'
      });

      imageOverlay.on('error', () => {
        console.warn('La imagen de radar no pudo ser cargada desde el backend');
      });

      // Crear canvas en memoria para consulta ultra-rápida (0ms) en el cliente
      this.radarCanvasData = null;
      const offscreenImg = new Image();
      offscreenImg.crossOrigin = 'anonymous';
      offscreenImg.onload = () => {
        try {
          const canvas = document.createElement('canvas');
          canvas.width = offscreenImg.naturalWidth;
          canvas.height = offscreenImg.naturalHeight;
          const ctx = canvas.getContext('2d', { willReadFrequently: true });
          ctx.drawImage(offscreenImg, 0, 0);
          this.radarCanvasData = {
            ctx: ctx,
            width: offscreenImg.naturalWidth,
            height: offscreenImg.naturalHeight,
            bounds: bounds,
            mode: 'composite'
          };
        } catch (err) {
          console.warn('Canvas raster inaccesible para lectura local:', err);
        }
      };
      offscreenImg.src = imgUrl;

      layerGroup.addLayer(imageOverlay);
      this.currentRadarOverlay = imageOverlay;
      this.currentRadarBounds = bounds;
      this.currentRadarMode = 'composite';

    } catch (err) {
      console.warn('Error al cargar radar desde backend API:', err);
    }
  }

  /**
   * Recarga la capa de radar manteniendo el grupo y estado de visualización
   */
  reloadRadarLayer() {
    const radarGroup = this.layers['radar'];
    if (!radarGroup) return;
    const opacity = (this.layerStates['radar'] && this.layerStates['radar'].opacity) || 0.75;
    this._loadRadarLayer(radarGroup, opacity);
  }

  /**
   * Genera una URL estable para la imagen de un paso de ECMWF, aprovechando el caché del navegador
   */
  _getEcmwfImageUrl(step, type) {
    const runId = (this.ecmwfMetadata && (this.ecmwfMetadata.run_id || this.ecmwfMetadata.run_timestamp)) || '';
    const runParam = runId ? `&run=${encodeURIComponent(runId)}` : '';
    return `${CONFIG.apiBaseUrl}/models/ecmwf/image?step=${step}&type=${type}${runParam}`;
  }

  /**
   * Precarga pasos adyacentes en la memoria del navegador para transiciones instantáneas y fluidas
   */
  _preloadEcmwfSteps(currentStep, type) {
    if (!this.ecmwfMetadata || !this.ecmwfMetadata.available_steps) return;
    const steps = this.ecmwfMetadata.available_steps;
    const curIdx = steps.indexOf(currentStep);
    if (curIdx === -1) return;

    // Precargar los 5 siguientes y los 2 anteriores
    const targetIndices = [
      curIdx + 1, curIdx + 2, curIdx + 3, curIdx + 4, curIdx + 5,
      curIdx - 1, curIdx - 2
    ];

    const bbox = this.ecmwfMetadata.bbox || { lat_min: 35.0, lat_max: 44.5, lon_min: -10.0, lon_max: 5.0 };
    const bounds = [[bbox.lat_min, bbox.lon_min], [bbox.lat_max, bbox.lon_max]];

    targetIndices.forEach(idx => {
      if (idx >= 0 && idx < steps.length) {
        const step = steps[idx];
        const key = `${type}_${step}`;
        if (!this._ecmwfImageCache.has(key)) {
          const img = new Image();
          img.crossOrigin = 'anonymous';
          const imgUrl = this._getEcmwfImageUrl(step, type);
          img.onload = () => {
            try {
              const canvas = document.createElement('canvas');
              canvas.width = img.naturalWidth;
              canvas.height = img.naturalHeight;
              const ctx = canvas.getContext('2d', { willReadFrequently: true });
              ctx.drawImage(img, 0, 0);
              const stepInfo = (this.ecmwfMetadata.steps || []).find(s => s.step === step);
              const validText = stepInfo ? (stepInfo.valid_time_local || `+${step}h`) : `+${step}h`;
              this._ecmwfImageCache.set(key, {
                img: img,
                canvasData: {
                  ctx: ctx,
                  width: img.naturalWidth,
                  height: img.naturalHeight,
                  bounds: bounds,
                  step: step,
                  type: type,
                  validText: validText
                }
              });
            } catch (e) {
              // Ignore canvas context errors
            }
          };
          img.src = imgUrl;
        }
      }
    });
  }

  /**
   * Carga la capa del modelo ECMWF IFS con doble búfer (sin parpadeo) y caché inteligente
   */
  async _loadEcmwfLayer(layerGroup, opacity, forceMetaFetch = false) {
    try {
      if (!this.ecmwfMetadata || forceMetaFetch) {
        const metaResp = await fetch(`${CONFIG.apiBaseUrl}/models/ecmwf/metadata?_t=${Date.now()}`);
        if (!metaResp.ok) throw new Error(`HTTP ${metaResp.status}`);
        const metadata = await metaResp.json();
        this.ecmwfMetadata = metadata;
        this._ecmwfImageCache.clear();
      }

      const metadata = this.ecmwfMetadata;
      const availSteps = metadata.available_steps || [];
      if (availSteps.length === 0) {
        if (this.uiManager && this.uiManager.updateLayerTimestamp) {
          this.uiManager.updateLayerTimestamp('ecmwf_ifs', `Estado: <strong>Sincronizando modelo...</strong>`);
        }
        return;
      }

      // Si el paso actual no está entre los disponibles, seleccionar el primero
      if (!availSteps.includes(this.currentEcmwfStep)) {
        this.currentEcmwfStep = availSteps[0];
      }

      const step = this.currentEcmwfStep;
      const type = this.currentEcmwfType || 'total';
      const stepInfo = (metadata.steps || []).find(s => s.step === step);
      const timeLabel = stepInfo ? (stepInfo.valid_time_local || `+${step}h`) : `+${step}h`;

      // Actualizar timestamp y controles en la UI de inmediato
      if (this.uiManager && this.uiManager.updateLayerTimestamp) {
        this.uiManager.updateLayerTimestamp('ecmwf_ifs', formatEcmwfTimestamp(metadata));
      }
      if (this.uiManager && this.uiManager.updateEcmwfPlayerUI) {
        this.uiManager.updateEcmwfPlayerUI(metadata, step, type, this.isEcmwfPlaying);
      }

      const bbox = metadata.bbox || { lat_min: 35.0, lat_max: 44.5, lon_min: -10.0, lon_max: 5.0 };
      const bounds = [[bbox.lat_min, bbox.lon_min], [bbox.lat_max, bbox.lon_max]];
      this.currentEcmwfBounds = bounds;

      const cacheKey = `${type}_${step}`;
      const imgUrl = this._getEcmwfImageUrl(step, type);
      const requestId = ++this._ecmwfStepRequestId;

      // Si ya tenemos los datos de canvas en caché, actualizarlos de inmediato para el cursor inspector
      const cached = this._ecmwfImageCache.get(cacheKey);
      if (cached && cached.canvasData) {
        this.ecmwfCanvasData = cached.canvasData;
      }

      // Función para reemplazar la capa overlay una vez la imagen esté completamente lista
      const swapOverlay = () => {
        if (requestId !== this._ecmwfStepRequestId) return; // Petición obsoleta descartada

        const newOverlay = L.imageOverlay(imgUrl, bounds, {
          pane: 'modelsPane',
          opacity: opacity,
          interactive: false,
          crossOrigin: 'anonymous',
          className: 'ecmwf-raster-overlay'
        });

        // Doble búfer: Añadir primero la nueva capa y luego retirar la anterior (cero parpadeo)
        layerGroup.addLayer(newOverlay);
        const oldOverlay = this.currentEcmwfOverlay;
        if (oldOverlay && oldOverlay !== newOverlay) {
          layerGroup.removeLayer(oldOverlay);
        }
        this.currentEcmwfOverlay = newOverlay;

        // Disparar precarga de pasos contiguos
        this._preloadEcmwfSteps(step, type);
      };

      // Precargar y decodificar la imagen antes de montar en Leaflet
      const offscreenImg = new Image();
      offscreenImg.crossOrigin = 'anonymous';
      offscreenImg.onload = () => {
        if (requestId !== this._ecmwfStepRequestId) return;

        try {
          const canvas = document.createElement('canvas');
          canvas.width = offscreenImg.naturalWidth;
          canvas.height = offscreenImg.naturalHeight;
          const ctx = canvas.getContext('2d', { willReadFrequently: true });
          ctx.drawImage(offscreenImg, 0, 0);
          const canvasData = {
            ctx: ctx,
            width: offscreenImg.naturalWidth,
            height: offscreenImg.naturalHeight,
            bounds: bounds,
            step: step,
            type: type,
            validText: timeLabel
          };
          this.ecmwfCanvasData = canvasData;
          this._ecmwfImageCache.set(cacheKey, {
            img: offscreenImg,
            canvasData: canvasData
          });
        } catch (err) {
          console.warn('Canvas raster ECMWF inaccesible para lectura local:', err);
        }

        swapOverlay();
      };

      offscreenImg.onerror = () => {
        if (requestId !== this._ecmwfStepRequestId) return;
        console.warn(`La imagen ECMWF IFS para paso +${step}h no pudo ser cargada.`);
      };

      offscreenImg.src = imgUrl;

      // Si la imagen ya estaba en memoria (caché del navegador), invocar onload inmediatamente
      if (offscreenImg.complete && offscreenImg.naturalWidth > 0) {
        offscreenImg.onload();
      }

    } catch (err) {
      console.warn('Error al cargar ECMWF IFS desde backend API:', err);
    }
  }

  /**
   * Cambia el paso temporal o tipo del modelo ECMWF IFS
   */
  setEcmwfStep(step, type = null) {
    const parsedStep = parseInt(step, 10);
    const targetType = type || this.currentEcmwfType || 'total';
    if (this.currentEcmwfStep === parsedStep && this.currentEcmwfType === targetType && this.currentEcmwfOverlay) {
      return;
    }
    this.currentEcmwfStep = parsedStep;
    this.currentEcmwfType = targetType;
    this.reloadEcmwfLayer();
  }

  /**
   * Cambia el tipo de visualización (total vs interval)
   */
  setEcmwfType(type) {
    if (this.currentEcmwfType === type && this.currentEcmwfOverlay) return;
    this.currentEcmwfType = type;
    this.reloadEcmwfLayer();
  }

  /**
   * Recarga la capa ECMWF con los parámetros activos
   */
  reloadEcmwfLayer(forceMetaFetch = false) {
    const ecmwfGroup = this.layers['ecmwf_ifs'];
    if (!ecmwfGroup) return;
    const opacity = (this.layerStates['ecmwf_ifs'] && this.layerStates['ecmwf_ifs'].opacity) || 0.65;
    this._loadEcmwfLayer(ecmwfGroup, opacity, forceMetaFetch);
  }

  /**
   * Inicia o detiene la reproducción automática temporal de ECMWF IFS
   */
  toggleEcmwfPlayback() {
    if (this.isEcmwfPlaying) {
      this.pauseEcmwfPlayback();
    } else {
      this.startEcmwfPlayback();
    }
  }

  startEcmwfPlayback() {
    if (this.isEcmwfPlaying) return;
    this.isEcmwfPlaying = true;
    if (this.uiManager && this.uiManager.updateEcmwfPlayState) {
      this.uiManager.updateEcmwfPlayState(true);
    }

    this.ecmwfPlaybackInterval = setInterval(() => {
      if (!this.ecmwfMetadata || !this.ecmwfMetadata.available_steps || this.ecmwfMetadata.available_steps.length === 0) {
        this.pauseEcmwfPlayback();
        return;
      }

      const steps = this.ecmwfMetadata.available_steps;
      const curIdx = steps.indexOf(this.currentEcmwfStep);
      let nextIdx = (curIdx + 1) % steps.length;
      this.setEcmwfStep(steps[nextIdx]);
    }, 1200);
  }

  pauseEcmwfPlayback() {
    this.isEcmwfPlaying = false;
    if (this.ecmwfPlaybackInterval) {
      clearInterval(this.ecmwfPlaybackInterval);
      this.ecmwfPlaybackInterval = null;
    }
    if (this.uiManager && this.uiManager.updateEcmwfPlayState) {
      this.uiManager.updateEcmwfPlayState(false);
    }
  }

  // =========================================================================
  // NOAA GFS (Global Forecast System, 0.25°) Pipeline & Interactivity
  // =========================================================================

  /**
   * Genera una URL estable para la imagen de un paso de GFS, aprovechando el caché del navegador
   */
  _getGfsImageUrl(step, type) {
    const runId = (this.gfsMetadata && (this.gfsMetadata.run_id || this.gfsMetadata.run_timestamp || this.gfsMetadata.cycle_str)) || '';
    const runParam = runId ? `&run=${encodeURIComponent(runId)}` : '';
    return `${CONFIG.apiBaseUrl}/models/gfs/image?step=${step}&type=${type}${runParam}`;
  }

  /**
   * Precarga pasos adyacentes de GFS en la memoria del navegador para transiciones instantáneas y fluidas
   */
  _preloadGfsSteps(currentStep, type) {
    if (!this.gfsMetadata || !this.gfsMetadata.available_steps) return;
    const steps = this.gfsMetadata.available_steps;
    const curIdx = steps.indexOf(currentStep);
    if (curIdx === -1) return;

    // Precargar los 5 siguientes y los 2 anteriores
    const targetIndices = [
      curIdx + 1, curIdx + 2, curIdx + 3, curIdx + 4, curIdx + 5,
      curIdx - 1, curIdx - 2
    ];

    const bbox = this.gfsMetadata.bbox || { lat_min: 35.0, lat_max: 44.5, lon_min: -10.0, lon_max: 5.0 };
    const bounds = [[bbox.lat_min, bbox.lon_min], [bbox.lat_max, bbox.lon_max]];

    targetIndices.forEach(idx => {
      if (idx >= 0 && idx < steps.length) {
        const step = steps[idx];
        const key = `${type}_${step}`;
        if (!this._gfsImageCache.has(key)) {
          const img = new Image();
          img.crossOrigin = 'anonymous';
          const imgUrl = this._getGfsImageUrl(step, type);
          img.onload = () => {
            try {
              const canvas = document.createElement('canvas');
              canvas.width = img.naturalWidth;
              canvas.height = img.naturalHeight;
              const ctx = canvas.getContext('2d', { willReadFrequently: true });
              ctx.drawImage(img, 0, 0);
              const stepInfo = (this.gfsMetadata.steps || []).find(s => s.step === step);
              const validText = stepInfo ? (stepInfo.valid_time_local || `+${step}h`) : `+${step}h`;
              this._gfsImageCache.set(key, {
                img: img,
                canvasData: {
                  ctx: ctx,
                  width: img.naturalWidth,
                  height: img.naturalHeight,
                  bounds: bounds,
                  step: step,
                  type: type,
                  validText: validText
                }
              });
            } catch (e) {
              // Ignore canvas context errors
            }
          };
          img.src = imgUrl;
        }
      }
    });
  }

  /**
   * Carga la capa del modelo NOAA GFS con doble búfer (sin parpadeo) y caché inteligente
   */
  async _loadGfsLayer(layerGroup, opacity, forceMetaFetch = false) {
    try {
      if (!this.gfsMetadata || forceMetaFetch) {
        const metaResp = await fetch(`${CONFIG.apiBaseUrl}/models/gfs/metadata?_t=${Date.now()}`);
        if (!metaResp.ok) throw new Error(`HTTP ${metaResp.status}`);
        const metadata = await metaResp.json();
        this.gfsMetadata = metadata;
        this._gfsImageCache.clear();
      }

      const metadata = this.gfsMetadata;
      const availSteps = metadata.available_steps || [];
      if (availSteps.length === 0) {
        if (this.uiManager && this.uiManager.updateLayerTimestamp) {
          this.uiManager.updateLayerTimestamp('gfs_0p25', `Estado: <strong>Sincronizando modelo...</strong>`);
        }
        return;
      }

      // Si el paso actual no está entre los disponibles, seleccionar el primero
      if (!availSteps.includes(this.currentGfsStep)) {
        this.currentGfsStep = availSteps[0];
      }

      const step = this.currentGfsStep;
      const type = this.currentGfsType || 'total';
      const stepInfo = (metadata.steps || []).find(s => s.step === step);
      const timeLabel = stepInfo ? (stepInfo.valid_time_local || `+${step}h`) : `+${step}h`;

      // Actualizar timestamp y controles en la UI de inmediato
      if (this.uiManager && this.uiManager.updateLayerTimestamp) {
        this.uiManager.updateLayerTimestamp('gfs_0p25', formatGfsTimestamp(metadata));
      }
      if (this.uiManager && this.uiManager.updateGfsPlayerUI) {
        this.uiManager.updateGfsPlayerUI(metadata, step, type, this.isGfsPlaying);
      }

      const bbox = metadata.bbox || { lat_min: 35.0, lat_max: 44.5, lon_min: -10.0, lon_max: 5.0 };
      const bounds = [[bbox.lat_min, bbox.lon_min], [bbox.lat_max, bbox.lon_max]];
      this.currentGfsBounds = bounds;

      const cacheKey = `${type}_${step}`;
      const imgUrl = this._getGfsImageUrl(step, type);
      const requestId = ++this._gfsStepRequestId;

      // Si ya tenemos los datos de canvas en caché, actualizarlos de inmediato para el cursor inspector
      const cached = this._gfsImageCache.get(cacheKey);
      if (cached && cached.canvasData) {
        this.gfsCanvasData = cached.canvasData;
      }

      // Función para reemplazar la capa overlay una vez la imagen esté completamente lista
      const swapOverlay = () => {
        if (requestId !== this._gfsStepRequestId) return; // Petición obsoleta descartada

        const newOverlay = L.imageOverlay(imgUrl, bounds, {
          pane: 'modelsPane',
          opacity: opacity,
          interactive: false,
          crossOrigin: 'anonymous',
          className: 'gfs-raster-overlay'
        });

        // Doble búfer: Añadir primero la nueva capa y luego retirar la anterior (cero parpadeo)
        layerGroup.addLayer(newOverlay);
        const oldOverlay = this.currentGfsOverlay;
        if (oldOverlay && oldOverlay !== newOverlay) {
          layerGroup.removeLayer(oldOverlay);
        }
        this.currentGfsOverlay = newOverlay;

        // Disparar precarga de pasos contiguos
        this._preloadGfsSteps(step, type);
      };

      // Precargar y decodificar la imagen antes de montar en Leaflet
      const offscreenImg = new Image();
      offscreenImg.crossOrigin = 'anonymous';
      offscreenImg.onload = () => {
        if (requestId !== this._gfsStepRequestId) return;

        try {
          const canvas = document.createElement('canvas');
          canvas.width = offscreenImg.naturalWidth;
          canvas.height = offscreenImg.naturalHeight;
          const ctx = canvas.getContext('2d', { willReadFrequently: true });
          ctx.drawImage(offscreenImg, 0, 0);
          const canvasData = {
            ctx: ctx,
            width: offscreenImg.naturalWidth,
            height: offscreenImg.naturalHeight,
            bounds: bounds,
            step: step,
            type: type,
            validText: timeLabel
          };
          this.gfsCanvasData = canvasData;
          this._gfsImageCache.set(cacheKey, {
            img: offscreenImg,
            canvasData: canvasData
          });
        } catch (err) {
          console.warn('Canvas raster GFS inaccesible para lectura local:', err);
        }

        swapOverlay();
      };

      offscreenImg.onerror = () => {
        if (requestId !== this._gfsStepRequestId) return;
        console.warn(`La imagen NOAA GFS para paso +${step}h no pudo ser cargada.`);
      };

      offscreenImg.src = imgUrl;

      // Si la imagen ya estaba en memoria (caché del navegador), invocar onload inmediatamente
      if (offscreenImg.complete && offscreenImg.naturalWidth > 0) {
        offscreenImg.onload();
      }

    } catch (err) {
      console.warn('Error al cargar NOAA GFS desde backend API:', err);
    }
  }

  /**
   * Cambia el paso temporal o tipo del modelo NOAA GFS
   */
  setGfsStep(step, type = null) {
    const parsedStep = parseInt(step, 10);
    const targetType = type || this.currentGfsType || 'total';
    if (this.currentGfsStep === parsedStep && this.currentGfsType === targetType && this.currentGfsOverlay) {
      return;
    }
    this.currentGfsStep = parsedStep;
    this.currentGfsType = targetType;
    this.reloadGfsLayer();
  }

  /**
   * Cambia el tipo de visualización (total vs interval) de GFS
   */
  setGfsType(type) {
    if (this.currentGfsType === type && this.currentGfsOverlay) return;
    this.currentGfsType = type;
    this.reloadGfsLayer();
  }

  /**
   * Recarga la capa GFS con los parámetros activos
   */
  reloadGfsLayer(forceMetaFetch = false) {
    const gfsGroup = this.layers['gfs_0p25'];
    if (!gfsGroup) return;
    const opacity = (this.layerStates['gfs_0p25'] && this.layerStates['gfs_0p25'].opacity) || 0.65;
    this._loadGfsLayer(gfsGroup, opacity, forceMetaFetch);
  }

  /**
   * Inicia o detiene la reproducción automática temporal de NOAA GFS
   */
  toggleGfsPlayback() {
    if (this.isGfsPlaying) {
      this.pauseGfsPlayback();
    } else {
      this.startGfsPlayback();
    }
  }

  startGfsPlayback() {
    if (this.isGfsPlaying) return;
    this.isGfsPlaying = true;
    if (this.uiManager && this.uiManager.updateGfsPlayState) {
      this.uiManager.updateGfsPlayState(true);
    }

    this.gfsPlaybackInterval = setInterval(() => {
      if (!this.gfsMetadata || !this.gfsMetadata.available_steps || this.gfsMetadata.available_steps.length === 0) {
        this.pauseGfsPlayback();
        return;
      }

      const steps = this.gfsMetadata.available_steps;
      const curIdx = steps.indexOf(this.currentGfsStep);
      let nextIdx = (curIdx + 1) % steps.length;
      this.setGfsStep(steps[nextIdx]);
    }, 1200);
  }

  pauseGfsPlayback() {
    this.isGfsPlaying = false;
    if (this.gfsPlaybackInterval) {
      clearInterval(this.gfsPlaybackInterval);
      this.gfsPlaybackInterval = null;
    }
    if (this.uiManager && this.uiManager.updateGfsPlayState) {
      this.uiManager.updateGfsPlayState(false);
    }
  }

  /**
   * Cambia la configuración del radar (compuesto vs único / estación) y actualiza el mapa
   */
  updateRadarMode(mode, stationId = null, autoPan = true) {
    this.currentRadarMode = mode;
    if (stationId) {
      this.currentRadarStationId = stationId;
    }
    StorageManager.setRadarMode(mode);
    if (stationId) {
      StorageManager.setRadarStationId(stationId);
    }
    this.reloadRadarLayer();

    // Si los rayos están activos, recargar inmediatamente filtrando por la nueva cobertura
    if (this.showRadarLightning) {
      this.reloadLightningLayer();
    }

    if (mode === 'single' && stationId && autoPan && this.map) {
      const stDef = CONFIG.radarStations[stationId];
      if (stDef && stDef.lat && stDef.lon) {
        this.map.flyTo([stDef.lat, stDef.lon], Math.max(this.map.getZoom(), 7.5), {
          duration: 0.7
        });
      }
    }
  }

  /**
   * Conmuta la visualización de rayos en tiempo real dentro de la cobertura del radar
   * @param {boolean} active 
   */
  toggleRadarLightning(active) {
    this.showRadarLightning = active;
    if (active) {
      if (!this.map.hasLayer(this.lightningGroup)) {
        this.lightningGroup.addTo(this.map);
      }
      this.reloadLightningLayer();
      this._startLightningSSE();
    } else {
      this.lightningGroup.clearLayers();
      if (this.map.hasLayer(this.lightningGroup)) {
        this.map.removeLayer(this.lightningGroup);
      }
      this._stopLightningSSE();
    }
  }

  /**
   * Cambia la ventana de tiempo de consulta de rayos (15m, 5m, 1m)
   * @param {number} minutes 
   */
  setLightningWindow(minutes) {
    this.lightningWindowMinutes = Math.min(15, Math.max(1, parseInt(minutes, 10) || 15));
    this.reloadLightningLayer();
  }

  /**
   * Recarga los avisos de AEMET
   */
  reloadAemetWarnings() {
    const aemetGroup = this.layers['aemet_warnings'];
    if (!aemetGroup) return;
    const opacity = (this.layerStates['aemet_warnings'] && this.layerStates['aemet_warnings'].opacity) || 0.85;
    this._loadAemetWarnings(aemetGroup, opacity);
  }

  /**
   * Recarga la capa de rayos en tiempo real
   */
  reloadLightningLayer() {
    if (!this.showRadarLightning) return;
    const radarOpacity = (this.layerStates['radar'] && this.layerStates['radar'].opacity) || 1.0;
    this._loadLightningLayer(this.lightningGroup, radarOpacity);
  }

  /**
   * Comprueba si un impacto de rayo se encuentra dentro del alcance físico del radar nacional
   * @param {Object} strike 
   * @returns {boolean}
   */
  _isStrikeInCoverage(strike) {
    const lat = strike.lat;
    const lon = strike.lon;

    // Cobertura del Compuesto Nacional (Península, Baleares y Canarias)
    const isPeninsulaBaleares = (lat >= 35.0 && lat <= 44.5 && lon >= -10.0 && lon <= 5.0);
    const isCanarias = (lat >= 27.0 && lat <= 29.8 && lon >= -18.8 && lon <= -13.0);
    return isPeninsulaBaleares || isCanarias;
  }

  /**
   * Carga los rayos recientes desde la API de FastAPI dentro de la cobertura del radar
   * @param {L.LayerGroup} layerGroup 
   * @param {number} opacity 
   */
  async _loadLightningLayer(layerGroup, opacity = 1.0) {
    if (!this.showRadarLightning) return;

    const url = `${CONFIG.apiBaseUrl}/lightning/recent?minutes=${this.lightningWindowMinutes}`;

    try {
      const response = await fetch(url);
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }
      const data = await response.json();
      const allStrikes = data.strikes || [];
      
      // Filtrar estrictamente por cobertura del radar activo
      const validStrikes = allStrikes.filter(s => this._isStrikeInCoverage(s));

      this._renderLightningStrikes(layerGroup, validStrikes, opacity);

      // Actualizar badge de estadísticas en la tarjeta de radar
      const statsBadgeEl = document.getElementById('radar-lightning-stats');
      if (statsBadgeEl) {
        statsBadgeEl.innerHTML = `⚡ <strong>${data.rate_per_min || 0}</strong> r/min · Cobertura: <strong>${validStrikes.length}</strong>`;
      }

      // Conectar stream SSE si no está activo
      this._startLightningSSE();
    } catch (err) {
      console.warn('No se pudieron cargar los datos de rayos para la cobertura del radar:', err);
    }
  }

  /**
   * Renderiza la lista de impactos sobre el mapa Leaflet
   */
  _renderLightningStrikes(layerGroup, strikes, opacity = 1.0) {
    layerGroup.clearLayers();

    strikes.forEach(strike => {
      const marker = this._createStrikeMarker(strike, opacity);
      if (marker) {
        layerGroup.addLayer(marker);
      }
    });
  }

  /**
   * Genera un CircleMarker para un impacto de rayo (ventana máx 15 min, tamaño compacto y alto contraste)
   */
  _createStrikeMarker(strike, opacity = 1.0) {
    const ageSec = strike.age_sec !== undefined ? strike.age_sec : (Date.now() / 1000 - strike.time);
    let color = '#000000'; // Borde negro nítido para máximo contraste sobre cualquier fondo
    let fillColor = '#ef4444'; // 5-15 min: Rojo vivo
    let radius = 2.2;
    let weight = 0.8;
    let fillOpacity = Math.min(1.0, opacity * 0.90);
    let className = 'lightning-marker';

    if (ageSec < 60) {
      // 0-1 min: Amarillo eléctrico puro (#ffff00) con resplandor
      fillColor = '#ffff00';
      color = '#000000';
      radius = 3.5;
      weight = 1.2;
      fillOpacity = 1.0;
      className = 'lightning-marker lightning-pulse-active';
    } else if (ageSec < 300) {
      // 1-5 min: Naranja brillante
      fillColor = '#ff9900';
      color = '#000000';
      radius = 2.8;
      weight = 1.0;
      fillOpacity = Math.min(1.0, opacity * 0.95);
    }

    const marker = L.circleMarker([strike.lat, strike.lon], {
      pane: 'lluviasPane',
      radius: radius,
      color: color,
      weight: weight,
      fillColor: fillColor,
      fillOpacity: fillOpacity,
      className: className
    });

    const strikeDate = new Date(strike.time * 1000);
    const dateFormatted = formatMadridDateTime(strikeDate);
    const sec = String(strikeDate.getSeconds()).padStart(2, '0');
    const exactTime = `${dateFormatted}:${sec}`;
    const ageMin = Math.round(ageSec / 60);
    const polarityStr = strike.pol === 1 ? 'Positiva (+)' : 'Negativa (-)';

    marker.bindPopup(`
      <div class="lightning-popup">
        <div class="lightning-popup-header">
          <span class="lightning-icon">⚡</span>
          <strong>Descarga Eléctrica</strong>
        </div>
        <div class="lightning-popup-body">
          <div><strong>Hora local:</strong> ${exactTime}</div>
          <div><strong>Antigüedad:</strong> ${ageSec < 60 ? 'Hace menos de 1 min' : `Hace ~${ageMin} min`}</div>
          <div><strong>Polaridad:</strong> ${polarityStr}</div>
          <div><strong>Estaciones:</strong> ${strike.stations || 0} receptoras</div>
          <div><strong>Coordenadas:</strong> [${strike.lat.toFixed(4)}, ${strike.lon.toFixed(4)}]</div>
        </div>
      </div>
    `, { className: 'lightning-leaflet-popup' });

    return marker;
  }

  /**
   * Inicia el stream de Server-Sent Events (SSE) para recibir rayos al milisegundo
   */
  _startLightningSSE() {
    if (this.lightningEventSource || !this.showRadarLightning) {
      return;
    }

    const sseUrl = `${CONFIG.apiBaseUrl}/lightning/stream`;
    try {
      this.lightningEventSource = new EventSource(sseUrl);
      
      this.lightningEventSource.onmessage = (event) => {
        try {
          const strike = JSON.parse(event.data);
          if (this.showRadarLightning && this._isStrikeInCoverage(strike)) {
            const radarOpacity = (this.layerStates['radar'] && this.layerStates['radar'].opacity) || 1.0;
            strike.age_sec = 0;
            const marker = this._createStrikeMarker(strike, radarOpacity);
            if (marker) {
              this.lightningGroup.addLayer(marker);
            }
          }
        } catch (e) {
          console.debug('Error procesando evento SSE de rayo:', e);
        }
      };

      this.lightningEventSource.onerror = (err) => {
        console.warn('Stream SSE de rayos desconectado. Intentando reconexión...', err);
        this._stopLightningSSE();
      };
    } catch (err) {
      console.warn('No se pudo inicializar EventSource para rayos:', err);
    }
  }

  /**
   * Inicia el stream de Server-Sent Events (SSE) para recibir actualizaciones de radar en tiempo real
   */
  _startRadarSSE() {
    if (this.radarEventSource) {
      return;
    }

    const sseUrl = `${CONFIG.apiBaseUrl}/radar/stream`;
    try {
      this.radarEventSource = new EventSource(sseUrl);

      this.radarEventSource.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          if (data && (data.event === 'radar_update' || data.event === 'radar_init')) {
            console.log('📡 Notificación SSE de radar recibida:', data.timestep);
            
            // Actualizar la fecha y hora en el HUD/tarjeta
            if (data.timestep && this.uiManager && this.uiManager.updateLayerTimestamp) {
              this.uiManager.updateLayerTimestamp('radar', `Captura: <strong>${formatMadridDateTime(data.timestep)}</strong>`);
            }

            // Recargar la imagen del radar si la capa está activa en el mapa
            if (this.layerStates['radar'] && this.layerStates['radar'].active) {
              this.reloadRadarLayer();
            }
          }
        } catch (e) {
          console.debug('Error procesando evento SSE de radar:', e);
        }
      };

      this.radarEventSource.onerror = (err) => {
        console.warn('Stream SSE de radar desconectado. Intentando reconexión en 5s...', err);
        this._stopRadarSSE();
        setTimeout(() => this._startRadarSSE(), 5000);
      };
    } catch (err) {
      console.warn('No se pudo inicializar EventSource para radar:', err);
    }
  }

  /**
   * Detiene el stream SSE de radar
   */
  _stopRadarSSE() {
    if (this.radarEventSource) {
      this.radarEventSource.close();
      this.radarEventSource = null;
    }
  }

  /**
   * Inicia el stream de Server-Sent Events (SSE) para recibir avisos de nuevos pasos/ciclos de ECMWF IFS
   */
  _startEcmwfSSE() {
    if (this.ecmwfEventSource) {
      return;
    }

    const sseUrl = `${CONFIG.apiBaseUrl}/models/ecmwf/stream`;
    try {
      this.ecmwfEventSource = new EventSource(sseUrl);

      this.ecmwfEventSource.onmessage = async (event) => {
        try {
          const data = JSON.parse(event.data);
          if (data && (data.event === 'ecmwf_update' || data.event === 'ecmwf_init')) {
            console.log('🌐 Notificación SSE ECMWF IFS recibida:', data.cycle_str, 'Pasos disponibles:', data.available_steps?.length);

            // Refrescar metadatos completos desde la API
            const metaResp = await fetch(`${CONFIG.apiBaseUrl}/models/ecmwf/metadata?_t=${Date.now()}`);
            if (metaResp.ok) {
              const metadata = await metaResp.json();
              this.ecmwfMetadata = metadata;

              const availSteps = metadata.available_steps || [];
              if (availSteps.length > 0 && !availSteps.includes(this.currentEcmwfStep)) {
                this.currentEcmwfStep = availSteps[0];
              }

              // Actualizar timestamp y estado de actualización en la tarjeta
              if (this.uiManager && this.uiManager.updateLayerTimestamp) {
                this.uiManager.updateLayerTimestamp('ecmwf_ifs', formatEcmwfTimestamp(metadata));
              }

              // Actualizar reproductor en la interfaz
              if (this.uiManager && this.uiManager.updateEcmwfPlayerUI) {
                this.uiManager.updateEcmwfPlayerUI(
                  metadata,
                  this.currentEcmwfStep,
                  this.currentEcmwfType || 'total',
                  this.isEcmwfPlaying
                );
              }

              // Si la capa está montada en el mapa, refrescar el raster
              if (this.isLayerOnMap('ecmwf_ifs')) {
                this.reloadEcmwfLayer();
              }
            }
          }
        } catch (e) {
          console.debug('Error procesando evento SSE de ECMWF:', e);
        }
      };

      this.ecmwfEventSource.onerror = (err) => {
        console.warn('Stream SSE de ECMWF IFS desconectado. Intentando reconexión en 5s...', err);
        this._stopEcmwfSSE();
        setTimeout(() => this._startEcmwfSSE(), 5000);
      };
    } catch (err) {
      console.warn('No se pudo inicializar EventSource para ECMWF IFS:', err);
    }
  }

  /**
   * Detiene el stream SSE de ECMWF IFS
   */
  _stopEcmwfSSE() {
    if (this.ecmwfEventSource) {
      this.ecmwfEventSource.close();
      this.ecmwfEventSource = null;
    }
  }

  /**
   * Inicia el stream de Server-Sent Events (SSE) para recibir avisos de nuevos pasos/ciclos de NOAA GFS
   */
  _startGfsSSE() {
    if (this.gfsEventSource) {
      return;
    }

    const sseUrl = `${CONFIG.apiBaseUrl}/models/gfs/stream`;
    try {
      this.gfsEventSource = new EventSource(sseUrl);

      this.gfsEventSource.onmessage = async (event) => {
        try {
          const data = JSON.parse(event.data);
          if (data && (data.event === 'gfs_update' || data.event === 'gfs_init')) {
            console.log('🌐 Notificación SSE NOAA GFS recibida:', data.cycle_str, 'Pasos disponibles:', data.available_steps?.length);

            // Refrescar metadatos completos desde la API
            const metaResp = await fetch(`${CONFIG.apiBaseUrl}/models/gfs/metadata?_t=${Date.now()}`);
            if (metaResp.ok) {
              const metadata = await metaResp.json();
              this.gfsMetadata = metadata;
              this._gfsImageCache.clear();

              const availSteps = metadata.available_steps || [];
              if (availSteps.length > 0 && !availSteps.includes(this.currentGfsStep)) {
                this.currentGfsStep = availSteps[0];
              }

              // Actualizar timestamp y estado de actualización en la tarjeta
              if (this.uiManager && this.uiManager.updateLayerTimestamp) {
                this.uiManager.updateLayerTimestamp('gfs_0p25', formatGfsTimestamp(metadata));
              }

              // Actualizar reproductor en la interfaz
              if (this.uiManager && this.uiManager.updateGfsPlayerUI) {
                this.uiManager.updateGfsPlayerUI(
                  metadata,
                  this.currentGfsStep,
                  this.currentGfsType || 'total',
                  this.isGfsPlaying
                );
              }

              // Si la capa está montada en el mapa, refrescar el raster
              if (this.isLayerOnMap('gfs_0p25')) {
                this.reloadGfsLayer();
              }
            }
          }
        } catch (e) {
          console.debug('Error procesando evento SSE de GFS:', e);
        }
      };

      this.gfsEventSource.onerror = (err) => {
        console.warn('Stream SSE de NOAA GFS desconectado. Intentando reconexión en 5s...', err);
        this._stopGfsSSE();
        setTimeout(() => this._startGfsSSE(), 5000);
      };
    } catch (err) {
      console.warn('No se pudo inicializar EventSource para NOAA GFS:', err);
    }
  }

  /**
   * Detiene el stream SSE de NOAA GFS
   */
  _stopGfsSSE() {
    if (this.gfsEventSource) {
      this.gfsEventSource.close();
      this.gfsEventSource = null;
    }
  }

  /**
   * Recarga la capa de caudales
   */
  reloadCaudalesLayer() {
    const group = this.layers['saih_caudales'];
    if (!group) return;
    const opacity = (this.layerStates['saih_caudales'] && this.layerStates['saih_caudales'].opacity) || 0.95;
    this._loadCaudalesLayer(group, opacity);
  }

  /**
   * Recarga la capa de embalses
   */
  reloadEmbalsesLayer() {
    const group = this.layers['saih_embalses'];
    if (!group) return;
    const opacity = (this.layerStates['saih_embalses'] && this.layerStates['saih_embalses'].opacity) || 0.95;
    this._loadEmbalsesLayer(group, opacity);
  }

  /**
   * Recarga la capa de pluviómetros / lluvias
   */
  reloadLluviasLayer() {
    const group = this.layers['saih_lluvias'];
    if (!group) return;
    const opacity = (this.layerStates['saih_lluvias'] && this.layerStates['saih_lluvias'].opacity) || 0.95;
    this._loadLluviasLayer(group, opacity);
  }

  /**
   * Configura e inicializa el autorrefresco en segundo plano para avisos AEMET, caudales, embalses y lluvias
   */
  startAutoRefresh(intervalSec = 300) {
    if (this._refreshTimer) {
      clearInterval(this._refreshTimer);
      this._refreshTimer = null;
    }

    const sec = Math.max(30, intervalSec || 300);
    console.log(`⏱️ Autorrefresco de capas SAIH/AEMET activado cada ${sec}s (${Math.round(sec / 60)} min).`);

    // Refresco periódico secundario (cada 5 min = 300s por defecto)
    this._refreshTimer = setInterval(() => {
      console.log('🔄 Ejecutando refresco automático periódico de capas SAIH / AEMET...');
      if (this.layerStates['aemet_warnings'] && this.layerStates['aemet_warnings'].active) {
        this.reloadAemetWarnings();
      }
      if (this.layerStates['saih_caudales'] && this.layerStates['saih_caudales'].active) {
        this.reloadCaudalesLayer();
      }
      if (this.layerStates['saih_embalses'] && this.layerStates['saih_embalses'].active) {
        this.reloadEmbalsesLayer();
      }
      if (this.layerStates['saih_lluvias'] && this.layerStates['saih_lluvias'].active) {
        this.reloadLluviasLayer();
      }
    }, sec * 1000);
  }



  /**
   * Carga asíncrona de estaciones de medición de caudal del SAIH Júcar (CHJ)
   */
  async _loadCaudalesLayer(layerGroup, opacity) {
    const apiUrl = `${CONFIG.apiBaseUrl}/saih/caudales?format=geojson&_t=${Date.now()}`;
    const fallbackUrls = [
      './data/saih_aforos.geojson',
      './saih_aforos.geojson',
      './backend/data/saih_aforos.geojson'
    ];

    let geojson = null;
    try {
      const resp = await fetch(apiUrl);
      if (resp.ok) {
        geojson = await resp.json();
      }
    } catch (err) {
      console.warn('No se pudo conectar a la API de caudales, buscando en rutas locales:', err);
    }

    if (!geojson) {
      for (const fbUrl of fallbackUrls) {
        try {
          const fbResp = await fetch(fbUrl);
          if (fbResp.ok) {
            geojson = await fbResp.json();
            break;
          }
        } catch (fbErr) {
          // continuar con el siguiente
        }
      }
    }

    if (!geojson || !geojson.features) return;

    this._caudalesFeatures = geojson.features;
    layerGroup.clearLayers();

    const caudalesGeoJSON = L.geoJSON(geojson, {
      pane: 'caudalesPane',
      pointToLayer: (feature, latlng) => {
        const props = feature.properties || {};
        props.lat = latlng.lat;
        props.lon = latlng.lng;
        const rawCaudal = props.ultimo_caudal !== undefined ? props.ultimo_caudal : (props.caudal !== undefined ? props.caudal : props.lastValue);
        const caudal = (rawCaudal !== null && rawCaudal !== undefined && rawCaudal !== '' && !isNaN(Number(rawCaudal))) ? Number(rawCaudal) : null;
        const umbrales = props.umbrales || {};
        const uAmarillo = umbrales.amarillo ? Number(umbrales.amarillo) : null;
        const uNaranja = umbrales.naranja ? Number(umbrales.naranja) : null;
        const uRojo = umbrales.rojo ? Number(umbrales.rojo) : null;

        // Determinar nivel de alerta y color
        let color = '#10b981'; // Normal (Verde)
        let alertClass = 'caudal-status-normal';
        let alertLevelText = 'Normal';

        if (caudal !== null) {
          if (uRojo !== null && caudal >= uRojo) {
            color = '#ef4444';
            alertClass = 'caudal-status-red caudal-pulse';
            alertLevelText = 'Umbral Rojo (Desbordamiento)';
          } else if (uNaranja !== null && caudal >= uNaranja) {
            color = '#f97316';
            alertClass = 'caudal-status-orange caudal-pulse';
            alertLevelText = 'Umbral Naranja (Muy Alto)';
          } else if (uAmarillo !== null && caudal >= uAmarillo) {
            color = '#f59e0b';
            alertClass = 'caudal-status-yellow';
            alertLevelText = 'Umbral Amarillo (Precaución)';
          }
        } else {
          color = '#94a3b8';
          alertClass = 'caudal-status-nodata';
          alertLevelText = 'Sin dato actual';
        }

        const marker = L.circleMarker(latlng, {
          pane: 'caudalesPane',
          radius: 6.5,
          color: '#ffffff',
          weight: 2,
          fillColor: color,
          fillOpacity: Math.min(1.0, opacity * 0.95),
          className: `caudal-marker ${alertClass}`,
          interactive: true
        });

        // Click con prioridad máxima (evita propagar evento a la cuenca y al mapa)
        marker.on('click', (e) => {
          if (e) {
            if (e.originalEvent) {
              e.originalEvent._caudalMarkerClicked = true;
              e.originalEvent._stopBasinClick = true;
              if (e.originalEvent.stopPropagation) e.originalEvent.stopPropagation();
              if (e.originalEvent.stopImmediatePropagation) e.originalEvent.stopImmediatePropagation();
            }
            L.DomEvent.stopPropagation(e);
            L.DomEvent.preventDefault(e);
          }
          if (this.map && this.map.closePopup) {
            this.map.closePopup();
          }
          this.openCaudalHistoryModal(props, 24);
        });

        return marker;
      }
    });

    layerGroup.addLayer(caudalesGeoJSON);

    if (this.uiManager && this.uiManager.updateLayerTimestamp) {
      this.uiManager.updateLayerTimestamp('saih_caudales', `Actualizado: <strong>${formatMadridDateTime(new Date())}</strong>`);
    }
  }

  /**
   * Abre el modal de evolución histórica del caudal y consulta la API
   */
  async openCaudalHistoryModal(props, initialHours = 24, cachedNearbyEntities = null) {
    const backdrop = document.getElementById('caudal-modal-backdrop');
    if (!backdrop) return;

    this._currentModalStationProps = props;
    this._currentModalHours = initialHours;

    // Buscar y renderizar elementos próximos (Embalses o estaciones adyacentes)
    let nearbyEntities = cachedNearbyEntities;
    if (!nearbyEntities) {
      const lat = props.lat ?? props.latitud;
      const lon = props.lon ?? props.longitud;
      nearbyEntities = await this._findNearbySaihEntities(lat, lon, props.codigo);
    }
    this._renderProximityTabs('caudal-modal-proximity-tabs', nearbyEntities, 'caudal', props.id_variable || props.codigo);

    // Elementos del modal
    const alertBadge = document.getElementById('caudal-modal-alert-badge');
    const subcuencaBadge = document.getElementById('caudal-modal-subcuenca-badge');
    const titleEl = document.getElementById('caudal-modal-title');
    const subtitleEl = document.getElementById('caudal-modal-subtitle');
    const statCurrent = document.getElementById('caudal-stat-current');
    const statTime = document.getElementById('caudal-stat-time');
    const saihLink = document.getElementById('caudal-saih-link');
    const closeBtn = document.getElementById('caudal-modal-close');
    const rangeButtons = document.querySelectorAll('#caudal-range-buttons .btn-range');

    // Umbrales
    const umbrales = props.umbrales || {};
    const uAmarillo = umbrales.amarillo ? Number(umbrales.amarillo) : null;
    const uNaranja = umbrales.naranja ? Number(umbrales.naranja) : null;
    const uRojo = umbrales.rojo ? Number(umbrales.rojo) : null;

    // Calcular estado
    const rawCaudal = props.ultimo_caudal !== undefined ? props.ultimo_caudal : (props.caudal !== undefined ? props.caudal : props.lastValue);
    const caudal = (rawCaudal !== null && rawCaudal !== undefined && rawCaudal !== '' && !isNaN(Number(rawCaudal))) ? Number(rawCaudal) : null;

    let alertColor = '#10b981';
    let alertText = 'Caudal Normal';
    if (caudal !== null) {
      if (uRojo !== null && caudal >= uRojo) {
        alertColor = '#ef4444';
        alertText = '🔴 Umbral Rojo (Desbordamiento)';
      } else if (uNaranja !== null && caudal >= uNaranja) {
        alertColor = '#f97316';
        alertText = '🟠 Umbral Naranja (Peligro)';
      } else if (uAmarillo !== null && caudal >= uAmarillo) {
        alertColor = '#f59e0b';
        alertText = '🟡 Umbral Amarillo (Aviso)';
      }
    } else {
      alertColor = '#94a3b8';
      alertText = 'Sin dato actual';
    }

    if (alertBadge) {
      alertBadge.textContent = alertText;
      alertBadge.style.backgroundColor = `${alertColor}22`;
      alertBadge.style.color = alertColor;
      alertBadge.style.borderColor = `${alertColor}66`;
    }

    if (subcuencaBadge) {
      subcuencaBadge.textContent = props.subcuenca ? `Cuenca: ${props.subcuenca}` : 'Demarcación CHJ';
    }

    if (titleEl) titleEl.textContent = props.nombre || 'Estación de Caudal';
    if (subtitleEl) subtitleEl.textContent = `${props.variable || 'Caudal'} · ${props.poblacion || '--'} (${props.provincia || ''}) · Código SAIH: ${props.codigo || '--'}`;

    if (statCurrent) {
      statCurrent.textContent = caudal !== null ? `${caudal.toFixed(2)} m³/s` : '-- m³/s';
      statCurrent.style.color = alertColor;
    }
    if (statTime) {
      statTime.textContent = props.ultima_hora ? `Última lectura: ${props.ultima_hora}` : 'Lectura en tiempo real';
    }

    // Chips de umbrales
    const chipY = document.getElementById('th-chip-yellow');
    const chipO = document.getElementById('th-chip-orange');
    const chipR = document.getElementById('th-chip-red');
    if (chipY) chipY.textContent = `🟡 Amarillo: ${uAmarillo ? uAmarillo + ' m³/s' : '--'}`;
    if (chipO) chipO.textContent = `🟠 Naranja: ${uNaranja ? uNaranja + ' m³/s' : '--'}`;
    if (chipR) chipR.textContent = `🔴 Rojo: ${uRojo ? uRojo + ' m³/s' : '--'}`;

    if (saihLink) {
      saihLink.href = `https://saih.chj.es/aforos/${props.id_variable}/chart`;
    }

    // Configurar botones de rango temporal (12h, 24h, 48h, 7d)
    rangeButtons.forEach(btn => {
      const h = Number(btn.getAttribute('data-hours'));
      btn.classList.toggle('active', h === initialHours);
      btn.onclick = (ev) => {
        ev.stopPropagation();
        rangeButtons.forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        this.loadCaudalHistoryForModal(props.id_variable, h, umbrales);
      };
    });

    // Cerrar modal
    const closeModal = () => {
      backdrop.style.display = 'none';
      backdrop.classList.remove('no-anim');
      const otherBackdrop = document.getElementById('embalse-modal-backdrop');
      if (otherBackdrop) otherBackdrop.classList.remove('no-anim');
      backdrop.setAttribute('aria-hidden', 'true');
      document.removeEventListener('keydown', onKeyDown);
    };

    const onKeyDown = (e) => {
      if (e.key === 'Escape') closeModal();
    };

    if (closeBtn) closeBtn.onclick = closeModal;
    backdrop.onclick = (e) => {
      if (e.target === backdrop) closeModal();
    };
    document.addEventListener('keydown', onKeyDown);

    // Mostrar modal y cargar gráfica
    backdrop.style.display = 'flex';
    backdrop.setAttribute('aria-hidden', 'false');

    await this.loadCaudalHistoryForModal(props.id_variable, initialHours, umbrales);
  }

  /**
   * Carga los datos de la serie temporal desde la API y genera la gráfica interactiva del modal
   */
  async loadCaudalHistoryForModal(idVariable, hours, umbrales) {
    const container = document.getElementById('caudal-chart-canvas-container');
    const statMax = document.getElementById('caudal-stat-max');
    const statMaxTime = document.getElementById('caudal-stat-max-time');
    const statMin = document.getElementById('caudal-stat-min');
    const statAvg = document.getElementById('caudal-stat-avg');

    if (!container) return;
    container.innerHTML = `
      <div class="caudal-chart-modal-loading">
        <div class="caudal-spinner"></div>
        <span>Consultando serie temporal de caudal en la API del SAIH (últimas ${hours}h)...</span>
      </div>
    `;

    try {
      const resp = await fetch(`${CONFIG.apiBaseUrl}/saih/caudales/${idVariable}/history?hours=${hours}`);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      const series = data.serie || [];

      if (series.length === 0) {
        container.innerHTML = `<div class="caudal-chart-nodata">No se encontraron lecturas de caudal registradas en las últimas ${hours} horas.</div>`;
        if (statMax) statMax.textContent = '-- m³/s';
        if (statMin) statMin.textContent = '-- m³/s';
        return;
      }

      // Estadísticas
      const validPoints = series.filter(s => s.valor !== null && s.valor !== undefined);
      const values = validPoints.map(s => Number(s.valor));

      if (values.length > 0) {
        const maxVal = Math.max(...values);
        const minVal = Math.min(...values);
        const avgVal = values.reduce((a, b) => a + b, 0) / values.length;

        const maxPoint = validPoints.find(p => Number(p.valor) === maxVal);
        const maxTimeStr = maxPoint && maxPoint.fecha ? formatMadridDateTime(new Date(maxPoint.fecha)) : '';

        if (statMax) statMax.textContent = `${maxVal.toFixed(2)} m³/s`;
        if (statMaxTime) statMaxTime.textContent = maxTimeStr ? `Registrado: ${maxTimeStr}` : 'Pico del periodo';
        if (statMin) statMin.textContent = `${minVal.toFixed(2)} m³/s`;
        if (statAvg) statAvg.textContent = `Media: ${avgVal.toFixed(2)} m³/s`;
      }

      // Renderizar gráfica SVG de alta resolución con líneas de aviso horizontales
      this._renderCaudalSvgChart(validPoints, umbrales, container);

    } catch (err) {
      console.warn('Error al obtener histórico de caudal:', err);
      container.innerHTML = `
        <div class="caudal-chart-error-banner">
          <strong>No se pudo conectar con la API de caudales en tiempo real</strong>
          <span>Asegúrate de que el servidor backend (<code>python backend/run.py</code>) esté ejecutándose en el puerto 8000.</span>
        </div>
      `;
    }
  }

  /**
   * Genera el SVG interactivo con curva de caudal y líneas horizontales de avisos
   */
  _renderCaudalSvgChart(points, umbrales, containerEl) {
    if (!points || points.length === 0) return;

    const values = points.map(p => Number(p.valor));
    const uA = umbrales.amarillo ? Number(umbrales.amarillo) : null;
    const uN = umbrales.naranja ? Number(umbrales.naranja) : null;
    const uR = umbrales.rojo ? Number(umbrales.rojo) : null;

    // Calcular límites Y (incluyendo umbrales relevantes en la escala)
    const rawMax = Math.max(...values, 0.1);
    const thresholdMax = Math.max(uA || 0, uN || 0, uR || 0);
    // Escala Y con margen superior para claridad visual
    let maxY = Math.max(rawMax * 1.25, 0.5);
    if (thresholdMax > 0 && rawMax >= thresholdMax * 0.4) {
      maxY = Math.max(maxY, thresholdMax * 1.15);
    } else if (uA && rawMax >= uA * 0.5) {
      maxY = Math.max(maxY, uA * 1.2);
    }
    const minY = 0;
    const rangeY = maxY - minY || 1;

    // Dimensiones del viewBox SVG
    const width = 720;
    const height = 240;
    const pad = { top: 25, right: 30, bottom: 35, left: 55 };
    const innerW = width - pad.left - pad.right;
    const innerH = height - pad.top - pad.bottom;

    // Puntos de la polilínea y área
    const chartCoords = points.map((pt, i) => {
      const x = pad.left + (i / Math.max(1, points.length - 1)) * innerW;
      const y = pad.top + innerH - ((Number(pt.valor) - minY) / rangeY) * innerH;
      return { x, y, pt };
    });

    const polyPointsStr = chartCoords.map(c => `${c.x.toFixed(1)},${c.y.toFixed(1)}`).join(' ');
    const baseY = (pad.top + innerH).toFixed(1);
    const areaPointsStr = `${pad.left},${baseY} ${polyPointsStr} ${pad.left + innerW},${baseY}`;

    // Líneas Horizontales de Umbrales / Avisos de Caudal
    const thresholdLines = [];
    const makeThresholdLine = (val, color, name) => {
      if (val === null || isNaN(val) || val > maxY || val < minY) return '';
      const y = pad.top + innerH - ((val - minY) / rangeY) * innerH;
      const yF = y.toFixed(1);
      return `
        <!-- Línea ${name} -->
        <g class="chart-threshold-group">
          <line x1="${pad.left}" y1="${yF}" x2="${pad.left + innerW}" y2="${yF}"
                stroke="${color}" stroke-dasharray="5,4" stroke-width="1.6" opacity="0.9"/>
          <!-- Etiqueta sobre la línea -->
          <rect x="${pad.left + 8}" y="${(y - 14).toFixed(1)}" width="140" height="15" rx="3" fill="rgba(15,23,42,0.85)" stroke="${color}" stroke-width="0.8"/>
          <text x="${pad.left + 14}" y="${(y - 3).toFixed(1)}" fill="${color}" font-size="10" font-weight="700">
            ${name}: ${val} m³/s
          </text>
        </g>
      `;
    };

    if (uA) thresholdLines.push(makeThresholdLine(uA, '#f59e0b', '🟡 Aviso Amarillo'));
    if (uN) thresholdLines.push(makeThresholdLine(uN, '#f97316', '🟠 Aviso Naranja'));
    if (uR) thresholdLines.push(makeThresholdLine(uR, '#ef4444', '🔴 Umbral Rojo'));

    // Cuadrícula y Ejes Y (4 divisiones)
    const gridLines = [];
    const yLabels = [];
    for (let i = 0; i <= 4; i++) {
      const val = minY + (rangeY * (i / 4));
      const y = pad.top + innerH - (innerH * (i / 4));
      const yF = y.toFixed(1);
      gridLines.push(`<line x1="${pad.left}" y1="${yF}" x2="${pad.left + innerW}" y2="${yF}" stroke="rgba(255,255,255,0.07)" stroke-width="1"/>`);
      yLabels.push(`<text x="${pad.left - 8}" y="${(y + 3.5).toFixed(1)}" fill="#94a3b8" font-size="10" font-weight="500" text-anchor="end">${val >= 10 ? val.toFixed(1) : val.toFixed(2)}</text>`);
    }

    // Ejes X y Fechas (5 etiquetas equidistantes)
    const xLabels = [];
    const numXMarks = Math.min(6, points.length);
    for (let k = 0; k < numXMarks; k++) {
      const idx = Math.round((k / (numXMarks - 1)) * (points.length - 1));
      const pt = points[idx];
      const x = pad.left + (idx / Math.max(1, points.length - 1)) * innerW;
      const d = pt.fecha ? new Date(pt.fecha) : new Date();
      const timeLabel = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
      const dateLabel = `${d.getDate()}/${d.getMonth() + 1}`;
      xLabels.push(`
        <g transform="translate(${x.toFixed(1)}, ${height - pad.bottom + 14})">
          <text x="0" y="0" fill="#94a3b8" font-size="9.5" text-anchor="middle">${timeLabel}</text>
          <text x="0" y="11" fill="#64748b" font-size="8.5" text-anchor="middle">${dateLabel}</text>
        </g>
      `);
    }

    // Identificador único para el gradiente
    const gradId = `caudal-grad-chart-${Date.now()}`;

    containerEl.innerHTML = `
      <div class="caudal-svg-chart-container">
        <svg viewBox="0 0 ${width} ${height}" class="caudal-modal-svg" id="caudal-interactive-svg" preserveAspectRatio="xMidYMid meet">
          <defs>
            <linearGradient id="${gradId}" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stop-color="#0284c7" stop-opacity="0.6"/>
              <stop offset="60%" stop-color="#0284c7" stop-opacity="0.15"/>
              <stop offset="100%" stop-color="#0284c7" stop-opacity="0.0"/>
            </linearGradient>
          </defs>

          <!-- Cuadrícula de fondo -->
          ${gridLines.join('')}

          <!-- Eje Y etiquetas -->
          ${yLabels.join('')}
          <text x="${pad.left}" y="${pad.top - 8}" fill="#38bdf8" font-size="10.5" font-weight="700">m³/s</text>

          <!-- Eje X etiquetas -->
          ${xLabels.join('')}

          <!-- Líneas de Umbrales Horizontales (Avisos de Caudal) -->
          ${thresholdLines.join('')}

          <!-- Área sombreada bajo la curva -->
          <polygon points="${areaPointsStr}" fill="url(#${gradId})"/>

          <!-- Línea de Caudal Circulante -->
          <polyline points="${polyPointsStr}" fill="none" stroke="#38bdf8" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"/>

          <!-- Punto final / lectura más reciente -->
          ${chartCoords.length > 0 ? `
            <circle cx="${chartCoords[chartCoords.length - 1].x.toFixed(1)}" cy="${chartCoords[chartCoords.length - 1].y.toFixed(1)}" r="4.5" fill="#38bdf8" stroke="#ffffff" stroke-width="2"/>
          ` : ''}

          <!-- Elementos de Hover interactivo -->
          <g id="caudal-chart-hover-group" style="display: none; pointer-events: none;">
            <line id="caudal-chart-guideline" x1="0" y1="${pad.top}" x2="0" y2="${pad.top + innerH}"
                  stroke="#38bdf8" stroke-dasharray="3,3" stroke-width="1.4" opacity="0.85"/>
            <circle id="caudal-chart-hover-dot" cx="0" cy="0" r="5.5" fill="#38bdf8" stroke="#ffffff" stroke-width="2.5"/>
          </g>

          <!-- Zona de captura de ratón -->
          <rect id="caudal-chart-hitbox" x="${pad.left}" y="${pad.top}" width="${innerW}" height="${innerH}"
                fill="transparent" cursor="crosshair" style="pointer-events: all;"/>
        </svg>

        <!-- Tooltip flotante interactivo (permite desbordamiento visible fuera de la gráfica) -->
        <div id="caudal-chart-tooltip" class="caudal-chart-hover-tooltip" aria-hidden="true">
          <div class="caudal-chart-tooltip-header">
            <span>📅</span>
            <span id="caudal-tt-time">--:--</span>
          </div>
          <div class="caudal-chart-tooltip-val-row">
            <span id="caudal-tt-val" class="caudal-chart-tooltip-val" style="color: #38bdf8;">-- m³/s</span>
            <span id="caudal-tt-badge" class="caudal-chart-tooltip-badge" style="display: none;"></span>
          </div>
        </div>
      </div>
    `;

    // Vincular interactividad de hover al SVG
    const svgEl = containerEl.querySelector('#caudal-interactive-svg');
    const hitbox = containerEl.querySelector('#caudal-chart-hitbox');
    const hoverGroup = containerEl.querySelector('#caudal-chart-hover-group');
    const guideline = containerEl.querySelector('#caudal-chart-guideline');
    const hoverDot = containerEl.querySelector('#caudal-chart-hover-dot');
    const tooltip = containerEl.querySelector('#caudal-chart-tooltip');
    const ttTime = containerEl.querySelector('#caudal-tt-time');
    const ttVal = containerEl.querySelector('#caudal-tt-val');
    const ttBadge = containerEl.querySelector('#caudal-tt-badge');

    if (!hitbox || !hoverGroup || !svgEl || !tooltip) return;

    const onHoverMove = (e) => {
      const rect = svgEl.getBoundingClientRect();
      const clientX = e.clientX - rect.left;
      const svgX = (clientX / rect.width) * width;

      // Restringir a la zona de datos
      const clampedSvgX = Math.max(pad.left, Math.min(pad.left + innerW, svgX));
      const fraction = (clampedSvgX - pad.left) / innerW;
      const idx = Math.round(fraction * (chartCoords.length - 1));
      const target = chartCoords[Math.max(0, Math.min(chartCoords.length - 1, idx))];

      if (!target) return;

      const val = Number(target.pt.valor);
      const fecha = target.pt.fecha ? new Date(target.pt.fecha) : new Date();

      // Determinar color de aviso y umbral
      let pointColor = '#38bdf8';
      let isWarning = false;
      let statusText = '';
      let bgBadge = '';
      let textBadge = '';

      if (uR !== null && val >= uR) {
        pointColor = '#ef4444';
        statusText = '🔴 Umbral Rojo';
        bgBadge = 'rgba(239, 68, 68, 0.25)';
        textBadge = '#fca5a5';
        isWarning = true;
      } else if (uN !== null && val >= uN) {
        pointColor = '#f97316';
        statusText = '🟠 Aviso Naranja';
        bgBadge = 'rgba(249, 115, 22, 0.25)';
        textBadge = '#fdba74';
        isWarning = true;
      } else if (uA !== null && val >= uA) {
        pointColor = '#f59e0b';
        statusText = '🟡 Aviso Amarillo';
        bgBadge = 'rgba(245, 158, 11, 0.25)';
        textBadge = '#fde68a';
        isWarning = true;
      }

      // Actualizar línea guía y punto resaltado en el SVG
      guideline.setAttribute('x1', target.x.toFixed(1));
      guideline.setAttribute('x2', target.x.toFixed(1));
      guideline.setAttribute('stroke', pointColor);

      hoverDot.setAttribute('cx', target.x.toFixed(1));
      hoverDot.setAttribute('cy', target.y.toFixed(1));
      hoverDot.setAttribute('fill', pointColor);

      hoverGroup.style.display = 'block';

      // Actualizar contenido del tooltip con color de aviso en el número
      ttTime.textContent = formatMadridDateTime(fecha);
      ttVal.textContent = `${val.toFixed(2)} m³/s`;
      ttVal.style.color = pointColor;

      // Si está en umbral de aviso mostrar el badge correspondiente, si es normal no mostrar ningún badge
      if (isWarning) {
        ttBadge.style.display = 'inline-block';
        ttBadge.textContent = statusText;
        ttBadge.style.backgroundColor = bgBadge;
        ttBadge.style.color = textBadge;
        ttBadge.style.border = `1px solid ${pointColor}60`;
      } else {
        ttBadge.style.display = 'none';
      }

      // Posicionar tooltip relativo al contenedor del gráfico
      const pctX = (target.x / width) * 100;
      const pctY = (target.y / height) * 100;

      tooltip.style.left = `${pctX}%`;
      tooltip.style.top = `${pctY}%`;

      // Evitar que el tooltip se corte horizontalmente en los bordes izquierdo/derecho
      let transformX = '-50%';
      if (pctX < 20) {
        transformX = '-10%';
      } else if (pctX > 80) {
        transformX = '-90%';
      }

      tooltip.style.transform = `translate(${transformX}, -120%)`;
      tooltip.classList.add('is-visible');
    };

    const onHoverLeave = () => {
      hoverGroup.style.display = 'none';
      tooltip.classList.remove('is-visible');
    };

    hitbox.addEventListener('mousemove', onHoverMove);
    hitbox.addEventListener('mouseenter', onHoverMove);
    hitbox.addEventListener('mouseleave', onHoverLeave);
    svgEl.addEventListener('mouseleave', onHoverLeave);
  }

  /**
   * Carga asíncrona de estaciones de embalses y presas del SAIH Júcar (CHJ)
   */
  async _loadEmbalsesLayer(layerGroup, opacity) {
    const apiUrl = `${CONFIG.apiBaseUrl}/saih/embalses?format=geojson&_t=${Date.now()}`;
    const fallbackUrls = [
      './data/saih_embalses.geojson',
      './saih_embalses.geojson',
      './backend/data/saih_embalses.geojson'
    ];

    let geojson = null;
    try {
      const resp = await fetch(apiUrl);
      if (resp.ok) {
        geojson = await resp.json();
      }
    } catch (err) {
      console.warn('API de embalses no accesible directamente, buscando fallback local...');
    }

    if (!geojson || !geojson.features) {
      for (const url of fallbackUrls) {
        try {
          const resp = await fetch(url);
          if (resp.ok) {
            geojson = await resp.json();
            break;
          }
        } catch (e) {}
      }
    }

    if (!geojson || !geojson.features) {
      console.warn('No se pudieron cargar datos de embalses.');
      return;
    }

    this._embalsesFeatures = geojson.features;
    layerGroup.clearLayers();

    const embalsesGeoJSON = L.geoJSON(geojson, {
      pane: 'embalsesPane',
      pointToLayer: (feature, latlng) => {
        const props = feature.properties || {};
        props.lat = latlng.lat;
        props.lon = latlng.lng;
        const vol = props.volumen_actual !== null && props.volumen_actual !== undefined ? Number(props.volumen_actual) : null;
        const cap = props.capacidad_nmn !== null && props.capacidad_nmn !== undefined ? Number(props.capacidad_nmn) : null;
        const pct = props.porcentaje_llenado !== null && props.porcentaje_llenado !== undefined ? Number(props.porcentaje_llenado) : (vol !== null && cap ? (vol / cap * 100) : null);

        // Color según peligrosidad de desbordamiento: verde <35%, amarillo 35-70%, rojo >=70%
        let color = '#10b981'; // Verde (<35%) - Desbordamiento improbable
        let alertClass = 'embalse-status-low';

        if (pct !== null) {
          if (pct >= 70) {
            color = '#ef4444'; // Rojo (>=70%) - Alto riesgo / Desbordamiento
            alertClass = 'embalse-status-critical caudal-pulse';
          } else if (pct >= 35) {
            color = '#f59e0b'; // Amarillo (35-70%) - Riesgo moderado
            alertClass = 'embalse-status-medium';
          } else {
            color = '#10b981'; // Verde (<35%) - Riesgo bajo
            alertClass = 'embalse-status-low';
          }
        } else {
          color = '#94a3b8';
          alertClass = 'embalse-status-nodata';
        }

        // Tamaño uniforme de bola grande para todos los embalses
        const radius = 10.5;

        const marker = L.circleMarker(latlng, {
          pane: 'embalsesPane',
          radius: radius,
          color: '#ffffff',
          weight: 2.2,
          fillColor: color,
          fillOpacity: Math.min(1.0, opacity * 0.95),
          className: `embalse-marker ${alertClass}`,
          interactive: true
        });

        // Click con prioridad máxima (evita propagar evento a la cuenca y al mapa)
        marker.on('click', (e) => {
          if (e) {
            if (e.originalEvent) {
              e.originalEvent._embalseMarkerClicked = true;
              e.originalEvent._stopBasinClick = true;
              if (e.originalEvent.stopPropagation) e.originalEvent.stopPropagation();
              if (e.originalEvent.stopImmediatePropagation) e.originalEvent.stopImmediatePropagation();
            }
            L.DomEvent.stopPropagation(e);
            L.DomEvent.preventDefault(e);
          }
          if (this.map && this.map.closePopup) {
            this.map.closePopup();
          }
          this.openEmbalseHistoryModal(props, 24);
        });

        return marker;
      }
    });

    layerGroup.addLayer(embalsesGeoJSON);

    if (this.uiManager && this.uiManager.updateLayerTimestamp) {
      this.uiManager.updateLayerTimestamp('saih_embalses', `Actualizado: <strong>${formatMadridDateTime(new Date())}</strong>`);
    }
  }

  /**
   * Carga asíncrona de estaciones pluviométricas / lluvia del SAIH Júcar (CHJ)
   */
  async _loadLluviasLayer(layerGroup, opacity) {
    const apiUrl = `${CONFIG.apiBaseUrl}/saih/lluvias?format=geojson&_t=${Date.now()}`;
    const fallbackUrls = [
      './data/saih_lluvias.geojson',
      './saih_lluvias.geojson',
      './backend/data/saih_lluvias.geojson'
    ];

    let geojson = null;
    try {
      const resp = await fetch(apiUrl);
      if (resp.ok) {
        geojson = await resp.json();
      }
    } catch (err) {
      console.warn('API de pluviómetros no accesible directamente, buscando fallback local...');
    }

    if (!geojson || !geojson.features) {
      for (const url of fallbackUrls) {
        try {
          const resp = await fetch(url);
          if (resp.ok) {
            geojson = await resp.json();
            break;
          }
        } catch (e) {}
      }
    }

    if (!geojson || !geojson.features) {
      console.warn('No se pudieron cargar datos de pluviómetros.');
      return;
    }

    this._lluviasFeatures = geojson.features;
    layerGroup.clearLayers();

    const lluviasGeoJSON = L.geoJSON(geojson, {
      pane: 'lluviasPane',
      pointToLayer: (feature, latlng) => {
        const props = feature.properties || {};
        props.lat = latlng.lat;
        props.lon = latlng.lng;

        const r1h = props.lluvia_1h !== null && props.lluvia_1h !== undefined ? Number(props.lluvia_1h) : 0;
        const r4h = props.lluvia_4h !== null && props.lluvia_4h !== undefined ? Number(props.lluvia_4h) : 0;
        const r12h = props.lluvia_12h !== null && props.lluvia_12h !== undefined ? Number(props.lluvia_12h) : 0;
        const r24h = props.lluvia_24h !== null && props.lluvia_24h !== undefined ? Number(props.lluvia_24h) : 0;

        // Escala de colores según precipitación acumulada
        let color = '#64748b'; // 0 mm (gris pizarra discreto)
        let alertClass = 'pluvio-status-zero';

        if (r24h >= 100 || r1h >= 20) {
          color = '#ef4444'; // Rojo / Torrencial
          alertClass = 'pluvio-status-extreme caudal-pulse';
        } else if (r24h >= 60 || r1h >= 10) {
          color = '#f97316'; // Naranja / Muy fuerte
          alertClass = 'pluvio-status-heavy';
        } else if (r24h >= 30 || r1h >= 5) {
          color = '#eab308'; // Amarillo / Fuerte
          alertClass = 'pluvio-status-mod';
        } else if (r24h >= 10) {
          color = '#0284c7'; // Azul / Moderada
          alertClass = 'pluvio-status-light';
        } else if (r24h > 0 || r1h > 0) {
          color = '#38bdf8'; // Celeste / Débil
          alertClass = 'pluvio-status-light';
        }

        // Tamaño uniforme de bola pequeña para todos los pluviómetros
        const radius = 3.8;

        const marker = L.circleMarker(latlng, {
          pane: 'lluviasPane',
          radius: radius,
          color: '#ffffff',
          weight: 1.2,
          fillColor: color,
          fillOpacity: Math.min(1.0, opacity * 0.92),
          className: `pluvio-marker ${alertClass}`,
          interactive: true
        });

        // IMPORTANTE: Los puntos de lluvia NO son clickables (no abren popup/modal),
        // pero interceptan el click para que no se seleccione la cuenca inferior accidentalmente.
        marker.on('click', (e) => {
          if (e) {
            if (e.originalEvent) {
              e.originalEvent._pluvioMarkerClicked = true;
              e.originalEvent._stopBasinClick = true;
              if (e.originalEvent.stopPropagation) e.originalEvent.stopPropagation();
              if (e.originalEvent.stopImmediatePropagation) e.originalEvent.stopImmediatePropagation();
            }
            L.DomEvent.stopPropagation(e);
            L.DomEvent.preventDefault(e);
          }
        });

        return marker;
      }
    });

    layerGroup.addLayer(lluviasGeoJSON);

    if (this.uiManager && this.uiManager.updateLayerTimestamp) {
      this.uiManager.updateLayerTimestamp('saih_lluvias', `Actualizado: <strong>${formatMadridDateTime(new Date())}</strong>`);
    }
  }

  /**
   * Garantiza que ambos catálogos (caudales y embalses) estén cargados para búsqueda espacial de proximidad
   */
  async _ensureSaihCatalogsLoaded() {
    const promises = [];
    if (!this._caudalesFeatures || this._caudalesFeatures.length === 0) {
      promises.push((async () => {
        try {
          const resp = await fetch(`${CONFIG.apiBaseUrl}/saih/caudales?format=geojson`);
          if (resp.ok) {
            const data = await resp.json();
            this._caudalesFeatures = data.features || [];
            return;
          }
        } catch (e) {}
        try {
          const resp = await fetch('./backend/data/saih_aforos.geojson');
          if (resp.ok) {
            const data = await resp.json();
            this._caudalesFeatures = data.features || [];
          }
        } catch (e) {}
      })());
    }

    if (!this._embalsesFeatures || this._embalsesFeatures.length === 0) {
      promises.push((async () => {
        try {
          const resp = await fetch(`${CONFIG.apiBaseUrl}/saih/embalses?format=geojson`);
          if (resp.ok) {
            const data = await resp.json();
            this._embalsesFeatures = data.features || [];
            return;
          }
        } catch (e) {}
        try {
          const resp = await fetch('./backend/data/saih_embalses.geojson');
          if (resp.ok) {
            const data = await resp.json();
            this._embalsesFeatures = data.features || [];
          }
        } catch (e) {}
      })());
    }

    if (promises.length > 0) {
      await Promise.all(promises);
    }
  }

  /**
   * Busca todas las entidades SAIH (caudales y embalses) dentro de un radio de proximidad (3 km) o con mismo código
   */
  async _findNearbySaihEntities(lat, lon, currentCode) {
    await this._ensureSaihCatalogsLoaded();
    const entities = [];
    const seenKeys = new Set();

    const targetLat = (lat !== undefined && lat !== null && !isNaN(Number(lat))) ? Number(lat) : null;
    const targetLon = (lon !== undefined && lon !== null && !isNaN(Number(lon))) ? Number(lon) : null;
    const hasCoords = (targetLat !== null && targetLon !== null);

    if (this._caudalesFeatures) {
      for (const feat of this._caudalesFeatures) {
        const p = feat.properties || {};
        const cLat = feat.geometry?.coordinates?.[1] ?? p.lat;
        const cLon = feat.geometry?.coordinates?.[0] ?? p.lon;
        const key = `caudal_${p.id_variable || p.codigo || p.nombre}`;
        if (seenKeys.has(key)) continue;

        let match = false;
        let dist = 999999;
        if (hasCoords && cLat != null && cLon != null) {
          dist = haversineDistanceKm(targetLat, targetLon, Number(cLat), Number(cLon));
          if (dist <= 3.0) match = true;
        }
        if (!match && currentCode && p.codigo && String(p.codigo).trim().toUpperCase() === String(currentCode).trim().toUpperCase()) {
          match = true;
          dist = 0;
        }
        if (match) {
          seenKeys.add(key);
          entities.push({
            type: 'caudal',
            props: p,
            distKm: dist,
            name: p.nombre || 'Estación de Caudal',
            code: p.codigo || ''
          });
        }
      }
    }

    if (this._embalsesFeatures) {
      for (const feat of this._embalsesFeatures) {
        const p = feat.properties || {};
        const eLat = feat.geometry?.coordinates?.[1] ?? p.lat;
        const eLon = feat.geometry?.coordinates?.[0] ?? p.lon;
        const key = `embalse_${p.codigo || p.id_estacion || p.nombre}`;
        if (seenKeys.has(key)) continue;

        let match = false;
        let dist = 999999;
        if (hasCoords && eLat != null && eLon != null) {
          dist = haversineDistanceKm(targetLat, targetLon, Number(eLat), Number(eLon));
          if (dist <= 3.0) match = true;
        }
        if (!match && currentCode && p.codigo && String(p.codigo).trim().toUpperCase() === String(currentCode).trim().toUpperCase()) {
          match = true;
          dist = 0;
        }
        if (match) {
          seenKeys.add(key);
          entities.push({
            type: 'embalse',
            props: p,
            distKm: dist,
            name: p.nombre || 'Embalse',
            code: p.codigo || ''
          });
        }
      }
    }

    entities.sort((a, b) => a.distKm - b.distKm);
    return entities;
  }

  /**
   * Renderiza los tabs de selección rápida entre Caudal y Embalse si existen múltiples elementos próximos
   */
  _renderProximityTabs(containerId, entities, currentSelectedType, currentSelectedId) {
    const container = document.getElementById(containerId);
    if (!container) return;

    if (!entities || entities.length <= 1) {
      container.style.display = 'none';
      container.innerHTML = '';
      return;
    }

    container.style.display = 'flex';
    container.innerHTML = '';

    entities.forEach(entity => {
      const isCaudal = entity.type === 'caudal';
      const p = entity.props;
      const entityId = isCaudal ? (p.id_variable || p.codigo) : (p.codigo || p.id_estacion || p.id_volumen);
      const isSelected = (entity.type === currentSelectedType) && (
        String(entityId) === String(currentSelectedId) || 
        (p.codigo && currentSelectedId && String(p.codigo).toUpperCase() === String(currentSelectedId).toUpperCase())
      );

      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = `saih-proximity-tab-btn ${isSelected ? 'active' : ''}`;
      
      const icon = isCaudal ? '💧' : '🌊';
      const typeLabel = isCaudal ? 'Caudal' : 'Embalse';
      const shortName = p.nombre || (isCaudal ? 'Estación' : 'Embalse');

      let valText = '';
      if (isCaudal) {
        const val = p.ultimo_caudal !== undefined ? p.ultimo_caudal : p.caudal;
        valText = (val !== null && val !== undefined && val !== '') ? `${Number(val).toFixed(1)} m³/s` : 'm³/s';
      } else {
        const vol = p.volumen_actual;
        const pct = p.porcentaje_llenado;
        if (pct !== null && pct !== undefined && pct !== '') {
          valText = `${Number(pct).toFixed(0)}%`;
        } else if (vol !== null && vol !== undefined && vol !== '') {
          valText = `${Number(vol).toFixed(1)} hm³`;
        } else {
          valText = 'hm³';
        }
      }

      btn.innerHTML = `
        <span class="saih-proximity-tab-badge">${icon} ${typeLabel}</span>
        <span>${escapeHtml(shortName)}</span>
        <span style="font-weight: 700; opacity: 0.9; margin-left: 2px;">(${escapeHtml(valText)})</span>
      `;

      btn.onclick = (e) => {
        e.stopPropagation();
        e.preventDefault();
        if (isSelected) return;

        const currentBackdrop = document.getElementById(isCaudal ? 'embalse-modal-backdrop' : 'caudal-modal-backdrop');
        const targetBackdrop = document.getElementById(isCaudal ? 'caudal-modal-backdrop' : 'embalse-modal-backdrop');

        if (currentBackdrop && currentBackdrop.style.display !== 'none') {
          if (targetBackdrop) targetBackdrop.classList.add('no-anim');
          currentBackdrop.style.display = 'none';
        }

        if (isCaudal) {
          this.openCaudalHistoryModal(p, 24, entities);
        } else {
          this.openEmbalseHistoryModal(p, 24, entities);
        }
      };

      container.appendChild(btn);
    });
  }

  /**
   * Abre el modal de evolución histórica del embalse y consulta la API
   */
  async openEmbalseHistoryModal(props, initialHours = 24, cachedNearbyEntities = null) {
    const backdrop = document.getElementById('embalse-modal-backdrop');
    if (!backdrop) return;

    this._currentModalEmbalseProps = props;
    this._currentModalEmbalseHours = initialHours;

    // Buscar y renderizar elementos próximos (Caudales o estaciones adyacentes)
    let nearbyEntities = cachedNearbyEntities;
    if (!nearbyEntities) {
      const lat = props.lat ?? props.latitud;
      const lon = props.lon ?? props.longitud;
      nearbyEntities = await this._findNearbySaihEntities(lat, lon, props.codigo);
    }
    this._renderProximityTabs('embalse-modal-proximity-tabs', nearbyEntities, 'embalse', props.codigo || props.id_estacion || props.id_volumen);

    const title = document.getElementById('embalse-modal-title');
    const subtitle = document.getElementById('embalse-modal-subtitle');
    const badgePct = document.getElementById('embalse-modal-pct-badge');
    const badgeSub = document.getElementById('embalse-modal-subcuenca-badge');
    const closeBtn = document.getElementById('embalse-modal-close');
    const rangeButtons = document.querySelectorAll('#embalse-range-buttons .btn-range');

    const statVol = document.getElementById('embalse-stat-vol');
    const statTime = document.getElementById('embalse-stat-time');
    const statCap = document.getElementById('embalse-stat-cap');
    const statPctSub = document.getElementById('embalse-stat-pct-sub');
    const statCota = document.getElementById('embalse-stat-cota');
    const statCotaVert = document.getElementById('embalse-stat-cota-vertido');
    const statCaudalIn = document.getElementById('embalse-stat-caudal-in');
    const statCaudalOut = document.getElementById('embalse-stat-caudal-out');
    const saihLink = document.getElementById('embalse-saih-link');

    const vol = props.volumen_actual !== null && props.volumen_actual !== undefined ? Number(props.volumen_actual) : null;
    const cap = props.capacidad_nmn !== null && props.capacidad_nmn !== undefined ? Number(props.capacidad_nmn) : null;
    const pct = props.porcentaje_llenado !== null && props.porcentaje_llenado !== undefined ? Number(props.porcentaje_llenado) : (vol !== null && cap ? (vol / cap * 100) : null);
    const cota = props.cota_actual !== null && props.cota_actual !== undefined ? Number(props.cota_actual) : null;
    const cotaV = props.cota_vertido !== null && props.cota_vertido !== undefined ? Number(props.cota_vertido) : null;
    const caudalIn = props.caudal_recibido !== null && props.caudal_recibido !== undefined ? Number(props.caudal_recibido) : null;
    const caudalOut = props.caudal_salida_rio !== null && props.caudal_salida_rio !== undefined ? Number(props.caudal_salida_rio) : (props.caudal_salida !== null && props.caudal_salida !== undefined ? Number(props.caudal_salida) : null);

    if (title) title.textContent = props.nombre || 'Embalse CHJ';
    if (subtitle) {
      const locParts = [props.poblacion, props.provincia, props.subcuenca, props.codigo ? `Cód: ${props.codigo}` : null].filter(Boolean);
      subtitle.textContent = locParts.join(' · ') || 'Demarcación Hidrográfica del Júcar';
    }

    if (badgePct) {
      const pctStr = pct !== null ? `${pct.toFixed(1)}%` : '--%';
      let bg = 'rgba(16, 185, 129, 0.2)';
      let col = '#10b981';
      let border = 'rgba(16, 185, 129, 0.4)';
      let riskLabel = 'Riesgo Bajo';
      if (pct !== null) {
        if (pct >= 70) {
          bg = 'rgba(239, 68, 68, 0.25)';
          col = '#ef4444';
          border = 'rgba(239, 68, 68, 0.5)';
          riskLabel = '🔴 Alto Riesgo Desbordamiento';
        } else if (pct >= 35) {
          bg = 'rgba(245, 158, 11, 0.25)';
          col = '#f59e0b';
          border = 'rgba(245, 158, 11, 0.5)';
          riskLabel = '🟡 Riesgo Moderado';
        } else {
          bg = 'rgba(16, 185, 129, 0.2)';
          col = '#10b981';
          border = 'rgba(16, 185, 129, 0.4)';
          riskLabel = '🟢 Riesgo Bajo';
        }
      }
      badgePct.textContent = `${pctStr} Lleno · ${riskLabel}`;
      badgePct.style.background = bg;
      badgePct.style.color = col;
      badgePct.style.borderColor = border;
    }

    if (badgeSub) {
      badgeSub.textContent = props.subcuenca || 'CHJ';
    }

    if (statVol) statVol.textContent = vol !== null ? `${vol.toFixed(2)} hm³` : '-- hm³';
    if (statTime) {
      const timeStr = props.ultima_hora ? formatMadridDateTime(new Date(props.ultima_hora)) : '';
      statTime.textContent = timeStr ? `Última lectura: ${timeStr}` : 'Tiempo real';
    }
    if (statCap) statCap.textContent = cap !== null ? `${cap.toFixed(2)} hm³` : '-- hm³';
    if (statPctSub) statPctSub.textContent = pct !== null ? `Reserva: ${pct.toFixed(1)}%` : 'Reserva: --%';
    if (statCota) statCota.textContent = cota !== null ? `${cota.toFixed(2)} m` : '-- m';
    if (statCotaVert) statCotaVert.textContent = cotaV !== null ? `Vertido: ${cotaV.toFixed(2)} m` : 'Vertido: -- m';
    if (statCaudalIn) statCaudalIn.textContent = caudalIn !== null ? `⬇ Entrada: ${caudalIn.toFixed(2)} m³/s` : '⬇ Entrada: -- m³/s';
    if (statCaudalOut) statCaudalOut.textContent = caudalOut !== null ? `⬆ Aliviado: ${caudalOut.toFixed(2)} m³/s` : '⬆ Aliviado: -- m³/s';

    if (saihLink) {
      saihLink.href = 'https://saih.chj.es/mapa-embalses';
    }

    rangeButtons.forEach(btn => {
      const h = Number(btn.getAttribute('data-hours'));
      btn.classList.toggle('active', h === initialHours);
      btn.onclick = (ev) => {
        ev.stopPropagation();
        rangeButtons.forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        this.loadEmbalseHistoryForModal(props.codigo || props.id_estacion || props.id_volumen, h, cap);
      };
    });

    const closeModal = () => {
      backdrop.style.display = 'none';
      backdrop.classList.remove('no-anim');
      const otherBackdrop = document.getElementById('caudal-modal-backdrop');
      if (otherBackdrop) otherBackdrop.classList.remove('no-anim');
      backdrop.setAttribute('aria-hidden', 'true');
      document.removeEventListener('keydown', onKeyDown);
    };

    const onKeyDown = (e) => {
      if (e.key === 'Escape') closeModal();
    };

    if (closeBtn) closeBtn.onclick = closeModal;
    backdrop.onclick = (e) => {
      if (e.target === backdrop) closeModal();
    };
    document.addEventListener('keydown', onKeyDown);

    backdrop.style.display = 'flex';
    backdrop.setAttribute('aria-hidden', 'false');

    await this.loadEmbalseHistoryForModal(props.codigo || props.id_estacion || props.id_volumen, initialHours, cap);
  }

  /**
   * Carga los datos de la serie temporal del embalse desde la API y genera la gráfica interactiva
   */
  async loadEmbalseHistoryForModal(idOrCode, hours, capNMN) {
    const container = document.getElementById('embalse-chart-canvas-container');
    if (!container) return;

    container.innerHTML = `
      <div class="caudal-chart-modal-loading">
        <div class="caudal-spinner"></div>
        <span>Consultando histórico de volumen en la API del SAIH (últimas ${hours}h)...</span>
      </div>
    `;

    try {
      const resp = await fetch(`${CONFIG.apiBaseUrl}/saih/embalses/${idOrCode}/history?hours=${hours}&variable_type=volumen`);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      const series = data.serie || [];

      if (series.length === 0) {
        container.innerHTML = `<div class="caudal-chart-nodata">No se encontraron registros de volumen en las últimas ${hours} horas.</div>`;
        return;
      }

      const validPoints = series.filter(s => s.valor !== null && s.valor !== undefined);
      this._renderEmbalseSvgChart(validPoints, capNMN, container);
    } catch (err) {
      console.warn('Error al obtener histórico de embalse:', err);
      container.innerHTML = `
        <div class="caudal-chart-error-banner">
          <strong>No se pudo conectar con la API de embalses en tiempo real</strong>
          <span>Asegúrate de que el servidor backend (<code>python backend/run.py</code>) esté ejecutándose en el puerto 8000.</span>
        </div>
      `;
    }
  }

  /**
   * Genera el SVG interactivo con la curva de volumen y línea horizontal de capacidad NMN
   */
  _renderEmbalseSvgChart(points, capNMN, containerEl) {
    if (!points || points.length === 0) return;

    const values = points.map(p => Number(p.valor));
    const rawMax = Math.max(...values, 0.1);
    const rawMin = Math.min(...values);

    // Escala Y
    let maxY = capNMN ? Math.max(capNMN * 1.08, rawMax * 1.15) : rawMax * 1.25;
    let minY = Math.max(0, rawMin > 0 && (rawMax - rawMin) < rawMax * 0.3 ? rawMin * 0.85 : 0);
    const rangeY = maxY - minY || 1;

    const width = 720;
    const height = 240;
    const pad = { top: 25, right: 30, bottom: 35, left: 55 };
    const innerW = width - pad.left - pad.right;
    const innerH = height - pad.top - pad.bottom;

    const chartCoords = points.map((pt, i) => {
      const x = pad.left + (i / Math.max(1, points.length - 1)) * innerW;
      const y = pad.top + innerH - ((Number(pt.valor) - minY) / rangeY) * innerH;
      return { x, y, pt };
    });

    const polyPointsStr = chartCoords.map(c => `${c.x.toFixed(1)},${c.y.toFixed(1)}`).join(' ');
    const baseY = (pad.top + innerH).toFixed(1);
    const areaPointsStr = `${pad.left},${baseY} ${polyPointsStr} ${pad.left + innerW},${baseY}`;

    // Línea de Capacidad NMN
    let capLine = '';
    if (capNMN && capNMN <= maxY && capNMN >= minY) {
      const capY = pad.top + innerH - ((capNMN - minY) / rangeY) * innerH;
      const yF = capY.toFixed(1);
      capLine = `
        <g class="chart-threshold-group">
          <line x1="${pad.left}" y1="${yF}" x2="${pad.left + innerW}" y2="${yF}"
                stroke="#38bdf8" stroke-dasharray="5,4" stroke-width="1.6" opacity="0.85"/>
          <rect x="${pad.left + 8}" y="${(capY - 14).toFixed(1)}" width="170" height="15" rx="3" fill="rgba(15,23,42,0.85)" stroke="#38bdf8" stroke-width="0.8"/>
          <text x="${pad.left + 14}" y="${(capY - 3).toFixed(1)}" fill="#38bdf8" font-size="10" font-weight="700">
            Capacidad NMN: ${capNMN} hm³
          </text>
        </g>
      `;
    }

    // Cuadrícula y Ejes Y (4 divisiones)
    const gridLines = [];
    const yLabels = [];
    for (let i = 0; i <= 4; i++) {
      const val = minY + (rangeY * (i / 4));
      const y = pad.top + innerH - (innerH * (i / 4));
      const yF = y.toFixed(1);
      gridLines.push(`<line x1="${pad.left}" y1="${yF}" x2="${pad.left + innerW}" y2="${yF}" stroke="rgba(255,255,255,0.07)" stroke-width="1"/>`);
      yLabels.push(`<text x="${pad.left - 8}" y="${(y + 3.5).toFixed(1)}" fill="#94a3b8" font-size="10" font-weight="500" text-anchor="end">${val >= 10 ? val.toFixed(1) : val.toFixed(2)}</text>`);
    }

    // Ejes X y Fechas (5 etiquetas equidistantes)
    const xLabels = [];
    const numXMarks = Math.min(6, points.length);
    for (let k = 0; k < numXMarks; k++) {
      const idx = Math.round((k / (numXMarks - 1)) * (points.length - 1));
      const pt = points[idx];
      const x = pad.left + (idx / Math.max(1, points.length - 1)) * innerW;
      const d = pt.fecha ? new Date(pt.fecha) : new Date();
      const timeLabel = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
      const dateLabel = `${d.getDate()}/${d.getMonth() + 1}`;
      xLabels.push(`
        <g transform="translate(${x.toFixed(1)}, ${height - pad.bottom + 14})">
          <text x="0" y="0" fill="#94a3b8" font-size="9.5" text-anchor="middle">${timeLabel}</text>
          <text x="0" y="11" fill="#64748b" font-size="8.5" text-anchor="middle">${dateLabel}</text>
        </g>
      `);
    }

    const gradId = `embalse-grad-chart-${Date.now()}`;

    containerEl.innerHTML = `
      <div class="caudal-svg-chart-container">
        <svg viewBox="0 0 ${width} ${height}" class="caudal-modal-svg" id="embalse-interactive-svg" preserveAspectRatio="xMidYMid meet">
          <defs>
            <linearGradient id="${gradId}" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stop-color="#0284c7" stop-opacity="0.6"/>
              <stop offset="60%" stop-color="#0ea5e9" stop-opacity="0.18"/>
              <stop offset="100%" stop-color="#0ea5e9" stop-opacity="0.0"/>
            </linearGradient>
          </defs>

          <!-- Cuadrícula de fondo -->
          ${gridLines.join('')}

          <!-- Eje Y etiquetas -->
          ${yLabels.join('')}
          <text x="${pad.left}" y="${pad.top - 8}" fill="#38bdf8" font-size="10.5" font-weight="700">hm³</text>

          <!-- Eje X etiquetas -->
          ${xLabels.join('')}

          <!-- Línea de Capacidad NMN -->
          ${capLine}

          <!-- Área sombreada bajo la curva -->
          <polygon points="${areaPointsStr}" fill="url(#${gradId})"/>

          <!-- Línea de Volumen Embalsado -->
          <polyline points="${polyPointsStr}" fill="none" stroke="#0ea5e9" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"/>

          <!-- Punto más reciente -->
          ${chartCoords.length > 0 ? `
            <circle cx="${chartCoords[chartCoords.length - 1].x.toFixed(1)}" cy="${chartCoords[chartCoords.length - 1].y.toFixed(1)}" r="4.5" fill="#0ea5e9" stroke="#ffffff" stroke-width="2"/>
          ` : ''}

          <!-- Elementos de Hover interactivo -->
          <g id="embalse-chart-hover-group" style="display: none; pointer-events: none;">
            <line id="embalse-chart-guideline" x1="0" y1="${pad.top}" x2="0" y2="${pad.top + innerH}"
                  stroke="#38bdf8" stroke-dasharray="3,3" stroke-width="1.4" opacity="0.85"/>
            <circle id="embalse-chart-hover-dot" cx="0" cy="0" r="5.5" fill="#0ea5e9" stroke="#ffffff" stroke-width="2.5"/>
          </g>

          <!-- Zona de captura de ratón -->
          <rect id="embalse-chart-hitbox" x="${pad.left}" y="${pad.top}" width="${innerW}" height="${innerH}"
                fill="transparent" cursor="crosshair" style="pointer-events: all;"/>
        </svg>

        <!-- Tooltip flotante interactivo con desbordamiento visible -->
        <div id="embalse-chart-tooltip" class="caudal-chart-hover-tooltip" aria-hidden="true">
          <div class="caudal-chart-tooltip-header">
            <span>📅</span>
            <span id="embalse-tt-time">--:--</span>
          </div>
          <div class="caudal-chart-tooltip-val-row">
            <span id="embalse-tt-val" class="caudal-chart-tooltip-val" style="color: #38bdf8;">-- hm³</span>
            <span id="embalse-tt-badge" class="caudal-chart-tooltip-badge" style="display: none;"></span>
          </div>
        </div>
      </div>
    `;

    const svgEl = containerEl.querySelector('#embalse-interactive-svg');
    const hitbox = containerEl.querySelector('#embalse-chart-hitbox');
    const hoverGroup = containerEl.querySelector('#embalse-chart-hover-group');
    const guideline = containerEl.querySelector('#embalse-chart-guideline');
    const hoverDot = containerEl.querySelector('#embalse-chart-hover-dot');
    const tooltip = containerEl.querySelector('#embalse-chart-tooltip');
    const ttTime = containerEl.querySelector('#embalse-tt-time');
    const ttVal = containerEl.querySelector('#embalse-tt-val');
    const ttBadge = containerEl.querySelector('#embalse-tt-badge');

    if (!hitbox || !hoverGroup || !svgEl || !tooltip) return;

    const onHoverMove = (e) => {
      const rect = svgEl.getBoundingClientRect();
      const clientX = e.clientX - rect.left;
      const svgX = (clientX / rect.width) * width;

      const clampedSvgX = Math.max(pad.left, Math.min(pad.left + innerW, svgX));
      const fraction = (clampedSvgX - pad.left) / innerW;
      const idx = Math.round(fraction * (chartCoords.length - 1));
      const target = chartCoords[Math.max(0, Math.min(chartCoords.length - 1, idx))];

      if (!target) return;

      const val = Number(target.pt.valor);
      const fecha = target.pt.fecha ? new Date(target.pt.fecha) : new Date();
      const pct = (capNMN && capNMN > 0) ? (val / capNMN * 100) : null;

      let pointColor = '#10b981';
      let isWarning = false;
      let statusText = '';
      let bgBadge = '';
      let textBadge = '';

      if (pct !== null) {
        if (pct >= 70) {
          pointColor = '#ef4444';
          statusText = `🔴 ${pct.toFixed(1)}% (Riesgo Alto)`;
          bgBadge = 'rgba(239, 68, 68, 0.25)';
          textBadge = '#fca5a5';
          isWarning = true;
        } else if (pct >= 35) {
          pointColor = '#f59e0b';
          statusText = `🟡 ${pct.toFixed(1)}% (Riesgo Moderado)`;
          bgBadge = 'rgba(245, 158, 11, 0.25)';
          textBadge = '#fde68a';
          isWarning = true;
        } else {
          pointColor = '#10b981';
          isWarning = false;
        }
      }

      // Actualizar línea guía y punto resaltado en el SVG
      guideline.setAttribute('x1', target.x.toFixed(1));
      guideline.setAttribute('x2', target.x.toFixed(1));
      guideline.setAttribute('stroke', pointColor);

      hoverDot.setAttribute('cx', target.x.toFixed(1));
      hoverDot.setAttribute('cy', target.y.toFixed(1));
      hoverDot.setAttribute('fill', pointColor);

      hoverGroup.style.display = 'block';

      // Actualizar contenido del tooltip con color en el número
      ttTime.textContent = formatMadridDateTime(fecha);
      ttVal.textContent = `${val.toFixed(2)} hm³`;
      ttVal.style.color = pointColor;

      // Si está en estado destacable mostrar el badge correspondiente, si es normal no mostrar badge
      if (isWarning) {
        ttBadge.style.display = 'inline-block';
        ttBadge.textContent = statusText;
        ttBadge.style.backgroundColor = bgBadge;
        ttBadge.style.color = textBadge;
        ttBadge.style.border = `1px solid ${pointColor}60`;
      } else {
        ttBadge.style.display = 'none';
      }

      // Posicionar tooltip relativo al contenedor del gráfico
      const pctX = (target.x / width) * 100;
      const pctY = (target.y / height) * 100;

      tooltip.style.left = `${pctX}%`;
      tooltip.style.top = `${pctY}%`;

      let transformX = '-50%';
      if (pctX < 20) {
        transformX = '-10%';
      } else if (pctX > 80) {
        transformX = '-90%';
      }

      tooltip.style.transform = `translate(${transformX}, -120%)`;
      tooltip.classList.add('is-visible');
    };

    const onHoverLeave = () => {
      hoverGroup.style.display = 'none';
      tooltip.classList.remove('is-visible');
    };

    hitbox.addEventListener('mousemove', onHoverMove);
    hitbox.addEventListener('mouseenter', onHoverMove);
    hitbox.addEventListener('mouseleave', onHoverLeave);
    svgEl.addEventListener('mouseleave', onHoverLeave);
  }
}

function haversineDistanceKm(lat1, lon1, lat2, lon2) {
  const R = 6371.0;
  const dLat = (lat2 - lat1) * Math.PI / 180.0;
  const dLon = (lon2 - lon1) * Math.PI / 180.0;
  const a = Math.sin(dLat / 2) * Math.sin(dLat / 2) +
            Math.cos(lat1 * Math.PI / 180.0) * Math.cos(lat2 * Math.PI / 180.0) *
            Math.sin(dLon / 2) * Math.sin(dLon / 2);
  const c = 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
  return R * c;
}

function escapeHtml(str) {
  if (str === null || str === undefined) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}


