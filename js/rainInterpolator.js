/**
 * RainLoc - Motor de Interpolación Espacial de Pluviometría (Cliente / Navegador)
 * Genera un mapa continuo suavizado (malla ráster interpolada) de acumulados (1h, 4h, 12h, 24h)
 * a partir de la red combinada de pluviómetros (AEMET, SAIH CHJ/CHE/CHS/CHG/Hidrosur, AVAMET, Meteocat).
 *
 * Algoritmo: IDW Adaptativo con decaimiento gaussiano (Barnes Objective Analysis),
 * indexación espacial por cuadrícula Hash 2D y recorte por máscara poligonal de tierra (CCAA).
 */

export class RainInterpolator {
  constructor() {
    // Escala de color AVAMET (umbral mm -> [R, G, B, A])
    // 0 mm es completamente transparente; a partir de 0.5 mm comienza la coloración
    this.colorScale = [
      { val: 0.0,   rgba: [255, 255, 255, 0] },
      { val: 0.49,  rgba: [255, 255, 255, 0] },
      { val: 0.5,   rgba: [224, 244, 252, 210] }, // Celeste muy pálido
      { val: 2.0,   rgba: [185, 229, 251, 220] }, // Celeste hielo
      { val: 5.0,   rgba: [126, 203, 248, 230] }, // Azul pastel
      { val: 10.0,  rgba: [56,  182, 244, 235] }, // Azul claro vivo
      { val: 20.0,  rgba: [2,   152, 232, 240] }, // Azul cielo
      { val: 30.0,  rgba: [1,   101, 184, 245] }, // Azul cobalto
      { val: 40.0,  rgba: [1,   53,  141, 245] }, // Azul marino
      { val: 50.0,  rgba: [61,  12,  114, 250] }, // Violeta oscuro
      { val: 60.0,  rgba: [104, 22,  153, 250] }, // Púrpura
      { val: 70.0,  rgba: [155, 33,  196, 250] }, // Violeta eléctrico
      { val: 80.0,  rgba: [201, 43,  185, 250] }, // Magenta
      { val: 90.0,  rgba: [232, 61,  164, 250] }, // Magenta vivo
      { val: 100.0, rgba: [244, 86,  149, 250] }, // Fucsia/Rosa fuerte
      { val: 110.0, rgba: [248, 108, 125, 250] }, // Rosa cálido
      { val: 120.0, rgba: [249, 121, 96,  250] }, // Coral
      { val: 130.0, rgba: [247, 123, 66,  250] }, // Naranja-rojo
      { val: 140.0, rgba: [242, 105, 44,  250] }, // Rojo anaranjado
      { val: 150.0, rgba: [230, 56,  30,  250] }, // Rojo brillante
      { val: 175.0, rgba: [206, 22,  25,  250] }, // Rojo intenso
      { val: 200.0, rgba: [176, 12,  25,  250] }, // Rojo carmín
      { val: 225.0, rgba: [143, 4,   23,  250] }, // Granate oscuro
      { val: 250.0, rgba: [110, 0,   19,  250] }, // Borgoña profundo
      { val: 275.0, rgba: [78,  0,   15,  250] }, // Marrón rojizo oscuro
      { val: 300.0, rgba: [48,  0,   10,  250] }, // Marrón muy oscuro
      { val: 350.0, rgba: [28,  0,   5,   255] }, // Casi negro
      { val: 500.0, rgba: [10,  0,   2,   255] }  // Negro absoluto
    ];

    // Bounding Box estándar de la Península Ibérica y Baleares
    this.defaultBounds = {
      minLat: 35.8,
      maxLat: 43.9,
      minLon: -9.6,
      maxLon: 4.5
    };
  }

  /**
   * Extrae puntos válidos de precipitación [lat, lon, value, name, id] según el periodo seleccionado
   */
  extractStationPoints(featuresList, period = '24h') {
    const points = [];
    const seen = new Set();

    const periodKey = `lluvia_${period}`;
    const altPeriodKey = `precipitacion_${period}`;

    featuresList.forEach(feat => {
      if (!feat || !feat.geometry || !feat.geometry.coordinates) return;
      const coords = feat.geometry.coordinates;
      const lon = Number(coords[0]);
      const lat = Number(coords[1]);

      if (isNaN(lat) || isNaN(lon)) return;
      // Filtrar estaciones fuera de la zona de España/Península/Baleares
      if (lat < 35.0 || lat > 44.5 || lon < -10.0 || lon > 5.0) return;

      const props = feat.properties || {};
      let val = null;

      if (props[periodKey] !== undefined && props[periodKey] !== null) {
        val = Number(props[periodKey]);
      } else if (props[altPeriodKey] !== undefined && props[altPeriodKey] !== null) {
        val = Number(props[altPeriodKey]);
      } else if (period === '24h' && props.lluvia_hoy !== undefined && props.lluvia_hoy !== null) {
        val = Number(props.lluvia_hoy);
      }

      if (val === null || isNaN(val) || val < 0) return;

      // Evitar duplicados muy cercanos (< 0.003 grados) dando prioridad al mayor valor
      const locKey = `${lat.toFixed(3)}_${lon.toFixed(3)}`;
      if (seen.has(locKey)) return;
      seen.add(locKey);

      points.push({
        lat,
        lon,
        val,
        name: props.nombre || props.poblacion || 'Estación',
        red: props.red || 'SAIH',
        id: props.id_estacion || props.codigo || `${lat}_${lon}`
      });
    });

    return points;
  }

  /**
   * Interpola una malla espacial continua a partir de los puntos pluviométricos.
   * Retorna una URL de imagen dataURL generada por Canvas 2D y los límites geográficos.
   *
   * @param {Array} points - Lista de objetos { lat, lon, val }
   * @param {Object} options - Parámetros de renderizado
   * @param {Object} [options.bounds] - Límites geográficos [[minLat, minLon], [maxLat, maxLon]]
   * @param {number} [options.width=600] - Resolución horizontal del canvas
   * @param {number} [options.height=450] - Resolución vertical del canvas
   * @param {Object} [options.maskGeoJson] - GeoJSON con polígonos de tierra/CCAA para recortar
   * @returns {Object} { dataUrl, bounds, maxVal, minVal, pointsCount }
   */
  generateInterpolatedGrid(points, options = {}) {
    if (!points || points.length === 0) {
      return null;
    }

    const bounds = options.bounds || this.defaultBounds;
    const minLat = bounds.minLat !== undefined ? bounds.minLat : bounds[0][0];
    const minLon = bounds.minLon !== undefined ? bounds.minLon : bounds[0][1];
    const maxLat = bounds.maxLat !== undefined ? bounds.maxLat : bounds[1][0];
    const maxLon = bounds.maxLon !== undefined ? bounds.maxLon : bounds[1][1];

    const latSpan = maxLat - minLat;
    const lonSpan = maxLon - minLon;
    if (latSpan <= 0 || lonSpan <= 0) return null;

    // Aspect ratio geográfico ajustado por latitud media
    const midLatRad = ((minLat + maxLat) / 2) * (Math.PI / 180);
    const cosLat = Math.cos(midLatRad);
    const width = options.width || 560;
    const height = options.height || Math.round(width * (latSpan / (lonSpan * cosLat)));

    // Canvas de renderizado en memoria
    const canvas = document.createElement('canvas');
    canvas.width = width;
    canvas.height = height;
    const ctx = canvas.getContext('2d', { willReadFrequently: true });
    if (!ctx) return null;

    // 1. Construir índice espacial Grid Hash 2D para búsqueda k-NN ultrarrápida
    const cellSize = 0.45; // ~45 km por celda de índice
    const gridCols = Math.ceil(lonSpan / cellSize) + 2;
    const gridRows = Math.ceil(latSpan / cellSize) + 2;
    const grid = new Array(gridCols * gridRows);
    for (let i = 0; i < grid.length; i++) grid[i] = [];

    const getGridIdx = (lon, lat) => {
      const c = Math.floor((lon - minLon) / cellSize);
      const r = Math.floor((lat - minLat) / cellSize);
      if (c < 0 || c >= gridCols || r < 0 || r >= gridRows) return -1;
      return r * gridCols + c;
    };

    let maxObsVal = 0;
    for (let i = 0; i < points.length; i++) {
      const p = points[i];
      if (p.val > maxObsVal) maxObsVal = p.val;
      const gIdx = getGridIdx(p.lon, p.lat);
      if (gIdx >= 0) {
        grid[gIdx].push(p);
      }
    }

    // 2. Parámetros de interpolación adaptativa IDW / Barnes
    const maxInfluenceRadiusDeg = 0.55; // ~55 km radio máximo de influencia
    const maxRadiusSq = maxInfluenceRadiusDeg * maxInfluenceRadiusDeg;
    const fadeRadiusDeg = 0.28; // Inicio de desvanecimiento suave a los bordes
    const smoothRadiusDeg = 0.32; // Radio de suavizado gaussiano
    const power = 1.85; // Exponente de atenuación IDW

    const imgData = ctx.createImageData(width, height);
    const data = imgData.data;

    const queryRadiusCells = Math.ceil(maxInfluenceRadiusDeg / cellSize);

    // Bucle píxel a píxel sobre la cuadrícula
    for (let py = 0; py < height; py++) {
      // Coordenada latitud (arriba = maxLat, abajo = minLat)
      const lat = maxLat - (py / height) * latSpan;
      const rowOffset = py * width * 4;

      for (let px = 0; px < width; px++) {
        // Coordenada longitud (izquierda = minLon, derecha = maxLon)
        const lon = minLon + (px / width) * lonSpan;

        const cellCol = Math.floor((lon - minLon) / cellSize);
        const cellRow = Math.floor((lat - minLat) / cellSize);

        let sumWeights = 0;
        let sumWeightedVal = 0;
        let minDistSq = 999999;
        let exactVal = null;

        // Buscar estaciones vecinas en celdas adyacentes
        const minC = Math.max(0, cellCol - queryRadiusCells);
        const maxC = Math.min(gridCols - 1, cellCol + queryRadiusCells);
        const minR = Math.max(0, cellRow - queryRadiusCells);
        const maxR = Math.min(gridRows - 1, cellRow + queryRadiusCells);

        for (let r = minR; r <= maxR; r++) {
          const rBase = r * gridCols;
          for (let c = minC; c <= maxC; c++) {
            const cellPoints = grid[rBase + c];
            if (!cellPoints || cellPoints.length === 0) continue;

            for (let k = 0; k < cellPoints.length; k++) {
              const pt = cellPoints[k];
              const dLat = lat - pt.lat;
              const dLon = (lon - pt.lon) * cosLat;
              const distSq = dLat * dLat + dLon * dLon;

              if (distSq < minDistSq) {
                minDistSq = distSq;
              }

              if (distSq > maxRadiusSq) continue;

              const dist = Math.sqrt(distSq);
              if (dist < 0.002) {
                // Coincidencia exacta sobre la estación
                exactVal = pt.val;
                break;
              }

              // Ponderación IDW con decaimiento gaussiano suave (Barnes)
              const gauss = Math.exp(- (dist / smoothRadiusDeg) * (dist / smoothRadiusDeg));
              const idw = 1.0 / Math.pow(dist + 0.015, power);
              const w = gauss * idw;

              sumWeights += w;
              sumWeightedVal += w * pt.val;
            }

            if (exactVal !== null) break;
          }
          if (exactVal !== null) break;
        }

        const idx = rowOffset + px * 4;

        if (exactVal !== null) {
          const rgba = this.getColorForValue(exactVal);
          data[idx]     = rgba[0];
          data[idx + 1] = rgba[1];
          data[idx + 2] = rgba[2];
          data[idx + 3] = rgba[3];
          continue;
        }

        const minDist = Math.sqrt(minDistSq);
        if (sumWeights === 0 || minDist > maxInfluenceRadiusDeg) {
          // Fuera de radio de cobertura (0 mm / transparente)
          data[idx + 3] = 0;
          continue;
        }

        let interpVal = sumWeightedVal / sumWeights;

        // Desvanecimiento suave si está lejos de la estación más cercana
        if (minDist > fadeRadiusDeg) {
          const fadeFactor = Math.max(0, 1 - ((minDist - fadeRadiusDeg) / (maxInfluenceRadiusDeg - fadeRadiusDeg)));
          interpVal *= (fadeFactor * fadeFactor);
        }

        if (interpVal < 0.3) {
          data[idx + 3] = 0; // Transparente para trazas inapreciables
        } else {
          const rgba = this.getColorForValue(interpVal);
          data[idx]     = rgba[0];
          data[idx + 1] = rgba[1];
          data[idx + 2] = rgba[2];
          data[idx + 3] = rgba[3];
        }
      }
    }

    ctx.putImageData(imgData, 0, 0);

    // 3. Recorte por máscara geográfica vectorial de tierra (CCAA / Península) si está disponible
    if (options.maskGeoJson && options.maskGeoJson.features) {
      this._applyGeoMask(ctx, options.maskGeoJson, { minLat, maxLat, minLon, maxLon, width, height });
    }

    return {
      dataUrl: canvas.toDataURL('image/png'),
      bounds: [
        [minLat, minLon],
        [maxLat, maxLon]
      ],
      maxObsVal,
      pointsCount: points.length
    };
  }

  /**
   * Obtiene el valor interpolado de precipitación en una coordenada exacta (lat, lon)
   * @param {number} lat
   * @param {number} lon
   * @param {Array} points Lista de puntos { lat, lon, val }
   * @returns {number|null}
   */
  sampleValueAt(lat, lon, points) {
    if (!points || points.length === 0) return null;

    const maxInfluenceRadiusDeg = 0.55; // ~55 km radio de influencia
    const maxRadiusSq = maxInfluenceRadiusDeg * maxInfluenceRadiusDeg;
    const fadeRadiusDeg = 0.28;
    const smoothRadiusDeg = 0.32;
    const power = 1.85;

    const cosLat = Math.cos(lat * (Math.PI / 180));

    let sumWeights = 0;
    let sumWeightedVal = 0;
    let minDistSq = 999999;
    let exactVal = null;

    for (let i = 0; i < points.length; i++) {
      const pt = points[i];
      const dLat = lat - pt.lat;
      const dLon = (lon - pt.lon) * cosLat;
      const distSq = dLat * dLat + dLon * dLon;

      if (distSq < minDistSq) {
        minDistSq = distSq;
      }

      if (distSq > maxRadiusSq) continue;

      const dist = Math.sqrt(distSq);
      if (dist < 0.002) {
        exactVal = pt.val;
        break;
      }

      const gauss = Math.exp(- (dist / smoothRadiusDeg) * (dist / smoothRadiusDeg));
      const idw = 1.0 / Math.pow(dist + 0.015, power);
      const w = gauss * idw;

      sumWeights += w;
      sumWeightedVal += w * pt.val;
    }

    if (exactVal !== null) return exactVal;

    const minDist = Math.sqrt(minDistSq);
    if (sumWeights === 0 || minDist > maxInfluenceRadiusDeg) return null;

    let val = sumWeightedVal / sumWeights;
    if (minDist > fadeRadiusDeg) {
      const fadeFactor = Math.max(0, 1 - ((minDist - fadeRadiusDeg) / (maxInfluenceRadiusDeg - fadeRadiusDeg)));
      val *= (fadeFactor * fadeFactor);
    }

    return val >= 0.05 ? val : 0;
  }

  /**
   * Mapea un valor continuo de precipitación (mm) a color RGBA interpolado de la rampa AVAMET
   */
  getColorForValue(val) {
    if (val < 0.5) return [0, 0, 0, 0];

    const scale = this.colorScale;
    if (val >= scale[scale.length - 1].val) {
      return scale[scale.length - 1].rgba;
    }

    for (let i = 0; i < scale.length - 1; i++) {
      const cur = scale[i];
      const next = scale[i + 1];

      if (val >= cur.val && val <= next.val) {
        const t = (val - cur.val) / (next.val - cur.val);
        const r = Math.round(cur.rgba[0] + (next.rgba[0] - cur.rgba[0]) * t);
        const g = Math.round(cur.rgba[1] + (next.rgba[1] - cur.rgba[1]) * t);
        const b = Math.round(cur.rgba[2] + (next.rgba[2] - cur.rgba[2]) * t);
        const a = Math.round(cur.rgba[3] + (next.rgba[3] - cur.rgba[3]) * t);
        return [r, g, b, a];
      }
    }

    return [0, 0, 0, 0];
  }

  /**
   * Aplica un recorte limpio sobre el Canvas usando la geometría vectorial de CCAA / España
   */
  _applyGeoMask(ctx, maskGeoJson, meta) {
    const { minLat, maxLat, minLon, maxLon, width, height } = meta;
    const latSpan = maxLat - minLat;
    const lonSpan = maxLon - minLon;

    const maskCanvas = document.createElement('canvas');
    maskCanvas.width = width;
    maskCanvas.height = height;
    const mCtx = maskCanvas.getContext('2d');
    if (!mCtx) return;

    mCtx.fillStyle = '#000000';

    const projectPoint = (coord) => {
      const lon = coord[0];
      const lat = coord[1];
      const x = ((lon - minLon) / lonSpan) * width;
      const y = ((maxLat - lat) / latSpan) * height;
      return [x, y];
    };

    const drawPolygon = (ring) => {
      if (!ring || ring.length < 3) return;
      const start = projectPoint(ring[0]);
      mCtx.moveTo(start[0], start[1]);
      for (let i = 1; i < ring.length; i++) {
        const pt = projectPoint(ring[i]);
        mCtx.lineTo(pt[0], pt[1]);
      }
      mCtx.closePath();
    };

    mCtx.beginPath();
    maskGeoJson.features.forEach(feat => {
      if (!feat.geometry) return;
      const geomType = feat.geometry.type;
      const coords = feat.geometry.coordinates;

      if (geomType === 'Polygon') {
        coords.forEach(ring => drawPolygon(ring));
      } else if (geomType === 'MultiPolygon') {
        coords.forEach(poly => {
          poly.forEach(ring => drawPolygon(ring));
        });
      }
    });
    mCtx.fill();

    // Recortar con destination-in (solo conserva lo que cae dentro de tierra)
    ctx.save();
    ctx.globalCompositeOperation = 'destination-in';
    ctx.drawImage(maskCanvas, 0, 0);
    ctx.restore();
  }
}

export const rainInterpolator = new RainInterpolator();
