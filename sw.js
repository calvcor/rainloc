/**
 * RainLoc - Service Worker Inteligente (PWA)
 * Estrategia Network-First con Auto-Update para evitar atascos de caché
 */

const CACHE_NAME = 'rainloc-pwa-v3.7';

// Recursos críticos para el funcionamiento offline básico
const PRECACHE_ASSETS = [
  './',
  './index.html',
  './styles.css',
  './manifest.json',
  './manifest.webmanifest',
  './favicon.png',
  './favicon.jpeg',
  './favicon.ico',
  './apple-touch-icon.png',
  './icons/favicon-64.png',
  './icons/icon-192.png',
  './icons/icon-512.png',
  './icons/icon-maskable-192.png',
  './icons/icon-maskable-512.png',
  './icons/apple-touch-icon.png',
  './js/app.js',
  './js/config.js',
  './js/map.js',
  './js/ui.js',
  './js/layerManager.js',
  './js/cuencas.js',
  './js/storage.js',
  './js/pwa.js'
];

// 1. Instalación: Pre-cachear el App Shell y activar inmediatamente
self.addEventListener('install', (event) => {
  self.skipWaiting();
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      return cache.addAll(PRECACHE_ASSETS).catch((err) => {
        console.warn('[SW] Error en precacheo inicial (no crítico):', err);
      });
    })
  );
});

// 2. Activación: Limpieza de versiones antiguas de caché y control de clientes
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((cacheNames) => {
      return Promise.all(
        cacheNames
          .filter((name) => name.startsWith('rainloc-') && name !== CACHE_NAME)
          .map((name) => {
            console.log('[SW] Purgando caché obsoleta:', name);
            return caches.delete(name);
          })
      );
    }).then(() => self.clients.claim())
  );
});

// 3. Interceptación de peticiones (Fetch)
self.addEventListener('fetch', (event) => {
  const { request } = event;
  const url = new URL(request.url);

  // Ignorar peticiones no HTTP (chrome-extension, data:, etc.) y peticiones no GET
  if (request.method !== 'GET' || !request.url.startsWith('http')) {
    return;
  }

  // A. APIs de datos en tiempo real y teselas de mapas: NETWORK ONLY (sin caché local)
  // Las observaciones meteorológicas, radares, modelos y mapas deben ser siempre en directo
  const isApiRequest = url.pathname.includes('/api/v1/') || 
                       url.pathname.includes('/health') ||
                       url.hostname.includes('opendata.aemet.es') ||
                       url.hostname.includes('arcgisonline.com') ||
                       url.hostname.includes('tile.openstreetmap.org') ||
                       url.hostname.includes('cartocdn.com');

  if (isApiRequest) {
    event.respondWith(
      fetch(request).catch(() => {
        // En caso de fallo total de red en APIs, devolver respuesta vacía o error controlado
        return new Response(JSON.stringify({ error: 'offline', offline: true }), {
          headers: { 'Content-Type': 'application/json' },
          status: 503
        });
      })
    );
    return;
  }

  // B. Recursos estáticos de la aplicación (HTML, JS, CSS, Iconos): NETWORK-FIRST
  // Siempre intenta descargar la versión más fresca de la red. Si falla (offline), usa la caché.
  event.respondWith(
    fetch(request, { cache: 'no-cache' })
      .then((networkResponse) => {
        // Si la respuesta es válida, clonar y refrescar la copia en caché de fondo
        if (networkResponse && networkResponse.status === 200 && networkResponse.type === 'basic') {
          const responseToCache = networkResponse.clone();
          caches.open(CACHE_NAME).then((cache) => {
            cache.put(request, responseToCache);
          });
        }
        return networkResponse;
      })
      .catch(async () => {
        // Red no disponible -> recurrir a la caché
        const cachedResponse = await caches.match(request);
        if (cachedResponse) {
          return cachedResponse;
        }
        // Si es navegación a página principal y falla, servir index.html precacheado
        if (request.mode === 'navigate') {
          const fallback = await caches.match('./index.html');
          if (fallback) return fallback;
        }
        return new Response('Sin conexión a Internet', { status: 503, statusText: 'Offline' });
      })
  );
});

// 4. Mensajería con la aplicación cliente
self.addEventListener('message', (event) => {
  if (event.data && event.data.type === 'SKIP_WAITING') {
    self.skipWaiting();
  }
});
