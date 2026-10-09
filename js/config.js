/**
 * RainLoc - Configuración General
 * Constantes geográficas, estilos cartográficos y metadatos de la cuenca CHJ.
 */

const apiBase = (typeof window !== 'undefined' && window.location && window.location.protocol.startsWith('http'))
  ? ((window.location.port === '' || window.location.port === '80') ? '/api/v1' : `http://${window.location.hostname}:8000/api/v1`)
  : '/api/v1';

export const CONFIG = {
  // Centro geográfico de la Comunitat Valenciana y zoom óptimo
  map: {
    initialCenter: [39.48, -0.37],
    initialZoom: 8,
    minZoom: 4,
    maxZoom: 18,
    maxBounds: null // Permite desplazamiento libre sin restricciones
  },

  // API Backend URL (FastAPI): Detecta si se accede tras Nginx/Docker en puerto 80 (usando ruta relativa) o en desarrollo directo
  apiBaseUrl: apiBase,

  // Rutas candidatas para el GeoJSON de cuencas/subsistemas y CCAA (prioriza Backend API con fallback local)
  dataSources: {
    subsistemasGeoJson: [
      `${apiBase}/cuencas?v=3.71`,
      './subsistemas.optimized.geojson',
      './subsistemas.geojson',
      './data/subsistemas.geojson',
      './data/cuencas.geojson'
    ],
    ebroCuencasGeoJson: [
      `${apiBase}/cuencas/ebro?v=3.71`,
      './data/ebro_subcuencas.geojson',
      './ebro_subcuencas.geojson'
    ],
    seguraCuencasGeoJson: [
      `${apiBase}/cuencas/segura?v=3.71`,
      './data/segura_subcuencas.geojson',
      './segura_subcuencas.geojson'
    ],
    guadalquivirCuencasGeoJson: [
      `${apiBase}/cuencas/guadalquivir?v=3.71`,
      './data/guadalquivir_subcuencas.geojson',
      './guadalquivir_subcuencas.geojson'
    ],
    ccaaGeoJson: [
      `${apiBase}/ccaa`,
      `${apiBase}/cuencas/ccaa`,
      './ccaa.geojson',
      './data/ccaa.geojson'
    ],
    riosGeoJson: [
      `${apiBase}/cuencas/rios`,
      './data/rios.geojson',
      './rios.geojson'
    ]
  },

  // Capas base de cartografía (100% abiertas, sin claves ni marcas de agua)
  basemaps: {
    esriCanvas: {
      id: 'esriCanvas',
      name: 'Esri Gris Claro (Lienzo Minimalista)',
      url: 'https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}',
      attribution: 'Tiles &copy; Esri &mdash; Esri, DeLorme, NAVTEQ',
      maxZoom: 16
    },
    esriDarkCanvas: {
      id: 'esriDarkCanvas',
      name: 'Esri Gris Oscuro (Modo Noche)',
      url: 'https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}',
      attribution: 'Tiles &copy; Esri &mdash; Esri, DeLorme, NAVTEQ',
      maxZoom: 16
    },
    ignBase: {
      id: 'ignBase',
      name: 'IGN Base España (Oficial)',
      url: 'https://www.ign.es/wmts/ign-base?service=WMTS&request=GetTile&version=1.0.0&Format=image/png&layer=IGNBaseTodo&style=default&tilematrixset=GoogleMapsCompatible&TileMatrix={z}&TileRow={y}&TileCol={x}',
      attribution: '&copy; <a href="https://www.ign.es" target="_blank">Instituto Geográfico Nacional</a>',
      maxZoom: 19
    },
    esriSatellite: {
      id: 'esriSatellite',
      name: 'Satélite (Esri World Imagery)',
      url: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
      attribution: 'Tiles &copy; Esri &mdash; Source: Esri, i-cubed, USDA, USGS, AEX, GeoEye, IGN',
      maxZoom: 18
    },
    osm: {
      id: 'osm',
      name: 'OpenStreetMap Estándar',
      url: 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank">OpenStreetMap</a>',
      maxZoom: 19
    }
  },

  // Paleta de colores de alto contraste cromático por Sistema de Explotación (CHJ, CHEbro y CHSegura)
  systemColors: {
    // Confederación Hidrográfica del Júcar (CHJ)
    'Cenia - Mijares': '#059669',       // Verde Esmeralda vibrante (Norte)
    'Palancia - Los Valles': '#d97706',   // Ámbar / Naranja dorado
    'Turia': '#8b5cf6',                 // Violeta / Púrpura eléctrico
    'Júcar': '#0284c7',                 // Azul Oceánico vivo (Centro)
    'Serpis': '#e11d48',                // Rojo Carmín intenso
    'Marina Alta': '#06b6d4',           // Cian / Turquesa costero
    'Marina Baja': '#d946ef',           // Fucsia / Magenta
    'Alacantí': '#ea580c',              // Naranja cálido
    'Vinalopó': '#4338ca',              // Azul Índigo profundo (Sur)

    // Confederación Hidrográfica del Ebro (CHEbro)
    'Matarraña': '#10b981',
    'Gállego - Cinca': '#0284c7',
    'Garona': '#06b6d4',
    'Guadalope - Regallo': '#f59e0b',
    'Huecha': '#e11d48',
    'Huerva': '#8b5cf6',
    'Iregua - Leza - Valle de Ocón': '#3b82f6',
    'Jalón': '#ea580c',
    'Martín': '#d946ef',
    'Ésera - Noguera Ribagorzana': '#0d9488',
    'Ebro bajo': '#2563eb',
    'Ega': '#6366f1',
    'Aguas Vivas': '#eab308',
    'Alhama': '#ec4899',
    'Arbas': '#14b8a6',
    'Bayas, Zadorra e Inglares': '#84cc16',
    'Cidacos': '#f97316',
    'Ciurana': '#a855f7',
    'Ebro alto y medio y Aragón': '#38bdf8',
    'Segre - Noguera Pallaresa': '#065f46',
    'Tirón': '#4f46e5',
    'Najerilla': '#0891b2',
    'Queiles': '#be185d',

    // Confederación Hidrográfica del Segura (CHSegura)
    'Cabecera Río Segura': '#0284c7',
    'Cabecera Río Mundo': '#0d9488',
    'Confluencia Segura-Mundo': '#06b6d4',
    'Zona Alta 1 Margen Der.': '#059669',
    'Zona Alta 1 Margen Izq.': '#10b981',
    'Zona Alta 1 Río Segura': '#38bdf8',
    'Zona Alta 2 Margen Der.': '#f59e0b',
    'Zona Alta 2 Margen Izq.': '#eab308',
    'Zona Alta 2 Río Segura': '#60a5fa',
    'Segura - Azud De Los Huertos': '#3b82f6',
    'Rambla Salada - Embalse Santomera': '#d946ef',
    'Alto Guadalentin': '#e11d48',
    'Medio Guadalentin': '#ea580c',
    'Bajo Guadalentin': '#f97316',
    'Sangonera-Regueron': '#ec4899',
    'Zona Baja Río Segura': '#2563eb',
    'Chícamo - Partidor': '#8b5cf6',
    'Crevillente-Levante': '#4338ca',
    'Rambla Del Albujon': '#065f46',
    'Ramblas Costeras Sur': '#14b8a6',
    'Ramblas Costeras Norte': '#6366f1',
    'Altiplano': '#84cc16',

    // Confederación Hidrográfica del Guadalquivir (CHGuadalquivir)
    'Abastecimiento de Sevilla': '#0284c7',
    'Alto Genil': '#10b981',
    'Bembézar-Retortillo': '#8b5cf6',
    'Guadiamar': '#06b6d4',
    'Hoya de Guadix': '#ea580c',
    'Regulación General': '#2563eb',
    'Abastecimiento de Córdoba': '#d946ef',
    'Abastecimiento de Jaén': '#e11d48',

    'Default': '#3b82f6'                // Azul estándar
  },

  // Estilos vectoriales de las cuencas
  styles: {
    // Estilo en reposo (contraste nítido y buena diferenciación)
    cuencaDefault: {
      weight: 2.0,               // Borde definido y visible
      opacity: 0.95,             // Borde con alta opacidad
      fillOpacity: 0.25,         // Opacidad de relleno óptima para distinguir colores sin tapar mapa
      lineCap: 'round',
      lineJoin: 'round'
    },
    // Estilo exclusivo para la cuenca favorita (Borde dorado siempre al 100% de opacidad)
    cuencaFavorite: {
      color: '#f59e0b',          // Dorado / Ámbar intenso
      weight: 3.6,               // Borde engrosado y definido
      opacity: 1.0,              // Inmune al slider de opacidad general
      lineCap: 'round',
      lineJoin: 'round'
    },
    // Estilo reactivo al pasar el ratón (hover)
    cuencaHover: {
      weight: 3.8,               // Borde engrosado
      opacity: 1.0,
      fillOpacity: 0.55,         // Relleno destacado y luminoso
      lineCap: 'round',
      lineJoin: 'round'
    },
    // Estilo seleccionado (click)
    cuencaSelected: {
      color: '#ffffff',          // Borde blanco de alto contraste
      weight: 4.2,
      opacity: 1.0,
      fillOpacity: 0.65
    }
  },

  // Catálogo de capas temáticas de observación y predicción
  overlayLayers: {
    realtime: [
      {
        id: 'aemet_warnings',
        name: 'Avisos AEMET',
        subtitle: 'Meteoalerta (Lluvias y Tormentas)',
        description: 'Avisos meteorológicos adversos oficiales activos por nivel de riesgo (Amarillo, Naranja, Rojo).',
        type: 'vector',
        defaultActive: false,
        defaultOpacity: 0.85,
        badge: 'Oficial',
        badgeType: 'warning',
        color: '#f59e0b',
        icon: `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z"></path><line x1="12" y1="9" x2="12" y2="13"></line><line x1="12" y1="17" x2="12.01" y2="17"></line></svg>`
      },
      {
        id: 'radar',
        name: 'Radar Meteorológico',
        subtitle: 'Reflectividad dBZ (ORD OPERA / EUMETNET)',
        description: 'Imágenes compuestas de radar en tiempo real calibradas en decibelios de reflectividad.',
        type: 'raster',
        defaultActive: false,
        defaultOpacity: 0.75,
        badge: 'En vivo',
        badgeType: 'live',
        color: '#0284c7',
        icon: `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 20a8 8 0 1 0 0-16 8 8 0 0 0 0 16Z"></path><path d="M12 14a2 2 0 1 0 0-4 2 2 0 0 0 0 4Z"></path><path d="M12 2v2"></path><path d="M12 22v-2"></path><path d="m17 17 1.4 1.4"></path><path d="m5.6 5.6 1.4 1.4"></path></svg>`
      },
      {
        id: 'saih_hidrologia',
        name: 'Red Hidrológica y Pluviometría',
        subtitle: 'Aforos, Embalses y Pluviómetros (~3.900 est.)',
        description: 'Monitorización integral en tiempo real: ~1.030 aforos en ríos, ~230 embalses y presas, y más de 2.900 pluviómetros oficiales (Redes SAIH, AEMET OpenData, AVAMET y Meteocat).',
        type: 'saih_group',
        defaultActive: false,
        defaultOpacity: 0.95,
        badge: 'En vivo',
        badgeType: 'info',
        color: '#0284c7',
        icon: `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2.69l5.66 5.66a8 8 0 1 1-11.31 0z"></path></svg>`,
        subLayers: [
          {
            id: 'saih_caudales',
            group: 'hidro',
            name: 'Caudales y Niveles en Ríos',
            subtitle: '~1.030 aforos en directo (CHJ, Segura, Ebro, Guadalquivir, Hidrosur, ACA y Duero)',
            icon: '🌊',
            badge: 'Aforos',
            badgeClass: 'badge-caudal',
            defaultActive: true,
            sourceName: 'Redes SAIH y ACA (CHJ, CHS, CHE, CHG, Hidrosur, ACA y CHD)',
            sourceUrl: 'https://saih.chj.es'
          },
          {
            id: 'saih_embalses',
            group: 'hidro',
            name: 'Embalses y Presas',
            subtitle: '~230 embalses y presas (CHJ, Segura, Ebro, Guadalquivir, Hidrosur, ACA y Duero)',
            icon: '🏞️',
            badge: 'Embalses',
            badgeClass: 'badge-embalse',
            defaultActive: true,
            sourceName: 'Redes SAIH y ACA (CHJ, CHS, CHE, CHG, Hidrosur, ACA y CHD)',
            sourceUrl: 'https://saih.chj.es'
          },
          {
            id: 'saih_lluvias',
            group: 'pluvio',
            name: 'Pluviómetros SAIH',
            subtitle: '~1.120 pluviómetros (CHJ, Segura, Ebro, Guadalquivir, Hidrosur y Duero)',
            icon: '🌧️',
            badge: 'SAIH',
            badgeClass: 'badge-lluvia',
            defaultActive: true,
            sourceName: 'Redes SAIH Hidrográficas (CHJ, CHS, CHE, CHG, Hidrosur, CHD)',
            sourceUrl: 'https://saih.chj.es'
          },
          {
            id: 'aemet_lluvias',
            group: 'pluvio',
            name: 'Pluviómetros AEMET OpenData',
            subtitle: '~850 estaciones automáticas (1h, 4h, 12h, 24h)',
            icon: '🌧️',
            badge: 'AEMET',
            badgeClass: 'badge-aemet',
            defaultActive: true,
            sourceName: 'Agencia Estatal de Meteorología (AEMET OpenData)',
            sourceUrl: 'https://opendata.aemet.es'
          },
          {
            id: 'avamet_lluvias',
            group: 'pluvio',
            name: 'Pluviómetros AVAMET (MeteoXarxa)',
            subtitle: '~860 estaciones Comunitat Valenciana (1h, 4h, 12h, 24h)',
            icon: '🌧️',
            badge: 'AVAMET',
            badgeClass: 'badge-avamet',
            defaultActive: true,
            sourceName: 'Associació Valenciana de Meteorologia (AVAMET MXO)',
            sourceUrl: 'https://www.avamet.org'
          },
          {
            id: 'meteocat_lluvias',
            group: 'pluvio',
            name: 'Pluviómetros Meteocat (XEMA)',
            subtitle: '~245 estaciones Catalunya (1h, 4h, 12h, 24h)',
            icon: '🌧️',
            badge: 'METEOCAT',
            badgeClass: 'badge-meteocat',
            defaultActive: true,
            sourceName: 'Servei Meteorològic de Catalunya (Dades Obertes Gencat)',
            sourceUrl: 'https://analisi.transparenciacatalunya.cat/d/nzvn-apee'
          }
        ]
      }
    ],
    prediction: [
      {
        id: 'arome_precip',
        name: 'Modelo AROME (1.3 km)',
        subtitle: 'Previsión convectiva alta resolución (48h)',
        description: 'Modelo no hidrostático de Météo-France / AEMET para predicción explícita de tormentas y chubascos intensos (hasta +48h).',
        type: 'model',
        defaultActive: false,
        defaultOpacity: 0.70,
        badge: 'Météo-France',
        badgeType: 'model',
        color: '#8b5cf6',
        icon: '<span class="model-flag-icon">🇫🇷</span>'
      },
      {
        id: 'harmonie_aemet',
        name: 'Modelo Harmonie-Arome (2.5 km)',
        subtitle: 'Previsión convectiva AEMET (48h)',
        description: 'Modelo no hidrostático de alta resolución de AEMET para predicción explícita de precipitaciones y tormentas (hasta +48h).',
        type: 'model',
        defaultActive: false,
        defaultOpacity: 0.70,
        badge: 'AEMET',
        badgeType: 'model',
        color: '#f59e0b',
        icon: '<span class="model-flag-icon">🇪🇸</span>'
      },
      {
        id: 'icon_eu',
        name: 'Modelo ICON-EU (6.5 km)',
        subtitle: 'Previsión DWD alta resolución (120h)',
        description: 'Modelo regional del Servicio Meteorológico Alemán (DWD) con resolución de 6.5 km y actualización rápida (hasta +120h).',
        type: 'model',
        defaultActive: false,
        defaultOpacity: 0.70,
        badge: 'DWD',
        badgeType: 'model',
        color: '#0284c7',
        icon: '<span class="model-flag-icon">🇩🇪</span>'
      },
      {
        id: 'gem_gdps',
        name: 'Modelo GEM-GDPS (15 km)',
        subtitle: 'Previsión global canadiense (10 días)',
        description: 'Modelo global del Centro Meteorológico Canadiense (MSC / ECCC GDPS, hasta +240h).',
        type: 'model',
        defaultActive: false,
        defaultOpacity: 0.65,
        badge: 'Canadá',
        badgeType: 'info',
        color: '#e11d48',
        icon: '<span class="model-flag-icon">🇨🇦</span>'
      },
      {
        id: 'ecmwf_ifs',
        name: 'Modelo ECMWF-IFS (25 km)',
        subtitle: 'Previsión global determinista (10 días)',
        description: 'Referencia global del Centro Europeo para predicción a medio plazo (ECMWF Open Data, hasta 240h).',
        type: 'model',
        defaultActive: false,
        defaultOpacity: 0.65,
        badge: 'Global',
        badgeType: 'info',
        color: '#059669',
        icon: '<span class="model-flag-icon">🇪🇺</span>'
      },
      {
        id: 'gfs_0p25',
        name: 'Modelo GFS (25 km)',
        subtitle: 'Previsión global NOAA (16 días)',
        description: 'Modelo global de la NOAA/NWS para predicción a medio y largo plazo (GFS Open Data, hasta +384h).',
        type: 'model',
        defaultActive: false,
        defaultOpacity: 0.65,
        badge: 'NOAA',
        badgeType: 'info',
        color: '#2563eb',
        icon: '<span class="model-flag-icon">🇺🇸</span>'
      }
    ]
  },

  // Catálogo completo de la red de radares meteorológicos de España (AEMET / ORD)
  radarStations: {
    'esahr': { id: 'esahr', name: 'Málaga / Alhaurín', province: 'Málaga', lat: 36.6134, lon: -4.6593, range_km: 240, short_range_km: 145, alt_m: 1159 },
    'esclg': { id: 'esclg', name: 'Sevilla / El Castillo', province: 'Sevilla', lat: 37.6887, lon: -6.3331, range_km: 250, short_range_km: 145, alt_m: 531 },
    'esnjr': { id: 'esnjr', name: 'Almería / Níjar', province: 'Almería', lat: 36.8324, lon: -2.0821, range_km: 240, short_range_km: 145, alt_m: 499 },
    'estjv': { id: 'estjv', name: 'Madrid / Torrejón de Velasco', province: 'Madrid', lat: 40.1759, lon: -3.7137, range_km: 240, short_range_km: 145, alt_m: 717 },
    'essft': { id: 'essft', name: 'Cáceres / Sierra Fuentes', province: 'Cáceres', lat: 39.4288, lon: -6.2853, range_km: 240, short_range_km: 145, alt_m: 667 },
    'esgrm': { id: 'esgrm', name: 'Salamanca / Guadramiro', province: 'Salamanca', lat: 41.0116, lon: -6.4777, range_km: 240, short_range_km: 145, alt_m: 793 },
    'eslid': { id: 'eslid', name: 'Valladolid', province: 'Valladolid', lat: 41.9956, lon: -4.6028, range_km: 240, short_range_km: 145, alt_m: 887 },
    'essse': { id: 'essse', name: 'País Vasco / Monte Oiz', province: 'Bizkaia/Gipuzkoa', lat: 43.4033, lon: -2.8419, range_km: 240, short_range_km: 145, alt_m: 625 },
    'espdg': { id: 'espdg', name: 'Zaragoza / Perdiguera', province: 'Zaragoza', lat: 41.7340, lon: -0.5459, range_km: 240, short_range_km: 145, alt_m: 835 },
    'esgld': { id: 'esgld', name: 'Barcelona / Gelida', province: 'Barcelona', lat: 41.4082, lon: 1.8849, range_km: 240, short_range_km: 145, alt_m: 662 },
    'esbnv': { id: 'esbnv', name: 'Tenerife / Buenavista', province: 'Santa Cruz de Tenerife', lat: 28.3109, lon: -16.8238, range_km: 240, short_range_km: 145, alt_m: 1367 },
    'esatn': { id: 'esatn', name: 'Gran Canaria / Artenara', province: 'Las Palmas', lat: 28.0188, lon: -15.6145, range_km: 240, short_range_km: 145, alt_m: 1777 },
    'escul': { id: 'escul', name: 'Valencia / Cullera', province: 'Valencia', lat: 39.1864, lon: -0.2520, range_km: 240, short_range_km: 145, alt_m: 236 },
    'espma': { id: 'espma', name: 'Murcia / Cabezo de la Plata', province: 'Murcia', lat: 37.9940, lon: -0.9940, range_km: 240, short_range_km: 145, alt_m: 460 },
    'espmb': { id: 'espmb', name: 'Mallorca / Puig de Randa', province: 'Illes Balears', lat: 39.5290, lon: 2.9230, range_km: 240, short_range_km: 145, alt_m: 543 },
    'esast': { id: 'esast', name: 'Asturias / Salas', province: 'Asturias', lat: 43.3420, lon: -6.5210, range_km: 240, short_range_km: 145, alt_m: 850 },
    'escor': { id: 'escor', name: 'A Coruña / Lousame', province: 'A Coruña', lat: 42.8360, lon: -8.8470, range_km: 240, short_range_km: 145, alt_m: 680 }
  }
};

/**
 * Formatea cualquier fecha, ISO string o timestep de radar (UTC)
 * a hora local oficial de España (zona horaria Europe/Madrid)
 * Formato consistente: 'DD/MM/YYYY · HH:MM'
 * 
 * @param {Date|string|number} dateInput 
 * @returns {string} Ejemplo: '18/09/2026 · 17:30'
 */
export function formatMadridDateTime(dateInput) {
  if (!dateInput) return '';
  let dateObj = null;

  if (dateInput instanceof Date) {
    dateObj = dateInput;
  } else if (typeof dateInput === 'string') {
    const s = dateInput.trim();
    // Caso 1: DD-MM-YYYY HH:MM o DD/MM/YYYY HH:MM (ej. AVAMET)
    const dmyMatch = s.match(/^(\d{2})[-\/](\d{2})[-\/](\d{4})(?:\s+(\d{2}):(\d{2})(?::(\d{2}))?)?$/);
    if (dmyMatch) {
      const [, day, month, year, hh, mm, ss] = dmyMatch;
      dateObj = new Date(parseInt(year, 10), parseInt(month, 10) - 1, parseInt(day, 10), parseInt(hh || 0, 10), parseInt(mm || 0, 10), parseInt(ss || 0, 10));
    }
    // Caso 2: Formato estándar ISO YYYY-MM-DD... (ej. SAIH CHJ '...Z', Hidrosur, Meteocat, AEMET)
    else if (s.includes('-') && /^\d{4}-\d{2}-\d{2}/.test(s)) {
      dateObj = new Date(s);
    }
    // Caso 3: Formato compacto de radar YYYYMMDDTHHMM (UTC) ej. 20260918T1530
    else if (s.length >= 13 && s.includes('T') && !s.includes('-')) {
      const yr = s.substring(0, 4);
      const mo = s.substring(4, 6);
      const dy = s.substring(6, 8);
      const hh = s.substring(9, 11);
      const mm = s.substring(11, 13);
      dateObj = new Date(Date.UTC(parseInt(yr, 10), parseInt(mo, 10) - 1, parseInt(dy, 10), parseInt(hh, 10), parseInt(mm, 10)));
    }
    // Caso 4: Formato numérico continuo YYYYMMDDHHMM (UTC) ej. 202609181530
    else if (s.length === 12 && /^\d{12}$/.test(s)) {
      const yr = s.substring(0, 4);
      const mo = s.substring(4, 6);
      const dy = s.substring(6, 8);
      const hh = s.substring(8, 10);
      const mm = s.substring(10, 12);
      dateObj = new Date(Date.UTC(parseInt(yr, 10), parseInt(mo, 10) - 1, parseInt(dy, 10), parseInt(hh, 10), parseInt(mm, 10)));
    }
    // Caso 5: Formato HHMM (UTC) de hoy
    else if (s.length === 4 && /^\d{4}$/.test(s)) {
      const now = new Date();
      const hh = parseInt(s.substring(0, 2), 10);
      const mm = parseInt(s.substring(2, 4), 10);
      dateObj = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate(), hh, mm));
    } else {
      dateObj = new Date(s);
    }
  } else if (typeof dateInput === 'number') {
    dateObj = new Date(dateInput);
  }

  if (!dateObj || isNaN(dateObj.getTime())) {
    return String(dateInput);
  }

  try {
    const formatter = new Intl.DateTimeFormat('es-ES', {
      timeZone: 'Europe/Madrid',
      day: '2-digit',
      month: '2-digit',
      year: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
      hour12: false
    });

    const parts = formatter.formatToParts(dateObj);
    const partMap = {};
    parts.forEach(p => { partMap[p.type] = p.value; });

    return `${partMap.day}/${partMap.month}/${partMap.year} · ${partMap.hour}:${partMap.minute}`;
  } catch (err) {
    return dateObj.toLocaleString('es-ES', { timeZone: 'Europe/Madrid' });
  }
}

/**
 * Formatea sólo la hora a Europe/Madrid en formato 'HH:MM'
 * @param {Date|string|number} dateInput 
 * @returns {string} Ejemplo: '17:30'
 */
export function formatMadridTime(dateInput) {
  const full = formatMadridDateTime(dateInput);
  if (full.includes(' · ')) {
    return full.split(' · ')[1];
  }
  return full;
}

/**
 * Formatea un instante temporal de predicción en lenguaje natural en tiempo local (Europe/Madrid)
 * Ejemplo: "Miércoles, 23 de septiembre a las 16h" o "Jueves, 24 de septiembre a las 18:30 h"
 * @param {Date|string|number} dateInput 
 * @returns {string}
 */
export function formatPredictionInstant(dateInput) {
  if (!dateInput) return '';
  let dateObj = (dateInput instanceof Date) ? dateInput : new Date(dateInput);
  if (isNaN(dateObj.getTime())) return String(dateInput);

  try {
    const formatter = new Intl.DateTimeFormat('es-ES', {
      timeZone: 'Europe/Madrid',
      weekday: 'long',
      day: 'numeric',
      month: 'long',
      hour: 'numeric',
      minute: '2-digit',
      hour12: false
    });

    const parts = formatter.formatToParts(dateObj);
    const partMap = {};
    parts.forEach(p => { partMap[p.type] = p.value; });

    const weekday = partMap.weekday ? (partMap.weekday.charAt(0).toUpperCase() + partMap.weekday.slice(1)) : '';
    const day = partMap.day || '';
    const month = partMap.month || '';
    const hour = partMap.hour || '';
    const minute = partMap.minute || '00';

    const timeStr = (minute && minute !== '00') ? `${hour}:${minute} h` : `${hour}h`;
    return `${weekday}, ${day} de ${month} a las ${timeStr}`;
  } catch (err) {
    return dateObj.toLocaleString('es-ES', { timeZone: 'Europe/Madrid' });
  }
}

/**
 * Formatea la salida y estado del ciclo ECMWF IFS para la tarjeta de capa
 * Mostrando la run (0z, 6z, 12z, 18z) y, si está actualizando, hasta qué paso ha llegado.
 * @param {Object} metadata
 * @returns {string}
 */
export function formatEcmwfTimestamp(metadata) {
  if (!metadata) return 'Salida modelo: <strong>Sincronizando...</strong>';

  const availSteps = metadata.available_steps || [];
  const maxStep = metadata.max_step !== undefined ? metadata.max_step : (availSteps.length > 0 ? Math.max(...availSteps) : 0);

  // Extraer run limpia (0z, 6z, 12z, 18z)
  let run = metadata.run;
  if (!run && metadata.cycle_str) {
    const parts = metadata.cycle_str.split('_');
    if (parts.length > 1) run = parts[1].toLowerCase();
  }
  if (!run && metadata.cycle) {
    try {
      const d = new Date(metadata.cycle);
      const h = d.getUTCHours();
      run = `${h}z`;
    } catch (e) {}
  }
  if (run) {
    run = run.replace(/^0(\d)z$/, '$1z').toLowerCase();
  } else {
    run = '0z';
  }

  // Formatear fecha del ciclo en hora local (Europe/Madrid)
  let dateText = '';
  if (metadata.cycle) {
    try {
      const d = new Date(metadata.cycle);
      const day = String(d.getDate()).padStart(2, '0');
      const month = String(d.getMonth() + 1).padStart(2, '0');
      dateText = `${day}/${month}`;
    } catch (e) {
      dateText = metadata.cycle;
    }
  } else if (metadata.cycle_str) {
    const p = metadata.cycle_str.split('_')[0];
    if (p && p.length === 8) {
      dateText = `${p.substring(6, 8)}/${p.substring(4, 6)}`;
    } else {
      dateText = metadata.cycle_str;
    }
  }

  const isIntermediate = ['06z', '18z', '6z', '18z'].includes(run);
  const targetMax = isIntermediate ? 144 : 240;

  const rawMax = metadata.downloaded_max_step !== undefined
    ? metadata.downloaded_max_step
    : (metadata.raw_max_step !== undefined ? metadata.raw_max_step : null);

  const isUpdating = Boolean(
    metadata.is_updating ||
    metadata.is_syncing ||
    (rawMax !== null && rawMax < targetMax && metadata.status !== 'complete')
  );

  if (isUpdating && (rawMax === null || rawMax < targetMax)) {
    const stepText = (rawMax !== null && rawMax > 0) ? ` (+${rawMax}h)` : '';
    return `Salida modelo: <strong>${dateText} (${run})</strong> <span class="ecmwf-updating-tag" title="Descargando nueva salida del modelo progresivamente"><span class="sync-pulse-dot"></span> Actualizando${stepText}</span>`;
  }

  return `Salida modelo: <strong>${dateText} (${run})</strong>`;
}

/**
 * Formatea la salida y estado del ciclo NOAA GFS para la tarjeta de capa
 * Mostrando la run (0z, 6z, 12z, 18z) y, si está actualizando, hasta qué paso ha llegado (+384h).
 * @param {Object} metadata
 * @returns {string}
 */
export function formatGfsTimestamp(metadata) {
  if (!metadata) return 'Salida modelo: <strong>Sincronizando...</strong>';

  const availSteps = metadata.available_steps || [];
  const maxStep = metadata.max_step !== undefined ? metadata.max_step : (availSteps.length > 0 ? Math.max(...availSteps) : 0);

  // Extraer run limpia (0z, 6z, 12z, 18z)
  let run = metadata.run;
  if (!run && metadata.cycle_str) {
    const parts = metadata.cycle_str.split('_');
    if (parts.length > 1) run = parts[1].toLowerCase();
  }
  if (!run && metadata.cycle) {
    try {
      const d = new Date(metadata.cycle);
      const h = d.getUTCHours();
      run = `${h}z`;
    } catch (e) {}
  }
  if (run) {
    run = run.replace(/^0(\d)z$/, '$1z').toLowerCase();
  } else {
    run = '0z';
  }

  // Formatear fecha del ciclo en hora local (Europe/Madrid)
  let dateText = '';
  if (metadata.cycle) {
    try {
      const d = new Date(metadata.cycle);
      const day = String(d.getDate()).padStart(2, '0');
      const month = String(d.getMonth() + 1).padStart(2, '0');
      dateText = `${day}/${month}`;
    } catch (e) {
      dateText = metadata.cycle;
    }
  } else if (metadata.cycle_str) {
    const p = metadata.cycle_str.split('_')[0];
    if (p && p.length === 8) {
      dateText = `${p.substring(6, 8)}/${p.substring(4, 6)}`;
    } else {
      dateText = metadata.cycle_str;
    }
  }

  const rawMax = metadata.downloaded_max_step !== undefined
    ? metadata.downloaded_max_step
    : (metadata.raw_max_step !== undefined ? metadata.raw_max_step : null);

  const isUpdating = Boolean(
    metadata.is_updating ||
    metadata.is_syncing ||
    (rawMax !== null && rawMax < 384 && metadata.status !== 'complete')
  );

  if (isUpdating && (rawMax === null || rawMax < 384)) {
    const stepText = (rawMax !== null && rawMax > 0) ? ` (+${rawMax}h)` : '';
    return `Salida modelo: <strong>${dateText} (${run})</strong> <span class="ecmwf-updating-tag" title="Descargando nueva salida del modelo progresivamente"><span class="sync-pulse-dot"></span> Actualizando${stepText}</span>`;
  }

  return `Salida modelo: <strong>${dateText} (${run})</strong>`;
}

/**
 * Formatea el timestamp del modelo Météo-France AROME
 * @param {Object} metadata 
 * @returns {string}
 */
export function formatAromeTimestamp(metadata) {
  if (!metadata) return 'Salida modelo: <strong>Sincronizando...</strong>';

  const availSteps = metadata.available_steps || [];
  const maxStep = metadata.max_step !== undefined ? metadata.max_step : (availSteps.length > 0 ? Math.max(...availSteps) : 0);

  // Extraer corrida (00z, 03z, 06z, 09z, 12z, 15z, 18z, 21z)
  let run = metadata.run;
  if (!run && metadata.cycle_str) {
    const parts = metadata.cycle_str.split('_');
    if (parts.length > 1) run = parts[1].toLowerCase();
  }
  if (!run && metadata.cycle) {
    try {
      const d = new Date(metadata.cycle);
      const h = d.getUTCHours();
      run = `${h}z`;
    } catch (e) {}
  }
  if (run) {
    run = run.replace(/^0(\d)z$/, '$1z').toLowerCase();
  } else {
    run = '0z';
  }

  // Formatear fecha del ciclo en hora local (Europe/Madrid)
  let dateText = '';
  if (metadata.cycle) {
    try {
      const d = new Date(metadata.cycle);
      const day = String(d.getDate()).padStart(2, '0');
      const month = String(d.getMonth() + 1).padStart(2, '0');
      dateText = `${day}/${month}`;
    } catch (e) {
      dateText = metadata.cycle;
    }
  } else if (metadata.cycle_str) {
    const p = metadata.cycle_str.split('_')[0];
    if (p && p.length === 8) {
      dateText = `${p.substring(6, 8)}/${p.substring(4, 6)}`;
    } else {
      dateText = metadata.cycle_str;
    }
  }

  const rawMax = metadata.downloaded_max_step !== undefined
    ? metadata.downloaded_max_step
    : (metadata.raw_max_step !== undefined ? metadata.raw_max_step : null);

  const isUpdating = Boolean(
    metadata.is_updating ||
    metadata.is_syncing ||
    (rawMax !== null && rawMax < 48 && metadata.status !== 'complete')
  );

  if (isUpdating && (rawMax === null || rawMax < 48)) {
    const stepText = (rawMax !== null && rawMax > 0) ? ` (+${rawMax}h)` : '';
    return `Salida modelo: <strong>${dateText} (${run})</strong> <span class="ecmwf-updating-tag" title="Descargando nueva salida del modelo progresivamente"><span class="sync-pulse-dot"></span> Actualizando${stepText}</span>`;
  }

  return `Salida modelo: <strong>${dateText} (${run})</strong>`;
}

/**
 * Formatea el timestamp del modelo AEMET HARMONIE-AROME
 * @param {Object} metadata 
 * @returns {string}
 */
export function formatHarmonieTimestamp(metadata) {
  if (!metadata) return 'Salida modelo: <strong>Sincronizando...</strong>';

  const availSteps = metadata.available_steps || [];
  const maxStep = metadata.max_step !== undefined ? metadata.max_step : (availSteps.length > 0 ? Math.max(...availSteps) : 0);

  // Extraer corrida (00z, 06z, 12z, 18z)
  let run = metadata.run || metadata.run_id || metadata.cycle_str || '';
  if (typeof run === 'string' && run.includes('_')) {
    const parts = run.split('_');
    run = parts[parts.length - 1];
  }
  if (!run && metadata.cycle) {
    try {
      const d = new Date(metadata.cycle);
      const h = d.getUTCHours();
      run = `${h}z`;
    } catch (e) {}
  }
  if (run) {
    run = String(run).replace(/^20\d{6}_?/i, '');
    run = run.replace(/^0(\d)z$/i, '$1z').toLowerCase();
  } else {
    run = '0z';
  }

  // Formatear fecha del ciclo en hora local (Europe/Madrid)
  let dateText = '';
  if (metadata.cycle) {
    try {
      const d = new Date(metadata.cycle);
      const day = String(d.getDate()).padStart(2, '0');
      const month = String(d.getMonth() + 1).padStart(2, '0');
      dateText = `${day}/${month}`;
    } catch (e) {
      dateText = metadata.cycle;
    }
  } else if (metadata.cycle_str) {
    const p = metadata.cycle_str.split('_')[0];
    if (p && p.length === 8) {
      dateText = `${p.substring(6, 8)}/${p.substring(4, 6)}`;
    } else {
      dateText = metadata.cycle_str;
    }
  }

  const rawMax = metadata.downloaded_max_step !== undefined
    ? metadata.downloaded_max_step
    : (metadata.raw_max_step !== undefined ? metadata.raw_max_step : null);

  const isUpdating = Boolean(
    metadata.is_updating ||
    metadata.is_syncing ||
    (rawMax !== null && rawMax < 48 && metadata.status !== 'complete')
  );

  if (isUpdating && (rawMax === null || rawMax < 48)) {
    const stepText = (rawMax !== null && rawMax > 0) ? ` (+${rawMax}h)` : '';
    return `Salida modelo: <strong>${dateText} (${run})</strong> <span class="ecmwf-updating-tag" title="Descargando nueva salida del modelo progresivamente"><span class="sync-pulse-dot"></span> Actualizando${stepText}</span>`;
  }

  return `Salida modelo: <strong>${dateText} (${run})</strong>`;
}

/**
 * Formatea el timestamp del modelo DWD ICON-EU
 * @param {Object} metadata 
 * @returns {string}
 */
export function formatIconTimestamp(metadata) {
  if (!metadata) return 'Salida modelo: <strong>Sincronizando...</strong>';

  const availSteps = metadata.available_steps || [];
  const maxStep = metadata.max_step !== undefined ? metadata.max_step : (availSteps.length > 0 ? Math.max(...availSteps) : 0);

  // Extraer corrida (00z, 03z, 06z, 09z, 12z, 15z, 18z, 21z)
  let run = metadata.run;
  if (!run && metadata.cycle_str) {
    const parts = metadata.cycle_str.split('_');
    if (parts.length > 1) run = parts[1].toLowerCase();
  }
  if (!run && metadata.cycle) {
    try {
      const d = new Date(metadata.cycle);
      const h = d.getUTCHours();
      run = `${h}z`;
    } catch (e) {}
  }
  if (run) {
    run = run.replace(/^0(\d)z$/, '$1z').toLowerCase();
  } else {
    run = '0z';
  }

  // Formatear fecha del ciclo en hora local (Europe/Madrid)
  let dateText = '';
  if (metadata.cycle) {
    try {
      const d = new Date(metadata.cycle);
      const day = String(d.getDate()).padStart(2, '0');
      const month = String(d.getMonth() + 1).padStart(2, '0');
      dateText = `${day}/${month}`;
    } catch (e) {
      dateText = metadata.cycle;
    }
  } else if (metadata.cycle_str) {
    const p = metadata.cycle_str.split('_')[0];
    if (p && p.length === 8) {
      dateText = `${p.substring(6, 8)}/${p.substring(4, 6)}`;
    } else {
      dateText = metadata.cycle_str;
    }
  }

  const rawMax = metadata.downloaded_max_step !== undefined
    ? metadata.downloaded_max_step
    : (metadata.raw_max_step !== undefined ? metadata.raw_max_step : null);

  const isMainRun = ['00z', '06z', '12z', '18z', '0z', '6z', '12z', '18z'].includes(run);
  const targetMax = isMainRun ? 120 : 30;
  const isUpdating = Boolean(
    metadata.is_updating ||
    metadata.is_syncing ||
    (rawMax !== null && rawMax < targetMax && metadata.status !== 'complete')
  );

  if (isUpdating && (rawMax === null || rawMax < targetMax)) {
    const stepText = (rawMax !== null && rawMax > 0) ? ` (+${rawMax}h)` : '';
    return `Salida modelo: <strong>${dateText} (${run})</strong> <span class="ecmwf-updating-tag" title="Descargando nueva salida del modelo progresivamente"><span class="sync-pulse-dot"></span> Actualizando${stepText}</span>`;
  }

  return `Salida modelo: <strong>${dateText} (${run})</strong>`;
}

/**
 * Formatea el timestamp del modelo canadiense MSC GEM-GDPS
 * @param {Object} metadata 
 * @returns {string}
 */
export function formatGemTimestamp(metadata) {
  if (!metadata) return 'Salida modelo: <strong>Sincronizando...</strong>';

  const availSteps = metadata.available_steps || [];
  const maxStep = metadata.max_step !== undefined ? metadata.max_step : (availSteps.length > 0 ? Math.max(...availSteps) : 0);

  // Extraer corrida (00z, 12z)
  let run = metadata.run;
  if (!run && metadata.cycle_str) {
    const parts = metadata.cycle_str.split('_');
    if (parts.length > 1) run = parts[1].toLowerCase();
  }
  if (!run && metadata.cycle) {
    try {
      const d = new Date(metadata.cycle);
      const h = d.getUTCHours();
      run = `${h}z`;
    } catch (e) {}
  }
  if (run) {
    run = run.replace(/^0(\d)z$/, '$1z').toLowerCase();
  } else {
    run = '0z';
  }

  // Formatear fecha del ciclo en hora local (Europe/Madrid)
  let dateText = '';
  if (metadata.cycle) {
    try {
      const d = new Date(metadata.cycle);
      const day = String(d.getDate()).padStart(2, '0');
      const month = String(d.getMonth() + 1).padStart(2, '0');
      dateText = `${day}/${month}`;
    } catch (e) {
      dateText = metadata.cycle;
    }
  } else if (metadata.cycle_str) {
    const p = metadata.cycle_str.split('_')[0];
    if (p && p.length === 8) {
      dateText = `${p.substring(6, 8)}/${p.substring(4, 6)}`;
    } else {
      dateText = metadata.cycle_str;
    }
  }

  const rawMax = metadata.downloaded_max_step !== undefined
    ? metadata.downloaded_max_step
    : (metadata.raw_max_step !== undefined ? metadata.raw_max_step : null);

  const isUpdating = Boolean(
    metadata.is_updating ||
    metadata.is_syncing ||
    (rawMax !== null && rawMax < 240 && metadata.status !== 'complete')
  );

  if (isUpdating && (rawMax === null || rawMax < 240)) {
    const stepText = (rawMax !== null && rawMax > 0) ? ` (+${rawMax}h)` : '';
    return `Salida modelo: <strong>${dateText} (${run})</strong> <span class="ecmwf-updating-tag" title="Descargando nueva salida del modelo progresivamente"><span class="sync-pulse-dot"></span> Actualizando${stepText}</span>`;
  }

  return `Salida modelo: <strong>${dateText} (${run})</strong>`;
}

/**
 * Retorna la configuración de intervalos de antigüedad y estilos para una ventana de rayos dada en minutos.
 * @param {number} windowMinutes (1, 5, 15)
 */
export function getLightningAgeTiers(windowMinutes = 15) {
  const win = Math.min(15, Math.max(1, parseInt(windowMinutes, 10) || 15));
  if (win <= 1) {
    return {
      windowMinutes: 1,
      maxAgeSec: 60,
      tiers: [
        {
          id: 'tier1',
          maxSec: 20,
          label: '0-20s',
          fillColor: '#ffff00',
          color: '#000000',
          size: 11,
          radius: 2.8,
          weight: 0.9,
          fillOpacity: 1.0,
          pulse: true
        },
        {
          id: 'tier2',
          maxSec: 40,
          label: '20-40s',
          fillColor: '#ff9900',
          color: '#000000',
          size: 9,
          radius: 2.2,
          weight: 0.8,
          fillOpacity: 0.95,
          pulse: false
        },
        {
          id: 'tier3',
          maxSec: 60,
          label: '40-60s',
          fillColor: '#ef4444',
          color: '#000000',
          size: 7,
          radius: 1.8,
          weight: 0.7,
          fillOpacity: 0.90,
          pulse: false
        }
      ]
    };
  } else if (win <= 5) {
    return {
      windowMinutes: 5,
      maxAgeSec: 300,
      tiers: [
        {
          id: 'tier1',
          maxSec: 60,
          label: '0-1m',
          fillColor: '#ffff00',
          color: '#000000',
          size: 11,
          radius: 2.8,
          weight: 0.9,
          fillOpacity: 1.0,
          pulse: true
        },
        {
          id: 'tier2',
          maxSec: 180,
          label: '1-3m',
          fillColor: '#ff9900',
          color: '#000000',
          size: 9,
          radius: 2.2,
          weight: 0.8,
          fillOpacity: 0.95,
          pulse: false
        },
        {
          id: 'tier3',
          maxSec: 300,
          label: '3-5m',
          fillColor: '#ef4444',
          color: '#000000',
          size: 7,
          radius: 1.8,
          weight: 0.7,
          fillOpacity: 0.90,
          pulse: false
        }
      ]
    };
  } else {
    // 15 min por defecto
    return {
      windowMinutes: 15,
      maxAgeSec: 900,
      tiers: [
        {
          id: 'tier1',
          maxSec: 120,
          label: '0-2m',
          fillColor: '#ffff00',
          color: '#000000',
          size: 11,
          radius: 2.8,
          weight: 0.9,
          fillOpacity: 1.0,
          pulse: true
        },
        {
          id: 'tier2',
          maxSec: 420,
          label: '2-7m',
          fillColor: '#ff9900',
          color: '#000000',
          size: 9,
          radius: 2.2,
          weight: 0.8,
          fillOpacity: 0.95,
          pulse: false
        },
        {
          id: 'tier3',
          maxSec: 900,
          label: '7-15m',
          fillColor: '#ef4444',
          color: '#000000',
          size: 7,
          radius: 1.8,
          weight: 0.7,
          fillOpacity: 0.90,
          pulse: false
        }
      ]
    };
  }
}

/**
 * Obtiene el tier de estilo correspondiente a la antigüedad en segundos según la configuración de la ventana.
 * @param {number} ageSec
 * @param {Object} windowConfig
 * @returns {Object|null}
 */
export function getLightningTierForAge(ageSec, windowConfig) {
  if (!windowConfig || !Array.isArray(windowConfig.tiers)) return null;
  for (const tier of windowConfig.tiers) {
    if (ageSec <= tier.maxSec) {
      return tier;
    }
  }
  return null;
}






