"""Filesystem preparation shared by the SQLite-backed persistence factories."""

from __future__ import annotations

from pathlib import Path

SQLITE_IN_MEMORY = ":memory:"


def prepare_sqlite_path(path: str) -> str:
    """Expand ``~``, create the file's parent folder, and return a path SQLite can open.

    An empty path is rejected: SQLite would silently open a private temporary
    database and nothing would persist.
    """
    if not path.strip():
        raise ValueError("SQLITE_PATH must not be empty")
    if path == SQLITE_IN_MEMORY:
        return path
    resolved = Path(path).expanduser()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    return str(resolved)
