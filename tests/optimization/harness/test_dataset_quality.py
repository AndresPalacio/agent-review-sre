from __future__ import annotations

import copy

import pytest


def test_scenario_fingerprint_is_stable_across_platform_line_endings(tmp_path):
    from fin_graph.evals.market.scenarios import EquityAnswer, EquityScenario

    roots = [tmp_path / "lf", tmp_path / "crlf"]
    contents = {
        "scenario.yml": b"tickers:\n- TEST\n",
        "prices.json": b'{\n  "close": 10\n}\n',
    }
    for root, newline in zip(roots, (b"\n", b"\r\n"), strict=True):
        root.mkdir()
        for name, content in contents.items():
            (root / name).write_bytes(content.replace(b"\n", newline))

    answer = EquityAnswer(tickers={})
    scenarios = [EquityScenario("example", {}, answer, root) for root in roots]

    assert scenarios[0].fingerprint() == scenarios[1].fingerprint()


def test_manifest_covers_corpus_and_keeps_related_cases_together():
    from fin_graph.optimization.harness.dataset import load_manifest

    manifest = load_manifest()
    assert len(manifest.cases) == 16
    assert {split: len(manifest.select(split)) for split in ("train", "holdout", "scorecard")} == {
        "train": 9,
        "holdout": 4,
        "scorecard": 3,
    }
    by_id = {case.scenario: case for case in manifest.cases}
    # The two-ticker case reuses the deep-value and overvalued companies.
    assert {
        by_id[f"equities/{key}"].split
        for key in ("001-deep-value", "003-overvalued", "012-two-tickers")
    } == {"train"}
    assert by_id["equities/001-deep-value"].nodeid == (
        "evals/harness/train/test_analysis.py::test_analysis[equities/001-deep-value]"
    )


def test_manifest_rejects_leakage_duplicates_and_stale_corpus():
    from fin_graph.optimization.harness.dataset import load_manifest, validate_manifest

    manifest = load_manifest()
    changes = (
        ("family", "family"),
        ("company", "identical company data"),
        ("duplicate", "unique"),
        ("hash", "fingerprint"),
    )
    for change, message in changes:
        value = manifest.model_dump()
        cases = {case["scenario"]: case for case in value["cases"]}
        if change == "family":
            cases["equities/001-deep-value"]["split"] = "holdout"
        elif change == "company":
            cases["equities/012-two-tickers"] |= {"split": "holdout", "family": "renamed"}
        elif change == "duplicate":
            value["cases"][1]["scenario"] = value["cases"][0]["scenario"]
        else:
            value["dataset_fingerprint"] = "0" * 64
        with pytest.raises(ValueError, match=message):
            validate_manifest(value)


def good_record():
    return {
        "scenario": "equities/example",
        "attempt": 1,
        "status": "completed",
        "deliveries": [],
        "graph_errors": [],
        "trajectory": {"passed": True, "failures": []},
        "score": {
            "tickers": [
                {
                    "ticker": "DEEP",
                    "signal_match": True,
                    "category_match": True,
                    "forbidden_category": False,
                    "keywords_found": 1,
                    "keywords_total": 1,
                    "verified": True,
                    "action_match": None,
                }
            ],
            "citations_valid": 1,
            "citations_total": 1,
            "required_queries_found": 1,
            "required_queries_total": 1,
            "trajectory_covered": 6,
        },
    }


@pytest.mark.parametrize(
    "field,value,reason",
    [
        ("signal_match", False, "signal"),
        ("category_match", False, "category"),
        ("forbidden_category", True, "forbidden_category"),
        ("keywords_found", 0, "required_keywords"),
        ("verified", False, "unverified_thesis"),
        ("action_match", False, "action"),
    ],
)
def test_quality_gate_rejects_wrong_or_unsupported_theses(field, value, reason):
    from fin_graph.optimization.harness.quality import assess_record

    record = good_record()
    assert assess_record(record) == []
    record["score"]["tickers"][0][field] = value
    assert assess_record(record) == [reason]


@pytest.mark.parametrize(
    "field,value",
    [
        ("citations_valid", 0),
        ("citations_total", 0),
        ("required_queries_found", 0),
        ("trajectory_covered", 0),
        ("tickers", []),
    ],
)
def test_quality_gate_rejects_unsupported_runs(field, value):
    from fin_graph.optimization.harness.quality import assess_record

    record = good_record()
    record["score"][field] = value
    assert assess_record(record)


def test_quality_gate_rejects_failed_execution_and_external_effects():
    from fin_graph.optimization.harness.quality import assess_record

    for patch in (
        {"status": "blocked"},
        {"deliveries": ["sent"]},
        {"score": None},
        {"graph_errors": ["fundamentals analyst failed: TimeoutError"]},
        {"trajectory": {"passed": False}},
    ):
        assert assess_record(good_record() | patch)


def test_acceptance_requires_train_gain_and_each_previous_holdout_pass():
    from fin_graph.optimization.harness.quality import acceptance_decision

    current = {"train": {"a": False, "b": False}, "holdout": {"c": True, "d": False}}
    candidate = copy.deepcopy(current)
    candidate["train"]["a"] = True
    assert acceptance_decision(current, candidate)[0]
    candidate["holdout"] = {"c": False, "d": True}
    assert not acceptance_decision(current, candidate)[0]
    assert not acceptance_decision(current, current)[0]
    candidate["holdout"] = {"d": True}
    assert not acceptance_decision(current, candidate)[0]
