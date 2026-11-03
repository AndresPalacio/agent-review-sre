"""Bounded better-harness optimization with paired holdout protection."""

from __future__ import annotations

import asyncio
import hashlib
import importlib.metadata
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from better_harness.core import (
    CandidateEvaluation,
    IterationRecord,
    Proposal,
    RunLayout,
    RunReport,
    SplitResult,
    Variant,
)
from better_harness.patching import build_baseline_variant

from fin_graph.evals.market.runner import services_for
from fin_graph.evals.market.scenarios import load_equity_suite
from fin_graph.evals.market.trajectory import contract_for
from fin_graph.llm.models import missing_credentials, parse_model_spec
from fin_graph.optimization.harness.artifacts import save_source_baseline
from fin_graph.optimization.harness.configuration import HarnessConfig, load_config
from fin_graph.optimization.harness.dataset import SPLITS, Split
from fin_graph.optimization.harness.proposer import propose_candidate
from fin_graph.optimization.harness.quality import acceptance_decision
from fin_graph.optimization.harness.runner import SplitEvaluation, evaluate_split, write_json
from fin_graph.optimization.harness.surfaces import validate_values
from fin_graph.safety.secrets import redact_secrets

Proposer = Callable[[HarnessConfig, Variant, SplitResult, RunLayout, int], tuple[Proposal, Variant]]


def provenance(config: HarnessConfig) -> dict[str, Any]:
    root = config.experiment.workspace_root
    digest = hashlib.sha256()
    for path in sorted((root / "src" / "fin_graph").rglob("*")):
        if path.suffix not in {".py", ".md", ".json", ".yml"} or "static" in path.parts:
            continue
        digest.update(str(path.relative_to(root)).replace("\\", "/").encode())
        digest.update(path.read_bytes())
    return {
        "source_sha256": digest.hexdigest(),
        "config_sha256": hashlib.sha256(config.experiment.path.read_bytes()).hexdigest(),
        "dataset_fingerprint": config.dataset.dataset_fingerprint,
        "split_fingerprint": config.dataset.fingerprint,
        "contract_fingerprints": {
            case.key: contract_for(case).fingerprint for case in load_equity_suite("all")
        },
        "options": config.options.model_dump(),
        "versions": {
            name: importlib.metadata.version(name)
            for name in (
                "fin-graph",
                "better-harness",
                "deepagents",
                "langgraph",
                "langchain",
            )
        },
    }


def _require_credentials(model: str) -> None:
    missing = missing_credentials(parse_model_spec(model).provider)
    if missing:
        raise RuntimeError(f"{missing} is not configured")


def persist_summary(layout: RunLayout, summary: dict[str, Any]) -> None:
    write_json(layout.root / "summary.json", summary)
    lines = [
        "# fin-graph better-harness",
        "",
        f"Status: **{summary['status']}**. Mode: **{summary['kind']}**.",
        f"Quality eligible: **{summary['quality_eligible']}**. "
        f"Promotable: **{summary['promotable']}**.",
        "",
        "Scripted runs check wiring only; no analysis quality was measured."
        if summary["kind"] == "wiring"
        else "Analysis passes require every repetition to meet the fixed criteria. "
        "No automatic deployment.",
        "",
        "| Split | Baseline | Final |",
        "|---|---:|---:|",
    ]
    for split in SPLITS:
        before, after = summary["baseline"].get(split), summary["final"].get(split)
        if before and after:
            lines.append(
                f"| {split} | {before['passed']}/{before['total']} | "
                f"{after['passed']}/{after['total']} |"
            )
    if summary.get("error"):
        lines.extend(["", f"Error: {summary['error']}"])
    for item in summary["iterations"]:
        lines.extend(["", f"Iteration {item['iteration']}: {item['decision']} ({item['reason']})."])
    lines.extend(
        [
            "",
            "## Final case diagnostics",
            "",
            "| Split / case | Pass | Tool / agent calls | Failures |",
            "|---|---|---:|---|",
        ]
    )
    for split, result in summary["final"].items():
        for case, metrics in result.get("cases", {}).items():
            failures = ", ".join(
                [*metrics.get("failures", []), *metrics.get("trajectory_failures", [])]
            )
            lines.append(
                f"| {split} / {case} | {metrics['passed']} | "
                f"{metrics.get('tool_calls', 0)} / {metrics.get('agent_calls', 0)} | {failures} |"
            )
    (layout.root / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


async def run_experiment(
    config_path: Path,
    output_dir: Path,
    *,
    model: str | None = None,
    proposer_model: str | None = None,
    max_iterations: int | None = None,
    repeat: int | None = None,
    proposer: Proposer | None = None,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    config = load_config(config_path, model=model, proposer_model=proposer_model, repeat=repeat)
    experiment = config.experiment
    limit = experiment.max_iterations if max_iterations is None else max_iterations
    if not 0 <= limit <= 20:
        raise ValueError("max_iterations must be between 0 and 20")
    scripted = experiment.model == "scripted"
    if scripted and limit:
        raise ValueError("scripted runs cannot optimize or establish quality")
    if limit and experiment.better_agent_model == "scripted":
        raise ValueError("outer proposer requires a real model")
    layout = RunLayout(await asyncio.to_thread(output_dir.resolve))
    await asyncio.to_thread(layout.root.mkdir, parents=True, exist_ok=False)
    layout.write_manifest(experiment)
    save_source_baseline(experiment.workspace_root, layout.root)
    summary: dict[str, Any] = {
        "version": 1,
        "status": "running",
        "kind": "wiring" if scripted else "analysis",
        "model": experiment.model,
        "proposer_model": experiment.better_agent_model,
        "max_iterations": limit,
        "quality_eligible": False,
        "promotable": False,
        "baseline": {},
        "final": {},
        "iterations": [],
        "provenance": provenance(config),
    }
    persist_summary(layout, summary)
    if not scripted:
        try:
            services_for(experiment.model)
            if limit and proposer is None:
                _require_credentials(experiment.better_agent_model)
        except Exception as exc:
            summary.update(status="blocked", error=redact_secrets(f"{type(exc).__name__}: {exc}"))
            persist_summary(layout, summary)
            return summary

    async def evaluate(variant: Variant, split: Split) -> SplitEvaluation:
        if progress:
            progress(
                f"{variant.key}: {split} "
                f"({len(config.dataset.select(split))} cases x {config.options.repeat})"
            )
        return await evaluate_split(config, variant, split, layout)

    baseline = build_baseline_variant(experiment)
    current = baseline
    initial = {split: await evaluate(baseline, split) for split in SPLITS}
    selected = dict(initial)
    summary["baseline"] = {split: result.metrics for split, result in initial.items()}
    summary["final"] = dict(summary["baseline"])
    persist_summary(layout, summary)
    iterations: list[IterationRecord] = []
    if all(result.complete for result in initial.values()):
        for index in range(1, limit + 1):
            if all(selected["train"].passes.values()):
                break
            if progress:
                progress(f"iteration {index}: proposing from train feedback")
            try:
                proposal, candidate = await asyncio.to_thread(
                    proposer or propose_candidate,
                    config,
                    current,
                    selected["train"].result,
                    layout,
                    index,
                )
                validate_values(candidate.values, complete=True)
                if (
                    candidate.surfaces != experiment.surfaces
                    or candidate.label != f"iter-{index:03d}"
                ):
                    raise ValueError("candidate changed surface contract")
            except Exception as exc:
                summary["iterations"].append(
                    {"iteration": index, "decision": "error", "reason": type(exc).__name__}
                )
                summary.update(
                    status="failed", error=redact_secrets(f"{type(exc).__name__}: {exc}")
                )
                break
            if candidate.values == current.values:
                iterations.append(IterationRecord(index, current.key, None))
                summary["iterations"].append(
                    {"iteration": index, "decision": "discard", "reason": "no_changes"}
                )
                break
            selection_splits: tuple[Split, ...] = ("train", "holdout")
            evaluated = {split: await evaluate(candidate, split) for split in selection_splits}
            accepted, reason = acceptance_decision(
                {split: selected[split].passes for split in ("train", "holdout")},
                {split: evaluated[split].passes for split in ("train", "holdout")},
            )
            if not all(result.quality_eligible for result in evaluated.values()):
                accepted, reason = False, "incomplete_evaluation"
            decision = CandidateEvaluation(
                candidate.key,
                proposal,
                evaluated["train"].result,
                evaluated["holdout"].result,
                accepted,
                reason,
            )
            # A generic private-gate result is enough feedback; no holdout case details.
            layout.write_iteration_decision(
                iteration=index, starting_variant=current.key, proposal=proposal, candidate=decision
            )
            iterations.append(IterationRecord(index, current.key, decision))
            summary["iterations"].append(
                {
                    "iteration": index,
                    "decision": "keep" if accepted else "discard",
                    "reason": reason,
                }
            )
            if accepted:
                current, selected = candidate, selected | evaluated
            summary["final"] = {split: result.metrics for split, result in selected.items()}
            persist_summary(layout, summary)

    if current.key != baseline.key:
        selected["scorecard"] = await evaluate(current, "scorecard")
    summary["final"] = {split: result.metrics for split, result in selected.items()}
    complete = all(result.complete for result in (*initial.values(), *selected.values()))
    if summary["status"] == "running":
        summary["status"] = "completed" if complete else "failed"
    summary["quality_eligible"] = complete and not scripted and summary["status"] == "completed"
    scorecard_regression = any(
        passed and not selected["scorecard"].passes[key]
        for key, passed in initial["scorecard"].passes.items()
    )
    summary["promotable"] = (
        summary["quality_eligible"] and current.key != baseline.key and not scorecard_regression
    )
    summary["scorecard_regression"] = scorecard_regression
    summary["selected_variant"] = current.key
    report = RunReport(
        datetime.now(UTC).isoformat(),
        str(experiment.path),
        experiment.model,
        experiment.better_agent_model,
        baseline,
        current,
        initial["train"].result,
        initial["holdout"].result,
        selected["train"].result,
        selected["holdout"].result,
        initial["scorecard"].result,
        selected["scorecard"].result,
        tuple(iterations),
    )
    layout.write_report(report)
    persist_summary(layout, summary)
    return summary
