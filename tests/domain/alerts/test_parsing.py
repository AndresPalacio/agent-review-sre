from __future__ import annotations

import json
from typing import Any

import pytest

from fin_graph.domain.alerts.model import AlertParseError, AlertSource
from fin_graph.domain.alerts.parsing import detect_source, parse_alert, text_alert


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"version": "4", "groupKey": "g", "alerts": [{"labels": {}}]}, AlertSource.ALERTMANAGER),
        ({"orgId": 1, "alerts": [{"labels": {}}]}, AlertSource.GRAFANA),
        ({"alerts": [{"panelURL": "https://grafana/p"}], "receiver": "r"}, AlertSource.GRAFANA),
        ({"AlarmName": "cpu"}, AlertSource.CLOUDWATCH),
        (
            {"Type": "Notification", "TopicArn": "arn:aws:sns:x", "Message": "{}"},
            AlertSource.CLOUDWATCH,
        ),
        ({"detail-type": "CloudWatch Alarm State Change"}, AlertSource.CLOUDWATCH),
        ({"event": {"event_type": "incident.triggered"}}, AlertSource.PAGERDUTY),
        ({"action": "triggered", "data": {"event": {}}}, AlertSource.SENTRY),
        ({"culprit": "a.b", "project_name": "checkout"}, AlertSource.SENTRY),
        ({"issueUrl": "https://one.newrelic.com/x"}, AlertSource.NEWRELIC),
        ({"alert_transition": "Triggered", "title": "x"}, AlertSource.DATADOG),
        ({"event_type": "query_alert_monitor", "org": {"id": 1}}, AlertSource.DATADOG),
        ({"title": "Disk full", "labels": {}}, AlertSource.GENERIC),
    ],
)
def test_each_source_is_recognized_by_its_payload_shape(
    payload: dict[str, Any], expected: AlertSource
) -> None:
    assert detect_source(payload) is expected


def test_the_source_hint_picks_the_parser() -> None:
    payload = {"title": "[Triggered] CPU high", "alert_transition": "Triggered"}

    alert = parse_alert(payload, source_hint="Datadog")

    assert alert.source is AlertSource.DATADOG
    assert alert.title == "CPU high"


def test_a_hint_whose_parser_rejects_the_payload_falls_back_to_detection() -> None:
    payload = {"title": "Disk full", "severity": "critical"}

    alert = parse_alert(payload, source_hint="alertmanager")

    assert alert.source is AlertSource.GENERIC
    assert alert.title == "Disk full"


@pytest.mark.parametrize("wrong_hint", ["datadog", "newrelic"])
def test_wrong_vendor_hints_keep_a_cloudwatch_alarm(wrong_hint: str) -> None:
    payload = {"AlarmName": "cpu", "NewStateValue": "ALARM", "Region": "us-east-1"}

    alert = parse_alert(payload, source_hint=wrong_hint)

    assert alert.source is AlertSource.CLOUDWATCH
    assert alert.title == "cpu"


def test_a_json_string_is_parsed_like_its_object() -> None:
    payload = {"AlarmName": "cpu", "NewStateValue": "ALARM", "Region": "us-east-1"}

    alert = parse_alert(json.dumps(payload))

    assert alert.source is AlertSource.CLOUDWATCH


def test_free_text_becomes_a_generic_alert_titled_by_its_first_line() -> None:
    alert = parse_alert("checkout is returning 502s\nstarted after the 10:00 deploy")

    assert alert.source is AlertSource.GENERIC
    assert alert.title == "checkout is returning 502s"
    assert "10:00 deploy" in alert.summary


def test_a_very_long_first_line_is_shortened_for_the_title() -> None:
    alert = text_alert("x" * 300)

    assert len(alert.title) == 120
    assert alert.title.endswith("…")


def test_an_empty_message_is_not_an_alert() -> None:
    with pytest.raises(AlertParseError, match="empty"):
        parse_alert("   ")
