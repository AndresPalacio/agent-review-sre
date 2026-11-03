from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from fin_graph.compat.reference import build_reference_snapshot


def _write(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def _create_directory_link(link: Path, target: Path) -> None:
    if os.name == "nt":
        completed = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            pytest.fail(f"could not create test junction: {completed.stderr or completed.stdout}")
        return
    link.symlink_to(target, target_is_directory=True)


def test_snapshot_is_stable_and_excludes_private_state(tmp_path: Path) -> None:
    root = tmp_path / "reference"
    _write(root / "LICENSE", "MIT License\nPermission is hereby granted\n")
    _write(root / "src" / "main.py", "print('safe')\n")
    _write(root / ".env", "REFERENCE_SECRET=must-not-leak\n")
    _write(root / "app" / "backend" / "hedge_fund.db", "must-not-leak")
    _write(root / "__pycache__" / "main.cpython-312.pyc", "must-not-leak")
    _write(root / ".venv" / "Lib" / "site-packages" / "private.py", "must-not-leak")
    _write(root / "node_modules" / "private" / "index.js", "must-not-leak")
    _write(root / ".ai_hedge_fund" / "jobs" / "request.json", "must-not-leak")
    _write(root / ".v2_cache" / "models" / "weights.json", "must-not-leak")
    _write(root / ".claude" / "settings.local.json", "must-not-leak")
    _write(root / "hedge_fund.db-wal", "must-not-leak")
    _write(root / "hedge_fund.sqlite-shm", "must-not-leak")
    _write(root / "hedge_fund.sqlite3-journal", "must-not-leak")

    first = build_reference_snapshot(root)
    second = build_reference_snapshot(root)

    assert first == second
    assert [entry.path for entry in first.files] == ["LICENSE", "src/main.py"]
    assert len(first.root_digest) == 64
    assert set(first.root_digest) <= set("0123456789abcdef")
    assert "must-not-leak" not in first.model_dump_json()


def test_snapshot_rejects_symlink_escape(tmp_path: Path) -> None:
    root = tmp_path / "reference"
    outside = tmp_path / "outside"
    root.mkdir()
    _write(outside / "secret.txt", "outside")
    _create_directory_link(root / "escape", outside)

    with pytest.raises(ValueError, match="escapes reference root"):
        build_reference_snapshot(root)


def test_snapshot_digest_changes_with_source(tmp_path: Path) -> None:
    root = tmp_path / "reference"
    source = root / "src" / "main.py"
    _write(source, "print('before')\n")
    before = build_reference_snapshot(root)

    _write(source, "print('after')\n")
    after = build_reference_snapshot(root)

    assert before.root_digest != after.root_digest
    assert before.files[0].sha256 != after.files[0].sha256


def test_recorder_writes_manifest_outside_reference(tmp_path: Path) -> None:
    root = tmp_path / "reference"
    output = tmp_path / "snapshot.json"
    _write(root / "src" / "main.py", "print('safe')\n")

    completed = subprocess.run(
        [
            sys.executable,
            "scripts/snapshot_ai_hedge_fund.py",
            "--root",
            str(root),
            "--output",
            str(output),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert '"schema_version": 1' in output.read_text(encoding="utf-8")


def test_recorder_refuses_output_inside_reference(tmp_path: Path) -> None:
    root = tmp_path / "reference"
    _write(root / "src" / "main.py", "print('safe')\n")

    completed = subprocess.run(
        [
            sys.executable,
            "scripts/snapshot_ai_hedge_fund.py",
            "--root",
            str(root),
            "--output",
            str(root / "snapshot.json"),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode != 0
    assert "outside the reference root" in completed.stderr
