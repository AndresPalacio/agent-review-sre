from __future__ import annotations

import pytest

from fin_graph.harness.quality import assess_record


@pytest.mark.parametrize(
    "score, reason",
    [
        ({"tokens": None}, "tokens_unavailable"),
        ({"tokens": 101}, "token_budget"),
        ({"latency_seconds": 11}, "latency_budget"),
        ({"latency_seconds": float("nan")}, "latency_budget"),
    ],
)
def test_configured_budgets_are_hard_gates(score, reason):
    record = {"score": {"tokens": 50, "latency_seconds": 5, **score}}
    assert reason in assess_record(record, max_tokens=100, max_latency=10)
