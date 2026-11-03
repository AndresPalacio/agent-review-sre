"""Run all cases of a split with the production graph and offline fixtures."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from better_harness.core import CaseOutcome, RunLayout, SplitResult, Variant

from fin_graph.optimization.harness.configuration import HarnessConfig
from fin_graph.optimization.harness.dataset import Split
from fin_graph.optimization.harness.execution import execute_variant
from fin_graph.optimization.harness.quality import assess_record
from fin_graph.safety.secrets import redact_secrets


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8"
    )


_FEEDBACK = (
    "ticker",
    "expected_signal",
    "signal",
    "expected_category",
    "category",
    "keywords_found",
    "keywords_total",
    "verified",
    "expected_action",
    "action",
)


def per_case_failures(records: list[dict[str, Any]]) -> list[str]:
    return sorted(
        {failure for r in records for failure in (r.get("trajectory") or {}).get("failures", [])}
    )


@dataclass(frozen=True)
class SplitEvaluation:
    result: SplitResult
    complete: bool
    quality_eligible: bool
    metrics: dict[str, Any]

    @property
    def passes(self) -> dict[str, bool]:
        return {outcome.case_id: outcome.passed for outcome in self.result.outcomes}


async def evaluate_split(
    config: HarnessConfig,
    variant: Variant,
    split: Split,
    layout: RunLayout,
) -> SplitEvaluation:
    cases = config.dataset.select(split)
    output = layout.split_dir(variant_key=variant.key, split=split)
    variant.save(layout.variant_path(variant.key))
    try:
        raw = await asyncio.to_thread(
            execute_variant, config, variant, [case.scenario for case in cases], output
        )
    except (OSError, ValueError, RuntimeError, TimeoutError) as exc:
        raw = {
            "runs": [
                {
                    "scenario": case.scenario,
                    "attempt": attempt,
                    "status": "failed",
                    "score": None,
                    "error": redact_secrets(f"{type(exc).__name__}: {exc}"),
                }
                for case in cases
                for attempt in range(1, config.options.repeat + 1)
            ]
        }
        write_json(output / "results.json", raw)
    scripted = config.experiment.model == "scripted"
    outcomes = []
    complete = True
    per_case: dict[str, Any] = {}
    for case in cases:
        records = [record for record in raw["runs"] if record["scenario"] == case.scenario]
        executed = len(records) == config.options.repeat and all(
            record["status"] == "completed"
            and not record.get("graph_errors")
            and not record.get("deliveries")
            for record in records
        )
        complete = complete and executed
        reasons = sorted(
            {
                reason
                for record in records
                for reason in assess_record(
                    record,
                    max_latency=config.options.max_case_latency,
                    max_tokens=config.options.max_case_tokens if not scripted else None,
                )
            }
        )
        passed = executed and (scripted or not reasons)
        scores = [r["score"] for r in records if r.get("score")]
        latency = sum(score["latency_seconds"] for score in scores)
        per_case[case.scenario] = {
            "passed": passed,
            "quality_passed": executed and not scripted and not reasons,
            "attempts": len(records),
            "failures": reasons,
            "trajectory_failures": per_case_failures(records),
            "tool_calls": sum(r.get("trajectory", {}).get("tool_calls", 0) for r in records),
            "agent_calls": sum(r.get("trajectory", {}).get("agent_calls", 0) for r in records),
            "latency_seconds": latency,
            "tokens": sum(s["tokens"] for s in scores)
            if scores and all(s["tokens"] is not None for s in scores)
            else None,
        }
        outcomes.append(
            CaseOutcome(
                case_id=case.nodeid,
                split=split,
                stratum=case.stratum,
                status="passed" if passed else ("failed" if executed else "error"),
                score=float(passed),
                duration_s=latency,
                failure_message=None
                if passed
                else json.dumps(
                    {
                        "criteria": reasons,
                        "execution": [r.get("error", r.get("graph_errors")) for r in records],
                        # Expected against observed per ticker: small enough for a 3B proposer.
                        "tickers": [
                            {key: ticker.get(key) for key in _FEEDBACK}
                            for score in scores
                            for ticker in score.get("tickers", [])
                        ],
                        "trajectory_failures": per_case_failures(records),
                    },
                    ensure_ascii=False,
                ),
                artifacts_dir=str(output),
            )
        )
    result = SplitResult(
        split=split,
        variant=variant.key,
        model=config.experiment.model,
        passed=sum(o.passed for o in outcomes),
        total=len(cases),
        score=float(sum(o.passed for o in outcomes)),
        returncode=0 if complete else 1,
        run_dir=str(output),
        outcomes=tuple(outcomes),
    )
    result.save(output / "result.json")
    metrics = {
        "passed": result.passed,
        "total": result.total,
        "complete": complete,
        "quality_eligible": complete and not scripted,
        "kind": "wiring" if scripted else "analysis",
        "repeat": config.options.repeat,
        "dataset_fingerprint": config.dataset.dataset_fingerprint,
        "split_fingerprint": config.dataset.fingerprint,
        "cases": per_case,
    }
    write_json(output / "metrics.json", metrics)
    return SplitEvaluation(result, complete, complete and not scripted, metrics)
