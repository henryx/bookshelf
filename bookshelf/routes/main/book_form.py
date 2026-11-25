"""What the add-book and edit-book forms share."""

from flask import request

from bookshelf.models.books import BookDetails
from bookshelf.routes.forms import EditBookForm

__all__ = ["book_data", "change_rows"]


def book_data(details: BookDetails) -> dict[str, object]:
    """A book's fields as form data, for the edit form and a picked book's add form."""
    return {
        "authors": [{"name": a.name, "surname": a.surname} for a in details.authors],
        "publisher": details.publisher,
        "publication_year": details.publication_year,
        "series": details.series,
        "volume": details.volume,
        "plot": details.plot,
        "categories": list(details.categories) or [""],
        "isbn": details.isbn,
    }


def change_rows(form: EditBookForm) -> bool:
    """
    Without JavaScript the + and - buttons of the author and category rows
    submit the form (add_author, remove_author=<index>, and the same for
    category): add or remove that row. True when one did, so the form is
    shown again without saving.
    """
    for field, row in ((form.authors, "author"), (form.categories, "category")):
        if f"add_{row}" in request.form:
            form.add_row(field)
            return True
        if remove := request.form.get(f"remove_{row}"):
            form.remove_row(field, remove)
            return True
    return False
