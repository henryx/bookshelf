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

  // Dialogs: [data-dialog-open="<id>"] opens that <dialog> as a modal (a
  // link opener is not followed: its href is the no-JavaScript fallback);
  // [data-dialog-close] closes the dialog it is in. Escape also closes it.
  document.addEventListener("click", function (event) {
    var opener = event.target.closest("[data-dialog-open]");
    if (opener) {
      var dialog = document.getElementById(opener.dataset.dialogOpen);
      if (dialog) {
        event.preventDefault();
        if (!dialog.open) {
          dialog.showModal();
        }
      }
      return;
    }
    var closer = event.target.closest("[data-dialog-close]");
    if (closer) {
      var parent = closer.closest("dialog");
      if (parent) {
        parent.close();
      }
    }
  });

  // Repeatable rows (e.g. a book's authors): in a [data-rows] container,
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
    // The copy has hx-* attributes (author suggestions): let htmx see them.
    htmx.process(added);
    var input = added && added.querySelector("input");
    if (input) {
      input.focus();
    }
  });

  // Suggestions: a [data-suggest] field (an author's name or surname, the
  // publisher) asks the server for matches as you type, sending its text as
  // q instead of under its own form name (e.g. authors-0-name).
  document.addEventListener("htmx:configRequest", function (event) {
    var input = event.detail.elt;
    if (!(input instanceof HTMLInputElement) || !input.matches("[data-suggest]")) {
      return;
    }
    delete event.detail.parameters[input.name];
    event.detail.parameters.q = input.value;
  });

  // Picking an author from a field's <datalist> puts "Name Surname" in the
  // field; it is then split into the row's name and surname fields.
  function isAuthorField(el) {
    return el instanceof HTMLInputElement && el.matches("[data-author-field]");
  }

  document.addEventListener("input", function (event) {
    var input = event.target;
    if (!isAuthorField(input) || !input.list) {
      return;
    }
    var picked = Array.prototype.find.call(input.list.options, function (option) {
      return option.value === input.value;
    });
    if (!picked) {
      return;
    }
    var row = input.closest("[data-row]");
    row.querySelector('[data-author-field="name"]').value = picked.dataset.name;
    row.querySelector('[data-author-field="surname"]').value = picked.dataset.surname;
  });

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

  // A modal dialog would hide the error banner behind its backdrop.
  function closeDialogs() {
    document.querySelectorAll("dialog[open]").forEach(function (dialog) {
      dialog.close();
    });
  }

  // Content swapped into a closed dialog (e.g. a user's details) opens it;
  // an error swapped into the banner closes any open dialog.
  document.addEventListener("htmx:afterSwap", function (event) {
    var target = event.detail.target;
    if (target.id === "error-banner") {
      closeDialogs();
      return;
    }
    var dialog = target.closest("dialog");
    if (dialog && !dialog.open) {
      dialog.showModal();
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
