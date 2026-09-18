import { CONFIG, formatMadridDateTime, formatMadridTime } from './config.js';
import { StorageManager } from './storage.js';

export class UIManager {
  constructor() {
    this.legendContainer = document.getElementById('map-legend');
    this.statusBadge = document.getElementById('status-badge');
    this.featuresCountEl = document.getElementById('features-count');
    this.coordsDisplay = document.getElementById('mouse-coords');
    this.opacitySlider = document.getElementById('opacity-slider');
    this.opacityValEl = document.getElementById('opacity-val');
    this.opacityContainerEl = document.getElementById('cuencas-opacity-container');
    this.toggleCuencasEl = document.getElementById('toggle-cuencas-visibility');
    this.favBtnEl = document.getElementById('btn-favorite-basin');
    this.realtimeListEl = document.getElementById('realtime-layer-list');
    this.predictionListEl = document.getElementById('prediction-layer-list');
    this.tabButtons = document.querySelectorAll('.panel-tab-btn');
    this.tabContents = {
      realtime: document.getElementById('tab-content-realtime'),
      prediction: document.getElementById('tab-content-prediction')
    };
    this.layerManager = null;
  }

  /**
   * Inicializa la interfaz y listeners de la UI
   * @param {MapManager} mapManager 
   * @param {CuencasLayer} cuencasLayer 
   * @param {LayerManager} layerManager 
   */
  init(mapManager, cuencasLayer = null, layerManager = null) {
    this.mapManager = mapManager;
    this.cuencasLayer = cuencasLayer;
    this.layerManager = layerManager;
    if (this.layerManager) {
      this.layerManager.uiManager = this;
    }

    // Cargar y aplicar valor de opacidad y visibilidad guardados en localStorage
    const savedPrefs = StorageManager.load();
    const isCuencasVisible = (savedPrefs.cuencasVisible !== undefined) ? Boolean(savedPrefs.cuencasVisible) : true;

    if (this.toggleCuencasEl) {
      this.toggleCuencasEl.checked = isCuencasVisible;
      this._updateCuencasUIState(isCuencasVisible);

      this.toggleCuencasEl.addEventListener('change', (e) => {
        const visible = e.target.checked;
        this._updateCuencasUIState(visible);
        if (this.cuencasLayer) {
          this.cuencasLayer.setVisible(visible);
        }
      });
    }

    if (this.opacitySlider && this.opacityValEl) {
      const savedOpacityPct = Math.round((savedPrefs.fillOpacity || 0.25) * 100);
      this.opacitySlider.value = savedOpacityPct;
      this.opacityValEl.textContent = `${savedOpacityPct}%`;

      this.opacitySlider.addEventListener('input', (e) => {
        const val = e.target.value;
        this.opacityValEl.textContent = `${val}%`;
        if (this.cuencasLayer) {
          this.cuencasLayer.setFillOpacity(val / 100);
        }
      });
    }

    // Botón de reestablecer vista a Comunitat Valenciana
    const resetBtn = document.getElementById('btn-reset-view');
    if (resetBtn) {
      resetBtn.addEventListener('click', () => {
        this.mapManager.resetView();
      });
    }

    // Botón de acceso rápido a cuenca favorita
    if (this.favBtnEl) {
      this.favBtnEl.addEventListener('click', () => {
        const currentPrefs = StorageManager.load();
        if (currentPrefs.favoriteBasinId && this.cuencasLayer) {
          this.cuencasLayer.focusOnBasin(currentPrefs.favoriteBasinId, true);
        }
      });
    }

    // Inicializar listeners de pestañas (Tiempo Real / Predicción)
    this._initTabs(savedPrefs.activeTab || 'realtime');

    // Renderizar listas de capas de Tiempo Real y Predicción
    this.renderLayerCards();

    // Listener de coordenadas en tiempo real al mover el ratón sobre el mapa
    if (this.coordsDisplay && this.mapManager.map) {
      this.mapManager.map.on('mousemove', (e) => {
        const lat = e.latlng.lat.toFixed(4);
        const lng = e.latlng.lng.toFixed(4);
        this.coordsDisplay.textContent = `${lat}°, ${lng}°`;
      });
    }

    // Renderizar leyenda inicial e indicador de favorita
    this.renderLegend();
    this.renderFavoriteBadge();
  }

  setCuencasLayer(cuencasLayer) {
    this.cuencasLayer = cuencasLayer;
    this.renderFavoriteBadge();

    // Asegurar que el estado del toggle refleje cuencasLayer.isVisible
    if (this.toggleCuencasEl && this.cuencasLayer) {
      this.toggleCuencasEl.checked = this.cuencasLayer.isVisible;
      this._updateCuencasUIState(this.cuencasLayer.isVisible);
    }
  }

  /**
   * Actualiza el estado visual de los controles relacionados con las cuencas
   * @param {boolean} visible 
   */
  _updateCuencasUIState(visible) {
    if (this.opacityContainerEl) {
      this.opacityContainerEl.classList.toggle('disabled', !visible);
    }
    if (this.statusBadge) {
      if (visible) {
        this.statusBadge.className = 'badge badge-success';
        this.statusBadge.innerHTML = '<span class="status-dot"></span> Capa Activa';
      } else {
        this.statusBadge.className = 'badge badge-info';
        this.statusBadge.innerHTML = '<span class="status-dot" style="animation:none; background:#94a3b8;"></span> Capa Oculta';
      }
    }
  }

  setLayerManager(layerManager) {
    this.layerManager = layerManager;
    this.renderLayerCards();
  }

  /**
   * Configura las pestañas de Tiempo Real y Predicción
   * @param {string} initialTab 
   */
  _initTabs(initialTab) {
    this.tabButtons.forEach(btn => {
      btn.addEventListener('click', () => {
        const tab = btn.getAttribute('data-tab');
        this.switchTab(tab);
      });
    });

    this.switchTab(initialTab);
  }

  /**
   * Cambia la pestaña activa
   * @param {string} tabId 'realtime' | 'prediction'
   */
  switchTab(tabId) {
    this.tabButtons.forEach(btn => {
      const match = btn.getAttribute('data-tab') === tabId;
      btn.classList.toggle('active', match);
      btn.setAttribute('aria-selected', match ? 'true' : 'false');
    });

    Object.keys(this.tabContents).forEach(key => {
      const content = this.tabContents[key];
      if (content) {
        if (key === tabId) {
          content.classList.add('active');
          content.style.display = 'block';
        } else {
          content.classList.remove('active');
          content.style.display = 'none';
        }
      }
    });

    StorageManager.setActiveTab(tabId);
  }

  /**
   * Renderiza las tarjetas de capas en los contenedores correspondientes
   */
  renderLayerCards() {
    if (!this.realtimeListEl || !this.predictionListEl) return;

    const prefs = StorageManager.load();
    const activeLayers = prefs.activeLayers || {};
    const layerOpacities = prefs.layerOpacities || {};

    // Renderizar Tiempo Real
    this.realtimeListEl.innerHTML = CONFIG.overlayLayers.realtime.map(layer => {
      const isActive = activeLayers[layer.id] !== undefined ? activeLayers[layer.id] : layer.defaultActive;
      const opacity = layerOpacities[layer.id] !== undefined ? layerOpacities[layer.id] : layer.defaultOpacity;
      return this._createLayerCardHtml(layer, isActive, opacity);
    }).join('');

    // Renderizar Predicción
    this.predictionListEl.innerHTML = CONFIG.overlayLayers.prediction.map(layer => {
      const isActive = activeLayers[layer.id] !== undefined ? activeLayers[layer.id] : layer.defaultActive;
      const opacity = layerOpacities[layer.id] !== undefined ? layerOpacities[layer.id] : layer.defaultOpacity;
      return this._createLayerCardHtml(layer, isActive, opacity);
    }).join('');

    // Vincular listeners a los switches y sliders generados
    this._bindLayerCardEvents();
  }

  /**
   * Genera el HTML para cada tarjeta de capa
   */
  _createLayerCardHtml(layer, isActive, opacity) {
    const opacityPct = Math.round(opacity * 100);
    const prefs = StorageManager.load();
    const refreshSec = prefs.autoRefreshInterval !== undefined ? prefs.autoRefreshInterval : 180;

    // Sub-controles específicos para la capa de Radar
    let radarExtraControls = '';
    if (layer.id === 'radar') {
      radarExtraControls = `
        <div class="radar-subcontrols">
          <div class="radar-dbz-legend">
            <span class="dbz-legend-title">Reflectividad (dBZ):</span>
            <div class="dbz-bar">
              <span style="background: #38bdf8;" title="5-15 dBZ (Muy débil)">5</span>
              <span style="background: #0284c7;" title="15-25 dBZ (Débil)">15</span>
              <span style="background: #22c55e;" title="25-35 dBZ (Moderada)">25</span>
              <span style="background: #eab308;" title="35-45 dBZ (Fuerte)">35</span>
              <span style="background: #f97316;" title="45-55 dBZ (Muy fuerte)">45</span>
              <span style="background: #ef4444;" title="55+ dBZ (Granizo)">55+</span>
            </div>
          </div>

          <div class="radar-source-link">
            <a href="https://radarspain.es" target="_blank" rel="noopener noreferrer" class="radar-external-link" title="Abrir RadarSpain.es en una nueva pestaña">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"></path><polyline points="15 3 21 3 21 9"></polyline><line x1="10" y1="14" x2="21" y2="3"></line></svg>
              <span>Ver en RadarSpain.es ↗</span>
            </a>
          </div>

          <!-- Opción integrada de Rayos en Tiempo Real dentro del Radar -->
          <div class="radar-lightning-option">
            <label class="radar-lightning-toggle-label">
              <input type="checkbox" id="radar-lightning-toggle" ${prefs.showRadarLightning ? 'checked' : ''}>
              <span class="lightning-toggle-text">⚡ Mostrar Rayos en Cobertura</span>
            </label>

            <div class="radar-lightning-details" id="radar-lightning-container" style="${prefs.showRadarLightning ? '' : 'display: none;'}">
              <div class="lightning-header-row">
                <span class="subcontrol-label">Ventana:</span>
                <select class="lightning-window-select" id="radar-lightning-window">
                  <option value="15" ${(prefs.lightningWindow || 15) === 15 ? 'selected' : ''}>Últimos 15 min</option>
                  <option value="5" ${(prefs.lightningWindow || 15) === 5 ? 'selected' : ''}>Últimos 5 min</option>
                  <option value="1" ${(prefs.lightningWindow || 15) === 1 ? 'selected' : ''}>Último 1 min</option>
                </select>
              </div>

              <div class="lightning-stats-row">
                <span class="lightning-stats-pill" id="radar-lightning-stats">⚡ <strong>0.0</strong> r/min · Total: <strong>0</strong></span>
                <button type="button" class="lightning-now-btn" id="radar-lightning-refresh-btn" title="Actualizar rayos en cobertura">
                  ↻
                </button>
              </div>

              <div class="lightning-age-legend">
                <span class="age-legend-title">Antigüedad del impacto:</span>
                <div class="age-pills-row">
                  <span class="age-pill"><span class="dot" style="background:#ffff00; box-shadow: 0 0 6px #ffff00;"></span> 0-1m</span>
                  <span class="age-pill"><span class="dot" style="background:#ff9900;"></span> 1-5m</span>
                  <span class="age-pill"><span class="dot" style="background:#ef4444;"></span> 5-15m</span>
                </div>
              </div>
            </div>
          </div>
        </div>
      `;
    }

    return `
      <div class="layer-card ${isActive ? 'active' : ''}" data-layer-id="${layer.id}">
        <div class="layer-card-main">
          <label class="layer-switch" title="Activar/desactivar capa ${escapeHtml(layer.name)}">
            <input type="checkbox" class="layer-toggle-input" data-layer-id="${layer.id}" ${isActive ? 'checked' : ''}>
            <span class="switch-slider"></span>
          </label>
          <div class="layer-card-info">
            <div class="layer-card-title-row">
              <span class="layer-icon" style="color: ${layer.color};">${layer.icon}</span>
              <span class="layer-card-name">${escapeHtml(layer.name)}</span>
            </div>
            <span class="layer-card-subtitle">${escapeHtml(layer.subtitle)}</span>
            <div class="layer-timestamp-pill" id="timestamp-pill-${layer.id}">
              <span class="timestamp-indicator"></span>
              <span class="timestamp-val" id="time-val-${layer.id}">${this._getDefaultLayerTimestamp(layer.id)}</span>
            </div>
          </div>
        </div>
        <div class="layer-card-controls" id="controls-${layer.id}" style="${isActive ? '' : 'display: none;'}">
          <div class="layer-opacity-row">
            <span class="layer-opacity-label">Opacidad</span>
            <span class="layer-opacity-value" id="val-${layer.id}">${opacityPct}%</span>
          </div>
          <input type="range" class="slider-glass layer-slider" min="10" max="100" value="${opacityPct}" step="5" data-layer-id="${layer.id}">
          ${radarExtraControls}
        </div>
      </div>
    `;
  }

  /**
   * Genera el texto inicial de fecha/hora para cada capa
   * @param {string} layerId 
   */
  _getDefaultLayerTimestamp(layerId) {
    const now = new Date();
    const nowFormatted = formatMadridDateTime(now);
    const timeOnly = formatMadridTime(now);

    switch (layerId) {
      case 'radar':
        return `Captura: <strong>${nowFormatted}</strong>`;
      case 'aemet_warnings':
        return `Vigencia: <strong>${nowFormatted}</strong>`;
      case 'arome_precip':
        return `Pasada: <strong>${nowFormatted.split(' · ')[0]} · 02:00 (+6h)</strong>`;
      case 'icon_d2':
        return `Pasada: <strong>${nowFormatted.split(' · ')[0]} · 05:00 (+3h)</strong>`;
      case 'ecmwf_ifs':
        return `Pasada: <strong>${nowFormatted.split(' · ')[0]} · 02:00 (+12h)</strong>`;
      default:
        return `Fecha: <strong>${nowFormatted}</strong>`;
    }
  }

  /**
   * Actualiza dinámicamente la fecha y hora mostrada en la cajita de una capa
   * @param {string} layerId 
   * @param {string} formattedHtml 
   */
  updateLayerTimestamp(layerId, formattedHtml) {
    const el = document.getElementById(`time-val-${layerId}`);
    if (el) {
      el.innerHTML = formattedHtml;
    }
  }

  /**
   * Vincula listeners interactivos a los switches y sliders de capas
   */
  _bindLayerCardEvents() {
    // Switches de activación
    document.querySelectorAll('.layer-toggle-input').forEach(checkbox => {
      checkbox.addEventListener('change', (e) => {
        const layerId = e.target.getAttribute('data-layer-id');
        const isChecked = e.target.checked;
        const card = document.querySelector(`.layer-card[data-layer-id="${layerId}"]`);
        const controls = document.getElementById(`controls-${layerId}`);

        if (card) {
          card.classList.toggle('active', isChecked);
        }
        if (controls) {
          controls.style.display = isChecked ? 'block' : 'none';
        }

        if (this.layerManager) {
          this.layerManager.toggleLayer(layerId, isChecked);
        }
      });
    });

    // Sliders de opacidad individual
    document.querySelectorAll('.layer-slider').forEach(slider => {
      slider.addEventListener('input', (e) => {
        const layerId = e.target.getAttribute('data-layer-id');
        const val = e.target.value;
        const valEl = document.getElementById(`val-${layerId}`);
        if (valEl) {
          valEl.textContent = `${val}%`;
        }
        if (this.layerManager) {
          this.layerManager.setLayerOpacity(layerId, val / 100);
        }
      });
    });


    // Controles de Rayos integrados en el Radar: Toggle de activación
    const radarLightningToggle = document.getElementById('radar-lightning-toggle');
    if (radarLightningToggle) {
      radarLightningToggle.addEventListener('change', (e) => {
        const isChecked = e.target.checked;
        const container = document.getElementById('radar-lightning-container');
        if (container) {
          container.style.display = isChecked ? 'block' : 'none';
        }
        StorageManager.save({ showRadarLightning: isChecked });
        if (this.layerManager) {
          this.layerManager.toggleRadarLightning(isChecked);
        }
      });
    }

    // Controles de Rayos: Selector de ventana temporal (15, 5, 1 min)
    const radarLightningWindow = document.getElementById('radar-lightning-window');
    if (radarLightningWindow) {
      radarLightningWindow.addEventListener('change', (e) => {
        const minutes = parseInt(e.target.value, 10);
        StorageManager.save({ lightningWindow: minutes });
        if (this.layerManager) {
          this.layerManager.setLightningWindow(minutes);
        }
      });
    }

    // Botón manual de refresco de rayos en cobertura
    const radarLightningRefreshBtn = document.getElementById('radar-lightning-refresh-btn');
    if (radarLightningRefreshBtn) {
      radarLightningRefreshBtn.addEventListener('click', () => {
        radarLightningRefreshBtn.classList.add('rotating');
        if (this.layerManager) {
          this.layerManager.reloadLightningLayer();
        }
        setTimeout(() => radarLightningRefreshBtn.classList.remove('rotating'), 800);
      });
    }
  }


  /**
   * Actualiza el botón indicador de cuenca favorita en el encabezado
   */
  renderFavoriteBadge() {
    if (!this.favBtnEl) return;
    const prefs = StorageManager.load();

    if (prefs.favoriteBasinId && prefs.favoriteBasinName) {
      this.favBtnEl.style.display = 'inline-flex';
      this.favBtnEl.innerHTML = `
        <svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor" stroke="currentColor" stroke-width="2">
          <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"></polygon>
        </svg>
        <span class="fav-label-text">${escapeHtml(prefs.favoriteBasinName)}</span>
      `;
      this.favBtnEl.title = `Centrar en cuenca favorita: ${prefs.favoriteBasinName}`;
    } else {
      this.favBtnEl.style.display = 'none';
    }
  }

  /**
   * Métodos no-op mantenidos por compatibilidad tras retirar el HUD de demarcación
   */
  updateHoverInfo(_properties) {}

  resetHoverInfo() {}

  /**
   * Genera la leyenda cartográfica contraíble por Sistemas de Explotación CHJ
   */
  renderLegend() {
    if (!this.legendContainer) return;

    const systems = Object.keys(CONFIG.systemColors).filter(s => s !== 'Default');
    const miniDots = systems.map(sys => `<span class="legend-mini-dot" style="background-color: ${CONFIG.systemColors[sys]};"></span>`).join('');

    let html = `
      <div class="legend-header">
        <div class="legend-title-row">
          <div class="legend-title-group">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <polygon points="12 2 2 7 12 12 22 7 12 2"></polygon>
              <polyline points="2 17 12 22 22 17"></polyline>
              <polyline points="2 12 12 17 22 12"></polyline>
            </svg>
            <h4>Leyenda Cuencas</h4>
          </div>
          <svg class="legend-chevron" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <polyline points="6 9 12 15 18 9"></polyline>
          </svg>
        </div>
        <div class="legend-mini-preview" aria-hidden="true">
          ${miniDots}
        </div>
      </div>
      <div class="legend-body">
        <div class="legend-subtitle">${systems.length} Sistemas de Explotación CHJ</div>
        <div class="legend-items">
    `;

    systems.forEach(sys => {
      const color = CONFIG.systemColors[sys];
      html += `
        <div class="legend-item" title="Sistema ${sys}">
          <span class="legend-color-chip" style="background-color: ${color}; border-color: ${color};"></span>
          <span class="legend-label">${sys}</span>
        </div>
      `;
    });

    html += `
        </div>
      </div>
    `;
    this.legendContainer.innerHTML = html;
  }

  /**
   * Actualiza el badge de estado tras la carga de datos
   * @param {number} count Número de geometrías cargadas
   */
  setLoadedState(count) {
    const isVisible = this.cuencasLayer ? this.cuencasLayer.isVisible : true;
    this._updateCuencasUIState(isVisible);
    if (this.featuresCountEl) {
      this.featuresCountEl.textContent = `${count} subsistemas cargados`;
    }
  }

  /**
   * Muestra estado de error
   * @param {string} msg Mensaje descriptivo
   */
  setErrorState(msg) {
    if (this.statusBadge) {
      this.statusBadge.className = 'badge badge-error';
      this.statusBadge.innerHTML = '<span class="status-dot"></span> Error de Carga';
    }
    if (this.featuresCountEl) {
      this.featuresCountEl.textContent = msg;
    }
  }
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
