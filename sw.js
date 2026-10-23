/* GRmarketcap: εφαρμογή (service worker).
   Πάντα δοκιμάζει πρώτα το δίκτυο, ώστε οι τιμές να είναι οι τελευταίες.
   Αν δεν υπάρχει internet, δείχνει ό,τι αποθηκεύτηκε την τελευταία φορά. */
var CACHE = "agora-v2";
var SHELL = ["./", "index.html", "manifest.webmanifest", "icons/icon-192.png", "icons/icon-512.png"];

self.addEventListener("install", function (e) {
  e.waitUntil(caches.open(CACHE).then(function (c) { return c.addAll(SHELL); }).then(function () { return self.skipWaiting(); }));
});

self.addEventListener("activate", function (e) {
  e.waitUntil(caches.keys().then(function (keys) {
    return Promise.all(keys.filter(function (k) { return k !== CACHE; }).map(function (k) { return caches.delete(k); }));
  }).then(function () { return self.clients.claim(); }));
});

self.addEventListener("fetch", function (e) {
  var req = e.request;
  if (req.method !== "GET") return;
  var url = new URL(req.url);
  if (url.origin !== self.location.origin) return; // Firebase, γραμματοσειρές κ.λπ. απευθείας
  e.respondWith(
    fetch(req).then(function (res) {
      if (res && res.ok) {
        var copy = res.clone();
        var key = url.origin + url.pathname; // χωρίς ?t=… ώστε να μένει ένα αντίγραφο ανά αρχείο
        caches.open(CACHE).then(function (c) { c.put(key, copy); });
      }
      return res;
    }).catch(function () {
      return caches.match(url.origin + url.pathname).then(function (r) {
        return r || caches.match("index.html");
      });
    })
  );
});
