from __future__ import annotations

import hashlib
import hmac

import pytest

from fin_graph.server.security import REQUEST_SOURCE, SignalSource, WebhookVerifier

BODY = b'{"ticker": "AAPL", "exchange": "NASDAQ", "interval": "1D"}'


def test_a_source_without_credentials_is_refused() -> None:
    verifier = WebhookVerifier()

    for source in SignalSource:
        assert not verifier.header_authenticated(source, BODY, {"x-fin-webhook-token": ""})
        with pytest.raises(ValueError):
            verifier.verify(source, BODY, {}, {"ticker": "AAPL"})


def test_hmac_wins_over_a_token_and_compares_the_raw_body() -> None:
    verifier = WebhookVerifier(
        tokens={"price_alert": "token"}, hmac_secrets={"price_alert": "secret"}
    )
    signature = "sha256=" + hmac.new(b"secret", BODY, hashlib.sha256).hexdigest()

    assert verifier.header_authenticated(
        SignalSource.PRICE_ALERT, BODY, {"x-fin-signature": signature}
    )
    assert not verifier.header_authenticated(
        SignalSource.PRICE_ALERT, BODY + b" ", {"x-fin-signature": signature}
    )
    assert not verifier.header_authenticated(
        SignalSource.PRICE_ALERT, BODY, {"x-fin-webhook-token": "token"}
    )


def test_tradingview_may_carry_its_token_in_the_body_and_never_leaks_it() -> None:
    verifier = WebhookVerifier(tokens={"tradingview": "tv"})
    payload = {"ticker": "AAPL", "token": "tv"}

    verified = verifier.verify(SignalSource.TRADINGVIEW, BODY, {}, payload)

    assert verified == {"ticker": "AAPL"}
    with pytest.raises(ValueError):
        verifier.verify(SignalSource.TRADINGVIEW, BODY, {}, {"ticker": "AAPL", "token": "x"})
    # Only TradingView, which cannot set headers, may authenticate in the body.
    generic = WebhookVerifier(tokens={"generic": "g"})
    with pytest.raises(ValueError):
        generic.verify(SignalSource.GENERIC, BODY, {}, {"tickers": ["AAPL"], "token": "g"})


def test_a_header_token_also_strips_any_body_token() -> None:
    verifier = WebhookVerifier(tokens={"generic": "g"})

    verified = verifier.verify(
        SignalSource.GENERIC, BODY, {"x-fin-webhook-token": "g"}, {"tickers": ["A"], "token": "z"}
    )

    assert verified == {"tickers": ["A"]}


def test_every_signal_source_maps_to_a_request_source() -> None:
    assert set(REQUEST_SOURCE) == set(SignalSource)
    assert REQUEST_SOURCE[SignalSource.GENERIC] == "json"
