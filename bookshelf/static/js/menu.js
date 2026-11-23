// The account menu in the top bar.
(function () {
  "use strict";

  // The account menu (<details class="avatar">) opens and closes on its own
  // summary without JavaScript; this also closes it on a click outside it,
  // or on Escape (giving the focus back to its summary).
  function openMenus() {
    return document.querySelectorAll("details.avatar[open]");
  }

  document.addEventListener("click", function (event) {
    openMenus().forEach(function (menu) {
      if (!menu.contains(event.target)) {
        menu.open = false;
      }
    });
  });

  document.addEventListener("keydown", function (event) {
    if (event.key !== "Escape") {
      return;
    }
    openMenus().forEach(function (menu) {
      menu.open = false;
      menu.querySelector("summary").focus();
    });
  });
})();
