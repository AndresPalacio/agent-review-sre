from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from tests.compat.oracle import assert_fixture_provenance

from fin_graph.compat.contracts import (
    ComparisonRule,
    compare_contract,
    normalize_contract,
)
from fin_graph.compat.reference import ReferenceFile, ReferenceSnapshot


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def test_exact_money_and_decision_differences_fail() -> None:
    reference = {"portfolio": {"cash": 100.0}, "decision": "buy"}
    candidate = {"portfolio": {"cash": 100.00000001}, "decision": "sell"}

    mismatches = compare_contract(
        reference,
        candidate,
        [ComparisonRule(path="$.portfolio.cash", mode="exact")],
    )

    assert [(mismatch.path, mismatch.reason) for mismatch in mismatches] == [
        ("$.decision", "values differ"),
        ("$.portfolio.cash", "values differ"),
    ]


def test_only_allowlisted_ids_and_timestamps_normalize() -> None:
    value = {
        "id": "run-a",
        "created_at": "2026-11-03T12:00:00Z",
        "nested": {"id": "business-id", "created_at": "business-time"},
    }

    normalized = normalize_contract(value, frozenset({"$.id", "$.created_at"}))

    assert normalized == {
        "id": "<volatile>",
        "created_at": "<volatile>",
        "nested": {"id": "business-id", "created_at": "business-time"},
    }
    assert value["id"] == "run-a"


def test_default_deterministic_float_rule_accepts_small_drift() -> None:
    assert compare_contract({"score": 1.0}, {"score": 1.00000001}, []) == []


def test_missing_field_fails_at_its_json_path() -> None:
    mismatches = compare_contract(
        {"signal": {"action": "hold", "confidence": 0.5}},
        {"signal": {"action": "hold"}},
        [],
    )

    assert len(mismatches) == 1
    assert mismatches[0].path == "$.signal.confidence"
    assert mismatches[0].reason == "missing candidate field"


def test_invalid_or_duplicate_rules_are_rejected() -> None:
    with pytest.raises(ValueError):
        ComparisonRule(path="$.score", mode="unknown")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="duplicate comparison rule"):
        compare_contract(
            {"score": 1.0},
            {"score": 1.0},
            [
                ComparisonRule(path="$.score", mode="exact"),
                ComparisonRule(path="$.score", mode="float"),
            ],
        )
    with pytest.raises(ValueError, match="wildcards"):
        normalize_contract({"id": "x"}, frozenset({"$..id"}))


def test_recorder_writes_fixture_with_complete_provenance(tmp_path: Path) -> None:
    snapshot = ReferenceSnapshot(
        schema_version=1,
        files=(
            ReferenceFile(
                path="src/main.py",
                sha256="b" * 64,
                size=12,
                license_id="MIT",
            ),
        ),
        root_digest="a" * 64,
    )
    snapshot_path = tmp_path / "snapshot.json"
    input_path = tmp_path / "recording.json"
    _write_json(snapshot_path, snapshot.model_dump(mode="json"))
    _write_json(input_path, {"status": "ok", "value": 3})
    (tmp_path / ".env").write_text("SECRET=never-read", encoding="utf-8")
    script = Path(__file__).parents[2] / "scripts" / "record_ai_hedge_fund_contracts.py"

    completed = subprocess.run(
        [
            sys.executable,
            str(script),
            "--snapshot",
            str(snapshot_path),
            "--name",
            "health-response",
            "--source-file",
            "src/main.py",
            "--input",
            str(input_path),
        ],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    fixtures_root = tmp_path / "tests" / "compat" / "fixtures"
    manifest = json.loads((fixtures_root / "manifest.json").read_text(encoding="utf-8"))
    assert_fixture_provenance(manifest)
    assert manifest["fixtures"] == [
        {
            "fixture": "health-response.json",
            "license": "MIT",
            "name": "health-response",
            "recorder_version": "1",
            "snapshot_digest": "a" * 64,
            "source_file": "src/main.py",
        }
    ]
    assert json.loads((fixtures_root / "health-response.json").read_text(encoding="utf-8")) == {
        "status": "ok",
        "value": 3,
    }


def test_recorder_rejects_private_or_database_input(tmp_path: Path) -> None:
    snapshot_path = tmp_path / "snapshot.json"
    _write_json(
        snapshot_path,
        {
            "schema_version": 1,
            "files": [],
            "root_digest": "a" * 64,
        },
    )
    private_input = tmp_path / "hedge_fund.db"
    private_input.write_text("private", encoding="utf-8")
    script = Path(__file__).parents[2] / "scripts" / "record_ai_hedge_fund_contracts.py"

    completed = subprocess.run(
        [
            sys.executable,
            str(script),
            "--snapshot",
            str(snapshot_path),
            "--name",
            "private",
            "--source-file",
            "src/main.py",
            "--input",
            str(private_input),
        ],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode != 0
    assert "JSON input" in completed.stderr
    assert not (tmp_path / "tests" / "compat" / "fixtures").exists()
