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
    this.riosLayer = null;
    this.riosGeoJson = null;
    this.currentBasemapId = null;
    const savedPrefs = StorageManager.load();
    this.ccaaVisible = (savedPrefs.ccaaVisible !== undefined) ? Boolean(savedPrefs.ccaaVisible) : true;
    this.riosVisible = (savedPrefs.riosVisible !== undefined) ? Boolean(savedPrefs.riosVisible) : true;
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

    // 0.5. Malla Continua de Acumulados de Lluvia Interpolada (320) -> Al fondo de todo, por debajo de cuencas (350), modelos (420) y radar (440)
    if (!this.map.getPane('pluvioMeshPane')) {
      this.map.createPane('pluvioMeshPane');
      this.map.getPane('pluvioMeshPane').style.zIndex = 320;
    }

    // 1. Cuencas base (350) y Resalte/Selección de cuencas (360) -> Por encima de la malla continua de lluvia
    if (!this.map.getPane('cuencasPane')) {
      this.map.createPane('cuencasPane');
      this.map.getPane('cuencasPane').style.zIndex = 350;
    }
    if (!this.map.getPane('cuencasHoverPane')) {
      this.map.createPane('cuencasHoverPane');
      this.map.getPane('cuencasHoverPane').style.zIndex = 360;
      this.map.getPane('cuencasHoverPane').style.pointerEvents = 'none';
    }

    // 1.5. Capa Vectorial de Ríos de España (358) -> Por encima de cuencas y debajo de avisos/modelos
    if (!this.map.getPane('riosPane')) {
      this.map.createPane('riosPane');
      this.map.getPane('riosPane').style.zIndex = 358;
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
    if (!this.map.getPane('pluvioLabelsPane')) {
      this.map.createPane('pluvioLabelsPane');
      this.map.getPane('pluvioLabelsPane').style.zIndex = 585;
      this.map.getPane('pluvioLabelsPane').style.pointerEvents = 'none';
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

    // Cargar y superponer la red de ríos de España
    this._loadRiosNetwork();

    // Guardar selección y adaptar color de límites cuando el usuario cambia el mapa base
    this.map.on('baselayerchange', (e) => {
      const basemapId = this.layerNameToId[e.name];
      if (basemapId) {
        this.currentBasemapId = basemapId;
        StorageManager.setBasemap(basemapId);
        this.updateCcaaStyle(basemapId);
        this.updateRiosStyle(basemapId);
        if (this.uiManager && this.uiManager.updateActiveBasemapUI) {
          this.uiManager.updateActiveBasemapUI(basemapId);
        }
      }
    });

    // Escuchar cambios en el modo de color del sistema (claro/oscuro) para adaptar el mapa base Esri por defecto
    if (typeof window !== 'undefined' && window.matchMedia) {
      window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', (e) => {
        if (this.currentBasemapId === 'esriCanvas' || this.currentBasemapId === 'esriDarkCanvas') {
          const newBasemapId = e.matches ? 'esriDarkCanvas' : 'esriCanvas';
          this.switchBasemap(newBasemapId);
        }
      });
    }

    // Observador de cambios de tamaño en el contenedor del mapa (crítico para PWA standalone en iOS/iPad)
    if (typeof ResizeObserver !== 'undefined') {
      const mapContainer = document.getElementById(this.containerId);
      if (mapContainer) {
        const resizeObserver = new ResizeObserver(() => {
          if (this.map) {
            this.map.invalidateSize({ pan: false });
          }
        });
        resizeObserver.observe(mapContainer);
      }
    }

    // Escuchar cambios de tamaño de ventana y orientación (p. ej. barra de herramientas de Safari en iPad/iOS)
    if (typeof window !== 'undefined') {
      const handleResize = () => {
        if (this.map) {
          this.map.invalidateSize({ pan: false });
        }
      };
      window.addEventListener('resize', handleResize, { passive: true });
      window.addEventListener('orientationchange', () => {
        setTimeout(handleResize, 150);
        setTimeout(handleResize, 500);
      }, { passive: true });
      if (window.visualViewport) {
        window.visualViewport.addEventListener('resize', handleResize, { passive: true });
      }
      window.addEventListener('load', handleResize, { passive: true });

      // Ráfaga de recalculación inicial para PWAs instaladas que inician con dimensiones de pantalla dinámicas
      [50, 150, 300, 600, 1200].forEach((delay) => {
        setTimeout(() => {
          if (this.map) {
            this.map.invalidateSize({ pan: false });
          }
        }, delay);
      });
    }

    return this.map;
  }

  /**
   * Obtiene el estilo de las líneas de CCAA en función del mapa base activo
   * (Azul muy oscuro en mapas claros/satélite, blanco en mapa oscuro)
   */
  _getCcaaStyle(basemapId = null) {
    const activeId = basemapId || this.currentBasemapId || StorageManager.load().basemapId || StorageManager.getDefaultBasemapId();
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
      this.ccaaGeoJson = geojsonData;
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
   * Obtiene el estilo de los ríos en función de la jerarquía hidrográfica y el mapa base activo
   */
  _getRiosStyle(feature, basemapId = null) {
    const activeId = basemapId || this.currentBasemapId || StorageManager.load().basemapId || StorageManager.getDefaultBasemapId();
    const isDarkMap = activeId === 'esriDarkCanvas';
    const tRio = feature?.properties?.t_rio || 3;
    const isPermanent = feature?.properties?.permanente !== false;

    // Jerarquía de grosores
    let weight = 1.2;
    let opacity = isDarkMap ? 0.85 : 0.8;
    if (tRio === 1) { // Ríos principales (Ebro, Tajo, Duero, Guadalquivir, etc.)
      weight = 2.8;
      opacity = isDarkMap ? 0.98 : 0.95;
    } else if (tRio === 2) { // Ríos secundarios destacados
      weight = 1.9;
      opacity = isDarkMap ? 0.92 : 0.9;
    } else if (tRio === 3) { // Afluentes y ríos menores
      weight = 1.2;
      opacity = isDarkMap ? 0.8 : 0.75;
    } else if (tRio >= 4) { // Ramblas y cursos de agua estacionales
      weight = 0.85;
      opacity = isDarkMap ? 0.65 : 0.6;
    }

    // Paleta azul luminosa en mapa oscuro, azul cian/marino en mapa claro
    let color = isDarkMap ? '#38bdf8' : '#0284c7';
    if (tRio === 1) {
      color = isDarkMap ? '#67e8f9' : '#0369a1';
    }

    return {
      color: color,
      weight: weight,
      opacity: opacity,
      fill: false,
      lineCap: 'round',
      lineJoin: 'round',
      dashArray: isPermanent ? null : '3, 4'
    };
  }

  /**
   * Actualiza el estilo dinámico de la red de ríos en tiempo real
   */
  updateRiosStyle(basemapId = null) {
    if (this.riosLayer) {
      this.riosLayer.setStyle((feature) => this._getRiosStyle(feature, basemapId));
    }
  }

  /**
   * Conmuta o establece la visibilidad de la red de ríos de España
   * @param {boolean} visible 
   */
  setRiosVisible(visible) {
    this.riosVisible = Boolean(visible);
    StorageManager.setRiosVisible(this.riosVisible);
    if (this.riosLayer) {
      if (this.riosVisible) {
        if (!this.map.hasLayer(this.riosLayer)) {
          this.map.addLayer(this.riosLayer);
        }
      } else {
        if (this.map.hasLayer(this.riosLayer)) {
          this.map.removeLayer(this.riosLayer);
        }
      }
    }
  }

  /**
   * Carga el GeoJSON de la red de ríos de España y lo dibuja en el panel de ríos con interactividad
   */
  async _loadRiosNetwork() {
    let geojsonData = null;
    for (const url of CONFIG.dataSources.riosGeoJson) {
      try {
        const resp = await fetch(url);
        if (resp.ok) {
          geojsonData = await resp.json();
          break;
        }
      } catch (e) {}
    }

    if (geojsonData && geojsonData.features) {
      this.riosGeoJson = geojsonData;
      this.riosLayer = L.geoJSON(geojsonData, {
        pane: 'riosPane',
        style: (feature) => this._getRiosStyle(feature),
        onEachFeature: (feature, layer) => {
          const nombre = feature.properties?.nombre;
          if (nombre) {
            layer.bindTooltip(`🌊 ${nombre}`, {
              sticky: true,
              direction: 'auto',
              className: 'river-tooltip'
            });
          }

          layer.on({
            mouseover: (e) => {
              const currentStyle = this._getRiosStyle(feature);
              e.target.setStyle({
                weight: currentStyle.weight + 1.8,
                opacity: 1.0,
                color: this.currentBasemapId === 'esriDarkCanvas' ? '#a5f3fc' : '#075985'
              });
            },
            mouseout: (e) => {
              if (this.riosLayer) {
                this.riosLayer.resetStyle(e.target);
              }
            }
          });
        }
      });

      if (this.riosVisible) {
        this.riosLayer.addTo(this.map);
      }
    }
  }

  /**
   * Configura las capas base de teselas (Esri, IGN, OSM) y restaura la preferencia guardada o el modo del sistema
   */
  _setupBaseLayers() {
    let defaultLayer = null;
    const systemDefaultId = StorageManager.getDefaultBasemapId();
    const savedPrefs = StorageManager.load();
    const savedBasemapId = savedPrefs.basemapId || systemDefaultId;

    Object.values(CONFIG.basemaps).forEach((bm) => {
      const tileLayer = L.tileLayer(bm.url, {
        attribution: bm.attribution,
        subdomains: bm.subdomains || 'abc',
        maxZoom: bm.maxZoom || 19
      });

      this.baseLayers[bm.name] = tileLayer;
      this.layerNameToId[bm.name] = bm.id;

      // Si coincide con el guardado en localStorage o con el predeterminado del sistema
      const isSavedActive = (savedBasemapId && bm.id === savedBasemapId);

      if (isSavedActive && !defaultLayer) {
        defaultLayer = tileLayer;
        this.currentBasemapId = bm.id;
        tileLayer.addTo(this.map);
      }
    });

    // Si ninguna fue marcada, usar el predeterminado según el sistema
    if (!defaultLayer && Object.values(this.baseLayers).length > 0) {
      const targetBm = Object.values(CONFIG.basemaps).find(b => b.id === systemDefaultId) || Object.values(CONFIG.basemaps)[0];
      defaultLayer = this.baseLayers[targetBm.name];
      this.currentBasemapId = targetBm.id;
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
   * Cambia programáticamente el mapa base activo y actualiza los estilos dependientes
   * @param {string} basemapId
   */
  switchBasemap(basemapId) {
    const targetBm = Object.values(CONFIG.basemaps).find(b => b.id === basemapId);
    if (!targetBm || !this.baseLayers[targetBm.name]) return;

    Object.values(this.baseLayers).forEach(layer => {
      if (this.map.hasLayer(layer)) {
        this.map.removeLayer(layer);
      }
    });

    const newLayer = this.baseLayers[targetBm.name];
    this.map.addLayer(newLayer);
    this.currentBasemapId = basemapId;
    StorageManager.setBasemap(basemapId);
    this.updateCcaaStyle(basemapId);
    if (this.uiManager && this.uiManager.updateActiveBasemapUI) {
      this.uiManager.updateActiveBasemapUI(basemapId);
    }
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
