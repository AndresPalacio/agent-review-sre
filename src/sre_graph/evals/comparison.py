"""Fail-closed comparison of completed, compatible real-model RCA evaluations."""

from __future__ import annotations

import math
from typing import Any


def _runs(result: dict[str, Any]) -> dict[tuple[str, int], dict[str, Any]]:
    if result.get("model") == "scripted" or result.get("baseline_eligible") is not True:
        raise ValueError("quality comparison requires eligible real-model results")
    model = result.get("model")
    suite = result.get("suite")
    if (
        not isinstance(model, str)
        or not model.strip()
        or ":" not in model
        or not all(part.strip() for part in model.split(":", 1))
        or not isinstance(suite, str)
        or not suite.strip()
    ):
        raise ValueError("explicit provider:model and suite identity required")
    fingerprint = result.get("dataset_fingerprint")
    if not isinstance(fingerprint, str) or not fingerprint.strip():
        raise ValueError("dataset fingerprint missing; rerun evaluation with the current runner")
    runs = result.get("runs")
    if not isinstance(runs, list) or not runs:
        raise ValueError("evaluation has no runs")
    indexed: dict[tuple[str, int], dict[str, Any]] = {}
    for run in runs:
        if not isinstance(run, dict):
            raise ValueError("invalid run entry")
        if run.get("status") != "completed" or run.get("graph_errors"):
            raise ValueError("all runs must complete without errors")
        key = (run.get("scenario"), run.get("attempt"))
        if not isinstance(key[0], str) or type(key[1]) is not int or key[1] < 1 or key in indexed:
            raise ValueError("invalid or duplicate scenario attempt")
        score = run.get("score")
        if not isinstance(score, dict) or type(score.get("category_match")) is not bool:
            raise ValueError("category score missing")
        if result.get("judge_model") is not None and (
            score.get("llm_judge_status") != "completed"
            or type(score.get("llm_judge_score")) not in (int, float)
        ):
            raise ValueError("reference judge did not complete")
        for value in score.values():
            if isinstance(value, float) and not math.isfinite(value):
                raise ValueError("non-finite score")
        indexed[(key[0], key[1])] = score
    repeat = result.get("repeat")
    if type(repeat) is not int or repeat < 1:
        raise ValueError("invalid repeat count")
    for scenario in {key[0] for key in indexed}:
        if {key[1] for key in indexed if key[0] == scenario} != set(range(1, repeat + 1)):
            raise ValueError("missing scenario attempts")
    return indexed


def compare_results(
    baseline: dict[str, Any], current: dict[str, Any], *, tolerance: float = 0.0
) -> dict[str, Any]:
    if not math.isfinite(tolerance) or not 0 <= tolerance <= 1:
        raise ValueError("tolerance must be between zero and one")
    old, new = _runs(baseline), _runs(current)
    if (
        any(
            baseline.get(field) != current.get(field)
            for field in ("suite", "model", "judge_model", "repeat", "dataset_fingerprint")
        )
        or old.keys() != new.keys()
    ):
        raise ValueError("evaluation model, suite, repeats or scenarios differ")
    regressions = []
    for key, before in old.items():
        after = new[key]
        metrics = {
            "category_match": (float(before["category_match"]), float(after["category_match"]))
        }
        for stem in (
            "required_keywords",
            "ruling_out_keywords",
            "required_sources",
            "citations",
            "trajectory",
            "required_queries",
        ):
            suffix = (
                "valid" if stem == "citations" else "covered" if stem == "trajectory" else "found"
            )
            fields = (stem + "_" + suffix, stem + "_total")
            if any(field in before or field in after for field in fields):
                if before.get(fields[1]) != after.get(fields[1]):
                    raise ValueError("reference expectation counts differ")
                values = []
                for score in (before, after):
                    numerator, denominator = (score.get(field, "missing") for field in fields)
                    if (
                        type(numerator) not in (int, float)
                        or type(denominator) not in (int, float)
                        or denominator < 0
                        or numerator < 0
                        or numerator > denominator
                    ):
                        raise ValueError("missing or invalid ratio score")
                    values.append(numerator / denominator if denominator else 1.0)
                metrics[stem] = (values[0], values[1])
        if before.get("llm_judge_score") is not None or after.get("llm_judge_score") is not None:
            judge_values: tuple[Any, Any] = (
                before.get("llm_judge_score"),
                after.get("llm_judge_score"),
            )
            if any(
                type(value) not in (int, float) or not 0 <= value <= 1 for value in judge_values
            ):
                raise ValueError("invalid judge score")
            metrics["judge"] = judge_values
        for metric, (previous, actual) in metrics.items():
            if actual + tolerance < previous:
                regressions.append(
                    {
                        "scenario": key[0],
                        "attempt": key[1],
                        "metric": metric,
                        "baseline": previous,
                        "current": actual,
                    }
                )
        if after.get("forbidden_category") and not before.get("forbidden_category"):
            regressions.append(
                {"scenario": key[0], "attempt": key[1], "metric": "forbidden_category"}
            )
        if set(after.get("forbidden_keywords_found", [])) - set(
            before.get("forbidden_keywords_found", [])
        ):
            regressions.append(
                {"scenario": key[0], "attempt": key[1], "metric": "forbidden_keywords"}
            )
    return {"passed": not regressions, "runs_compared": len(old), "regressions": regressions}
