"""Fail-closed verification of inbound trading signals (TradingView, price alerts, JSON).

Each source authenticates with either an HMAC-SHA256 of the raw body
(``X-Fin-Signature: sha256=<hex>``) or a shared token (``X-Fin-Webhook-Token``). TradingView
cannot sign or set headers, so its alert body may carry the token as ``"token"``; the field is
removed before the payload reaches the analysis. A source with nothing configured is refused.
"""

from __future__ import annotations

import hmac
from collections.abc import Mapping
from enum import StrEnum
from hashlib import sha256
from typing import Any


class SignalSource(StrEnum):
    """Who may post a signal; ``generic`` is any JSON naming tickers."""

    TRADINGVIEW = "tradingview"
    PRICE_ALERT = "price_alert"
    GENERIC = "generic"


#: The request source each signal source becomes inside the analysis.
REQUEST_SOURCE = {
    SignalSource.TRADINGVIEW: "tradingview",
    SignalSource.PRICE_ALERT: "price_alert",
    SignalSource.GENERIC: "json",
}


class WebhookVerifier:
    def __init__(
        self,
        *,
        tokens: Mapping[str, str] | None = None,
        hmac_secrets: Mapping[str, str] | None = None,
    ) -> None:
        self.tokens = dict(tokens or {})
        self.hmac_secrets = dict(hmac_secrets or {})

    def header_authenticated(
        self, source: SignalSource, raw: bytes, headers: Mapping[str, str]
    ) -> bool:
        secret = self.hmac_secrets.get(source.value)
        if secret:
            supplied = headers.get("x-fin-signature", "")
            expected = "sha256=" + hmac.new(secret.encode(), raw, sha256).hexdigest()
            return hmac.compare_digest(supplied, expected)
        token = self.tokens.get(source.value)
        supplied = headers.get("x-fin-webhook-token", "")
        return bool(token and hmac.compare_digest(supplied, token))

    def verify(
        self,
        source: SignalSource,
        raw: bytes,
        headers: Mapping[str, str],
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """The payload to analyse, without any credential, or ``ValueError``."""
        if self.header_authenticated(source, raw, headers):
            return {key: value for key, value in payload.items() if key != "token"}
        token = self.tokens.get(source.value)
        supplied = payload.get("token")
        if (
            source is SignalSource.TRADINGVIEW
            and not self.hmac_secrets.get(source.value)
            and token
            and isinstance(supplied, str)
            and hmac.compare_digest(supplied, token)
        ):
            return {key: value for key, value in payload.items() if key != "token"}
        raise ValueError("signal authentication failed")
