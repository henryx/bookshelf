"""The CSV's columns and cell conventions, shared by the export and the import."""

__all__ = ["COLUMNS", "SEPARATOR", "guard", "unguard"]

# The export's columns, in order; an import may have any of them, in any
# order, but needs title. authors ("Name Surname", or "Surname" alone) and
# categories are separated by ", ". read: yes or no.
COLUMNS = (
    "title",
    "authors",
    "isbn",
    "publisher",
    "year",
    "series",
    "volume",
    "plot",
    "categories",
    "copies",
    "read",
)
# Between authors and between categories alike.
SEPARATOR = ","
# A spreadsheet runs a cell starting with one of these as a formula: the
# export puts a ' before it (shown as text), and the import takes it off.
_FORMULA_STARTS = ("=", "+", "-", "@", "\t", "\r")


def guard(text: str) -> str:
    return f"'{text}" if text.startswith(_FORMULA_STARTS) else text


def unguard(text: str) -> str:
    return (
        text[1:] if text[:1] == "'" and text[1:].startswith(_FORMULA_STARTS) else text
    )
