// Repeatable rows of a form, added and removed in the page.
(function () {
  "use strict";

  // Repeatable rows (a book's authors and categories): in a [data-rows] container,
  // [data-row-add] appends a copy of its <template data-row-template> with
  // "__index__" replaced by a number no other row uses, and
  // [data-row-remove] deletes the [data-row] it is in. The buttons also
  // submit the form, so without JavaScript the server does the same.
  function nextIndex(container) {
    var max = -1;
    container.querySelectorAll("[data-row]").forEach(function (row) {
      var index = parseInt(row.dataset.index, 10);
      if (index > max) {
        max = index;
      }
    });
    return max + 1;
  }

  document.addEventListener("click", function (event) {
    var adder = event.target.closest("[data-row-add]");
    var remover = event.target.closest("[data-row-remove]");
    var container = (adder || remover) && (adder || remover).closest("[data-rows]");
    if (!container) {
      return;
    }
    event.preventDefault();
    if (remover) {
      remover.closest("[data-row]").remove();
      return;
    }
    var template = container.querySelector("template[data-row-template]");
    var html = template.innerHTML.replace(/__index__/g, String(nextIndex(container)));
    template.insertAdjacentHTML("beforebegin", html);
    var added = template.previousElementSibling;
    // The copy has hx-* attributes (suggestions): let htmx see them.
    htmx.process(added);
    var input = added && added.querySelector("input");
    if (input) {
      input.focus();
    }
  });
})();
