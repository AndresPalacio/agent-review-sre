"""Read-only access to the solver's gateway-owned execution controls."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any


def read_control(path: str, work_id: str) -> dict[str, Any] | None:
    """Read only the execution gate fields from inside an Agent Server process."""

    # Do not create a missing ledger silently: a broken control path fails closed.
    uri = Path(path).resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, timeout=5)) as database:
        row = database.execute("SELECT data FROM work WHERE work_id=?", (work_id,)).fetchone()
    if row is None:
        return None
    work = json.loads(row[0])
    return {key: work[key] for key in ("actor", "revision", "cancel_requested")}
