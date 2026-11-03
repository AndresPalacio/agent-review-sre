"""Pure translations between frozen AI Hedge Fund payloads and Fin Graph domain models."""

from __future__ import annotations

from datetime import date

from fin_graph.backtesting.models import (
    BacktestRequest as NativeBacktestRequest,
)
from fin_graph.backtesting.models import (
    BacktestResult as NativeBacktestResult,
)
from fin_graph.backtesting.strategies import StrategyBacktestRequest
from fin_graph.compat.ai_hedge_fund.models import LegacyPortfolio
from fin_graph.compat.ai_hedge_fund.schemas import (
    AlgorithmBacktestConfig,
    BacktestDayResult,
    BacktestPerformanceMetrics,
    BacktestRequest,
    BacktestResponse,
    BaseHedgeFundRequest,
    HedgeFundRequest,
    SignalResponse,
    extract_base_agent_key,
)
from fin_graph.domain.analysts import AnalystId
from fin_graph.domain.portfolio import Portfolio, Position, RealizedGains
from fin_graph.domain.requests.model import AnalysisRequest, RequestSource, unique_tickers
from fin_graph.quant.signals.base import Signal as NativeSignal


class CompatibilityAdapterError(ValueError):
    """A legacy payload is valid JSON/schema-wise but cannot map safely to the native domain."""


def _date(value: str, field: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise CompatibilityAdapterError(f"{field} must be a valid YYYY-MM-DD date") from exc


def _whole_shares(value: float, ticker: str) -> int:
    if not value.is_integer():
        raise CompatibilityAdapterError(f"portfolio position for {ticker} must use whole shares")
    return int(value)


def _selected_analysts(request: BaseHedgeFundRequest) -> tuple[AnalystId, ...]:
    selected: list[AnalystId] = []
    seen: set[AnalystId] = set()
    for node in request.graph_nodes:
        value = extract_base_agent_key(node.id)
        try:
            analyst = AnalystId(value)
        except ValueError:
            continue
        if analyst not in seen:
            selected.append(analyst)
            seen.add(analyst)
    return tuple(selected)


def to_native_portfolio(request: BaseHedgeFundRequest, *, cash: float) -> Portfolio:
    """Mirror reference initial-position semantics without executing an opening fill."""

    tickers = unique_tickers(request.tickers)
    positions = {ticker: Position() for ticker in tickers}
    realized = {ticker: RealizedGains() for ticker in tickers}
    margin_used = 0.0
    for item in request.portfolio_positions or ():
        ticker = item.ticker.strip().upper()
        if ticker not in positions:
            continue
        quantity = _whole_shares(item.quantity, ticker)
        held = positions[ticker]
        if quantity > 0:
            positions[ticker] = held.model_copy(
                update={"long": quantity, "long_cost_basis": item.trade_price}
            )
        elif quantity < 0:
            short = abs(quantity)
            margin = short * item.trade_price * request.margin_requirement
            positions[ticker] = held.model_copy(
                update={
                    "short": short,
                    "short_cost_basis": item.trade_price,
                    "short_margin_used": margin,
                }
            )
            margin_used += margin
    return Portfolio(
        cash=cash,
        positions=positions,
        margin_requirement=request.margin_requirement,
        margin_used=margin_used,
        realized_gains=realized,
    )


def to_native_analysis_request(request: HedgeFundRequest) -> AnalysisRequest:
    if request.end_date is None:
        raise CompatibilityAdapterError("end_date cannot be null")
    end = _date(request.end_date, "end_date")
    try:
        start_value = request.get_start_date()
    except ValueError as exc:
        raise CompatibilityAdapterError("start_date must be a valid YYYY-MM-DD date") from exc
    start = _date(start_value, "start_date")
    if start > end:
        raise CompatibilityAdapterError("start_date must not be after end_date")
    return AnalysisRequest(
        tickers=request.tickers,
        as_of=end,
        source=RequestSource.JSON,
        portfolio=to_native_portfolio(request, cash=request.initial_cash),
        analysts=_selected_analysts(request),
    )


def to_native_backtest_request(request: BacktestRequest) -> NativeBacktestRequest:
    start = _date(request.start_date, "start_date")
    end = _date(request.end_date, "end_date")
    if start > end:
        raise CompatibilityAdapterError("start_date must not be after end_date")
    # Validate initial positions now even though the v1 backtest service starts from capital;
    # Task 4 passes this exact portfolio to the compatible runner when positions were supplied.
    to_native_portfolio(request, cash=request.initial_capital)
    return NativeBacktestRequest(
        tickers=request.tickers,
        start_date=start,
        end_date=end,
        initial_capital=request.initial_capital,
        margin_requirement=request.margin_requirement,
        analysts=_selected_analysts(request),
        model=request.model_name or "gpt-4.1",
    )


def from_native_backtest_result(result: NativeBacktestResult) -> BacktestResponse:
    days = [
        BacktestDayResult(
            date=snapshot.date.isoformat(),
            portfolio_value=snapshot.portfolio_value,
            cash=snapshot.portfolio.cash,
            decisions={
                ticker: decision.model_dump(mode="json")
                for ticker, decision in snapshot.decisions.items()
            },
            executed_trades=snapshot.executed_trades,
            analyst_signals={
                analyst: {
                    ticker: value.model_dump(mode="json") for ticker, value in ticker_values.items()
                }
                for analyst, ticker_values in snapshot.analyst_signals.items()
            },
            current_prices=snapshot.current_prices,
            long_exposure=snapshot.long_exposure,
            short_exposure=snapshot.short_exposure,
            gross_exposure=snapshot.gross_exposure,
            net_exposure=snapshot.net_exposure,
            long_short_ratio=snapshot.long_short_ratio,
        )
        for snapshot in result.snapshots
    ]
    metrics = result.metrics
    return BacktestResponse(
        results=days,
        performance_metrics=BacktestPerformanceMetrics(
            sharpe_ratio=metrics.sharpe_ratio,
            sortino_ratio=metrics.sortino_ratio,
            max_drawdown=metrics.max_drawdown_pct,
            max_drawdown_date=(
                metrics.max_drawdown_date.isoformat() if metrics.max_drawdown_date else None
            ),
            long_short_ratio=metrics.long_short_ratio,
            gross_exposure=metrics.gross_exposure,
            net_exposure=metrics.net_exposure,
        ),
        final_portfolio=LegacyPortfolio.from_native(result.final_portfolio).model_dump(mode="json"),
    )


def from_native_signal(signal: NativeSignal) -> SignalResponse:
    return SignalResponse(
        ticker=signal.ticker,
        date=signal.as_of.isoformat(),
        value=signal.value,
        reasoning=signal.reasoning or None,
        metadata=signal.metadata,
    )


def to_native_strategy_backtest_request(
    request: AlgorithmBacktestConfig,
) -> StrategyBacktestRequest:
    return StrategyBacktestRequest(
        algorithm_key=request.algorithm_key,
        parameters=request.parameters,
        symbols=request.symbols,
        start_date=request.start_date,
        end_date=request.end_date,
        interval=request.interval,
        provider=request.provider,
        initial_capital=request.initial_capital,
        commission_rate=request.commission_rate,
        slippage_bps=request.slippage_bps,
        seed=request.seed,
    )
