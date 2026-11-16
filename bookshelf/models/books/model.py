"""The books' data types, errors and limits."""

from dataclasses import dataclass, field
from enum import StrEnum
from uuid import UUID

# Firebird GDS code for "violation of PRIMARY or UNIQUE KEY constraint".
UNIQUE_KEY_VIOLATION = 335544665
# Firebird GDS code for "violation of FOREIGN KEY constraint".
FOREIGN_KEY_VIOLATION = 335544466

PAGE_SIZE = 50
# BOOK_CATEGORIES.CATEGORY; a longer text matches no category.
CATEGORY_MAX_LENGTH = 64
# The longest searched text (TITLE, or an author's name and surname); a
# longer text matches nothing.
SEARCH_MAX_LENGTH = 255


class InvalidIsbnError(ValueError):
    """Text that is not a valid ISBN-10 or ISBN-13."""

    def __init__(self, isbn: str) -> None:
        super().__init__(f"Not a valid ISBN: {isbn}")
        self.isbn = isbn


class IsbnLostDigitsError(InvalidIsbnError):
    """
    An ISBN in scientific notation ("9.78044E+12"): a spreadsheet saved it
    as a number and dropped digits, so it cannot be read back.
    """


class BookExistsError(Exception):
    """A book with the same ISBN is already in the catalogue."""

    def __init__(self, isbn: str) -> None:
        super().__init__(f"A book with ISBN {isbn} is already in the catalogue.")
        self.isbn = isbn


class BookInCollectionError(Exception):
    """A book in a user's collection cannot be deleted."""

    def __init__(self, title: str) -> None:
        super().__init__(
            f"“{title}” is in someone’s collection: it can be deleted only when it is "
            "in nobody’s collection."
        )
        self.title = title


class IsbnAlreadySetError(Exception):
    """A book's ISBN can be set only while it has none."""

    def __init__(self, book_id: UUID) -> None:
        super().__init__("This book already has an ISBN.")
        self.book_id = book_id


class UnknownBookError(Exception):
    """No book in the catalogue has this ID (it may have just been deleted)."""

    def __init__(self, book_id: UUID) -> None:
        super().__init__(f"No book {book_id} in the catalogue.")
        self.book_id = book_id


class AuthorField(StrEnum):
    """The author field whose start is matched by suggest_authors."""

    NAME = "name"
    SURNAME = "surname"


class BookField(StrEnum):
    """The book field whose start is matched by suggest_books."""

    TITLE = "title"
    ISBN = "isbn"


class Sort(StrEnum):
    """Orders of the book list."""

    TITLE = "title"
    AUTHOR = "author"
    ADDED = "added"


@dataclass(frozen=True)
class Author:
    """
    An author's given name ('' when there is none, as for "Homer") and
    surname. `id`: the author's record, when read from one (as in the book
    lists); it plays no part in comparing authors, which is by name.
    """

    name: str
    surname: str
    id: UUID | None = field(default=None, compare=False, kw_only=True)

    def __str__(self) -> str:
        return f"{self.name} {self.surname}".strip()


@dataclass(frozen=True)
class Book:
    """
    A book as shown in the book lists. copies: how many the current user
    owns, or None when the book is not in their collection; read: whether
    they have read it (False when it is not in their collection).
    """

    id: UUID
    title: str
    authors: tuple[Author, ...]
    publication_year: int | None
    copies: int | None = None
    read: bool = False


@dataclass(frozen=True)
class NewBook:
    """
    A book to add. Optional texts are None when not given; authors and categories
    are trimmed, blank ones skipped and repeats (ignoring case) dropped.
    """

    title: str
    authors: tuple[Author, ...] = ()
    isbn: str | None = None
    publisher: str | None = None
    publication_year: int | None = None
    series: str | None = None
    volume: str | None = None
    plot: str | None = None
    categories: tuple[str, ...] = ()


@dataclass(frozen=True, kw_only=True)
class BookDetails:
    """
    A catalogue book with every field; categories in alphabetical order.
    collections: how many users have it in their collection.
    """

    id: UUID
    title: str
    authors: tuple[Author, ...] = ()
    isbn: str | None = None
    publisher: str | None = None
    publication_year: int | None = None
    series: str | None = None
    volume: str | None = None
    plot: str | None = None
    categories: tuple[str, ...] = ()
    collections: int = 0


@dataclass(frozen=True)
class BookSuggestion:
    """A catalogue book suggested by its title or ISBN in the add-book form."""

    id: UUID
    title: str
    isbn: str | None
    publication_year: int | None
    authors: tuple[Author, ...] = ()


@dataclass(frozen=True)
class DeletedBook:
    """A book delete_book deleted, and the authors that went with it (no other book)."""

    title: str
    authors_deleted: tuple[UUID, ...]


@dataclass(frozen=True)
class BookPage:
    books: list[Book]
    total: int
    page: int
    page_size: int

    @property
    def pages(self) -> int:
        """Number of pages; at least 1, so an empty list still has a page."""
        return max(1, -(-self.total // self.page_size))


# A query and its parameters.
type Query = tuple[str, tuple[object, ...]]
