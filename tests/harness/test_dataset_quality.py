from __future__ import annotations

import copy

import pytest


def test_manifest_covers_corpus_and_keeps_related_cases_together():
    from sre_graph.harness.dataset import load_manifest

    manifest = load_manifest()
    assert len(manifest.cases) == 36
    assert {split: len(manifest.select(split)) for split in ("train", "holdout", "scorecard")} == {
        "train": 19,
        "holdout": 10,
        "scorecard": 7,
    }
    by_id = {case.scenario: case for case in manifest.cases}
    assert (
        by_id["rds_postgres/015-mysql-ec2-load-attribution"].split
        == by_id["rds_upstream/001-request-burst-ec2-app-tier"].split
    )


def test_manifest_rejects_leakage_duplicates_and_stale_corpus():
    from sre_graph.harness.dataset import load_manifest, validate_manifest

    manifest = load_manifest()
    for change, message in (("split", "family"), ("duplicate", "unique"), ("hash", "fingerprint")):
        value = manifest.model_dump()
        if change == "split":
            value["cases"][0]["split"] = "holdout"
        elif change == "duplicate":
            value["cases"][1]["scenario"] = value["cases"][0]["scenario"]
        else:
            value["dataset_fingerprint"] = "0" * 64
        with pytest.raises(ValueError, match=message):
            validate_manifest(value)


def good_record():
    return {
        "scenario": "eks/example",
        "attempt": 1,
        "status": "completed",
        "deliveries": [],
        "graph_errors": [],
        "trajectory": {"passed": True, "failures": []},
        "score": {
            "category_match": True,
            "forbidden_category": False,
            "required_keywords_found": 1,
            "required_keywords_total": 1,
            "ruling_out_keywords_found": 0,
            "ruling_out_keywords_total": 0,
            "forbidden_keywords_found": [],
            "required_sources_found": 1,
            "required_sources_total": 1,
            "citations_valid": 1,
            "citations_total": 1,
            "required_queries_found": 1,
            "required_queries_total": 1,
            "llm_judge_status": "not_run",
            "used_sources": ["eks_pods"],
        },
    }


@pytest.mark.parametrize(
    "field,value",
    [
        ("category_match", False),
        ("forbidden_category", True),
        ("required_keywords_found", 0),
        ("required_sources_found", 0),
        ("citations_valid", 0),
        ("citations_total", 0),
        ("forbidden_keywords_found", ["invented"]),
        ("required_queries_found", 0),
        ("used_sources", []),
        ("llm_judge_status", "failed"),
    ],
)
def test_quality_gate_rejects_wrong_or_unsupported_diagnoses(field, value):
    from sre_graph.harness.quality import assess_record

    record = good_record()
    assert assess_record(record) == []
    record["score"][field] = value
    assert assess_record(record)


def test_quality_gate_rejects_failed_execution_and_external_effects():
    from sre_graph.harness.quality import assess_record

    for patch in (
        {"status": "blocked"},
        {"deliveries": ["sent"]},
        {"score": None},
        {"graph_errors": ["timeout"]},
    ):
        assert assess_record(good_record() | patch)


def test_acceptance_requires_train_gain_and_each_previous_holdout_pass():
    from sre_graph.harness.quality import acceptance_decision

    current = {"train": {"a": False, "b": False}, "holdout": {"c": True, "d": False}}
    candidate = copy.deepcopy(current)
    candidate["train"]["a"] = True
    assert acceptance_decision(current, candidate)[0]
    candidate["holdout"] = {"c": False, "d": True}
    assert not acceptance_decision(current, candidate)[0]
    assert not acceptance_decision(current, current)[0]
    candidate["holdout"] = {"d": True}
    assert not acceptance_decision(current, candidate)[0]
