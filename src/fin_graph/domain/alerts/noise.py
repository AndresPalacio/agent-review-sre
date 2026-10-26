"""Separate chat from alerts: a deterministic gate, then the schema the intake model fills."""

from __future__ import annotations

import re
from datetime import datetime

from pydantic import BaseModel, Field

from fin_graph.domain.alerts.model import (
    AlertSource,
    NormalizedAlert,
    Severity,
    clean_text,
    fingerprint,
)

_TRIVIAL_MESSAGES = frozenset(
    {
        "ok", "okay", "k", "thanks", "thank you", "thx", "ty", "hi", "hello", "hey", "hola",
        "gracias", "vale", "listo", "yes", "no", "si", "sí", "got it", "cool", "nice",
        "great", "perfect", "lgtm", "ack", "done",
    }
)  # fmt: skip
_NOT_WORDS = re.compile(r"[\W_]+")


def is_trivial_message(text: str) -> bool:
    """True for an empty message, bare punctuation/emoji, or a one-word ack or greeting.

    Anything else might be an alert, so it goes to the intake model, which
    decides with :class:`AlertExtraction`. Structured payloads never reach
    this gate: a webhook body is always an alert.
    """
    words = _NOT_WORDS.sub(" ", text).strip().lower()
    return not words or words in _TRIVIAL_MESSAGES


class AlertExtraction(BaseModel):
    """What the intake model reads out of a free-text message."""

    is_noise: bool = Field(
        description=(
            "True only for casual chat, greetings, thanks or replies to an earlier report. "
            "Any alert, error, failure, warning, incident or health check, even a healthy "
            "one, is not noise. When in doubt, false."
        )
    )
    alert_name: str = Field(default="", description="Short name of the alert or the problem.")
    severity: Severity = Field(default=Severity.WARNING, description="How urgent it is.")
    source: AlertSource = Field(
        default=AlertSource.GENERIC, description="The system that raised it, if named."
    )
    service: str | None = Field(default=None, description="Affected service or application.")
    namespace: str | None = Field(default=None, description="Kubernetes namespace, if named.")
    cluster: str | None = Field(default=None, description="Cluster name, if named.")
    workload: str | None = Field(default=None, description="Deployment or workload, if named.")
    pod: str | None = Field(default=None, description="Pod name, if named.")
    error_message: str = Field(default="", description="The error line quoted in the message.")
    started_at: datetime | None = Field(
        default=None, description="When the problem started, if the message says."
    )


def alert_from_extraction(extraction: AlertExtraction, text: str) -> NormalizedAlert:
    """The NormalizedAlert for a message the intake model judged to be an alert."""
    title = clean_text(extraction.alert_name) or clean_text(text).splitlines()[0][:120]
    return NormalizedAlert(
        source=extraction.source,
        title=title or "Alert",
        fingerprint=fingerprint(extraction.source, title, extraction.service),
        severity=extraction.severity,
        started_at=extraction.started_at,
        summary=clean_text(text)[:4000],
        error=clean_text(extraction.error_message),
        service=extraction.service,
        namespace=extraction.namespace,
        cluster=extraction.cluster,
        workload=extraction.workload,
        pod=extraction.pod,
    )
