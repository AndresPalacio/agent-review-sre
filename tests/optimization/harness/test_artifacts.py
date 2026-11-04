from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("better_harness")


def make_run(tmp_path):
    from better_harness.core import RunLayout
    from better_harness.patching import build_baseline_variant, build_variant

    from fin_graph.optimization.harness.artifacts import save_source_baseline
    from fin_graph.optimization.harness.configuration import load_config

    config = load_config(
        Path(__file__).resolve().parents[3] / "optimization/fin.toml", model="scripted"
    )
    layout = RunLayout(tmp_path / "run")
    layout.root.mkdir()
    save_source_baseline(config.experiment.workspace_root, layout.root)
    baseline = build_baseline_variant(config.experiment)
    candidate = build_variant(
        experiment=config.experiment,
        label="iter-001",
        values=baseline.values
        | {"skills": "Compare the price with the Graham Number.\n", "plan": "Check the gap."},
    )
    baseline.save(layout.variant_path(baseline.key))
    candidate.save(layout.variant_path(candidate.key))
    (layout.root / "summary.json").write_text(
        json.dumps({"selected_variant": candidate.key, "promotable": False, "status": "completed"}),
        encoding="utf-8",
    )
    return layout.root


def test_export_contains_reviewable_files_diff_and_qualification(tmp_path):
    from fin_graph.optimization.harness.artifacts import export_variant

    run = make_run(tmp_path)
    destination = tmp_path / "review"
    result = export_variant(run, destination)
    assert result["promotable"] is False
    assert (
        destination / "src/fin_graph/workflows/analysis/strategy/skills.md"
    ).read_text() == "Compare the price with the Graham Number.\n"
    diff = (destination / "changes.diff").read_text()
    assert "+Compare the price with the Graham Number." in diff
    prompts = (destination / "src/fin_graph/workflows/analysis/prompts.py").read_text()
    assert "PLAN_PROMPT = 'Check the gap.'" in prompts and "EXTRACT_PROMPT = (" in prompts
    import hashlib

    for relative, digest in result["files"].items():
        assert hashlib.sha256((destination / relative).read_bytes()).hexdigest() == digest
    tools = "src/fin_graph/workflows/analysis/strategy/tools.py"
    assert (destination / tools).read_bytes() == (run / "source-baseline" / tools).read_bytes()
    with pytest.raises(FileExistsError):
        export_variant(run, destination)


def test_inspection_rejects_variant_path_traversal(tmp_path):
    from fin_graph.optimization.harness.artifacts import inspect_run

    run = make_run(tmp_path)
    (run / "summary.json").write_text(json.dumps({"selected_variant": "../../secret"}))
    with pytest.raises(ValueError, match="variant"):
        inspect_run(run)


def test_exported_patch_applies_to_the_captured_source(tmp_path):
    import shutil
    import subprocess

    from fin_graph.optimization.harness.artifacts import export_variant

    run = make_run(tmp_path)
    export = tmp_path / "review"
    export_variant(run, export)
    checkout = tmp_path / "checkout"
    shutil.copytree(run / "source-baseline", checkout)
    result = subprocess.run(
        ["git", "apply", "--check", str(export / "changes.diff")],
        cwd=checkout,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_cli_exposes_contracts_inspection_and_export():
    from typer.testing import CliRunner

    from fin_graph.cli.main import app

    cli = CliRunner()
    help_text = cli.invoke(app, ["harness", "--help"]).output
    assert "inspect" in help_text and "export" in help_text and "contract" in help_text
    result = cli.invoke(app, ["harness", "contract", "equities/012-two-tickers"])
    assert result.exit_code == 0, result.output
    assert "analyst_valuation" in result.output and "compute_valuation" in result.output
    assert cli.invoke(app, ["harness", "contract", "forex/002-eurusd-breakout"]).exit_code == 2
