"""Explicitly verify destinations and send one rendered report per channel."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any, Protocol, cast

from pydantic import ValidationError

from fin_graph.delivery.client import PagerDutyClient, SlackClient, TelegramClient
from fin_graph.delivery.settings import PagerDutySettings, SlackSettings, TelegramSettings
from fin_graph.domain.analysis_report import AnalysisReport
from fin_graph.domain.rendering.analysis import (
    render_analysis_slack,
    render_analysis_telegram,
)
from fin_graph.domain.rendering.pagerduty import render_pagerduty_note
from fin_graph.domain.rendering.slack import render_slack
from fin_graph.domain.rendering.telegram import render_telegram
from fin_graph.domain.report import Report
from fin_graph.integrations.base import VerifyResult, VerifyStatus
from fin_graph.integrations.context import RunContext
from fin_graph.safety.secrets import redact_secrets, redact_value


class DeliveryStatus(StrEnum):
    SENT = "sent"
    FAILED = "failed"
    NOT_CONFIGURED = "not_configured"


@dataclass(frozen=True)
class DeliveryResult:
    channel: str
    status: DeliveryStatus
    detail: str
    remote_id: str | None = None


class DeliveryLedger(Protocol):
    async def claim_delivery(self, key: str, destination: str) -> str: ...

    async def complete_delivery(self, key: str) -> None: ...


class DeliveryService:
    """Clients are inert until verify_all or send_report is explicitly invoked."""

    def __init__(
        self,
        slack: SlackClient | None = None,
        telegram: TelegramClient | None = None,
        pagerduty: PagerDutyClient | None = None,
        *,
        ledger: DeliveryLedger | None = None,
    ) -> None:
        self.ledger = ledger
        self.clients: dict[str, SlackClient | TelegramClient | PagerDutyClient | None] = {
            "slack": slack,
            "telegram": telegram,
            "pagerduty": pagerduty,
        }

    def extend_run_context(self, base: RunContext) -> RunContext:
        """Expose PagerDuty to approved actions without checkpointing credentials."""
        pagerduty = self.clients["pagerduty"]
        if pagerduty is None:
            return base
        return replace(base, clients={**base.clients, "pagerduty": pagerduty})

    @classmethod
    def from_env(
        cls, env_file: str | None = ".env", *, ledger: DeliveryLedger | None = None
    ) -> DeliveryService:
        try:
            slack = SlackSettings(_env_file=env_file)
            telegram = TelegramSettings(_env_file=env_file)
            pagerduty = PagerDutySettings(_env_file=env_file)
        except ValidationError:
            raise ValueError("delivery settings are invalid") from None
        return cls(
            SlackClient(
                slack.token.get_secret_value(), timeout_seconds=slack.request_timeout_seconds
            )
            if slack.enabled and slack.token
            else None,
            TelegramClient(
                telegram.token.get_secret_value(), timeout_seconds=telegram.request_timeout_seconds
            )
            if telegram.enabled and telegram.token
            else None,
            PagerDutyClient(
                pagerduty.token.get_secret_value(),
                pagerduty.from_email,
                timeout_seconds=pagerduty.request_timeout_seconds,
            )
            if pagerduty.enabled and pagerduty.token and pagerduty.from_email
            else None,
            ledger=ledger,
        )

    async def verify_all(self) -> list[VerifyResult]:
        results: list[VerifyResult] = []
        for channel, client in self.clients.items():
            if client is None:
                results.append(VerifyResult(channel, VerifyStatus.NOT_CONFIGURED, "not configured"))
                continue
            try:
                await client.verify()
            except Exception as exc:
                results.append(
                    VerifyResult(
                        channel, VerifyStatus.FAILED, f"{type(exc).__name__}: verification failed"
                    )
                )
            else:
                results.append(
                    VerifyResult(channel, VerifyStatus.OK, "provider identity read confirmed")
                )
        return results

    async def send_report(
        self, report: Report, destinations: dict[str, str], idempotency_key: str
    ) -> list[DeliveryResult]:
        """Send once per run/channel/destination when a durable ledger is configured."""
        payloads: dict[str, Any] = {
            "slack": cast(dict[str, Any], redact_value(render_slack(report))),
            "telegram": redact_secrets(render_telegram(report)),
            "pagerduty": redact_secrets(render_pagerduty_note(report)),
        }
        return await self._send("report", payloads, destinations, idempotency_key)

    async def send_analysis(
        self, report: AnalysisReport, destinations: dict[str, str], idempotency_key: str
    ) -> list[DeliveryResult]:
        """Send an analysis report to Slack or Telegram; PagerDuty carries no analyses."""
        payloads: dict[str, Any] = {
            "slack": cast(dict[str, Any], redact_value(render_analysis_slack(report))),
            "telegram": redact_secrets(render_analysis_telegram(report)),
        }
        return await self._send("analysis", payloads, destinations, idempotency_key)

    async def _send(
        self,
        kind: str,
        payloads: dict[str, Any],
        destinations: dict[str, str],
        idempotency_key: str,
    ) -> list[DeliveryResult]:
        if not idempotency_key:
            raise ValueError("idempotency_key is required")
        unknown = set(destinations) - self.clients.keys()
        if unknown:
            raise ValueError("unsupported delivery channel")
        results: list[DeliveryResult] = []
        for channel, destination in destinations.items():
            client = self.clients[channel]
            if channel not in payloads:
                results.append(
                    DeliveryResult(channel, DeliveryStatus.FAILED, f"{channel} carries no {kind}")
                )
                continue
            if client is None:
                results.append(
                    DeliveryResult(channel, DeliveryStatus.NOT_CONFIGURED, "not configured")
                )
                continue
            claim_key = hashlib.sha256(
                json.dumps([kind, idempotency_key, channel, destination]).encode()
            ).hexdigest()
            try:
                if self.ledger is not None:
                    status = await self.ledger.claim_delivery(
                        claim_key, hashlib.sha256(destination.encode()).hexdigest()
                    )
                    if status == "completed":
                        results.append(
                            DeliveryResult(channel, DeliveryStatus.SENT, "previously sent")
                        )
                        continue
                    if status != "new":
                        results.append(
                            DeliveryResult(
                                channel, DeliveryStatus.FAILED, "delivery outcome is uncertain"
                            )
                        )
                        continue
                remote_id = await client.send(destination, payloads[channel], claim_key)
                if self.ledger is not None:
                    await self.ledger.complete_delivery(claim_key)
            except Exception as exc:
                results.append(
                    DeliveryResult(
                        channel, DeliveryStatus.FAILED, f"{type(exc).__name__}: send failed"
                    )
                )
            else:
                results.append(DeliveryResult(channel, DeliveryStatus.SENT, "sent", remote_id))
        return results
