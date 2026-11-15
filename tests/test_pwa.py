"""The progressive web app: manifest, icons, service worker and offline page."""

import json
import struct
from pathlib import Path

from flask.testing import FlaskClient

STATIC = Path(__file__).parent.parent / "bookshelf" / "static"


def _png_size(path: Path) -> tuple[int, int]:
    """Width and height from a PNG's IHDR chunk."""
    header = path.read_bytes()[:24]
    assert header[:8] == b"\x89PNG\r\n\x1a\n", f"{path.name} is not a PNG"
    return struct.unpack(">II", header[16:24])


def test_manifest_describes_an_installable_app(client: FlaskClient):
    rv = client.get("/static/manifest.webmanifest")
    manifest = json.loads(rv.get_data(as_text=True))

    assert rv.status_code == 200
    assert manifest["name"] == "Bookshelf"
    assert manifest["display"] == "standalone"
    # Relative to the manifest (/static/): the app's root, wherever it is mounted.
    assert manifest["start_url"] == "../"
    assert manifest["scope"] == "../"
    sizes = {icon["sizes"] for icon in manifest["icons"]}
    assert {"192x192", "512x512"} <= sizes
    assert any(icon.get("purpose") == "maskable" for icon in manifest["icons"])


def test_manifest_icons_exist_with_their_declared_sizes():
    manifest = json.loads((STATIC / "manifest.webmanifest").read_text())

    for icon in manifest["icons"]:
        path = STATIC / icon["src"]
        if icon["type"] == "image/png":
            width, height = _png_size(path)
            assert icon["sizes"] == f"{width}x{height}"
        else:
            assert path.is_file()


def test_pages_link_the_manifest_and_service_worker(client: FlaskClient):
    html = client.get("/login").get_data(as_text=True)

    assert 'rel="manifest" href="/static/manifest.webmanifest"' in html
    assert 'data-service-worker="/sw.js"' in html
    assert '<meta name="theme-color"' in html
    assert 'rel="apple-touch-icon"' in html


def test_service_worker_is_served_from_the_root(client: FlaskClient):
    rv = client.get("/sw.js")

    assert rv.status_code == 200
    assert rv.mimetype == "text/javascript"
    # Checked on every load, so a new version is picked up at once.
    assert rv.headers["Cache-Control"] == "no-cache"
    assert "addEventListener" in rv.get_data(as_text=True)


def test_offline_page_needs_no_login(client: FlaskClient):
    rv = client.get("/offline")
    html = rv.get_data(as_text=True)

    assert rv.status_code == 200
    assert "offline" in html.lower()
