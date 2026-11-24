"""
SQL that Firebird and PostgreSQL write differently, as SQLAlchemy
constructs: models use these, never one backend's own syntax. Each compiles
to Firebird's form by default, and to PostgreSQL's on PostgreSQL; values
stay bound parameters.

Case is ignored by the columns' collations (Firebird's UNICODE_CI,
PostgreSQL's nondeterministic unicode_ci), not by these constructs.
"""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    ColumnElement,
    DateTime,
    SmallInteger,
    func,
    literal,
    literal_column,
)
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql.compiler import SQLCompiler
from sqlalchemy.sql.functions import FunctionElement

__all__ = ["least", "matches_search", "minutes_ago", "record_key", "starts_with_ci"]

# LIKE's escape character in PostgreSQL's patterns.
_ESCAPE = "\\"


def _like_text(text: ColumnElement[object]) -> ColumnElement[object]:
    """`text` with LIKE's wildcards (and the escape character) escaped."""
    escaped = func.replace(text, _ESCAPE, _ESCAPE * 2)
    escaped = func.replace(escaped, "%", f"{_ESCAPE}%")
    return func.replace(escaped, "_", f"{_ESCAPE}_")


class matches_search(FunctionElement[bool]):
    """
    `expr` matches the search `text`, ignoring case: on Firebird it contains
    the text as typed; on PostgreSQL each of its words starts a word of
    `expr`, after stemming (SEARCH_MATCHES, db/schema.py).
    """

    type = Boolean()
    inherit_cache = True
    name = "matches_search"


class starts_with_ci(FunctionElement[bool]):
    """`expr` starts with `text`, as typed (no wildcards), ignoring case."""

    type = Boolean()
    inherit_cache = True
    name = "starts_with_ci"


class minutes_ago(FunctionElement[datetime]):
    """The database's current time less `minutes` (its clock, as stored times')."""

    type = DateTime()
    inherit_cache = True
    name = "minutes_ago"


class record_key(FunctionElement[bytes]):
    """
    A row's physical key, for picking one of identical rows in a table with
    no key of its own: unqualified, it is the innermost query's table's.
    """

    inherit_cache = True
    name = "record_key"


class least(FunctionElement[int]):
    """The smaller of two numbers."""

    type = SmallInteger()
    inherit_cache = True
    name = "least"


def _arguments[T](element: FunctionElement[T]) -> list[ColumnElement[object]]:
    return list(element.clauses)


@compiles(matches_search)
def _matches_search(
    element: matches_search, compiler: SQLCompiler, **kw: object
) -> str:
    expr, text = _arguments(element)
    return compiler.process(expr.op("CONTAINING", is_comparison=True)(text), **kw)


@compiles(matches_search, "postgresql")
def _matches_search_postgresql(
    element: matches_search, compiler: SQLCompiler, **kw: object
) -> str:
    expr, text = _arguments(element)
    return compiler.process(func.search_matches(expr, text), **kw)


@compiles(starts_with_ci)
def _starts_with_ci(
    element: starts_with_ci, compiler: SQLCompiler, **kw: object
) -> str:
    expr, text = _arguments(element)
    return compiler.process(expr.op("STARTING WITH", is_comparison=True)(text), **kw)


@compiles(starts_with_ci, "postgresql")
def _starts_with_ci_postgresql(
    element: starts_with_ci, compiler: SQLCompiler, **kw: object
) -> str:
    expr, text = _arguments(element)
    pattern = _like_text(text).concat(literal("%"))
    return compiler.process(expr.like(pattern, escape=_ESCAPE), **kw)


@compiles(minutes_ago)
def _minutes_ago(element: minutes_ago, compiler: SQLCompiler, **kw: object) -> str:
    (minutes,) = _arguments(element)
    return compiler.process(
        func.dateadd(_unit("MINUTE"), -minutes, func.current_timestamp()), **kw
    )


@compiles(minutes_ago, "postgresql")
def _minutes_ago_postgresql(
    element: minutes_ago, compiler: SQLCompiler, **kw: object
) -> str:
    (minutes,) = _arguments(element)
    return compiler.process(
        func.localtimestamp() - func.make_interval(0, 0, 0, 0, 0, minutes), **kw
    )


@compiles(record_key)
def _record_key(element: record_key, compiler: SQLCompiler, **kw: object) -> str:
    return "RDB$DB_KEY"


@compiles(record_key, "postgresql")
def _record_key_postgresql(
    element: record_key, compiler: SQLCompiler, **kw: object
) -> str:
    return "ctid"


@compiles(least)
def _least(element: least, compiler: SQLCompiler, **kw: object) -> str:
    return compiler.process(func.minvalue(*_arguments(element)), **kw)


@compiles(least, "postgresql")
def _least_postgresql(element: least, compiler: SQLCompiler, **kw: object) -> str:
    return compiler.process(func.least(*_arguments(element)), **kw)


def _unit(name: str) -> ColumnElement[object]:
    """DATEADD's unit, a keyword: MINUTE."""
    return literal_column(name)
