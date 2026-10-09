import json
import os
import subprocess
import sys
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "phase8_acceptance.py"


def test_offline_acceptance_checks_real_graph_and_capacity():
    run = subprocess.run(
        [
            sys.executable,
            str(_SCRIPT),
            "load",
            "--burst",
            "8",
            "--pending",
            "3",
            "--concurrent",
            "2",
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    result = json.loads(run.stdout)
    assert run.returncode == 0, result
    assert result["passed"]
    assert result["measurements"]["accepted"] == 3
    assert result["measurements"]["overloaded"] == 5
    assert all(result["criteria"].values())


def test_preflight_prints_presence_without_values(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "never-print-this-secret")
    run = subprocess.run(
        [sys.executable, str(_SCRIPT), "preflight"],
        capture_output=True,
        text=True,
        env=dict(os.environ),
        timeout=15,
    )
    result = json.loads(run.stdout)
    assert result["environment"]["OPENAI_API_KEY"]
    assert "never-print-this-secret" not in run.stdout
    assert result["validation"] == "presence_only"
