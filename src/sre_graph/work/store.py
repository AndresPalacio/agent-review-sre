"""SQLite command ledger, not a task scheduler or a graph checkpointer."""

import json
import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any


class WorkStore:
    def __init__(self, path: Path) -> None:
        self.path = path.resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS work (
                    work_id TEXT PRIMARY KEY, data TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS command (
                    actor TEXT NOT NULL, command_id TEXT NOT NULL,
                    fingerprint TEXT NOT NULL, work_id TEXT NOT NULL,
                    state TEXT NOT NULL, run_id TEXT,
                    PRIMARY KEY (actor, command_id)
                );
            """)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def get(self, work_id: str) -> dict[str, Any]:
        with self.connect() as db:
            row = db.execute("SELECT data FROM work WHERE work_id=?", (work_id,)).fetchone()
        if row is None:
            raise KeyError(work_id)
        return dict(json.loads(row["data"]))

    def command(self, actor: str, command_id: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM command WHERE actor=? AND command_id=?", (actor, command_id)
            ).fetchone()
        return dict(row) if row else None

    def save(self, work: dict[str, Any], *, command: dict[str, Any] | None = None) -> None:
        # Both the revision/intent and its command identity commit atomically.
        with self.connect() as db:
            db.execute(
                "INSERT INTO work VALUES (?, ?) ON CONFLICT(work_id) "
                "DO UPDATE SET data=excluded.data",
                (work["work_id"], json.dumps(work, ensure_ascii=False)),
            )
            if command is not None:
                db.execute(
                    "INSERT INTO command VALUES (:actor, :command_id, :fingerprint, "
                    ":work_id, :state, :run_id) ON CONFLICT(actor,command_id) "
                    "DO UPDATE SET state=excluded.state, run_id=excluded.run_id",
                    command,
                )


def read_control(path: str, work_id: str) -> dict[str, Any] | None:
    """Read only the execution gate fields from inside an Agent Server process."""
    # Do not create a missing ledger silently: a broken control path fails closed.
    uri = Path(path).resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, timeout=5)) as db:
        row = db.execute("SELECT data FROM work WHERE work_id=?", (work_id,)).fetchone()
    if row is None:
        return None
    work = json.loads(row[0])
    return {key: work[key] for key in ("actor", "revision", "cancel_requested")}
