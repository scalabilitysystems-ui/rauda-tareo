/* Service worker del panel del conductor.
 *
 * Regla: la API nunca se cachea (los toques se guardan en la cola del celular y se suben
 * cuando hay red). Las pantallas y los archivos se guardan para que la app abra igual sin
 * señal — si no, el conductor se queda mirando un error del navegador en la calle.
 */
const CACHE = 'rauda-conductor-v8';
const CAPARAZON = [
  '/conductor',
  '/conductor/hoy',
  '/conductor/app/conductor.css?v=11',
  '/conductor/app/conductor.js?v=11',
  '/conductor/manifest.webmanifest',
  '/conductor/app/icono-192.png',
];

self.addEventListener('install', (e) => {
  e.waitUntil(
    caches.open(CACHE)
      .then((c) => c.addAll(CAPARAZON))
      .then(() => self.skipWaiting())
      .catch(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys()
      .then((claves) => Promise.all(claves.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== 'GET' || url.pathname.startsWith('/api/')) return;

  // Red primero (para que las actualizaciones lleguen), caché como respaldo.
  e.respondWith(
    fetch(e.request)
      .then((respuesta) => {
        const copia = respuesta.clone();
        caches.open(CACHE).then((c) => c.put(e.request, copia)).catch(() => {});
        return respuesta;
      })
      .catch(async () => {
        const guardado = await caches.match(e.request);
        if (guardado) return guardado;
        return (await caches.match('/conductor/hoy')) || Response.error();
      })
  );
});
