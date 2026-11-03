"""Post-earnings-announcement drift, ported from the v2 reference."""

from __future__ import annotations

from datetime import date
from typing import Literal, Protocol, cast

from pydantic import BaseModel, ConfigDict

from fin_graph.quant.data import MarketDataClient
from fin_graph.quant.signals.base import QuantModel, Signal

_RETROSPECTIVE_CUTOFF_DAYS = 45
_SOURCE_PRIORITY = {"8-K": 0, "10-Q": 1, "10-K": 2, "20-F": 3}


class EarningsData(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    eps_surprise: Literal["BEAT", "MISS", "MEET"] | None = None


class EarningsRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    ticker: str
    report_period: date
    source_type: str
    filing_date: date | None = None
    quarterly: EarningsData | None = None


class _EarningsClient(Protocol):
    async def earnings_history(self, ticker: str, *, limit: int = 12) -> list[EarningsRecord]: ...


class PEADModel(QuantModel):
    def __init__(self, *, earnings_limit: int = 8, signal_window_days: int = 4) -> None:
        self.earnings_limit = earnings_limit
        self.signal_window_days = signal_window_days
        self._cache: dict[str, list[EarningsRecord]] = {}

    @property
    def name(self) -> str:
        return "pead"

    async def predict(self, ticker: str, as_of: date, data: MarketDataClient) -> Signal:
        events = await self._events(ticker, cast(_EarningsClient, data))
        past = [item for item in events if item.filing_date and item.filing_date <= as_of]
        if not past:
            return Signal(model_name=self.name, ticker=ticker, as_of=as_of, value=0)
        event = max(past, key=lambda item: cast(date, item.filing_date))
        filed = cast(date, event.filing_date)
        if (as_of - filed).days > self.signal_window_days:
            return Signal(model_name=self.name, ticker=ticker, as_of=as_of, value=0)
        surprise = cast(EarningsData, event.quarterly).eps_surprise
        value = 1.0 if surprise == "BEAT" else -1.0
        return Signal(
            model_name=self.name,
            ticker=ticker,
            as_of=as_of,
            value=value,
            reasoning=(
                f"{surprise} on {event.report_period.isoformat()} earnings "
                f"(filed {filed.isoformat()}, {event.source_type})"
            ),
            metadata={
                "eps_surprise": surprise,
                "source_type": event.source_type,
                "report_period": event.report_period.isoformat(),
                "filing_date": filed.isoformat(),
            },
        )

    async def _events(self, ticker: str, data: _EarningsClient) -> list[EarningsRecord]:
        records = self._cache.get(ticker)
        if records is None:
            records = await data.earnings_history(ticker, limit=self.earnings_limit)
            self._cache[ticker] = records
        best: dict[date, tuple[int, EarningsRecord]] = {}
        for record in records:
            if not record.filing_date or not record.quarterly:
                continue
            if record.quarterly.eps_surprise not in {"BEAT", "MISS"}:
                continue
            if (record.filing_date - record.report_period).days >= _RETROSPECTIVE_CUTOFF_DAYS:
                continue
            priority = _SOURCE_PRIORITY.get(record.source_type, 99)
            if record.report_period not in best or priority < best[record.report_period][0]:
                best[record.report_period] = (priority, record)
        return [record for _, record in best.values()]
