/**
 * RainLoc - SmoothWheelZoomHandler
 * Proporciona un zoom fluido, continuo y amortiguado (60/120fps vía requestAnimationFrame)
 * para trackpads (gesto de dos dedos/pinch) y ruedas de ratón.
 * Elimina los saltos escalonados enteros de Leaflet y acompaña el movimiento de los dedos.
 */

export class SmoothWheelZoomHandler extends L.Handler {
  addHooks() {
    L.DomEvent.on(this._map._container, 'wheel', this._onWheelScroll, this);
  }

  removeHooks() {
    L.DomEvent.off(this._map._container, 'wheel', this._onWheelScroll, this);
    if (this._zoomAnimationId) {
      cancelAnimationFrame(this._zoomAnimationId);
      this._zoomAnimationId = null;
    }
    clearTimeout(this._timeoutId);
    this._isWheeling = false;
  }

  _onWheelScroll(e) {
    if (!this._isWheeling) {
      this._onWheelStart(e);
    }
    this._onWheeling(e);
  }

  _onWheelStart(e) {
    const map = this._map;
    this._isWheeling = true;
    this._wheelMousePosition = map.mouseEventToContainerPoint(e);
    this._centerPoint = map.getSize().divideBy(2);
    this._startLatLng = map.containerPointToLatLng(this._centerPoint);
    this._wheelMouseLatLng = map.containerPointToLatLng(this._wheelMousePosition);
    this._startZoom = map.getZoom();
    this._moved = false;
    this._zooming = true;

    map._stop();
    if (map._panAnim) map._panAnim.stop();

    this._goalZoom = map.getZoom();
    this._prevCenter = map.getCenter();
    this._prevZoom = map.getZoom();

    this._zoomAnimationId = requestAnimationFrame(this._updateWheelZoom.bind(this));
  }

  _onWheeling(e) {
    const map = this._map;
    // Si el usuario hace gesto de pellizco con dos dedos en trackpad (Mac / Windows precision),
    // el navegador emite eventos wheel con e.ctrlKey = true
    const isPinch = Boolean(e.ctrlKey);
    const delta = L.DomEvent.getWheelDelta(e);
    const sensitivity = map.options.smoothSensitivity || 1.0;

    // Sensibilidad diferenciada: trackpad pinch (movimiento directo de dedos) vs scroll continuo
    const stepFactor = isPinch ? 0.005 : 0.0028;

    this._goalZoom = this._goalZoom + delta * stepFactor * sensitivity;
    if (this._goalZoom < map.getMinZoom() || this._goalZoom > map.getMaxZoom()) {
      this._goalZoom = map._limitZoom(this._goalZoom);
    }

    this._wheelMousePosition = map.mouseEventToContainerPoint(e);
    this._wheelMouseLatLng = map.containerPointToLatLng(this._wheelMousePosition);

    clearTimeout(this._timeoutId);
    this._timeoutId = setTimeout(this._onWheelEnd.bind(this), 160);

    L.DomEvent.preventDefault(e);
    L.DomEvent.stopPropagation(e);
  }

  _onWheelEnd() {
    this._isWheeling = false;
    cancelAnimationFrame(this._zoomAnimationId);
    this._zoomAnimationId = null;
    if (this._moved) {
      this._map._moveEnd(true);
      this._moved = false;
    }
  }

  _updateWheelZoom() {
    const map = this._map;

    if (!map.getCenter().equals(this._prevCenter) || map.getZoom() !== this._prevZoom) {
      return;
    }

    const currentZoom = map.getZoom();
    const diff = this._goalZoom - currentZoom;

    if (Math.abs(diff) < 0.001) {
      this._zoom = this._goalZoom;
      if (!this._isWheeling) {
        cancelAnimationFrame(this._zoomAnimationId);
        this._zoomAnimationId = null;
        if (this._moved) {
          map._moveEnd(true);
          this._moved = false;
        }
        return;
      }
    } else {
      // Interpolación suave amortiguada hacia el zoom objetivo (easing exponencial 60/120Hz)
      this._zoom = currentZoom + diff * 0.28;
    }

    const delta = this._wheelMousePosition.subtract(this._centerPoint);

    if (map.options.smoothWheelZoom === 'center') {
      this._center = this._startLatLng;
    } else {
      this._center = map.unproject(map.project(this._wheelMouseLatLng, this._zoom).subtract(delta), this._zoom);
    }

    if (!this._moved) {
      map._moveStart(true, false);
      this._moved = true;
    }

    map._move(this._center, this._zoom);
    this._prevCenter = map.getCenter();
    this._prevZoom = map.getZoom();

    this._zoomAnimationId = requestAnimationFrame(this._updateWheelZoom.bind(this));
  }
}

// Registrar handler en Leaflet
if (typeof L !== 'undefined' && L.Map) {
  L.Map.addInitHook('addHandler', 'smoothWheelZoom', SmoothWheelZoomHandler);
}
