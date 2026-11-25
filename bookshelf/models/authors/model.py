"""The authors' data types and errors."""

from dataclasses import dataclass
from uuid import UUID

from bookshelf.models.books import Author

__all__ = ["AuthorEntry", "AuthorPage", "Renamed", "UnknownAuthorError"]


class UnknownAuthorError(Exception):
    """No author has this ID (it may have just been merged into another)."""

    def __init__(self, author_id: UUID) -> None:
        super().__init__(f"No author {author_id}.")
        self.author_id = author_id


@dataclass(frozen=True)
class AuthorEntry:
    """An author as the authors page lists it: with how many books are theirs."""

    id: UUID
    author: Author
    books: int


@dataclass(frozen=True)
class AuthorPage:
    authors: list[AuthorEntry]
    total: int
    page: int
    page_size: int

    @property
    def pages(self) -> int:
        """Number of pages; at least 1, so an empty list still has a page."""
        return max(1, -(-self.total // self.page_size))


@dataclass(frozen=True)
class Renamed:
    """
    What rename_author did: the author's name now, as stored, and whether it
    was merged into another author who already had that name.
    """

    author: Author
    merged: bool
