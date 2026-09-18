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
    this.ccaaLayer = null;
    this.currentBasemapId = null;
    const savedPrefs = StorageManager.load();
    this.ccaaVisible = (savedPrefs.ccaaVisible !== undefined) ? Boolean(savedPrefs.ccaaVisible) : true;
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
    // 1. Cuencas base (350) y Resalte/Selección de cuencas (360) -> Por debajo de todas las capas de tiempo real y modelos
    if (!this.map.getPane('cuencasPane')) {
      this.map.createPane('cuencasPane');
      this.map.getPane('cuencasPane').style.zIndex = 350;
    }
    if (!this.map.getPane('cuencasHoverPane')) {
      this.map.createPane('cuencasHoverPane');
      this.map.getPane('cuencasHoverPane').style.zIndex = 360;
      this.map.getPane('cuencasHoverPane').style.pointerEvents = 'none';
    }

    // 2. Modelos de Predicción Numérica (ECMWF, AROME, ICON) -> por encima de cuencas (420)
    if (!this.map.getPane('modelsPane')) {
      this.map.createPane('modelsPane');
      this.map.getPane('modelsPane').style.zIndex = 420;
    }

    // 3. Radar Meteorológico en Tiempo Real -> por encima de modelos y cuencas (440)
    if (!this.map.getPane('radarPane')) {
      this.map.createPane('radarPane');
      this.map.getPane('radarPane').style.zIndex = 440;
    }

    // 4. Avisos Meteorológicos AEMET en Tiempo Real (460)
    if (!this.map.getPane('warningsPane')) {
      this.map.createPane('warningsPane');
      this.map.getPane('warningsPane').style.zIndex = 460;
    }

    // 5. Límites Autonómicos CCAA (500)
    if (!this.map.getPane('ccaaPane')) {
      this.map.createPane('ccaaPane');
      this.map.getPane('ccaaPane').style.zIndex = 500;
      this.map.getPane('ccaaPane').style.pointerEvents = 'none';
    }

    // 6. Redes de Observación y Estaciones en Tiempo Real (SAIH Júcar) -> Máxima prioridad
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

    // 7. Tooltips y Popups Leaflet
    if (this.map.getPane('tooltipPane')) {
      this.map.getPane('tooltipPane').style.zIndex = 750;
    }
    if (this.map.getPane('popupPane')) {
      this.map.getPane('popupPane').style.zIndex = 800;
    }

    // Registrar y montar capas base respetando preferencias guardadas
    this._setupBaseLayers();

    // Cargar y superponer límites vectoriales de CCAA por encima de todo
    this._loadCcaaBoundaries();

    // Guardar selección y adaptar color de límites cuando el usuario cambia el mapa base
    this.map.on('baselayerchange', (e) => {
      const basemapId = this.layerNameToId[e.name];
      if (basemapId) {
        this.currentBasemapId = basemapId;
        StorageManager.setBasemap(basemapId);
        this.updateCcaaStyle(basemapId);
      }
    });

    return this.map;
  }

  /**
   * Obtiene el estilo de las líneas de CCAA en función del mapa base activo
   * (Azul muy oscuro en mapas claros/satélite, blanco en mapa oscuro)
   */
  _getCcaaStyle(basemapId = null) {
    const activeId = basemapId || this.currentBasemapId || StorageManager.load().basemapId || 'ignBase';
    const isDarkMap = activeId === 'esriDarkCanvas';

    return {
      color: isDarkMap ? '#ffffff' : '#0a192f',
      weight: 2.0,
      opacity: isDarkMap ? 0.95 : 0.9,
      fill: false,
      fillOpacity: 0.0,
      lineCap: 'round',
      lineJoin: 'round',
      dashArray: '6, 6'
    };
  }

  /**
   * Actualiza el estilo dinámico de las fronteras de CCAA en tiempo real
   */
  updateCcaaStyle(basemapId = null) {
    if (this.ccaaLayer) {
      this.ccaaLayer.setStyle(this._getCcaaStyle(basemapId));
    }
  }

  /**
   * Conmuta o establece la visibilidad de los límites de CCAA
   * @param {boolean} visible 
   */
  setCcaaVisible(visible) {
    this.ccaaVisible = Boolean(visible);
    StorageManager.setCcaaVisible(this.ccaaVisible);
    if (this.ccaaLayer) {
      if (this.ccaaVisible) {
        if (!this.map.hasLayer(this.ccaaLayer)) {
          this.map.addLayer(this.ccaaLayer);
        }
      } else {
        if (this.map.hasLayer(this.ccaaLayer)) {
          this.map.removeLayer(this.ccaaLayer);
        }
      }
    }
  }

  /**
   * Carga el GeoJSON de Comunidades Autónomas y lo dibuja en el panel prioritario sin relleno
   */
  async _loadCcaaBoundaries() {
    let geojsonData = null;
    for (const url of CONFIG.dataSources.ccaaGeoJson) {
      try {
        const resp = await fetch(url);
        if (resp.ok) {
          geojsonData = await resp.json();
          break;
        }
      } catch (e) {}
    }

    if (geojsonData && geojsonData.features) {
      this.ccaaLayer = L.geoJSON(geojsonData, {
        pane: 'ccaaPane',
        interactive: false,
        style: () => this._getCcaaStyle()
      });
      if (this.ccaaVisible) {
        this.ccaaLayer.addTo(this.map);
      }
    }
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
        this.currentBasemapId = bm.id;
        tileLayer.addTo(this.map);
      }
    });

    // Si ninguna fue marcada, usar la primera
    if (!defaultLayer && Object.values(this.baseLayers).length > 0) {
      const firstEntry = Object.entries(this.baseLayers)[0];
      defaultLayer = firstEntry[1];
      this.currentBasemapId = this.layerNameToId[firstEntry[0]];
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
