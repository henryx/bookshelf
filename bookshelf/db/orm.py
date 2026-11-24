"""
The ORM mapping of the schema: one `*Row` class per table the models use
(not SETTINGS, which only init_db writes, with its own SQL), mirroring the
DDL of the migrations (bookshelf/migrations/), which alone create and change
the schema; this metadata never does.

Names are lower case: SQLAlchemy's Firebird dialect writes them unquoted,
which Firebird reads as the upper-case names of the DDL. Column types carry
no collation: the dialect would repeat it in each parameter's CAST, which
Firebird refuses; the columns' UNICODE_CI collation still applies.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    Dialect,
    ForeignKey,
    SmallInteger,
    String,
    TypeDecorator,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import TypeEngine
from sqlalchemy_firebird.types import FBBINARY

__all__ = [
    "AuthorRow",
    "Base",
    "BookAuthorRow",
    "BookCategoryRow",
    "BookRow",
    "CollectionBookRow",
    "LoginFailureRow",
    "UUIDType",
    "UserRow",
]


class UUIDType(TypeDecorator[UUID]):
    """
    A UUID. Firebird stores its 16 raw bytes (the UUID domain: CHAR(16)
    CHARACTER SET OCTETS), bound as OCTETS too: as CHAR(16) in the UTF8
    connection's character set, the bytes would be read as text and refused
    or changed. PostgreSQL has a UUID type of its own.
    """

    impl = FBBINARY(16)
    cache_ok = True

    def load_dialect_impl(self, dialect: Dialect) -> TypeEngine[UUID] | TypeEngine[str]:
        if dialect.name == "postgresql":
            return dialect.type_descriptor(Uuid(as_uuid=True))
        # Typed as a string by sqlalchemy-firebird: it carries the bytes.
        return dialect.type_descriptor(FBBINARY(16))

    def process_bind_param(
        self, value: UUID | None, dialect: Dialect
    ) -> UUID | bytes | None:
        if value is None or dialect.name == "postgresql":
            return value
        return value.bytes

    def process_result_value(
        self, value: UUID | bytes | None, dialect: Dialect
    ) -> UUID | None:
        if value is None or isinstance(value, UUID):
            return value
        return UUID(bytes=value)


class Base(DeclarativeBase):
    pass


class UserRow(Base):
    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True)
    email: Mapped[str] = mapped_column(String(254))
    password_hash: Mapped[str | None] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(16), server_default=text("'REGULAR'"))
    is_locked: Mapped[bool] = mapped_column(Boolean, server_default=text("FALSE"))
    name: Mapped[str | None] = mapped_column(String(127))
    surname: Mapped[str | None] = mapped_column(String(127))
    auth_method: Mapped[str] = mapped_column(String(16), server_default=text("'LOCAL'"))
    google_subject: Mapped[str | None] = mapped_column(String(255))


class LoginFailureRow(Base):
    """
    LOGIN_FAILURES has no key: the mapper's key is all its columns but `id`.
    Only read and written through statements (select, insert, delete), never
    added to a session, where two equal rows would clash.
    """

    __tablename__ = "login_failures"

    id: Mapped[UUID | None] = mapped_column(UUIDType)
    email: Mapped[str] = mapped_column(String(254), primary_key=True)
    ip_address: Mapped[str] = mapped_column(String(45), primary_key=True)
    attempted_at: Mapped[datetime] = mapped_column(
        server_default=func.current_timestamp(), primary_key=True
    )


class BookRow(Base):
    __tablename__ = "books"

    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True)
    title: Mapped[str] = mapped_column(String(255))
    isbn: Mapped[str | None] = mapped_column(String(18))
    publisher: Mapped[str | None] = mapped_column(String(255))
    publication_year: Mapped[int | None] = mapped_column(SmallInteger)
    series: Mapped[str | None] = mapped_column(String(255))
    volume: Mapped[str | None] = mapped_column(String(32))
    plot: Mapped[str | None] = mapped_column(String(4000))
    created_at: Mapped[datetime] = mapped_column(
        server_default=func.current_timestamp()
    )

    authors: Mapped[list[BookAuthorRow]] = relationship(
        order_by="BookAuthorRow.sort_order", viewonly=True
    )
    categories: Mapped[list[BookCategoryRow]] = relationship(
        order_by="BookCategoryRow.category", viewonly=True
    )


class AuthorRow(Base):
    __tablename__ = "authors"

    id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True)
    name: Mapped[str] = mapped_column(String(127), server_default=text("''"))
    surname: Mapped[str] = mapped_column(String(127))


class BookAuthorRow(Base):
    __tablename__ = "book_authors"

    book_id: Mapped[UUID] = mapped_column(
        UUIDType, ForeignKey("books.id"), primary_key=True
    )
    author_id: Mapped[UUID] = mapped_column(
        UUIDType, ForeignKey("authors.id"), primary_key=True
    )
    sort_order: Mapped[int] = mapped_column(SmallInteger)

    author: Mapped[AuthorRow] = relationship(lazy="joined", viewonly=True)


class BookCategoryRow(Base):
    __tablename__ = "book_categories"

    book_id: Mapped[UUID] = mapped_column(
        UUIDType, ForeignKey("books.id"), primary_key=True
    )
    category: Mapped[str] = mapped_column(String(64), primary_key=True)


class CollectionBookRow(Base):
    __tablename__ = "collection_books"

    user_id: Mapped[UUID] = mapped_column(
        UUIDType, ForeignKey("users.id"), primary_key=True
    )
    book_id: Mapped[UUID] = mapped_column(
        UUIDType, ForeignKey("books.id"), primary_key=True
    )
    copies: Mapped[int] = mapped_column(SmallInteger, server_default=text("1"))
    is_read: Mapped[bool] = mapped_column(Boolean, server_default=text("FALSE"))
