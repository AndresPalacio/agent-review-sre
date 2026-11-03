"""Orchestration tests use controlled outcomes; they make no model-quality claims."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("better_harness")

CONFIG = Path(__file__).resolve().parents[3] / "optimization" / "fin.toml"


@pytest.mark.parametrize(
    "private_regression", ["none", "holdout", "scorecard", "incomplete", "noop"]
)
async def test_loop_keeps_train_gain_but_rejects_paired_holdout_regression(
    tmp_path, monkeypatch, private_regression
):
    from better_harness.core import CaseOutcome, Proposal, SplitResult
    from better_harness.patching import build_variant

    import fin_graph.optimization.harness.experiment as loop
    from fin_graph.optimization.harness.runner import SplitEvaluation

    calls = []

    async def evaluate(config, variant, split, layout):
        calls.append((variant.key, split))
        cases = config.dataset.select(split)
        outcomes = []
        for index, case in enumerate(cases):
            passed = split != "train" or (variant.key != "baseline" and index == 0)
            if split == private_regression and variant.key != "baseline" and index == 0:
                passed = False
            outcomes.append(
                CaseOutcome(
                    case.nodeid,
                    split,
                    case.stratum,
                    "passed" if passed else "failed",
                    float(passed),
                    0.01,
                )
            )
        count = sum(o.passed for o in outcomes)
        result = SplitResult(
            split,
            variant.key,
            config.experiment.model,
            count,
            len(cases),
            float(count),
            0,
            str(tmp_path),
            tuple(outcomes),
        )
        complete = not (private_regression == "incomplete" and variant.key != "baseline")
        return SplitEvaluation(result, complete, complete, {"passed": count, "total": len(cases)})

    def propose(config, current, train, layout, iteration):
        variant = build_variant(
            experiment=config.experiment,
            label=f"iter-{iteration:03d}",
            values=current.values
            if private_regression == "noop"
            else current.values | {"plan": current.values["plan"] + " Check the valuation gap."},
        )
        return Proposal(("plan",), str(tmp_path), "General valuation reasoning", "done"), variant

    monkeypatch.setattr(loop, "evaluate_split", evaluate)
    monkeypatch.setattr(loop, "services_for", lambda name: None)
    result = await loop.run_experiment(
        CONFIG, tmp_path / "run", model="offline:controlled", proposer=propose, max_iterations=1
    )
    kept = private_regression in {"none", "scorecard"}
    assert result["iterations"][0]["decision"] == ("keep" if kept else "discard")
    assert result["promotable"] == (private_regression == "none")
    assert result["selected_variant"] == ("iter-001" if kept else "baseline")
    assert calls == [
        ("baseline", "train"),
        ("baseline", "holdout"),
        ("baseline", "scorecard"),
    ] + (
        []
        if private_regression == "noop"
        else [
            ("iter-001", "train"),
            ("iter-001", "holdout"),
        ]
    ) + ([("iter-001", "scorecard")] if kept else [])
    assert (tmp_path / "run/report.json").is_file()


async def test_official_proposer_adapter_reads_only_allowed_text_edits(tmp_path, monkeypatch):
    from better_harness.core import RunLayout
    from better_harness.patching import build_baseline_variant
    from langchain_core.messages import AIMessage

    import fin_graph.optimization.harness.proposer as outer
    from fin_graph.agents.common.testing.scripted_model import ScriptedChatModel, tool_call_message
    from fin_graph.optimization.harness.configuration import load_config
    from fin_graph.optimization.harness.runner import evaluate_split
    from fin_graph.optimization.harness.surfaces import current_values

    config = load_config(CONFIG, model="scripted", repeat=1)
    layout = RunLayout(tmp_path)
    baseline = build_baseline_variant(config.experiment)
    train = await evaluate_split(config, baseline, "train", layout)
    original = current_values()

    model = ScriptedChatModel(
        responses=[
            tool_call_message(
                "edit_file",
                {
                    "file_path": "/current/plan.txt",
                    "old_string": baseline.values["plan"],
                    "new_string": "Compare the price with the intrinsic value first.",
                },
                "edit-plan",
            ),
            AIMessage(content="Applied general valuation reasoning."),
        ]
    )
    import fin_graph.agents.optimizer.proposer as proposer_factory

    real_factory = proposer_factory.create_deep_agent

    def offline_factory(**kwargs):
        return real_factory(**(kwargs | {"model": model}))

    monkeypatch.setattr(proposer_factory, "create_deep_agent", offline_factory)
    proposal, candidate = outer.propose_in_process(config, baseline, train.result, layout, 1)
    assert proposal.changed_surfaces == ("plan",)
    assert candidate.values["plan"] == "Compare the price with the intrinsic value first."
    assert current_values() == original


async def test_proposer_rejects_a_surface_symlink_outside_workspace(tmp_path, monkeypatch):
    from better_harness.core import RunLayout, SplitResult
    from better_harness.patching import build_baseline_variant

    import fin_graph.optimization.harness.proposer as outer
    from fin_graph.optimization.harness.configuration import load_config

    target = tmp_path / "outside.txt"
    target.write_text("must not be used", encoding="utf-8")
    probe = tmp_path / "probe.txt"
    try:
        probe.symlink_to(target)
    except OSError:
        pytest.skip("Windows account lacks symlink creation privilege")
    probe.unlink()
    config = load_config(CONFIG)
    baseline = build_baseline_variant(config.experiment)
    train = SplitResult("train", "baseline", config.experiment.model, 0, 9, 0, 0, str(tmp_path), ())

    def escaped_response(config, workspace):
        path = workspace.surface_files["plan"]
        path.unlink()
        path.symlink_to(target)
        return "done"

    monkeypatch.setattr(outer, "run_outer_agent", escaped_response)
    with pytest.raises(ValueError, match="escaped"):
        outer.propose_in_process(config, baseline, train, RunLayout(tmp_path / "run"), 1)
    assert target.read_text(encoding="utf-8") == "must not be used"


def test_proposer_enforces_timeout_without_materializing_candidate(tmp_path, monkeypatch):
    import subprocess

    from better_harness.core import RunLayout, SplitResult
    from better_harness.patching import build_baseline_variant

    from fin_graph.optimization.harness.configuration import load_config
    from fin_graph.optimization.harness.proposer import propose_candidate

    config = load_config(CONFIG)
    layout = RunLayout(tmp_path)
    baseline = build_baseline_variant(config.experiment)
    train = SplitResult("train", "baseline", config.experiment.model, 0, 9, 0, 0, str(tmp_path), ())

    def timed_out(command, **kwargs):
        assert kwargs["timeout"] == config.options.proposer_timeout == 1800
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr("fin_graph.optimization.harness.proposer.subprocess.run", timed_out)
    with pytest.raises(subprocess.TimeoutExpired):
        propose_candidate(config, baseline, train, layout, 1)
    assert not list(tmp_path.rglob("candidate.json"))


def test_config_rejects_external_proposer_checkout():
    from dataclasses import replace

    from fin_graph.optimization.harness.configuration import load_config, validate_config

    config = load_config(CONFIG)
    config = replace(
        config, experiment=replace(config.experiment, better_agent_deepagents_root=Path("external"))
    )
    with pytest.raises(ValueError, match="installed"):
        validate_config(config)
