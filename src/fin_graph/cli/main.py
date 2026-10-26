"""Local analysis, investigation, chat and server commands."""

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
from fin_graph.cli.eval_tools import register_eval_tools
from fin_graph.cli.harness import app as harness_app
from fin_graph.cli.integrations import app as integrations_app
from fin_graph.cli.schedules import app as schedules_app
from fin_graph.config.settings import PersistenceBackend, Settings
from fin_graph.config.tracing import disable_external_tracing
from fin_graph.delivery.service import DeliveryService
from fin_graph.domain.alerts.model import AlertSource
from fin_graph.domain.rendering.markdown import render_markdown
from fin_graph.domain.report import Report
from fin_graph.graphs.chat.graph import make_chat_graph
from fin_graph.graphs.remediation.actions import default_action_catalog
from fin_graph.harness.runtime import _services, make_investigation_graph, make_runtime
from fin_graph.integrations.catalog import default_registry
from fin_graph.integrations.context import RunContext
from fin_graph.llm.models import ModelRole, get_model
from fin_graph.memory.service import IncidentMemory
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
register_eval_tools(eval_app)
console = Console()


@app.callback()
def root() -> None:
    """Run local investigation commands."""


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
) -> None:
    """Analiza acciones y muestra tesis, señales, riesgo y órdenes paper propuestas."""
    if (tickers is None) == (text is None):
        raise typer.BadParameter("Indica exactamente uno de --tickers o --text")
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
        asyncio.run(_run_analysis(graph, context, raw, portfolio, as_json))
    except (OSError, UnicodeError, ValueError) as exc:
        console.print(f"Error: {type(exc).__name__}: {exc}")
        raise typer.Exit(code=1) from None
    except Exception as exc:
        console.print(f"Error: análisis fallido ({type(exc).__name__})")
        raise typer.Exit(code=1) from None


async def _run_analysis(
    graph: Any,
    context: RunContext,
    raw: str | dict[str, Any],
    portfolio: dict[str, Any] | None,
    as_json: bool,
) -> None:
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


@app.command()
def investigate(
    file: Annotated[Path | None, typer.Option("--file", help="Alerta JSON o texto UTF-8.")] = None,
    text: Annotated[str | None, typer.Option("--text", help="Texto de la alerta.")] = None,
    source: Annotated[str | None, typer.Option("--source", help="Fuente del webhook.")] = None,
) -> None:
    """Investiga una alerta local y muestra progreso y reporte Markdown."""
    if (file is None) == (text is None):
        raise typer.BadParameter("Indica exactamente uno de --file o --text")
    if source is not None and source.lower() not in {item.value for item in AlertSource}:
        raise typer.BadParameter("Fuente no reconocida", param_hint="--source")
    try:
        raw = text if text is not None else _read_file(file)
        graph, context = make_runtime()
        asyncio.run(_run(graph, context, raw, source))
    except (OSError, UnicodeError, ValueError) as exc:
        console.print(f"Error: {type(exc).__name__}: entrada o configuración inválida")
        raise typer.Exit(code=1) from None
    except Exception as exc:
        console.print(f"Error: investigación fallida ({type(exc).__name__})")
        raise typer.Exit(code=1) from None


@app.command()
def ask(
    text: Annotated[str, typer.Argument(help="Pregunta para el copiloto SRE.")],
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
    """Abre el REPL SRE con hilos, investigación, integraciones y aprobaciones."""
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
        console.print(f"Hilo {session.thread_id}. /new /threads /resume /investigate ")
        console.print("/approve /integrations /cost /exit")
        while True:
            try:
                line = (await prompt.prompt_async("sre> ")).strip()
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
    context = DeliveryService.from_env().extend_run_context(registry.run_context())
    async with (
        open_checkpointer(settings) as saver,
        open_store(settings) as store,
        open_sqlite_action_ledger(path) as action_ledger,
    ):
        memory = IncidentMemory(store)
        investigation = make_investigation_graph(
            _services(settings), registry, memory=memory, checkpointer=False
        )
        graph = make_chat_graph(
            get_model(ModelRole.REASONING, settings),
            registry,
            context,
            memory,
            default_action_catalog(ledger=action_ledger),
            policy=ApprovalPolicy.from_settings(settings),
            investigation=investigation,
            checkpointer=saver,
            store=store,
            settings=settings,
        )
        session = ChatSession(
            graph,
            store,
            context,
            registry=registry,
            investigation=investigation,
            user_id=getpass.getuser(),
            approval_audit=action_ledger,
        )
        await session.start()
        yield session


def _read_file(path: Path | None) -> str:
    if path is None:
        raise ValueError("missing file")
    if path.stat().st_size > 1024 * 1024:
        raise ValueError("alert file exceeds 1 MiB")
    return path.read_text(encoding="utf-8")


async def _run(graph: Any, context: RunContext, raw: str, source: str | None) -> None:
    latest: dict[str, Any] = {}
    run_id = str(uuid4())
    async for mode, event in graph.astream(
        {
            "raw_alert": raw,
            "source_hint": source,
            "user_requested": True,
            "destinations": {},
            "thread_id": run_id,
            "run_id": run_id,
        },
        context=context,
        stream_mode=["custom", "values"],
        config={"recursion_limit": 64},
    ):
        if mode == "custom" and isinstance(event, dict):
            stage = event.get("data", {}).get("stage", "")
            if stage:
                console.print(f"[cyan]{stage}[/cyan]")
        elif mode == "values" and isinstance(event, dict):
            latest = event
    if "report" not in latest:
        raise ValueError("investigation produced no report")
    console.print(render_markdown(Report.model_validate(latest["report"])), markup=False)


def main() -> None:
    app()


@eval_app.command("run")
def eval_run(
    suite: Annotated[
        str,
        typer.Option(
            "--suite",
            help="smoke, all o un escenario de acciones; sre:smoke|eks|rds|all para el SRE.",
        ),
    ] = "smoke",
    model: Annotated[str, typer.Option("--model", help="scripted o provider:model.")] = "scripted",
    judge_model: Annotated[
        str | None, typer.Option("--judge-model", help="Juez opcional provider:model (SRE).")
    ] = None,
    repeat: Annotated[int, typer.Option("--repeat", help="Repeticiones por escenario.")] = 1,
    output_dir: Annotated[
        Path, typer.Option("--output-dir", help="Directorio de resultados.")
    ] = Path(".eval-results"),
) -> None:
    """Ejecuta los escenarios de acciones (o los SRE con sre:) contra fixtures locales."""
    try:
        if suite.startswith("sre:"):
            from fin_graph.evals.runner import run_suite

            result = asyncio.run(
                run_suite(
                    suite.removeprefix("sre:"), model, repeat, output_dir, judge_model=judge_model
                )
            )
        else:
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
