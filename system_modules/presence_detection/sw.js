/* Service Worker for SelenaCore Presence Detection PWA */

self.addEventListener('install', function (event) {
    self.skipWaiting();
});

self.addEventListener('activate', function (event) {
    event.waitUntil(clients.claim());
});

self.addEventListener('push', function (event) {
    var data = { title: 'SelenaCore', body: 'New notification', data: {} };
    if (event.data) {
        try {
            data = event.data.json();
        } catch (e) {
            data.body = event.data.text();
        }
    }
    var options = {
        body: data.body || '',
        icon: '/api/ui/modules/presence-detection/icon.svg',
        badge: '/api/ui/modules/presence-detection/icon.svg',
        data: data.data || {},
        requireInteraction: false
    };
    event.waitUntil(
        self.registration.showNotification(data.title || 'SelenaCore', options)
    );
});

self.addEventListener('notificationclick', function (event) {
    event.notification.close();
    event.waitUntil(
        clients.matchAll({ type: 'window', includeUncontrolled: true }).then(function (clientList) {
            for (var i = 0; i < clientList.length; i++) {
                var client = clientList[i];
                if (client.url && 'focus' in client) {
                    return client.focus();
                }
            }
            return clients.openWindow('/');
        })
    );
});
