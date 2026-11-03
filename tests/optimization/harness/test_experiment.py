from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

pytest.importorskip("better_harness")

ROOT = Path(__file__).resolve().parents[3]
CONFIG = ROOT / "optimization" / "fin.toml"


def test_config_is_upstream_compatible_and_limits_surfaces():
    from better_harness.core import load_experiment

    from fin_graph.optimization.harness.configuration import load_config

    config = load_config(CONFIG, model="scripted")
    upstream = load_experiment(CONFIG)
    assert len(upstream.cases) == 16
    assert set(config.experiment.surfaces) == {
        "plan",
        "analyst",
        "synthesis",
        "narrative",
        "judge",
        "portfolio",
        "tools",
        "skills",
        "planning",
        "middleware",
        "middleware_registration",
    }
    assert config.experiment.workspace_root == ROOT
    # Everything runs locally: the evaluated model and the proposer.
    assert upstream.model == upstream.better_agent_model == "ollama:llama3.2:3b"


async def test_smoke_runs_all_splits_without_proposer_and_never_claims_quality(tmp_path):
    from fin_graph.optimization.harness.experiment import run_experiment

    result = await run_experiment(CONFIG, tmp_path / "smoke", model="scripted", max_iterations=0)
    assert result["status"] == "completed"
    assert result["kind"] == "wiring"
    assert result["quality_eligible"] is False
    assert result["promotable"] is False
    assert result["baseline"] == result["final"]
    assert {s: r["total"] for s, r in result["baseline"].items()} == {
        "train": 9,
        "holdout": 4,
        "scorecard": 3,
    }
    assert all(r["passed"] == r["total"] for r in result["baseline"].values())
    assert (tmp_path / "smoke" / "report.md").is_file()
    with pytest.raises(FileExistsError):
        await run_experiment(CONFIG, tmp_path / "smoke", model="scripted", max_iterations=0)


async def test_scripted_cannot_optimize(tmp_path):
    from fin_graph.optimization.harness.experiment import run_experiment

    with pytest.raises(ValueError, match="scripted"):
        await run_experiment(CONFIG, tmp_path / "invalid", model="scripted", max_iterations=1)
    assert not (tmp_path / "invalid").exists()


async def test_missing_credentials_records_blocked_before_proposing(tmp_path, monkeypatch):
    from fin_graph.optimization.harness.experiment import run_experiment

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    result = await run_experiment(
        CONFIG, tmp_path / "blocked", model="openai:unused", max_iterations=1
    )
    assert result["status"] == "blocked"
    assert not result["quality_eligible"]
    assert not result["iterations"]
    assert (tmp_path / "blocked" / "summary.json").is_file()


async def test_train_workspace_contains_no_private_case_data(tmp_path):
    from better_harness.core import RunLayout
    from better_harness.patching import build_baseline_variant

    from fin_graph.optimization.harness.configuration import load_config
    from fin_graph.optimization.harness.proposer import prepare_workspace
    from fin_graph.optimization.harness.runner import evaluate_split

    config = load_config(CONFIG, model="scripted")
    layout = RunLayout(tmp_path)
    baseline = build_baseline_variant(config.experiment)
    train = await evaluate_split(config, baseline, "train", layout)
    workspace = prepare_workspace(config, baseline, train.result, layout, 1)
    files = list((workspace.root / "train_data").glob("*.json"))
    assert len(files) == 9
    all_text = "\n".join(
        path.read_text(encoding="utf-8") for path in workspace.root.rglob("*") if path.is_file()
    )
    assert "001-deep-value" in all_text and "tool_plan" in all_text
    for private in (*config.dataset.select("holdout"), *config.dataset.select("scorecard")):
        assert private.scenario not in all_text


def test_config_rejects_a_baseline_that_is_not_production(tmp_path):
    from fin_graph.optimization.harness.configuration import load_config, validate_config

    config = load_config(CONFIG, model="scripted")
    surfaces = dict(config.experiment.surfaces)
    surfaces["narrative"] = replace(surfaces["narrative"], base_value="Write anything.")
    modified = replace(config, experiment=replace(config.experiment, surfaces=surfaces))
    with pytest.raises(ValueError, match="production"):
        validate_config(modified)


def test_config_rejects_editable_evaluator_or_unsafe_surface_path(tmp_path):
    from fin_graph.optimization.harness.configuration import load_config, validate_config

    config = load_config(CONFIG, model="scripted")
    for patch in (
        {"target": "fin_graph.optimization.harness.quality:assess_record"},
        {"filename": "../escape.txt"},
    ):
        surfaces = dict(config.experiment.surfaces)
        surfaces["plan"] = replace(surfaces["plan"], **patch)
        modified = replace(config, experiment=replace(config.experiment, surfaces=surfaces))
        with pytest.raises(ValueError, match="surface"):
            validate_config(modified)


def test_cli_exposes_validation_and_offline_smoke():
    from typer.testing import CliRunner

    from fin_graph.cli.main import app

    result = CliRunner().invoke(app, ["harness", "validate", str(CONFIG)])
    assert result.exit_code == 0, result.output
    assert '"cases": 16' in result.output
    result = CliRunner().invoke(app, ["harness", "--help"])
    assert "smoke" in result.output and "run" in result.output
