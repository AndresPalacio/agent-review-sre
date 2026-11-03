from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).parents[2]
SCRIPT = ROOT / "scripts" / "check_compatibility_manifest.py"


def _fixture(tmp_path: Path) -> tuple[Path, dict[str, Any]]:
    owner_file = tmp_path / "tests" / "test_owner.py"
    owner_file.parent.mkdir(parents=True)
    owner_file.write_text("def test_contract():\n    assert True\n", encoding="utf-8")
    candidate_digest = hashlib.sha256(b"def test_contract():\n    assert True\n").hexdigest()
    manifest = {
        "schema_version": 1,
        "snapshot_digest": "a" * 64,
        "recorder_version": "1",
        "fixtures": [
            {
                "fixture": "recording.json",
                "license": "MIT",
                "name": "recording",
                "origin": "AI Hedge Fund",
                "recorder_version": "1",
                "snapshot_digest": "a" * 64,
                "source_file": "src/main.py",
            }
        ],
        "contracts": [
            {
                "id": "flow-crud",
                "status": "verified",
                "owner": "tests/test_owner.py::test_contract",
                "reference_digest": "a" * 64,
                "candidate_digest": candidate_digest,
                "comparison_rule": "exact",
                "evidence_command": ("uv run pytest tests/test_owner.py::test_contract -q"),
            }
        ],
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return manifest_path, manifest


def _run(tmp_path: Path, manifest: dict[str, Any]) -> subprocess.CompletedProcess[str]:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--root",
            str(tmp_path),
            "--manifest",
            str(manifest_path),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def test_fully_owned_manifest_passes(tmp_path: Path) -> None:
    _path, manifest = _fixture(tmp_path)

    completed = _run(tmp_path, manifest)

    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == {
        "contracts": 1,
        "excluded": 0,
        "status": "closed",
        "verified": 1,
    }


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda value: value["contracts"][0].update(status="pending"), "invalid status"),
        (
            lambda value: value["contracts"][0].update(owner="tests/test_owner.py::test_missing"),
            "pytest owner does not exist",
        ),
        (
            lambda value: value["contracts"][0].update(reference_digest="b" * 64),
            "stale reference digest",
        ),
        (
            lambda value: value["contracts"][0].update(
                status="excluded", exclusion={"rationale": "not implemented"}
            ),
            "unapproved exclusion",
        ),
        (
            lambda value: value["contracts"][0].update(evidence_command="See the parity report"),
            "executable pytest command",
        ),
        (
            lambda value: value["contracts"].append(
                deepcopy(value["contracts"][0]) | {"id": "duplicate-owner"}
            ),
            "duplicate owner",
        ),
    ],
)
def test_incomplete_or_stale_manifests_fail(
    tmp_path: Path,
    mutation: Any,
    message: str,
) -> None:
    _path, manifest = _fixture(tmp_path)
    mutation(manifest)

    completed = _run(tmp_path, manifest)

    assert completed.returncode != 0
    assert message in completed.stderr


def test_candidate_digest_tracks_owning_test_file(tmp_path: Path) -> None:
    _path, manifest = _fixture(tmp_path)
    owner = tmp_path / "tests" / "test_owner.py"
    owner.write_text("def test_contract():\n    assert False\n", encoding="utf-8")

    completed = _run(tmp_path, manifest)

    assert completed.returncode != 0
    assert "stale candidate digest" in completed.stderr


def test_candidate_digest_is_stable_across_platform_line_endings(tmp_path: Path) -> None:
    _path, manifest = _fixture(tmp_path)
    owner = tmp_path / "tests" / "test_owner.py"
    owner.write_bytes(b"def test_contract():\n    assert True\n")

    completed = _run(tmp_path, manifest)

    assert completed.returncode == 0, completed.stderr
