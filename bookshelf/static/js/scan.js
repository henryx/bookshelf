// Scanning a book's barcode with the camera into the add-book form.
(function () {
  "use strict";

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
})();
