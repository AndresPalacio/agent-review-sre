"""Fail-closed RCA criteria and paired candidate acceptance."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any


def assess_record(
    record: Mapping[str, Any],
    *,
    max_latency: float | None = None,
    max_tokens: int | None = None,
    min_judge_score: float | None = None,
) -> list[str]:
    reasons: list[str] = []
    if record.get("status") != "completed" or record.get("graph_errors"):
        reasons.append("execution_incomplete")
    if record.get("deliveries"):
        reasons.append("external_side_effect")
    trajectory = record.get("trajectory")
    if not isinstance(trajectory, dict) or trajectory.get("passed") is not True:
        reasons.append("trajectory")
    score = record.get("score")
    if not isinstance(score, dict):
        return [*reasons, "missing_score"]
    if score.get("category_match") is not True:
        reasons.append("category")
    if score.get("forbidden_category") is not False or score.get("forbidden_keywords_found") != []:
        reasons.append("forbidden_claim")
    for metric in (
        "required_keywords",
        "ruling_out_keywords",
        "required_sources",
        "required_queries",
    ):
        found, total = score.get(f"{metric}_found"), score.get(f"{metric}_total")
        if not isinstance(total, int) or total < 0 or found != total:
            reasons.append(metric)
    if not score.get("used_sources"):
        reasons.append("no_observations")
    citations = score.get("citations_total", 0)
    if not isinstance(citations, int) or citations < 1 or score.get("citations_valid") != citations:
        reasons.append("citations")
    if score.get("llm_judge_status") not in {"not_run", "completed"}:
        reasons.append("judge_incomplete")
    if min_judge_score is not None:
        value = score.get("llm_judge_score")
        if (
            score.get("llm_judge_status") != "completed"
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value < min_judge_score
        ):
            reasons.append("reference_judge")
    if max_latency is not None:
        latency = score.get("latency_seconds")
        if (
            not isinstance(latency, (int, float))
            or not math.isfinite(latency)
            or not 0 <= latency <= max_latency
        ):
            reasons.append("latency_budget")
    if max_tokens is not None:
        tokens = score.get("tokens")
        if not isinstance(tokens, int) or tokens < 0:
            reasons.append("tokens_unavailable")
        elif tokens > max_tokens:
            reasons.append("token_budget")
    return reasons


def acceptance_decision(
    current: Mapping[str, Mapping[str, bool]], candidate: Mapping[str, Mapping[str, bool]]
) -> tuple[bool, str]:
    for split in ("train", "holdout"):
        if not current.get(split) or set(current[split]) != set(candidate.get(split, {})):
            return False, "incomplete_or_changed_cases"
    if sum(candidate["train"].values()) <= sum(current["train"].values()):
        return False, "no_train_improvement"
    if any(passed and not candidate["holdout"][key] for key, passed in current["holdout"].items()):
        return False, "holdout_regression"
    return True, "train_improved_without_holdout_regression"
