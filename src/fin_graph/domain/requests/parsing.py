"""Turn free text and webhook payloads into an :class:`AnalysisRequest`, deterministically.

Text like "analiza AAPL y MSFT" or "¿cómo ves a Nvidia?" resolves without a model: cashtags,
upper-case symbols that are not common words, and well-known company names. The intake model
is only needed when none of these match.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import UTC, date, datetime
from typing import Any

from fin_graph.domain.requests.model import (
    MAX_TICKERS,
    TICKER_PATTERN,
    AnalysisRequest,
    Horizon,
    RequestParseError,
    RequestSource,
    unique_tickers,
)

#: Company names people type instead of the symbol (lower case, accents removed). Names that
#: are everyday Spanish words ("meta", "visa") are left out; their symbols still match.
COMPANY_ALIASES: dict[str, str] = {
    "apple": "AAPL",
    "microsoft": "MSFT",
    "tesla": "TSLA",
    "nvidia": "NVDA",
    "amazon": "AMZN",
    "alphabet": "GOOGL",
    "google": "GOOGL",
    "facebook": "META",
    "netflix": "NFLX",
    "berkshire": "BRK-B",
    "jpmorgan": "JPM",
    "mastercard": "MA",
    "coca cola": "KO",
    "coca-cola": "KO",
    "pepsico": "PEP",
    "walmart": "WMT",
    "disney": "DIS",
    "intel": "INTC",
    "amd": "AMD",
    "palantir": "PLTR",
    "broadcom": "AVGO",
    "oracle": "ORCL",
    "salesforce": "CRM",
    "adobe": "ADBE",
    "mercadolibre": "MELI",
    "mercado libre": "MELI",
    "bancolombia": "CIB",
    "ecopetrol": "EC",
}
#: Upper-case words that are not tickers even though they look like one.
_NOT_TICKERS = frozenset(
    {
        "A", "I", "Y", "O", "E", "U", "DE", "LA", "EL", "LOS", "LAS", "EN", "ES", "UN", "UNA", "MI",
        "TU", "SI", "NO", "QUE", "CON", "POR", "PARA", "DEL", "AL", "LO", "SE", "YA", "HOY",
        "THE", "AND", "OR", "OF", "TO", "IN", "IS", "IT", "ON", "FOR", "BUY", "SELL", "HOLD",
        "CEO", "CFO", "IPO", "ETF", "EPS", "PE", "PEG", "ROE", "ROIC", "FCF", "DCF", "EBITDA",
        "USD", "EUR", "COP", "MXN", "GDP", "PIB", "FED", "SEC", "AI", "IA", "OK", "TTM", "YOY",
        "RSI", "SMA", "EMA", "ATR", "ADX", "NYSE", "NASDAQ", "BMV", "BVC", "Q1", "Q2", "Q3", "Q4",
    }
)  # fmt: skip
_CASHTAG = re.compile(r"\$([A-Za-z][A-Za-z0-9]{0,5}(?:[.\-][A-Za-z0-9]{1,4})?)\b")
_SYMBOL = re.compile(r"\b([A-Z][A-Z0-9]{0,5}(?:[.\-][A-Z0-9]{1,4})?)\b")
_ACCENTS = str.maketrans("áéíóúüñ", "aeiouun")
_HORIZON_WORDS = {
    Horizon.SHORT: ("corto plazo", "short term", "short-term", "esta semana", "trading", "swing"),
    Horizon.LONG: (
        "largo plazo",
        "long term",
        "long-term",
        "años",
        "years",
        "retiro",
        "jubilacion",
    ),
}


def tickers_in_text(text: str) -> list[str]:
    """Tickers mentioned in ``text``, in order: cashtags, then symbols, then company names."""
    found: list[str] = [match.group(1) for match in _CASHTAG.finditer(text)]
    if not _shouting(text):
        found += [
            match.group(1)
            for match in _SYMBOL.finditer(text)
            if match.group(1) not in _NOT_TICKERS and TICKER_PATTERN.fullmatch(match.group(1))
        ]
    folded = text.lower().translate(_ACCENTS)
    for name, symbol in COMPANY_ALIASES.items():
        if re.search(rf"\b{re.escape(name)}\b", folded):
            found.append(symbol)
    try:
        return unique_tickers(found)[:MAX_TICKERS]
    except RequestParseError:
        return []


def _shouting(text: str) -> bool:
    """A message typed in capitals: every word would look like a symbol, so only cashtags count."""
    letters = [char for char in text if char.isalpha()]
    return len(text.split()) > 3 and sum(char.isupper() for char in letters) > 0.8 * len(letters)


def horizon_in_text(text: str) -> Horizon:
    """``long`` or ``short`` when the text says so, else ``medium``."""
    folded = text.lower().translate(_ACCENTS)
    for horizon, words in _HORIZON_WORDS.items():
        if any(word in folded for word in words):
            return horizon
    return Horizon.MEDIUM


def _today(now: datetime | None) -> date:
    return (now or datetime.now(UTC)).astimezone(UTC).date()


def _as_of(raw: object, today: date) -> date:
    """A date from ``YYYY-MM-DD``, an ISO timestamp or epoch seconds/ms; never after ``today``."""
    parsed: date | None = None
    if isinstance(raw, datetime):
        parsed = raw.date()
    elif isinstance(raw, date):
        parsed = raw
    elif isinstance(raw, int | float) and not isinstance(raw, bool):
        seconds = raw / 1000 if raw > 1e11 else raw
        parsed = datetime.fromtimestamp(seconds, UTC).date()
    elif isinstance(raw, str) and raw.strip():
        text = raw.strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text).date()
        except ValueError:
            try:
                parsed = date.fromisoformat(text[:10])
            except ValueError as error:
                raise RequestParseError(f"not a date: {raw!r}") from error
    return min(parsed or today, today)


def request_from_text(
    text: str, *, now: datetime | None = None, source: RequestSource = RequestSource.TEXT
) -> AnalysisRequest | None:
    """The request a message asks for, or ``None`` when it names no ticker."""
    tickers = tickers_in_text(text)
    if not tickers:
        return None
    return AnalysisRequest(
        tickers=tickers,
        as_of=_today(now),
        source=source,
        horizon=horizon_in_text(text),
        question=text.strip()[:2000],
    )


def detect_source(payload: Mapping[str, Any]) -> RequestSource:
    """TradingView alerts carry ``exchange``/``interval``; price alerts a change percentage."""
    keys = {key.lower() for key in payload}
    if keys & {"exchange", "interval", "strategy"} and keys & {"ticker", "symbol"}:
        return RequestSource.TRADINGVIEW
    if keys & {"change_pct", "change_percent", "pct_change", "threshold"}:
        return RequestSource.PRICE_ALERT
    return RequestSource.JSON


def _trigger(source: RequestSource, payload: Mapping[str, Any]) -> str:
    if source is RequestSource.TRADINGVIEW:
        parts = [str(payload.get(key, "")).strip() for key in ("exchange", "interval", "close")]
        message = str(payload.get("message") or payload.get("alert_message") or "").strip()
        return " · ".join(part for part in [*parts, message] if part)[:500]
    if source is RequestSource.PRICE_ALERT:
        change = next(
            (
                payload[key]
                for key in ("change_pct", "change_percent", "pct_change")
                if key in payload
            ),
            None,
        )
        price = payload.get("price") or payload.get("close")
        bits = [
            f"cambio {change}%" if change is not None else "",
            f"precio {price}" if price else "",
        ]
        return " · ".join(bit for bit in bits if bit)[:500]
    return ""


def request_from_payload(
    payload: Mapping[str, Any],
    *,
    now: datetime | None = None,
    source: RequestSource | None = None,
) -> AnalysisRequest:
    """A request from a JSON body: ``tickers`` (list or comma string), ``ticker`` or ``symbol``.

    Optional keys: ``as_of`` (or ``time``/``date``), ``horizon`` and ``question``. TradingView
    prefixes like ``NASDAQ:AAPL`` are stripped. Raises :class:`RequestParseError` when no
    ticker is named.
    """
    kind = source or detect_source(payload)
    raw = payload.get("tickers") or payload.get("ticker") or payload.get("symbol")
    if isinstance(raw, str):
        raw = re.split(r"[\s,;]+", raw)
    if not isinstance(raw, list | tuple) or not raw:
        question = str(payload.get("question") or payload.get("text") or "")
        tickers = tickers_in_text(question)
        if not tickers:
            raise RequestParseError("the payload names no ticker")
        raw = tickers
    symbols = [str(item).split(":")[-1] for item in raw if str(item).strip()]
    try:
        horizon = Horizon(str(payload.get("horizon") or "medium").strip().lower())
    except ValueError:
        horizon = Horizon.MEDIUM
    today = _today(now)
    return AnalysisRequest(
        tickers=symbols[:MAX_TICKERS],
        as_of=_as_of(payload.get("as_of") or payload.get("date") or payload.get("time"), today),
        source=kind,
        horizon=horizon,
        question=str(payload.get("question") or "")[:2000],
        trigger=_trigger(kind, payload),
    )
