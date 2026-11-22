// Error feedback for htmx requests. Server errors are swapped into
// #error-banner by the server (HX-Retarget); this handles the rest.
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

  // A <dialog data-dialog-reset> goes back to the content it was first
  // opened with when the user closes it (its close button or Escape), so it
  // opens empty again: e.g. the add-book form after a book was picked into
  // it. Closed by an error (closeDialogs), it keeps its content, to try
  // again. Saved on first opening, not on page load: by then a saved
  // offline page has disabled its forms.
  var pristine = new Map();

  function saveDialog(dialog) {
    if (dialog.hasAttribute("data-dialog-reset") && !pristine.has(dialog)) {
      pristine.set(dialog, dialog.innerHTML);
    }
  }

  function resetDialog(dialog) {
    if (!pristine.has(dialog)) {
      return;
    }
    dialog.innerHTML = pristine.get(dialog);
    // The copy has hx-* attributes (the form, suggestions): let htmx see them.
    htmx.process(dialog);
  }

  // Escape closes a modal dialog after a "cancel" event, which does not bubble.
  document.addEventListener("cancel", function (event) {
    if (event.target instanceof HTMLDialogElement) {
      resetDialog(event.target);
    }
  }, true);

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
          saveDialog(dialog);
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
        resetDialog(parent);
      }
    }
  });

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

  // Suggestions: a [data-suggest] field (an author's name or surname, a category, the
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

  // Picking a catalogue book from the add-book form's title or ISBN
  // <datalist> ([data-book-pick]) loads it into the form, read-only, to add
  // it to the collection: the form's [data-book-loader] fetches it. Only a
  // pick does: typing fires an InputEvent ("insertText"), while a pick fires
  // a plain Event (Chromium) or an "insertReplacementText" InputEvent. A
  // title several books share is not picked: their ISBN tells them apart.
  document.addEventListener("input", function (event) {
    var input = event.target;
    if (!(input instanceof HTMLInputElement) || !input.matches("[data-book-pick]") || !input.list) {
      return;
    }
    if (event instanceof InputEvent && event.inputType !== "insertReplacementText") {
      return;
    }
    var matches = Array.prototype.filter.call(input.list.options, function (option) {
      return option.value === input.value && option.dataset.bookId;
    });
    var loader = input.form && input.form.querySelector("[data-book-loader]");
    if (matches.length !== 1 || !loader) {
      return;
    }
    loader.setAttribute("hx-vals", JSON.stringify({ book: matches[0].dataset.bookId }));
    htmx.trigger(loader, "book-picked");
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

  // Content swapped into a closed dialog (e.g. a user's details) opens it,
  // but not suggestions: they may arrive after the dialog was closed.
  // An error swapped into the banner closes any open dialog.
  document.addEventListener("htmx:afterSwap", function (event) {
    var target = event.detail.target;
    if (target.id === "error-banner") {
      closeDialogs();
      return;
    }
    // detail.elt is the target here; the element that asked is in requestConfig.
    var source = event.detail.requestConfig && event.detail.requestConfig.elt;
    if (source && source.matches("[data-suggest]")) {
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
