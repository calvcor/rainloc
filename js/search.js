/**
 * RainLoc - Buscador Geográfico de Lugares y Toponimia
 * Soporte dual:
 * - Desktop / iPad: Barra de búsqueda integrada en el Header superior.
 * - Móvil (< 768px): Botón flotante con lupa en mapa que expande a pantalla completa y oculta controles.
 */

import { CONFIG } from './config.js';

export class PlacesSearchControl {
  constructor(mapManager) {
    this.mapManager = mapManager;
    this.map = mapManager ? mapManager.map : null;
    this.activeMarker = null;

    // Header Desktop Elements
    this.headerContainer = document.getElementById('header-search-container');
    this.headerInput = document.getElementById('header-search-input');
    this.headerClearBtn = document.getElementById('btn-header-search-clear');
    this.headerSpinner = document.getElementById('header-search-spinner');
    this.headerDropdown = document.getElementById('header-search-dropdown');
    this.headerResults = document.getElementById('header-search-results');

    // Mobile Map Control Elements
    this.mobileControl = null;
    this.mobileContainer = null;
    this.mobileInput = null;
    this.mobileClearBtn = null;
    this.mobileSpinner = null;
    this.mobileDropdown = null;
    this.mobileResults = null;
    this.mobileToggleBtn = null;
    this.mobileCloseBtn = null;

    this.isMobileOpen = false;
    this.searchTimeout = null;
    this.abortController = null;
    this.currentResults = [];
    this.selectedIndex = -1;
    this.activeSource = 'header'; // 'header' o 'mobile'
  }

  /**
   * Inicializa ambos buscadores (Header en Desktop + Control en Móvil)
   */
  init() {
    if (!this.map) {
      console.warn('PlacesSearchControl: Mapa no disponible para inicializar el buscador.');
      return;
    }

    // 1. Inicializar buscador de escritorio (Header)
    this._initHeaderSearch();

    // 2. Inicializar control flotante de mapa (Móvil)
    this._initMobileMapControl();
  }

  /**
   * Inicializa la barra de búsqueda del Header (Desktop / iPad)
   */
  _initHeaderSearch() {
    if (!this.headerInput) return;

    this.headerInput.addEventListener('input', (e) => {
      const q = e.target.value.trim();
      this.activeSource = 'header';
      if (this.headerClearBtn) {
        this.headerClearBtn.style.display = q.length > 0 ? 'flex' : 'none';
      }

      if (this.searchTimeout) clearTimeout(this.searchTimeout);

      if (q.length < 2) {
        this._hideHeaderDropdown();
        this._setHeaderLoading(false);
        return;
      }

      this._setHeaderLoading(true);
      this.searchTimeout = setTimeout(() => {
        this.performSearch(q, 'header');
      }, 180);
    });

    if (this.headerClearBtn) {
      this.headerClearBtn.addEventListener('click', () => {
        this.headerInput.value = '';
        this.headerClearBtn.style.display = 'none';
        this._hideHeaderDropdown();
        this.headerInput.focus();
      });
    }

    this.headerInput.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') {
        this._hideHeaderDropdown();
        this.headerInput.blur();
        return;
      }
      this._handleKeyboardNavigation(e, 'header');
    });

    // Cerrar dropdown al hacer clic fuera
    document.addEventListener('click', (e) => {
      if (this.headerContainer && !this.headerContainer.contains(e.target)) {
        this._hideHeaderDropdown();
      }
    });
  }

  /**
   * Inicializa el control de mapa Leaflet para vista móvil
   */
  _initMobileMapControl() {
    const self = this;
    const SearchControlClass = L.Control.extend({
      options: {
        position: 'topright'
      },
      onAdd: function() {
        return self._createMobileDOM();
      }
    });

    this.mobileControl = new SearchControlClass();
    this.mobileControl.addTo(this.map);

    // Reordenar control justo antes del zoom
    if (this.mobileContainer && this.mobileContainer.parentNode) {
      const zoomControl = this.mobileContainer.parentNode.querySelector('.leaflet-control-zoom');
      if (zoomControl) {
        this.mobileContainer.parentNode.insertBefore(this.mobileContainer, zoomControl);
      }
    }

    this._bindMobileEvents();
  }

  _createMobileDOM() {
    this.mobileContainer = L.DomUtil.create('div', 'leaflet-control-places-search mobile-search-control');
    L.DomEvent.disableClickPropagation(this.mobileContainer);
    L.DomEvent.disableScrollPropagation(this.mobileContainer);

    this.mobileContainer.innerHTML = `
      <div class="places-search-box" id="mobile-places-search-box">
        <button type="button" class="places-search-toggle" id="btn-mobile-search-toggle" title="Buscar localidad, río, embalse..." aria-label="Abrir buscador">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
            <circle cx="11" cy="11" r="8"></circle>
            <line x1="21" y1="21" x2="16.65" y2="16.65"></line>
          </svg>
        </button>

        <div class="places-search-input-wrapper">
          <div class="places-search-leading-icon" aria-hidden="true">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
              <circle cx="11" cy="11" r="8"></circle>
              <line x1="21" y1="21" x2="16.65" y2="16.65"></line>
            </svg>
          </div>
          <input 
            type="text" 
            id="mobile-places-search-input" 
            class="places-search-input" 
            placeholder="Buscar municipio, río, embalse, comarca..." 
            autocomplete="off" 
            spellcheck="false"
            aria-label="Buscar lugar"
          >
          <div class="places-search-spinner" id="mobile-places-search-spinner" style="display: none;">
            <div class="places-spinner-dot"></div>
          </div>
          <button type="button" class="places-search-clear" id="btn-mobile-search-clear" style="display: none;" title="Borrar texto">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
              <line x1="18" y1="6" x2="6" y2="18"></line>
              <line x1="6" y1="6" x2="18" y2="18"></line>
            </svg>
          </button>
          <button type="button" class="places-search-close" id="btn-mobile-search-close" title="Cerrar buscador">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
              <line x1="18" y1="6" x2="6" y2="18"></line>
              <line x1="6" y1="6" x2="18" y2="18"></line>
            </svg>
          </button>
        </div>
      </div>

      <div class="places-search-dropdown mobile-places-dropdown" id="mobile-places-search-dropdown" style="display: none;" role="listbox">
        <div class="places-search-results" id="mobile-places-search-results"></div>
      </div>
    `;

    this.mobileToggleBtn = this.mobileContainer.querySelector('#btn-mobile-search-toggle');
    this.mobileInput = this.mobileContainer.querySelector('#mobile-places-search-input');
    this.mobileClearBtn = this.mobileContainer.querySelector('#btn-mobile-search-clear');
    this.mobileCloseBtn = this.mobileContainer.querySelector('#btn-mobile-search-close');
    this.mobileSpinner = this.mobileContainer.querySelector('#mobile-places-search-spinner');
    this.mobileDropdown = this.mobileContainer.querySelector('#mobile-places-search-dropdown');
    this.mobileResults = this.mobileContainer.querySelector('#mobile-places-search-results');

    return this.mobileContainer;
  }

  _bindMobileEvents() {
    if (!this.mobileToggleBtn || !this.mobileInput) return;

    this.mobileToggleBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      this.setMobileOpen(true);
    });

    this.mobileCloseBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      this.setMobileOpen(false);
    });

    this.mobileClearBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      this.mobileInput.value = '';
      this.mobileClearBtn.style.display = 'none';
      this._hideMobileDropdown();
      this.mobileInput.focus();
    });

    this.mobileInput.addEventListener('input', (e) => {
      const q = e.target.value.trim();
      this.activeSource = 'mobile';
      this.mobileClearBtn.style.display = q.length > 0 ? 'flex' : 'none';

      if (this.searchTimeout) clearTimeout(this.searchTimeout);

      if (q.length < 2) {
        this._hideMobileDropdown();
        this._setMobileLoading(false);
        return;
      }

      this._setMobileLoading(true);
      this.searchTimeout = setTimeout(() => {
        this.performSearch(q, 'mobile');
      }, 180);
    });

    this.mobileInput.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') {
        this.setMobileOpen(false);
        return;
      }
      this._handleKeyboardNavigation(e, 'mobile');
    });

    document.addEventListener('click', (e) => {
      if (this.mobileContainer && !this.mobileContainer.contains(e.target)) {
        if (this.mobileInput && this.mobileInput.value.trim() === '') {
          this.setMobileOpen(false);
        } else {
          this._hideMobileDropdown();
        }
      }
    });
  }

  setMobileOpen(open) {
    this.isMobileOpen = open;
    if (this.mobileContainer) {
      if (open) {
        this.mobileContainer.classList.add('is-expanded');
        document.body.classList.add('rainloc-mobile-search-active');
        setTimeout(() => this.mobileInput.focus(), 80);
      } else {
        this.mobileContainer.classList.remove('is-expanded');
        document.body.classList.remove('rainloc-mobile-search-active');
        this._hideMobileDropdown();
        if (this.mobileInput) this.mobileInput.blur();
      }
    }
  }

  _setHeaderLoading(loading) {
    if (this.headerSpinner) this.headerSpinner.style.display = loading ? 'flex' : 'none';
  }

  _setMobileLoading(loading) {
    if (this.mobileSpinner) this.mobileSpinner.style.display = loading ? 'flex' : 'none';
  }

  _hideHeaderDropdown() {
    if (this.headerDropdown) this.headerDropdown.style.display = 'none';
    this.selectedIndex = -1;
  }

  _hideMobileDropdown() {
    if (this.mobileDropdown) this.mobileDropdown.style.display = 'none';
    this.selectedIndex = -1;
  }

  /**
   * Petición de búsqueda a la API
   */
  async performSearch(query, source) {
    if (this.abortController) {
      this.abortController.abort();
    }
    this.abortController = new AbortController();

    const url = `${CONFIG.apiBaseUrl}/search?q=${encodeURIComponent(query)}&limit=12`;

    try {
      const response = await fetch(url, { signal: this.abortController.signal });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();

      this._setHeaderLoading(false);
      this._setMobileLoading(false);

      this.currentResults = data.results || [];
      this.selectedIndex = -1;
      this.renderResults(query, this.currentResults, source);
    } catch (err) {
      if (err.name === 'AbortError') return;
      console.error('Error buscando lugares:', err);
      this._setHeaderLoading(false);
      this._setMobileLoading(false);
      this.renderError('Error conectando con el servidor de búsqueda.', source);
    }
  }

  /**
   * Renderiza los resultados en el contenedor adecuado (Header o Mobile)
   */
  renderResults(query, results, source) {
    const resultsContainer = (source === 'header') ? this.headerResults : this.mobileResults;
    const dropdownContainer = (source === 'header') ? this.headerDropdown : this.mobileDropdown;

    if (!resultsContainer || !dropdownContainer) return;

    if (!results || results.length === 0) {
      resultsContainer.innerHTML = `
        <div class="places-no-results">
          <div class="places-no-results-icon">🔍</div>
          <div class="places-no-results-text">No se encontraron lugares para <strong>"${this._escapeHtml(query)}"</strong></div>
          <div class="places-no-results-hint">Prueba con otro municipio, río, embalse o comarca</div>
        </div>
      `;
      dropdownContainer.style.display = 'block';
      return;
    }

    const html = results.map((item, index) => {
      const highlightedName = this._highlightMatch(item.name, query);
      const subInfo = [item.province || item.community, item.alt_name].filter(Boolean).join(' • ');

      return `
        <div class="places-result-item" data-index="${index}" role="option" tabindex="-1">
          <div class="places-item-icon" aria-hidden="true">${item.icon || '📍'}</div>
          <div class="places-item-body">
            <div class="places-item-title-row">
              <span class="places-item-title">${highlightedName}</span>
              <span class="places-item-badge places-badge-${item.category}">${item.category_label}</span>
            </div>
            ${subInfo ? `<div class="places-item-subtitle">${this._escapeHtml(subInfo)}</div>` : ''}
          </div>
        </div>
      `;
    }).join('');

    resultsContainer.innerHTML = html;
    dropdownContainer.style.display = 'block';

    const items = resultsContainer.querySelectorAll('.places-result-item');
    items.forEach((el) => {
      el.addEventListener('click', (e) => {
        e.stopPropagation();
        const idx = parseInt(el.getAttribute('data-index'), 10);
        if (this.currentResults[idx]) {
          this.selectPlace(this.currentResults[idx], source);
        }
      });

      el.addEventListener('mouseenter', () => {
        const idx = parseInt(el.getAttribute('data-index'), 10);
        this.setSelectedIndex(idx, false, source);
      });
    });
  }

  renderError(msg, source) {
    const resultsContainer = (source === 'header') ? this.headerResults : this.mobileResults;
    const dropdownContainer = (source === 'header') ? this.headerDropdown : this.mobileDropdown;
    if (!resultsContainer || !dropdownContainer) return;

    resultsContainer.innerHTML = `
      <div class="places-no-results places-error">
        <div class="places-no-results-text">${this._escapeHtml(msg)}</div>
      </div>
    `;
    dropdownContainer.style.display = 'block';
  }

  _handleKeyboardNavigation(e, source) {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      this.navigateResults(1, source);
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      this.navigateResults(-1, source);
    } else if (e.key === 'Enter') {
      e.preventDefault();
      if (this.selectedIndex >= 0 && this.selectedIndex < this.currentResults.length) {
        this.selectPlace(this.currentResults[this.selectedIndex], source);
      } else if (this.currentResults.length > 0) {
        this.selectPlace(this.currentResults[0], source);
      }
    }
  }

  navigateResults(direction, source) {
    if (!this.currentResults.length) return;
    let nextIndex = this.selectedIndex + direction;
    if (nextIndex < 0) nextIndex = this.currentResults.length - 1;
    if (nextIndex >= this.currentResults.length) nextIndex = 0;
    this.setSelectedIndex(nextIndex, true, source);
  }

  setSelectedIndex(index, scrollIntoView, source) {
    this.selectedIndex = index;
    const resultsContainer = (source === 'header') ? this.headerResults : this.mobileResults;
    const items = resultsContainer ? resultsContainer.querySelectorAll('.places-result-item') : [];
    items.forEach((el, i) => {
      if (i === index) {
        el.classList.add('is-active');
        if (scrollIntoView) {
          el.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
        }
      } else {
        el.classList.remove('is-active');
      }
    });
  }

  /**
   * Al seleccionar un lugar: vuela al punto en el mapa y añade marcador destacado
   */
  selectPlace(place, source) {
    if (!place || !this.map) return;

    const lat = place.lat;
    const lon = place.lon;
    const targetZoom = Math.max(place.zoom || 13, this.map.getZoom());

    // 1. Ocultar dropdowns y sincronizar input
    this._hideHeaderDropdown();
    this._hideMobileDropdown();

    if (this.headerInput) this.headerInput.value = place.name;
    if (this.mobileInput) this.mobileInput.value = place.name;

    if (source === 'mobile') {
      this.setMobileOpen(false);
    }

    // 2. Volar suavemente al punto
    this.map.flyTo([lat, lon], targetZoom, {
      duration: 1.2,
      easeLinearity: 0.25
    });

    // 3. Crear marcador temporal de resalte
    this._showHighlightMarker(place);
  }

  _showHighlightMarker(place) {
    if (this.activeMarker) {
      this.map.removeLayer(this.activeMarker);
      this.activeMarker = null;
    }

    const customIcon = L.divIcon({
      className: 'places-highlight-pin-container',
      html: `
        <div class="places-pulse-ring"></div>
        <div class="places-pin-core">
          <span class="places-pin-icon">${place.icon || '📍'}</span>
        </div>
      `,
      iconSize: [36, 36],
      iconAnchor: [18, 18],
      popupAnchor: [0, -22]
    });

    const marker = L.marker([place.lat, place.lon], {
      icon: customIcon,
      zIndexOffset: 1000
    }).addTo(this.map);

    const subInfo = [place.province, place.community].filter(Boolean).join(', ');
    const popupContent = `
      <div class="places-popup-card">
        <div class="places-popup-header">
          <span class="places-popup-badge places-badge-${place.category}">${place.category_label}</span>
          <button class="places-popup-close-btn" onclick="window.__rainlocCloseSearchMarker()" title="Cerrar">✕</button>
        </div>
        <div class="places-popup-title">${this._escapeHtml(place.name)}</div>
        ${place.alt_name ? `<div class="places-popup-alt">${this._escapeHtml(place.alt_name)}</div>` : ''}
        ${subInfo ? `<div class="places-popup-location">${this._escapeHtml(subInfo)}</div>` : ''}
        <div class="places-popup-coords">Lat: ${place.lat.toFixed(4)} &bull; Lon: ${place.lon.toFixed(4)}</div>
      </div>
    `;

    marker.bindPopup(popupContent, {
      className: 'places-custom-popup',
      closeButton: false,
      autoPan: true,
      autoPanPadding: [50, 50]
    }).openPopup();

    window.__rainlocCloseSearchMarker = () => {
      if (this.activeMarker) {
        this.map.removeLayer(this.activeMarker);
        this.activeMarker = null;
      }
    };

    this.activeMarker = marker;
  }

  _escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
  }

  _highlightMatch(text, query) {
    if (!text || !query) return this._escapeHtml(text);
    const escapedQ = query.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const regex = new RegExp(`(${escapedQ})`, 'gi');
    return this._escapeHtml(text).replace(regex, '<mark class="places-highlight">$1</mark>');
  }
}
