from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from fin_graph.domain.requests.model import (
    AnalysisRequest,
    Horizon,
    RequestParseError,
    RequestSource,
    normalize_ticker,
)
from fin_graph.domain.requests.noise import (
    RequestExtraction,
    is_trivial_message,
    request_from_extraction,
)
from fin_graph.domain.requests.parsing import (
    detect_source,
    request_from_payload,
    request_from_text,
    tickers_in_text,
)

NOW = datetime(2026, 10, 26, 15, tzinfo=UTC)


@pytest.mark.parametrize(
    ("text", "tickers"),
    [
        ("analiza AAPL y MSFT", ["AAPL", "MSFT"]),
        ("¿Cómo ves a Nvidia y a $tsla?", ["TSLA", "NVDA"]),
        ("compara BRK.B con RY.TO", ["BRK.B", "RY.TO"]),
        ("mi meta es comprar acciones de Apple", ["AAPL"]),
        ("¿QUÉ OPINAS DEL MERCADO HOY?", []),
        ("el PIB y la FED en USD", []),
        ("hola", []),
    ],
)
def test_tickers_in_text(text: str, tickers: list[str]) -> None:
    assert tickers_in_text(text) == tickers


def test_text_request_reads_horizon_and_never_dates_in_the_future() -> None:
    request = request_from_text("Analiza MSFT a largo plazo", now=NOW)

    assert request is not None
    assert request.tickers == ["MSFT"] and request.horizon is Horizon.LONG
    assert request.as_of == date(2026, 10, 26) and request.source is RequestSource.TEXT
    assert request_from_text("gracias!", now=NOW) is None


def test_generic_json_payload() -> None:
    request = request_from_payload(
        {
            "tickers": "aapl, msft",
            "as_of": "2024-03-08",
            "horizon": "short",
            "question": "¿Qué tal?",
        },
        now=NOW,
    )

    assert request.tickers == ["AAPL", "MSFT"]
    assert request.as_of == date(2024, 3, 8)
    assert request.horizon is Horizon.SHORT and request.source is RequestSource.JSON


def test_tradingview_alert_strips_the_exchange_prefix() -> None:
    payload = {"ticker": "NASDAQ:NVDA", "exchange": "NASDAQ", "interval": "1D", "close": "131.2"}

    request = request_from_payload(payload, now=NOW)

    assert detect_source(payload) is RequestSource.TRADINGVIEW
    assert request.tickers == ["NVDA"]
    assert "NASDAQ" in request.trigger and "131.2" in request.trigger


def test_price_alert_payload_keeps_the_trigger_and_clamps_future_dates() -> None:
    request = request_from_payload(
        {"symbol": "TSLA", "change_pct": -7.4, "price": 182.3, "time": 1893456000000}, now=NOW
    )

    assert request.source is RequestSource.PRICE_ALERT
    assert request.trigger == "cambio -7.4% · precio 182.3"
    assert request.as_of == NOW.date()


def test_payload_without_tickers_falls_back_to_its_question_or_fails() -> None:
    assert request_from_payload({"question": "¿Y Microsoft?"}, now=NOW).tickers == ["MSFT"]
    with pytest.raises(RequestParseError):
        request_from_payload({"question": "¿cómo va todo?"}, now=NOW)


def test_request_validation_and_fingerprint() -> None:
    first = AnalysisRequest(tickers=["msft", "AAPL", "msft"], as_of=date(2024, 3, 8))
    second = AnalysisRequest(tickers=["AAPL", "MSFT"], as_of=date(2024, 3, 8))

    assert first.tickers == ["MSFT", "AAPL"]
    assert first.fingerprint == second.fingerprint
    assert first.title == "Análisis MSFT, AAPL al 2024-03-08"
    with pytest.raises(ValidationError):
        AnalysisRequest(tickers=["not a ticker!"], as_of=date(2024, 3, 8))
    with pytest.raises(ValidationError):
        AnalysisRequest(tickers=[], as_of=date(2024, 3, 8))
    assert normalize_ticker("$brk-b") == "BRK-B"


def test_intake_model_extraction() -> None:
    found = RequestExtraction(is_noise=False, tickers=["amzn"], horizon=Horizon.LONG)
    noise = RequestExtraction(is_noise=True, tickers=["AMZN"])
    junk = RequestExtraction(is_noise=False, tickers=["???"])

    request = request_from_extraction(found, "y amazon?", now=NOW)

    assert request is not None and request.tickers == ["AMZN"]
    assert request.question == "y amazon?"
    assert request_from_extraction(noise, "", now=NOW) is None
    assert request_from_extraction(junk, "", now=NOW) is None
    assert is_trivial_message("¡Gracias!") and not is_trivial_message("analiza AAPL")
