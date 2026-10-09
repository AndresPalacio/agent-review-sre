from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest

from sre_graph.domain.alerts.model import AlertParseError, AlertSource, AlertStatus, Severity
from sre_graph.domain.alerts.sources import (
    parse_alertmanager,
    parse_cloudwatch,
    parse_datadog,
    parse_generic,
    parse_grafana,
    parse_newrelic,
    parse_pagerduty,
    parse_sentry,
)

STARTED = datetime(2026, 10, 7, 10, 0, tzinfo=UTC)


def _alertmanager(status: str = "firing") -> dict[str, Any]:
    return {
        "version": "4",
        "groupKey": '{}:{alertname="KubePodCrashLooping"}',
        "status": status,
        "receiver": "sre-graph",
        "groupLabels": {"alertname": "KubePodCrashLooping"},
        "commonLabels": {
            "alertname": "KubePodCrashLooping",
            "severity": "critical",
            "namespace": "payments",
            "pod": "checkout-7d9f8b6c5-x2x9z",
            "cluster": "prod-eu-1",
        },
        "commonAnnotations": {"summary": "Pod is crash looping.", "description": "8 restarts."},
        "externalURL": "http://alertmanager:9093",
        "alerts": [
            {
                "status": status,
                "labels": {"alertname": "KubePodCrashLooping"},
                "annotations": {},
                "startsAt": "2026-10-07T10:02:00Z",
                "endsAt": "2026-10-07T10:30:00Z"
                if status == "resolved"
                else "0001-01-01T00:00:00Z",
                "generatorURL": "http://prometheus:9090/graph?g0.expr=up",
                "fingerprint": "b1",
            },
            {
                "status": status,
                "labels": {"alertname": "KubePodCrashLooping"},
                "annotations": {},
                "startsAt": "2026-10-07T10:00:00Z",
                "endsAt": "0001-01-01T00:00:00Z",
                "generatorURL": "http://prometheus:9090/graph?g0.expr=up",
                "fingerprint": "b2",
            },
        ],
    }


def test_an_alertmanager_group_becomes_one_alert_with_its_identities() -> None:
    alert = parse_alertmanager(_alertmanager())

    assert alert.source is AlertSource.ALERTMANAGER
    assert alert.title == "KubePodCrashLooping"
    assert alert.status is AlertStatus.FIRING
    assert alert.severity is Severity.CRITICAL
    assert alert.started_at == STARTED
    assert alert.ended_at is None
    assert (alert.namespace, alert.pod, alert.cluster) == (
        "payments",
        "checkout-7d9f8b6c5-x2x9z",
        "prod-eu-1",
    )
    assert alert.summary == "Pod is crash looping."
    assert alert.alert_count == 2
    assert alert.url == "http://prometheus:9090/graph?g0.expr=up"


def test_a_resolved_alertmanager_group_keeps_its_fingerprint_and_end() -> None:
    firing = parse_alertmanager(_alertmanager())
    resolved = parse_alertmanager(_alertmanager("resolved"))

    assert resolved.status is AlertStatus.RESOLVED
    assert resolved.ended_at == datetime(2026, 10, 7, 10, 30, tzinfo=UTC)
    assert resolved.fingerprint == firing.fingerprint


def test_an_alertmanager_payload_without_alerts_is_rejected() -> None:
    with pytest.raises(AlertParseError, match="alerts"):
        parse_alertmanager({"version": "4", "alerts": []})


@pytest.mark.parametrize("alerts", [7, {"one": {"status": "firing"}}, "oops"])
def test_malformed_alertmanager_alerts_raise_a_parse_error(alerts: Any) -> None:
    with pytest.raises(AlertParseError, match="alerts"):
        parse_alertmanager({"version": "4", "alerts": alerts})


def test_a_grafana_webhook_strips_the_status_prefix_and_links_the_panel() -> None:
    payload = {
        "receiver": "sre-graph",
        "status": "firing",
        "orgId": 1,
        "title": "[FIRING:1] HighLatency (checkout)",
        "commonLabels": {},
        "commonAnnotations": {},
        "alerts": [
            {
                "status": "firing",
                "labels": {"severity": "warning", "service": "checkout"},
                "annotations": {"summary": "p99 above 2s"},
                "startsAt": "2026-10-07T10:00:00Z",
                "panelURL": "https://grafana/d/abc?viewPanel=2",
                "generatorURL": "",
                "fingerprint": "g1",
            }
        ],
    }

    alert = parse_grafana(payload)

    assert alert.source is AlertSource.GRAFANA
    assert alert.title == "HighLatency (checkout)"
    assert alert.service == "checkout"
    assert alert.summary == "p99 above 2s"
    assert alert.url == "https://grafana/d/abc?viewPanel=2"


def test_a_datadog_monitor_reads_priority_tags_and_body() -> None:
    payload = {
        "title": "[P1] [Triggered on {service:checkout}] Error rate above 5%",
        "alert_transition": "Triggered",
        "alert_type": "error",
        "priority": "P1",
        "tags": "env:prod,service:checkout,kube_namespace:payments",
        "body": "%%%\nError rate is 7% on checkout.\n%%%",
        "date": "1791367200000",
        "alert_id": "1234",
        "link": "https://app.datadoghq.com/monitors/1234",
    }

    alert = parse_datadog(payload)

    assert alert.title == "Error rate above 5%"
    assert alert.severity is Severity.CRITICAL
    assert alert.status is AlertStatus.FIRING
    assert (alert.service, alert.namespace) == ("checkout", "payments")
    assert alert.summary == "Error rate is 7% on checkout."
    assert alert.started_at == STARTED
    assert alert.url == "https://app.datadoghq.com/monitors/1234"


def test_a_recovered_datadog_monitor_is_resolved() -> None:
    alert = parse_datadog({"title": "[Recovered] Error rate", "alert_transition": "Recovered"})

    assert alert.status is AlertStatus.RESOLVED


def test_an_unrecognizable_datadog_payload_is_rejected() -> None:
    with pytest.raises(AlertParseError, match="Datadog"):
        parse_datadog({"AlarmName": "cpu"})


def _cloudwatch_alarm(state: str = "ALARM") -> dict[str, Any]:
    return {
        "AlarmName": "payments-prod-high-cpu",
        "AlarmDescription": "CPU above 90% on payments-prod",
        "AWSAccountId": "123456789012",
        "NewStateValue": state,
        "NewStateReason": "Threshold Crossed: 1 datapoint [97.2] was greater than 90.0",
        "StateChangeTime": "2026-10-07T10:00:00.000+0000",
        "Region": "us-east-1",
        "AlarmArn": "arn:aws:cloudwatch:us-east-1:123456789012:alarm:payments-prod-high-cpu",
        "Trigger": {
            "MetricName": "CPUUtilization",
            "Namespace": "AWS/RDS",
            "Dimensions": [{"value": "payments-prod", "name": "DBInstanceIdentifier"}],
        },
    }


def test_a_cloudwatch_alarm_inside_an_sns_notification_is_unwrapped() -> None:
    envelope = {
        "Type": "Notification",
        "TopicArn": "arn:aws:sns:us-east-1:123456789012:alarms",
        "Message": json.dumps(_cloudwatch_alarm()),
    }

    alert = parse_cloudwatch(envelope)

    assert alert.source is AlertSource.CLOUDWATCH
    assert alert.title == "payments-prod-high-cpu"
    assert alert.severity is Severity.HIGH
    assert alert.service == "payments-prod"
    assert alert.started_at == STARTED
    assert alert.labels["metric_namespace"] == "AWS/RDS"
    assert alert.namespace is None
    assert alert.error.startswith("Threshold Crossed")
    assert alert.url is not None and "alarmsV2:alarm/payments-prod-high-cpu" in alert.url


def test_a_cloudwatch_alarm_back_to_ok_is_resolved_and_informational() -> None:
    alert = parse_cloudwatch(_cloudwatch_alarm("OK"))

    assert (alert.status, alert.severity, alert.error) == (
        AlertStatus.RESOLVED,
        Severity.INFO,
        "",
    )


def test_an_sns_subscription_confirmation_is_not_an_alert() -> None:
    with pytest.raises(AlertParseError, match="subscription"):
        parse_cloudwatch({"Type": "SubscriptionConfirmation", "TopicArn": "arn:aws:sns:x"})


def test_a_sentry_issue_alert_reads_the_event() -> None:
    payload = {
        "action": "triggered",
        "data": {
            "triggered_rule": "High error volume",
            "event": {
                "event_id": "e1",
                "issue_id": "42",
                "title": "KeyError: 'customer_id'",
                "level": "error",
                "culprit": "checkout.views.pay",
                "datetime": "2026-10-07T10:00:00Z",
                "web_url": "https://sentry.io/issues/42/events/e1/",
                "project_slug": "checkout",
                "tags": [["environment", "production"], ["release", "1.4.2"]],
            },
        },
    }

    alert = parse_sentry(payload)

    assert alert.title == "KeyError: 'customer_id'"
    assert alert.severity is Severity.HIGH
    assert alert.service == "checkout"
    assert alert.labels["release"] == "1.4.2"
    assert alert.labels["rule"] == "High error volume"
    assert alert.summary == "checkout.views.pay"


def test_a_resolved_sentry_issue_is_resolved() -> None:
    payload = {
        "action": "resolved",
        "data": {
            "issue": {
                "id": "42",
                "title": "KeyError: 'customer_id'",
                "level": "error",
                "status": "resolved",
                "firstSeen": "2026-10-07T10:00:00Z",
                "project": {"slug": "checkout"},
            }
        },
    }

    alert = parse_sentry(payload)

    assert alert.status is AlertStatus.RESOLVED
    assert alert.started_at == STARTED


def test_a_sentry_metric_alert_maps_its_action_to_severity() -> None:
    payload = {
        "action": "critical",
        "data": {
            "metric_alert": {"title": "p95 latency", "date_started": "2026-10-07T10:00:00Z"},
            "description_text": "p95 latency above 3s",
            "web_url": "https://sentry.io/alerts/rules/details/7/",
        },
    }

    alert = parse_sentry(payload)

    assert (alert.title, alert.severity) == ("p95 latency", Severity.CRITICAL)


def test_a_payload_that_is_not_sentry_is_rejected() -> None:
    with pytest.raises(AlertParseError):
        parse_sentry({"data": {}})


def test_a_pagerduty_incident_reads_priority_urgency_and_service() -> None:
    payload = {
        "event": {
            "id": "01DEN",
            "event_type": "incident.triggered",
            "resource_type": "incident",
            "occurred_at": "2026-10-07T10:00:00.000Z",
            "data": {
                "id": "PGR0VU2",
                "type": "incident",
                "html_url": "https://acme.pagerduty.com/incidents/PGR0VU2",
                "number": 2,
                "status": "triggered",
                "title": "Checkout error rate above 5%",
                "created_at": "2026-10-07T10:00:00Z",
                "service": {"id": "PF9KMXH", "summary": "checkout"},
                "priority": {"summary": "P2"},
                "urgency": "high",
            },
        }
    }

    alert = parse_pagerduty(payload)

    assert alert.title == "Checkout error rate above 5%"
    assert alert.severity is Severity.HIGH
    assert alert.service == "checkout"
    assert alert.url == "https://acme.pagerduty.com/incidents/PGR0VU2"
    assert alert.started_at == STARTED


def test_a_resolved_pagerduty_incident_is_resolved() -> None:
    payload = {"event": {"event_type": "incident.resolved", "data": {"id": "P1", "title": "x"}}}

    assert parse_pagerduty(payload).status is AlertStatus.RESOLVED


def test_a_new_relic_issue_reads_entities_and_priority() -> None:
    payload = {
        "id": "a1b2",
        "issueUrl": "https://one.newrelic.com/alerts-ai/issues/a1b2",
        "title": "Checkout latency above threshold",
        "priority": "CRITICAL",
        "impactedEntities": ["checkout-api", "checkout-db"],
        "state": "ACTIVATED",
        "trigger": "STATE_CHANGE",
        "createdAt": 1791367200000,
        "alertPolicyNames": ["Checkout"],
        "alertConditionNames": ["Latency"],
    }

    alert = parse_newrelic(payload)

    assert alert.severity is Severity.CRITICAL
    assert alert.service == "checkout-api"
    assert alert.labels["policy"] == "Checkout"
    assert alert.started_at == STARTED


def test_a_closed_new_relic_issue_is_resolved() -> None:
    alert = parse_newrelic(
        {"id": "a1", "title": "x", "state": "CLOSED", "updatedAt": 1791367200000}
    )

    assert alert.status is AlertStatus.RESOLVED
    assert alert.ended_at == STARTED


def test_an_unrecognizable_newrelic_payload_is_rejected() -> None:
    with pytest.raises(AlertParseError, match="New Relic"):
        parse_newrelic({"AlarmName": "cpu"})


def test_malformed_newrelic_entity_list_raises_a_parse_error() -> None:
    with pytest.raises(AlertParseError, match="impactedEntities"):
        parse_newrelic({"issueUrl": "https://one.newrelic.com/issues/1", "impactedEntities": 7})


def test_the_canonical_opensre_shape_keeps_its_declared_source() -> None:
    payload = {
        "title": "[synthetic-k8s] CrashLoopBackOff — payments-api-7f9dd8b6c4-x7gr9",
        "state": "alerting",
        "alert_source": "datadog",
        "commonLabels": {
            "alertname": "KubernetesPodCrashLooping",
            "severity": "critical",
            "service": "payments-api",
            "cluster_name": "payments-prod-eks",
            "namespace": "payments",
        },
        "commonAnnotations": {
            "summary": "Pod payments-api-7f9dd8b6c4-x7gr9 has restarted 8 times.",
            "error": "Container exit code 137 reported by kubelet.",
            "kube_pod": "payments-api-7f9dd8b6c4-x7gr9",
        },
    }

    alert = parse_generic(payload)

    assert alert.source is AlertSource.DATADOG
    assert alert.status is AlertStatus.FIRING
    assert alert.severity is Severity.CRITICAL
    assert alert.pod == "payments-api-7f9dd8b6c4-x7gr9"
    assert alert.cluster == "payments-prod-eks"
    assert alert.error == "Container exit code 137 reported by kubelet."


def test_a_generic_alert_with_an_unknown_declared_source_stays_generic() -> None:
    alert = parse_generic({"title": "Disk full", "alert_source": "zabbix", "state": "normal"})

    assert (alert.source, alert.status) == (AlertSource.GENERIC, AlertStatus.RESOLVED)


def test_a_cloudwatch_alarm_delivered_by_eventbridge_is_read() -> None:
    event = {
        "version": "0",
        "detail-type": "CloudWatch Alarm State Change",
        "source": "aws.cloudwatch",
        "account": "123456789012",
        "time": "2026-10-07T10:00:00Z",
        "region": "us-east-1",
        "resources": ["arn:aws:cloudwatch:us-east-1:123456789012:alarm:checkout-5xx"],
        "detail": {
            "alarmName": "checkout-5xx",
            "state": {
                "value": "ALARM",
                "reason": "Threshold Crossed",
                "timestamp": "2026-10-07T10:00:00.000+0000",
            },
            "configuration": {
                "description": "5xx above 1%",
                "metrics": [
                    {
                        "metricStat": {
                            "metric": {
                                "namespace": "AWS/ApplicationELB",
                                "name": "HTTPCode_Target_5XX_Count",
                                "dimensions": {"LoadBalancer": "app/checkout/abc"},
                            }
                        }
                    }
                ],
            },
        },
    }

    alert = parse_cloudwatch(event)

    assert (alert.title, alert.severity, alert.started_at) == (
        "checkout-5xx",
        Severity.HIGH,
        STARTED,
    )
    assert alert.labels["LoadBalancer"] == "app/checkout/abc"
    assert alert.summary == "5xx above 1%"


def test_malformed_eventbridge_metrics_raise_a_parse_error() -> None:
    event = {
        "detail-type": "CloudWatch Alarm State Change",
        "detail": {"alarmName": "cpu", "configuration": {"metrics": {"foo": 1}}},
    }

    with pytest.raises(AlertParseError, match="metrics"):
        parse_cloudwatch(event)
