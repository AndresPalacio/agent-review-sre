from __future__ import annotations

from pathlib import Path

import pytest

from fin_graph.persistence.paths import prepare_sqlite_path


def test_home_shortcut_is_expanded_and_its_folder_created(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    # Left unexpanded, "~" would become a folder in the working directory: keep it in tmp_path.
    monkeypatch.chdir(tmp_path)

    resolved = prepare_sqlite_path("~/sre-data/state.sqlite")

    assert Path(resolved) == tmp_path / "sre-data" / "state.sqlite"
    assert (tmp_path / "sre-data").is_dir()


def test_empty_path_is_rejected() -> None:
    with pytest.raises(ValueError, match="SQLITE_PATH"):
        prepare_sqlite_path("  ")


def test_in_memory_path_is_left_alone() -> None:
    assert prepare_sqlite_path(":memory:") == ":memory:"
