/**
 * RainLoc - Inspector Multi-Capa en Hover
 * Detecta simultáneamente todas las capas activas bajo el cursor
 * y genera un tooltip unificado consolidado por secciones.
 */

import { CONFIG } from "./config.js";
import { StorageManager } from "./storage.js";

export class MultiLayerInspector {
  constructor(mapManager, cuencasLayer, layerManager, uiManager) {
    this.mapManager = mapManager;
    this.map = mapManager.map;
    this.cuencasLayer = cuencasLayer;
    this.layerManager = layerManager;
    this.uiManager = uiManager;

    // Instancia única de tooltip flotante global
    this.unifiedTooltip = L.tooltip({
      sticky: false,
      direction: "auto",
      className: "rainloc-unified-tooltip",
      offset: [15, 10],
      opacity: 0.98
    });

    this._lastLatLng = null;
    this._hoverThrottle = null;
    this._currentHoveredCuenca = null;

    // Cache y debounce para consultas puntuales de reflectividad dBZ
    this._lastRadarLookup = null;
    this._dbzDebounceTimer = null;
  }

  /**
   * Obtiene la reflectividad en dBZ instantáneamente (0ms) a partir del pixel en el canvas de la imagen raster
   */
  _getInstantRadarPixel(lat, lng) {
    if (!this.layerManager || !this.layerManager.radarCanvasData) return null;
    const { ctx, width, height, bounds } = this.layerManager.radarCanvasData;
    if (!bounds || bounds.length < 2) return null;

    const latMin = Math.min(bounds[0][0], bounds[1][0]);
    const latMax = Math.max(bounds[0][0], bounds[1][0]);
    const lonMin = Math.min(bounds[0][1], bounds[1][1]);
    const lonMax = Math.max(bounds[0][1], bounds[1][1]);

    if (lat < latMin || lat > latMax || lng < lonMin || lng > lonMax) return null;

    // Coordenadas normalizadas a píxeles usando proyección Web Mercator idéntica a Leaflet ImageOverlay
    const yPt = Math.log(Math.tan(Math.PI / 4 + (lat * Math.PI / 360)));
    const yMin = Math.log(Math.tan(Math.PI / 4 + (latMin * Math.PI / 360)));
    const yMax = Math.log(Math.tan(Math.PI / 4 + (latMax * Math.PI / 360)));

    const x = Math.floor(((lng - lonMin) / (lonMax - lonMin)) * width);
    const y = Math.floor(((yMax - yPt) / (yMax - yMin)) * height);

    if (x < 0 || x >= width || y < 0 || y >= height) return null;

    try {
      const pixel = ctx.getImageData(x, y, 1, 1).data;
      const r = pixel[0], g = pixel[1], b = pixel[2], a = pixel[3];

      // Si el píxel es transparente o sin reflectividad (<30 opacidad), no hay precipitación
      if (a < 30) return null;

      // Paleta meteorológica oficial de RainLoc (valores dBZ, etiquetas y colores exactos)
      const palette = [
        { minDbz: 55, dbz: 55, label: 'Muy Fuerte / Granizo', rgb: [239, 68, 68], color: '#ef4444' },
        { minDbz: 45, dbz: 45, label: 'Muy Fuerte', rgb: [249, 115, 22], color: '#f97316' },
        { minDbz: 35, dbz: 35, label: 'Fuerte', rgb: [234, 179, 8], color: '#eab308' },
        { minDbz: 25, dbz: 25, label: 'Moderada', rgb: [34, 197, 94], color: '#22c55e' },
        { minDbz: 15, dbz: 15, label: 'Ligera', rgb: [2, 132, 199], color: '#0284c7' },
        { minDbz: 8,  dbz: 10, label: 'Débil', rgb: [56, 189, 248], color: '#38bdf8' }
      ];

      let bestMatch = palette[palette.length - 1];
      let minDistance = Infinity;

      for (const p of palette) {
        const dr = r - p.rgb[0];
        const dg = g - p.rgb[1];
        const db = b - p.rgb[2];
        const dist = dr * dr + dg * dg + db * db;
        if (dist < minDistance) {
          minDistance = dist;
          bestMatch = p;
        }
      }

      return {
        dbz: bestMatch.dbz,
        badgeColor: bestMatch.color,
        rain_intensity: bestMatch.label
      };

    } catch (e) {
      return null;
    }
  }

  _debouncedFetchDbz(lat, lng, mode, stationId) {
    if (this._dbzDebounceTimer) clearTimeout(this._dbzDebounceTimer);
    this._dbzDebounceTimer = setTimeout(async () => {
      try {
        const url = `${CONFIG.apiBaseUrl}/radar/value-at?lat=${lat.toFixed(4)}&lon=${lng.toFixed(4)}&mode=${mode}&station_id=${stationId || ''}`;
        const resp = await fetch(url);
        if (resp.ok) {
          const data = await resp.json();
          if (data.dbz !== null && data.dbz !== undefined) {
            this._lastRadarLookup = { lat, lng, dbz: data.dbz, rain_intensity: data.rain_intensity };
            // Forzar refresco inmediato si el cursor sigue en la misma celda (< 0.005° ~ 500m)
            if (this._lastLatLng && Math.abs(this._lastLatLng.lat - lat) < 0.005 && Math.abs(this._lastLatLng.lng - lng) < 0.005) {
              this._inspectPoint(this._lastLatLng);
            }
          }
        }
      } catch (e) {
        // Silencioso en caso de error de red
      }
    }, 40);
  }





  init() {
    if (!this.map) return;

    // Escuchar movimiento del cursor global sobre el mapa
    this.map.on("mousemove", (e) => this._onMouseMove(e));
    this.map.on("mouseout", () => this._onMouseOut());
  }

  setCuencasLayer(cuencasLayer) {
    this.cuencasLayer = cuencasLayer;
  }

  setLayerManager(layerManager) {
    this.layerManager = layerManager;
  }

  _onMouseMove(e) {
    this._lastLatLng = e.latlng;

    // Throttle ligero (16ms ~ 60fps) para máxima suavidad sin sobrecargar la CPU
    if (this._hoverThrottle) return;
    this._hoverThrottle = requestAnimationFrame(() => {
      this._inspectPoint(this._lastLatLng);
      this._hoverThrottle = null;
    });
  }

  _onMouseOut() {
    this._closeTooltip();
    if (this.uiManager) {
      this.uiManager.resetHoverInfo();
    }
    this._clearCuencaHover();
  }

  /**
   * Realiza la intersección geométrica del punto con todas las capas activas
   */
  _inspectPoint(latlng) {
    if (!latlng || !this.map) return;

    const lat = latlng.lat;
    const lng = latlng.lng;
    const sections = [];

    // 1. Inspeccionar Capa de Cuencas (si está visible)
    let cuencaFound = null;
    if (this.cuencasLayer && this.cuencasLayer.isVisible && this.cuencasLayer.geoJsonLayer) {
      this.cuencasLayer.geoJsonLayer.eachLayer((layer) => {
        if (cuencaFound) return;
        const feature = layer.feature;
        if (feature && feature.geometry && this._isPointInGeometry(lat, lng, feature.geometry)) {
          cuencaFound = { feature, layer };
        }
      });
    }

    if (cuencaFound) {
      const props = cuencaFound.feature.properties || {};
      const sistema = props.NomSistExp || "Demarcación CHJ";
      const subsistema = props.Subsistema || `Subsistema ${cuencaFound.feature.id || ""}`;
      const color = CONFIG.systemColors[sistema] || CONFIG.systemColors["Default"] || "#38bdf8";
      const rawSuperf = props["Superf km2"] || props["Area km2"] || props.Superficie;
      const superfText = rawSuperf ? `${Number(rawSuperf).toLocaleString("es-ES", { maximumFractionDigits: 1 })} km²` : "";

      sections.push({
        type: "cuenca",
        title: "Cuenca Hidrográfica",
        headerColor: color,
        icon: "🌊",
        name: subsistema,
        badge: sistema,
        badgeBg: color,
        details: superfText ? `Superficie: ${superfText}` : null
      });

      // Actualizar estilo hover de la cuenca
      this._applyCuencaHover(cuencaFound.layer, color);

      // Notificar al HUD inferior
      if (this.uiManager) {
        this.uiManager.updateHoverInfo(props);
      }
    } else {
      this._clearCuencaHover();
      if (this.uiManager) {
        this.uiManager.resetHoverInfo();
      }
    }

    // 2. Inspeccionar Capas Temáticas Activas (AEMET, Radar, Acumulados, Modelos)
    if (this.layerManager) {
      const activeStates = this.layerManager.layerStates;

      // 2.1 Avisos AEMET
      if (activeStates["aemet_warnings"] && activeStates["aemet_warnings"].active) {
        const aemetGroup = this.layerManager.layers["aemet_warnings"];
        if (aemetGroup) {
          const matchingWarnings = [];
          aemetGroup.eachLayer((child) => {
            // child puede ser L.GeoJSON
            if (child.eachLayer) {
              child.eachLayer((subLayer) => {
                const feat = subLayer.feature;
                if (feat && feat.geometry && this._isPointInGeometry(lat, lng, feat.geometry)) {
                  matchingWarnings.push(feat.properties);
                }
              });
            }
          });

          if (matchingWarnings.length > 0) {
            // Consolidar aviso de mayor nivel
            const topWarning = matchingWarnings[0];
            const event = topWarning.event || topWarning.headline || "Aviso Meteorológico";
            const sev = topWarning.severity || "Aviso";
            const color = topWarning.color || "#f59e0b";
            const expires = topWarning.expires ? new Date(topWarning.expires).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "";
            const area = topWarning.area_desc || "";

            sections.push({
              type: "warning",
              title: "AEMET Meteoalerta (Activo)",
              headerColor: color,
              icon: "⚠️",
              name: event,
              badge: sev,
              badgeBg: color,
              badgeColor: "#000",
              details: `📍 ${area}${expires ? " · Hasta " + expires : ""}`
            });
          }
        }
      }

      // 2.2 Radar Meteorológico
      if (activeStates["radar"] && activeStates["radar"].active) {
        const bounds = this.layerManager.currentRadarBounds;

        let inRadarArea = false;
        if (bounds) {
          const latMin = Math.min(bounds[0][0], bounds[1][0]);
          const latMax = Math.max(bounds[0][0], bounds[1][0]);
          const lonMin = Math.min(bounds[0][1], bounds[1][1]);
          const lonMax = Math.max(bounds[0][1], bounds[1][1]);
          if (lat >= latMin && lat <= latMax && lng >= lonMin && lng <= lonMax) {
            inRadarArea = true;
          }
        }

        if (inRadarArea) {
          // 1. Detección instantánea en el cliente desde el raster canvas (0ms de latencia)
          // Esto garantiza que el valor y color mostrados coinciden exactamente con el píxel visual bajo el cursor
          const instantPixel = this._getInstantRadarPixel(lat, lng);

          // 2. Comprobar si hay valor float de alta precisión en caché para la misma celda de cuadrícula
          // (tolerancia fina < 0.005° ~ 500m para evitar usar valores de celdas adyacentes)
          const cached = this._lastRadarLookup &&
            Math.abs(this._lastRadarLookup.lat - lat) < 0.005 &&
            Math.abs(this._lastRadarLookup.lng - lng) < 0.005
              ? this._lastRadarLookup
              : null;

          // Si el píxel en pantalla tiene reflectividad, mostrar la sección del radar
          if (instantPixel) {
            const dbzValue = (cached && cached.dbz !== null && cached.dbz !== undefined) ? cached.dbz : instantPixel.dbz;
            const rainIntensity = (cached && cached.rain_intensity) ? cached.rain_intensity : instantPixel.rain_intensity;

            // Determinar color del badge estrictamente según el valor de dBZ o el color exacto del píxel
            let badgeColor = instantPixel.badgeColor || "#38bdf8";
            if (dbzValue >= 55) badgeColor = "#ef4444";
            else if (dbzValue >= 45) badgeColor = "#f97316";
            else if (dbzValue >= 35) badgeColor = "#eab308";
            else if (dbzValue >= 25) badgeColor = "#22c55e";
            else if (dbzValue >= 15) badgeColor = "#0284c7";
            else badgeColor = "#38bdf8";

            const rainDesc = `${rainIntensity} (${dbzValue.toFixed(0)} dBZ)`;

            sections.push({
              type: "radar",
              title: "Radar Meteorológico (España)",
              headerColor: "#38bdf8",
              icon: "📡",
              name: "Eco de Precipitación",
              badge: `${dbzValue.toFixed(0)} dBZ`,
              badgeBg: badgeColor,
              details: `Intensidad: ${rainDesc}`
            });

            // Disparar consulta de calibración fina con debounce si no está en cache
            if (!cached) {
              this._debouncedFetchDbz(lat, lng, "composite", "");
            }
          }
        }
      }




      // 2.3 Caudales en Ríos (SAIH Júcar)
      if (activeStates["saih_caudales"] && activeStates["saih_caudales"].active) {
        const caudalesGroup = this.layerManager.layers["saih_caudales"];
        if (caudalesGroup) {
          let closestStation = null;
          let minPixDist = 20; // Tolerancia de 20px en pantalla para detección precisa en hover

          caudalesGroup.eachLayer((child) => {
            const checkLayer = (l) => {
              if (l.getLatLng && l.feature && l.feature.properties) {
                const ptPix = this.map.latLngToContainerPoint(l.getLatLng());
                const mousePix = this.map.latLngToContainerPoint(latlng);
                const pixDist = Math.hypot(ptPix.x - mousePix.x, ptPix.y - mousePix.y);
                if (pixDist <= minPixDist) {
                  minPixDist = pixDist;
                  closestStation = l.feature.properties;
                }
              }
            };

            if (child.eachLayer) {
              child.eachLayer(checkLayer);
            } else {
              checkLayer(child);
            }
          });

          if (closestStation) {
            const rawCaudal = closestStation.ultimo_caudal !== undefined ? closestStation.ultimo_caudal : (closestStation.caudal !== undefined ? closestStation.caudal : closestStation.lastValue);
            const caudal = (rawCaudal !== null && rawCaudal !== undefined && rawCaudal !== '' && !isNaN(Number(rawCaudal))) ? Number(rawCaudal) : null;
            const umbrales = closestStation.umbrales || {};
            const uAmarillo = umbrales.amarillo ? Number(umbrales.amarillo) : null;
            const uNaranja = umbrales.naranja ? Number(umbrales.naranja) : null;
            const uRojo = umbrales.rojo ? Number(umbrales.rojo) : null;

            let badgeBg = "#10b981";
            let alertLevel = "Normal";
            if (caudal !== null) {
              if (uRojo !== null && caudal >= uRojo) {
                badgeBg = "#ef4444";
                alertLevel = "🔴 Umbral Rojo";
              } else if (uNaranja !== null && caudal >= uNaranja) {
                badgeBg = "#f97316";
                alertLevel = "🟠 Umbral Naranja";
              } else if (uAmarillo !== null && caudal >= uAmarillo) {
                badgeBg = "#f59e0b";
                alertLevel = "🟡 Umbral Amarillo";
              }
            } else {
              badgeBg = "#94a3b8";
              alertLevel = "Sin dato";
            }

            const caudalText = caudal !== null ? `${caudal.toFixed(2)} m³/s` : "-- m³/s";
            const horaText = closestStation.ultima_hora ? `· ${closestStation.ultima_hora}` : '';
            const thList = [];
            if (uAmarillo) thList.push(`🟡 ${uAmarillo}`);
            if (uNaranja) thList.push(`🟠 ${uNaranja}`);
            if (uRojo) thList.push(`🔴 ${uRojo}`);
            const thText = thList.length > 0 ? thList.join(' · ') + ' m³/s' : 'En estudio';
            const metaLoc = `${closestStation.poblacion || '--'} (${closestStation.provincia || ''}) · ${closestStation.subcuenca || ''}`;

            sections.push({
              type: "caudal",
              title: "Caudal en Río (SAIH)",
              headerColor: "#0284c7",
              icon: "💧",
              name: `${closestStation.nombre}`,
              badge: `${alertLevel}`,
              badgeBg: badgeBg,
              badgeColor: "#ffffff",
              details: `
                <div class="unified-caudal-block">
                  <div style="color:#cbd5e1; font-size:0.75rem;">${closestStation.variable || 'Caudal'}</div>
                  <div style="margin-top:2px; font-size:0.82rem;">Caudal: <strong style="color:${badgeBg}; font-size:1.08em;">${caudalText}</strong> <span style="color:#94a3b8; font-size:0.72rem;">${horaText}</span></div>
                  <div style="font-size:0.72rem; color:#cbd5e1; margin-top:2px;">Umbrales: <span>${thText}</span></div>
                  <div style="font-size:0.70rem; color:#94a3b8; margin-top:2px; border-top:1px solid rgba(255,255,255,0.08); padding-top:2px;">📍 ${metaLoc} · Cód: ${closestStation.codigo || '--'}</div>
                </div>
              `
            });
          }
        }
      }

      // 2.3.b Embalses y Presas (SAIH Júcar)
      if (activeStates["saih_embalses"] && activeStates["saih_embalses"].active) {
        const embalsesGroup = this.layerManager.layers["saih_embalses"];
        if (embalsesGroup) {
          let closestEmbalse = null;
          let minScreenDist = 25; // Radio de captura 25px

          const mousePt = this.map.latLngToContainerPoint(latlng);

          const checkLayer = (layer) => {
            if (layer.getLatLng && layer.feature && layer.feature.properties) {
              const markerPt = this.map.latLngToContainerPoint(layer.getLatLng());
              const screenDist = Math.hypot(markerPt.x - mousePt.x, markerPt.y - mousePt.y);
              if (screenDist < minScreenDist) {
                minScreenDist = screenDist;
                closestEmbalse = layer.feature.properties;
              }
            }
          };

          embalsesGroup.eachLayer((child) => {
            if (child.eachLayer) {
              child.eachLayer(checkLayer);
            } else {
              checkLayer(child);
            }
          });

          if (closestEmbalse) {
            const vol = closestEmbalse.volumen_actual !== null && closestEmbalse.volumen_actual !== undefined ? Number(closestEmbalse.volumen_actual) : null;
            const cap = closestEmbalse.capacidad_nmn !== null && closestEmbalse.capacidad_nmn !== undefined ? Number(closestEmbalse.capacidad_nmn) : null;
            const pct = closestEmbalse.porcentaje_llenado !== null && closestEmbalse.porcentaje_llenado !== undefined ? Number(closestEmbalse.porcentaje_llenado) : (vol !== null && cap ? (vol / cap * 100) : null);
            const cota = closestEmbalse.cota_actual !== null && closestEmbalse.cota_actual !== undefined ? Number(closestEmbalse.cota_actual) : null;
            const cotaV = closestEmbalse.cota_vertido !== null && closestEmbalse.cota_vertido !== undefined ? Number(closestEmbalse.cota_vertido) : null;

            // Color del badge según peligrosidad de desbordamiento: <35% verde, 35-70% amarillo, >=70% rojo
            let badgeBg = "#10b981";
            if (pct !== null) {
              if (pct >= 70) badgeBg = "#ef4444";
              else if (pct >= 35) badgeBg = "#f59e0b";
              else badgeBg = "#10b981";
            }

            const pctText = pct !== null ? `${pct.toFixed(1)}%` : "--%";
            const volText = vol !== null ? `${vol.toFixed(2)} hm³` : "-- hm³";
            const capText = cap !== null ? `${cap.toFixed(1)} hm³` : "-- hm³";
            const cotaText = cota !== null ? `${cota.toFixed(1)} m` : "-- m";
            const cotaVText = cotaV !== null ? `(Vertido: ${cotaV.toFixed(1)} m)` : '';
            const metaLoc = `${closestEmbalse.poblacion || '--'} (${closestEmbalse.provincia || ''}) · ${closestEmbalse.subcuenca || ''}`;

            sections.push({
              type: "embalse",
              title: "Embalse (SAIH Júcar)",
              headerColor: "#0ea5e9",
              icon: "🌊",
              name: `${closestEmbalse.nombre}`,
              badge: `${pctText} lleno`,
              badgeBg: badgeBg,
              badgeColor: "#ffffff",
              details: `
                <div class="unified-caudal-block">
                  <div style="margin-top:2px; font-size:0.82rem;">Volumen: <strong style="color:#38bdf8; font-size:1.08em;">${volText}</strong> <span style="color:#94a3b8; font-size:0.72rem;">/ ${capText}</span></div>
                  <div style="font-size:0.72rem; color:#cbd5e1; margin-top:2px;">Cota nivel: <strong>${cotaText}</strong> <span style="color:#94a3b8; font-size:0.70rem;">${cotaVText}</span></div>
                  <div style="font-size:0.70rem; color:#94a3b8; margin-top:2px; border-top:1px solid rgba(255,255,255,0.08); padding-top:2px;">📍 ${metaLoc} · Cód: ${closestEmbalse.codigo || '--'}</div>
                </div>
              `
            });
          }
        }
      }

      // 2.3.c Pluviómetros / Lluvia (SAIH Júcar)
      if (activeStates["saih_lluvias"] && activeStates["saih_lluvias"].active) {
        const lluviasGroup = this.layerManager.layers["saih_lluvias"];
        if (lluviasGroup) {
          let closestPluvio = null;
          let minScreenDist = 20; // Radio de captura 20px en pantalla

          const mousePt = this.map.latLngToContainerPoint(latlng);

          const checkLayer = (layer) => {
            if (layer.getLatLng && layer.feature && layer.feature.properties) {
              const markerPt = this.map.latLngToContainerPoint(layer.getLatLng());
              const screenDist = Math.hypot(markerPt.x - mousePt.x, markerPt.y - mousePt.y);
              if (screenDist < minScreenDist) {
                minScreenDist = screenDist;
                closestPluvio = layer.feature.properties;
              }
            }
          };

          lluviasGroup.eachLayer((child) => {
            if (child.eachLayer) {
              child.eachLayer(checkLayer);
            } else {
              checkLayer(child);
            }
          });

          if (closestPluvio) {
            const r1h = closestPluvio.lluvia_1h !== null && closestPluvio.lluvia_1h !== undefined ? Number(closestPluvio.lluvia_1h) : 0;
            const r4h = closestPluvio.lluvia_4h !== null && closestPluvio.lluvia_4h !== undefined ? Number(closestPluvio.lluvia_4h) : 0;
            const r12h = closestPluvio.lluvia_12h !== null && closestPluvio.lluvia_12h !== undefined ? Number(closestPluvio.lluvia_12h) : 0;
            const r24h = closestPluvio.lluvia_24h !== null && closestPluvio.lluvia_24h !== undefined ? Number(closestPluvio.lluvia_24h) : 0;

            let badgeBg = "#64748b";
            if (r24h >= 100 || r1h >= 20) badgeBg = "#ef4444";
            else if (r24h >= 60 || r1h >= 10) badgeBg = "#f97316";
            else if (r24h >= 30 || r1h >= 5) badgeBg = "#eab308";
            else if (r24h >= 10) badgeBg = "#0284c7";
            else if (r24h > 0 || r1h > 0) badgeBg = "#38bdf8";

            const badgeText = r24h > 0 ? `${r24h.toFixed(1)} mm (24h)` : `0.0 mm`;
            const metaLoc = `${closestPluvio.poblacion || '--'} (${closestPluvio.provincia || ''})`;

            sections.push({
              type: "lluvia",
              title: "Pluviómetro (SAIH Júcar)",
              headerColor: "#38bdf8",
              icon: "🌧️",
              name: `${closestPluvio.nombre}`,
              badge: badgeText,
              badgeBg: badgeBg,
              badgeColor: "#ffffff",
              details: `
                <div class="unified-caudal-block">
                  <div style="display: grid; grid-template-columns: repeat(4, 1fr); gap: 4px; margin-top: 4px; background: rgba(0,0,0,0.3); padding: 5px 6px; border-radius: 6px; text-align: center;">
                    <div>
                      <div style="font-size:0.65rem; color:#94a3b8;">1 hora</div>
                      <div style="font-size:0.80rem; font-weight:700; color:${r1h > 0 ? '#38bdf8' : '#e2e8f0'};">${r1h.toFixed(1)}<span style="font-size:0.62rem; font-weight:400; color:#94a3b8;"> mm</span></div>
                    </div>
                    <div>
                      <div style="font-size:0.65rem; color:#94a3b8;">4 horas</div>
                      <div style="font-size:0.80rem; font-weight:700; color:${r4h > 0 ? '#38bdf8' : '#e2e8f0'};">${r4h.toFixed(1)}<span style="font-size:0.62rem; font-weight:400; color:#94a3b8;"> mm</span></div>
                    </div>
                    <div>
                      <div style="font-size:0.65rem; color:#94a3b8;">12 horas</div>
                      <div style="font-size:0.80rem; font-weight:700; color:${r12h > 0 ? '#38bdf8' : '#e2e8f0'};">${r12h.toFixed(1)}<span style="font-size:0.62rem; font-weight:400; color:#94a3b8;"> mm</span></div>
                    </div>
                    <div>
                      <div style="font-size:0.65rem; color:#94a3b8;">24 horas</div>
                      <div style="font-size:0.80rem; font-weight:700; color:${r24h > 0 ? '#38bdf8' : '#e2e8f0'};">${r24h.toFixed(1)}<span style="font-size:0.62rem; font-weight:400; color:#94a3b8;"> mm</span></div>
                    </div>
                  </div>
                  <div style="font-size:0.70rem; color:#94a3b8; margin-top:4px; border-top:1px solid rgba(255,255,255,0.08); padding-top:2px;">📍 ${metaLoc} · Cód: ${closestPluvio.codigo || '--'}</div>
                </div>
              `
            });
          }
        }
      }

      // 2.4 Modelos Numéricos (AROME, ICON, ECMWF)
      ["arome_precip", "icon_d2", "ecmwf_ifs"].forEach((modelId) => {
        if (activeStates[modelId] && activeStates[modelId].active) {
          const modelGroup = this.layerManager.layers[modelId];
          if (modelGroup) {
            let modelHit = null;
            modelGroup.eachLayer((rect) => {
              if (rect.getBounds && rect.getBounds().contains(latlng)) {
                modelHit = rect;
              }
            });

            if (modelHit) {
              const def = CONFIG.overlayLayers.prediction.find((p) => p.id === modelId);
              sections.push({
                type: "model",
                title: def ? def.name : "Modelo Numérico",
                headerColor: def ? def.color : "#c084fc",
                icon: "🔮",
                name: "Precipitación Prevista",
                badge: def ? def.badge : "Predicción",
                badgeBg: def ? def.color : "#7c3aed",
                details: def ? def.description : null
              });
            }
          }
        }
      });
    }

    // Si hay secciones coincidentes, renderizar tooltip unificado
    if (sections.length > 0) {
      this._renderUnifiedTooltip(latlng, sections);
    } else {
      this._closeTooltip();
    }
  }

  _renderUnifiedTooltip(latlng, sections) {
    const isMultiLayer = sections.length > 1;

    let html = `<div class="unified-tooltip-container ${isMultiLayer ? "is-multi-layer" : ""}">`;

    if (isMultiLayer) {
      html += `
        <div class="unified-tooltip-counter">
          <span class="counter-dot"></span>
          <span>${sections.length} capas superpuestas en este punto</span>
        </div>
      `;
    }

    sections.forEach((sec, idx) => {
      const isDivider = idx > 0;
      html += `
        ${isDivider ? '<div class="unified-section-divider"></div>' : ''}
        <div class="unified-tooltip-section section-${sec.type}">
          <div class="unified-section-header">
            <span class="unified-section-title" style="color: ${sec.headerColor || "#38bdf8"};">
              ${sec.icon} ${sec.title}
            </span>
            ${sec.badge ? `<span class="unified-section-badge" style="background:${sec.badgeBg || "rgba(255,255,255,0.1)"}; color:${sec.badgeColor || "#fff"};">${sec.badge}</span>` : ""}
          </div>
          <div class="unified-section-name">${sec.name}</div>
          ${sec.details ? `<div class="unified-section-details">${sec.details}</div>` : ""}
        </div>
      `;
    });

    html += `</div>`;

    this.unifiedTooltip
      .setLatLng(latlng)
      .setContent(html);

    if (!this.map.hasLayer(this.unifiedTooltip)) {
      this.unifiedTooltip.addTo(this.map);
    }
  }

  _closeTooltip() {
    if (this.map.hasLayer(this.unifiedTooltip)) {
      this.map.removeLayer(this.unifiedTooltip);
    }
  }

  _applyCuencaHover(layer, systemColor) {
    if (this._currentHoveredCuenca === layer) return;
    this._clearCuencaHover();

    this._currentHoveredCuenca = layer;
    if (layer && layer.setStyle) {
      const isFav = layer.feature && StorageManager.isFavorite(layer.feature.id);
      layer.setStyle({
        ...CONFIG.styles.cuencaHover,
        color: isFav ? '#fbbf24' : systemColor,
        opacity: 1.0,
        fillColor: systemColor
      });
    }
  }

  _clearCuencaHover() {
    if (this._currentHoveredCuenca) {
      if (this.cuencasLayer && this.cuencasLayer.getFeatureStyle) {
        const originalStyle = this.cuencasLayer.getFeatureStyle(this._currentHoveredCuenca.feature);
        this._currentHoveredCuenca.setStyle(originalStyle);
      }
      this._currentHoveredCuenca = null;
    }
  }

  /**
   * Algoritmo Ray-Casting (Point-in-Polygon) optimizado
   */
  _isPointInGeometry(lat, lng, geometry) {
    if (!geometry || !geometry.coordinates) return false;
    const type = geometry.type;

    if (type === "Polygon") {
      return this._isPointInPolygon(lat, lng, geometry.coordinates);
    } else if (type === "MultiPolygon") {
      for (const poly of geometry.coordinates) {
        if (this._isPointInPolygon(lat, lng, poly)) return true;
      }
    }
    return false;
  }

  _isPointInPolygon(lat, lng, rings) {
    if (!rings || rings.length === 0) return false;
    // Anillo exterior
    const outerRing = rings[0];
    if (!this._pointInRing(lat, lng, outerRing)) return false;

    // Si tiene anillos interiores (agujeros), no debe estar dentro de ellos
    for (let i = 1; i < rings.length; i++) {
      if (this._pointInRing(lat, lng, rings[i])) {
        return false;
      }
    }
    return true;
  }

  _pointInRing(lat, lng, ring) {
    let inside = false;
    for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
      const xi = ring[i][0], yi = ring[i][1];
      const xj = ring[j][0], yj = ring[j][1];

      const intersect = ((yi > lat) !== (yj > lat)) &&
        (lng < (xj - xi) * (lat - yi) / (yj - yi) + xi);
      if (intersect) inside = !inside;
    }
    return inside;
  }
}
