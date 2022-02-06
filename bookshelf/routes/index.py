import dataclasses
import json

from flask import Blueprint, Response

from bookshelf.utils import Config, clean_nones
from bookshelf.utils.mappers import IndexResponse

index_route = Blueprint("index_route", __name__)


@index_route.route("/", methods=("GET",))
def index() -> Response:
    """
    Index route. Return a JSON response with the application name
    :return: the response
    """

    cfg = Config()
    res = IndexResponse(
        operation="index", result="ok", message="Simple REST API", version=cfg.version
    )
    resp = dataclasses.asdict(res)

    return Response(
        json.dumps(clean_nones(resp)), status=200, mimetype="application/json"
    )
