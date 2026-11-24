"""
Scanning a book's barcode with the camera (js/scan.js): the camera is replaced by
a canvas stream painting an EAN-13 barcode, so ZXing reads a real image in
every browser; the browser's BarcodeDetector is removed, or replaced by a stub.
"""

import json

import pytest
from flask import Flask
from playwright.sync_api import ConsoleMessage, Page, Request, expect

from bookshelf.db import get_session
from bookshelf.models.books import NewBook, add_book
from bookshelf.models.users import User

DUNE_ISBN = "9780441172719"
NOT_A_BOOK = "4006381333931"  # a valid EAN-13, but not 977/978/979
SERIAL = "9770317847001"  # a serial's barcode: ISSN 0317-8471

# EAN-13 digit patterns (L, and R its complement; G is R reversed), and the
# L/G parity of the left half set by the first digit.
_L = ["0001101", "0011001", "0010011", "0111101", "0100011"]
_L += ["0110001", "0101111", "0111011", "0110111", "0001011"]
_PARITY = ["LLLLLL", "LLGLGG", "LLGGLG", "LLGGGL", "LGLLGG"]
_PARITY += ["LGGLLG", "LGGGLL", "LGLGLG", "LGLGGL", "LGGLGL"]


def _r(digit: str) -> str:
    return "".join("1" if bit == "0" else "0" for bit in _L[int(digit)])


def ean13_modules(code: str) -> str:
    """The barcode's 95 modules, 1 for a bar and 0 for a space."""
    left = "".join(
        _L[int(digit)] if parity == "L" else _r(digit)[::-1]
        for digit, parity in zip(code[1:7], _PARITY[int(code[0])], strict=True)
    )
    right = "".join(_r(digit) for digit in code[7:])
    return f"101{left}01010{right}101"


# getUserMedia gives a canvas painting the modules (none: a blank view);
# window.cameraTracks are the tracks it handed out.
_CAMERA = """
window.cameraTracks = [];
navigator.mediaDevices.getUserMedia = async function () {
  if (window.cameraError) {
    throw new DOMException("No camera for you", window.cameraError);
  }
  var canvas = document.createElement("canvas");
  canvas.width = 640;
  canvas.height = 480;
  var context = canvas.getContext("2d");
  function paint() {
    context.fillStyle = "#fff";
    context.fillRect(0, 0, canvas.width, canvas.height);
    context.fillStyle = "#000";
    var width = 4;
    var left = (canvas.width - MODULES.length * width) / 2;
    for (var i = 0; i < MODULES.length; i++) {
      if (MODULES[i] === "1") {
        context.fillRect(left + i * width, 140, width, 200);
      }
    }
  }
  paint();
  setInterval(paint, 100);  // a canvas stream sends frames as it is painted
  var stream = canvas.captureStream(10);
  window.cameraTracks.push.apply(window.cameraTracks, stream.getTracks());
  return stream;
};
"""

_NO_DETECTOR = "delete window.BarcodeDetector;"

# A BarcodeDetector that reads EAN-13 and finds RAW_VALUE in every frame.
_DETECTOR = """
window.BarcodeDetector = class {
  static async getSupportedFormats() { return ["ean_13", "qr_code"]; }
  constructor(options) { window.detectorFormats = options.formats; }
  async detect() { return [{ format: "ean_13", rawValue: RAW_VALUE }]; }
};
"""


def _camera(page: Page, code: str | None, detector: str = _NO_DETECTOR) -> None:
    modules = ean13_modules(code) if code else ""
    page.add_init_script(
        _CAMERA.replace("MODULES", json.dumps(modules))
        + detector.replace("RAW_VALUE", json.dumps(code))
    )
    page.reload()


def _open_scanner(page: Page) -> None:
    """
    Press Scan. Whether the scanner stays open is the test's to check: a
    barcode read from the first frame closes it at once.
    """
    page.get_by_role("link", name="Add book").click()
    expect(page.locator("#add-book-dialog")).to_be_visible()
    page.get_by_role("button", name="Scan").click()


def _expect_camera_stopped(page: Page) -> None:
    """
    Every track handed out ends (after the dialog's "close" event, a task
    later). A function, not an expression: Playwright evals an expression,
    which the app's CSP blocks.
    """
    page.wait_for_function(
        "() => window.cameraTracks.length > 0"
        " && window.cameraTracks.every(t => t.readyState === 'ended')",
        timeout=5_000,
    )


@pytest.fixture
def dune(db_app: Flask, user: User) -> None:
    """Dune is in the user's collection: looking its ISBN up says so, offline."""
    with db_app.app_context():
        add_book(get_session(), user.id, NewBook("Dune", isbn=DUNE_ISBN))


def test_zxing_reads_the_isbn_and_looks_it_up(reader_page: Page, dune):
    violations: list[str] = []

    def record(message: ConsoleMessage) -> None:
        if "Content Security Policy" in message.text:
            violations.append(message.text)

    reader_page.on("console", record)
    _camera(reader_page, DUNE_ISBN)

    with reader_page.expect_request("**/static/zxing/zxing.min.js"):
        _open_scanner(reader_page)

    expect(reader_page.locator("#book-scan-dialog")).to_be_hidden(timeout=10_000)
    expect(reader_page.get_by_label("ISBN")).to_have_value(DUNE_ISBN)
    expect(
        reader_page.get_by_text("“Dune” is already in your collection")
    ).to_be_visible()
    _expect_camera_stopped(reader_page)
    assert violations == []


def test_the_browser_reads_the_barcode_without_zxing(reader_page: Page, dune):
    loaded: list[str] = []

    def record(request: Request) -> None:
        if "zxing" in request.url:
            loaded.append(request.url)

    reader_page.on("request", record)
    _camera(reader_page, DUNE_ISBN, _DETECTOR)

    _open_scanner(reader_page)

    expect(
        reader_page.get_by_text("“Dune” is already in your collection")
    ).to_be_visible()
    expect(reader_page.get_by_label("ISBN")).to_have_value(DUNE_ISBN)
    assert reader_page.evaluate("window.detectorFormats") == ["ean_13"]
    assert loaded == []


@pytest.mark.parametrize(
    "detector", [_NO_DETECTOR, _DETECTOR], ids=["zxing", "browser"]
)
def test_a_serials_barcode_fills_the_field_and_asks_for_the_issue(
    reader_page: Page, detector: str
):
    looked_up: list[str] = []
    reader_page.on(
        "request",
        lambda request: (
            looked_up.append(request.url) if request.method == "POST" else None
        ),
    )
    _camera(reader_page, SERIAL, detector)

    _open_scanner(reader_page)

    expect(reader_page.locator("#book-scan-dialog")).to_be_hidden(timeout=10_000)
    field = reader_page.get_by_label("ISBN")
    expect(field).to_have_value(SERIAL)
    expect(field).to_be_focused()
    expect(reader_page.locator("[data-barcode-note]")).to_have_text(
        "ISSN 0317-8471: add the issue number after a space, if it has one."
    )
    # Not looked up: Google Books and Open Library hardly know serials.
    assert looked_up == []


def test_a_barcode_that_is_not_an_isbn_says_so(reader_page: Page):
    _camera(reader_page, NOT_A_BOOK)

    _open_scanner(reader_page)

    expect(reader_page.locator("#book-scan-dialog [role=status]")).to_have_text(
        "That barcode is not an ISBN or ISSN: try the one starting 977, 978 or 979.",
        timeout=10_000,
    )
    expect(reader_page.locator("#book-scan-dialog")).to_be_visible()
    expect(reader_page.get_by_label("ISBN")).to_have_value("")


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (
            "NotAllowedError",
            "Camera access was denied: allow it in the browser, or type the ISBN or ISSN.",
        ),
        ("NotFoundError", "No camera found: type the ISBN or ISSN."),
        ("NotReadableError", "The camera could not be started: type the ISBN or ISSN."),
    ],
)
def test_a_camera_that_cannot_be_used_says_why(reader_page: Page, error, message):
    _camera(reader_page, None)
    reader_page.evaluate(f"window.cameraError = {json.dumps(error)}")

    _open_scanner(reader_page)

    status = reader_page.locator("#book-scan-dialog [role=status]")
    expect(status).to_have_text(message)
    expect(status).to_have_class("scan-status error")


def test_frames_without_a_barcode_keep_the_console_quiet(reader_page: Page):
    messages: list[str] = []
    reader_page.on(
        "console",
        lambda m: messages.append(m.text) if m.type in ("warning", "error") else None,
    )
    _camera(reader_page, None)

    with reader_page.expect_response("**/static/zxing/zxing.min.js"):
        _open_scanner(reader_page)
    reader_page.wait_for_timeout(1_000)  # several frames read

    expect(reader_page.locator("#book-scan-dialog [role=status]")).to_have_text(
        "Point the camera at the barcode on the back cover."
    )
    assert messages == []


def test_closing_the_scanner_stops_the_camera(reader_page: Page):
    _camera(reader_page, None)
    _open_scanner(reader_page)
    expect(reader_page.locator("#book-scan-dialog [role=status]")).to_have_text(
        "Point the camera at the barcode on the back cover."
    )

    reader_page.locator("#book-scan-dialog").get_by_role("button", name="Close").click()

    expect(reader_page.locator("#book-scan-dialog")).to_be_hidden()
    expect(reader_page.locator("#add-book-dialog")).to_be_visible()
    _expect_camera_stopped(reader_page)


def test_escape_stops_the_camera(reader_page: Page):
    _camera(reader_page, None)
    _open_scanner(reader_page)
    expect(reader_page.locator("#book-scan-dialog video")).to_have_js_property(
        "paused", False
    )

    reader_page.keyboard.press("Escape")

    expect(reader_page.locator("#book-scan-dialog")).to_be_hidden()
    _expect_camera_stopped(reader_page)


def test_no_scan_button_without_a_camera(reader_page: Page):
    reader_page.add_init_script(
        "Object.defineProperty(navigator, 'mediaDevices', {value: undefined});"
    )
    reader_page.reload()

    reader_page.get_by_role("link", name="Add book").click()

    expect(reader_page.get_by_role("button", name="Look up")).to_be_visible()
    expect(reader_page.get_by_role("button", name="Scan")).to_be_hidden()
