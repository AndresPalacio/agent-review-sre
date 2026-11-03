from __future__ import annotations

import json

import httpx
import pytest

from fin_graph.delivery.client import SlackClient, TelegramClient
from fin_graph.delivery.service import DeliveryService, DeliveryStatus
from fin_graph.delivery.settings import SlackSettings, TelegramSettings
from fin_graph.integrations.base import IntegrationError, VerifyStatus


def test_delivery_channels_are_disabled_without_credentials() -> None:
    assert SlackSettings(_env_file=None).enabled is False
    assert TelegramSettings(_env_file=None).enabled is False
    with pytest.raises(ValueError, match="token"):
        SlackSettings(_env_file=None, enabled=True)


async def test_verify_checks_identity_without_sending_a_message() -> None:
    seen: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if "slack.com" in request.url.host:
            return httpx.Response(200, json={"ok": True, "user_id": "U1"})
        return httpx.Response(200, json={"ok": True, "result": {"id": 1}})

    transport = httpx.MockTransport(respond)
    service = DeliveryService(
        SlackClient("slack-key", transport=transport),
        TelegramClient("telegram-key", transport=transport),
    )

    results = await service.verify_all()

    assert [result.status for result in results] == [VerifyStatus.OK] * 2
    assert [request.url.path for request in seen] == [
        "/api/auth.test",
        "/bottelegram-key/getMe",
    ]
    assert all(request.method == "GET" for request in seen)


@pytest.mark.parametrize("channel", ["slack", "telegram"])
async def test_delivery_rejects_provider_error_envelopes_without_echoing_secrets(
    channel: str,
) -> None:
    secret = "provider-private-value"
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json={"ok": False, "error": secret})
    )
    clients = {
        "slack": SlackClient("key", transport=transport),
        "telegram": TelegramClient("key", transport=transport),
    }
    with pytest.raises(IntegrationError) as caught:
        await clients[channel].send("42", "message", "analysis-1")
    assert secret not in str(caught.value)


async def test_telegram_rejects_a_malformed_success_body() -> None:
    client = TelegramClient(
        "key", transport=httpx.MockTransport(lambda request: httpx.Response(200, json=[]))
    )

    with pytest.raises(IntegrationError, match="send failed"):
        await client.send("42", "message", "analysis-1")


async def test_send_analysis_renders_financial_output() -> None:
    from tests.domain.test_analysis_report import _report as analysis_report

    seen: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if "slack.com" in request.url.host:
            return httpx.Response(200, json={"ok": True, "ts": "1.2"})
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 7}})

    transport = httpx.MockTransport(respond)
    service = DeliveryService(
        SlackClient("slack-key", transport=transport),
        TelegramClient("telegram-key", transport=transport),
    )

    results = await service.send_analysis(
        analysis_report(), {"slack": "C1", "telegram": "42"}, "run-1"
    )

    assert [item.status for item in results] == [DeliveryStatus.SENT, DeliveryStatus.SENT]
    assert len(seen) == 2
    assert "Análisis DEEP" in json.loads(seen[0].content)["text"]
