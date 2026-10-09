from __future__ import annotations

import json

import httpx
import pytest

from sre_graph.delivery.client import PagerDutyClient, SlackClient, TelegramClient
from sre_graph.delivery.service import DeliveryService, DeliveryStatus
from sre_graph.delivery.settings import PagerDutySettings, SlackSettings, TelegramSettings
from sre_graph.domain.alerts.model import AlertSource, NormalizedAlert
from sre_graph.domain.diagnosis import unknown_diagnosis
from sre_graph.domain.report import Report
from sre_graph.integrations.base import IntegrationError, VerifyStatus
from sre_graph.integrations.context import RunContext


def test_pagerduty_client_is_available_to_approved_actions_only_when_configured() -> None:
    client = PagerDutyClient("token", "oncall@example.org")
    base = RunContext(clients={"kubernetes": object()})

    merged = DeliveryService(pagerduty=client).extend_run_context(base)

    assert merged.clients["pagerduty"] is client
    assert "pagerduty" not in DeliveryService().extend_run_context(base).clients
    assert "pagerduty" not in base.clients


def _report(secret: str = "none") -> Report:
    alert = NormalizedAlert(source=AlertSource.GENERIC, title="API error", fingerprint="f1")
    return Report(alert=alert, diagnosis=unknown_diagnosis(alert, secret))


def test_delivery_channels_are_disabled_without_credentials() -> None:
    assert SlackSettings(_env_file=None).enabled is False
    assert TelegramSettings(_env_file=None).enabled is False
    assert PagerDutySettings(_env_file=None).enabled is False
    with pytest.raises(ValueError, match="token"):
        SlackSettings(_env_file=None, enabled=True)


def test_delivery_settings_error_does_not_expose_a_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = "QwErTy1234567890"
    monkeypatch.setenv("PAGERDUTY_DELIVERY_ENABLED", "true")
    monkeypatch.setenv("PAGERDUTY_DELIVERY_TOKEN", marker * 5)
    monkeypatch.delenv("PAGERDUTY_DELIVERY_FROM_EMAIL", raising=False)

    with pytest.raises(ValueError) as caught:
        DeliveryService.from_env(env_file=None)

    assert marker not in str(caught.value)


async def test_verify_checks_identity_without_sending_a_message() -> None:
    seen: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if "slack.com" in request.url.host:
            return httpx.Response(200, json={"ok": True, "user_id": "U1"})
        if "telegram.org" in request.url.host:
            return httpx.Response(200, json={"ok": True, "result": {"id": 1}})
        return httpx.Response(200, json={"user": {"id": "U2"}})

    transport = httpx.MockTransport(respond)
    service = DeliveryService(
        SlackClient("slack-key", transport=transport),
        TelegramClient("telegram-key", transport=transport),
        PagerDutyClient("pager-key", "oncall@example.com", transport=transport),
    )

    results = await service.verify_all()

    assert [result.status for result in results] == [VerifyStatus.OK] * 3
    assert [request.url.path for request in seen] == [
        "/api/auth.test",
        "/bottelegram-key/getMe",
        "/users/me",
    ]
    assert all(request.method == "GET" for request in seen)


async def test_send_report_uses_renderers_redaction_and_provider_destinations() -> None:
    seen: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if "slack.com" in request.url.host:
            return httpx.Response(200, json={"ok": True, "ts": "123.45"})
        if "telegram.org" in request.url.host:
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 22}})
        return httpx.Response(201, json={"note": {"id": "N1"}})

    transport = httpx.MockTransport(respond)
    service = DeliveryService(
        SlackClient("slack-key", transport=transport),
        TelegramClient("telegram-key", transport=transport),
        PagerDutyClient("pager-key", "oncall@example.com", transport=transport),
    )
    secret = "sk-proj-" + "A" * 25

    results = await service.send_report(
        _report(secret), {"slack": "C123", "telegram": "42", "pagerduty": "P123"}, "incident-1"
    )

    assert [result.status for result in results] == [DeliveryStatus.SENT] * 3
    assert [request.url.path for request in seen] == [
        "/api/chat.postMessage",
        "/bottelegram-key/sendMessage",
        "/incidents/P123/notes",
    ]
    bodies = [json.loads(request.content) for request in seen]
    assert bodies[0]["channel"] == "C123"
    assert bodies[0]["client_msg_id"]
    assert bodies[1]["parse_mode"] == "HTML"
    assert bodies[1]["chat_id"] == "42"
    assert bodies[2]["note"]["content"].startswith("sre-graph RCA")
    assert all(secret not in request.content.decode() for request in seen)


@pytest.mark.parametrize("channel", ["slack", "telegram", "pagerduty"])
async def test_delivery_rejects_provider_error_envelopes_without_echoing_secrets(
    channel: str,
) -> None:
    secret = "provider-private-value"
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            json={"ok": False, "error": secret} if channel != "pagerduty" else {"error": secret},
        )
    )
    clients = {
        "slack": SlackClient("key", transport=transport),
        "telegram": TelegramClient("key", transport=transport),
        "pagerduty": PagerDutyClient("key", "oncall@example.com", transport=transport),
    }
    with pytest.raises(IntegrationError) as caught:
        await clients[channel].send("P123", "message", "incident-1")
    assert secret not in str(caught.value)


async def test_telegram_rejects_a_malformed_success_body() -> None:
    client = TelegramClient(
        "key", transport=httpx.MockTransport(lambda request: httpx.Response(200, json=[]))
    )

    with pytest.raises(IntegrationError, match="send failed"):
        await client.send("42", "message", "incident-1")
