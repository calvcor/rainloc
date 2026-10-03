/**
 * RainLoc - Buscador Geográfico de Lugares y Toponimia
 * Control de mapa Leaflet con autocompletado en tiempo real y soporte para
 * municipios, comarcas, cuencas, ríos, barrancos, embalses, cimas y estaciones.
 */

import { CONFIG } from './config.js';

export class PlacesSearchControl {
  constructor(mapManager) {
    this.mapManager = mapManager;
    this.map = mapManager ? mapManager.map : null;
    this.control = null;
    this.container = null;
    this.inputEl = null;
    this.resultsEl = null;
    this.clearBtn = null;
    this.spinnerEl = null;
    this.isOpen = false;
    this.searchTimeout = null;
    this.abortController = null;
    this.currentResults = [];
    this.selectedIndex = -1;
    this.activeMarker = null;
  }

  /**
   * Inicializa el control y lo añade al mapa
   */
  init() {
    if (!this.map) {
      console.warn('PlacesSearchControl: Mapa no disponible para inicializar el buscador.');
      return;
    }

    const self = this;
    const SearchControlClass = L.Control.extend({
      options: {
        position: 'topright'
      },
      onAdd: function() {
        return self._createDOM();
      }
    });

    this.control = new SearchControlClass();
    this.control.addTo(this.map);

    // Mover el control justo antes del control de zoom para que quede ordenado arriba
    if (this.container && this.container.parentNode) {
      const zoomControl = this.container.parentNode.querySelector('.leaflet-control-zoom');
      if (zoomControl) {
        this.container.parentNode.insertBefore(this.container, zoomControl);
      }
    }

    this._bindEvents();
  }

  /**
   * Genera el árbol DOM del buscador
   */
  _createDOM() {
    this.container = L.DomUtil.create('div', 'leaflet-control-places-search');
    // Prevenir que los clics o scrolls en el buscador afecten al mapa
    L.DomEvent.disableClickPropagation(this.container);
    L.DomEvent.disableScrollPropagation(this.container);

    this.container.innerHTML = `
      <div class="places-search-box" id="places-search-box">
        <button type="button" class="places-search-toggle" id="btn-places-search-toggle" title="Buscar localidad, río, embalse o estación..." aria-label="Abrir buscador">
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
            id="places-search-input" 
            class="places-search-input" 
            placeholder="Buscar municipio, río, embalse, comarca..." 
            autocomplete="off" 
            spellcheck="false"
            aria-label="Buscar lugar"
          >
          <div class="places-search-spinner" id="places-search-spinner" style="display: none;" aria-label="Buscando...">
            <div class="places-spinner-dot"></div>
          </div>
          <button type="button" class="places-search-clear" id="btn-places-search-clear" style="display: none;" title="Borrar texto" aria-label="Borrar texto">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
              <line x1="18" y1="6" x2="6" y2="18"></line>
              <line x1="6" y1="6" x2="18" y2="18"></line>
            </svg>
          </button>
          <button type="button" class="places-search-close" id="btn-places-search-close" title="Cerrar buscador" aria-label="Cerrar buscador">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
              <polyline points="9 18 15 12 9 6"></polyline>
            </svg>
          </button>
        </div>
      </div>

      <div class="places-search-dropdown" id="places-search-dropdown" style="display: none;" role="listbox">
        <div class="places-search-results" id="places-search-results"></div>
      </div>
    `;

    this.toggleBtn = this.container.querySelector('#btn-places-search-toggle');
    this.searchBox = this.container.querySelector('#places-search-box');
    this.inputEl = this.container.querySelector('#places-search-input');
    this.clearBtn = this.container.querySelector('#btn-places-search-clear');
    this.closeBtn = this.container.querySelector('#btn-places-search-close');
    this.spinnerEl = this.container.querySelector('#places-search-spinner');
    this.dropdownEl = this.container.querySelector('#places-search-dropdown');
    this.resultsEl = this.container.querySelector('#places-search-results');

    return this.container;
  }

  /**
   * Enlaza eventos de usuario (teclado, clics, input)
   */
  _bindEvents() {
    if (!this.toggleBtn || !this.inputEl) return;

    // Toggle abrir/cerrar
    this.toggleBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      this.toggleOpen();
    });

    this.closeBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      this.setOpen(false);
    });

    this.clearBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      this.inputEl.value = '';
      this.clearBtn.style.display = 'none';
      this.hideDropdown();
      this.inputEl.focus();
    });

    // Input con debounce de 180ms
    this.inputEl.addEventListener('input', (e) => {
      const q = e.target.value.trim();
      this.clearBtn.style.display = q.length > 0 ? 'flex' : 'none';

      if (this.searchTimeout) {
        clearTimeout(this.searchTimeout);
      }

      if (q.length < 2) {
        this.hideDropdown();
        this.setLoading(false);
        return;
      }

      this.setLoading(true);
      this.searchTimeout = setTimeout(() => {
        this.performSearch(q);
      }, 180);
    });

    // Navegación por teclado en resultados (Flechas, Enter, Esc)
    this.inputEl.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') {
        this.setOpen(false);
        return;
      }

      if (e.key === 'ArrowDown') {
        e.preventDefault();
        this.navigateResults(1);
      } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        this.navigateResults(-1);
      } else if (e.key === 'Enter') {
        e.preventDefault();
        if (this.selectedIndex >= 0 && this.selectedIndex < this.currentResults.length) {
          this.selectPlace(this.currentResults[this.selectedIndex]);
        } else if (this.currentResults.length > 0) {
          this.selectPlace(this.currentResults[0]);
        }
      }
    });

    // Cerrar al hacer clic fuera
    document.addEventListener('click', (e) => {
      if (!this.container.contains(e.target)) {
        this.hideDropdown();
        if (this.inputEl && this.inputEl.value.trim() === '') {
          this.setOpen(false);
        }
      }
    });
  }

  toggleOpen() {
    this.setOpen(!this.isOpen);
  }

  setOpen(open) {
    this.isOpen = open;
    if (this.container) {
      if (open) {
        this.container.classList.add('is-expanded');
        setTimeout(() => this.inputEl.focus(), 80);
      } else {
        this.container.classList.remove('is-expanded');
        this.hideDropdown();
        if (this.inputEl) this.inputEl.blur();
      }
    }
  }

  setLoading(loading) {
    if (this.spinnerEl) {
      this.spinnerEl.style.display = loading ? 'flex' : 'none';
    }
  }

  /**
   * Ejecuta la petición de búsqueda a la API de FastAPI
   */
  async performSearch(query) {
    if (this.abortController) {
      this.abortController.abort();
    }
    this.abortController = new AbortController();

    const url = `${CONFIG.apiBaseUrl}/search?q=${encodeURIComponent(query)}&limit=10`;

    try {
      const response = await fetch(url, { signal: this.abortController.signal });
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }
      const data = await response.json();
      this.setLoading(false);
      this.currentResults = data.results || [];
      this.selectedIndex = -1;
      this.renderResults(query, this.currentResults);
    } catch (err) {
      if (err.name === 'AbortError') return;
      console.error('Error buscando lugares:', err);
      this.setLoading(false);
      this.renderError('Error conectando con el servidor de búsqueda.');
    }
  }

  /**
   * Renderiza el listado de resultados con badges y resaltado
   */
  renderResults(query, results) {
    if (!this.resultsEl || !this.dropdownEl) return;

    if (!results || results.length === 0) {
      this.resultsEl.innerHTML = `
        <div class="places-no-results">
          <div class="places-no-results-icon">🔍</div>
          <div class="places-no-results-text">No se encontraron lugares para <strong>"${this._escapeHtml(query)}"</strong></div>
          <div class="places-no-results-hint">Prueba con otro municipio, río, embalse o comarca</div>
        </div>
      `;
      this.dropdownEl.style.display = 'block';
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

    this.resultsEl.innerHTML = html;
    this.dropdownEl.style.display = 'block';

    // Eventos de clic y hover en cada elemento
    const items = this.resultsEl.querySelectorAll('.places-result-item');
    items.forEach((el) => {
      el.addEventListener('click', (e) => {
        e.stopPropagation();
        const idx = parseInt(el.getAttribute('data-index'), 10);
        if (this.currentResults[idx]) {
          this.selectPlace(this.currentResults[idx]);
        }
      });

      el.addEventListener('mouseenter', () => {
        const idx = parseInt(el.getAttribute('data-index'), 10);
        this.setSelectedIndex(idx, false);
      });
    });
  }

  renderError(msg) {
    if (!this.resultsEl || !this.dropdownEl) return;
    this.resultsEl.innerHTML = `
      <div class="places-no-results places-error">
        <div class="places-no-results-text">${this._escapeHtml(msg)}</div>
      </div>
    `;
    this.dropdownEl.style.display = 'block';
  }

  hideDropdown() {
    if (this.dropdownEl) {
      this.dropdownEl.style.display = 'none';
    }
    this.selectedIndex = -1;
  }

  navigateResults(direction) {
    if (!this.currentResults.length) return;
    let nextIndex = this.selectedIndex + direction;
    if (nextIndex < 0) nextIndex = this.currentResults.length - 1;
    if (nextIndex >= this.currentResults.length) nextIndex = 0;
    this.setSelectedIndex(nextIndex, true);
  }

  setSelectedIndex(index, scrollIntoView) {
    this.selectedIndex = index;
    const items = this.resultsEl ? this.resultsEl.querySelectorAll('.places-result-item') : [];
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
  selectPlace(place) {
    if (!place || !this.map) return;

    const lat = place.lat;
    const lon = place.lon;
    const targetZoom = Math.max(place.zoom || 13, this.map.getZoom());

    // 1. Ocultar dropdown y actualizar texto de búsqueda
    this.hideDropdown();
    if (this.inputEl) {
      this.inputEl.value = place.name;
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
    // Eliminar marcador anterior si existía
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
