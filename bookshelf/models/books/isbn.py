"""ISBN checks, and the texts an ISBN search looks for."""

import re
import unicodedata

from bookshelf.models.books.model import InvalidIsbnError, IsbnLostDigitsError

_ISBN_SEPARATORS = re.compile(r"[\s-]")
_ISBN_10 = re.compile(r"\d{9}[\dXx]")
_ISBN_13 = re.compile(r"\d{13}")
# An ISBN a spreadsheet saved as a number: "9780441172719.0" (",0" with a
# decimal comma) keeps its digits; scientific notation ("9.78044E+12") has
# lost them.
_ISBN_DECIMAL_ZERO = re.compile(r"(\d+)[.,]0+")
_ISBN_SCIENTIFIC = re.compile(r"\d[.,]\d+E\+?\d+", re.IGNORECASE)


def _isbn_13_check_digit(first_12: str) -> str:
    total = sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(first_12))
    return str(-total % 10)


def _isbn_10_valid(isbn: str) -> bool:
    digits = [10 if d in "Xx" else int(d) for d in isbn]
    return sum(d * (10 - i) for i, d in enumerate(digits)) % 11 == 0


def _tidy_isbn(text: str) -> str:
    """
    Undo what spreadsheets and copying do to an ISBN: drop invisible
    characters (zero-width spaces, byte order marks: Unicode format
    characters), a leading ' that kept it text and a ".0" that made it a
    number. IsbnLostDigitsError for one in scientific notation.
    """
    visible = "".join(c for c in text if unicodedata.category(c) != "Cf")
    isbn = visible.strip().removeprefix("'")
    if _ISBN_SCIENTIFIC.fullmatch(isbn):
        raise IsbnLostDigitsError(text)
    if whole := _ISBN_DECIMAL_ZERO.fullmatch(isbn):
        return whole[1]
    return isbn


def normalise_isbn(text: str) -> str:
    """
    The 13-digit form of an ISBN-10 or ISBN-13, with hyphens and spaces
    removed, and tidied as _tidy_isbn says. Raises InvalidIsbnError for
    anything else, a wrong check digit included (IsbnLostDigitsError, a
    kind of it, for scientific notation).
    """
    compact = _ISBN_SEPARATORS.sub("", _tidy_isbn(text))
    if _ISBN_13.fullmatch(compact):
        if _isbn_13_check_digit(compact[:12]) == compact[12]:
            return compact
    elif _ISBN_10.fullmatch(compact) and _isbn_10_valid(compact):
        first_12 = f"978{compact[:9]}"
        return first_12 + _isbn_13_check_digit(first_12)
    raise InvalidIsbnError(text)


def isbn_searches(text: str) -> tuple[str, str]:
    """
    The two texts to look for in ISBNs, which are stored as 13 digits. First
    the text as typed, with hyphens and spaces removed from digits (other text
    is kept as is: it has letters, so it matches no ISBN). Second, for a valid
    ISBN-10, its ISBN-13 without the check digit (the only part that differs);
    otherwise the first again. Both are needed: the first 10 digits of an
    ISBN-13 may also be a valid ISBN-10.
    """
    compact = _ISBN_SEPARATORS.sub("", text)
    typed = compact if compact.isdigit() else text
    if _ISBN_10.fullmatch(compact) and _isbn_10_valid(compact):
        return typed, f"978{compact[:9]}"
    return typed, typed
