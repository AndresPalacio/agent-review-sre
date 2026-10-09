import copy

import pytest

from sre_graph.evals.comparison import compare_results


def result():
    return {
        "dataset_fingerprint": "a" * 64,
        "suite": "all",
        "model": "provider:model",
        "judge_model": None,
        "repeat": 1,
        "baseline_eligible": True,
        "runs": [
            {
                "scenario": "x/001",
                "attempt": 1,
                "status": "completed",
                "score": {"category_match": True, "citations_valid": 1, "citations_total": 1},
            }
        ],
    }


def test_compare_pass_and_regression():
    base = result()
    current = copy.deepcopy(base)
    assert compare_results(base, current)["passed"]
    current["runs"][0]["score"]["category_match"] = False
    assert not compare_results(base, current)["passed"]


@pytest.mark.parametrize(
    "change", [{"model": "scripted"}, {"baseline_eligible": False}, {"repeat": 2}]
)
def test_incomparable(change):
    current = result()
    current.update(change)
    with pytest.raises(ValueError):
        compare_results(result(), current)


def test_nan_and_duplicate_rejected():
    current = result()
    current["runs"][0]["score"]["llm_judge_score"] = float("nan")
    with pytest.raises(ValueError):
        compare_results(result(), current)
    current = result()
    current["runs"] *= 2
    with pytest.raises(ValueError):
        compare_results(result(), current)


def test_dataset_and_reference_counts_must_match():
    base = result()
    current = result()
    base["dataset_fingerprint"] = "a"
    current["dataset_fingerprint"] = "b"
    with pytest.raises(ValueError):
        compare_results(base, current)
    current = result()
    current["runs"][0]["score"].update(citations_valid=2, citations_total=2)
    with pytest.raises(ValueError):
        compare_results(result(), current)


def test_failed_or_missing_attempt_refused():
    current = result()
    current["runs"][0]["status"] = "failed"
    with pytest.raises(ValueError):
        compare_results(result(), current)
    current = result()
    current["repeat"] = 2
    with pytest.raises(ValueError):
        compare_results(result(), current)


def test_malformed_run_and_judge_refused():
    current = result()
    current["runs"] = ["bad"]
    with pytest.raises(ValueError):
        compare_results(result(), current)
    base = result()
    current = result()
    base["judge_model"] = "provider:judge"
    current["judge_model"] = "provider:judge"
    with pytest.raises(ValueError):
        compare_results(base, current)


def test_legacy_quality_without_fingerprint_is_refused():
    current = result()
    current.pop("dataset_fingerprint")
    with pytest.raises(ValueError, match="fingerprint"):
        compare_results(current, current)


@pytest.mark.parametrize(
    "field,value",
    [("model", None), ("model", ""), ("model", "scripted"), ("suite", None), ("suite", "")],
)
def test_quality_requires_explicit_model_and_suite(field, value):
    current = result()
    current[field] = value
    with pytest.raises(ValueError):
        compare_results(current, current)
