// Error feedback for htmx requests. Server errors are swapped into
// #error-banner by the server (HX-Retarget); this handles the rest.
(function () {
  "use strict";

  function banner() {
    return document.getElementById("error-banner");
  }

  // Clear the previous error when a new request starts.
  document.addEventListener("htmx:beforeRequest", function () {
    var el = banner();
    if (el) {
      el.textContent = "";
    }
  });

  // No response at all (server down, network lost).
  document.addEventListener("htmx:sendError", function () {
    var el = banner();
    if (el) {
      el.textContent = "Could not reach the server. Check your connection and try again.";
    }
  });
})();
