"""
The Flask-WTF forms, by area: `users.py` (login, setup, users, account),
`books.py` (a book, an author), `collection.py` (copies, read, import, copying
a collection); `fields.py` holds what they share.
"""

from bookshelf.routes.forms.books import (
    AuthorForm,
    BookForm,
    EditBookForm,
    RenameAuthorForm,
)
from bookshelf.routes.forms.collection import (
    CopiesForm,
    CopyCollectionForm,
    ImportForm,
    ReadForm,
    ScoreForm,
)
from bookshelf.routes.forms.users import (
    AccountForm,
    LoginForm,
    NewAccountForm,
    NewUserForm,
    SetupForm,
    UserForm,
)

__all__ = [
    "AccountForm",
    "AuthorForm",
    "BookForm",
    "CopiesForm",
    "CopyCollectionForm",
    "EditBookForm",
    "ImportForm",
    "LoginForm",
    "NewAccountForm",
    "NewUserForm",
    "ReadForm",
    "RenameAuthorForm",
    "ScoreForm",
    "SetupForm",
    "UserForm",
]
