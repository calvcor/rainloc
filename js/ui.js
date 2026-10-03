import { CONFIG, formatMadridDateTime, formatMadridTime, formatPredictionInstant, getLightningAgeTiers } from './config.js';
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
    this.predictionBannerRun = document.getElementById('prediction-banner-run');
    this.predictionBannerMode = document.getElementById('prediction-banner-mode');
    this.predictionBannerExactTime = document.getElementById('prediction-banner-exact-time');
    this.timelineBottomPlayer = document.getElementById('timeline-bottom-player');
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

    // Inicializar reproductor temporal inferior unificado (Radar / Modelos)
    this._initTimelineBottomPlayer();
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
    if (window.RainLoc && window.RainLoc.multiInspector) {
      window.RainLoc.multiInspector.closeMobileInspector();
    }

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
          content.style.display = 'flex';
        } else {
          content.classList.remove('active');
          content.style.display = 'none';
        }
      }
    });

    StorageManager.setActiveTab(tabId);

    if (this.layerManager && this.layerManager.onTabChange) {
      this.layerManager.onTabChange(tabId);
    }

    if (tabId === 'prediction') {
      this.updatePredictionFooterOpacity();
    }

    this.updateUnifiedTimelinePlayer();
  }

  /**
   * Obtiene el ID del modelo de predicción actualmente activo
   * @returns {string|null}
   */
  getActivePredictionModelId() {
    if (!this.layerManager) return null;
    const predLayers = CONFIG.overlayLayers.prediction;
    for (const p of predLayers) {
      const state = this.layerManager.getLayerState(p.id);
      if (state && state.active) return p.id;
    }
    return null;
  }

  /**
   * Sincroniza el control de opacidad unificado del pie del panel con el modelo activo
   */
  updatePredictionFooterOpacity() {
    const predSlider = document.getElementById('prediction-opacity-slider');
    const predValEl = document.getElementById('prediction-opacity-val');
    if (!predSlider) return;

    const activeModelId = this.getActivePredictionModelId();
    let opacityPct = 70;
    if (activeModelId && this.layerManager) {
      const state = this.layerManager.getLayerState(activeModelId);
      if (state && state.opacity !== undefined) {
        opacityPct = Math.round(state.opacity * 100);
      }
    } else {
      const prefs = StorageManager.load();
      const activePred = CONFIG.overlayLayers.prediction.find(p => (prefs.activeLayers && prefs.activeLayers[p.id]));
      if (activePred) {
        const op = (prefs.layerOpacities && prefs.layerOpacities[activePred.id] !== undefined) ? prefs.layerOpacities[activePred.id] : activePred.defaultOpacity;
        opacityPct = Math.round(op * 100);
      }
    }

    predSlider.value = opacityPct;
    if (predValEl) predValEl.textContent = `${opacityPct}%`;
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

    if (layerId === 'saih_hidrologia' || layerId.startsWith('saih_')) {
      this.updateSaihGroupUI();
    }

    if (CONFIG.overlayLayers.prediction.some(p => p.id === layerId)) {
      this.updatePredictionFooterOpacity();
    }

    this.updateUnifiedTimelinePlayer();
    this.updateMobileLayersBadge();
  }

  /**
   * Actualiza el contador de subcapas activas y el estado visual del selector agrupado SAIH y de pluviometría
   */
  updateSaihGroupUI() {
    const prefs = StorageManager.load();
    const activeLayers = prefs.activeLayers || {};
    const saihDef = CONFIG.overlayLayers.realtime.find(r => r.id === 'saih_hidrologia');
    if (!saihDef) return;

    const isMasterActive = (this.layerManager && this.layerManager.layerStates['saih_hidrologia']?.active !== undefined)
      ? Boolean(this.layerManager.layerStates['saih_hidrologia'].active)
      : Boolean(activeLayers['saih_hidrologia']);

    const subLayers = saihDef.subLayers || [];

    let activeCount = 0;
    subLayers.forEach(sub => {
      const isSubActive = (this.layerManager && this.layerManager.layerStates[sub.id]?.active !== undefined)
        ? Boolean(this.layerManager.layerStates[sub.id].active)
        : (activeLayers[sub.id] !== undefined ? Boolean(activeLayers[sub.id]) : (sub.defaultActive !== undefined ? sub.defaultActive : true));

      if (isMasterActive && isSubActive) {
        activeCount++;
      }

      const subItem = document.querySelector(`.saih-sublayer-item[data-sublayer-id="${sub.id}"]`);
      const subCheckbox = document.querySelector(`.saih-sublayer-checkbox[data-sublayer-id="${sub.id}"]`);
      if (subCheckbox) {
        subCheckbox.checked = isSubActive;
      }
      if (subItem) {
        subItem.classList.toggle('active', isMasterActive && isSubActive);
      }
    });

    const countEl = document.getElementById('saih-active-count');
    if (countEl) {
      countEl.textContent = `${activeCount}/${subLayers.length} activos`;
      countEl.classList.toggle('none-active', isMasterActive && activeCount === 0);
    }

    // Actualizar estado del toggle general de pluviometría
    const pluvioSubLayers = subLayers.filter(s => s.group === 'pluvio' || s.id.includes('lluvias'));
    if (pluvioSubLayers.length > 0) {
      const activePluvioCount = pluvioSubLayers.filter(sub => {
        const isSubActive = (this.layerManager && this.layerManager.layerStates[sub.id]?.active !== undefined)
          ? Boolean(this.layerManager.layerStates[sub.id].active)
          : (activeLayers[sub.id] !== undefined ? Boolean(activeLayers[sub.id]) : true);
        return isMasterActive && isSubActive;
      }).length;

      const pluvioMasterCheckbox = document.getElementById('pluvio-master-toggle');
      const pluvioCountEl = document.getElementById('pluvio-active-count');

      if (pluvioMasterCheckbox) {
        pluvioMasterCheckbox.checked = isMasterActive && activePluvioCount === pluvioSubLayers.length;
        pluvioMasterCheckbox.indeterminate = isMasterActive && activePluvioCount > 0 && activePluvioCount < pluvioSubLayers.length;
      }
      if (pluvioCountEl) {
        pluvioCountEl.textContent = `${activePluvioCount}/${pluvioSubLayers.length}`;
        pluvioCountEl.classList.toggle('none-active', isMasterActive && activePluvioCount === 0);
      }
    }

    const masterCheckbox = document.querySelector(`.layer-toggle-input[data-layer-id="saih_hidrologia"]`);
    const masterCard = document.querySelector(`.layer-card[data-layer-id="saih_hidrologia"]`);
    const masterControls = document.getElementById('controls-saih_hidrologia');

    if (masterCheckbox) masterCheckbox.checked = isMasterActive;
    if (masterCard) masterCard.classList.toggle('active', isMasterActive);
    if (masterControls) masterControls.style.display = isMasterActive ? 'block' : 'none';
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

    // Sincronizar slider de opacidad del pie fijo de predicción
    this.updatePredictionFooterOpacity();

    // Vincular listeners a los switches y sliders generados
    this._bindLayerCardEvents();
  }

  /**
   * Genera el HTML para cada tarjeta de capa
   */
  _createLayerCardHtml(layer, isActive, opacity) {
    const opacityPct = Math.round(opacity * 100);
    const prefs = StorageManager.load();
    const activeLayers = prefs.activeLayers || {};
    const refreshSec = prefs.autoRefreshInterval !== undefined ? prefs.autoRefreshInterval : 180;

    // Tarjeta especial agrupada para la Red Hidrológica y Pluviometría
    if (layer.type === 'saih_group') {
      const subLayers = layer.subLayers || [];
      const activeCount = subLayers.filter(sub => {
        const isSubActive = (this.layerManager && this.layerManager.layerStates[sub.id]?.active !== undefined)
          ? Boolean(this.layerManager.layerStates[sub.id].active)
          : (activeLayers[sub.id] !== undefined ? Boolean(activeLayers[sub.id]) : (sub.defaultActive !== undefined ? sub.defaultActive : true));
        return isActive && isSubActive;
      }).length;

      const renderSublayerItem = (sub) => {
        const isSubActive = (this.layerManager && this.layerManager.layerStates[sub.id]?.active !== undefined)
          ? Boolean(this.layerManager.layerStates[sub.id].active)
          : (activeLayers[sub.id] !== undefined ? Boolean(activeLayers[sub.id]) : (sub.defaultActive !== undefined ? sub.defaultActive : true));
        const isEffectiveActive = isActive && isSubActive;

        return `
          <div class="saih-sublayer-item ${isEffectiveActive ? 'active' : ''}" data-sublayer-id="${sub.id}">
            <label class="saih-sublayer-label">
              <input type="checkbox" class="saih-sublayer-checkbox" data-sublayer-id="${sub.id}" ${isSubActive ? 'checked' : ''}>
              <span class="saih-sublayer-icon">${sub.icon}</span>
              <div class="saih-sublayer-info">
                <div class="saih-sublayer-title-row">
                  <span class="saih-sublayer-name">${escapeHtml(sub.name)}</span>
                  <span class="saih-sublayer-badge ${sub.badgeClass || ''}">${escapeHtml(sub.badge || '')}</span>
                </div>
                <span class="saih-sublayer-subtitle">${escapeHtml(sub.subtitle)}</span>
              </div>
            </label>
            ${sub.sourceUrl ? `
              <a href="${sub.sourceUrl}" target="_blank" rel="noopener noreferrer" class="sublayer-source-link" title="Abrir fuente oficial: ${escapeHtml(sub.sourceName || sub.name)}" onclick="event.stopPropagation();">
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"></path><polyline points="15 3 21 3 21 9"></polyline><line x1="10" y1="14" x2="21" y2="3"></line></svg>
              </a>
            ` : ''}
          </div>
        `;
      };

      const hidroSubLayers = subLayers.filter(s => s.group === 'hidro' || s.id === 'saih_caudales' || s.id === 'saih_embalses');
      const pluvioSubLayers = subLayers.filter(s => s.group === 'pluvio' || s.id.includes('lluvias'));

      const hidroListHtml = hidroSubLayers.map(renderSublayerItem).join('');
      const pluvioListHtml = pluvioSubLayers.map(renderSublayerItem).join('');

      const activePluvioCount = pluvioSubLayers.filter(sub => {
        const isSubActive = (this.layerManager && this.layerManager.layerStates[sub.id]?.active !== undefined)
          ? Boolean(this.layerManager.layerStates[sub.id].active)
          : (activeLayers[sub.id] !== undefined ? Boolean(activeLayers[sub.id]) : true);
        return isActive && isSubActive;
      }).length;
      const isAllPluvioActive = isActive && activePluvioCount === pluvioSubLayers.length;

      return `
        <div class="layer-card saih-group-card ${isActive ? 'active' : ''}" data-layer-id="${layer.id}">
          <div class="layer-card-main">
            <label class="layer-switch" title="Activar/desactivar ${escapeHtml(layer.name)}">
              <input type="checkbox" class="layer-toggle-input" data-layer-id="${layer.id}" ${isActive ? 'checked' : ''}>
              <span class="switch-slider"></span>
            </label>
            <div class="layer-card-info">
              <div class="layer-card-title-row">
                <span class="layer-icon" style="color: ${layer.color};">${layer.icon}</span>
                <span class="layer-card-name">${escapeHtml(layer.name)}</span>
                <span class="saih-active-count-badge ${isActive && activeCount === 0 ? 'none-active' : ''}" id="saih-active-count">${activeCount}/${subLayers.length} activos</span>
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
              <span class="layer-opacity-label">Opacidad general</span>
              <span class="layer-opacity-value" id="val-${layer.id}">${opacityPct}%</span>
            </div>
            <input type="range" class="slider-glass layer-slider" min="10" max="100" value="${opacityPct}" step="5" data-layer-id="${layer.id}">
            
            <div class="saih-sublayers-container">
              <!-- Sección Aforos y Embalses -->
              <div class="saih-sublayers-section">
                <div class="saih-sublayers-header">
                  <span class="saih-sublayers-header-title">Aforos y Embalses</span>
                </div>
                <div class="saih-sublayers-list">
                  ${hidroListHtml}
                </div>
              </div>

              <!-- Sección Red Pluviométrica con Selector de Modo y Periodos -->
              <div class="saih-sublayers-section pluvio-sublayers-section">
                <div class="saih-sublayers-header pluvio-header-row">
                  <span class="saih-sublayers-header-title">Red de Pluviometría</span>
                  <label class="pluvio-master-toggle-label" title="Activar o desactivar todos los pluviómetros de todas las redes">
                    <input type="checkbox" class="pluvio-master-checkbox" id="pluvio-master-toggle" ${isAllPluvioActive ? 'checked' : ''}>
                    <span class="pluvio-master-toggle-text">Todas las redes</span>
                    <span class="pluvio-master-count-badge" id="pluvio-active-count">${activePluvioCount}/${pluvioSubLayers.length}</span>
                  </label>
                </div>

                <!-- Selector de Modo de Visualización (Puntos vs Malla Suave Continua) -->
                <div class="pluvio-mode-control-group">
                  <div class="pluvio-mode-segmented">
                    <button type="button" class="pluvio-mode-btn ${((this.layerManager && this.layerManager.pluvioRenderMode) || prefs.pluvioRenderMode || 'points') === 'points' ? 'active' : ''}" data-pluvio-mode="points" title="Mostrar estaciones individuales como puntos interactivos">
                      <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor"><circle cx="12" cy="12" r="6"/></svg>
                      Puntos
                    </button>
                    <button type="button" class="pluvio-mode-btn ${((this.layerManager && this.layerManager.pluvioRenderMode) || prefs.pluvioRenderMode || 'points') === 'mesh' ? 'active' : ''}" data-pluvio-mode="mesh" title="Generar mapa suave y continuo de acumulados interpolado en el navegador">
                      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><path d="M4 14.899A7 7 0 1 1 15.71 8h1.79a4.5 4.5 0 0 1 2.5 8.242"></path><path d="M16 14v6"></path><path d="M8 14v6"></path><path d="M12 16v6"></path></svg>
                      Malla Suave
                    </button>
                  </div>
                </div>

                <!-- Opciones avanzadas de Malla Continua de Acumulados -->
                <div class="pluvio-mesh-options-panel" id="pluvio-mesh-options-panel" style="${((this.layerManager && this.layerManager.pluvioRenderMode) || prefs.pluvioRenderMode || 'points') === 'mesh' ? '' : 'display: none;'}">
                  <div class="pluvio-period-row">
                    <span class="pluvio-period-label">Acumulado:</span>
                    <div class="pluvio-period-chips">
                      <button type="button" class="pluvio-period-chip ${((this.layerManager && this.layerManager.pluvioMeshPeriod) || prefs.pluvioMeshPeriod || '24h') === '1h' ? 'active' : ''}" data-period="1h" title="Lluvia acumulada en la última 1 hora">1h</button>
                      <button type="button" class="pluvio-period-chip ${((this.layerManager && this.layerManager.pluvioMeshPeriod) || prefs.pluvioMeshPeriod || '24h') === '4h' ? 'active' : ''}" data-period="4h" title="Lluvia acumulada en las últimas 4 horas">4h</button>
                      <button type="button" class="pluvio-period-chip ${((this.layerManager && this.layerManager.pluvioMeshPeriod) || prefs.pluvioMeshPeriod || '24h') === '12h' ? 'active' : ''}" data-period="12h" title="Lluvia acumulada en las últimas 12 horas">12h</button>
                      <button type="button" class="pluvio-period-chip ${((this.layerManager && this.layerManager.pluvioMeshPeriod) || prefs.pluvioMeshPeriod || '24h') === '24h' ? 'active' : ''}" data-period="24h" title="Lluvia acumulada en las últimas 24 horas">24h</button>
                    </div>
                  </div>

                  <div class="pluvio-labels-row">
                    <label class="pluvio-labels-toggle-label" title="Mostrar valores numéricos en estaciones sobre el mapa suave">
                      <input type="checkbox" id="toggle-pluvio-mesh-labels" ${((this.layerManager && this.layerManager.pluvioMeshLabels !== undefined) ? this.layerManager.pluvioMeshLabels : (prefs.pluvioMeshLabels !== undefined ? prefs.pluvioMeshLabels : true)) ? 'checked' : ''}>
                      <span class="pluvio-labels-toggle-text">Valores en estaciones (mm)</span>
                    </label>
                  </div>

                  <!-- Leyenda de colores cromáticos de lluvia acumulada estilo AVAMET -->
                  <div class="pluvio-mesh-legend-card">
                    <div class="mesh-legend-header">
                      <span class="mesh-legend-title">Escala de Acumulado (${((this.layerManager && this.layerManager.pluvioMeshPeriod) || prefs.pluvioMeshPeriod || '24h').toUpperCase()})</span>
                      <span class="mesh-legend-unit">mm</span>
                    </div>
                    <div class="mesh-legend-colorbar"></div>
                    <div class="mesh-legend-ticks">
                      <span>0</span>
                      <span>2</span>
                      <span>10</span>
                      <span>30</span>
                      <span>60</span>
                      <span>100</span>
                      <span>150</span>
                      <span>250</span>
                      <span>500</span>
                    </div>
                  </div>
                </div>

                <div class="saih-sublayers-list">
                  ${pluvioListHtml}
                </div>
              </div>

              <!-- Bloque de Atribución Legal y Licencias de Reutilización -->
              <div class="saih-sources-legal-block">
                <div class="saih-sources-legal-header">
                  <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"></circle><line x1="12" y1="16" x2="12" y2="12"></line><line x1="12" y1="8" x2="12.01" y2="8"></line></svg>
                  <span>Fuentes oficiales & Licencias de uso</span>
                </div>
                <div class="saih-sources-links">
                  <a href="https://saih.chj.es" target="_blank" rel="noopener noreferrer" class="source-legal-link" title="Redes SAIH Hidrográficas (CHJ, CHE, CHG, Hidrosur)">
                    <span class="source-dot" style="background:#0284c7;"></span> <strong>Redes SAIH</strong>
                  </a>
                  <a href="https://opendata.aemet.es" target="_blank" rel="noopener noreferrer" class="source-legal-link" title="Agencia Estatal de Meteorología - OpenData">
                    <span class="source-dot" style="background:#2563eb;"></span> <strong>AEMET</strong>
                  </a>
                  <a href="https://www.avamet.org" target="_blank" rel="noopener noreferrer" class="source-legal-link" title="Associació Valenciana de Meteorologia - MeteoXarxa Online">
                    <span class="source-dot" style="background:#059669;"></span> <strong>AVAMET</strong>
                  </a>
                  <a href="https://analisi.transparenciacatalunya.cat/d/nzvn-apee" target="_blank" rel="noopener noreferrer" class="source-legal-link" title="Servei Meteorològic de Catalunya - Dades Obertes Gencat">
                    <span class="source-dot" style="background:#d97706;"></span> <strong>METEOCAT</strong>
                  </a>
                </div>
              </div>
            </div>
          </div>
        </div>
      `;
    }

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

          <!-- Opción de Área de Cobertura de Radar dinámica por fotograma -->
          <div class="radar-coverage-option">
            <label class="radar-coverage-toggle-label">
              <input type="checkbox" id="radar-coverage-toggle" ${prefs.showRadarCoverage ? 'checked' : ''}>
              <span class="coverage-toggle-text">📡 Mostrar área de cobertura</span>
            </label>
            <div class="radar-coverage-details" id="radar-coverage-container" style="${prefs.showRadarCoverage ? '' : 'display: none;'}">
              <span class="radar-stations-pill" id="radar-active-stations-pill">
                ● <strong>${Object.keys(CONFIG.radarStations).length} radares con datos</strong>
              </span>
            </div>
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
                  ${getLightningAgeTiers(prefs.lightningWindow || 15).tiers.map(t => `<span class="age-pill"><svg viewBox="0 0 24 24" class="legend-bolt-icon" style="width: 10px; height: 10px; flex-shrink: 0; ${t.pulse ? `filter: drop-shadow(0 0 3px ${t.fillColor});` : ''}"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2" fill="${t.fillColor}" stroke="#000000" stroke-width="1.5" stroke-linejoin="round"/></svg> ${t.label}</span>`).join('')}
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
    } else if (layer.id === 'harmonie_aemet') {
      modelSyncBtnHtml = `
        <button type="button" class="btn-model-sync-pill" id="harmonie-sync-btn" title="Comprobar si hay nueva corrida o pasos ahora">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.3" stroke-linecap="round" stroke-linejoin="round"><path d="M21.5 2v6h-6M21.34 15.57a10 10 0 1 1-.57-8.38l5.67-5.67"/></svg>
        </button>
      `;
    } else if (layer.id === 'icon_eu') {
      modelSyncBtnHtml = `
        <button type="button" class="btn-model-sync-pill" id="icon-sync-btn" title="Comprobar si hay nueva corrida o pasos ahora">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.3" stroke-linecap="round" stroke-linejoin="round"><path d="M21.5 2v6h-6M21.34 15.57a10 10 0 1 1-.57-8.38l5.67-5.67"/></svg>
        </button>
      `;
    } else if (layer.id === 'gem_gdps') {
      modelSyncBtnHtml = `
        <button type="button" class="btn-model-sync-pill" id="gem-sync-btn" title="Comprobar si hay nueva corrida o pasos ahora">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.3" stroke-linecap="round" stroke-linejoin="round"><path d="M21.5 2v6h-6M21.34 15.57a10 10 0 1 1-.57-8.38l5.67-5.67"/></svg>
        </button>
      `;
    }

    // Sub-controles específicos para Avisos AEMET (Selector de periodo: Activos ahora arriba, Hoy / Mañana / Pasado abajo)
    let aemetExtraControls = '';
    if (layer.id === 'aemet_warnings') {
      const selectedPeriod = (this.layerManager && this.layerManager.currentAemetPeriod) || prefs.aemetPeriod || 'now';
      aemetExtraControls = `
        <div class="aemet-subcontrols">
          <div class="aemet-period-selector" role="group" aria-label="Periodo de avisos AEMET">
            <div class="aemet-period-row-top">
              <button type="button" class="aemet-period-btn aemet-period-btn-full ${selectedPeriod === 'now' ? 'active' : ''}" data-period="now" title="Avisos en vigor en este momento"><span class="aemet-btn-dot"></span>Activos ahora</button>
            </div>
            <div class="aemet-period-row-bottom">
              <button type="button" class="aemet-period-btn ${selectedPeriod === 'today' ? 'active' : ''}" data-period="today" title="Avisos activos y restantes en el día de hoy"><span class="aemet-btn-dot"></span>Hoy</button>
              <button type="button" class="aemet-period-btn ${selectedPeriod === 'tomorrow' ? 'active' : ''}" data-period="tomorrow" title="Avisos previstos para mañana"><span class="aemet-btn-dot"></span>Mañana</button>
              <button type="button" class="aemet-period-btn ${selectedPeriod === 'after_tomorrow' ? 'active' : ''}" data-period="after_tomorrow" title="Avisos previstos para pasado mañana"><span class="aemet-btn-dot"></span>Pasado</button>
            </div>
          </div>
        </div>
      `;
    }

    const isPrediction = CONFIG.overlayLayers.prediction.some(p => p.id === layer.id);

    const controlsHtml = isPrediction ? '' : `
      <div class="layer-card-controls" id="controls-${layer.id}" style="${isActive ? '' : 'display: none;'}">
        <div class="layer-opacity-row">
          <span class="layer-opacity-label">Opacidad</span>
          <span class="layer-opacity-value" id="val-${layer.id}">${opacityPct}%</span>
        </div>
        <input type="range" class="slider-glass layer-slider" min="10" max="100" value="${opacityPct}" step="5" data-layer-id="${layer.id}">
        ${aemetExtraControls}
        ${radarExtraControls}
      </div>
    `;

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
        ${controlsHtml}
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
      case 'saih_hidrologia':
      case 'saih_caudales':
      case 'saih_embalses':
      case 'saih_lluvias':
      case 'aemet_lluvias':
      case 'avamet_lluvias':
      case 'meteocat_lluvias':
      case 'hidrosur_lluvias':
        return `Actualizado: <strong>${nowFormatted}</strong>`;
      case 'arome_precip':
        return `Pasada: <strong>${nowFormatted.split(' · ')[0]} · 02:00 (+6h)</strong>`;
      case 'harmonie_aemet':
        return `Pasada: <strong>${nowFormatted.split(' · ')[0]} · 00:00 (+6h)</strong>`;
      case 'icon_eu':
        return `Pasada: <strong>${nowFormatted.split(' · ')[0]} · 06:00 (+1h)</strong>`;
      case 'ecmwf_ifs':
        return `Pasada: <strong>${nowFormatted.split(' · ')[0]} · 02:00 (+12h)</strong>`;
      case 'gem_gdps':
        return `Pasada: <strong>${nowFormatted.split(' · ')[0]} · 00:00 (+3h)</strong>`;
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
   * Actualiza el estado visual activo de los botones de periodo de Avisos AEMET
   * @param {string} period 
   */
  updateAemetPeriodButtons(period) {
    document.querySelectorAll('.aemet-period-btn').forEach(btn => {
      if (btn.getAttribute('data-period') === period) {
        btn.classList.add('active');
      } else {
        btn.classList.remove('active');
      }
    });
  }

  /**
   * Colorea dinámicamente los botones de periodo de Avisos AEMET con el nivel y color de aviso máximo nacional
   * @param {Object} periodsSummary Resumen con { now: { max_color, max_rank, ... }, tomorrow: ..., after_tomorrow: ... }
   */
  updateAemetPeriodStyles(periodsSummary) {
    if (!periodsSummary) return;
    const levelRgbMap = {
      red: '239, 68, 68',
      orange: '251, 146, 60',
      yellow: '250, 204, 21',
      green: '34, 197, 94'
    };

    Object.entries(periodsSummary).forEach(([period, info]) => {
      const btn = document.querySelector(`.aemet-period-btn[data-period="${period}"]`);
      if (!btn) return;

      let dot = btn.querySelector('.aemet-btn-dot');
      if (!dot) {
        dot = document.createElement('span');
        dot.className = 'aemet-btn-dot';
        btn.prepend(dot);
      }

      if (info && info.max_color && info.max_rank > 0) {
        const sev = (info.max_severity || 'yellow').toLowerCase();
        let level = 'yellow';
        if (sev.includes('red') || sev.includes('rojo') || sev.includes('extreme') || info.max_rank === 4) {
          level = 'red';
        } else if (sev.includes('orange') || sev.includes('naranja') || sev.includes('severe') || info.max_rank === 3) {
          level = 'orange';
        } else if (sev.includes('yellow') || sev.includes('amarillo') || sev.includes('moderate') || info.max_rank === 2) {
          level = 'yellow';
        } else if (sev.includes('green') || sev.includes('verde') || info.max_rank === 1) {
          level = 'green';
        }

        const rgb = levelRgbMap[level] || '250, 204, 21';
        btn.setAttribute('data-severity-color', info.max_color);
        btn.setAttribute('data-severity-level', level);
        btn.style.setProperty('--period-color', info.max_color);
        btn.style.setProperty('--period-color-rgb', rgb);
        dot.style.backgroundColor = info.max_color;
        dot.style.boxShadow = `0 0 6px ${info.max_color}`;
        dot.style.display = 'inline-block';
      } else {
        btn.removeAttribute('data-severity-color');
        btn.removeAttribute('data-severity-level');
        btn.style.removeProperty('--period-color');
        btn.style.removeProperty('--period-color-rgb');
        dot.style.display = 'none';
      }
    });
  }

  /**
   * Actualiza dinámicamente la leyenda de colores de rayos según la ventana seleccionada
   * @param {number} windowMinutes 
   */
  updateLightningLegend(windowMinutes) {
    const pillsRow = document.querySelector('.lightning-age-legend .age-pills-row');
    if (!pillsRow) return;
    const tierConfig = getLightningAgeTiers(windowMinutes);
    pillsRow.innerHTML = tierConfig.tiers.map(t => {
      const shadow = t.pulse ? `filter: drop-shadow(0 0 3px ${t.fillColor});` : '';
      return `<span class="age-pill"><svg viewBox="0 0 24 24" class="legend-bolt-icon" style="width: 10px; height: 10px; flex-shrink: 0; ${shadow}"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2" fill="${t.fillColor}" stroke="#000000" stroke-width="1.5" stroke-linejoin="round"/></svg> ${t.label}</span>`;
    }).join('');
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

    // Sub-switches de la Red SAIH (CHJ) y Pluviometría
    document.querySelectorAll('.saih-sublayer-checkbox').forEach(checkbox => {
      checkbox.addEventListener('change', (e) => {
        const sublayerId = e.target.getAttribute('data-sublayer-id');
        const isChecked = e.target.checked;
        if (this.layerManager && this.layerManager.toggleSaihSublayer) {
          this.layerManager.toggleSaihSublayer(sublayerId, isChecked);
        }
      });
    });

    // Toggle General de la Red Pluviométrica (Todas las redes)
    const pluvioMasterCheckbox = document.getElementById('pluvio-master-toggle');
    if (pluvioMasterCheckbox) {
      pluvioMasterCheckbox.addEventListener('change', (e) => {
        const isChecked = e.target.checked;
        if (this.layerManager && this.layerManager.togglePluvioGroup) {
          this.layerManager.togglePluvioGroup(isChecked);
        }
      });
    }

    // Modo de renderizado de Pluviómetros (Puntos vs Malla Suave Continua)
    document.querySelectorAll('.pluvio-mode-btn').forEach(btn => {
      btn.addEventListener('click', (e) => {
        const mode = e.currentTarget.getAttribute('data-pluvio-mode');
        document.querySelectorAll('.pluvio-mode-btn').forEach(b => b.classList.remove('active'));
        e.currentTarget.classList.add('active');

        const meshPanel = document.getElementById('pluvio-mesh-options-panel');
        if (meshPanel) {
          meshPanel.style.display = (mode === 'mesh') ? 'block' : 'none';
        }

        if (this.layerManager && this.layerManager.setPluvioRenderMode) {
          this.layerManager.setPluvioRenderMode(mode);
        }
      });
    });

    // Selector de periodo de acumulación de Malla de Lluvia (1h, 4h, 12h, 24h)
    document.querySelectorAll('.pluvio-period-chip').forEach(chip => {
      chip.addEventListener('click', (e) => {
        const period = e.currentTarget.getAttribute('data-period');
        document.querySelectorAll('.pluvio-period-chip').forEach(c => c.classList.remove('active'));
        e.currentTarget.classList.add('active');

        const legendTitle = document.querySelector('.pluvio-mesh-legend-card .mesh-legend-title');
        if (legendTitle) {
          legendTitle.textContent = `Escala de Acumulado (${period.toUpperCase()})`;
        }

        if (this.layerManager && this.layerManager.setPluvioMeshPeriod) {
          this.layerManager.setPluvioMeshPeriod(period);
        }
      });
    });

    // Toggle de etiquetas numéricas sobre la malla
    const meshLabelsToggle = document.getElementById('toggle-pluvio-mesh-labels');
    if (meshLabelsToggle) {
      meshLabelsToggle.addEventListener('change', (e) => {
        const isChecked = e.target.checked;
        if (this.layerManager && this.layerManager.setPluvioMeshLabels) {
          this.layerManager.setPluvioMeshLabels(isChecked);
        }
      });
    }

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

    // Selector de periodo de Avisos AEMET (Activos ahora, Mañana, Pasado)
    document.querySelectorAll('.aemet-period-btn').forEach(btn => {
      btn.addEventListener('click', (e) => {
        const period = e.currentTarget.getAttribute('data-period');
        document.querySelectorAll('.aemet-period-btn').forEach(b => b.classList.remove('active'));
        e.currentTarget.classList.add('active');

        if (this.layerManager) {
          this.layerManager.setAemetPeriod(period);
        }
      });
    });



    // Controles de Cobertura de Radar: Toggle de visualización
    const radarCoverageToggle = document.getElementById('radar-coverage-toggle');
    if (radarCoverageToggle) {
      radarCoverageToggle.addEventListener('change', (e) => {
        const isChecked = e.target.checked;
        const container = document.getElementById('radar-coverage-container');
        if (container) {
          container.style.display = isChecked ? 'flex' : 'none';
        }
        StorageManager.save({ showRadarCoverage: isChecked });
        if (this.layerManager) {
          this.layerManager.toggleRadarCoverage(isChecked);
        }
      });
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
        this.updateLightningLegend(minutes);
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

    // Slider de opacidad unificado para el pie de modelos de predicción
    const predSlider = document.getElementById('prediction-opacity-slider');
    const predValEl = document.getElementById('prediction-opacity-val');
    if (predSlider && !predSlider._hasBoundEvents) {
      predSlider._hasBoundEvents = true;
      predSlider.addEventListener('input', (e) => {
        const val = parseInt(e.target.value, 10);
        if (predValEl) predValEl.textContent = `${val}%`;
        const activeModelId = this.getActivePredictionModelId();
        if (activeModelId && this.layerManager) {
          this.layerManager.setLayerOpacity(activeModelId, val / 100);
        }
      });
    }

    // Botones de Sincronización Manual Inmediata
    this._setupModelSyncButton('ecmwf-sync-btn', 'ecmwf');
    this._setupModelSyncButton('gfs-sync-btn', 'gfs');
    this._setupModelSyncButton('arome-sync-btn', 'arome');
    this._setupModelSyncButton('harmonie-sync-btn', 'harmonie');
    this._setupModelSyncButton('icon-sync-btn', 'icon');
    this._setupModelSyncButton('gem-sync-btn', 'gem');
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
          if (this.layerManager.isLayerActive('harmonie_aemet')) {
            syncTasks.push(this.layerManager.triggerModelSync('harmonie'));
          }
          if (this.layerManager.isLayerActive('ecmwf_ifs')) {
            syncTasks.push(this.layerManager.triggerModelSync('ecmwf'));
          }
          if (this.layerManager.isLayerActive('gfs_0p25')) {
            syncTasks.push(this.layerManager.triggerModelSync('gfs'));
          }
          if (this.layerManager.isLayerActive('arome_precip')) {
            syncTasks.push(this.layerManager.triggerModelSync('arome'));
          }
          if (this.layerManager.isLayerActive('icon_eu')) {
            syncTasks.push(this.layerManager.triggerModelSync('icon'));
          }
          if (this.layerManager.isLayerActive('gem_gdps')) {
            syncTasks.push(this.layerManager.triggerModelSync('gem'));
          }

          if (syncTasks.length === 0) {
            // Si ninguno está activo visible, sincroniza todos
            await Promise.all([
              this.layerManager.triggerModelSync('harmonie'),
              this.layerManager.triggerModelSync('ecmwf'),
              this.layerManager.triggerModelSync('gfs'),
              this.layerManager.triggerModelSync('arome'),
              this.layerManager.triggerModelSync('icon'),
              this.layerManager.triggerModelSync('gem')
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
   * Helper para obtener el tipo de capa con línea temporal actualmente activa en el mapa
   * @returns {'radar' | 'ecmwf_ifs' | 'gfs_0p25' | 'arome_precip' | 'icon_eu' | 'gem_gdps' | null}
   */
  _getActiveTimelineType() {
    if (!this.layerManager) return null;
    if (this.layerManager.isLayerOnMap('radar')) return 'radar';
    if (this.layerManager.isLayerOnMap('harmonie_aemet') || this.layerManager.isLayerOnMap('harmonie')) return 'harmonie_aemet';
    if (this.layerManager.isLayerOnMap('arome_precip')) return 'arome_precip';
    if (this.layerManager.isLayerOnMap('icon_eu') || this.layerManager.isLayerOnMap('icon')) return 'icon_eu';
    if (this.layerManager.isLayerOnMap('gem_gdps') || this.layerManager.isLayerOnMap('gem')) return 'gem_gdps';
    if (this.layerManager.isLayerOnMap('ecmwf_ifs')) return 'ecmwf_ifs';
    if (this.layerManager.isLayerOnMap('gfs_0p25')) return 'gfs_0p25';
    return null;
  }

  /**
   * Inicializa los listeners del reproductor temporal inferior unificado (Radar / Modelos)
   */
  _initTimelineBottomPlayer() {
    const playBtn = document.getElementById('timeline-play-btn');
    if (playBtn) {
      playBtn.addEventListener('click', () => {
        if (!this.layerManager) return;
        const activeType = this._getActiveTimelineType();
        if (activeType === 'radar') {
          this.layerManager.toggleRadarPlayback();
        } else if (activeType === 'harmonie_aemet') {
          this.layerManager.toggleHarmoniePlayback();
        } else if (activeType === 'arome_precip') {
          this.layerManager.toggleAromePlayback();
        } else if (activeType === 'icon_eu') {
          this.layerManager.toggleIconPlayback();
        } else if (activeType === 'gem_gdps') {
          this.layerManager.toggleGemPlayback();
        } else if (activeType === 'ecmwf_ifs') {
          this.layerManager.toggleEcmwfPlayback();
        } else if (activeType === 'gfs_0p25') {
          this.layerManager.toggleGfsPlayback();
        }
      });
    }

    const liveBtn = document.getElementById('timeline-live-btn');
    if (liveBtn) {
      liveBtn.addEventListener('click', () => {
        if (!this.layerManager) return;
        this.layerManager.pauseRadarPlayback();
        this.layerManager.setRadarLive();
      });
    }

    const prevBtn = document.getElementById('timeline-prev-btn');
    if (prevBtn) {
      prevBtn.addEventListener('click', () => {
        if (!this.layerManager) return;
        const activeType = this._getActiveTimelineType();
        if (activeType === 'radar') {
          this.layerManager.pauseRadarPlayback();
          const timeline = this.layerManager.radarTimeline || [];
          if (timeline.length === 0) return;
          const curIdx = timeline.findIndex(t => t.timestep === this.layerManager.currentRadarTimestep);
          const prevIdx = curIdx <= 0 ? 0 : curIdx - 1;
          this.layerManager.setRadarTimestep(timeline[prevIdx].timestep);
        } else if (activeType) {
          this._stepModel(activeType, -1);
        }
      });
    }

    const nextBtn = document.getElementById('timeline-next-btn');
    if (nextBtn) {
      nextBtn.addEventListener('click', () => {
        if (!this.layerManager) return;
        const activeType = this._getActiveTimelineType();
        if (activeType === 'radar') {
          this.layerManager.pauseRadarPlayback();
          const timeline = this.layerManager.radarTimeline || [];
          if (timeline.length === 0) return;
          const curIdx = timeline.findIndex(t => t.timestep === this.layerManager.currentRadarTimestep);
          const nextIdx = (curIdx === -1 || curIdx >= timeline.length - 1) ? timeline.length - 1 : curIdx + 1;
          this.layerManager.setRadarTimestep(timeline[nextIdx].timestep);
        } else if (activeType) {
          this._stepModel(activeType, 1);
        }
      });
    }

    const modeTotal = document.getElementById('timeline-mode-total');
    const modeInterval = document.getElementById('timeline-mode-interval');
    if (modeTotal && modeInterval) {
      modeTotal.addEventListener('click', () => {
        if (!this.layerManager) return;
        const activeType = this._getActiveTimelineType();
        if (activeType === 'harmonie_aemet') this.layerManager.setHarmonieType('total');
        else if (activeType === 'arome_precip') this.layerManager.setAromeType('total');
        else if (activeType === 'icon_eu') this.layerManager.setIconType('total');
        else if (activeType === 'gem_gdps') this.layerManager.setGemType('total');
        else if (activeType === 'ecmwf_ifs') this.layerManager.setEcmwfType('total');
        else if (activeType === 'gfs_0p25') this.layerManager.setGfsType('total');
      });
      modeInterval.addEventListener('click', () => {
        if (!this.layerManager) return;
        const activeType = this._getActiveTimelineType();
        if (activeType === 'harmonie_aemet') this.layerManager.setHarmonieType('interval');
        else if (activeType === 'arome_precip') this.layerManager.setAromeType('interval');
        else if (activeType === 'icon_eu') this.layerManager.setIconType('interval');
        else if (activeType === 'gem_gdps') this.layerManager.setGemType('interval');
        else if (activeType === 'ecmwf_ifs') this.layerManager.setEcmwfType('interval');
        else if (activeType === 'gfs_0p25') this.layerManager.setGfsType('interval');
      });
    }

    const maxPill = document.getElementById('timeline-max-pill');
    if (maxPill) {
      maxPill.addEventListener('click', () => {
        if (!this.layerManager) return;
        const activeType = this._getActiveTimelineType();
        if (activeType === 'harmonie_aemet') this.layerManager.flyToModelMax('harmonie');
        else if (activeType === 'arome_precip') this.layerManager.flyToModelMax('arome');
        else if (activeType === 'icon_eu') this.layerManager.flyToModelMax('icon');
        else if (activeType === 'gem_gdps') this.layerManager.flyToModelMax('gem');
        else if (activeType === 'ecmwf_ifs') this.layerManager.flyToModelMax('ecmwf');
        else if (activeType === 'gfs_0p25') this.layerManager.flyToModelMax('gfs');
      });
    }

    const slider = document.getElementById('timeline-step-slider');
    if (slider) {
      let _sliderDebounceTimer = null;

      const previewTimelineSlider = (rawVal) => {
        if (!this.layerManager) return;
        const activeType = this._getActiveTimelineType();
        if (activeType === 'radar') {
          const timeline = this.layerManager.radarTimeline || [];
          const item = timeline[rawVal];
          if (item) {
            const timeText = document.getElementById('timeline-time-text');
            if (timeText) {
              const timeOnly = item.valid_time_local ? String(item.valid_time_local).trim().split(/\s+/).pop() : '';
              const isLive = item.is_latest || item.timestep === timeline[timeline.length - 1]?.timestep;
              timeText.textContent = isLive ? (timeOnly ? `Radar · ${timeOnly}` : 'Radar · Directo') : `Radar · ${timeOnly}`;
            }
          }
        } else if (activeType) {
          let meta = null;
          let modelLabel = '';
          let modelFlag = '';
          let curType = 'total';
          if (activeType === 'harmonie_aemet') {
            meta = this.layerManager.harmonieMetadata;
            curType = this.layerManager.currentHarmonieType || 'total';
            modelLabel = 'HARMONIE (2.5km)';
            modelFlag = '🇪🇸';
          } else if (activeType === 'arome_precip') {
            meta = this.layerManager.aromeMetadata;
            curType = this.layerManager.currentAromeType || 'total';
            modelLabel = 'AROME HD';
            modelFlag = '🇫🇷';
          } else if (activeType === 'icon_eu') {
            meta = this.layerManager.iconMetadata;
            curType = this.layerManager.currentIconType || 'total';
            modelLabel = 'ICON-EU (6.5km)';
            modelFlag = '🇩🇪';
          } else if (activeType === 'gem_gdps') {
            meta = this.layerManager.gemMetadata;
            curType = this.layerManager.currentGemType || 'total';
            modelLabel = 'GEM-GDPS (15km)';
            modelFlag = '🇨🇦';
          } else if (activeType === 'ecmwf_ifs') {
            meta = this.layerManager.ecmwfMetadata;
            curType = this.layerManager.currentEcmwfType || 'total';
            modelLabel = 'ECMWF IFS';
            modelFlag = '🇪🇺';
          } else if (activeType === 'gfs_0p25') {
            meta = this.layerManager.gfsMetadata;
            curType = this.layerManager.currentGfsType || 'total';
            modelLabel = 'NOAA GFS';
            modelFlag = '🇺🇸';
          }
          if (!meta || !meta.available_steps || meta.available_steps.length === 0) return;
          const steps = meta.available_steps;
          let closestStep = steps[0];
          let minDiff = Infinity;
          for (const s of steps) {
            const diff = Math.abs(s - rawVal);
            if (diff < minDiff) {
              minDiff = diff;
              closestStep = s;
            }
          }
          const timeText = document.getElementById('timeline-time-text');
          if (timeText) {
            timeText.innerHTML = `${modelFlag} <strong>${modelLabel}</strong> (+${closestStep}h)`;
          }
          const maxPill = document.getElementById('timeline-max-pill');
          const stepInfo = (meta.steps || []).find(s => s.step === closestStep);
          if (maxPill && stepInfo) {
            const maxVal = curType === 'interval'
              ? (stepInfo.max_interval_mm !== undefined ? stepInfo.max_interval_mm : stepInfo.max_interval_precip_mm)
              : (stepInfo.max_total_mm !== undefined ? stepInfo.max_total_mm : stepInfo.max_total_precip_mm);
            maxPill.innerHTML = `🎯 Máx: <strong>${maxVal !== undefined ? maxVal : '--'} mm</strong>`;
          }
          if (this.predictionBannerExactTime && stepInfo && stepInfo.valid_time_iso) {
            this.predictionBannerExactTime.textContent = formatPredictionInstant(stepInfo.valid_time_iso);
          }
        }
      };

      const handleSliderChange = (rawVal) => {
        if (!this.layerManager) return;
        const activeType = this._getActiveTimelineType();
        if (activeType === 'radar') {
          this.layerManager.pauseRadarPlayback();
          const timeline = this.layerManager.radarTimeline || [];
          const item = timeline[rawVal];
          if (item) this.layerManager.setRadarTimestep(item.timestep);
        } else if (activeType) {
          this._handleModelSliderInput(activeType, rawVal);
        }
      };

      slider.addEventListener('input', (e) => {
        const val = parseInt(e.target.value, 10);
        previewTimelineSlider(val);
        if (_sliderDebounceTimer) clearTimeout(_sliderDebounceTimer);
        _sliderDebounceTimer = setTimeout(() => {
          _sliderDebounceTimer = null;
          handleSliderChange(val);
        }, 75);
      });

      slider.addEventListener('change', (e) => {
        if (_sliderDebounceTimer) {
          clearTimeout(_sliderDebounceTimer);
          _sliderDebounceTimer = null;
        }
        handleSliderChange(parseInt(e.target.value, 10));
      });

      slider.addEventListener('wheel', (e) => {
        e.preventDefault();
        if (!this.layerManager) return;
        const activeType = this._getActiveTimelineType();
        if (activeType === 'radar') {
          const timeline = this.layerManager.radarTimeline || [];
          if (timeline.length === 0) return;
          const curIdx = timeline.findIndex(t => t.timestep === this.layerManager.currentRadarTimestep);
          const delta = e.deltaY > 0 ? -1 : 1;
          const newIdx = Math.max(0, Math.min(curIdx + delta, timeline.length - 1));
          this.layerManager.pauseRadarPlayback();
          this.layerManager.setRadarTimestep(timeline[newIdx].timestep);
        } else if (activeType) {
          const delta = e.deltaY > 0 ? -1 : 1;
          this._stepModel(activeType, delta);
        }
      }, { passive: false });
    }
  }

  _stepModel(modelId, direction) {
    if (!this.layerManager) return;
    let meta = null;
    let curStep = null;
    let setStepFn = null;
    if (modelId === 'harmonie_aemet' || modelId === 'harmonie') {
      meta = this.layerManager.harmonieMetadata;
      curStep = this.layerManager.currentHarmonieStep;
      setStepFn = (s) => this.layerManager.setHarmonieStep(s);
    } else if (modelId === 'arome_precip' || modelId === 'arome') {
      meta = this.layerManager.aromeMetadata;
      curStep = this.layerManager.currentAromeStep;
      setStepFn = (s) => this.layerManager.setAromeStep(s);
    } else if (modelId === 'icon_eu' || modelId === 'icon') {
      meta = this.layerManager.iconMetadata;
      curStep = this.layerManager.currentIconStep;
      setStepFn = (s) => this.layerManager.setIconStep(s);
    } else if (modelId === 'gem_gdps' || modelId === 'gem') {
      meta = this.layerManager.gemMetadata;
      curStep = this.layerManager.currentGemStep;
      setStepFn = (s) => this.layerManager.setGemStep(s);
    } else if (modelId === 'ecmwf_ifs' || modelId === 'ecmwf') {
      meta = this.layerManager.ecmwfMetadata;
      curStep = this.layerManager.currentEcmwfStep;
      setStepFn = (s) => this.layerManager.setEcmwfStep(s);
    } else if (modelId === 'gfs_0p25' || modelId === 'gfs') {
      meta = this.layerManager.gfsMetadata;
      curStep = this.layerManager.currentGfsStep;
      setStepFn = (s) => this.layerManager.setGfsStep(s);
    }
    if (!meta || !setStepFn) return;
    const steps = meta.available_steps || [];
    if (steps.length === 0) return;
    const curIdx = steps.indexOf(curStep);
    const nextIdx = (curIdx + direction + steps.length) % steps.length;
    setStepFn(steps[nextIdx]);
  }

  _handleModelSliderInput(modelId, rawVal) {
    if (!this.layerManager) return;
    let meta = null;
    let setStepFn = null;
    if (modelId === 'harmonie_aemet' || modelId === 'harmonie') {
      meta = this.layerManager.harmonieMetadata;
      setStepFn = (s) => this.layerManager.setHarmonieStep(s);
    } else if (modelId === 'arome_precip' || modelId === 'arome') {
      meta = this.layerManager.aromeMetadata;
      setStepFn = (s) => this.layerManager.setAromeStep(s);
    } else if (modelId === 'icon_eu' || modelId === 'icon') {
      meta = this.layerManager.iconMetadata;
      setStepFn = (s) => this.layerManager.setIconStep(s);
    } else if (modelId === 'gem_gdps' || modelId === 'gem') {
      meta = this.layerManager.gemMetadata;
      setStepFn = (s) => this.layerManager.setGemStep(s);
    } else if (modelId === 'ecmwf_ifs' || modelId === 'ecmwf') {
      meta = this.layerManager.ecmwfMetadata;
      setStepFn = (s) => this.layerManager.setEcmwfStep(s);
    } else if (modelId === 'gfs_0p25' || modelId === 'gfs') {
      meta = this.layerManager.gfsMetadata;
      setStepFn = (s) => this.layerManager.setGfsStep(s);
    }
    if (!meta || !setStepFn) return;
    const steps = meta.available_steps || [];
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
    setStepFn(closestStep);
  }

  setTimelineLoading(isLoading) {
    const bottomPlayer = this.timelineBottomPlayer || document.getElementById('timeline-bottom-player');
    if (bottomPlayer) {
      bottomPlayer.classList.toggle('is-loading', Boolean(isLoading));
    }
    if (this.predictionBanner) {
      this.predictionBanner.classList.toggle('is-loading', Boolean(isLoading));
    }
  }

  toggleRadarBottomPlayer(show) {
    this.updateUnifiedTimelinePlayer();
  }

  /**
   * Actualiza el Banner Superior de Predicción únicamente con el modelo actualmente activo en el mapa
   */
  updatePredictionBanner() {
    if (!this.predictionBanner) return;
    if (!this.layerManager) {
      this.predictionBanner.style.display = 'none';
      return;
    }

    const activeType = this._getActiveTimelineType();
    let metadata = null;
    let currentStep = null;
    let currentType = 'total';
    let modelName = '';
    let modeIntervalText = 'Intervalo 1h';

    if (activeType === 'harmonie_aemet') {
      metadata = this.layerManager.harmonieMetadata;
      currentStep = this.layerManager.currentHarmonieStep;
      currentType = this.layerManager.currentHarmonieType || 'total';
      modelName = `🇪🇸 HARMONIE (+${currentStep}h)`;
      modeIntervalText = 'Intervalo 1h';
    } else if (activeType === 'arome_precip') {
      metadata = this.layerManager.aromeMetadata;
      currentStep = this.layerManager.currentAromeStep;
      currentType = this.layerManager.currentAromeType || 'total';
      modelName = `🇫🇷 AROME HD (+${currentStep}h)`;
      modeIntervalText = 'Intervalo 1h';
    } else if (activeType === 'icon_eu') {
      metadata = this.layerManager.iconMetadata;
      currentStep = this.layerManager.currentIconStep;
      currentType = this.layerManager.currentIconType || 'total';
      modelName = `🇩🇪 ICON-EU (+${currentStep}h)`;
      modeIntervalText = currentStep > 78 ? 'Intervalo 3h' : 'Intervalo 1h';
    } else if (activeType === 'gem_gdps') {
      metadata = this.layerManager.gemMetadata;
      currentStep = this.layerManager.currentGemStep;
      currentType = this.layerManager.currentGemType || 'total';
      modelName = `🇨🇦 GEM-GDPS (+${currentStep}h)`;
      modeIntervalText = 'Intervalo 3h';
    } else if (activeType === 'ecmwf_ifs') {
      metadata = this.layerManager.ecmwfMetadata;
      currentStep = this.layerManager.currentEcmwfStep;
      currentType = this.layerManager.currentEcmwfType || 'total';
      modelName = `🇪🇺 ECMWF IFS (+${currentStep}h)`;
      modeIntervalText = currentStep > 144 ? 'Intervalo 6h' : 'Intervalo 3h';
    } else if (activeType === 'gfs_0p25') {
      metadata = this.layerManager.gfsMetadata;
      currentStep = this.layerManager.currentGfsStep;
      currentType = this.layerManager.currentGfsType || 'total';
      modelName = `🇺🇸 NOAA GFS (+${currentStep}h)`;
      modeIntervalText = currentStep > 120 ? 'Intervalo 6h' : 'Intervalo 3h';
    } else {
      // Si el radar u otra capa no predictiva está activa o no hay modelo en el mapa
      this.predictionBanner.style.display = 'none';
      return;
    }

    if (!metadata) {
      this.predictionBanner.style.display = 'none';
      return;
    }

    this.predictionBanner.style.display = 'flex';

    const stepInfo = (metadata.steps || []).find(s => s.step === currentStep);

    if (this.predictionBannerExactTime) {
      if (stepInfo && stepInfo.valid_time_iso) {
        this.predictionBannerExactTime.textContent = formatPredictionInstant(stepInfo.valid_time_iso);
      } else {
        this.predictionBannerExactTime.textContent = '--';
      }
    }

    if (this.predictionBannerModel) {
      this.predictionBannerModel.textContent = modelName;
    }

    if (this.predictionBannerRun) {
      const isFallback = Boolean(stepInfo && stepInfo.is_fallback);
      const mainRun = (metadata.run || (metadata.cycle_str ? metadata.cycle_str.split('_')[1] : '')).toUpperCase();
      const stepRun = (stepInfo && (stepInfo.run || stepInfo.fallback_run)) ? (stepInfo.run || stepInfo.fallback_run).toUpperCase() : mainRun;
      if (stepRun) {
        this.predictionBannerRun.style.display = 'inline-flex';
        this.predictionBannerRun.textContent = isFallback ? `Run ${stepRun} ant.` : `Run ${stepRun}`;
        this.predictionBannerRun.className = `prediction-run-tag ${isFallback ? 'fallback-run' : 'new-run'}`;
        this.predictionBannerRun.title = isFallback ? `Paso de la salida anterior (${stepRun}) mientras se descarga la nueva salida (${mainRun})` : `Salida ${stepRun}`;
      } else {
        this.predictionBannerRun.style.display = 'none';
      }
    }

    if (this.predictionBannerMode) {
      this.predictionBannerMode.textContent = currentType === 'total' ? 'Acumulado Total' : modeIntervalText;
    }
  }

  /**
   * Actualiza el reproductor temporal inferior unificado según la capa activa en el mapa
   */
  updateUnifiedTimelinePlayer() {
    this.updatePredictionBanner();

    const bottomPlayer = this.timelineBottomPlayer || document.getElementById('timeline-bottom-player');
    if (!bottomPlayer) return;

    const activeType = this._getActiveTimelineType();
    if (!activeType) {
      bottomPlayer.style.display = 'none';
      if (typeof document !== 'undefined' && document.body) {
        document.body.classList.remove('has-timeline-player');
      }
      return;
    }

    bottomPlayer.style.display = 'flex';
    if (typeof document !== 'undefined' && document.body) {
      document.body.classList.add('has-timeline-player');
    }

    const slider = document.getElementById('timeline-step-slider');
    const timeText = document.getElementById('timeline-time-text');
    const liveDot = document.getElementById('timeline-live-dot');
    const liveBtn = document.getElementById('timeline-live-btn');
    const modeGroup = document.getElementById('timeline-model-mode-group');
    const modeTotal = document.getElementById('timeline-mode-total');
    const modeInterval = document.getElementById('timeline-mode-interval');
    const maxPill = document.getElementById('timeline-max-pill');
    const labelsContainer = document.getElementById('timeline-slider-labels');

    if (activeType === 'radar') {
      const timeline = this.layerManager.radarTimeline || [];
      const currentStep = this.layerManager.currentRadarTimestep;
      const currentEntry = timeline.find(t => t.timestep === currentStep);
      const isLive = currentEntry ? (currentEntry.is_latest || currentStep === timeline[timeline.length - 1]?.timestep) : true;

      if (liveDot) liveDot.style.display = isLive ? 'inline-block' : 'none';
      if (liveBtn) {
        liveBtn.style.display = 'inline-flex';
        liveBtn.classList.toggle('active', isLive);
      }
      if (modeGroup) modeGroup.style.display = 'none';
      if (maxPill) maxPill.style.display = 'none';

      const getCleanTime = (ts, validLocal) => {
        if (ts) {
          const t = formatMadridTime(ts);
          if (t && t.length <= 5) return t;
        }
        if (validLocal) {
          const parts = String(validLocal).trim().split(/\s+/);
          return parts[parts.length - 1];
        }
        return '';
      };

      if (timeText) {
        const timeOnly = getCleanTime(currentEntry?.timestep, currentEntry?.valid_time_local);
        if (isLive) {
          timeText.textContent = timeOnly ? `Radar · ${timeOnly}` : 'Radar · Directo';
        } else if (currentEntry) {
          const ageStr = currentEntry.age_text ? ` (-${currentEntry.age_text})` : '';
          timeText.textContent = `Radar · ${timeOnly}${ageStr}`;
        } else {
          timeText.textContent = `Radar · ${currentStep || '--'}`;
        }
      }

      const activePill = document.getElementById('radar-active-stations-pill');
      if (activePill) {
        const total = Object.keys(CONFIG.radarStations).length;
        const count = currentEntry?.active_radars_count ?? (currentEntry?.active_radars ? currentEntry.active_radars.length : 12);
        activePill.innerHTML = `📡 <strong>${count}/${total}</strong> radares con datos`;
      }

      if (slider && timeline.length > 0) {
        slider.min = 0;
        slider.max = timeline.length - 1;
        slider.step = 1;
        const curIdx = timeline.findIndex(t => t.timestep === currentStep);
        slider.value = curIdx >= 0 ? curIdx : timeline.length - 1;
        slider.disabled = false;
        slider.style.background = '';
        slider.title = '';
      }

      if (labelsContainer && (!labelsContainer._currentType || labelsContainer._currentType !== 'radar')) {
        labelsContainer._currentType = 'radar';
        labelsContainer.innerHTML = `
          <span>-24h</span>
          <span>-18h</span>
          <span>-12h</span>
          <span>-6h</span>
          <span>-3h</span>
          <span>-1h</span>
          <span>Ahora</span>
        `;
      }

      this._updateTimelinePlayButton(this.layerManager.isRadarPlaying);

    } else if (activeType === 'harmonie_aemet' || activeType === 'arome_precip' || activeType === 'icon_eu' || activeType === 'gem_gdps' || activeType === 'ecmwf_ifs' || activeType === 'gfs_0p25') {
      let meta = null;
      let curStep = null;
      let curType = null;
      let isPlaying = false;
      let modelLabel = '';
      let modelFlag = '';
      let labelsHtml = '';

      if (activeType === 'harmonie_aemet') {
        meta = this.layerManager.harmonieMetadata || {};
        curStep = this.layerManager.currentHarmonieStep || 1;
        curType = this.layerManager.currentHarmonieType || 'total';
        isPlaying = this.layerManager.isHarmoniePlaying;
        modelLabel = 'HARMONIE (2.5km)';
        modelFlag = '🇪🇸';
        labelsHtml = '<span>+1h</span><span>+12h</span><span>+24h (1d)</span><span>+36h</span><span>+48h (2d)</span>';
      } else if (activeType === 'arome_precip') {
        meta = this.layerManager.aromeMetadata || {};
        curStep = this.layerManager.currentAromeStep || 1;
        curType = this.layerManager.currentAromeType || 'total';
        isPlaying = this.layerManager.isAromePlaying;
        modelLabel = 'AROME HD';
        modelFlag = '🇫🇷';
        labelsHtml = '<span>+1h</span><span>+12h</span><span>+24h (1d)</span><span>+36h</span><span>+48h (2d)</span>';
      } else if (activeType === 'icon_eu') {
        meta = this.layerManager.iconMetadata || {};
        curStep = this.layerManager.currentIconStep || 1;
        curType = this.layerManager.currentIconType || 'total';
        isPlaying = this.layerManager.isIconPlaying;
        modelLabel = 'ICON-EU (6.5km)';
        modelFlag = '🇩🇪';
        labelsHtml = '<span>+1h</span><span>+24h (1d)</span><span>+48h (2d)</span><span>+72h (3d)</span><span>+120h (5d)</span>';
      } else if (activeType === 'gem_gdps') {
        meta = this.layerManager.gemMetadata || {};
        curStep = this.layerManager.currentGemStep || 3;
        curType = this.layerManager.currentGemType || 'total';
        isPlaying = this.layerManager.isGemPlaying;
        modelLabel = 'GEM-GDPS (15km)';
        modelFlag = '🇨🇦';
        labelsHtml = '<span>+3h</span><span>+48h (2d)</span><span>+96h (4d)</span><span>+168h (7d)</span><span>+240h (10d)</span>';
      } else if (activeType === 'ecmwf_ifs') {
        meta = this.layerManager.ecmwfMetadata || {};
        curStep = this.layerManager.currentEcmwfStep || 3;
        curType = this.layerManager.currentEcmwfType || 'total';
        isPlaying = this.layerManager.isEcmwfPlaying;
        modelLabel = 'ECMWF IFS';
        modelFlag = '🇪🇺';
        labelsHtml = '<span>+3h</span><span>+72h (3d)</span><span>+144h (6d)</span><span>+240h (10d)</span>';
      } else if (activeType === 'gfs_0p25') {
        meta = this.layerManager.gfsMetadata || {};
        curStep = this.layerManager.currentGfsStep || 3;
        curType = this.layerManager.currentGfsType || 'total';
        isPlaying = this.layerManager.isGfsPlaying;
        modelLabel = 'NOAA GFS';
        modelFlag = '🇺🇸';
        labelsHtml = '<span>+3h</span><span>+96h (4d)</span><span>+192h (8d)</span><span>+288h (12d)</span><span>+384h (16d)</span>';
      }

      if (liveDot) liveDot.style.display = 'none';
      if (liveBtn) liveBtn.style.display = 'none';
      if (modeGroup) modeGroup.style.display = 'inline-flex';
      if (modeTotal) modeTotal.classList.toggle('active', curType === 'total');
      if (modeInterval) modeInterval.classList.toggle('active', curType === 'interval');

      const stepInfo = (meta.steps || []).find(s => s.step === curStep);
      const mainRun = (meta.run || (meta.cycle_str ? meta.cycle_str.split('_')[1] : '')).toUpperCase();

      if (maxPill) {
        maxPill.style.display = 'inline-flex';
        if (stepInfo) {
          const maxVal = curType === 'interval'
            ? (stepInfo.max_interval_mm !== undefined ? stepInfo.max_interval_mm : stepInfo.max_interval_precip_mm)
            : (stepInfo.max_total_mm !== undefined ? stepInfo.max_total_mm : stepInfo.max_total_precip_mm);
          maxPill.innerHTML = `🎯 Máx: <strong>${maxVal !== undefined ? maxVal : '--'} mm</strong>`;
        } else {
          maxPill.innerHTML = `🎯 Máx: -- mm`;
        }
      }

      if (timeText) {
        timeText.innerHTML = `${modelFlag} <strong>${modelLabel}</strong> (+${curStep}h)`;
      }

      const availSteps = meta.available_steps || [];
      const nativeSteps = (meta.steps || []).filter(s => !s.is_fallback).map(s => s.step);
      const nativeMax = nativeSteps.length > 0
        ? Math.max(...nativeSteps)
        : (meta.downloaded_max_step || meta.raw_max_step || null);
      const hasFallback = (meta.steps || []).some(s => s.is_fallback) || (nativeMax && (meta.is_updating || meta.is_syncing));

      if (slider && availSteps.length > 0) {
        let minStep = availSteps[0];
        let maxStep = availSteps[availSteps.length - 1];
        let stepInc = 3;

        if (activeType === 'harmonie_aemet' || activeType === 'arome_precip') {
          minStep = 1;
          maxStep = 48;
          stepInc = 1;
        } else if (activeType === 'icon_eu') {
          minStep = 1;
          maxStep = 120;
          stepInc = 1;
        } else if (activeType === 'gem_gdps') {
          minStep = 3;
          maxStep = 240;
          stepInc = 3;
        } else if (activeType === 'ecmwf_ifs') {
          minStep = 3;
          maxStep = 240;
          stepInc = 3;
        } else if (activeType === 'gfs_0p25') {
          minStep = 3;
          maxStep = 384;
          stepInc = 3;
        }

        slider.min = minStep;
        slider.max = maxStep;
        slider.step = stepInc;
        slider.value = curStep;
        slider.disabled = false;

        // Estilizar track del slider cuando la salida es parcial o se está actualizando (hay corte con la salida anterior)
        if (hasFallback && nativeMax && nativeMax < maxStep) {
          const splitPct = Math.max(0, Math.min(100, ((nativeMax - minStep) / Math.max(1, maxStep - minStep)) * 100));
          const modelColor = (activeType === 'ecmwf_ifs') ? '#059669' : ((activeType === 'gfs_0p25') ? '#2563eb' : ((activeType === 'harmonie_aemet') ? '#f59e0b' : ((activeType === 'icon_eu') ? '#0284c7' : ((activeType === 'gem_gdps') ? '#e11d48' : '#8b5cf6'))));
          slider.style.background = `linear-gradient(to right, ${modelColor} 0%, ${modelColor} ${splitPct}%, rgba(245, 158, 11, 0.45) ${splitPct}%, rgba(245, 158, 11, 0.45) 100%)`;
          slider.title = `Salida ${mainRun} disponible hasta +${nativeMax}h (${splitPct.toFixed(0)}%). Pasos posteriores (+${nativeMax + 1}h a +${maxStep}h): Salida anterior`;
        } else {
          slider.style.background = '';
          slider.title = '';
        }
      }

      if (labelsContainer && (!labelsContainer._currentType || labelsContainer._currentType !== activeType)) {
        labelsContainer._currentType = activeType;
        labelsContainer.innerHTML = labelsHtml;
      }

      this._updateTimelinePlayButton(isPlaying);
    }
  }

  _updateTimelinePlayButton(isPlaying) {
    const playBtn = document.getElementById('timeline-play-btn');
    const playIcon = document.getElementById('timeline-play-icon');
    const playText = document.getElementById('timeline-play-text');

    if (!playBtn) return;

    if (isPlaying) {
      playBtn.classList.add('playing');
      if (playIcon) {
        playIcon.innerHTML = '<rect x="6" y="4" width="4" height="16"></rect><rect x="14" y="4" width="4" height="16"></rect>';
      }
      if (playText) {
        playText.textContent = 'Pausa';
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
   * Actualiza el reproductor interactivo de Radar 24h en la UI
   */
  updateRadarPlayerUI(timeline, currentTimestep, isPlaying, isLive) {
    this.updateUnifiedTimelinePlayer();
  }

  /**
   * Actualiza el estado visual del botón play/pause del radar
   */
  updateRadarPlayState(isPlaying) {
    this._updateTimelinePlayButton(isPlaying);
  }

  /**
   * Actualiza el reproductor interactivo de ECMWF IFS en la UI
   */
  updateEcmwfPlayerUI(metadata, currentStep, currentType, isPlaying) {
    const slider = document.getElementById('ecmwf-step-slider');
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

    this.updateEcmwfPlayState(isPlaying);
    this.updateUnifiedTimelinePlayer();
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

    this.updateGfsPlayState(isPlaying);
    this.updateUnifiedTimelinePlayer();
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

    this.updateAromePlayState(isPlaying);
    this.updateUnifiedTimelinePlayer();
  }

  /**
   * Actualiza el reproductor interactivo de AEMET HARMONIE-AROME en la UI
   */
  updateHarmoniePlayerUI(metadata, currentStep, currentType, isPlaying) {
    const slider = document.getElementById('harmonie-step-slider');
    const maxPill = document.getElementById('harmonie-max-pill');
    const btnTotal = document.getElementById('harmonie-btn-total');
    const btnInterval = document.getElementById('harmonie-btn-interval');

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
      const maxVal = currentType === 'interval'
        ? (stepInfo.max_interval_mm !== undefined ? stepInfo.max_interval_mm : stepInfo.max_interval_precip_mm)
        : (stepInfo.max_total_mm !== undefined ? stepInfo.max_total_mm : stepInfo.max_total_precip_mm);
      maxPill.innerHTML = `🎯 Máx: <strong>${maxVal !== undefined ? maxVal : '--'} mm</strong>`;
      maxPill.title = 'Ir al punto de precipitación máxima en el mapa';
    }

    this.updateHarmoniePlayState(isPlaying);
    this.updateUnifiedTimelinePlayer();
  }

  updateHarmoniePlayState(isPlaying) {
    const playBtn = document.getElementById('harmonie-play-btn');
    if (!playBtn) return;
    if (isPlaying) {
      playBtn.classList.add('playing');
      playBtn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="4" width="4" height="16"></rect><rect x="14" y="4" width="4" height="16"></rect></svg> Pausar';
    } else {
      playBtn.classList.remove('playing');
      playBtn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><polygon points="5 3 19 12 5 21 5 3"></polygon></svg> Animar';
    }
  }

  /**
   * Actualiza el reproductor interactivo de DWD ICON-EU en la UI
   */
  updateIconPlayerUI(metadata, currentStep, currentType, isPlaying) {
    const slider = document.getElementById('icon-step-slider');
    const maxPill = document.getElementById('icon-max-pill');
    const btnTotal = document.getElementById('icon-btn-total');
    const btnInterval = document.getElementById('icon-btn-interval');

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

    this.updateIconPlayState(isPlaying);
    this.updateUnifiedTimelinePlayer();
  }

  /**
   * Actualiza el reproductor interactivo de MSC GEM-GDPS en la UI
   */
  updateGemPlayerUI(metadata, currentStep, currentType, isPlaying) {
    const slider = document.getElementById('gem-step-slider');
    const maxPill = document.getElementById('gem-max-pill');
    const btnTotal = document.getElementById('gem-btn-total');
    const btnInterval = document.getElementById('gem-btn-interval');

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

    this.updateGemPlayState(isPlaying);
    this.updateUnifiedTimelinePlayer();
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
   * Actualiza el estado visual del botón Play/Pausa de DWD ICON-EU
   */
  updateIconPlayState(isPlaying) {
    const playBtn = document.getElementById('icon-play-btn');
    const playText = document.getElementById('icon-play-text');
    const playIcon = document.getElementById('icon-play-icon');

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
   * Actualiza el estado visual del botón Play/Pausa de MSC GEM-GDPS
   */
  updateGemPlayState(isPlaying) {
    const playBtn = document.getElementById('gem-play-btn');
    const playText = document.getElementById('gem-play-text');
    const playIcon = document.getElementById('gem-play-icon');

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
   * Actualiza el estado visual de los controles de la malla de lluvia
   */
  updatePluvioMeshUI() {
    if (!this.layerManager) return;
    const mode = this.layerManager.pluvioRenderMode;
    const period = this.layerManager.pluvioMeshPeriod;
    const labels = this.layerManager.pluvioMeshLabels;

    document.querySelectorAll('.pluvio-mode-btn').forEach(btn => {
      btn.classList.toggle('active', btn.getAttribute('data-pluvio-mode') === mode);
    });

    const meshPanel = document.getElementById('pluvio-mesh-options-panel');
    if (meshPanel) {
      meshPanel.style.display = (mode === 'mesh') ? 'block' : 'none';
    }

    document.querySelectorAll('.pluvio-period-chip').forEach(chip => {
      chip.classList.toggle('active', chip.getAttribute('data-period') === period);
    });

    const legendTitle = document.querySelector('.pluvio-mesh-legend-card .mesh-legend-title');
    if (legendTitle) {
      legendTitle.textContent = `Escala de Acumulado (${period.toUpperCase()})`;
    }

    const meshLabelsToggle = document.getElementById('toggle-pluvio-mesh-labels');
    if (meshLabelsToggle) {
      meshLabelsToggle.checked = labels;
    }
  }

  /**
   * Actualiza la leyenda y los datos resumen de la malla
   */
  updatePluvioMeshLegend(period, maxVal, pointsCount) {
    const legendTitle = document.querySelector('.pluvio-mesh-legend-card .mesh-legend-title');
    if (legendTitle) {
      legendTitle.textContent = `Acumulado ${period.toUpperCase()}${maxVal !== undefined && maxVal > 0 ? ` · Máx: ${maxVal.toFixed(1)} mm` : ''}`;
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
