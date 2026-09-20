/**
 * RainLoc - Aplicación Principal (Hito 1: Visor Hidrológico Base)
 * Coordinador de módulos: Mapa, Capa de Cuencas CHJ e Interfaz Reactiva.
 */

import { MapManager } from './map.js';
import { CuencasLayer } from './cuencas.js';
import { UIManager } from './ui.js';
import { LayerManager } from './layerManager.js';
import { StorageManager } from './storage.js';
import { MultiLayerInspector } from './multiLayerInspector.js';
import { PwaManager } from './pwa.js';

class RainLocApp {
  constructor() {
    this.mapManager = null;
    this.uiManager = null;
    this.cuencasLayer = null;
    this.layerManager = null;
  }

  /**
   * Inicialización global de la aplicación
   */
  async init() {
    console.log('🌧️ Iniciando RainLoc GIS - Monitorización de Cuencas Hidrográficas...');

    try {
      // 0. Inicializar Gestor de PWA y Service Worker
      PwaManager.init();

      // 1. Inicializar Gestor de UI
      this.uiManager = new UIManager();

      // 2. Inicializar Mapa Base Leaflet
      this.mapManager = new MapManager('map');
      this.mapManager.init();

      // 3. Inicializar Gestor de Capas Temáticas (Tiempo Real & Predicción)
      this.layerManager = new LayerManager(this.mapManager);
      this.layerManager.init();

      // 4. Conectar UI con el Mapa y el Gestor de Capas
      this.uiManager.init(this.mapManager, null, this.layerManager);

      // 5. Inicializar y cargar Capa de Cuencas y Subsistemas CHJ
      this.cuencasLayer = new CuencasLayer(this.mapManager, this.uiManager, this.layerManager);
      this.uiManager.setCuencasLayer(this.cuencasLayer);
      const loadResult = await this.cuencasLayer.load();

      // 6. Notificar a la UI del éxito de carga
      this.uiManager.setLoadedState(loadResult.featuresCount);
      console.log(`✅ Capa de cuencas cargada exitosamente: ${loadResult.featuresCount} geometrías desde ${loadResult.sourceUrl}`);

      // 7. Inicializar Inspector Multi-Capa Unificado en Hover
      this.multiInspector = new MultiLayerInspector(
        this.mapManager,
        this.cuencasLayer,
        this.layerManager,
        this.uiManager
      );
      this.multiInspector.init();

      // 8. Si el usuario tiene una cuenca favorita guardada en localStorage, enfocar automáticamente
      const savedPrefs = StorageManager.load();
      if (savedPrefs.favoriteBasinId) {
        console.log(`⭐ Centrando en cuenca favorita: ${savedPrefs.favoriteBasinName} (${savedPrefs.favoriteBasinId})`);
        this.cuencasLayer.focusOnBasin(savedPrefs.favoriteBasinId, false);
      }

      // 9. Exponer API en ventana para futuras extensiones (Hito 2: Radar OPERA / Hito 3: AROME)
      window.RainLoc = {
        map: this.mapManager.map,
        mapManager: this.mapManager,
        cuencasLayer: this.cuencasLayer,
        layerManager: this.layerManager,
        multiInspector: this.multiInspector,
        uiManager: this.uiManager,
        storageManager: StorageManager,
        version: '1.0.0'
      };

    } catch (error) {
      console.error('❌ Error al inicializar RainLoc:', error);
      if (this.uiManager) {
        this.uiManager.setErrorState('Error al cargar datos GeoJSON');
      }
    }
  }
}

// Arrancar cuando el DOM esté listo
document.addEventListener('DOMContentLoaded', () => {
  const app = new RainLocApp();
  app.init();
});
