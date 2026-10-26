from __future__ import annotations

import pytest
from pydantic import ValidationError

from fin_graph.domain.alerts.model import NormalizedAlert
from fin_graph.domain.diagnosis import Diagnosis, diagnosis_schema, unknown_diagnosis
from fin_graph.domain.taxonomy import UNKNOWN


def test_the_schema_closes_the_category_to_the_allowed_names_plus_unknown(
    oom_diagnosis: Diagnosis,
) -> None:
    schema = diagnosis_schema({"pod_oomkilled", "connection_exhaustion", "not_a_category"})

    category = schema.model_json_schema()["properties"]["category"]

    assert category["enum"] == ["connection_exhaustion", "pod_oomkilled", "unknown"]
    assert "### kubernetes_workload" in category["description"]


def test_the_schema_accepts_an_allowed_category_and_is_still_a_diagnosis(
    oom_diagnosis: Diagnosis,
) -> None:
    schema = diagnosis_schema({"pod_oomkilled"})

    parsed = schema.model_validate(oom_diagnosis.model_dump())

    assert isinstance(parsed, Diagnosis)
    assert parsed.category == "pod_oomkilled"


def test_the_schema_rejects_a_category_outside_the_allowed_names(
    oom_diagnosis: Diagnosis,
) -> None:
    schema = diagnosis_schema({"connection_exhaustion"})

    with pytest.raises(ValidationError):
        schema.model_validate(oom_diagnosis.model_dump())


def test_without_a_restriction_every_category_is_allowed() -> None:
    enum = diagnosis_schema().model_json_schema()["properties"]["category"]["enum"]

    assert len(enum) == 96


def test_an_unknown_diagnosis_names_the_alert_and_the_reason(
    crashloop_alert: NormalizedAlert,
) -> None:
    diagnosis = unknown_diagnosis(crashloop_alert, "every specialist failed")

    assert diagnosis.category == UNKNOWN
    assert diagnosis.confidence == 0.0
    assert "every specialist failed" in diagnosis.root_cause
    assert diagnosis.triage_summary.startswith("KubePodCrashLooping")
