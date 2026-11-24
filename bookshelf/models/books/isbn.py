"""
The ISBN field's codes: ISBNs, and serials' ISSNs as their EAN-13 barcode.
Their checks, how they are shown, and the texts a search looks for.

A serial's barcode is 977, the ISSN's 7 digits (its own check digit
dropped), a 2-digit variant (usually 00) and the EAN check digit; a separate
2- or 5-digit add-on, usually the issue, may follow. All of it is stored, as
digits: 13, 15 or 18 of them.
"""

import re
import unicodedata

from bookshelf.models.books.model import InvalidIsbnError, IsbnLostDigitsError

_ISBN_SEPARATORS = re.compile(r"[\s-]")
_ISBN_10 = re.compile(r"\d{9}[\dXx]")
_ISBN_13 = re.compile(r"\d{13}")
# A serial's barcode: 977 + 10 digits, then maybe its add-on, apart or not.
_SERIAL = re.compile(r"(977\d{10})[\s-]?(\d{2}|\d{5})?")
# An ISSN as typed (hyphen optional), then maybe the issue after spaces.
_ISSN = re.compile(r"(\d{4})-?(\d{3}[\dXx])(?:\s+(\d{2}|\d{5}))?")
SERIAL_PREFIX = "977"
# An ISBN a spreadsheet saved as a number: "9780441172719.0" (",0" with a
# decimal comma) keeps its digits; scientific notation ("9.78044E+12") has
# lost them.
_ISBN_DECIMAL_ZERO = re.compile(r"(\d+)[.,]0+")
_ISBN_SCIENTIFIC = re.compile(r"\d[.,]\d+E\+?\d+", re.IGNORECASE)


def _isbn_13_check_digit(first_12: str) -> str:
    total = sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(first_12))
    return str(-total % 10)


def _issn_check_digit(first_7: str) -> str:
    total = sum(int(d) * (8 - i) for i, d in enumerate(first_7))
    check = -total % 11
    return "X" if check == 10 else str(check)


def _issn_valid(issn: str) -> bool:
    """An ISSN's 8 characters (no hyphen) with the right check digit."""
    return _issn_check_digit(issn[:7]) == issn[7].upper()


def _serial(issn_7: str, variant: str = "00") -> str:
    """The barcode of the ISSN whose first 7 digits are `issn_7`."""
    first_12 = f"{SERIAL_PREFIX}{issn_7}{variant}"
    return first_12 + _isbn_13_check_digit(first_12)


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
    The code to store for an ISBN or an ISSN, tidied as _tidy_isbn says:
    an ISBN-10 or ISBN-13 as 13 digits; a serial's barcode (977…) as its 13
    digits and its add-on, if any; an ISSN as typed (0317-8471), with an
    issue after a space (0317-8471 05), as its barcode (variant 00) and that
    issue as the add-on. Raises InvalidIsbnError for anything else, a wrong
    check digit included (IsbnLostDigitsError, a kind of it, for scientific
    notation).
    """
    tidy = _tidy_isbn(text)
    if serial := _serial_code(tidy):
        return serial
    compact = _ISBN_SEPARATORS.sub("", tidy)
    if _ISBN_13.fullmatch(compact) and not compact.startswith(SERIAL_PREFIX):
        if _isbn_13_check_digit(compact[:12]) == compact[12]:
            return compact
    elif _ISBN_10.fullmatch(compact) and _isbn_10_valid(compact):
        first_12 = f"978{compact[:9]}"
        return first_12 + _isbn_13_check_digit(first_12)
    raise InvalidIsbnError(text)


def _serial_code(text: str) -> str | None:
    """A serial's code from its barcode or its ISSN (see normalise_isbn), or None."""
    barcode_text = re.sub(r"(?<=\d)-(?=\d)", "", text)
    if barcode := _SERIAL.fullmatch(barcode_text):
        ean, add_on = barcode.groups()
        if _isbn_13_check_digit(ean[:12]) == ean[12]:
            return ean + (add_on or "")
        return None
    if (issn := _ISSN.fullmatch(" ".join(text.split()))) is None:
        return None
    head, tail, issue = issn.groups()
    if not _issn_valid(head + tail):
        return None
    return _serial(head + tail[:3]) + (issue or "")


def issn_of(code: str) -> str | None:
    """The ISSN (0317-8471) of a stored serial's code; None for an ISBN."""
    if not code.startswith(SERIAL_PREFIX):
        return None
    first_7 = code[3:10]
    issn = first_7 + _issn_check_digit(first_7)
    return f"{issn[:4]}-{issn[4:]}"


def issue_of(code: str) -> str | None:
    """A stored serial's add-on (usually its issue); None without one."""
    return code[13:] or None if code.startswith(SERIAL_PREFIX) else None


def code_label(code: str) -> str:
    """What a stored code is: "ISSN" for a serial's, else "ISBN"."""
    return "ISBN" if issn_of(code) is None else "ISSN"


def format_code(code: str, *, label: bool = True) -> str:
    """
    A stored code as shown: an ISBN as is, a serial as "0317-8471 · issue 05";
    with `label`, after its code_label ("ISSN 0317-8471 · issue 05").
    """
    issn = issn_of(code)
    if issn is None:
        return f"ISBN {code}" if label else code
    issue = issue_of(code)
    shown = issn + (f" · issue {issue}" if issue else "")
    return f"ISSN {shown}" if label else shown


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
    # An ISSN: its barcodes start with 977 and its first 7 digits.
    if re.fullmatch(r"\d{7}[\dXx]", compact) and _issn_valid(compact):
        return typed, f"{SERIAL_PREFIX}{compact[:7]}"
    return typed, typed
