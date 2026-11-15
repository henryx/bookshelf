from flask import Blueprint, Response, current_app, render_template

__all__ = ["pwa_route"]

# Served without login, and before setup too (see setup.require_setup).
pwa_route = Blueprint("pwa", __name__)


@pwa_route.route("/sw.js", methods=("GET",))
def service_worker() -> Response:
    """
    The service worker (static/sw.js), served from the root: its scope is
    the directory it is served from. Revalidated on every load, so a new
    version is picked up at once.
    """
    response = current_app.send_static_file("sw.js")
    response.headers["Cache-Control"] = "no-cache"
    return response


@pwa_route.route("/offline", methods=("GET",))
def offline() -> str:
    """Shown by the service worker for pages it has no saved copy of."""
    return render_template("offline.html")
