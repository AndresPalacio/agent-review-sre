from __future__ import annotations

from datetime import UTC, datetime

import pytest

from sre_graph.domain.alerts.model import AlertSource, Severity
from sre_graph.domain.alerts.noise import AlertExtraction, alert_from_extraction, is_trivial_message


@pytest.mark.parametrize(
    "text", ["", "   ", "ok", "Thanks!", "thank you", "👍", "gracias", "LGTM."]
)
def test_empty_messages_and_bare_acknowledgements_are_trivial(text: str) -> None:
    assert is_trivial_message(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "checkout is down",
        "ok so the payments pods keep restarting",
        "health check passed",
        "CPU 97% on payments-prod",
    ],
)
def test_anything_that_could_be_an_alert_is_not_trivial(text: str) -> None:
    assert is_trivial_message(text) is False


def test_an_extraction_becomes_an_alert_with_its_identities() -> None:
    extraction = AlertExtraction(
        is_noise=False,
        alert_name="Payments pods restarting",
        severity=Severity.HIGH,
        source=AlertSource.DATADOG,
        service="payments-api",
        namespace="payments",
        error_message="exit code 137",
    )

    alert = alert_from_extraction(extraction, "payments pods keep restarting, exit code 137")

    assert alert.title == "Payments pods restarting"
    assert alert.severity is Severity.HIGH
    assert (alert.service, alert.namespace) == ("payments-api", "payments")
    assert alert.error == "exit code 137"
    assert alert.summary.startswith("payments pods keep restarting")


def test_an_extraction_without_a_name_is_titled_by_the_message() -> None:
    alert = alert_from_extraction(AlertExtraction(is_noise=False), "checkout 502s\nsince 10:00")

    assert alert.title == "checkout 502s"


@pytest.mark.parametrize("moment", ["2026-10-07T10:00:00-05:00", "2026-10-07T15:00:00"])
def test_extraction_times_are_stored_as_utc(moment: str) -> None:
    alert = alert_from_extraction(
        AlertExtraction(is_noise=False, started_at=datetime.fromisoformat(moment)), "checkout down"
    )

    assert alert.started_at == datetime(2026, 10, 7, 15, tzinfo=UTC)
    assert alert.started_at.tzinfo is UTC
