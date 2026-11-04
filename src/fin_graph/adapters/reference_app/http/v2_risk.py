"""Legacy portfolio VaR route over the seeded native risk engine."""

from __future__ import annotations

from datetime import date, timedelta
from itertools import pairwise

from fastapi import APIRouter, HTTPException, Query, Request

from fin_graph.adapters.reference_app.http.v2_signals import _data
from fin_graph.adapters.reference_app.schemas import VaRResponse
from fin_graph.quant.data import adjusted_close
from fin_graph.quant.risk import RiskSimulationRequest, simulate_risk

router = APIRouter(prefix="/v2/risk")


async def _holdings(request: Request, tickers: str | None, shares: str | None) -> dict[str, float]:
    if (tickers is None) != (shares is None):
        raise HTTPException(422, "tickers and shares must be provided together")
    if tickers is not None and shares is not None:
        symbols = [item.strip().upper() for item in tickers.split(",") if item.strip()]
        try:
            quantities = [float(item) for item in shares.split(",") if item.strip()]
        except ValueError as exc:
            raise HTTPException(422, "shares must be a comma-separated list of numbers") from exc
        if not symbols or len(symbols) != len(quantities):
            raise HTTPException(422, "tickers and shares must be non-empty and the same length")
        if any(value <= 0 for value in quantities):
            raise HTTPException(422, "shares must all be positive")
        return dict(zip(symbols, quantities, strict=True))
    broker = getattr(request.app.state, "paper_broker", None)
    if broker is None:
        raise HTTPException(
            404, "no PaperBroker positions found; pass tickers and shares explicitly"
        )
    portfolio = await broker.portfolio()
    values = {
        ticker: float(position.long - position.short)
        for ticker, position in portfolio.positions.items()
        if position.long != position.short
    }
    if not values:
        raise HTTPException(
            404, "no PaperBroker positions found; pass tickers and shares explicitly"
        )
    return values


@router.get("/var", response_model=VaRResponse)
async def value_at_risk(
    request: Request,
    tickers: str | None = None,
    shares: str | None = None,
    date_value: str | None = Query(default=None, alias="date"),
    confidence: float = Query(default=0.95, gt=0, lt=1),
    horizon_days: int = Query(default=1, ge=1, le=252),
    n_sims: int = Query(default=100_000, ge=1_000, le=1_000_000),
) -> VaRResponse:
    try:
        as_of = date.today() if date_value is None else date.fromisoformat(date_value)
    except ValueError as exc:
        raise HTTPException(422, "date must be a valid YYYY-MM-DD date") from exc
    holdings = await _holdings(request, tickers, shares)
    async with _data(request) as data:
        histories = {
            ticker: await data.prices(ticker, as_of - timedelta(days=400), as_of)
            for ticker in holdings
        }
    by_ticker = {
        ticker: {item.day: adjusted_close(item) for item in values}
        for ticker, values in histories.items()
    }
    common = sorted(set.intersection(*(set(values) for values in by_ticker.values())))
    if len(common) < 3:
        raise HTTPException(422, "insufficient aligned price history")
    returns = [
        [by_ticker[ticker][today] / by_ticker[ticker][yesterday] - 1 for ticker in holdings]
        for yesterday, today in pairwise(common)
    ]
    notionals = {
        ticker: quantity * by_ticker[ticker][common[-1]] for ticker, quantity in holdings.items()
    }
    portfolio_value = sum(notionals.values())
    if portfolio_value <= 0:
        raise HTTPException(422, "portfolio value must be positive")
    result = simulate_risk(
        RiskSimulationRequest(
            returns=returns,
            weights=[value / portfolio_value for value in notionals.values()],
            portfolio_value=portfolio_value,
            confidence=confidence,
            horizon_days=horizon_days,
            simulations=n_sims,
            seed=42,
        )
    )
    return VaRResponse(
        var=result.var,
        cvar=result.cvar,
        confidence=result.confidence,
        horizon_days=result.horizon_days,
        n_sims=result.simulations,
        portfolio_value=result.portfolio_value,
        expected_pnl=result.expected_pnl,
        portfolio_sigma=result.portfolio_sigma,
        var_pct=result.var_pct,
        holdings=holdings,
        date=as_of.isoformat(),
    )
