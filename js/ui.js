import { CONFIG, formatMadridDateTime, formatMadridTime, formatPredictionInstant } from './config.js';
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
    this.toggleCcaaEl = document.getElementById('toggle-ccaa-visibility');
    this.favBtnEl = document.getElementById('btn-favorite-basin');
    this.realtimeListEl = document.getElementById('realtime-layer-list');
    this.predictionListEl = document.getElementById('prediction-layer-list');
    this.tabButtons = document.querySelectorAll('.panel-tab-btn');
    this.tabContents = {
      realtime: document.getElementById('tab-content-realtime'),
      prediction: document.getElementById('tab-content-prediction')
    };
    this.predictionBanner = document.getElementById('prediction-time-banner');
    this.predictionBannerModel = document.getElementById('prediction-banner-model');
    this.predictionBannerMode = document.getElementById('prediction-banner-mode');
    this.predictionBannerExactTime = document.getElementById('prediction-banner-exact-time');
    this.layerManager = null;

    // Elementos exclusivos de la versión móvil
    this.mobileBottomBar = document.getElementById('mobile-bottom-bar');
    this.btnMobileLayers = document.getElementById('btn-mobile-layers');
    this.btnMobileSettings = document.getElementById('btn-mobile-settings');
    this.mobileLayersBadge = document.getElementById('mobile-layers-badge');
    this.layersDrawer = document.getElementById('left-sidebar-container');
    this.settingsDrawer = document.getElementById('mobile-settings-drawer');
    this.drawerBackdrop = document.getElementById('mobile-drawer-backdrop');
    this.btnCloseLayers = document.getElementById('btn-close-layers-drawer');
    this.btnCloseSettings = document.getElementById('btn-close-settings-drawer');
    this.toggleCuencasMobileEl = document.getElementById('toggle-cuencas-mobile');
    this.toggleCcaaMobileEl = document.getElementById('toggle-ccaa-mobile');
    this.opacitySliderMobile = document.getElementById('opacity-slider-mobile');
    this.opacityValMobileEl = document.getElementById('opacity-val-mobile');
    this.cuencasOpacityContainerMobile = document.getElementById('cuencas-opacity-container-mobile');
    this.btnResetViewMobile = document.getElementById('btn-reset-view-mobile');
    this.btnFavoriteBasinMobile = document.getElementById('btn-favorite-basin-mobile');
    this.favBasinMobileLabel = document.getElementById('fav-basin-mobile-label');
    this.mobileBasemapSelector = document.getElementById('mobile-basemap-selector');
  }

  /**
   * Inicializa la interfaz y listeners de la UI
   * @param {MapManager} mapManager 
   * @param {CuencasLayer} cuencasLayer 
   * @param {LayerManager} layerManager 
   */
  init(mapManager, cuencasLayer = null, layerManager = null) {
    this.mapManager = mapManager;
    if (this.mapManager) {
      this.mapManager.uiManager = this;
    }
    this.cuencasLayer = cuencasLayer;
    this.layerManager = layerManager;
    if (this.layerManager) {
      this.layerManager.uiManager = this;
    }

    // Cargar y aplicar valor de opacidad y visibilidad guardados en localStorage
    const savedPrefs = StorageManager.load();
    const isCuencasVisible = (savedPrefs.cuencasVisible !== undefined) ? Boolean(savedPrefs.cuencasVisible) : true;
    const isCcaaVisible = (savedPrefs.ccaaVisible !== undefined) ? Boolean(savedPrefs.ccaaVisible) : true;

    if (this.toggleCuencasEl) {
      this.toggleCuencasEl.checked = isCuencasVisible;
      this._updateCuencasUIState(isCuencasVisible);

      this.toggleCuencasEl.addEventListener('change', (e) => {
        const visible = e.target.checked;
        if (this.toggleCuencasMobileEl) this.toggleCuencasMobileEl.checked = visible;
        this._updateCuencasUIState(visible);
        if (this.cuencasLayer) {
          this.cuencasLayer.setVisible(visible);
        }
      });
    }

    if (this.toggleCcaaEl) {
      this.toggleCcaaEl.checked = isCcaaVisible;
      this.toggleCcaaEl.addEventListener('change', (e) => {
        const visible = e.target.checked;
        if (this.toggleCcaaMobileEl) this.toggleCcaaMobileEl.checked = visible;
        if (this.mapManager) {
          this.mapManager.setCcaaVisible(visible);
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
        if (this.opacityValMobileEl) this.opacityValMobileEl.textContent = `${val}%`;
        if (this.opacitySliderMobile) this.opacitySliderMobile.value = val;
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

    // Inicializar navegación y ajustes móviles
    this._initMobileNavigation();
    this._initMobileSettings();
    this._renderMobileBasemaps();

    // Inicializar listeners de pestañas (Tiempo Real / Predicción)
    this._initTabs(savedPrefs.activeTab || 'realtime');

    // Renderizar listas de capas de Tiempo Real y Predicción
    this.renderLayerCards();
    this.updateMobileLayersBadge();

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

  /**
   * Inicializa los listeners de la barra inferior y drawers móviles
   */
  _initMobileNavigation() {
    if (this.btnMobileLayers) {
      this.btnMobileLayers.addEventListener('click', () => {
        if (this.layersDrawer && this.layersDrawer.classList.contains('mobile-open')) {
          this.closeMobileDrawers();
        } else {
          this.openMobileDrawer('layers');
        }
      });
    }

    if (this.btnMobileSettings) {
      this.btnMobileSettings.addEventListener('click', () => {
        if (this.settingsDrawer && this.settingsDrawer.classList.contains('mobile-open')) {
          this.closeMobileDrawers();
        } else {
          this.openMobileDrawer('settings');
        }
      });
    }

    if (this.btnCloseLayers) {
      this.btnCloseLayers.addEventListener('click', () => {
        this.closeMobileDrawers();
      });
    }

    if (this.btnCloseSettings) {
      this.btnCloseSettings.addEventListener('click', () => {
        this.closeMobileDrawers();
      });
    }

    if (this.drawerBackdrop) {
      this.drawerBackdrop.addEventListener('click', () => {
        this.closeMobileDrawers();
      });
    }

    window.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') {
        this.closeMobileDrawers();
      }
    });
  }

  /**
   * Abre un panel/drawer móvil específico
   * @param {string} drawerName 'layers' | 'settings'
   */
  openMobileDrawer(drawerName) {
    this.closeMobileDrawers();

    if (drawerName === 'layers') {
      if (this.layersDrawer) {
        this.layersDrawer.classList.add('mobile-open');
      }
      if (this.btnMobileLayers) {
        this.btnMobileLayers.classList.add('active');
        this.btnMobileLayers.setAttribute('aria-expanded', 'true');
      }
    } else if (drawerName === 'settings') {
      if (this.settingsDrawer) {
        this.settingsDrawer.classList.add('mobile-open');
      }
      if (this.btnMobileSettings) {
        this.btnMobileSettings.classList.add('active');
        this.btnMobileSettings.setAttribute('aria-expanded', 'true');
      }
    }

    if (this.drawerBackdrop) {
      this.drawerBackdrop.classList.add('active');
      this.drawerBackdrop.setAttribute('aria-hidden', 'false');
    }
  }

  /**
   * Cierra todos los drawers móviles y retira el backdrop
   */
  closeMobileDrawers() {
    if (this.layersDrawer) {
      this.layersDrawer.classList.remove('mobile-open');
    }
    if (this.settingsDrawer) {
      this.settingsDrawer.classList.remove('mobile-open');
    }
    if (this.btnMobileLayers) {
      this.btnMobileLayers.classList.remove('active');
      this.btnMobileLayers.setAttribute('aria-expanded', 'false');
    }
    if (this.btnMobileSettings) {
      this.btnMobileSettings.classList.remove('active');
      this.btnMobileSettings.setAttribute('aria-expanded', 'false');
    }
    if (this.drawerBackdrop) {
      this.drawerBackdrop.classList.remove('active');
      this.drawerBackdrop.setAttribute('aria-hidden', 'true');
    }
  }

  /**
   * Inicializa los controles del panel de ajustes móvil
   */
  _initMobileSettings() {
    const savedPrefs = StorageManager.load();
    const isCuencasVisible = (savedPrefs.cuencasVisible !== undefined) ? Boolean(savedPrefs.cuencasVisible) : true;
    const isCcaaVisible = (savedPrefs.ccaaVisible !== undefined) ? Boolean(savedPrefs.ccaaVisible) : true;
    const savedOpacityPct = Math.round((savedPrefs.fillOpacity || 0.25) * 100);

    // Toggle Cuencas Móvil
    if (this.toggleCuencasMobileEl) {
      this.toggleCuencasMobileEl.checked = isCuencasVisible;
      this.toggleCuencasMobileEl.addEventListener('change', (e) => {
        const visible = e.target.checked;
        if (this.toggleCuencasEl) this.toggleCuencasEl.checked = visible;
        this._updateCuencasUIState(visible);
        if (this.cuencasLayer) {
          this.cuencasLayer.setVisible(visible);
        }
      });
    }

    // Toggle CCAA Móvil
    if (this.toggleCcaaMobileEl) {
      this.toggleCcaaMobileEl.checked = isCcaaVisible;
      this.toggleCcaaMobileEl.addEventListener('change', (e) => {
        const visible = e.target.checked;
        if (this.toggleCcaaEl) this.toggleCcaaEl.checked = visible;
        if (this.mapManager) {
          this.mapManager.setCcaaVisible(visible);
        }
      });
    }

    // Slider Opacidad Móvil
    if (this.opacitySliderMobile && this.opacityValMobileEl) {
      this.opacitySliderMobile.value = savedOpacityPct;
      this.opacityValMobileEl.textContent = `${savedOpacityPct}%`;

      this.opacitySliderMobile.addEventListener('input', (e) => {
        const val = e.target.value;
        this.opacityValMobileEl.textContent = `${val}%`;
        if (this.opacityValEl) this.opacityValEl.textContent = `${val}%`;
        if (this.opacitySlider) this.opacitySlider.value = val;
        if (this.cuencasLayer) {
          this.cuencasLayer.setFillOpacity(val / 100);
        }
      });
    }

    // Botón Recentrar Móvil
    if (this.btnResetViewMobile) {
      this.btnResetViewMobile.addEventListener('click', () => {
        if (this.mapManager) {
          this.mapManager.resetView();
        }
      });
    }

    // Botón Cuenca Favorita Móvil
    if (this.btnFavoriteBasinMobile) {
      this.btnFavoriteBasinMobile.addEventListener('click', () => {
        const currentPrefs = StorageManager.load();
        if (currentPrefs.favoriteBasinId && this.cuencasLayer) {
          this.cuencasLayer.focusOnBasin(currentPrefs.favoriteBasinId, true);
        }
      });
    }
  }

  /**
   * Renderiza el selector interactivo de mapas base en el panel de ajustes móvil
   */
  _renderMobileBasemaps() {
    if (!this.mobileBasemapSelector) return;
    const currentBasemapId = (this.mapManager && this.mapManager.currentBasemapId) 
      || StorageManager.load().basemapId 
      || StorageManager.getDefaultBasemapId();

    const basemapSubtitles = {
      esriCanvas: 'Lienzo claro minimalista',
      esriDarkCanvas: 'Modo noche / contraste',
      ignBase: 'Topográfico oficial España',
      esriSatellite: 'Imágenes satélite aéreas',
      osm: 'OpenStreetMap estándar'
    };

    const basemaps = Object.values(CONFIG.basemaps);
    this.mobileBasemapSelector.innerHTML = basemaps.map(bm => {
      const isActive = bm.id === currentBasemapId;
      const sub = basemapSubtitles[bm.id] || 'Cartografía base';
      const cleanName = bm.name.split(' (')[0];
      return `
        <div class="basemap-card ${isActive ? 'active' : ''}" data-basemap-id="${bm.id}" role="button" tabindex="0">
          <div class="basemap-card-name">
            <span>${escapeHtml(cleanName)}</span>
            ${isActive ? `
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                <polyline points="20 6 9 17 4 12"></polyline>
              </svg>
            ` : ''}
          </div>
          <span class="basemap-card-sub">${escapeHtml(sub)}</span>
        </div>
      `;
    }).join('');

    this.mobileBasemapSelector.querySelectorAll('.basemap-card').forEach(card => {
      card.addEventListener('click', () => {
        const bmId = card.getAttribute('data-basemap-id');
        if (bmId && this.mapManager) {
          this.mapManager.switchBasemap(bmId);
          this.updateActiveBasemapUI(bmId);
        }
      });
    });
  }

  /**
   * Actualiza el resalte activo en el selector de mapa base móvil
   * @param {string} activeBasemapId 
   */
  updateActiveBasemapUI(activeBasemapId) {
    if (!this.mobileBasemapSelector) return;
    this.mobileBasemapSelector.querySelectorAll('.basemap-card').forEach(card => {
      const bmId = card.getAttribute('data-basemap-id');
      const isActive = bmId === activeBasemapId;
      card.classList.toggle('active', isActive);
      const nameEl = card.querySelector('.basemap-card-name');
      if (nameEl) {
        const bm = Object.values(CONFIG.basemaps).find(b => b.id === bmId);
        const nameText = bm ? bm.name.split(' (')[0] : '';
        nameEl.innerHTML = `
          <span>${escapeHtml(nameText)}</span>
          ${isActive ? `
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
              <polyline points="20 6 9 17 4 12"></polyline>
            </svg>
          ` : ''}
        `;
      }
    });
  }

  /**
   * Actualiza el badge indicador en el botón móvil de Capas
   */
  updateMobileLayersBadge() {
    if (!this.mobileLayersBadge) return;
    const prefs = StorageManager.load();
    const activeLayers = prefs.activeLayers || {};
    const count = Object.values(activeLayers).filter(Boolean).length;
    this.mobileLayersBadge.style.display = count > 0 ? 'block' : 'none';
  }

  setCuencasLayer(cuencasLayer) {
    this.cuencasLayer = cuencasLayer;
    this.renderFavoriteBadge();

    // Asegurar que el estado del toggle refleje cuencasLayer.isVisible
    if (this.cuencasLayer) {
      if (this.toggleCuencasEl) this.toggleCuencasEl.checked = this.cuencasLayer.isVisible;
      if (this.toggleCuencasMobileEl) this.toggleCuencasMobileEl.checked = this.cuencasLayer.isVisible;
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
    if (this.cuencasOpacityContainerMobile) {
      this.cuencasOpacityContainerMobile.classList.toggle('disabled', !visible);
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

    if (this.predictionBanner) {
      if (tabId === 'realtime') {
        this.predictionBanner.style.display = 'none';
      } else if (tabId === 'prediction') {
        const isEcmwfActive = Boolean(this.layerManager && this.layerManager.layerStates['ecmwf_ifs'] && this.layerManager.layerStates['ecmwf_ifs'].active);
        this.predictionBanner.style.display = isEcmwfActive ? 'flex' : 'none';
      }
    }

    if (this.layerManager && this.layerManager.onTabChange) {
      this.layerManager.onTabChange(tabId);
    }
  }

  /**
   * Actualiza el estado visual (switch, clase active y contenedor de controles) de una tarjeta de capa
   * @param {string} layerId 
   * @param {boolean} isActive 
   */
  updateLayerCardActiveState(layerId, isActive) {
    const card = document.querySelector(`.layer-card[data-layer-id="${layerId}"]`);
    const checkbox = document.querySelector(`.layer-toggle-input[data-layer-id="${layerId}"]`);
    const controls = document.getElementById(`controls-${layerId}`);

    if (card) {
      card.classList.toggle('active', isActive);
    }
    if (checkbox) {
      checkbox.checked = isActive;
    }
    if (controls) {
      controls.style.display = isActive ? 'block' : 'none';
    }

    if (layerId === 'ecmwf_ifs' && this.predictionBanner) {
      this.predictionBanner.style.display = isActive ? 'flex' : 'none';
    }

    this.updateMobileLayersBadge();
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
          <!-- Reproductor temporal interactivo del Radar (24h de histórico) -->
          <div class="radar-player-panel" id="radar-player-panel">
            <div class="radar-player-header">
              <span class="radar-time-badge" id="radar-time-badge">
                <span class="radar-live-dot"></span>
                <span id="radar-time-text">En Directo</span>
              </span>
              <button type="button" class="radar-live-btn active" id="radar-live-btn" title="Saltar al radar en directo">
                Directo
              </button>
            </div>

            <!-- Controles Play / Prev / Next -->
            <div class="radar-player-controls-row">
              <button type="button" class="radar-step-btn" id="radar-prev-btn" title="Paso anterior (-5 min)">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><path d="M6 6h2v12H6zm3.5 6 8.5 6V6z"/></svg>
              </button>
              <button type="button" class="radar-play-btn" id="radar-play-btn" title="Reproducir bucle de 24h">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" id="radar-play-icon"><polygon points="5 3 19 12 5 21 5 3"></polygon></svg>
                <span id="radar-play-text">Animar</span>
              </button>
              <button type="button" class="radar-step-btn" id="radar-next-btn" title="Paso siguiente (+5 min)">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><path d="m6 18 8.5-6L6 6v12zM16 6v12h2V6h-2z"/></svg>
              </button>
            </div>

            <!-- Slider de pasos temporales (-24h hasta directo) -->
            <div class="radar-slider-wrapper">
              <input type="range" class="slider-glass radar-step-slider" id="radar-step-slider" min="0" max="0" step="1" value="0">
              <div class="radar-slider-labels">
                <span>-24h</span>
                <span>-12h</span>
                <span>-6h</span>
                <span>-1h</span>
                <span>Ahora</span>
              </div>
            </div>
          </div>

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

    // Botón de sincronización para modelos numéricos en la píldora de estado
    let modelSyncBtnHtml = '';
    if (layer.id === 'ecmwf_ifs') {
      modelSyncBtnHtml = `
        <button type="button" class="btn-model-sync-pill" id="ecmwf-sync-btn" title="Comprobar si hay nueva corrida o pasos ahora">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.3" stroke-linecap="round" stroke-linejoin="round"><path d="M21.5 2v6h-6M21.34 15.57a10 10 0 1 1-.57-8.38l5.67-5.67"/></svg>
        </button>
      `;
    } else if (layer.id === 'gfs_0p25') {
      modelSyncBtnHtml = `
        <button type="button" class="btn-model-sync-pill" id="gfs-sync-btn" title="Comprobar si hay nueva corrida o pasos ahora">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.3" stroke-linecap="round" stroke-linejoin="round"><path d="M21.5 2v6h-6M21.34 15.57a10 10 0 1 1-.57-8.38l5.67-5.67"/></svg>
        </button>
      `;
    } else if (layer.id === 'arome_precip') {
      modelSyncBtnHtml = `
        <button type="button" class="btn-model-sync-pill" id="arome-sync-btn" title="Comprobar si hay nueva corrida o pasos ahora">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.3" stroke-linecap="round" stroke-linejoin="round"><path d="M21.5 2v6h-6M21.34 15.57a10 10 0 1 1-.57-8.38l5.67-5.67"/></svg>
        </button>
      `;
    }

    // Sub-controles específicos para el Modelo ECMWF IFS (Reproductor temporal y selector total/intervalo)
    let ecmwfExtraControls = '';
    if (layer.id === 'ecmwf_ifs') {
      ecmwfExtraControls = `
        <div class="ecmwf-subcontrols">
          <!-- Selector de Modo: Acumulado Total vs Intervalo 3h -->
          <div class="ecmwf-type-selector">
            <button type="button" class="ecmwf-type-btn active" data-type="total" id="ecmwf-btn-total">
              Acumulado Total
            </button>
            <button type="button" class="ecmwf-type-btn" data-type="interval" id="ecmwf-btn-interval">
              Intervalo (3h / 6h)
            </button>
          </div>

          <!-- Reproductor temporal interactivo -->
          <div class="ecmwf-player-panel">
            <div class="ecmwf-player-header">
              <span class="ecmwf-max-pill" id="ecmwf-max-pill" title="Ir al punto de precipitación máxima en el mapa">🎯 Máx: -- mm</span>
            </div>

            <!-- Controles Play / Prev / Next -->
            <div class="ecmwf-player-controls-row">
              <button type="button" class="ecmwf-step-btn" id="ecmwf-prev-btn" title="Paso anterior (-3h)">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><path d="M6 6h2v12H6zm3.5 6 8.5 6V6z"/></svg>
              </button>
              <button type="button" class="ecmwf-play-btn" id="ecmwf-play-btn" title="Reproducir animación">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" id="ecmwf-play-icon"><polygon points="5 3 19 12 5 21 5 3"></polygon></svg>
                <span id="ecmwf-play-text">Animar</span>
              </button>
              <button type="button" class="ecmwf-step-btn" id="ecmwf-next-btn" title="Paso siguiente (+3h)">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><path d="m6 18 8.5-6L6 6v12zM16 6v12h2V6h-2z"/></svg>
              </button>
            </div>

            <!-- Slider de pasos temporales hasta +240h (10 días) -->
            <div class="ecmwf-slider-wrapper">
              <input type="range" class="slider-glass ecmwf-step-slider" id="ecmwf-step-slider" min="3" max="240" step="3" value="3">
              <div class="ecmwf-slider-labels">
                <span>+3h</span>
                <span>+72h (3d)</span>
                <span>+144h (6d)</span>
                <span>+240h (10d)</span>
              </div>
            </div>
          </div>

          <!-- Leyenda de Precipitación -->
          <div class="ecmwf-precip-legend">
            <span class="precip-legend-title">Precipitación (mm):</span>
            <div class="precip-bar">
              <span style="background: #bae6fd; color: #0284c7;" title="0.1 - 1 mm">0.1</span>
              <span style="background: #38bdf8; color: #0369a1;" title="1 - 3 mm">1</span>
              <span style="background: #0284c7; color: #ffffff;" title="3 - 10 mm">3</span>
              <span style="background: #4ade80; color: #14532d;" title="10 - 20 mm">10</span>
              <span style="background: #16a34a; color: #ffffff;" title="20 - 40 mm">20</span>
              <span style="background: #facc15; color: #713f12;" title="40 - 70 mm">40</span>
              <span style="background: #f97316; color: #ffffff;" title="70 - 100 mm">70</span>
              <span style="background: #ef4444; color: #ffffff;" title="100 - 150 mm">100</span>
              <span style="background: #d946ef; color: #ffffff;" title="150 - 250 mm">150</span>
              <span style="background: #ffffff; color: #6b21a8;" title="> 250 mm">>250</span>
            </div>
          </div>
        </div>
      `;
    }

    // Sub-controles específicos para el Modelo NOAA GFS (Reproductor temporal hasta +384h y selector total/intervalo)
    let gfsExtraControls = '';
    if (layer.id === 'gfs_0p25') {
      gfsExtraControls = `
        <div class="ecmwf-subcontrols">
          <!-- Selector de Modo: Acumulado Total vs Intervalo 3h -->
          <div class="ecmwf-type-selector">
            <button type="button" class="ecmwf-type-btn active" data-type="total" id="gfs-btn-total">
              Acumulado Total
            </button>
            <button type="button" class="ecmwf-type-btn" data-type="interval" id="gfs-btn-interval">
              Intervalo (3h / 6h)
            </button>
          </div>

          <!-- Reproductor temporal interactivo -->
          <div class="ecmwf-player-panel">
            <div class="ecmwf-player-header">
              <span class="ecmwf-max-pill" id="gfs-max-pill" title="Ir al punto de precipitación máxima en el mapa">🎯 Máx: -- mm</span>
            </div>

            <!-- Controles Play / Prev / Next -->
            <div class="ecmwf-player-controls-row">
              <button type="button" class="ecmwf-step-btn" id="gfs-prev-btn" title="Paso anterior (-3h)">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><path d="M6 6h2v12H6zm3.5 6 8.5 6V6z"/></svg>
              </button>
              <button type="button" class="ecmwf-play-btn" id="gfs-play-btn" title="Reproducir animación">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" id="gfs-play-icon"><polygon points="5 3 19 12 5 21 5 3"></polygon></svg>
                <span id="gfs-play-text">Animar</span>
              </button>
              <button type="button" class="ecmwf-step-btn" id="gfs-next-btn" title="Paso siguiente (+3h)">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><path d="m6 18 8.5-6L6 6v12zM16 6v12h2V6h-2z"/></svg>
              </button>
            </div>

            <!-- Slider de pasos temporales hasta +384h (16 días) -->
            <div class="ecmwf-slider-wrapper">
              <input type="range" class="slider-glass ecmwf-step-slider" id="gfs-step-slider" min="3" max="384" step="3" value="3">
              <div class="ecmwf-slider-labels">
                <span>+3h</span>
                <span>+120h (5d)</span>
                <span>+240h (10d)</span>
                <span>+384h (16d)</span>
              </div>
            </div>
          </div>

          <!-- Leyenda de Precipitación -->
          <div class="ecmwf-precip-legend">
            <span class="precip-legend-title">Precipitación (mm):</span>
            <div class="precip-bar">
              <span style="background: #bae6fd; color: #0284c7;" title="0.1 - 1 mm">0.1</span>
              <span style="background: #38bdf8; color: #0369a1;" title="1 - 3 mm">1</span>
              <span style="background: #0284c7; color: #ffffff;" title="3 - 10 mm">3</span>
              <span style="background: #4ade80; color: #14532d;" title="10 - 20 mm">10</span>
              <span style="background: #16a34a; color: #ffffff;" title="20 - 40 mm">20</span>
              <span style="background: #facc15; color: #713f12;" title="40 - 70 mm">40</span>
              <span style="background: #f97316; color: #ffffff;" title="70 - 100 mm">70</span>
              <span style="background: #ef4444; color: #ffffff;" title="100 - 150 mm">100</span>
              <span style="background: #d946ef; color: #ffffff;" title="150 - 250 mm">150</span>
              <span style="background: #ffffff; color: #6b21a8;" title="> 250 mm">>250</span>
            </div>
          </div>
        </div>
      `;
    }

    // Sub-controles específicos para el Modelo Météo-France / AEMET AROME (Reproductor temporal hasta +48h y selector total/intervalo)
    let aromeExtraControls = '';
    if (layer.id === 'arome_precip') {
      aromeExtraControls = `
        <div class="ecmwf-subcontrols">
          <!-- Selector de Modo: Acumulado Total vs Intervalo 1h -->
          <div class="ecmwf-type-selector">
            <button type="button" class="ecmwf-type-btn active" data-type="total" id="arome-btn-total">
              Acumulado Total
            </button>
            <button type="button" class="ecmwf-type-btn" data-type="interval" id="arome-btn-interval">
              Intervalo (1h)
            </button>
          </div>

          <!-- Reproductor temporal interactivo -->
          <div class="ecmwf-player-panel">
            <div class="ecmwf-player-header">
              <span class="ecmwf-max-pill" id="arome-max-pill" title="Ir al punto de precipitación máxima en el mapa">🎯 Máx: -- mm</span>
            </div>

            <!-- Controles Play / Prev / Next -->
            <div class="ecmwf-player-controls-row">
              <button type="button" class="ecmwf-step-btn" id="arome-prev-btn" title="Paso anterior (-1h)">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><path d="M6 6h2v12H6zm3.5 6 8.5 6V6z"/></svg>
              </button>
              <button type="button" class="ecmwf-play-btn" id="arome-play-btn" title="Reproducir animación">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" id="arome-play-icon"><polygon points="5 3 19 12 5 21 5 3"></polygon></svg>
                <span id="arome-play-text">Animar</span>
              </button>
              <button type="button" class="ecmwf-step-btn" id="arome-next-btn" title="Paso siguiente (+1h)">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><path d="m6 18 8.5-6L6 6v12zM16 6v12h2V6h-2z"/></svg>
              </button>
            </div>

            <!-- Slider de pasos temporales hasta +48h (2 días) -->
            <div class="ecmwf-slider-wrapper">
              <input type="range" class="slider-glass ecmwf-step-slider" id="arome-step-slider" min="1" max="48" step="1" value="1">
              <div class="ecmwf-slider-labels">
                <span>+1h</span>
                <span>+12h</span>
                <span>+24h (1d)</span>
                <span>+36h</span>
                <span>+48h (2d)</span>
              </div>
            </div>
          </div>

          <!-- Leyenda de Precipitación -->
          <div class="ecmwf-precip-legend">
            <span class="precip-legend-title">Precipitación (mm):</span>
            <div class="precip-bar">
              <span style="background: #bae6fd; color: #0284c7;" title="0.1 - 1 mm">0.1</span>
              <span style="background: #38bdf8; color: #0369a1;" title="1 - 3 mm">1</span>
              <span style="background: #0284c7; color: #ffffff;" title="3 - 10 mm">3</span>
              <span style="background: #4ade80; color: #14532d;" title="10 - 20 mm">10</span>
              <span style="background: #16a34a; color: #ffffff;" title="20 - 40 mm">20</span>
              <span style="background: #facc15; color: #713f12;" title="40 - 70 mm">40</span>
              <span style="background: #f97316; color: #ffffff;" title="70 - 100 mm">70</span>
              <span style="background: #ef4444; color: #ffffff;" title="100 - 150 mm">100</span>
              <span style="background: #d946ef; color: #ffffff;" title="150 - 250 mm">150</span>
              <span style="background: #ffffff; color: #6b21a8;" title="> 250 mm">>250</span>
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
              ${modelSyncBtnHtml}
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
          ${ecmwfExtraControls}
          ${gfsExtraControls}
          ${aromeExtraControls}
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


    // Controles de Reproductor Temporal de Radar (24h de histórico)
    const radarPlayBtn = document.getElementById('radar-play-btn');
    if (radarPlayBtn) {
      radarPlayBtn.addEventListener('click', () => {
        if (this.layerManager) {
          this.layerManager.toggleRadarPlayback();
        }
      });
    }

    const radarLiveBtn = document.getElementById('radar-live-btn');
    if (radarLiveBtn) {
      radarLiveBtn.addEventListener('click', () => {
        if (this.layerManager) {
          this.layerManager.pauseRadarPlayback();
          this.layerManager.setRadarLive();
        }
      });
    }

    const radarPrevBtn = document.getElementById('radar-prev-btn');
    if (radarPrevBtn) {
      radarPrevBtn.addEventListener('click', () => {
        if (!this.layerManager || !this.layerManager.radarTimeline) return;
        this.layerManager.pauseRadarPlayback();
        const timeline = this.layerManager.radarTimeline;
        if (timeline.length === 0) return;
        const curIdx = timeline.findIndex(t => t.timestep === this.layerManager.currentRadarTimestep);
        const prevIdx = curIdx <= 0 ? 0 : curIdx - 1;
        this.layerManager.setRadarTimestep(timeline[prevIdx].timestep);
      });
    }

    const radarNextBtn = document.getElementById('radar-next-btn');
    if (radarNextBtn) {
      radarNextBtn.addEventListener('click', () => {
        if (!this.layerManager || !this.layerManager.radarTimeline) return;
        this.layerManager.pauseRadarPlayback();
        const timeline = this.layerManager.radarTimeline;
        if (timeline.length === 0) return;
        const curIdx = timeline.findIndex(t => t.timestep === this.layerManager.currentRadarTimestep);
        const nextIdx = (curIdx === -1 || curIdx >= timeline.length - 1) ? timeline.length - 1 : curIdx + 1;
        this.layerManager.setRadarTimestep(timeline[nextIdx].timestep);
      });
    }

    const radarSlider = document.getElementById('radar-step-slider');
    if (radarSlider) {
      const handleRadarSlider = (idx) => {
        if (!this.layerManager || !this.layerManager.radarTimeline) return;
        this.layerManager.pauseRadarPlayback();
        const timeline = this.layerManager.radarTimeline;
        if (timeline.length === 0) return;
        const clampedIdx = Math.max(0, Math.min(idx, timeline.length - 1));
        this.layerManager.setRadarTimestep(timeline[clampedIdx].timestep);
      };

      radarSlider.addEventListener('input', (e) => {
        handleRadarSlider(parseInt(e.target.value, 10));
      });

      radarSlider.addEventListener('wheel', (e) => {
        e.preventDefault();
        if (!this.layerManager || !this.layerManager.radarTimeline) return;
        const timeline = this.layerManager.radarTimeline;
        if (timeline.length === 0) return;
        const curIdx = timeline.findIndex(t => t.timestep === this.layerManager.currentRadarTimestep);
        const delta = e.deltaY > 0 ? -1 : 1;
        const newIdx = Math.max(0, Math.min(curIdx + delta, timeline.length - 1));
        this.layerManager.pauseRadarPlayback();
        this.layerManager.setRadarTimestep(timeline[newIdx].timestep);
      }, { passive: false });
    }

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

    // Controles Modelo ECMWF IFS: Selector de Modo (Total vs Intervalo)
    const ecmwfBtnTotal = document.getElementById('ecmwf-btn-total');
    const ecmwfBtnInterval = document.getElementById('ecmwf-btn-interval');
    if (ecmwfBtnTotal && ecmwfBtnInterval) {
      ecmwfBtnTotal.addEventListener('click', () => {
        ecmwfBtnTotal.classList.add('active');
        ecmwfBtnInterval.classList.remove('active');
        if (this.layerManager) {
          this.layerManager.setEcmwfType('total');
        }
      });
      ecmwfBtnInterval.addEventListener('click', () => {
        ecmwfBtnInterval.classList.add('active');
        ecmwfBtnTotal.classList.remove('active');
        if (this.layerManager) {
          this.layerManager.setEcmwfType('interval');
        }
      });
    }

    // Controles ECMWF: Botón Play / Pause
    const ecmwfPlayBtn = document.getElementById('ecmwf-play-btn');
    if (ecmwfPlayBtn) {
      ecmwfPlayBtn.addEventListener('click', () => {
        if (this.layerManager) {
          this.layerManager.toggleEcmwfPlayback();
        }
      });
    }

    // Controles ECMWF: Paso Anterior (-3h)
    const ecmwfPrevBtn = document.getElementById('ecmwf-prev-btn');
    if (ecmwfPrevBtn) {
      ecmwfPrevBtn.addEventListener('click', () => {
        if (!this.layerManager || !this.layerManager.ecmwfMetadata) return;
        const steps = this.layerManager.ecmwfMetadata.available_steps || [];
        if (steps.length === 0) return;
        const curStep = this.layerManager.currentEcmwfStep || steps[0];
        const curIdx = steps.indexOf(curStep);
        const prevIdx = (curIdx - 1 + steps.length) % steps.length;
        this.layerManager.setEcmwfStep(steps[prevIdx]);
      });
    }

    // Controles ECMWF: Paso Siguiente (+3h)
    const ecmwfNextBtn = document.getElementById('ecmwf-next-btn');
    if (ecmwfNextBtn) {
      ecmwfNextBtn.addEventListener('click', () => {
        if (!this.layerManager || !this.layerManager.ecmwfMetadata) return;
        const steps = this.layerManager.ecmwfMetadata.available_steps || [];
        if (steps.length === 0) return;
        const curStep = this.layerManager.currentEcmwfStep || steps[0];
        const curIdx = steps.indexOf(curStep);
        const nextIdx = (curIdx + 1) % steps.length;
        this.layerManager.setEcmwfStep(steps[nextIdx]);
      });
    }

    // Controles ECMWF: Slider de pasos
    const ecmwfSlider = document.getElementById('ecmwf-step-slider');
    if (ecmwfSlider) {
      const handleSliderInput = (rawVal) => {
        if (!this.layerManager) return;
        const steps = (this.layerManager.ecmwfMetadata && this.layerManager.ecmwfMetadata.available_steps) || [];
        if (steps.length === 0) return;

        // Encontrar el paso disponible más cercano al valor arrastrado
        let closestStep = steps[0];
        let minDiff = Infinity;
        for (const s of steps) {
          const diff = Math.abs(s - rawVal);
          if (diff < minDiff) {
            minDiff = diff;
            closestStep = s;
          }
        }
        this.layerManager.setEcmwfStep(closestStep);
      };

      ecmwfSlider.addEventListener('input', (e) => {
        handleSliderInput(parseInt(e.target.value, 10));
      });

      // Soporte de scroll / rueda de ratón en el slider para navegación fluida
      ecmwfSlider.addEventListener('wheel', (e) => {
        e.preventDefault();
        if (!this.layerManager || !this.layerManager.ecmwfMetadata) return;
        const steps = this.layerManager.ecmwfMetadata.available_steps || [];
        if (steps.length === 0) return;
        const curStep = this.layerManager.currentEcmwfStep || steps[0];
        const curIdx = steps.indexOf(curStep);
        if (e.deltaY > 0) {
          const nextIdx = Math.min(steps.length - 1, curIdx + 1);
          this.layerManager.setEcmwfStep(steps[nextIdx]);
        } else if (e.deltaY < 0) {
          const prevIdx = Math.max(0, curIdx - 1);
          this.layerManager.setEcmwfStep(steps[prevIdx]);
        }
      }, { passive: false });
    }

    // Clic en píldora de máximo para volar directamente al punto geográfico en el mapa
    const ecmwfMaxPill = document.getElementById('ecmwf-max-pill');
    if (ecmwfMaxPill) {
      ecmwfMaxPill.addEventListener('click', () => {
        if (this.layerManager) this.layerManager.flyToModelMax('ecmwf');
      });
    }

    // =========================================================================
    // Controles Interactivos para NOAA GFS
    // =========================================================================
    const gfsBtnTotal = document.getElementById('gfs-btn-total');
    const gfsBtnInterval = document.getElementById('gfs-btn-interval');

    if (gfsBtnTotal && gfsBtnInterval) {
      gfsBtnTotal.addEventListener('click', () => {
        gfsBtnTotal.classList.add('active');
        gfsBtnInterval.classList.remove('active');
        if (this.layerManager) {
          this.layerManager.setGfsType('total');
        }
      });
      gfsBtnInterval.addEventListener('click', () => {
        gfsBtnInterval.classList.add('active');
        gfsBtnTotal.classList.remove('active');
        if (this.layerManager) {
          this.layerManager.setGfsType('interval');
        }
      });
    }

    // Controles GFS: Botón Play / Pause
    const gfsPlayBtn = document.getElementById('gfs-play-btn');
    if (gfsPlayBtn) {
      gfsPlayBtn.addEventListener('click', () => {
        if (this.layerManager) {
          this.layerManager.toggleGfsPlayback();
        }
      });
    }

    // Controles GFS: Paso Anterior (-3h)
    const gfsPrevBtn = document.getElementById('gfs-prev-btn');
    if (gfsPrevBtn) {
      gfsPrevBtn.addEventListener('click', () => {
        if (!this.layerManager || !this.layerManager.gfsMetadata) return;
        const steps = this.layerManager.gfsMetadata.available_steps || [];
        if (steps.length === 0) return;
        const curStep = this.layerManager.currentGfsStep || steps[0];
        const curIdx = steps.indexOf(curStep);
        const prevIdx = (curIdx - 1 + steps.length) % steps.length;
        this.layerManager.setGfsStep(steps[prevIdx]);
      });
    }

    // Controles GFS: Paso Siguiente (+3h)
    const gfsNextBtn = document.getElementById('gfs-next-btn');
    if (gfsNextBtn) {
      gfsNextBtn.addEventListener('click', () => {
        if (!this.layerManager || !this.layerManager.gfsMetadata) return;
        const steps = this.layerManager.gfsMetadata.available_steps || [];
        if (steps.length === 0) return;
        const curStep = this.layerManager.currentGfsStep || steps[0];
        const curIdx = steps.indexOf(curStep);
        const nextIdx = (curIdx + 1) % steps.length;
        this.layerManager.setGfsStep(steps[nextIdx]);
      });
    }

    // Controles GFS: Slider de pasos
    const gfsSlider = document.getElementById('gfs-step-slider');
    if (gfsSlider) {
      const handleGfsSliderInput = (rawVal) => {
        if (!this.layerManager) return;
        const steps = (this.layerManager.gfsMetadata && this.layerManager.gfsMetadata.available_steps) || [];
        if (steps.length === 0) return;

        let closestStep = steps[0];
        let minDiff = Infinity;
        for (const s of steps) {
          const diff = Math.abs(s - rawVal);
          if (diff < minDiff) {
            minDiff = diff;
            closestStep = s;
          }
        }
        this.layerManager.setGfsStep(closestStep);
      };

      gfsSlider.addEventListener('input', (e) => {
        handleGfsSliderInput(parseInt(e.target.value, 10));
      });

      // Soporte de scroll / rueda de ratón en el slider para navegación fluida
      gfsSlider.addEventListener('wheel', (e) => {
        e.preventDefault();
        if (!this.layerManager || !this.layerManager.gfsMetadata) return;
        const steps = this.layerManager.gfsMetadata.available_steps || [];
        if (steps.length === 0) return;
        const curStep = this.layerManager.currentGfsStep || steps[0];
        const curIdx = steps.indexOf(curStep);
        if (e.deltaY > 0) {
          const nextIdx = Math.min(steps.length - 1, curIdx + 1);
          this.layerManager.setGfsStep(steps[nextIdx]);
        } else if (e.deltaY < 0) {
          const prevIdx = Math.max(0, curIdx - 1);
          this.layerManager.setGfsStep(steps[prevIdx]);
        }
      }, { passive: false });
    }

    // Clic en píldora de máximo GFS para volar al punto
    const gfsMaxPill = document.getElementById('gfs-max-pill');
    if (gfsMaxPill) {
      gfsMaxPill.addEventListener('click', () => {
        if (this.layerManager) this.layerManager.flyToModelMax('gfs');
      });
    }

    // =========================================================================
    // Controles Interactivos para Météo-France / AEMET AROME
    // =========================================================================
    const aromeBtnTotal = document.getElementById('arome-btn-total');
    const aromeBtnInterval = document.getElementById('arome-btn-interval');

    if (aromeBtnTotal && aromeBtnInterval) {
      aromeBtnTotal.addEventListener('click', () => {
        aromeBtnTotal.classList.add('active');
        aromeBtnInterval.classList.remove('active');
        if (this.layerManager) {
          this.layerManager.setAromeType('total');
        }
      });
      aromeBtnInterval.addEventListener('click', () => {
        aromeBtnInterval.classList.add('active');
        aromeBtnTotal.classList.remove('active');
        if (this.layerManager) {
          this.layerManager.setAromeType('interval');
        }
      });
    }

    // Controles AROME: Botón Play / Pause
    const aromePlayBtn = document.getElementById('arome-play-btn');
    if (aromePlayBtn) {
      aromePlayBtn.addEventListener('click', () => {
        if (this.layerManager) {
          this.layerManager.toggleAromePlayback();
        }
      });
    }

    // Controles AROME: Paso Anterior (-1h)
    const aromePrevBtn = document.getElementById('arome-prev-btn');
    if (aromePrevBtn) {
      aromePrevBtn.addEventListener('click', () => {
        if (!this.layerManager || !this.layerManager.aromeMetadata) return;
        const steps = this.layerManager.aromeMetadata.available_steps || [];
        if (steps.length === 0) return;
        const curStep = this.layerManager.currentAromeStep || steps[0];
        const curIdx = steps.indexOf(curStep);
        const prevIdx = (curIdx - 1 + steps.length) % steps.length;
        this.layerManager.setAromeStep(steps[prevIdx]);
      });
    }

    // Controles AROME: Paso Siguiente (+1h)
    const aromeNextBtn = document.getElementById('arome-next-btn');
    if (aromeNextBtn) {
      aromeNextBtn.addEventListener('click', () => {
        if (!this.layerManager || !this.layerManager.aromeMetadata) return;
        const steps = this.layerManager.aromeMetadata.available_steps || [];
        if (steps.length === 0) return;
        const curStep = this.layerManager.currentAromeStep || steps[0];
        const curIdx = steps.indexOf(curStep);
        const nextIdx = (curIdx + 1) % steps.length;
        this.layerManager.setAromeStep(steps[nextIdx]);
      });
    }

    // Controles AROME: Slider de pasos
    const aromeSlider = document.getElementById('arome-step-slider');
    if (aromeSlider) {
      const handleAromeSliderInput = (rawVal) => {
        if (!this.layerManager) return;
        const steps = (this.layerManager.aromeMetadata && this.layerManager.aromeMetadata.available_steps) || [];
        if (steps.length === 0) return;

        let closestStep = steps[0];
        let minDiff = Infinity;
        for (const s of steps) {
          const diff = Math.abs(s - rawVal);
          if (diff < minDiff) {
            minDiff = diff;
            closestStep = s;
          }
        }
        this.layerManager.setAromeStep(closestStep);
      };

      aromeSlider.addEventListener('input', (e) => {
        handleAromeSliderInput(parseInt(e.target.value, 10));
      });

      // Soporte de scroll / rueda de ratón en el slider para navegación fluida
      aromeSlider.addEventListener('wheel', (e) => {
        e.preventDefault();
        if (!this.layerManager || !this.layerManager.aromeMetadata) return;
        const steps = this.layerManager.aromeMetadata.available_steps || [];
        if (steps.length === 0) return;
        const curStep = this.layerManager.currentAromeStep || steps[0];
        const curIdx = steps.indexOf(curStep);
        if (e.deltaY > 0) {
          const nextIdx = Math.min(steps.length - 1, curIdx + 1);
          this.layerManager.setAromeStep(steps[nextIdx]);
        } else if (e.deltaY < 0) {
          const prevIdx = Math.max(0, curIdx - 1);
          this.layerManager.setAromeStep(steps[prevIdx]);
        }
      }, { passive: false });
    }

    // Clic en píldora de máximo AROME para volar al punto
    const aromeMaxPill = document.getElementById('arome-max-pill');
    if (aromeMaxPill) {
      aromeMaxPill.addEventListener('click', () => {
        if (this.layerManager) this.layerManager.flyToModelMax('arome');
      });
    }

    // Botones de Sincronización Manual Inmediata
    this._setupModelSyncButton('ecmwf-sync-btn', 'ecmwf');
    this._setupModelSyncButton('gfs-sync-btn', 'gfs');
    this._setupModelSyncButton('arome-sync-btn', 'arome');
    this._setupPredictionBannerSync();
  }

  /**
   * Configura el comportamiento interactivo de un botón de sincronización de modelo
   */
  _setupModelSyncButton(btnId, modelKey) {
    const btn = document.getElementById(btnId);
    if (!btn) return;

    btn.addEventListener('click', async (e) => {
      e.stopPropagation();
      if (btn.classList.contains('syncing')) return;

      btn.classList.add('syncing');
      btn.setAttribute('title', 'Comprobando si hay nueva corrida o pasos...');

      try {
        if (this.layerManager) {
          await this.layerManager.triggerModelSync(modelKey);
        }
        btn.setAttribute('title', '✓ Actualizado con éxito');
      } catch (err) {
        console.warn(`Error al sincronizar ${modelKey}:`, err);
        btn.setAttribute('title', 'Error al sincronizar');
      } finally {
        setTimeout(() => {
          btn.classList.remove('syncing');
          btn.setAttribute('title', 'Comprobar si hay nueva corrida o pasos ahora');
        }, 1200);
      }
    });
  }

  /**
   * Configura el botón de sincronización rápida ubicado en el banner flotante de predicción
   */
  _setupPredictionBannerSync() {
    const bannerSyncBtn = document.getElementById('btn-prediction-banner-sync');
    if (!bannerSyncBtn || bannerSyncBtn._hasSyncListener) return;
    bannerSyncBtn._hasSyncListener = true;

    bannerSyncBtn.addEventListener('click', async (e) => {
      e.stopPropagation();
      if (bannerSyncBtn.classList.contains('syncing')) return;

      bannerSyncBtn.classList.add('syncing');
      try {
        if (this.layerManager) {
          const syncTasks = [];
          if (this.layerManager.isLayerActive('ecmwf_ifs')) {
            syncTasks.push(this.layerManager.triggerModelSync('ecmwf'));
          }
          if (this.layerManager.isLayerActive('gfs_0p25')) {
            syncTasks.push(this.layerManager.triggerModelSync('gfs'));
          }
          if (this.layerManager.isLayerActive('arome_precip')) {
            syncTasks.push(this.layerManager.triggerModelSync('arome'));
          }

          if (syncTasks.length === 0) {
            // Si ninguno está activo visible, sincroniza los tres
            await Promise.all([
              this.layerManager.triggerModelSync('ecmwf'),
              this.layerManager.triggerModelSync('gfs'),
              this.layerManager.triggerModelSync('arome')
            ]);
          } else {
            await Promise.all(syncTasks);
          }
        }
      } catch (err) {
        console.warn('Error al sincronizar desde el banner:', err);
      } finally {
        setTimeout(() => {
          bannerSyncBtn.classList.remove('syncing');
        }, 1200);
      }
    });
  }

  /**
   * Actualiza el reproductor interactivo de Radar 24h en la UI
   */
  updateRadarPlayerUI(timeline, currentTimestep, isPlaying, isLive) {
    const slider = document.getElementById('radar-step-slider');
    const timeText = document.getElementById('radar-time-text');
    const liveDot = document.querySelector('#radar-time-badge .radar-live-dot');
    const liveBtn = document.getElementById('radar-live-btn');

    if (slider && Array.isArray(timeline) && timeline.length > 0) {
      slider.min = 0;
      slider.max = timeline.length - 1;
      const curIdx = timeline.findIndex(t => t.timestep === currentTimestep);
      slider.value = curIdx >= 0 ? curIdx : timeline.length - 1;
      slider.disabled = false;
    }

    const currentEntry = Array.isArray(timeline) ? timeline.find(t => t.timestep === currentTimestep) : null;

    if (timeText) {
      if (isLive) {
        timeText.textContent = currentEntry ? `${currentEntry.valid_time_local} (Directo)` : 'En Directo';
      } else if (currentEntry) {
        timeText.textContent = `${currentEntry.valid_time_local} (-${currentEntry.age_text || ''})`;
      } else {
        timeText.textContent = currentTimestep || '--';
      }
    }

    if (liveDot) {
      liveDot.style.display = isLive ? 'inline-block' : 'none';
    }

    if (liveBtn) {
      if (isLive) {
        liveBtn.classList.add('active');
      } else {
        liveBtn.classList.remove('active');
      }
    }

    this.updateRadarPlayState(isPlaying);
  }

  /**
   * Actualiza el estado visual del botón play/pause del radar
   */
  updateRadarPlayState(isPlaying) {
    const playBtn = document.getElementById('radar-play-btn');
    const playIcon = document.getElementById('radar-play-icon');
    const playText = document.getElementById('radar-play-text');

    if (!playBtn) return;

    if (isPlaying) {
      playBtn.classList.add('playing');
      if (playIcon) {
        playIcon.innerHTML = '<rect x="6" y="4" width="4" height="16"></rect><rect x="14" y="4" width="4" height="16"></rect>';
      }
      if (playText) {
        playText.textContent = 'Pausar';
      }
    } else {
      playBtn.classList.remove('playing');
      if (playIcon) {
        playIcon.innerHTML = '<polygon points="5 3 19 12 5 21 5 3"></polygon>';
      }
      if (playText) {
        playText.textContent = 'Animar';
      }
    }
  }

  /**
   * Actualiza el reproductor interactivo de ECMWF IFS en la UI
   */
  updateEcmwfPlayerUI(metadata, currentStep, currentType, isPlaying) {
    const slider = document.getElementById('ecmwf-step-slider');
    const badge = document.getElementById('ecmwf-current-step-badge');
    const maxPill = document.getElementById('ecmwf-max-pill');
    const btnTotal = document.getElementById('ecmwf-btn-total');
    const btnInterval = document.getElementById('ecmwf-btn-interval');

    if (btnTotal && btnInterval) {
      if (currentType === 'interval') {
        btnInterval.classList.add('active');
        btnTotal.classList.remove('active');
      } else {
        btnTotal.classList.add('active');
        btnInterval.classList.remove('active');
      }
    }

    const availSteps = metadata.available_steps || [];
    if (slider && availSteps.length > 0) {
      slider.min = availSteps[0];
      slider.max = availSteps[availSteps.length - 1];
      slider.value = currentStep;
    }

    const stepInfo = (metadata.steps || []).find(s => s.step === currentStep);

    if (maxPill && stepInfo) {
      const maxVal = currentType === 'interval' ? stepInfo.max_interval_mm : stepInfo.max_total_mm;
      maxPill.innerHTML = `🎯 Máx: <strong>${maxVal !== undefined ? maxVal : '--'} mm</strong>`;
      maxPill.title = 'Ir al punto de precipitación máxima en el mapa';
    }

    // Actualizar el Banner Superior con el instante exacto en hora local (Europe/Madrid)
    if (this.predictionBannerExactTime && stepInfo && stepInfo.valid_time_iso) {
      const formattedInstant = formatPredictionInstant(stepInfo.valid_time_iso);
      this.predictionBannerExactTime.textContent = formattedInstant;
    }
    if (this.predictionBannerModel) {
      this.predictionBannerModel.textContent = `ECMWF IFS (+${currentStep}h)`;
    }
    if (this.predictionBannerMode) {
      const modeText = currentType === 'total' ? 'Acumulado Total' : (currentStep > 144 ? 'Intervalo 6h' : 'Intervalo 3h');
      this.predictionBannerMode.textContent = modeText;
    }

    // Visibilidad del banner: visible si alguna capa de predicción está activa
    if (this.predictionBanner) {
      const isPredActive = Boolean(this.layerManager && (this.layerManager.isLayerOnMap('ecmwf_ifs') || this.layerManager.isLayerOnMap('gfs_0p25') || this.layerManager.isLayerOnMap('arome_precip')));
      this.predictionBanner.style.display = isPredActive ? 'flex' : 'none';
    }

    this.updateEcmwfPlayState(isPlaying);
  }

  /**
   * Actualiza el reproductor interactivo de NOAA GFS en la UI
   */
  updateGfsPlayerUI(metadata, currentStep, currentType, isPlaying) {
    const slider = document.getElementById('gfs-step-slider');
    const maxPill = document.getElementById('gfs-max-pill');
    const btnTotal = document.getElementById('gfs-btn-total');
    const btnInterval = document.getElementById('gfs-btn-interval');

    if (btnTotal && btnInterval) {
      if (currentType === 'interval') {
        btnInterval.classList.add('active');
        btnTotal.classList.remove('active');
      } else {
        btnTotal.classList.add('active');
        btnInterval.classList.remove('active');
      }
    }

    const availSteps = metadata.available_steps || [];
    if (slider && availSteps.length > 0) {
      slider.min = availSteps[0];
      slider.max = availSteps[availSteps.length - 1];
      slider.value = currentStep;
    }

    const stepInfo = (metadata.steps || []).find(s => s.step === currentStep);

    if (maxPill && stepInfo) {
      const maxVal = currentType === 'interval' ? stepInfo.max_interval_mm : stepInfo.max_total_mm;
      maxPill.innerHTML = `🎯 Máx: <strong>${maxVal !== undefined ? maxVal : '--'} mm</strong>`;
      maxPill.title = 'Ir al punto de precipitación máxima en el mapa';
    }

    // Actualizar el Banner Superior con el instante exacto en hora local (Europe/Madrid)
    if (this.predictionBannerExactTime && stepInfo && stepInfo.valid_time_iso) {
      const formattedInstant = formatPredictionInstant(stepInfo.valid_time_iso);
      this.predictionBannerExactTime.textContent = formattedInstant;
    }
    if (this.predictionBannerModel) {
      this.predictionBannerModel.textContent = `NOAA GFS (+${currentStep}h)`;
    }
    if (this.predictionBannerMode) {
      const modeText = currentType === 'total' ? 'Acumulado Total' : (currentStep > 120 ? 'Intervalo 6h' : 'Intervalo 3h');
      this.predictionBannerMode.textContent = modeText;
    }

    // Visibilidad del banner: visible si alguna capa de predicción está activa
    if (this.predictionBanner) {
      const isPredActive = Boolean(this.layerManager && (this.layerManager.isLayerOnMap('ecmwf_ifs') || this.layerManager.isLayerOnMap('gfs_0p25') || this.layerManager.isLayerOnMap('arome_precip')));
      this.predictionBanner.style.display = isPredActive ? 'flex' : 'none';
    }

    this.updateGfsPlayState(isPlaying);
  }

  /**
   * Actualiza el reproductor interactivo de Météo-France / AEMET AROME en la UI
   */
  updateAromePlayerUI(metadata, currentStep, currentType, isPlaying) {
    const slider = document.getElementById('arome-step-slider');
    const maxPill = document.getElementById('arome-max-pill');
    const btnTotal = document.getElementById('arome-btn-total');
    const btnInterval = document.getElementById('arome-btn-interval');

    if (btnTotal && btnInterval) {
      if (currentType === 'interval') {
        btnInterval.classList.add('active');
        btnTotal.classList.remove('active');
      } else {
        btnTotal.classList.add('active');
        btnInterval.classList.remove('active');
      }
    }

    const availSteps = metadata.available_steps || [];
    if (slider && availSteps.length > 0) {
      slider.min = availSteps[0];
      slider.max = availSteps[availSteps.length - 1];
      slider.value = currentStep;
    }

    const stepInfo = (metadata.steps || []).find(s => s.step === currentStep);

    if (maxPill && stepInfo) {
      const maxVal = currentType === 'interval' ? stepInfo.max_interval_mm : stepInfo.max_total_mm;
      maxPill.innerHTML = `🎯 Máx: <strong>${maxVal !== undefined ? maxVal : '--'} mm</strong>`;
      maxPill.title = 'Ir al punto de precipitación máxima en el mapa';
    }

    // Actualizar el Banner Superior con el instante exacto en hora local (Europe/Madrid)
    if (this.predictionBannerExactTime && stepInfo && stepInfo.valid_time_iso) {
      const formattedInstant = formatPredictionInstant(stepInfo.valid_time_iso);
      this.predictionBannerExactTime.textContent = formattedInstant;
    }
    if (this.predictionBannerModel) {
      this.predictionBannerModel.textContent = `AROME HD (+${currentStep}h)`;
    }
    if (this.predictionBannerMode) {
      const modeText = currentType === 'total' ? 'Acumulado Total' : 'Intervalo 1h';
      this.predictionBannerMode.textContent = modeText;
    }

    // Visibilidad del banner: visible si alguna capa de predicción está activa
    if (this.predictionBanner) {
      const isPredActive = Boolean(this.layerManager && (this.layerManager.isLayerOnMap('ecmwf_ifs') || this.layerManager.isLayerOnMap('gfs_0p25') || this.layerManager.isLayerOnMap('arome_precip')));
      this.predictionBanner.style.display = isPredActive ? 'flex' : 'none';
    }

    this.updateAromePlayState(isPlaying);
  }

  /**
   * Actualiza el estado visual del botón Play/Pausa de ECMWF IFS
   */
  updateEcmwfPlayState(isPlaying) {
    const playBtn = document.getElementById('ecmwf-play-btn');
    const playText = document.getElementById('ecmwf-play-text');
    const playIcon = document.getElementById('ecmwf-play-icon');

    if (playBtn && playText && playIcon) {
      if (isPlaying) {
        playBtn.classList.add('playing');
        playText.textContent = 'Pausa';
        playIcon.innerHTML = '<rect x="6" y="4" width="4" height="16"></rect><rect x="14" y="4" width="4" height="16"></rect>';
      } else {
        playBtn.classList.remove('playing');
        playText.textContent = 'Animar';
        playIcon.innerHTML = '<polygon points="5 3 19 12 5 21 5 3"></polygon>';
      }
    }
  }

  /**
   * Actualiza el estado visual del botón Play/Pausa de NOAA GFS
   */
  updateGfsPlayState(isPlaying) {
    const playBtn = document.getElementById('gfs-play-btn');
    const playText = document.getElementById('gfs-play-text');
    const playIcon = document.getElementById('gfs-play-icon');

    if (playBtn && playText && playIcon) {
      if (isPlaying) {
        playBtn.classList.add('playing');
        playText.textContent = 'Pausa';
        playIcon.innerHTML = '<rect x="6" y="4" width="4" height="16"></rect><rect x="14" y="4" width="4" height="16"></rect>';
      } else {
        playBtn.classList.remove('playing');
        playText.textContent = 'Animar';
        playIcon.innerHTML = '<polygon points="5 3 19 12 5 21 5 3"></polygon>';
      }
    }
  }

  /**
   * Actualiza el estado visual del botón Play/Pausa de AROME
   */
  updateAromePlayState(isPlaying) {
    const playBtn = document.getElementById('arome-play-btn');
    const playText = document.getElementById('arome-play-text');
    const playIcon = document.getElementById('arome-play-icon');

    if (playBtn && playText && playIcon) {
      if (isPlaying) {
        playBtn.classList.add('playing');
        playText.textContent = 'Pausa';
        playIcon.innerHTML = '<rect x="6" y="4" width="4" height="16"></rect><rect x="14" y="4" width="4" height="16"></rect>';
      } else {
        playBtn.classList.remove('playing');
        playText.textContent = 'Animar';
        playIcon.innerHTML = '<polygon points="5 3 19 12 5 21 5 3"></polygon>';
      }
    }
  }


  /**
   * Actualiza el botón indicador de cuenca favorita en el encabezado y en el panel de ajustes móvil
   */
  renderFavoriteBadge() {
    const prefs = StorageManager.load();

    // Versión Escritorio
    if (this.favBtnEl) {
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

    // Versión Móvil
    if (this.btnFavoriteBasinMobile) {
      if (prefs.favoriteBasinId && prefs.favoriteBasinName) {
        this.btnFavoriteBasinMobile.style.display = 'flex';
        if (this.favBasinMobileLabel) {
          this.favBasinMobileLabel.textContent = `Centrar en ${prefs.favoriteBasinName}`;
        }
      } else {
        this.btnFavoriteBasinMobile.style.display = 'none';
      }
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
