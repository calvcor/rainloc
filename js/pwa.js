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
   * Registra el Service Worker con estrategia de activación inmediata y fallback seguro
   */
  static _registerServiceWorker() {
    if (!('serviceWorker' in navigator)) {
      return;
    }

    let hasRegistered = false;
    const register = async () => {
      if (hasRegistered) return;
      hasRegistered = true;

      try {
        const registration = await navigator.serviceWorker.register('sw.js', { scope: './' });
        console.log('[PWA] Service Worker registrado con éxito en scope:', registration.scope);

        // Si se encuentra una nueva versión del Service Worker mientras la app está abierta
        registration.addEventListener('updatefound', () => {
          const newWorker = registration.installing;
          if (!newWorker) return;

          newWorker.addEventListener('statechange', () => {
            if (newWorker.state === 'installed' && navigator.serviceWorker.controller) {
              console.log('[PWA] Nueva versión de RainLoc lista. Activando...');
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
    };

    // Si el documento ya ha cargado, registrar inmediatamente; si no, esperar al evento o timeout
    if (document.readyState === 'complete' || document.readyState === 'interactive') {
      register();
    } else {
      window.addEventListener('load', register, { once: true });
      setTimeout(register, 1200);
    }

    // Cuando el nuevo Service Worker toma el control
    let refreshing = false;
    navigator.serviceWorker.addEventListener('controllerchange', () => {
      if (!refreshing) {
        refreshing = true;
        console.log('[PWA] Service Worker actualizado.');
      }
    });
  }

  /**
   * Captura el evento beforeinstallprompt para soporte de instalación en Chrome/Edge/Android
   */
  static _setupInstallPromptListener() {
    const showInstallBtn = () => {
      const btn = document.getElementById('btn-install-pwa');
      if (btn) {
        btn.style.display = 'inline-flex';
        btn.onclick = async () => {
          await PwaManager.promptInstall();
        };
      }
    };

    window.addEventListener('beforeinstallprompt', (e) => {
      e.preventDefault();
      PwaManager.deferredPrompt = e;
      console.log('[PWA] Evento beforeinstallprompt capturado. RainLoc es instalable en Chrome/Edge.');

      showInstallBtn();
      window.dispatchEvent(new CustomEvent('rainloc:installable', { detail: { prompt: e } }));
    });

    window.addEventListener('appinstalled', () => {
      PwaManager.deferredPrompt = null;
      PwaManager.isInstalled = true;
      const btn = document.getElementById('btn-install-pwa');
      if (btn) btn.style.display = 'none';
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
