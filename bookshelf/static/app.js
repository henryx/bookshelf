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

  // Suggestions: a [data-suggest] field (role="combobox": a book's title or
  // ISBN, an author's name or surname, a category, the publisher) asks the
  // server for matches as you type, sending its text as q instead of under
  // its own form name (e.g. authors-0-name).
  document.addEventListener("htmx:configRequest", function (event) {
    var input = event.detail.elt;
    if (!(input instanceof HTMLInputElement) || !input.matches("[data-suggest]")) {
      return;
    }
    delete event.detail.parameters[input.name];
    event.detail.parameters.q = input.value;
  });

  // The matches come back as the options of the field's listbox (its
  // aria-controls), shown under it while it has the focus. Up and Down move
  // through them, Enter or a click picks one, Escape or leaving the field
  // closes the list. Our own list, not a <datalist>: Firefox opens a
  // datalist's dropdown only for the options it holds when a key is
  // pressed, not for those the server sends a moment later.
  function suggestionList(input) {
    return document.getElementById(input.getAttribute("aria-controls"));
  }

  function suggestionField(list) {
    return document.querySelector('[aria-controls="' + list.id + '"]');
  }

  function suggestions(list) {
    return Array.prototype.slice.call(list.querySelectorAll('[role="option"]'));
  }

  function setActive(input, option) {
    suggestions(suggestionList(input)).forEach(function (each) {
      each.setAttribute("aria-selected", String(each === option));
    });
    if (option) {
      input.setAttribute("aria-activedescendant", option.id);
      option.scrollIntoView({ block: "nearest" });
    } else {
      input.removeAttribute("aria-activedescendant");
    }
  }

  function showSuggestions(input, shown) {
    var list = suggestionList(input);
    if (!list) {
      return;
    }
    list.hidden = !shown;
    input.setAttribute("aria-expanded", String(shown));
    if (!shown) {
      setActive(input, null);
    }
  }

  function moveSuggestion(input, step) {
    var list = suggestionList(input);
    var options = suggestions(list);
    if (!options.length) {
      return;
    }
    showSuggestions(input, true);
    var index = options.indexOf(list.querySelector('[aria-selected="true"]'));
    var next = index < 0 ? (step > 0 ? 0 : options.length - 1)
      : (index + step + options.length) % options.length;
    setActive(input, options[next]);
  }

  // What picking an option does besides filling the field: an author fills
  // both fields of the row; a catalogue book ([data-book-pick]) is loaded
  // into the form, read-only, to add it to the collection (the form's
  // [data-book-loader] fetches it).
  function pickSuggestion(input, option) {
    input.value = option.dataset.value;
    showSuggestions(input, false);
    if (input.matches("[data-author-field]")) {
      var row = input.closest("[data-row]");
      row.querySelector('[data-author-field="name"]').value = option.dataset.name;
      row.querySelector('[data-author-field="surname"]').value = option.dataset.surname;
    }
    var loader = input.form && input.form.querySelector("[data-book-loader]");
    if (input.matches("[data-book-pick]") && option.dataset.bookId && loader) {
      loader.setAttribute("hx-vals", JSON.stringify({ book: option.dataset.bookId }));
      htmx.trigger(loader, "book-picked");
    }
  }

  document.addEventListener("htmx:afterSwap", function (event) {
    var list = event.detail.target;
    if (!list.matches('[role="listbox"]')) {
      return;
    }
    var input = suggestionField(list);
    if (!input) {
      return;
    }
    var options = suggestions(list);
    options.forEach(function (option, index) {
      option.id = list.id + "-" + index;
      option.setAttribute("aria-selected", "false");
    });
    showSuggestions(input, options.length > 0 && document.activeElement === input);
  });

  document.addEventListener("keydown", function (event) {
    var input = event.target;
    if (!(input instanceof HTMLInputElement) || !input.matches("[data-suggest]")) {
      return;
    }
    var list = suggestionList(input);
    var open = list && !list.hidden;
    var active = open && list.querySelector('[aria-selected="true"]');
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      moveSuggestion(input, event.key === "ArrowDown" ? 1 : -1);
    } else if (event.key === "Enter" && active) {
      // Picks the option instead of submitting the form.
      event.preventDefault();
      pickSuggestion(input, active);
    } else if (event.key === "Escape" && open) {
      // Closes the list, not the dialog the field is in.
      event.preventDefault();
      showSuggestions(input, false);
    }
  });

  // Typing changes the text the options were for: none is chosen any more.
  document.addEventListener("input", function (event) {
    var input = event.target;
    if (input instanceof HTMLInputElement && input.matches("[data-suggest]") && suggestionList(input)) {
      setActive(input, null);
    }
  });

  document.addEventListener("focusout", function (event) {
    var input = event.target;
    if (input instanceof HTMLInputElement && input.matches("[data-suggest]")) {
      showSuggestions(input, false);
    }
  });

  // Pressing an option would take the focus from the field, closing the list.
  document.addEventListener("mousedown", function (event) {
    if (event.target.closest('.suggestions [role="option"]')) {
      event.preventDefault();
    }
  });

  document.addEventListener("click", function (event) {
    var option = event.target.closest('.suggestions [role="option"]');
    var input = option && suggestionField(option.closest('[role="listbox"]'));
    if (input) {
      pickSuggestion(input, option);
    }
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

  // Barcode scanning: the add form's Scan button ([data-barcode-scan]) opens
  // its dialog (aria-controls), whose camera view reads the book's EAN-13
  // (an ISBN-13: 978 or 979) into the ISBN field (data-barcode-input) and
  // presses Look up. The browser's own BarcodeDetector reads it where it
  // can; elsewhere ZXing (data-barcode-decoder), loaded on first use.
  // Closing the dialog (or a found barcode) stops the camera. The button
  // shows only where a camera can be asked for (<html data-can-scan>):
  // over HTTPS (or on localhost), in a browser with getUserMedia.
  var SCAN_INTERVAL = 150; // milliseconds between two frames read
  var BOOK_EAN = /^97[89]\d{10}$/;
  var scanning = null; // the open dialog's session: {dialog, video, stream, timer, active}
  var decoders = new Map(); // ZXing's URL -> a promise of its decode function

  if (window.isSecureContext && navigator.mediaDevices &&
      navigator.mediaDevices.getUserMedia) {
    document.documentElement.dataset.canScan = "";
  }

  function scanStatus(dialog, text, isError) {
    var status = dialog.querySelector("[data-barcode-status]");
    status.textContent = text;
    status.className = "scan-status " + (isError ? "error" : "hint");
  }

  function cameraMessage(error) {
    switch (error && error.name) {
      case "NotAllowedError":
      case "SecurityError":
        return "Camera access was denied: allow it in the browser, or type the ISBN.";
      case "NotFoundError":
      case "OverconstrainedError":
        return "No camera found: type the ISBN.";
      default:
        return "The camera could not be started: type the ISBN.";
    }
  }

  // The browser's decoder, or null where it has none for EAN-13.
  function nativeDecoder() {
    if (!("BarcodeDetector" in window)) {
      return Promise.resolve(null);
    }
    return window.BarcodeDetector.getSupportedFormats().then(function (formats) {
      if (formats.indexOf("ean_13") < 0) {
        return null;
      }
      var detector = new window.BarcodeDetector({ formats: ["ean_13"] });
      return function (canvas) {
        return detector.detect(canvas).then(function (codes) {
          return codes.map(function (code) { return code.rawValue; });
        });
      };
    }, function (error) {
      console.warn("BarcodeDetector unusable, using ZXing instead", error);
      return null;
    });
  }

  function loadScript(src) {
    return new Promise(function (resolve, reject) {
      var script = document.createElement("script");
      script.src = src;
      script.addEventListener("load", resolve);
      script.addEventListener("error", function () {
        script.remove();
        reject(new Error("Could not load " + src));
      });
      document.head.appendChild(script);
    });
  }

  function zxingDecoder(src) {
    return loadScript(src).then(function () {
      var ZXing = window.ZXing;
      // The EAN-13 reader itself: MultiFormatReader warns in the console
      // for every frame without a barcode. TRY_HARDER also reads it sideways.
      var reader = new ZXing.EAN13Reader();
      var hints = new Map([[ZXing.DecodeHintType.TRY_HARDER, true]]);
      return function (canvas) {
        var source = new ZXing.HTMLCanvasElementLuminanceSource(canvas);
        var bitmap = new ZXing.BinaryBitmap(new ZXing.HybridBinarizer(source));
        try {
          return Promise.resolve([reader.decode(bitmap, hints).getText()]);
        } catch (error) {
          // No barcode in this frame, or one only partly read.
          if (error instanceof ZXing.NotFoundException ||
              error instanceof ZXing.ChecksumException ||
              error instanceof ZXing.FormatException) {
            return Promise.resolve([]);
          }
          return Promise.reject(error);
        }
      };
    });
  }

  function decoder(src) {
    if (!decoders.has(src)) {
      var decode = nativeDecoder().then(function (native) {
        return native || zxingDecoder(src);
      });
      // Not kept when it failed, so the next scan tries again.
      decode.catch(function () { decoders.delete(src); });
      decoders.set(src, decode);
    }
    return decoders.get(src);
  }

  function stopScan() {
    if (!scanning) {
      return;
    }
    scanning.active = false;
    clearTimeout(scanning.timer);
    if (scanning.stream) {
      scanning.stream.getTracks().forEach(function (track) { track.stop(); });
    }
    scanning.video.pause();
    scanning.video.srcObject = null;
    scanning = null;
  }

  function scanFailed(session, message, error) {
    console.error("Barcode scanning failed", error);
    if (session.active) {
      scanStatus(session.dialog, message, true);
      stopScan();
    }
  }

  function scanned(session, isbn) {
    var input = document.getElementById(session.dialog.dataset.barcodeInput);
    // Now, not on the "close" event: Look up swaps the form, and the dialog
    // in it, out of the page, and an event on it no longer reaches document.
    stopScan();
    session.dialog.close();
    input.value = isbn;
    var lookUp = input.form && input.form.querySelector('[name="lookup"]');
    if (lookUp) {
      lookUp.click();
    }
  }

  // Reads a frame every SCAN_INTERVAL, one at a time, until a book's barcode.
  function readFrames(session, decode) {
    var canvas = document.createElement("canvas");
    var context = canvas.getContext("2d", { willReadFrequently: true });

    function next() {
      session.timer = setTimeout(frame, SCAN_INTERVAL);
    }

    function frame() {
      if (!session.active) {
        return;
      }
      var video = session.video;
      if (video.readyState < video.HAVE_CURRENT_DATA || !video.videoWidth) {
        next();
        return;
      }
      canvas.width = video.videoWidth;
      canvas.height = video.videoHeight;
      context.drawImage(video, 0, 0);
      decode(canvas).then(function (codes) {
        if (!session.active) {
          return;
        }
        var isbn = codes.find(function (code) { return BOOK_EAN.test(code); });
        if (isbn) {
          scanned(session, isbn);
          return;
        }
        if (codes.length) {
          scanStatus(session.dialog,
            "That barcode is not an ISBN: try the one with 978 or 979.", false);
        }
        next();
      }, function (error) {
        scanFailed(session, "The barcode could not be read: type the ISBN.", error);
      });
    }

    frame();
  }

  function startScan(dialog) {
    stopScan();
    var session = {
      dialog: dialog,
      video: dialog.querySelector("video"),
      stream: null,
      timer: null,
      active: true,
    };
    scanning = session;
    scanStatus(dialog, "Starting the camera…", false);
    navigator.mediaDevices.getUserMedia({
      video: { facingMode: { ideal: "environment" }, width: { ideal: 1280 } },
      audio: false,
    }).then(function (stream) {
      session.stream = stream;
      if (!session.active) {
        // Closed while the browser asked for the camera.
        stream.getTracks().forEach(function (track) { track.stop(); });
        return null;
      }
      session.video.srcObject = stream;
      return session.video.play();
    }).then(function () {
      if (!session.active) {
        return;
      }
      scanStatus(dialog, "Point the camera at the barcode on the back cover.", false);
      decoder(dialog.dataset.barcodeDecoder).then(function (decode) {
        if (session.active) {
          readFrames(session, decode);
        }
      }, function (error) {
        scanFailed(session, "The barcode reader could not be loaded: type the ISBN.", error);
      });
    }, function (error) {
      scanFailed(session, cameraMessage(error), error);
    });
  }

  document.addEventListener("click", function (event) {
    var button = event.target.closest("[data-barcode-scan]");
    if (!button) {
      return;
    }
    var dialog = document.getElementById(button.getAttribute("aria-controls"));
    if (dialog && !dialog.open) {
      dialog.showModal();
      startScan(dialog);
    }
  });

  // However the scanner closes (its button, Escape, an error elsewhere),
  // the camera stops. "close" does not bubble.
  document.addEventListener("close", function (event) {
    if (scanning && event.target === scanning.dialog) {
      stopScan();
    }
  }, true);

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
