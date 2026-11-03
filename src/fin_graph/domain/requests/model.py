"""The normalized analysis request every intake path produces."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from datetime import date
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from fin_graph.domain.analysts import AnalystId, resolve_analysts
from fin_graph.domain.portfolio import Portfolio

MAX_TICKERS = 10
#: Exchange symbols: letters and digits, optionally with a class or market suffix (BRK.B, RY.TO).
TICKER_PATTERN = re.compile(r"^[A-Z0-9]{1,6}(?:[.\-][A-Z0-9]{1,4})?$")


class RequestSource(StrEnum):
    """Where an analysis request came from."""

    CLI = "cli"
    TEXT = "text"
    JSON = "json"
    TRADINGVIEW = "tradingview"
    PRICE_ALERT = "price_alert"
    SCHEDULE = "schedule"
    CHAT = "chat"


class Horizon(StrEnum):
    """How far ahead the thesis looks; it sets how much history the analysts read."""

    SHORT = "short"
    MEDIUM = "medium"
    LONG = "long"


#: Days of history each horizon starts with (refine widens it).
HORIZON_LOOKBACK_DAYS = {Horizon.SHORT: 365, Horizon.MEDIUM: 365, Horizon.LONG: 1095}


class RequestParseError(ValueError):
    """The payload names no ticker this system can analyse."""


def normalize_ticker(raw: str) -> str:
    """``$aapl`` → ``AAPL``; raises :class:`RequestParseError` for anything that is not a symbol."""
    symbol = raw.strip().lstrip("$").upper()
    if not TICKER_PATTERN.fullmatch(symbol):
        raise RequestParseError(f"not a ticker symbol: {raw!r}")
    return symbol


def unique_tickers(raw: Iterable[str]) -> list[str]:
    """Normalized tickers in first-seen order, without repeats."""
    return list(dict.fromkeys(normalize_ticker(item) for item in raw))


class AnalysisRequest(BaseModel):
    """One request: tickers, the point-in-time date, horizon and context.

    ``as_of`` is the last day whose data the analysis may read, so a backtest or a replayed
    request never sees the future. Store it in graph state with ``model_dump(mode="json")``.
    """

    model_config = ConfigDict(frozen=True)

    tickers: list[str] = Field(min_length=1, max_length=MAX_TICKERS)
    as_of: date
    source: RequestSource = RequestSource.CLI
    horizon: Horizon = Horizon.MEDIUM
    question: str = ""
    trigger: str = ""
    portfolio: Portfolio | None = None
    analysts: tuple[AnalystId, ...] | None = None
    fingerprint: str = ""

    @field_validator("tickers", mode="before")
    @classmethod
    def _normalize(cls, value: object) -> object:
        if isinstance(value, str):
            value = re.split(r"[\s,;]+", value)
        if isinstance(value, list | tuple):
            return unique_tickers(str(item) for item in value if str(item).strip())
        return value

    @field_validator("analysts", mode="before")
    @classmethod
    def _validate_analysts(cls, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, str):
            value = tuple(item for item in re.split(r"[\s,;]+", value) if item)
        if isinstance(value, list | tuple):
            if not value:
                return ()
            return tuple(definition.id for definition in resolve_analysts(value))
        return value

    @model_validator(mode="after")
    def _default_fingerprint(self) -> Self:
        if not self.fingerprint:
            object.__setattr__(
                self, "fingerprint", request_fingerprint(self.tickers, self.as_of, self.source)
            )
        return self

    @property
    def title(self) -> str:
        """``Análisis AAPL, MSFT al 2026-10-26``."""
        return f"Análisis {', '.join(self.tickers)} al {self.as_of.isoformat()}"


def request_fingerprint(tickers: Iterable[str], as_of: date, source: RequestSource) -> str:
    """Dedupe key: the same tickers, date and source give the same fingerprint, in any order."""
    canonical = "|".join([",".join(sorted(tickers)), as_of.isoformat(), source.value])
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]
