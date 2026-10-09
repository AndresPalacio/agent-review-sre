"""One deterministic parser per alert source; each returns a NormalizedAlert or raises."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

from sre_graph.domain.alerts.model import (
    AlertParseError,
    AlertSource,
    AlertStatus,
    NormalizedAlert,
    Severity,
    build_alert,
    clean_text,
    first_text,
    labels_key,
    parse_severity,
    parse_status,
    parse_timestamp,
    string_map,
)

#: Leading "[FIRING:2]" (Grafana) or "[P1] [Triggered on {host:a}]" (Datadog) tags.
_BRACKET_PREFIX = re.compile(r"^(?:\[[^\]]*\]\s*)+")
#: CloudWatch dimensions that name an identity the investigation needs.
_AWS_DIMENSIONS: dict[str, str] = {
    "ClusterName": "cluster",
    "Namespace": "namespace",
    "PodName": "pod",
    "Service": "service",
    "ServiceName": "service",
    "FunctionName": "service",
    "DBInstanceIdentifier": "service",
    "DBClusterIdentifier": "service",
}
_SUMMARY_KEYS = ("summary", "description", "message")
_ERROR_KEYS = ("error", "error_message")


def parse_alertmanager(
    payload: Mapping[str, Any], source: AlertSource = AlertSource.ALERTMANAGER
) -> NormalizedAlert:
    """An Alertmanager (or Grafana) group notification as one alert.

    Common labels and annotations describe the group; when they are empty the
    first firing alert's own are used. The group started with its earliest
    firing alert.
    """
    alerts = _list(payload.get("alerts"), "alerts")
    if any(not isinstance(item, Mapping) for item in alerts):
        raise AlertParseError("'alerts' must contain alert objects")
    if not alerts:
        raise AlertParseError("an Alertmanager notification needs a non-empty 'alerts' list")
    firing = [item for item in alerts if clean_text(item.get("status")).lower() != "resolved"]
    relevant = firing or alerts
    first = relevant[0]
    labels = string_map(payload.get("commonLabels")) or string_map(first.get("labels"))
    annotations = string_map(payload.get("commonAnnotations")) or string_map(
        first.get("annotations")
    )
    started = [moment for item in relevant if (moment := parse_timestamp(item.get("startsAt")))]
    ended = [moment for item in alerts if (moment := parse_timestamp(item.get("endsAt")))]
    status = parse_status(payload.get("status"), first.get("status"))
    title = first_text(
        labels.get("alertname"),
        _BRACKET_PREFIX.sub("", clean_text(payload.get("title"))),
        annotations.get("summary"),
        "Alert",
    )
    return build_alert(
        source=source,
        title=title,
        fingerprint_parts=(payload.get("groupKey") or labels_key(labels),),
        labels=labels,
        annotations=annotations,
        status=status,
        severity=parse_severity(labels.get("severity"), labels.get("priority")),
        started_at=min(started) if started else None,
        ended_at=max(ended) if status is AlertStatus.RESOLVED and ended else None,
        summary=first_text(*(annotations.get(key) for key in _SUMMARY_KEYS)),
        error=first_text(*(annotations.get(key) for key in _ERROR_KEYS)),
        url=first_text(first.get("generatorURL"), payload.get("externalURL")) or None,
        alert_count=len(relevant),
    )


def parse_grafana(payload: Mapping[str, Any]) -> NormalizedAlert:
    """A Grafana unified-alerting webhook (Alertmanager-shaped, with panel links)."""
    alert = parse_alertmanager(payload, source=AlertSource.GRAFANA)
    alerts = [item for item in payload.get("alerts") or [] if isinstance(item, Mapping)]
    panel = first_text(*(item.get("panelURL") or item.get("dashboardURL") for item in alerts))
    return alert.model_copy(update={"url": alert.url or panel or None})


def parse_datadog(payload: Mapping[str, Any]) -> NormalizedAlert:
    """A Datadog monitor webhook: the default body plus the common ``$VARIABLE`` keys."""
    if not (
        any(key in payload for key in ("alert_transition", "alert_type"))
        or ("event_type" in payload and "org" in payload)
    ):
        raise AlertParseError("the payload is not a Datadog monitor webhook")
    tags = string_map(payload.get("tags"))
    raw_title = first_text(
        payload.get("title"), payload.get("alert_title"), payload.get("event_title")
    )
    title = _BRACKET_PREFIX.sub("", raw_title).strip() or raw_title or "Datadog monitor"
    transition = clean_text(payload.get("alert_transition")).lower()
    alert_type = clean_text(payload.get("alert_type")).lower()
    resolved = transition in ("recovered", "resolved") or alert_type == "success"
    body = "\n".join(
        line for line in clean_text(payload.get("body")).splitlines() if line.strip() != "%%%"
    ).strip()
    return build_alert(
        source=AlertSource.DATADOG,
        title=title,
        fingerprint_parts=(
            first_text(payload.get("alert_id"), payload.get("aggreg_key"), payload.get("id"))
            or title,
        ),
        labels=tags,
        annotations={},
        status=AlertStatus.RESOLVED if resolved else AlertStatus.FIRING,
        severity=parse_severity(payload.get("priority"), tags.get("severity"), alert_type),
        started_at=parse_timestamp(payload.get("date") or payload.get("last_updated")),
        summary=first_text(body, payload.get("event_msg"), payload.get("message")),
        url=first_text(payload.get("link"), payload.get("url")) or None,
    )


def parse_cloudwatch(payload: Mapping[str, Any]) -> NormalizedAlert:
    """A CloudWatch alarm: raw, wrapped in an SNS notification, or as an EventBridge event."""
    if clean_text(payload.get("Type")) == "SubscriptionConfirmation":
        raise AlertParseError("an SNS subscription confirmation is not an alert")
    if clean_text(payload.get("detail-type")) == "CloudWatch Alarm State Change":
        payload = _eventbridge_alarm(payload)
    alarm: Any = payload.get("Message", payload)
    if isinstance(alarm, str):
        try:
            alarm = json.loads(alarm)
        except ValueError as exc:
            raise AlertParseError("the SNS message is not a CloudWatch alarm") from exc
    if not isinstance(alarm, Mapping) or not alarm.get("AlarmName"):
        raise AlertParseError("the SNS message is not a CloudWatch alarm")
    name = clean_text(alarm.get("AlarmName"))
    region = clean_text(alarm.get("Region"))
    state = clean_text(alarm.get("NewStateValue")).upper()
    trigger = _mapping(alarm.get("Trigger"))
    dimensions = {
        first_text(item.get("name"), item.get("Name")): first_text(
            item.get("value"), item.get("Value")
        )
        for item in _list(trigger.get("Dimensions"), "Trigger.Dimensions")
        if isinstance(item, Mapping)
    }
    labels = {
        key: value
        for key, value in {
            "alarm_name": name,
            "region": region,
            "account_id": clean_text(alarm.get("AWSAccountId")),
            "metric_name": clean_text(trigger.get("MetricName")),
            "metric_namespace": clean_text(trigger.get("Namespace")),
            **dimensions,
        }.items()
        if key and value
    }
    identities = {
        _AWS_DIMENSIONS[key]: value for key, value in dimensions.items() if key in _AWS_DIMENSIONS
    }
    return build_alert(
        source=AlertSource.CLOUDWATCH,
        title=name,
        fingerprint_parts=(alarm.get("AlarmArn") or f"{region}/{name}",),
        labels=labels,
        annotations={},
        status=AlertStatus.RESOLVED if state == "OK" else AlertStatus.FIRING,
        severity={"ALARM": Severity.HIGH, "OK": Severity.INFO}.get(state, Severity.WARNING),
        started_at=parse_timestamp(alarm.get("StateChangeTime")),
        summary=first_text(alarm.get("AlarmDescription"), alarm.get("NewStateReason")),
        error=clean_text(alarm.get("NewStateReason")) if state == "ALARM" else "",
        url=(
            f"https://console.aws.amazon.com/cloudwatch/home?region={region}#alarmsV2:alarm/{name}"
            if region
            else None
        ),
        **identities,
    )


def _eventbridge_alarm(event: Mapping[str, Any]) -> dict[str, Any]:
    """The SNS-style alarm fields carried by an EventBridge alarm state change."""
    detail = _mapping(event.get("detail"))
    state = _mapping(detail.get("state"))
    configuration = _mapping(detail.get("configuration"))
    metrics = _list(configuration.get("metrics"), "configuration.metrics") or [{}]
    metric = _mapping(_mapping(_mapping(metrics[0]).get("metricStat")).get("metric"))
    resources = _list(event.get("resources"), "resources")
    return {
        "AlarmName": detail.get("alarmName"),
        "AlarmDescription": configuration.get("description"),
        "AWSAccountId": event.get("account"),
        "NewStateValue": state.get("value"),
        "NewStateReason": state.get("reason"),
        "StateChangeTime": state.get("timestamp") or event.get("time"),
        "Region": event.get("region"),
        "AlarmArn": resources[0] if resources else None,
        "Trigger": {
            "MetricName": metric.get("name"),
            "Namespace": metric.get("namespace"),
            "Dimensions": [
                {"name": name, "value": value}
                for name, value in _mapping(metric.get("dimensions")).items()
            ],
        },
    }


def parse_sentry(payload: Mapping[str, Any]) -> NormalizedAlert:
    """A Sentry issue alert, issue webhook, metric alert, or legacy plugin payload."""
    data = _mapping(payload.get("data"))
    action = clean_text(payload.get("action")).lower()
    if isinstance(data.get("event"), Mapping):
        return _sentry_event(data["event"], rule=clean_text(data.get("triggered_rule")))
    if isinstance(data.get("issue"), Mapping):
        return _sentry_issue(data["issue"], action)
    if isinstance(data.get("metric_alert"), Mapping):
        return _sentry_metric_alert(data, action)
    if payload.get("culprit") is not None or payload.get("project_name"):
        event = _mapping(payload.get("event"))
        return _sentry_event(
            {**event, **payload, "title": first_text(payload.get("message"), event.get("title"))},
            rule="",
        )
    raise AlertParseError("the payload is not a Sentry webhook")


def _sentry_event(event: Mapping[str, Any], *, rule: str) -> NormalizedAlert:
    tags = {
        clean_text(pair[0]): clean_text(pair[1])
        for pair in _list(event.get("tags"), "tags")
        if isinstance(pair, list | tuple) and len(pair) == 2
    }
    project = first_text(event.get("project_slug"), event.get("project_name"), event.get("project"))
    labels = {
        key: value
        for key, value in {**tags, "project": project, "rule": rule}.items()
        if key and value
    }
    title = first_text(event.get("title"), event.get("message"), "Sentry event")
    return build_alert(
        source=AlertSource.SENTRY,
        title=title,
        fingerprint_parts=(first_text(event.get("issue_id"), event.get("event_id")) or title,),
        labels=labels,
        annotations={},
        severity=parse_severity(event.get("level"), default=Severity.HIGH),
        started_at=parse_timestamp(event.get("datetime") or event.get("timestamp")),
        summary=clean_text(event.get("culprit")),
        error=title,
        url=first_text(event.get("web_url"), event.get("issue_url"), event.get("url")) or None,
        service=project or None,
    )


def _sentry_issue(issue: Mapping[str, Any], action: str) -> NormalizedAlert:
    project = _mapping(issue.get("project"))
    slug = first_text(project.get("slug"), project.get("name"))
    title = first_text(issue.get("title"), "Sentry issue")
    resolved = action == "resolved" or clean_text(issue.get("status")).lower() == "resolved"
    return build_alert(
        source=AlertSource.SENTRY,
        title=title,
        fingerprint_parts=(first_text(issue.get("id"), issue.get("shortId")) or title,),
        labels={key: value for key, value in {"project": slug}.items() if value},
        annotations={},
        status=AlertStatus.RESOLVED if resolved else AlertStatus.FIRING,
        severity=parse_severity(issue.get("level"), default=Severity.HIGH),
        started_at=parse_timestamp(issue.get("firstSeen")),
        summary=clean_text(issue.get("culprit")),
        error=title,
        url=first_text(issue.get("permalink"), issue.get("web_url")) or None,
        service=slug or None,
    )


def _sentry_metric_alert(data: Mapping[str, Any], action: str) -> NormalizedAlert:
    alert = data["metric_alert"]
    rule = _mapping(alert.get("alert_rule"))
    title = first_text(
        alert.get("title"), data.get("description_title"), rule.get("name"), "Sentry metric alert"
    )
    return build_alert(
        source=AlertSource.SENTRY,
        title=title,
        fingerprint_parts=(first_text(alert.get("id"), rule.get("id")) or title,),
        labels={},
        annotations={},
        status=AlertStatus.RESOLVED if action == "resolved" else AlertStatus.FIRING,
        severity=parse_severity(action, default=Severity.WARNING),
        started_at=parse_timestamp(alert.get("date_started") or alert.get("date_detected")),
        summary=clean_text(data.get("description_text")),
        url=clean_text(data.get("web_url")) or None,
    )


def parse_pagerduty(payload: Mapping[str, Any]) -> NormalizedAlert:
    """A PagerDuty v3 webhook for an incident event."""
    event = _mapping(payload.get("event"))
    data = _mapping(event.get("data"))
    if not data:
        raise AlertParseError("a PagerDuty webhook needs event.data")
    event_type = clean_text(event.get("event_type"))
    service = _mapping(data.get("service"))
    priority = _mapping(data.get("priority"))
    urgency = clean_text(data.get("urgency")).lower()
    resolved = (
        clean_text(data.get("status")).lower() == "resolved" or event_type == "incident.resolved"
    )
    service_name = clean_text(service.get("summary"))
    labels = {
        key: value
        for key, value in {
            "event_type": event_type,
            "urgency": urgency,
            "priority": clean_text(priority.get("summary")),
            "incident_number": clean_text(data.get("number")),
            "service": service_name,
        }.items()
        if value
    }
    title = first_text(data.get("title"), data.get("summary"), "PagerDuty incident")
    return build_alert(
        source=AlertSource.PAGERDUTY,
        title=title,
        fingerprint_parts=(first_text(data.get("id"), data.get("incident_key")) or title,),
        labels=labels,
        annotations={},
        status=AlertStatus.RESOLVED if resolved else AlertStatus.FIRING,
        severity=parse_severity(
            priority.get("summary"),
            default=Severity.HIGH if urgency == "high" else Severity.WARNING,
        ),
        started_at=parse_timestamp(data.get("created_at") or event.get("occurred_at")),
        url=clean_text(data.get("html_url")) or None,
    )


def parse_newrelic(payload: Mapping[str, Any]) -> NormalizedAlert:
    """A New Relic workflow webhook (the default payload template)."""
    if not (
        any(key in payload for key in ("issueUrl", "alertPolicyNames", "impactedEntities"))
        or ("id" in payload and "state" in payload)
    ):
        raise AlertParseError("the payload is not a New Relic issue webhook")
    entities = [
        clean_text(item)
        for item in _list(payload.get("impactedEntities"), "impactedEntities")
        if item
    ]
    conditions = [
        clean_text(item)
        for item in _list(payload.get("alertConditionNames"), "alertConditionNames")
        if item
    ]
    policies = [
        clean_text(item)
        for item in _list(payload.get("alertPolicyNames"), "alertPolicyNames")
        if item
    ]
    state = clean_text(payload.get("state")).upper()
    title = first_text(payload.get("title"), *conditions, "New Relic issue")
    labels = {
        key: value
        for key, value in {
            "policy": policies[0] if policies else "",
            "condition": conditions[0] if conditions else "",
            "entities": ",".join(entities),
            "trigger": clean_text(payload.get("trigger")),
        }.items()
        if value
    }
    closed = state == "CLOSED"
    return build_alert(
        source=AlertSource.NEWRELIC,
        title=title,
        fingerprint_parts=(clean_text(payload.get("id")) or title,),
        labels=labels,
        annotations={},
        status=AlertStatus.RESOLVED if closed else AlertStatus.FIRING,
        severity=parse_severity(payload.get("priority")),
        started_at=parse_timestamp(payload.get("createdAt")),
        ended_at=parse_timestamp(payload.get("updatedAt")) if closed else None,
        url=clean_text(payload.get("issueUrl")) or None,
        service=entities[0] if entities else None,
    )


def parse_generic(payload: Mapping[str, Any]) -> NormalizedAlert:
    """Any other JSON alert: labels/annotations/tags plus the usual top-level keys.

    Also reads the canonical OpenSRE shape (``title``, ``state``,
    ``alert_source``, ``commonLabels``, ``commonAnnotations``) used by the
    evaluation scenarios; a known ``alert_source`` becomes the alert's source.
    """
    labels = {
        **string_map(payload.get("tags")),
        **(string_map(payload.get("commonLabels")) or string_map(payload.get("labels"))),
    }
    annotations = string_map(payload.get("commonAnnotations")) or string_map(
        payload.get("annotations")
    )
    title = first_text(
        payload.get("title"),
        payload.get("alert_name"),
        labels.get("alertname"),
        payload.get("name"),
        annotations.get("summary"),
        payload.get("message"),
        "Alert",
    )
    declared = clean_text(payload.get("alert_source")).lower()
    source = AlertSource(declared) if declared in set(AlertSource) else AlertSource.GENERIC
    return build_alert(
        source=source,
        title=title,
        fingerprint_parts=(
            first_text(payload.get("fingerprint"), payload.get("id"))
            or f"{title}|{labels_key(labels)}",
        ),
        labels=labels,
        annotations=annotations,
        status=parse_status(payload.get("status"), payload.get("state")),
        severity=parse_severity(
            payload.get("severity"),
            labels.get("severity"),
            labels.get("priority"),
            payload.get("priority"),
        ),
        started_at=parse_timestamp(
            payload.get("startsAt")
            or payload.get("started_at")
            or payload.get("firedAt")
            or payload.get("activatedAt")
            or payload.get("timestamp")
        ),
        summary=first_text(
            *(annotations.get(key) for key in _SUMMARY_KEYS),
            payload.get("description"),
            payload.get("message"),
            payload.get("body"),
            payload.get("text"),
        ),
        error=first_text(
            *(annotations.get(key) for key in _ERROR_KEYS),
            payload.get("error"),
            payload.get("error_message"),
        ),
        url=first_text(payload.get("url"), payload.get("link"), payload.get("generatorURL"))
        or None,
    )


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _list(value: object, field: str) -> list[Any]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise AlertParseError(f"'{field}' must be a list")
    return value
