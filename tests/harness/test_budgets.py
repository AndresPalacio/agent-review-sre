from __future__ import annotations

import pytest

from sre_graph.harness.quality import assess_record


@pytest.mark.parametrize(
    "score, reason",
    [
        ({"tokens": None}, "tokens_unavailable"),
        ({"tokens": 101}, "token_budget"),
        ({"latency_seconds": 11}, "latency_budget"),
        ({"latency_seconds": float("nan")}, "latency_budget"),
        ({"llm_judge_score": 0.6, "llm_judge_status": "completed"}, "reference_judge"),
        ({"llm_judge_score": 1.0, "llm_judge_status": "failed"}, "reference_judge"),
    ],
)
def test_configured_budgets_and_reference_judge_are_hard_gates(score, reason):
    record = {
        "score": {
            "tokens": 50,
            "latency_seconds": 5,
            "llm_judge_score": 0.9,
            "llm_judge_status": "completed",
            **score,
        }
    }
    assert reason in assess_record(record, max_tokens=100, max_latency=10, min_judge_score=0.8)
