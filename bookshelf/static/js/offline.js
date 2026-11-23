// The service worker, and the saved copies of pages it serves offline.
(function () {
  "use strict";

  function banner() {
    return document.getElementById("error-banner");
  }

  // The service worker (sw.js): installable app, and pages to read offline.
  var worker = document.documentElement.dataset.serviceWorker;
  if (worker && "serviceWorker" in navigator) {
    navigator.serviceWorker.register(worker).catch(function (error) {
      console.error("Service worker registration failed", error);
    });
  }

  // A saved copy of a list, served by the service worker without a
  // connection (<body data-offline="<when it was saved>">): read-only, its
  // forms disabled and htmx requests not sent. Reloaded once back online.
  function offlineMessage(saved) {
    var date = new Date(saved);
    var when = isNaN(date.getTime()) ? "" : " from " + date.toLocaleString();
    return "You're offline: this is a saved copy" + when +
      ". Changes need a connection.";
  }

  if (document.body.hasAttribute("data-offline")) {
    var message = offlineMessage(document.body.dataset.offline);
    banner().textContent = message;
    document.querySelectorAll("form input, form select, form textarea, form button")
      .forEach(function (control) {
        control.disabled = true;
      });
    document.addEventListener("htmx:beforeRequest", function (event) {
      event.preventDefault();
      banner().textContent = message;
    });
    window.addEventListener("online", function () {
      window.location.reload();
    });
  }
})();
