/**
 * RainLoc - Capa de Cuencas y Subsistemas Hidrográficos (CHJ)
 * Carga asíncrona de GeoJSON, renderizado vectorial y gestión de interacciones hover/click.
 */

import { CONFIG } from './config.js';
import { StorageManager } from './storage.js';

export class CuencasLayer {
  constructor(mapManager, uiManager) {
    this.mapManager = mapManager;
    this.map = mapManager.map;
    this.uiManager = uiManager;
    this.geoJsonLayer = null;
    this.featuresData = [];
    this.selectedFeatureId = null;
    this.activeLayer = null;
    this.layersById = {};
    this.hoverHighlightLayer = null;
    this.selectedHighlightLayer = null;
    this._hoveredFeatureId = null;
    
    // Cargar opacidad y visibilidad de preferencias guardadas o por defecto
    const savedPrefs = StorageManager.load();
    this.currentFillOpacity = savedPrefs.fillOpacity || CONFIG.styles.cuencaDefault.fillOpacity;
    this.isVisible = (savedPrefs.cuencasVisible !== undefined) ? Boolean(savedPrefs.cuencasVisible) : true;
  }

  /**
   * Conmuta o establece la visibilidad de la capa de cuencas
   * @param {boolean} visible 
   */
  setVisible(visible) {
    this.isVisible = Boolean(visible);
    StorageManager.setCuencasVisible(this.isVisible);

    if (this.geoJsonLayer) {
      if (this.isVisible) {
        if (!this.map.hasLayer(this.geoJsonLayer)) {
          this.map.addLayer(this.geoJsonLayer);
        }
      } else {
        if (this.map.hasLayer(this.geoJsonLayer)) {
          this.map.removeLayer(this.geoJsonLayer);
        }
        this.clearHoverHighlight();
        this.clearSelectedHighlight();
      }
    }
  }

  /**
   * Actualiza la opacidad global de todas las geometrías (bordes y relleno) en tiempo real
   * La cuenca favorita mantiene su borde dorado siempre al 100% de opacidad, pero su interior sí responde a la opacidad
   * @param {number} opacity Valor entre 0.0 y 1.0
   */
  setFillOpacity(opacity) {
    this.currentFillOpacity = parseFloat(opacity);
    StorageManager.setOpacity(this.currentFillOpacity);
    if (this.geoJsonLayer) {
      this.geoJsonLayer.eachLayer((layer) => {
        const feature = layer.feature;
        if (feature) {
          const isFav = StorageManager.isFavorite(feature.id);
          layer.setStyle({
            opacity: isFav ? 1.0 : this.currentFillOpacity,
            fillOpacity: this.currentFillOpacity
          });
        }
      });
    }
    if (this.selectedHighlightLayer && this.selectedFeatureId && this.layersById[this.selectedFeatureId]) {
      const item = this.layersById[this.selectedFeatureId];
      this.setSelectedHighlight(item.feature);
    }
  }

  /**
   * Recarga los estilos de todas las cuencas (usado al marcar/desmarcar favorita)
   */
  refreshStyles() {
    if (!this.geoJsonLayer) return;
    this.geoJsonLayer.eachLayer((layer) => {
      const feature = layer.feature;
      if (feature) {
        layer.setStyle(this.getFeatureStyle(feature));
        if (StorageManager.isFavorite(feature.id)) {
          layer.bringToFront();
        }
      }
    });
    if (this.selectedFeatureId && this.layersById[this.selectedFeatureId]) {
      const item = this.layersById[this.selectedFeatureId];
      this._selectFeature(item.feature, item.layer);
    }
  }

  /**
   * Carga asíncrona del GeoJSON buscando en rutas candidatas
   */
  async load() {
    let geojsonData = null;
    let loadedFrom = '';

    for (const url of CONFIG.dataSources.subsistemasGeoJson) {
      try {
        const response = await fetch(url);
        if (response.ok) {
          geojsonData = await response.json();
          loadedFrom = url;
          break;
        }
      } catch (err) {
        // Intentar siguiente ruta
      }
    }

    if (!geojsonData || !geojsonData.features) {
      throw new Error(`No se pudo cargar el archivo de subsistemas GeoJSON desde ninguna de las rutas: ${CONFIG.dataSources.subsistemasGeoJson.join(', ')}`);
    }

    this.featuresData = geojsonData.features;
    this._createLayer(geojsonData);

    return {
      featuresCount: geojsonData.features.length,
      sourceUrl: loadedFrom
    };
  }

  /**
   * Genera el estilo de cada polígono de cuenca
   * @param {GeoJSON.Feature} feature 
   */
  getFeatureStyle(feature) {
    const props = feature.properties || {};
    const sistema = props.NomSistExp || 'Default';
    const systemColor = CONFIG.systemColors[sistema] || CONFIG.systemColors['Default'];
    const isFav = StorageManager.isFavorite(feature.id);

    if (isFav) {
      return {
        ...CONFIG.styles.cuencaDefault,
        ...CONFIG.styles.cuencaFavorite,
        color: '#f59e0b',
        opacity: 1.0, // El borde dorado es inmune a la opacidad global
        fillColor: systemColor,
        fillOpacity: this.currentFillOpacity
      };
    }

    return {
      ...CONFIG.styles.cuencaDefault,
      color: systemColor,
      opacity: this.currentFillOpacity,
      fillColor: systemColor,
      fillOpacity: this.currentFillOpacity
    };
  }

  /**
   * Construye y monta la capa GeoJSON en Leaflet
   * @param {GeoJSON.FeatureCollection} geojsonData 
   */
  _createLayer(geojsonData) {
    this.geoJsonLayer = L.geoJSON(geojsonData, {
      pane: 'cuencasPane',
      style: (feature) => this.getFeatureStyle(feature),
      onEachFeature: (feature, layer) => this._bindFeatureEvents(feature, layer)
    });

    // Añadir al mapa si la visibilidad está activa
    if (this.isVisible) {
      this.geoJsonLayer.addTo(this.map);
    }

    // Si hay una cuenca favorita guardada, traerla al frente para que su borde dorado destaque
    const savedFavId = StorageManager.load().favoriteBasinId;
    if (savedFavId) {
      Object.values(this.layersById).forEach(({ feature, layer }) => {
        if (StorageManager.isFavorite(feature.id)) {
          layer.bringToFront();
        }
      });
    }

    // Limpiar resalte de selección cuando se cierra el popup
    this.map.on('popupclose', () => {
      this.clearSelection();
    });

    // Registrar en el control de capas para futura gestión de overlays
    this.mapManager.addOverlayLayer(this.geoJsonLayer, 'Cuencas y Subsistemas (CHJ)');
  }

  /**
   * Vincula eventos de interacción (mouseover, mouseout, click) a cada geometría
   * @param {GeoJSON.Feature} feature 
   * @param {L.Layer} layer 
   */
  _bindFeatureEvents(feature, layer) {
    const props = feature.properties || {};
    const sistema = props.NomSistExp || 'Cuenca Hidrográfica';
    const subsistema = props.Subsistema || `Subsistema ${feature.id || ''}`;

    // Indexar capa para acceso rápido por ID
    if (feature.id) {
      this.layersById[feature.id] = { feature, layer };
    }

    // Eventos del cursor
    layer.on({
      mouseover: (e) => this._onMouseOver(e, feature, layer),
      mouseout: (e) => this._onMouseOut(e, feature, layer),
      click: (e) => this._onFeatureClick(e, feature, layer)
    });
  }

  /**
   * Resalta dinámicamente la cuenca en el panel de hover de máxima prioridad (por encima de todo)
   */
  setHoverHighlight(feature, systemColor) {
    if (!feature || !this.isVisible) return;
    if (this._hoveredFeatureId === feature.id && this.hoverHighlightLayer) return;

    this.clearHoverHighlight();
    this._hoveredFeatureId = feature.id;

    const props = feature.properties || {};
    const sistema = props.NomSistExp || 'Default';
    const color = systemColor || CONFIG.systemColors[sistema] || CONFIG.systemColors['Default'] || '#38bdf8';
    const isFav = StorageManager.isFavorite(feature.id);

    this.hoverHighlightLayer = L.geoJSON(feature, {
      pane: 'cuencasHoverPane',
      interactive: false,
      style: () => ({
        ...CONFIG.styles.cuencaHover,
        color: isFav ? '#fbbf24' : color,
        opacity: 1.0,
        fillColor: color,
        fillOpacity: Math.min(1.0, this.currentFillOpacity + 0.35)
      })
    }).addTo(this.map);
  }

  /**
   * Limpia el resalte de hover
   */
  clearHoverHighlight(featureId = null) {
    if (featureId && this._hoveredFeatureId !== featureId) {
      return;
    }
    if (this.hoverHighlightLayer) {
      this.map.removeLayer(this.hoverHighlightLayer);
      this.hoverHighlightLayer = null;
    }
    this._hoveredFeatureId = null;
  }

  /**
   * Manejador de evento mouseover (resalte dinámico de cuenca y actualización de UI)
   */
  _onMouseOver(e, feature, layer) {
    const props = feature.properties || {};
    const sistema = props.NomSistExp || 'Default';
    const systemColor = CONFIG.systemColors[sistema] || CONFIG.systemColors['Default'];

    this.setHoverHighlight(feature, systemColor);

    // Notificar al panel flotante de interfaz de usuario
    if (this.uiManager) {
      this.uiManager.updateHoverInfo(props);
    }
  }

  /**
   * Manejador de evento mouseout (restauración inmediata del estilo original)
   */
  _onMouseOut(e, feature, layer) {
    this.clearHoverHighlight(feature.id);

    // Resetear información en el panel HUD
    if (this.uiManager && this._hoveredFeatureId === null) {
      this.uiManager.resetHoverInfo();
    }
  }

  /**
   * Manejador de evento click (mostrar popup detallado y zoom opcional)
   */
  _onFeatureClick(e, feature, layer) {
    if (e && e.originalEvent && (e.originalEvent._caudalMarkerClicked || e.originalEvent._embalseMarkerClicked || e.originalEvent._stopBasinClick || e.originalEvent.defaultPrevented)) {
      return;
    }
    if (e && e.originalEvent && e.originalEvent.target) {
      const el = e.originalEvent.target;
      if (
        (el.classList && (el.classList.contains('caudal-marker') || el.classList.contains('embalse-marker'))) ||
        (el.closest && (el.closest('.caudal-marker') || el.closest('.embalse-marker')))
      ) {
        return;
      }
    }
    const props = feature.properties || {};
    const sistema = props.NomSistExp || 'Demarcación CHJ';
    const subsistema = props.Subsistema || `Subsistema ${feature.id || 'N/D'}`;
    const rawSuperf = props['Superf km2'] || props['Area km2'] || props.Superficie || null;
    const superfFormatted = rawSuperf ? formatNumber(rawSuperf) + ' km²' : 'No disponible';
    const systemColor = CONFIG.systemColors[sistema] || CONFIG.systemColors['Default'];
    const isFav = StorageManager.isFavorite(feature.id);

    // Popup enriquecido con estrella de favorita junto al título
    const popupContent = `
      <div class="rainloc-popup">
        <div class="popup-header" style="border-left: 4px solid ${systemColor};">
          <span class="popup-badge" style="background-color: ${systemColor}20; color: ${systemColor};">
            ${escapeHtml(sistema)}
          </span>
          <div class="popup-title-row">
            <h3 class="popup-title">${escapeHtml(subsistema)}</h3>
            <button type="button" class="popup-star-fav ${isFav ? 'is-fav' : ''}" data-fav-id="${feature.id}" title="${isFav ? 'Quitar de cuencas favoritas' : 'Marcar como cuenca favorita'}" aria-label="Favorito">
              <svg width="20" height="20" viewBox="0 0 24 24" fill="${isFav ? '#f59e0b' : 'none'}" stroke="${isFav ? '#f59e0b' : 'currentColor'}" stroke-width="2">
                <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"></polygon>
              </svg>
            </button>
          </div>
        </div>
        <div class="popup-body">
          <div class="popup-metric">
            <span class="metric-label">Superficie de Cuenca</span>
            <span class="metric-value">${superfFormatted}</span>
          </div>
        </div>
        <div class="popup-footer">
          <button type="button" class="popup-btn-zoom" data-feature-id="${feature.id}">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <circle cx="11" cy="11" r="8"></circle>
              <line x1="21" y1="21" x2="16.65" y2="16.65"></line>
            </svg>
            Centrar Cuenca
          </button>
        </div>
      </div>
    `;

    const popup = L.popup({
      className: 'rainloc-leaflet-popup',
      closeButton: true,
      autoPan: true,
      offset: [0, -10]
    })
    .setLatLng(e.latlng)
    .setContent(popupContent)
    .openOn(this.map);

    // Escuchar botones dentro del popup
    setTimeout(() => {
      const zoomBtn = document.querySelector(`.popup-btn-zoom[data-feature-id="${feature.id}"]`);
      if (zoomBtn) {
        zoomBtn.addEventListener('click', () => {
          this.mapManager.fitBounds(layer.getBounds());
        });
      }

      const favBtn = document.querySelector(`.popup-star-fav[data-fav-id="${feature.id}"]`);
      if (favBtn) {
        favBtn.addEventListener('click', (ev) => {
          ev.stopPropagation();
          const currentlyFav = StorageManager.isFavorite(feature.id);
          const svg = favBtn.querySelector('svg');
          if (currentlyFav) {
            StorageManager.removeFavoriteBasin();
            favBtn.classList.remove('is-fav');
            favBtn.title = 'Marcar como cuenca favorita';
            svg.setAttribute('fill', 'none');
            svg.setAttribute('stroke', 'currentColor');
          } else {
            StorageManager.setFavoriteBasin(feature.id, subsistema);
            favBtn.classList.add('is-fav');
            favBtn.title = 'Quitar de cuencas favoritas';
            svg.setAttribute('fill', '#f59e0b');
            svg.setAttribute('stroke', '#f59e0b');
          }

          // Actualizar estilos en el mapa en tiempo real
          this.refreshStyles();

          if (this.uiManager) {
            this.uiManager.renderFavoriteBadge();
          }
        });
      }
    }, 50);

    // Fijar selección en capa
    this._selectFeature(feature, layer);
  }

  /**
   * Enfoca y centra la vista en una cuenca específica (por ejemplo al cargar la favorita)
   * @param {string} featureId 
   * @param {boolean} openPopup 
   */
  focusOnBasin(featureId, openPopup = false) {
    const item = this.layersById[featureId];
    if (!item) return;
    const { feature, layer } = item;
    this.mapManager.fitBounds(layer.getBounds());
    this._selectFeature(feature, layer);
    if (openPopup) {
      const bounds = layer.getBounds();
      const center = bounds.getCenter();
      this._onFeatureClick({ latlng: center }, feature, layer);
    }
  }

  /**
   * Resalta visualmente una cuenca seleccionada en el panel prioritario
   */
  setSelectedHighlight(feature) {
    this.clearSelectedHighlight();
    if (!feature || !this.isVisible) return;

    const props = feature.properties || {};
    const sistema = props.NomSistExp || 'Default';
    const systemColor = CONFIG.systemColors[sistema] || CONFIG.systemColors['Default'];
    const isFav = StorageManager.isFavorite(feature.id);

    const selectStyle = {
      ...CONFIG.styles.cuencaSelected,
      color: isFav ? '#f59e0b' : '#ffffff',
      weight: 4.2,
      opacity: 1.0,
      fillColor: systemColor,
      fillOpacity: Math.min(1.0, this.currentFillOpacity + 0.35)
    };

    this.selectedHighlightLayer = L.geoJSON(feature, {
      pane: 'cuencasHoverPane',
      interactive: false,
      style: () => selectStyle
    }).addTo(this.map);
  }

  /**
   * Limpia el resalte de selección
   */
  clearSelectedHighlight() {
    if (this.selectedHighlightLayer) {
      this.map.removeLayer(this.selectedHighlightLayer);
      this.selectedHighlightLayer = null;
    }
  }

  /**
   * Resalta visualmente una cuenca seleccionada
   */
  _selectFeature(feature, layer) {
    if (this.activeLayer && this.selectedFeatureId && this.activeLayer !== layer) {
      if (this.activeLayer.feature) {
        this.activeLayer.setStyle(this.getFeatureStyle(this.activeLayer.feature));
      }
    }
    this.selectedFeatureId = feature.id;
    this.activeLayer = layer;

    const props = feature.properties || {};
    const sistema = props.NomSistExp || 'Default';
    const systemColor = CONFIG.systemColors[sistema] || CONFIG.systemColors['Default'];
    const isFav = StorageManager.isFavorite(feature.id);

    const selectStyle = {
      ...CONFIG.styles.cuencaSelected,
      color: isFav ? '#f59e0b' : '#ffffff',
      weight: 4.2,
      opacity: 1.0,
      fillColor: systemColor,
      fillOpacity: Math.min(1.0, this.currentFillOpacity + 0.35)
    };

    layer.setStyle(selectStyle);
    this.setSelectedHighlight(feature);
  }

  /**
   * Limpia la selección actual y restaura el estilo original
   */
  clearSelection() {
    if (this.activeLayer && this.activeLayer.feature) {
      this.activeLayer.setStyle(this.getFeatureStyle(this.activeLayer.feature));
      this.activeLayer = null;
      this.selectedFeatureId = null;
    }
    this.clearSelectedHighlight();
  }
}

// Utilidades auxiliares
function escapeHtml(str) {
  if (str === null || str === undefined) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

function formatNumber(num) {
  const n = parseFloat(num);
  if (isNaN(n)) return String(num);
  return n.toLocaleString('es-ES', { minimumFractionDigits: 1, maximumFractionDigits: 2 });
}
