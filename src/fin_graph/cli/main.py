"""Local analysis, paper trading, chat and server commands."""

from __future__ import annotations

import asyncio
import getpass
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any
from uuid import uuid4

import httpx
import typer
from platformdirs import user_data_dir
from prompt_toolkit import PromptSession
from rich.console import Console

from fin_graph.cli.chat import ChatSession, RemoteChatSession
from fin_graph.cli.compat import register_compat_commands
from fin_graph.cli.harness import app as harness_app
from fin_graph.cli.integrations import app as integrations_app
from fin_graph.cli.orders import app as orders_app
from fin_graph.cli.schedules import app as schedules_app
from fin_graph.config.settings import PersistenceBackend, Settings
from fin_graph.config.tracing import disable_external_tracing
from fin_graph.graphs.chat.graph import make_chat_graph
from fin_graph.harness.runtime import _analysis_services, make_analysis_graph
from fin_graph.integrations.broker.paper.runtime import open_trading_context
from fin_graph.integrations.catalog import default_registry
from fin_graph.integrations.context import RunContext
from fin_graph.llm.models import ModelRole, get_model
from fin_graph.memory.theses import ThesisMemory
from fin_graph.persistence.action_ledger import open_sqlite_action_ledger
from fin_graph.persistence.checkpointer import open_checkpointer
from fin_graph.persistence.store import open_store
from fin_graph.safety.approval import ApprovalPolicy

app = typer.Typer(
    help="Análisis financiero multiagente (paper trading, uso educativo).", no_args_is_help=True
)
eval_app = typer.Typer(help="Evaluación reproducible del grafo de análisis.", no_args_is_help=True)
app.add_typer(eval_app, name="eval")
app.add_typer(harness_app, name="harness")
app.add_typer(schedules_app, name="schedules")
app.add_typer(integrations_app, name="integrations")
app.add_typer(orders_app, name="orders")
console = Console()
register_compat_commands(app)


@app.callback()
def root() -> None:
    """Análisis financiero multiagente con cartera paper."""


@app.command()
def serve(
    host: Annotated[str, typer.Option("--host", help="Interfaz HTTP.")] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port", help="Puerto HTTP.")] = 8000,
) -> None:
    """Inicia la API con Postgres, migraciones y autenticación obligatoria."""
    import uvicorn

    from fin_graph.server.config import ServerConfig

    config = ServerConfig()
    try:
        config.require_database_url()
        if (
            config.bootstrap_admin_key is None
            or len(config.bootstrap_admin_key.get_secret_value()) < 32
        ):
            raise ValueError("BOOTSTRAP_ADMIN_KEY must contain at least 32 characters")
    except ValueError as exc:
        console.print(f"Error: {exc}")
        raise typer.Exit(code=2) from None
    uvicorn.run("fin_graph.server.asgi:app", host=host, port=port, reload=False)


@app.command()
def analyze(
    tickers: Annotated[
        str | None, typer.Option("--tickers", help="Tickers separados por comas: AAPL,MSFT.")
    ] = None,
    text: Annotated[
        str | None, typer.Option("--text", help="Pregunta libre: '¿cómo ves NVDA?'.")
    ] = None,
    as_of: Annotated[
        str | None, typer.Option("--as-of", help="Fecha de análisis YYYY-MM-DD (point in time).")
    ] = None,
    horizon: Annotated[str, typer.Option("--horizon", help="short, medium o long.")] = "medium",
    cash: Annotated[
        float | None, typer.Option("--cash", help="Efectivo de la cartera paper.")
    ] = None,
    as_json: Annotated[bool, typer.Option("--json", help="Imprime el reporte en JSON.")] = False,
    propose: Annotated[
        bool,
        typer.Option("--propose", help="Deja las órdenes propuestas pendientes de aprobación."),
    ] = False,
) -> None:
    """Analiza acciones y muestra tesis, señales, riesgo y órdenes paper propuestas.

    Sin ``--cash`` dimensiona contra la cartera paper actual.
    """
    if (tickers is None) == (text is None):
        raise typer.BadParameter("Indica exactamente uno de --tickers o --text")
    if propose and cash is not None:
        raise typer.BadParameter("--propose usa la cartera paper real; no lo combines con --cash")
    if horizon not in {"short", "medium", "long"}:
        raise typer.BadParameter("Horizonte no reconocido", param_hint="--horizon")
    raw: str | dict[str, Any]
    if text is not None:
        raw = text
    else:
        raw = {"tickers": tickers, "horizon": horizon}
        if as_of:
            raw["as_of"] = as_of
    portfolio = {"cash": cash} if cash is not None else None
    try:
        from fin_graph.harness.runtime import make_analysis_runtime

        graph, context = make_analysis_runtime()
        asyncio.run(_analyze(graph, context, raw, portfolio, as_json, propose))
    except (OSError, UnicodeError, ValueError) as exc:
        console.print(f"Error: {type(exc).__name__}: {exc}")
        raise typer.Exit(code=1) from None
    except Exception as exc:
        console.print(f"Error: análisis fallido ({type(exc).__name__})")
        raise typer.Exit(code=1) from None


async def _analyze(
    graph: Any,
    context: RunContext,
    raw: str | dict[str, Any],
    portfolio: dict[str, Any] | None,
    as_json: bool,
    propose: bool,
) -> None:
    """Run with the paper broker in the context; with ``propose``, queue each order for approval."""
    from fin_graph.cli.orders import open_orders_runtime, outcome_line, submit, thread_for

    async with open_orders_runtime(context=context) as orders:
        report = await _run_analysis(graph, orders.context, raw, portfolio, as_json)
        if not propose:
            return
        proposed = [section.order for section in report.sections if section.order]
        if not proposed:
            console.print("Ninguna orden propuesta: todas las decisiones son hold.")
        for order in proposed:
            result = await submit(orders, order)
            console.print(f"{thread_for(order)}: {outcome_line(result)}", markup=False)


async def _run_analysis(
    graph: Any,
    context: RunContext,
    raw: str | dict[str, Any],
    portfolio: dict[str, Any] | None,
    as_json: bool,
) -> Any:
    from fin_graph.domain.analysis_report import AnalysisReport
    from fin_graph.domain.rendering.analysis import render_analysis_markdown

    latest: dict[str, Any] = {}
    run_id = str(uuid4())
    payload: dict[str, Any] = {
        "raw_request": raw,
        "source_hint": "cli" if isinstance(raw, dict) else None,
        "user_requested": True,
        "destinations": {},
        "thread_id": run_id,
        "run_id": run_id,
    }
    if portfolio:
        payload["portfolio"] = portfolio
    async for mode, event in graph.astream(
        payload, context=context, stream_mode=["custom", "values"], config={"recursion_limit": 64}
    ):
        if mode == "custom" and isinstance(event, dict):
            stage = event.get("data", {}).get("stage", "")
            if stage and not as_json:
                console.print(f"[cyan]{stage}[/cyan]")
        elif mode == "values" and isinstance(event, dict):
            latest = event
    if latest.get("is_noise"):
        raise ValueError("el mensaje no nombra ningún ticker")
    if not latest.get("report"):
        raise ValueError("el análisis no produjo reporte")
    report = AnalysisReport.model_validate(latest["report"])
    if as_json:
        console.print(report.model_dump_json(indent=2), markup=False)
    else:
        console.print(render_analysis_markdown(report), markup=False)
    return report


@app.command()
def portfolio(as_json: Annotated[bool, typer.Option("--json")] = False) -> None:
    """Muestra la cartera paper: efectivo, posiciones, coste medio y P&L abierto."""
    from fin_graph.cli.orders import show_portfolio

    show_portfolio(as_json)


@app.command()
def ask(
    text: Annotated[str, typer.Argument(help="Pregunta para el copiloto financiero.")],
    sqlite_path: Annotated[
        Path | None, typer.Option("--sqlite-path", help="Archivo SQLite local de hilos.")
    ] = None,
    server: Annotated[str | None, typer.Option("--server", help="URL de la API remota.")] = None,
    api_key: Annotated[
        str | None,
        typer.Option("--api-key", envvar="FIN_GRAPH_API_KEY", help="API key remota."),
    ] = None,
    thread_id: Annotated[
        str | None, typer.Option("--thread-id", help="Hilo remoto existente.")
    ] = None,
) -> None:
    """Envía una pregunta al chat local o remoto."""
    try:
        asyncio.run(_ask(text, sqlite_path, server, api_key, thread_id))
    except (OSError, ValueError, RuntimeError) as exc:
        console.print(f"Error: {type(exc).__name__}: {exc}")
        raise typer.Exit(code=1) from None


@app.command()
def chat(
    sqlite_path: Annotated[
        Path | None, typer.Option("--sqlite-path", help="Archivo SQLite local de hilos.")
    ] = None,
    server: Annotated[str | None, typer.Option("--server", help="URL de la API remota.")] = None,
    api_key: Annotated[
        str | None,
        typer.Option("--api-key", envvar="FIN_GRAPH_API_KEY", help="API key remota."),
    ] = None,
    thread_id: Annotated[
        str | None, typer.Option("--thread-id", help="Hilo remoto existente.")
    ] = None,
) -> None:
    """Abre el chat financiero: hilos, análisis, cartera paper y aprobación de órdenes."""
    try:
        asyncio.run(_chat(sqlite_path, server, api_key, thread_id))
    except (OSError, ValueError, RuntimeError) as exc:
        console.print(f"Error: {type(exc).__name__}: {exc}")
        raise typer.Exit(code=1) from None


async def _ask(
    message: str,
    sqlite_path: Path | None,
    server: str | None,
    api_key: str | None,
    thread_id: str | None,
) -> None:
    async with _chat_session(sqlite_path, server, api_key, thread_id) as session:
        turn = await session.send(message)
        if turn.text:
            console.print(turn.text, markup=False)
        if turn.status == "awaiting_approval":
            console.print(f"Aprobación pendiente en hilo {session.thread_id}")
            console.print(json.dumps(turn.requests, ensure_ascii=False), markup=False)
        elif isinstance(session, RemoteChatSession):
            console.print(f"Hilo {session.thread_id}")


async def _chat(
    sqlite_path: Path | None,
    server: str | None,
    api_key: str | None,
    thread_id: str | None,
) -> None:
    prompt: PromptSession[str] = PromptSession()
    async with _chat_session(sqlite_path, server, api_key, thread_id) as session:
        console.print(f"Hilo {session.thread_id}. /new /threads /resume /analiza ")
        console.print("/approve /integrations /cost /exit · cartera paper, uso educativo")
        while True:
            try:
                line = (await prompt.prompt_async("fin> ")).strip()
            except (EOFError, KeyboardInterrupt):
                return
            if not line:
                continue
            if line == "/exit":
                return
            try:
                if line.startswith("/"):
                    console.print(await session.command(line), markup=False)
                    continue
                turn = await session.send(line)
                if turn.text:
                    console.print(turn.text, markup=False)
                if turn.status == "awaiting_approval":
                    console.print("Aprobación pendiente; usa /approve approve|reject|edit")
                    console.print(json.dumps(turn.requests, ensure_ascii=False), markup=False)
            except (ValueError, RuntimeError) as exc:
                console.print(f"Error: {type(exc).__name__}: {exc}")


@asynccontextmanager
async def _chat_session(
    sqlite_path: Path | None,
    server: str | None,
    api_key: str | None,
    thread_id: str | None,
) -> AsyncIterator[ChatSession | RemoteChatSession]:
    if server is None:
        if thread_id is not None:
            raise ValueError("--thread-id requires --server")
        async with _local_session(sqlite_path) as session:
            yield session
        return
    if sqlite_path is not None:
        raise ValueError("--sqlite-path is only available for local chat")
    if not api_key:
        raise ValueError("--server requires --api-key or FIN_GRAPH_API_KEY")
    try:
        url = httpx.URL(server)
    except httpx.InvalidURL as exc:
        raise ValueError("invalid server URL") from exc
    if (
        url.scheme not in {"http", "https"}
        or not url.host
        or url.username
        or url.password
        or url.query
        or url.fragment
        or url.path not in {"", "/"}
    ):
        raise ValueError("server URL must be an HTTP(S) origin without credentials")
    if url.scheme == "http" and url.host not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("remote API keys require HTTPS outside loopback")
    timeout = httpx.Timeout(30.0, read=120.0)
    async with httpx.AsyncClient(base_url=url, timeout=timeout) as client:
        remote_session = RemoteChatSession(client, api_key, thread_id=thread_id)
        await remote_session.start()
        yield remote_session


@asynccontextmanager
async def _local_session(sqlite_path: Path | None) -> AsyncIterator[ChatSession]:
    disable_external_tracing()
    path = sqlite_path or Path(user_data_dir("fin-graph", "fin-graph")) / "chat.sqlite"
    settings = Settings(persistence_backend=PersistenceBackend.SQLITE, sqlite_path=str(path))
    from fin_graph.integrations.mcp.runtime import with_configured_mcp

    registry = await with_configured_mcp(default_registry())
    base = registry.run_context()
    async with (
        open_checkpointer(settings) as saver,
        open_store(settings) as store,
        open_sqlite_action_ledger(path) as action_ledger,
        open_trading_context(settings, base) as (_, context),
    ):
        memory = ThesisMemory(store)
        analysis = make_analysis_graph(
            _analysis_services(settings),
            registry,
            memory=memory,
            checkpointer=False,  # persistence belongs to the chat checkpointer
            settings=settings,
        )
        graph = make_chat_graph(
            get_model(ModelRole.REASONING, settings),
            registry,
            context,
            memory,
            policy=ApprovalPolicy.from_settings(settings),
            analysis=analysis,
            checkpointer=saver,
            store=store,
            settings=settings,
        )
        session = ChatSession(
            graph,
            store,
            context,
            registry=registry,
            analysis=analysis,
            user_id=getpass.getuser(),
            approval_audit=action_ledger,
        )
        await session.start()
        yield session


def main() -> None:
    app()


@eval_app.command("run")
def eval_run(
    suite: Annotated[
        str,
        typer.Option(
            "--suite",
            help="smoke, all o un escenario de acciones.",
        ),
    ] = "smoke",
    model: Annotated[str, typer.Option("--model", help="scripted o provider:model.")] = "scripted",
    repeat: Annotated[int, typer.Option("--repeat", help="Repeticiones por escenario.")] = 1,
    output_dir: Annotated[
        Path, typer.Option("--output-dir", help="Directorio de resultados.")
    ] = Path(".eval-results"),
) -> None:
    """Ejecuta escenarios reproducibles de acciones contra fixtures locales."""
    try:
        from fin_graph.evals.market.runner import run_equity_suite

        result = asyncio.run(run_equity_suite(suite, model, repeat, output_dir))
    except (OSError, ValueError) as exc:
        console.print(f"Error: {type(exc).__name__}: {exc}")
        raise typer.Exit(code=2) from None
    counts = {
        status: sum(run["status"] == status for run in result["runs"])
        for status in ("completed", "failed", "blocked")
    }
    summary = result.get("summary")
    detail = (
        f"; aprobados {summary['passed']}/{summary['runs']}, señal {summary['signal_accuracy']}, "
        f"categoría {summary['category_accuracy']}"
        if summary
        else ""
    )
    console.print(f"Evaluación {suite}: {counts}{detail}; resultados en {output_dir}")
    if counts["failed"] or counts["blocked"]:
        raise typer.Exit(code=1)
