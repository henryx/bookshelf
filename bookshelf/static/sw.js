// Service worker, served at the app's root (/sw.js) so its scope is the
// whole app. Everything goes to the network first; the cache is only a
// fallback for when the server cannot be reached:
// - static files are kept as they are fetched (the core ones on install);
// - the collection and the catalogue, as last seen online, are shown again
//   read-only (app.js disables their forms; see markOffline);
// - any other page shows the offline page.
// The saved lists are one user's: they are deleted on login, logout and
// setup, and when a list redirects (the session has ended).
"use strict";

var STATIC_CACHE = "bookshelf-static-v1";
var PAGES_CACHE = "bookshelf-pages";

var root = new URL(self.registration.scope);
var OFFLINE_URL = new URL("offline", root).href;
var LISTS = [root.pathname, new URL("catalogue", root).pathname];
var SESSION_CHANGES = ["login", "logout", "setup"].map(function (path) {
  return new URL(path, root).pathname;
});
var STATIC_PATH = new URL("static/", root).pathname;
var PRECACHE = [
  "offline",
  "static/style.css",
  "static/htmx.min.js",
  "static/app.js",
  "static/fonts/fraunces-latin.woff2",
  "static/fonts/bricolage-grotesque-latin.woff2",
  "static/fonts/jetbrains-mono-latin.woff2",
  "static/icons/icon.svg",
].map(function (path) {
  return new URL(path, root).href;
});

self.addEventListener("install", function (event) {
  event.waitUntil(
    caches.open(STATIC_CACHE)
      .then(function (cache) {
        return cache.addAll(PRECACHE);
      })
      .then(function () {
        return self.skipWaiting();
      })
  );
});

// Drop the caches of earlier versions and take over the open pages.
self.addEventListener("activate", function (event) {
  event.waitUntil(
    caches.keys()
      .then(function (names) {
        return Promise.all(names.filter(function (name) {
          return name !== STATIC_CACHE && name !== PAGES_CACHE;
        }).map(function (name) {
          return caches.delete(name);
        }));
      })
      .then(function () {
        return self.clients.claim();
      })
  );
});

self.addEventListener("fetch", function (event) {
  var request = event.request;
  var url = new URL(request.url);
  if (url.origin !== root.origin) {
    return;
  }
  if (request.method === "POST" && SESSION_CHANGES.indexOf(url.pathname) !== -1) {
    // Someone else may be logging in: forget the lists, and let the request through.
    event.waitUntil(caches.delete(PAGES_CACHE));
    return;
  }
  if (request.method !== "GET") {
    return;
  }
  if (request.mode === "navigate") {
    event.respondWith(navigate(event));
  } else if (url.pathname.indexOf(STATIC_PATH) === 0) {
    event.respondWith(staticFile(event));
  }
});

function navigate(event) {
  var request = event.request;
  var isList = LISTS.indexOf(new URL(request.url).pathname) !== -1;
  return fetch(request).then(function (response) {
    if (isList) {
      event.waitUntil(saveList(request, response.clone()));
    }
    return response;
  }, function () {
    return (isList ? savedList(request) : Promise.resolve(undefined))
      .then(function (saved) {
        return saved ? markOffline(saved) : caches.match(OFFLINE_URL);
      })
      .then(function (response) {
        return response || Response.error();
      });
  });
}

// A redirect (opaque to the worker) means the session has ended: to the
// login page, or to setup on a new database.
function saveList(request, response) {
  if (response.type === "opaqueredirect") {
    return caches.delete(PAGES_CACHE);
  }
  if (!response.ok) {
    return Promise.resolve();
  }
  return caches.open(PAGES_CACHE).then(function (cache) {
    return cache.put(request.url, response);
  });
}

// The list as saved for this URL, else as saved with any search or page.
function savedList(request) {
  return caches.open(PAGES_CACHE).then(function (cache) {
    return cache.match(request.url).then(function (saved) {
      return saved || cache.match(request.url, { ignoreSearch: true });
    });
  });
}

// Mark a saved page as a copy for app.js: <body data-offline="<its Date header>">.
function markOffline(saved) {
  return saved.text().then(function (html) {
    var date = (saved.headers.get("Date") || "").replace(/[^\w ,:+-]/g, "");
    var headers = new Headers(saved.headers);
    headers.delete("Content-Length");
    return new Response(html.replace("<body>", '<body data-offline="' + date + '">'), {
      status: 200,
      headers: headers,
    });
  });
}

function staticFile(event) {
  var request = event.request;
  return fetch(request).then(function (response) {
    if (response.ok) {
      var copy = response.clone();
      event.waitUntil(caches.open(STATIC_CACHE).then(function (cache) {
        return cache.put(request, copy);
      }));
    }
    return response;
  }, function (error) {
    return caches.match(request).then(function (cached) {
      if (cached) {
        return cached;
      }
      throw error;
    });
  });
}
