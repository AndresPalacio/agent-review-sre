"""Turn any alert payload into a NormalizedAlert: detect the source, then parse it."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from typing import Any

from fin_graph.domain.alerts.model import (
    AlertParseError,
    AlertSource,
    NormalizedAlert,
    clean_text,
    fingerprint,
)
from fin_graph.domain.alerts.sources import (
    parse_alertmanager,
    parse_cloudwatch,
    parse_datadog,
    parse_generic,
    parse_grafana,
    parse_newrelic,
    parse_pagerduty,
    parse_sentry,
)

Parser = Callable[[Mapping[str, Any]], NormalizedAlert]

PARSERS: dict[AlertSource, Parser] = {
    AlertSource.ALERTMANAGER: parse_alertmanager,
    AlertSource.GRAFANA: parse_grafana,
    AlertSource.DATADOG: parse_datadog,
    AlertSource.CLOUDWATCH: parse_cloudwatch,
    AlertSource.SENTRY: parse_sentry,
    AlertSource.PAGERDUTY: parse_pagerduty,
    AlertSource.NEWRELIC: parse_newrelic,
    AlertSource.GENERIC: parse_generic,
}
_TITLE_LIMIT = 120
_TEXT_LIMIT = 4000


def detect_source(payload: Mapping[str, Any]) -> AlertSource:
    """The source whose payload shape ``payload`` has; GENERIC when none matches."""
    alerts = payload.get("alerts")
    if isinstance(alerts, list) and alerts:
        grafana_keys = ("dashboardURL", "panelURL", "silenceURL", "valueString")
        if "orgId" in payload or any(
            isinstance(item, Mapping) and any(key in item for key in grafana_keys)
            for item in alerts
        ):
            return AlertSource.GRAFANA
        if any(key in payload for key in ("groupKey", "receiver", "version")):
            return AlertSource.ALERTMANAGER
    if (
        "AlarmName" in payload
        or ("TopicArn" in payload and "Type" in payload)
        or clean_text(payload.get("detail-type")) == "CloudWatch Alarm State Change"
    ):
        return AlertSource.CLOUDWATCH
    event = payload.get("event")
    if isinstance(event, Mapping) and clean_text(event.get("event_type")).startswith("incident."):
        return AlertSource.PAGERDUTY
    data = payload.get("data")
    if (
        isinstance(data, Mapping)
        and "action" in payload
        and any(key in data for key in ("event", "issue", "metric_alert"))
    ) or ("culprit" in payload and "project_name" in payload):
        return AlertSource.SENTRY
    if any(key in payload for key in ("issueUrl", "alertPolicyNames", "impactedEntities")):
        return AlertSource.NEWRELIC
    if any(key in payload for key in ("alert_transition", "alert_type")) or (
        "event_type" in payload and "org" in payload
    ):
        return AlertSource.DATADOG
    return AlertSource.GENERIC


def parse_alert(
    payload: Mapping[str, Any] | str, *, source_hint: str | None = None
) -> NormalizedAlert:
    """Parse a webhook body or a pasted alert into a NormalizedAlert.

    ``source_hint`` (for example the source named in the webhook route) picks
    the parser directly; if that parser rejects the payload, detection runs as
    for an unhinted payload. Text that is not JSON becomes a GENERIC alert
    whose title is its first line.
    """
    if isinstance(payload, str):
        decoded = _json_object(payload)
        if decoded is None:
            return text_alert(payload)
        payload = decoded
    hint = clean_text(source_hint).lower()
    if hint in set(AlertSource) and hint != AlertSource.GENERIC:
        try:
            return PARSERS[AlertSource(hint)](payload)
        except AlertParseError:
            pass
    return PARSERS[detect_source(payload)](payload)


def text_alert(text: str) -> NormalizedAlert:
    """A GENERIC alert from free text: first line as title, the text as summary."""
    stripped = text.strip()
    if not stripped:
        raise AlertParseError("an empty message is not an alert")
    first_line = stripped.splitlines()[0].strip()
    title = first_line if len(first_line) <= _TITLE_LIMIT else first_line[: _TITLE_LIMIT - 1] + "…"
    return NormalizedAlert(
        source=AlertSource.GENERIC,
        title=title,
        fingerprint=fingerprint(AlertSource.GENERIC, stripped),
        summary=stripped[:_TEXT_LIMIT],
    )


def _json_object(text: str) -> dict[str, Any] | None:
    stripped = text.strip()
    if not stripped.startswith("{"):
        return None
    try:
        decoded = json.loads(stripped)
    except ValueError:
        return None
    return decoded if isinstance(decoded, dict) else None
