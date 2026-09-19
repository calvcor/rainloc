/**
 * RainLoc - Gestor de Almacenamiento Local (localStorage)
 * Persistencia de preferencias del usuario: mapa base, opacidad y cuenca favorita.
 */

export class StorageManager {
  static STORAGE_KEY = 'rainloc_user_preferences_v1';

  /**
   * Obtiene el mapa base por defecto según el modo del sistema (claro u oscuro)
   */
  static getDefaultBasemapId() {
    const isDark = typeof window !== 'undefined' && window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
    return isDark ? 'esriDarkCanvas' : 'esriCanvas';
  }

  /**
   * Valores por defecto
   */
  static getDefaults() {
    return {
      basemapId: this.getDefaultBasemapId(),
      fillOpacity: 0.25,
      cuencasVisible: true,
      ccaaVisible: true,
      favoriteBasinId: null,
      favoriteBasinName: null,
      activeTab: 'realtime', // 'realtime' | 'prediction'
      activeLayers: {
        'radar': false,
        'saih_caudales': false,
        'saih_embalses': false,
        'saih_lluvias': false,
        'aemet_warnings': false,
        'arome_precip': false,
        'icon_d2': false,
        'ecmwf_ifs': false,
        'gfs_0p25': false
      },
      layerOpacities: {
        'radar': 0.75,
        'saih_caudales': 0.95,
        'saih_embalses': 0.95,
        'saih_lluvias': 0.95,
        'aemet_warnings': 0.85,
        'arome_precip': 0.75,
        'icon_d2': 0.70,
        'ecmwf_ifs': 0.65,
        'gfs_0p25': 0.65
      },
      radarMode: 'composite', // 'composite' | 'single'
      radarStationId: 'esbnv', // Estación por defecto (Valencia / Cullera)
      autoRefreshInterval: 180 // Segundos (180 = 3 min, 300 = 5 min, 0 = off)
    };
  }

  /**
   * Carga las preferencias guardadas o devuelve las predeterminadas
   */
  static load() {
    try {
      const raw = localStorage.getItem(this.STORAGE_KEY);
      if (!raw) return this.getDefaults();
      const parsed = JSON.parse(raw);
      return { ...this.getDefaults(), ...parsed };
    } catch (e) {
      console.warn('No se pudieron leer las preferencias de localStorage:', e);
      return this.getDefaults();
    }
  }

  /**
   * Guarda o actualiza campos específicos en localStorage
   * @param {Object} updates 
   */
  static save(updates) {
    try {
      const current = this.load();
      const updated = { ...current, ...updates };
      localStorage.setItem(this.STORAGE_KEY, JSON.stringify(updated));
      return updated;
    } catch (e) {
      console.warn('No se pudieron guardar las preferencias en localStorage:', e);
      return null;
    }
  }

  /**
   * Guarda el ID del mapa base activo
   * @param {string} basemapId 
   */
  static setBasemap(basemapId) {
    return this.save({ basemapId });
  }

  /**
   * Guarda la opacidad de relleno (número entre 0.0 y 1.0)
   * @param {number} fillOpacity 
   */
  static setOpacity(fillOpacity) {
    return this.save({ fillOpacity: parseFloat(fillOpacity) });
  }

  /**
   * Guarda la cuenca favorita
   * @param {string} id Identificador de la cuenca
   * @param {string} name Nombre legible
   */
  static setFavoriteBasin(id, name) {
    return this.save({ favoriteBasinId: id, favoriteBasinName: name });
  }

  /**
   * Elimina la cuenca favorita
   */
  static removeFavoriteBasin() {
    return this.save({ favoriteBasinId: null, favoriteBasinName: null });
  }

  /**
   * Guarda la pestaña activa del menú
   * @param {string} activeTab 'realtime' | 'prediction'
   */
  static setActiveTab(activeTab) {
    return this.save({ activeTab });
  }

  /**
   * Guarda el estado de activación de una capa
   * @param {string} layerId 
   * @param {boolean} active 
   */
  static setLayerActive(layerId, active) {
    const prefs = this.load();
    const activeLayers = { ...(prefs.activeLayers || {}), [layerId]: Boolean(active) };
    return this.save({ activeLayers });
  }

  /**
   * Guarda la opacidad de una capa específica
   * @param {string} layerId 
   * @param {number} opacity 
   */
  static setLayerOpacity(layerId, opacity) {
    const prefs = this.load();
    const layerOpacities = { ...(prefs.layerOpacities || {}), [layerId]: parseFloat(opacity) };
    return this.save({ layerOpacities });
  }

  /**
   * Guarda la visibilidad de la capa de cuencas
   * @param {boolean} visible 
   */
  static setCuencasVisible(visible) {
    return this.save({ cuencasVisible: Boolean(visible) });
  }

  /**
   * Guarda la visibilidad de los límites de CCAA
   * @param {boolean} visible 
   */
  static setCcaaVisible(visible) {
    return this.save({ ccaaVisible: Boolean(visible) });
  }

  /**
   * Guarda el modo de visualización de radar ('composite' | 'single')
   * @param {string} mode 
   */
  static setRadarMode(mode) {
    return this.save({ radarMode: mode });
  }

  /**
   * Guarda la estación individual de radar seleccionada (ej: 'esbnv')
   * @param {string} stationId 
   */
  static setRadarStationId(stationId) {
    return this.save({ radarStationId: stationId });
  }

  /**
   * Guarda el intervalo de autorrefresco en segundos (180, 300, etc.)
   * @param {number} intervalSec 
   */
  static setAutoRefreshInterval(intervalSec) {
    return this.save({ autoRefreshInterval: parseInt(intervalSec, 10) });
  }

  /**
   * Comprueba si una cuenca es la favorita
   * @param {string|number} id 
   */
  static isFavorite(id) {
    if (id === null || id === undefined) return false;
    const prefs = this.load();
    if (prefs.favoriteBasinId === null || prefs.favoriteBasinId === undefined) return false;
    return String(prefs.favoriteBasinId) === String(id);
  }
}

