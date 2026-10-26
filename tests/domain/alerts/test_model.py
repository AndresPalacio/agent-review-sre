from __future__ import annotations

from datetime import UTC, datetime

import pytest

from fin_graph.domain.alerts.model import (
    AlertSource,
    AlertStatus,
    Severity,
    fingerprint,
    identity,
    parse_severity,
    parse_status,
    parse_timestamp,
    string_map,
)

MOMENT = datetime(2026, 10, 7, 10, 5, tzinfo=UTC)


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        (("critical",), Severity.CRITICAL),
        (("P1",), Severity.CRITICAL),
        (("error",), Severity.HIGH),
        (("MEDIUM",), Severity.WARNING),
        (("low",), Severity.INFO),
        ((None, "", "normal", "warning"), Severity.WARNING),
        (("bogus",), Severity.WARNING),
    ],
)
def test_severity_words_from_every_source_map_to_one_scale(
    values: tuple[object, ...], expected: Severity
) -> None:
    assert parse_severity(*values) is expected


def test_an_unknown_severity_falls_back_to_the_given_default() -> None:
    assert parse_severity("whatever", default=Severity.HIGH) is Severity.HIGH


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        (("resolved",), AlertStatus.RESOLVED),
        (("OK",), AlertStatus.RESOLVED),
        (("normal",), AlertStatus.RESOLVED),
        (("alerting",), AlertStatus.FIRING),
        ((None, "", "firing"), AlertStatus.FIRING),
        ((), AlertStatus.FIRING),
    ],
)
def test_status_is_resolved_only_when_the_first_present_value_says_so(
    values: tuple[object, ...], expected: AlertStatus
) -> None:
    assert parse_status(*values) is expected


@pytest.mark.parametrize(
    "value",
    [
        "2026-10-07T10:05:00Z",
        "2026-10-07T10:05:00.000+0000",
        "2026-10-07T12:05:00+02:00",
        1791367500,
        "1791367500000",
        MOMENT.replace(tzinfo=None),
    ],
)
def test_timestamps_in_every_format_become_utc(value: object) -> None:
    assert parse_timestamp(value) == MOMENT


@pytest.mark.parametrize("value", [None, "", "yesterday", "0001-01-01T00:00:00Z", True])
def test_unusable_timestamps_are_none(value: object) -> None:
    assert parse_timestamp(value) is None


@pytest.mark.parametrize(
    "value",
    [
        {"env": "prod", "service": "payments", "empty": ""},
        ["env:prod", "service:payments", "no-colon"],
        "env:prod, service:payments",
    ],
)
def test_labels_are_read_from_dicts_tag_lists_and_tag_strings(value: object) -> None:
    assert string_map(value) == {"env": "prod", "service": "payments"}


def test_identity_prefers_specific_keys_and_labels_over_annotations() -> None:
    labels = {"job": "node-exporter", "kube_namespace": "payments"}
    annotations = {"service": "checkout", "namespace": "other"}

    found = identity(labels, annotations)

    assert found["service"] == "checkout"
    assert found["namespace"] == "other"
    assert found["pod"] is None


def test_fingerprints_are_stable_and_source_specific() -> None:
    first = fingerprint(AlertSource.ALERTMANAGER, "group-1")

    assert first == fingerprint(AlertSource.ALERTMANAGER, "group-1")
    assert first != fingerprint(AlertSource.GRAFANA, "group-1")
    assert len(first) == 16
