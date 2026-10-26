"""The normalized alert every intake path produces, whatever system fired it."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class AlertSource(StrEnum):
    """Systems whose alert payloads have a dedicated parser."""

    ALERTMANAGER = "alertmanager"
    GRAFANA = "grafana"
    DATADOG = "datadog"
    CLOUDWATCH = "cloudwatch"
    SENTRY = "sentry"
    PAGERDUTY = "pagerduty"
    NEWRELIC = "newrelic"
    GENERIC = "generic"


class Severity(StrEnum):
    """How urgent an alert is, on one scale for every source."""

    CRITICAL = "critical"
    HIGH = "high"
    WARNING = "warning"
    INFO = "info"


class AlertStatus(StrEnum):
    """Whether the alerting condition is still active."""

    FIRING = "firing"
    RESOLVED = "resolved"


class AlertParseError(ValueError):
    """The payload is not an alert this parser understands."""


class NormalizedAlert(BaseModel):
    """One alert in a source-independent shape.

    ``labels`` and ``annotations`` keep the source's own key/value context;
    ``service`` through ``pod`` are the identities the planner and the
    specialists need, pulled out of them. Store it in graph state with
    ``model_dump(mode="json")``.
    """

    model_config = ConfigDict(frozen=True)

    source: AlertSource
    title: str
    fingerprint: str
    status: AlertStatus = AlertStatus.FIRING
    severity: Severity = Severity.WARNING
    started_at: datetime | None = None
    ended_at: datetime | None = None
    summary: str = ""
    error: str = ""
    labels: dict[str, str] = Field(default_factory=dict)
    annotations: dict[str, str] = Field(default_factory=dict)
    service: str | None = None
    namespace: str | None = None
    cluster: str | None = None
    workload: str | None = None
    pod: str | None = None
    url: str | None = None
    alert_count: int = 1

    @field_validator("started_at", "ended_at", mode="before")
    @classmethod
    def normalize_time(cls, value: object) -> datetime | None:
        """All intake paths, including model extraction, store UTC instants."""
        return parse_timestamp(value)


_SEVERITY_WORDS: dict[str, Severity] = {
    **dict.fromkeys(
        ("critical", "crit", "fatal", "emergency", "disaster", "page", "p1", "sev1", "sev-1"),
        Severity.CRITICAL,
    ),
    **dict.fromkeys(("high", "error", "major", "p2", "sev2", "sev-2"), Severity.HIGH),
    **dict.fromkeys(
        ("warning", "warn", "medium", "moderate", "minor", "p3", "sev3", "sev-3"),
        Severity.WARNING,
    ),
    **dict.fromkeys(
        ("info", "informational", "low", "none", "debug", "success", "p4", "p5", "sev4", "sev5"),
        Severity.INFO,
    ),
}
_RESOLVED_WORDS = frozenset({"resolved", "ok", "normal", "recovered", "closed", "inactive"})
_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")
#: Below this an epoch number is seconds; above it, milliseconds (year 5138 vs 1973).
_EPOCH_MILLIS_THRESHOLD = 1e11

#: Label keys naming each identity, most specific first.
IDENTITY_KEYS: dict[str, tuple[str, ...]] = {
    "service": ("service", "service_name", "app", "app_kubernetes_io_name", "job"),
    "namespace": ("namespace", "kube_namespace", "exported_namespace", "k8s_namespace_name"),
    "cluster": ("cluster", "cluster_name", "kube_cluster", "eks_cluster", "k8s_cluster_name"),
    "workload": ("deployment", "kube_deployment", "workload_name", "statefulset", "daemonset"),
    "pod": ("pod", "kube_pod", "pod_name", "exported_pod"),
}


def clean_text(value: object) -> str:
    """``value`` as stripped text; empty for None."""
    return "" if value is None else str(value).strip()


def first_text(*values: object) -> str:
    """The first value that is not empty once stripped."""
    for value in values:
        text = clean_text(value)
        if text:
            return text
    return ""


def string_map(value: object) -> dict[str, str]:
    """Labels from a dict, a ``["key:value", ...]`` list or a ``"key:value,key:value"`` string."""
    if isinstance(value, Mapping):
        pairs = [(clean_text(key), clean_text(item)) for key, item in value.items()]
    elif isinstance(value, list | tuple | str):
        items = value.split(",") if isinstance(value, str) else [str(item) for item in value]
        pairs = [
            (clean_text(key), clean_text(item))
            for key, _, item in (entry.partition(":") for entry in items if ":" in entry)
        ]
    else:
        return {}
    return {key: item for key, item in pairs if key and item}


def parse_severity(*values: object, default: Severity = Severity.WARNING) -> Severity:
    """The first value that names a known severity; ``default`` when none does."""
    for value in values:
        severity = _SEVERITY_WORDS.get(clean_text(value).lower())
        if severity is not None:
            return severity
    return default


def parse_status(*values: object) -> AlertStatus:
    """RESOLVED when the first present value says the condition cleared, else FIRING."""
    for value in values:
        text = clean_text(value).lower()
        if text:
            return AlertStatus.RESOLVED if text in _RESOLVED_WORDS else AlertStatus.FIRING
    return AlertStatus.FIRING


def parse_timestamp(value: object) -> datetime | None:
    """A UTC datetime from ISO-8601 text or epoch seconds/milliseconds; None when unusable.

    Times at or before 1970 count as unset: Alertmanager sends
    ``0001-01-01T00:00:00Z`` as the end of a firing alert.
    """
    moment: datetime | None
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, int | float):
        moment = _from_epoch(float(value))
    else:
        text = clean_text(value)
        if _NUMBER.fullmatch(text):
            moment = _from_epoch(float(text))
        else:
            try:
                moment = datetime.fromisoformat(text)
            except ValueError:
                return None
    if moment is None:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    moment = moment.astimezone(UTC)
    return moment if moment.year > 1970 else None


def identity(*sources: Mapping[str, str]) -> dict[str, str | None]:
    """Service, namespace, cluster, workload and pod named in ``sources``.

    Keys are tried most specific first; for each key the sources are tried in
    the order given (labels before annotations).
    """
    found: dict[str, str | None] = {}
    for name, keys in IDENTITY_KEYS.items():
        found[name] = next(
            (source[key] for key in keys for source in sources if source.get(key)), None
        )
    return found


def fingerprint(source: AlertSource, *parts: object) -> str:
    """A stable 16-hex id for one alert identity, used to deduplicate notifications."""
    joined = "|".join([source.value, *(clean_text(part) for part in parts)])
    return hashlib.sha256(joined.encode()).hexdigest()[:16]


def labels_key(labels: Mapping[str, str]) -> str:
    """A canonical text form of ``labels`` for fingerprints."""
    return ",".join(f"{key}={labels[key]}" for key in sorted(labels))


def _from_epoch(number: float) -> datetime | None:
    seconds = number / 1000 if abs(number) > _EPOCH_MILLIS_THRESHOLD else number
    try:
        return datetime.fromtimestamp(seconds, UTC)
    except (OverflowError, OSError, ValueError):
        return None


def build_alert(
    *,
    source: AlertSource,
    title: str,
    fingerprint_parts: tuple[object, ...],
    labels: Mapping[str, str],
    annotations: Mapping[str, str],
    **fields: Any,
) -> NormalizedAlert:
    """A NormalizedAlert whose identities come from ``labels``/``annotations`` unless given."""
    names = identity(labels, annotations)
    for name, value in names.items():
        fields.setdefault(name, value)
    return NormalizedAlert(
        source=source,
        title=title,
        fingerprint=fingerprint(source, *fingerprint_parts),
        labels=dict(labels),
        annotations=dict(annotations),
        **fields,
    )
