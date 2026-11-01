import json
from dataclasses import asdict, dataclass

from bookshelf.utils import clean_nones

__all__ = ["IndexResponse", "KoResponse", "Responses"]


@dataclass(kw_only=True)
class Responses:
    """
    Responses is a dataclass that returns values to the client
    Attributes are:

    operation: it defines the operation called to the API
    result:    it returns result of the operation
    """

    operation: str
    result: str
    message: str | None = None

    def __str__(self):
        d = asdict(self)
        return json.dumps(clean_nones(d))


@dataclass
class IndexResponse(Responses):
    version: str = "0.0.0"


@dataclass
class KoResponse(Responses):
    def __init__(self, operation: str, message: str):
        super().__init__(operation=operation, result="ko", message=message)
