"""Upgrade server metadata with the Alembic revisions shipped in the wheel."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config


def upgrade(database_url: str) -> None:
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
    config.attributes["database_url"] = database_url
    command.upgrade(config, "head")
