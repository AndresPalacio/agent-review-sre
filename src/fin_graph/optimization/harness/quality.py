"""Fail-closed analysis criteria and paired candidate acceptance."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any


def _ticker_reasons(ticker: Mapping[str, Any]) -> list[str]:
    reasons = []
    if ticker.get("signal_match") is not True:
        reasons.append("signal")
    if ticker.get("category_match") is not True:
        reasons.append("category")
    if ticker.get("forbidden_category") is not False:
        reasons.append("forbidden_category")
    found, total = ticker.get("keywords_found"), ticker.get("keywords_total")
    if not isinstance(total, int) or total < 0 or found != total:
        reasons.append("required_keywords")
    if ticker.get("verified") is not True:
        reasons.append("unverified_thesis")
    if ticker.get("action_match") is False:
        reasons.append("action")
    return reasons


def assess_record(
    record: Mapping[str, Any],
    *,
    max_latency: float | None = None,
    max_tokens: int | None = None,
) -> list[str]:
    """Every criterion one run misses; an empty list is a pass.

    Per ticker: stance, category, no forbidden category, the required keywords, a verified
    thesis and (when the scenario names one) the expected action. Per run: a complete graph
    that delivered nothing, the execution contract, valid citations, the required queries and
    the latency and token budgets.
    """
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
    tickers = score.get("tickers")
    if not isinstance(tickers, list) or not tickers:
        reasons.append("missing_tickers")
    else:
        reasons.extend(dict.fromkeys(r for item in tickers for r in _ticker_reasons(item)))
    found, total = score.get("required_queries_found"), score.get("required_queries_total")
    if not isinstance(total, int) or total < 0 or found != total:
        reasons.append("required_queries")
    if not score.get("trajectory_covered"):
        reasons.append("no_observations")
    citations = score.get("citations_total", 0)
    if not isinstance(citations, int) or citations < 1 or score.get("citations_valid") != citations:
        reasons.append("citations")
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
