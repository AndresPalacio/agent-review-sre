"""Native commands that preserve the AI Hedge Fund command-line surfaces."""

from __future__ import annotations

import asyncio
import importlib
import json
from datetime import date, timedelta
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console

from fin_graph.adapters.reference_app.python_api import run_hedge_fund_sync
from fin_graph.domain.analysts import analyst_catalog
from fin_graph.domain.requests.model import unique_tickers

console = Console()


def _date(value: str, option: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise typer.BadParameter("must use YYYY-MM-DD", param_hint=option) from exc


def _tickers(value: str) -> list[str]:
    try:
        return unique_tickers(item for item in value.split(",") if item.strip())
    except ValueError as exc:
        raise typer.BadParameter(str(exc), param_hint="--tickers") from exc


def _analysts(value: str | None, all_analysts: bool) -> list[str]:
    available = {item.id.value for item in analyst_catalog()}
    selected = (
        [item.id.value for item in analyst_catalog()]
        if all_analysts
        else [item.strip() for item in (value or "").split(",") if item.strip()]
    )
    unknown = sorted(set(selected) - available)
    if unknown:
        raise typer.BadParameter(
            f"unknown analyst(s): {', '.join(unknown)}", param_hint="--analysts"
        )
    return selected


def _emit(payload: object, as_json: bool) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=None if as_json else 2, default=str)
    if as_json:
        typer.echo(text)
    else:
        console.print(text, markup=False, soft_wrap=True)


def run_command(
    tickers: Annotated[str, typer.Option("--tickers", "--ticker", "-t")],
    analysts: Annotated[str | None, typer.Option("--analysts")] = None,
    analysts_all: Annotated[bool, typer.Option("--analysts-all")] = False,
    model: Annotated[str, typer.Option("--model", "--model-name")] = "gpt-4.1",
    model_provider: Annotated[str, typer.Option("--model-provider")] = "OpenAI",
    start_date: Annotated[str | None, typer.Option("--start-date")] = None,
    end_date: Annotated[str | None, typer.Option("--end-date")] = None,
    initial_cash: Annotated[
        float,
        typer.Option(
            "--initial-cash",
            "--initial-capital",
            min=0,
            help="Initial cash (legacy alias: --initial-capital).",
        ),
    ] = 100_000.0,
    margin_requirement: Annotated[float, typer.Option("--margin-requirement", min=0, max=1)] = 0.0,
    show_reasoning: Annotated[bool, typer.Option("--show-reasoning")] = False,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Run the full nineteen-persona hedge fund with historical CLI aliases."""

    end = _date(end_date or date.today().isoformat(), "--end-date")
    start = _date(start_date, "--start-date") if start_date else end - timedelta(days=90)
    if start > end:
        raise typer.BadParameter("must not be after --end-date", param_hint="--start-date")
    portfolio = {
        "cash": initial_cash,
        "margin_requirement": margin_requirement,
        "positions": {},
        "realized_gains": {},
    }
    try:
        result = run_hedge_fund_sync(
            _tickers(tickers),
            start.isoformat(),
            end.isoformat(),
            portfolio,
            show_reasoning,
            _analysts(analysts, analysts_all),
            model,
            model_provider,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        console.print(f"Error: {type(exc).__name__}: {exc}", markup=False)
        raise typer.Exit(1) from None
    _emit(result, as_json)


def backtest_command(
    tickers: Annotated[str, typer.Option("--tickers", "--ticker", "-t")],
    start_date: Annotated[str, typer.Option("--start-date")],
    end_date: Annotated[str, typer.Option("--end-date")],
    model: Annotated[str, typer.Option("--model", "--model-name")] = "gpt-4.1",
    model_provider: Annotated[str, typer.Option("--model-provider")] = "OpenAI",
    analysts: Annotated[str | None, typer.Option("--analysts")] = None,
    analysts_all: Annotated[bool, typer.Option("--analysts-all")] = False,
    initial_capital: Annotated[
        float, typer.Option("--initial-capital", "--initial-cash", min=0)
    ] = 100_000.0,
    margin_requirement: Annotated[float, typer.Option("--margin-requirement", min=0, max=1)] = 0.0,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Run the compatible point-in-time portfolio backtest."""

    from fin_graph.adapters.reference_app.http.hedge_fund import make_v1_backtest_runner
    from fin_graph.adapters.reference_app.runtime import make_reference_analysis_runtime_async
    from fin_graph.adapters.reference_app.schemas import BacktestRequest, GraphNode, ModelProvider

    start = _date(start_date, "--start-date")
    end = _date(end_date, "--end-date")
    if start > end:
        raise typer.BadParameter("must not be after --end-date", param_hint="--start-date")
    try:
        provider = ModelProvider(model_provider)
    except ValueError:
        provider = ModelProvider.OPENAI
    body = BacktestRequest(
        tickers=_tickers(tickers),
        graph_nodes=[GraphNode(id=item) for item in _analysts(analysts, analysts_all)],
        graph_edges=[],
        start_date=start.isoformat(),
        end_date=end.isoformat(),
        initial_capital=initial_capital,
        margin_requirement=margin_requirement,
        model_name=model,
        model_provider=provider,
    )

    async def execute() -> dict[str, Any]:
        graph, context = await make_reference_analysis_runtime_async()
        runner = make_v1_backtest_runner(graph, lambda: context)

        async def ignore(_event: Any) -> None:
            return None

        return await runner(body, ignore, asyncio.Event())

    try:
        _emit(asyncio.run(execute()), as_json)
    except (OSError, RuntimeError, ValueError) as exc:
        console.print(f"Error: {type(exc).__name__}: {exc}", markup=False)
        raise typer.Exit(1) from None


def models_command(as_json: Annotated[bool, typer.Option("--json")] = False) -> None:
    """List the installed quantitative and language-model compatibility catalogs."""

    from fin_graph.adapters.reference_app.http.models import CLOUD_MODELS
    from fin_graph.quant.signals import alpha_model_registry

    _emit(
        {
            "alpha_models": sorted(alpha_model_registry()),
            "language_models": [dict(item) for item in CLOUD_MODELS],
        },
        as_json,
    )


def algorithms_command(as_json: Annotated[bool, typer.Option("--json")] = False) -> None:
    """List the validated algorithm strategy catalog."""

    from fin_graph.strategies import strategy_catalog

    values = [
        factory.specification.model_dump(mode="json") for factory in strategy_catalog().values()
    ]
    _emit({"algorithms": values}, as_json)


def event_study_command(
    tickers: Annotated[str, typer.Option("--tickers", "--ticker", "-t")],
    as_of: Annotated[str, typer.Option("--as-of", "--date")],
    price_start: Annotated[str, typer.Option("--price-start")],
    market_ticker: Annotated[str, typer.Option("--market-ticker")] = "SPY",
    earnings_limit: Annotated[int, typer.Option("--earnings-limit", min=1)] = 12,
    bootstrap: Annotated[int, typer.Option("--bootstrap", min=1)] = 10_000,
    seed: Annotated[int | None, typer.Option("--seed")] = None,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Compute the native point-in-time earnings event study."""

    from fin_graph.integrations.analytics.engine import AnalyticsEngine
    from fin_graph.integrations.catalog import default_registry
    from fin_graph.integrations.context import close_run_context
    from fin_graph.integrations.market_data import MarketDataClient
    from fin_graph.quant.event_study import EventStudyRequest, run_event_study

    request = EventStudyRequest(
        tickers=tuple(_tickers(tickers)),
        as_of=_date(as_of, "--as-of"),
        price_start=_date(price_start, "--price-start"),
        market_ticker=market_ticker.upper(),
        earnings_limit=earnings_limit,
        n_bootstrap=bootstrap,
        seed=seed,
    )

    async def execute() -> Any:
        context = default_registry().run_context()
        async with close_run_context(context):
            engine = context.client("analytics", AnalyticsEngine)
            data = context.clients.get(engine.data_integration)
            if not isinstance(data, MarketDataClient):
                raise typer.BadParameter("configured market-data provider is unavailable")
            return await run_event_study(request, data)

    try:
        result = asyncio.run(execute())
    except (OSError, RuntimeError, ValueError) as exc:
        console.print(f"Error: {type(exc).__name__}: {exc}", markup=False)
        raise typer.Exit(1) from None
    _emit(result.model_dump(mode="json"), as_json)


def app_command(
    host: Annotated[str, typer.Option("--host")] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port", min=1, max=65535)] = 8000,
    data_dir: Annotated[Path | None, typer.Option("--data-dir")] = None,
) -> None:
    """Start the clean local compatible backend and bundled web application."""

    import uvicorn

    try:
        factory = importlib.import_module("fin_graph.server.factory")
        application = factory.create_runtime_app(
            factory.RuntimeProfile.local(data_dir=data_dir, host=host)
        )
    except ImportError:
        from fin_graph.server.app import create_app

        application = create_app()
    uvicorn.run(application, host=host, port=port, reload=False)


def register_reference_app_commands(application: typer.Typer) -> None:
    application.command("run")(run_command)
    application.command("backtest")(backtest_command)
    application.command("models")(models_command)
    application.command("algorithms")(algorithms_command)
    application.command("event-study")(event_study_command)
    application.command("app")(app_command)
