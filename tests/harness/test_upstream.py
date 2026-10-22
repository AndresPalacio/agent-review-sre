from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

pytest.importorskip("better_harness")


def test_upstream_pytest_runner_executes_exactly_one_explicit_case(tmp_path, monkeypatch):
    from better_harness.core import RunLayout
    from better_harness.patching import build_baseline_variant
    from better_harness.runners import PytestRunner

    from sre_graph.harness.configuration import load_config

    root = Path(__file__).resolve().parents[2]
    experiment = load_config(root / "harness/sre.toml", model="scripted").experiment
    experiment = replace(experiment, cases=experiment.cases[:1])
    result = PytestRunner().run_split(
        experiment=experiment,
        variant=build_baseline_variant(experiment),
        split="train",
        layout=RunLayout(tmp_path),
    )
    assert result.total == result.passed == 1, result.to_dict()
    summary_path = next(tmp_path.rglob("cases/*/summary.json"))
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert len(summary["runs"]) == 1
    assert summary["model"] == "scripted"


def test_upstream_inventory_collects_the_frozen_corpus():
    from better_harness.runners import PytestRunner

    from sre_graph.harness.configuration import load_config

    root = Path(__file__).resolve().parents[2]
    config = load_config(root / "harness/sre.toml", model="scripted")
    inventory = PytestRunner().collect_inventory(config.experiment)
    assert set(inventory) == {case.nodeid for case in config.dataset.cases}
