"""Connection-string adapters at the SQLAlchemy/LangGraph boundary."""

from __future__ import annotations


def psycopg_connection_url(value: str) -> str:
    """Return the libpq URL expected by psycopg-backed LangGraph components."""

    prefix = "postgresql+psycopg://"
    if value.startswith(prefix):
        return "postgresql://" + value.removeprefix(prefix)
    return value


__all__ = ["psycopg_connection_url"]
