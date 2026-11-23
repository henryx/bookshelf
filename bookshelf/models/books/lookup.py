"""Looking a book up online by its ISBN: Google Books, then Open Library."""

import html
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from importlib.metadata import version

import requests

from bookshelf.models.books.model import Author

GOOGLE_BOOKS_URL = "https://www.googleapis.com/books/v1/volumes"
OPEN_LIBRARY_URL = "https://openlibrary.org/api/books"
# Seconds to wait for each service: both in turn stay well within a request.
_TIMEOUT = 5
_HEADERS = {"User-Agent": f"Bookshelf/{version('bookshelf')}"}
_YEAR = re.compile(r"\b\d{4}\b")
# A description's line and paragraph ends, and its other tags.
_BREAK = re.compile(r"<(?:br|/p|/div|/li)\b[^>]*>", re.IGNORECASE)
_TAG = re.compile(r"<[^>]*>")
_SPACES = re.compile(r"\s+")

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FoundBook:
    """A book as an online catalogue (`source`) has it; None or () for what it lacks."""

    source: str
    title: str | None
    authors: tuple[Author, ...]
    publisher: str | None
    publication_year: int | None
    plot: str | None
    categories: tuple[str, ...]


class BookLookupError(Exception):
    """A service could not be asked, and no other had the book."""

    def __init__(self, isbn: str, failed: list[str]) -> None:
        super().__init__(
            f"Could not reach {' or '.join(failed)} to look up ISBN {isbn}. "
            "Try again later, or fill in the book yourself."
        )
        self.isbn = isbn


def look_up_isbn(isbn: str) -> FoundBook | None:
    """
    The book with this ISBN-13 as Google Books has it, else as Open Library
    has it; None when neither has it. Raises BookLookupError when a service
    could not be asked (unreachable, too slow, an error or over its quota)
    and no other had the book.
    """
    failed = []
    for source, find in _SOURCES:
        try:
            found = find(isbn)
        except requests.RequestException:
            logger.warning(
                "Looking up ISBN %s in %s failed", isbn, source, exc_info=True
            )
            failed.append(source)
            continue
        if found is not None:
            return found
    if failed:
        raise BookLookupError(isbn, failed)
    return None


def _from_google_books(isbn: str) -> FoundBook | None:
    items = _list(_get_json(GOOGLE_BOOKS_URL, {"q": f"isbn:{isbn}"}), "items")
    if not items:
        return None
    info = _member(items[0], "volumeInfo")
    return FoundBook(
        source="Google Books",
        title=_text(info, "title"),
        authors=tuple(_author(name) for name in _texts(info, "authors")),
        publisher=_text(info, "publisher"),
        publication_year=_year(_text(info, "publishedDate")),
        plot=_plain(_text(info, "description")),
        categories=tuple(_texts(info, "categories")),
    )


def _from_open_library(isbn: str) -> FoundBook | None:
    params = {"bibkeys": f"ISBN:{isbn}", "jscmd": "data", "format": "json"}
    book = _member(_get_json(OPEN_LIBRARY_URL, params), f"ISBN:{isbn}")
    if not isinstance(book, dict):
        return None
    publishers = _names(book, "publishers")
    # No plot in this API; its subjects ("Large type books") are no categories.
    return FoundBook(
        source="Open Library",
        title=_text(book, "title"),
        authors=tuple(_author(name) for name in _names(book, "authors")),
        publisher=publishers[0] if publishers else None,
        publication_year=_year(_text(book, "publish_date")),
        plot=None,
        categories=(),
    )


_SOURCES: tuple[tuple[str, Callable[[str], FoundBook | None]], ...] = (
    ("Google Books", _from_google_books),
    ("Open Library", _from_open_library),
)


def _get_json(url: str, params: dict[str, str]) -> object:
    """The JSON answer; requests.RequestException when there is none."""
    response = requests.get(url, params=params, headers=_HEADERS, timeout=_TIMEOUT)
    response.raise_for_status()
    return response.json()


def _member(data: object, key: str) -> object:
    return data.get(key) if isinstance(data, dict) else None


def _list(data: object, key: str) -> list[object]:
    value = _member(data, key)
    return value if isinstance(value, list) else []


def _tidy(value: object) -> str | None:
    """A text with its spaces tidied; None for a blank one or no text."""
    if not isinstance(value, str):
        return None
    return _SPACES.sub(" ", value).strip() or None


def _text(data: object, key: str) -> str | None:
    return _tidy(_member(data, key))


def _texts(data: object, key: str) -> list[str]:
    """A list of texts, the blank ones left out."""
    return [text for value in _list(data, key) if (text := _tidy(value))]


def _names(data: object, key: str) -> list[str]:
    """The names of a list of {"name": ...} (Open Library's authors, publishers)."""
    return [name for entry in _list(data, key) if (name := _text(entry, "name"))]


def _author(text: str) -> Author:
    """
    "Name Surname" split at the last space, or "Surname, Name"; one word is
    a surname (an author known by one name).
    """
    if "," in text:
        surname, _, name = text.partition(",")
        return Author(name.strip(), surname.strip())
    name, _, surname = text.rpartition(" ")
    return Author(name, surname)


def _year(date: str | None) -> int | None:
    """The year of a date given as "1965", "1965-08-01" or "August 1965"."""
    found = _YEAR.search(date or "")
    return int(found[0]) if found else None


def _plain(text: str | None) -> str | None:
    """
    A description without its HTML (Google Books' may have some), one line
    per paragraph.
    """
    if text is None:
        return None
    plain = html.unescape(_TAG.sub("", _BREAK.sub("\n", text)))
    return "\n".join(line for line in map(_tidy, plain.splitlines()) if line) or None
