"""
Authors: the authors page's list and finding one (`listing.py`), renaming one
for every book, merging into a namesake (`renaming.py`), and their data types
(`model.py`).
"""

from bookshelf.models.authors.listing import find_author, list_authors
from bookshelf.models.authors.model import (
    AuthorEntry,
    AuthorPage,
    Renamed,
    UnknownAuthorError,
)
from bookshelf.models.authors.renaming import rename_author

__all__ = [
    "AuthorEntry",
    "AuthorPage",
    "Renamed",
    "UnknownAuthorError",
    "find_author",
    "list_authors",
    "rename_author",
]
