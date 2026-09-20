/**
 * RainLoc - Gestor de Ciclo de Vida PWA e Instalación
 */

export class PwaManager {
  static deferredPrompt = null;
  static isInstalled = false;

  /**
   * Inicializa el Service Worker y los escuchadores de instalación PWA
   */
  static init() {
    this._registerServiceWorker();
    this._setupInstallPromptListener();
    this._checkStandaloneMode();
  }

  /**
   * Registra el Service Worker con estrategia de actualización inmediata
   */
  static _registerServiceWorker() {
    if (!('serviceWorker' in navigator)) {
      return;
    }

    window.addEventListener('load', async () => {
      try {
        const registration = await navigator.serviceWorker.register('./sw.js', { scope: './' });

        // Si se encuentra una nueva versión del Service Worker mientras la app está abierta
        registration.addEventListener('updatefound', () => {
          const newWorker = registration.installing;
          if (!newWorker) return;

          newWorker.addEventListener('statechange', () => {
            if (newWorker.state === 'installed' && navigator.serviceWorker.controller) {
              console.log('[PWA] Nueva versión de RainLoc lista. Activando...');
              // Enviar mensaje para que el nuevo SW tome el control sin esperar
              newWorker.postMessage({ type: 'SKIP_WAITING' });
            }
          });
        });

        // Comprobar si hay actualizaciones periódicamente (cada 30 min)
        setInterval(() => {
          registration.update().catch(() => {});
        }, 30 * 60 * 1000);

      } catch (err) {
        console.warn('[PWA] Error al registrar el Service Worker:', err);
      }
    });

    // Cuando el nuevo Service Worker toma el control, recargar limpiamente si es necesario
    let refreshing = false;
    navigator.serviceWorker.addEventListener('controllerchange', () => {
      if (!refreshing) {
        refreshing = true;
        console.log('[PWA] Service Worker actualizado. Aplicación al día.');
      }
    });
  }

  /**
   * Captura el evento beforeinstallprompt para soporte de instalación en Chrome/Edge/Android
   */
  static _setupInstallPromptListener() {
    window.addEventListener('beforeinstallprompt', (e) => {
      // Evitar que el navegador muestre su banner automático si queremos controlarlo
      e.preventDefault();
      PwaManager.deferredPrompt = e;
      console.log('[PWA] Evento beforeinstallprompt capturado. RainLoc es instalable.');

      // Disparar evento personalizado por si la UI desea mostrar un botón de "Instalar App"
      window.dispatchEvent(new CustomEvent('rainloc:installable', { detail: { prompt: e } }));
    });

    window.addEventListener('appinstalled', () => {
      PwaManager.deferredPrompt = null;
      PwaManager.isInstalled = true;
      console.log('[PWA] RainLoc se ha instalado correctamente en el dispositivo.');
    });
  }

  /**
   * Comprueba si la aplicación se está ejecutando en modo standalone (PWA instalada)
   */
  static _checkStandaloneMode() {
    const isStandalone = window.matchMedia('(display-mode: standalone)').matches || 
                         window.navigator.standalone === true;
    if (isStandalone) {
      document.body.classList.add('pwa-standalone');
      console.log('[PWA] Ejecutándose en modo Standalone (App nativa/PWA).');
    }
  }

  /**
   * Muestra el diálogo de instalación nativo si está disponible
   */
  static async promptInstall() {
    if (!PwaManager.deferredPrompt) {
      return false;
    }
    PwaManager.deferredPrompt.prompt();
    const choiceResult = await PwaManager.deferredPrompt.userChoice;
    PwaManager.deferredPrompt = null;
    return choiceResult.outcome === 'accepted';
  }
}
