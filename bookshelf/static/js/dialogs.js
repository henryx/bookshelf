// Modal dialogs (<dialog>): opened and closed by buttons and links, emptied
// again when closed (data-dialog-reset), and opened by content htmx swaps
// into them.
(function () {
  "use strict";

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
        // Opened from the account menu (About): the menu is done.
        var menu = opener.closest("details.avatar");
        if (menu) {
          menu.open = false;
        }
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

  // Content swapped into a closed dialog (e.g. a user's details) opens it,
  // but not suggestions: they may arrive after the dialog was closed. The
  // error banner is in no dialog (errors.js closes them instead).
  document.addEventListener("htmx:afterSwap", function (event) {
    var target = event.detail.target;
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
})();
