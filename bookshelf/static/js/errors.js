// Error feedback for htmx requests. Server errors are swapped into
// #error-banner by the server (HX-Retarget); this handles the rest. Loaded
// first (base.html), so a new request clears the banner before anything else.
(function () {
  "use strict";

  function banner() {
    return document.getElementById("error-banner");
  }

  // Clear the previous error when a new request starts, but not for
  // suggestions ([data-suggest]): they are sent while typing, and would
  // clear an error before it is read.
  document.addEventListener("htmx:beforeRequest", function (event) {
    if (event.detail.elt.matches("[data-suggest]")) {
      return;
    }
    var el = banner();
    if (el) {
      el.textContent = "";
    }
  });

  // A modal dialog would hide the error banner behind its backdrop.
  function closeDialogs() {
    document.querySelectorAll("dialog[open]").forEach(function (dialog) {
      dialog.close();
    });
  }

  // An error swapped into the banner closes any open dialog.
  document.addEventListener("htmx:afterSwap", function (event) {
    if (event.detail.target.id === "error-banner") {
      closeDialogs();
    }
  });

  // No response at all (server down, network lost).
  document.addEventListener("htmx:sendError", function () {
    var el = banner();
    if (el) {
      closeDialogs();
      el.textContent = "Could not reach the server. Check your connection and try again.";
    }
  });
})();
