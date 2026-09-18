/**
 * RainLoc - Inicialización de Mapa y Capas Base
 */

import { CONFIG } from './config.js';
import { StorageManager } from './storage.js';

export class MapManager {
  constructor(mapContainerId = 'map') {
    this.containerId = mapContainerId;
    this.map = null;
    this.baseLayers = {};
    this.layerControl = null;
    this.layerNameToId = {};
  }

  /**
   * Inicializa la instancia del mapa Leaflet
   */
  init() {
    const { initialCenter, initialZoom, minZoom, maxZoom, maxBounds } = CONFIG.map;

    this.map = L.map(this.containerId, {
      center: initialCenter,
      zoom: initialZoom,
      minZoom: minZoom,
      maxZoom: maxZoom,
      maxBounds: maxBounds,
      zoomControl: false, // Usamos control personalizado para mejor posición UI
      attributionControl: false // Personalizaremos el control
    });

    // Añadir controles de zoom en la esquina superior derecha
    L.control.zoom({ position: 'topright' }).addTo(this.map);

    // Añadir control de escala en km/m
    L.control.scale({ imperial: false, position: 'bottomleft' }).addTo(this.map);

    // Crear paneles dedicados para jerarquía estricta de capas y clics
    // Cuencas (400), Avisos (450), Lluvias (580), Caudales (590), Embalses (610 - prioridad superior)
    if (!this.map.getPane('cuencasPane')) {
      this.map.createPane('cuencasPane');
      this.map.getPane('cuencasPane').style.zIndex = 400;
    }
    if (!this.map.getPane('warningsPane')) {
      this.map.createPane('warningsPane');
      this.map.getPane('warningsPane').style.zIndex = 450;
    }
    if (!this.map.getPane('lluviasPane')) {
      this.map.createPane('lluviasPane');
      this.map.getPane('lluviasPane').style.zIndex = 580;
    }
    if (!this.map.getPane('caudalesPane')) {
      this.map.createPane('caudalesPane');
      this.map.getPane('caudalesPane').style.zIndex = 590;
    }
    if (!this.map.getPane('embalsesPane')) {
      this.map.createPane('embalsesPane');
      this.map.getPane('embalsesPane').style.zIndex = 610;
    }

    // Registrar y montar capas base respetando preferencias guardadas
    this._setupBaseLayers();

    // Guardar selección cuando el usuario cambia el mapa base
    this.map.on('baselayerchange', (e) => {
      const basemapId = this.layerNameToId[e.name];
      if (basemapId) {
        StorageManager.setBasemap(basemapId);
      }
    });

    return this.map;
  }

  /**
   * Configura las capas base de teselas (IGN, Esri, OSM) y restaura la preferencia guardada
   */
  _setupBaseLayers() {
    let defaultLayer = null;
    const savedPrefs = StorageManager.load();
    const savedBasemapId = savedPrefs.basemapId;

    Object.values(CONFIG.basemaps).forEach((bm) => {
      const tileLayer = L.tileLayer(bm.url, {
        attribution: bm.attribution,
        subdomains: bm.subdomains || 'abc',
        maxZoom: bm.maxZoom || 19
      });

      this.baseLayers[bm.name] = tileLayer;
      this.layerNameToId[bm.name] = bm.id;

      // Si coincide con el guardado en localStorage o es el predeterminado
      const isSavedActive = (savedBasemapId && bm.id === savedBasemapId);
      const isConfigDefault = (!savedBasemapId && bm.isDefault);

      if ((isSavedActive || isConfigDefault) && !defaultLayer) {
        defaultLayer = tileLayer;
        tileLayer.addTo(this.map);
      }
    });

    // Si ninguna fue marcada, usar la primera
    if (!defaultLayer && Object.values(this.baseLayers).length > 0) {
      defaultLayer = Object.values(this.baseLayers)[0];
      defaultLayer.addTo(this.map);
    }

    // Inicializamos el control de capas (preparado para recibir overlays de radar y modelos mesoescalares)
    this.layerControl = L.control.layers(
      this.baseLayers,
      {},
      { position: 'topright', collapsed: true }
    ).addTo(this.map);
  }

  /**
   * Permite registrar capas overlay en el control de capas
   * @param {L.Layer} layer Capa Leaflet
   * @param {string} name Nombre legible
   */
  addOverlayLayer(layer, name) {
    if (this.layerControl) {
      this.layerControl.addOverlay(layer, name);
    }
  }

  /**
   * Reestablece la vista al centro de la Comunitat Valenciana
   */
  resetView() {
    const { initialCenter, initialZoom } = CONFIG.map;
    this.map.flyTo(initialCenter, initialZoom, {
      animate: true,
      duration: 1.0
    });
  }

  /**
   * Ajusta la vista del mapa a los límites de una capa o geometría
   * @param {L.LatLngBounds} bounds 
   */
  fitBounds(bounds) {
    this.map.fitBounds(bounds, {
      padding: [40, 40],
      maxZoom: 12,
      animate: true
    });
  }
}
