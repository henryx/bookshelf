"""
ISSNs in the ISBN field: serials (magazines, journals) by their EAN-13
barcode, 977 + the ISSN's 7 digits + a 2-digit variant + check digit, with an
optional 2- or 5-digit add-on (usually the issue). An ISSN may be typed as
such (0317-8471), with the issue after a space.
"""

import pytest

from bookshelf.models.books import (
    InvalidIsbnError,
    code_label,
    format_code,
    isbn_searches,
    issn_of,
    issue_of,
    normalise_isbn,
)

# ISSN 0317-8471 as a barcode (variant 00), and 1050-124X (check digit X).
NATURE = "9770317847001"
X_SERIAL = "9771050124008"


@pytest.mark.parametrize(
    ("typed", "expected"),
    [
        (NATURE, NATURE),
        ("977-0317-847-00-1", NATURE),
        # With the add-on: 2 or 5 digits, apart or straight after.
        (f"{NATURE} 05", f"{NATURE}05"),
        (f"{NATURE}-05", f"{NATURE}05"),
        (f"{NATURE}05", f"{NATURE}05"),
        (f"{NATURE} 12345", f"{NATURE}12345"),
        (f"{NATURE}12345", f"{NATURE}12345"),
        # An ISSN as typed: with or without its hyphen, X in either case.
        ("0317-8471", NATURE),
        ("03178471", NATURE),
        ("1050-124X", X_SERIAL),
        ("1050-124x", X_SERIAL),
        ("1050124X", X_SERIAL),
        # And its issue after a space.
        ("0317-8471 05", f"{NATURE}05"),
        ("0317-8471 12345", f"{NATURE}12345"),
        ("  0317-8471   05 ", f"{NATURE}05"),
        # A variant other than 00 is kept.
        ("9770317847018", "9770317847018"),
    ],
)
def test_issns_are_stored_as_their_barcode(typed, expected):
    assert normalise_isbn(typed) == expected


@pytest.mark.parametrize(
    "typed",
    [
        "9770317847002",  # wrong EAN check digit
        f"{NATURE}0",  # add-on of 1 digit
        f"{NATURE}123",  # add-on of 3 digits
        f"{NATURE}1234",  # of 4
        f"{NATURE}123456",  # of 6
        "0317-8472",  # wrong ISSN check digit
        "0317-847",  # too short
        "0317-8471 5",  # issue of 1 digit
        "0317-8471 123",  # of 3
        "0317-8471 ab",
        "X317-8471",
        "9780441172719 05",  # an add-on is a serial's only
    ],
)
def test_invalid_issns_are_refused(typed):
    with pytest.raises(InvalidIsbnError, match="ISBN or ISSN"):
        normalise_isbn(typed)


def test_an_isbn_is_still_an_isbn():
    assert normalise_isbn("978-0-441-17271-9") == "9780441172719"
    assert normalise_isbn("0441172717") == "9780441172719"


@pytest.mark.parametrize(
    ("code", "issn", "issue", "shown"),
    [
        (NATURE, "0317-8471", None, "ISSN 0317-8471"),
        (f"{NATURE}05", "0317-8471", "05", "ISSN 0317-8471 · issue 05"),
        (f"{X_SERIAL}12345", "1050-124X", "12345", "ISSN 1050-124X · issue 12345"),
        ("9780441172719", None, None, "ISBN 9780441172719"),
    ],
)
def test_codes_are_shown_as_isbn_or_issn(code, issn, issue, shown):
    assert issn_of(code) == issn
    assert issue_of(code) == issue
    assert format_code(code) == shown
    label, _, unlabelled = shown.partition(" ")
    assert code_label(code) == label
    assert format_code(code, label=False) == unlabelled


@pytest.mark.parametrize(
    ("typed", "searches"),
    [
        # An ISSN as typed also finds its barcodes, whatever their variant.
        ("0317-8471", ("03178471", "9770317847")),
        ("1050-124X", ("1050-124X", "9771050124")),
        # Digits are still found anywhere, as for ISBNs.
        ("977031", ("977031", "977031")),
    ],
)
def test_an_issn_search_finds_its_barcodes(typed, searches):
    assert isbn_searches(typed) == searches
