/* SelenaCore service worker — RETIRED.
 *
 * Earlier the presence-detection /join invite flow registered an SW with
 * scope "/" to handle web-push. The dashboard does not benefit from any SW
 * caching, and a leftover registration intercepts requests on the kiosk
 * even with strict cache-control headers. This stub auto-unregisters on
 * the first install + skips claiming, so any browser still controlled by
 * a stale SW frees itself the next time it fetches /sw.js.
 */
self.addEventListener('install', function () {
  self.skipWaiting();
});

self.addEventListener('activate', function (event) {
  event.waitUntil(
    (async function () {
      try {
        const keys = await caches.keys();
        await Promise.all(keys.map(function (k) { return caches.delete(k); }));
      } catch (e) { /* ignore */ }
      try { await self.registration.unregister(); } catch (e) { /* ignore */ }
      try {
        const all = await self.clients.matchAll();
        all.forEach(function (c) { c.navigate(c.url).catch(function () { }); });
      } catch (e) { /* ignore */ }
    })()
  );
});
