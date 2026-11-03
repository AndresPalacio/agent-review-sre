"""Async point-in-time cumulative abnormal-return event study engine."""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Any, Protocol, cast

from fin_graph.quant.data import MarketDataClient, adjusted_close
from fin_graph.quant.event_study.models import (
    AggregateResult,
    EventCAR,
    EventStudyRequest,
    EventStudyResult,
    WindowStats,
)
from fin_graph.quant.event_study.stats import (
    bootstrap_ci,
    compute_abnormal_returns,
    fit_market_model,
    sum_car,
    ttest_cars,
)
from fin_graph.quant.signals.pead import EarningsRecord

_MIN_ESTIMATION_DAYS = 200
_ESTIMATION_START = -250
_ESTIMATION_END = -11
_MAX_EVENT_WINDOW = 20


class _EarningsClient(Protocol):
    async def earnings_history(self, ticker: str, *, limit: int = 12) -> list[EarningsRecord]: ...


async def run_event_study(request: EventStudyRequest, data: MarketDataClient) -> EventStudyResult:
    market_rows = await data.prices(request.market_ticker, request.price_start, request.as_of)
    market = {row.day: adjusted_close(row) for row in market_rows}
    if not market:
        return EventStudyResult(
            skipped_tickers=request.tickers,
            market_ticker=request.market_ticker,
            as_of=request.as_of,
            seed=request.seed,
        )
    events: list[EventCAR] = []
    skipped: list[str] = []
    earnings = cast(_EarningsClient, data)
    for ticker in request.tickers:
        records = await earnings.earnings_history(ticker, limit=request.earnings_limit)
        valid = [
            item
            for item in records
            if item.filing_date is not None
            and item.filing_date <= request.as_of
            and (item.filing_date - item.report_period).days < 45
        ]
        rows = await data.prices(ticker, request.price_start, request.as_of)
        found = _ticker_events(ticker, rows, market, valid)
        if found:
            events.extend(found)
        else:
            skipped.append(ticker)
    if request.require_eps_surprise:
        events = [item for item in events if item.eps_surprise is not None]
    return EventStudyResult(
        events=tuple(events),
        aggregates=tuple(_aggregate(events, request.n_bootstrap, request.seed)),
        skipped_tickers=tuple(skipped),
        market_ticker=request.market_ticker,
        as_of=request.as_of,
        seed=request.seed,
    )


def _ticker_events(
    ticker: str,
    rows: list[Any],
    market: dict[date, float],
    records: list[EarningsRecord],
) -> list[EventCAR]:
    import numpy as np

    closes = {row.day: adjusted_close(row) for row in rows}
    days = sorted(set(closes) & set(market))
    if len(days) < _MIN_ESTIMATION_DAYS + _MAX_EVENT_WINDOW:
        return []
    stock_prices = np.asarray([closes[day] for day in days], dtype=float)
    market_prices = np.asarray([market[day] for day in days], dtype=float)
    if (
        not np.isfinite(stock_prices).all()
        or not np.isfinite(market_prices).all()
        or (stock_prices <= 0).any()
        or (market_prices <= 0).any()
    ):
        raise ValueError(f"non-finite or non-positive aligned prices for {ticker}")
    stock_returns = np.diff(stock_prices) / stock_prices[:-1]
    market_returns = np.diff(market_prices) / market_prices[:-1]
    return_days = days[1:]
    result: list[EventCAR] = []
    for record in records:
        assert record.filing_date is not None
        index = next(
            (position for position, day in enumerate(return_days) if day >= record.filing_date),
            None,
        )
        if index is None:
            continue
        start = index + _ESTIMATION_START
        end = index + _ESTIMATION_END
        if start < 0 or end < 0:
            continue
        stock_est = stock_returns[start : end + 1]
        market_est = market_returns[start : end + 1]
        if len(stock_est) < _MIN_ESTIMATION_DAYS:
            continue
        model = fit_market_model(stock_est, market_est)
        event_end = min(index + _MAX_EVENT_WINDOW, len(stock_returns) - 1)
        abnormal = compute_abnormal_returns(
            stock_returns[index : event_end + 1],
            market_returns[index : event_end + 1],
            model.alpha,
            model.beta,
        )
        surprise = record.quarterly.eps_surprise if record.quarterly else None
        result.append(
            EventCAR(
                ticker=ticker,
                event_date=record.filing_date,
                source_type=record.source_type,
                report_period=record.report_period,
                eps_surprise=surprise,
                market_model=model,
                daily_ar=tuple(float(item) for item in abnormal),
                car_0_1=sum_car(abnormal, 0, 1) if len(abnormal) > 1 else None,
                car_0_5=sum_car(abnormal, 0, 5) if len(abnormal) > 5 else None,
                car_0_20=sum_car(abnormal, 0, 20) if len(abnormal) > 20 else None,
            )
        )
    return result


def _aggregate(events: list[EventCAR], n_bootstrap: int, seed: int | None) -> list[AggregateResult]:
    import numpy as np

    groups: dict[str, list[EventCAR]] = defaultdict(list)
    for event in events:
        groups[event.source_type].append(event)
    mapping = {"[0,+1]": "car_0_1", "[0,+5]": "car_0_5", "[0,+20]": "car_0_20"}
    result: list[AggregateResult] = []
    for source_type in sorted(groups):
        windows: list[WindowStats] = []
        for label, attribute in mapping.items():
            values = [
                value
                for event in groups[source_type]
                if (value := getattr(event, attribute)) is not None
            ]
            if len(values) < 2:
                continue
            array = np.asarray(values, dtype=float)
            statistic, probability = ttest_cars(array)
            windows.append(
                WindowStats(
                    window=label,
                    n_events=len(values),
                    mean_car=float(array.mean()),
                    std_car=float(array.std(ddof=1)),
                    t_stat=statistic,
                    p_value=probability,
                    ci=bootstrap_ci(array, n_bootstrap=n_bootstrap, seed=seed),
                )
            )
        result.append(
            AggregateResult(
                source_type=source_type,
                n_events=len(groups[source_type]),
                windows=tuple(windows),
            )
        )
    return result
