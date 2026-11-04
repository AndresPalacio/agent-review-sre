"""FastAPI API: persisted analyses, trading signals, paper orders, portfolio and chat."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import secrets
from collections.abc import AsyncIterator, MutableMapping
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, cast
from uuid import NAMESPACE_URL, uuid4, uuid5
from weakref import WeakValueDictionary

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.types import Command
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import create_async_engine
from starlette.middleware.gzip import GZipMiddleware

from fin_graph.adapters.reference_app.http import reference_app_router
from fin_graph.adapters.reference_app.http.hedge_fund import make_v1_backtest_runner
from fin_graph.adapters.reference_app.http.ollama import OllamaReferenceService
from fin_graph.adapters.reference_app.settings import ReferenceAppProfile
from fin_graph.config.settings import PersistenceBackend, Settings
from fin_graph.config.tracing import disable_external_tracing
from fin_graph.delivery.service import DeliveryService
from fin_graph.delivery.settings import DeliveryDestinations
from fin_graph.domain.analysis_report import AnalysisReport
from fin_graph.domain.fills import OrderStatus
from fin_graph.domain.orders import OrderAction, ProposedOrder, client_order_id
from fin_graph.domain.rendering.analysis import render_analysis_slack, render_analysis_telegram
from fin_graph.domain.requests.model import (
    RequestParseError,
    RequestSource,
    normalize_ticker,
    request_fingerprint,
)
from fin_graph.domain.requests.parsing import request_from_payload
from fin_graph.gateway import GatewayResult, GatewayRunPending
from fin_graph.gateway.settings import SlackGatewaySettings, TelegramGatewaySettings
from fin_graph.gateway.slack import SlackGateway
from fin_graph.gateway.telegram import TelegramGateway
from fin_graph.integrations.base import IntegrationError, IntegrationNotConfiguredError
from fin_graph.integrations.broker.paper.broker import PaperBroker, PaperBrokerError
from fin_graph.integrations.broker.paper.runtime import context_quotes, paper_broker
from fin_graph.integrations.catalog import default_registry
from fin_graph.integrations.context import RunContext, close_run_context
from fin_graph.integrations.registry import IntegrationRegistry
from fin_graph.llm.models import ModelRole, get_model
from fin_graph.memory.theses import ThesisMemory
from fin_graph.persistence.checkpointer import open_checkpointer
from fin_graph.persistence.paper_ledger import PaperLedgerStore
from fin_graph.persistence.reference_app import (
    FernetSecretCipher,
    ReferenceStore,
    open_reference_postgres,
)
from fin_graph.persistence.store import open_store
from fin_graph.runtime.analysis import _analysis_services, make_analysis_graph
from fin_graph.safety.approval import ApprovalPolicy
from fin_graph.safety.secrets import redact_secrets, redact_value
from fin_graph.scheduler.delivery import send_digest_text
from fin_graph.scheduler.expiry import ApprovalGraph, expire_pending_approvals
from fin_graph.scheduler.market import MarketJobs
from fin_graph.scheduler.service import SchedulerService, validate_schedule
from fin_graph.server.config import ServerConfig
from fin_graph.server.events import EventListener
from fin_graph.server.explain import MAX_EXPLAIN_EVENTS, build_explanation
from fin_graph.server.manager import RunManager, RunOverloaded
from fin_graph.server.migrate import upgrade
from fin_graph.server.security import REQUEST_SOURCE, SignalSource, WebhookVerifier
from fin_graph.server.storage import Storage, checkpoint_thread_ids
from fin_graph.trading.order_desk import OrderDesk
from fin_graph.workflows.analysis.snapshots import MarketSnapshotServices
from fin_graph.workflows.orders.graph import PLACE_ORDER, make_order_graph

_MAX_BODY = 1024 * 1024
_ROLE_LEVEL = {"viewer": 0, "operator": 1, "approver": 2, "admin": 3}
_LOGGER = logging.getLogger(__name__)


class AnalysisRequestBody(BaseModel):
    """Free text ("¿cómo ves AAPL?") or a JSON request naming tickers, horizon and question."""

    text: str | None = Field(default=None, min_length=1, max_length=16000)
    request: dict[str, Any] | None = None
    destinations: DeliveryDestinations = Field(default_factory=dict)


class CreateKeyRequest(BaseModel):
    role: Literal["viewer", "operator", "approver", "admin"]


class ChatMessageRequest(BaseModel):
    message: str = Field(min_length=1, max_length=16000)


class OrderRequest(BaseModel):
    """A paper market order; the reference price is today's quote."""

    ticker: str = Field(min_length=1, max_length=16)
    action: OrderAction
    quantity: int = Field(gt=0, le=1_000_000)
    reasoning: str = Field(default="", max_length=500)


class CreateScheduleRequest(BaseModel):
    id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    definition: dict[str, Any]
    enabled: bool = False


class UpdateScheduleRequest(BaseModel):
    definition: dict[str, Any]
    enabled: bool = False


def create_app(
    *,
    manager: RunManager | None = None,
    registry: IntegrationRegistry | None = None,
    verifier: WebhookVerifier | None = None,
    config: ServerConfig | None = None,
    chat_graph: Any | None = None,
    order_graph: Any | None = None,
    approval_policy: ApprovalPolicy | None = None,
    scheduler_service: Any | None = None,
    slack_gateway: Any | None = None,
    telegram_gateway: Any | None = None,
    paper: PaperBroker | None = None,
    ui_dir: Path | None = None,
    reference_app_store: ReferenceStore | None = None,
    reference_app_profile: ReferenceAppProfile | None = None,
    reference_app_export_dir: Path | None = None,
    reference_app_ollama: Any | None = None,
    reference_app_hedge_fund: Any | None = None,
) -> FastAPI:
    """Create a production Postgres server or an injected offline ASGI test app."""
    disable_external_tracing()
    options = config or ServerConfig()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if manager is not None:
            app.state.paper_broker = paper
            app.state.manager = manager
            app.state.registry = registry or IntegrationRegistry([], env_file=None)
            app.state.verifier = verifier or WebhookVerifier()
            app.state.chat_graph = chat_graph
            app.state.order_graph = order_graph
            app.state.approval_policy = approval_policy or ApprovalPolicy.from_settings(Settings())
            app.state.scheduler = scheduler_service
            app.state.slack_gateway = slack_gateway
            app.state.telegram_gateway = telegram_gateway
            app.state.reference_app_context_factory = getattr(manager, "context_factory", None)
            app.state.events = EventListener()
            await app.state.events.start()
            try:
                await manager.start()
                yield
            finally:
                backtests = getattr(app.state, "reference_app_backtest_manager", None)
                if backtests is not None:
                    await backtests.close()
                await app.state.events.stop()
                await manager.stop()
                await manager.storage.close()
            return
        database_url = options.require_database_url()
        bootstrap = options.bootstrap_admin_key
        if bootstrap is None or len(bootstrap.get_secret_value()) < 32:
            raise ValueError("BOOTSTRAP_ADMIN_KEY must contain at least 32 characters")
        await asyncio.to_thread(upgrade, database_url)
        engine = create_async_engine(database_url, pool_pre_ping=True)
        storage = Storage(engine, lock_pool_size=max(8, options.max_concurrent_runs + 8))
        listener = EventListener(database_url)
        app.state.events = listener
        async with AsyncExitStack() as stack:
            stack.push_async_callback(engine.dispose)
            stack.push_async_callback(storage.close)
            stack.push_async_callback(listener.stop)
            if reference_app_store is None and engine.dialect.name == "postgresql":
                derived_key = base64.urlsafe_b64encode(
                    hashlib.sha256(
                        f"fin-graph-compatibility:{bootstrap.get_secret_value()}".encode()
                    ).digest()
                )
                app.state.reference_app_store = await stack.enter_async_context(
                    open_reference_postgres(engine, cipher=FernetSecretCipher(derived_key))
                )
            settings = Settings(
                persistence_backend=PersistenceBackend.POSTGRES,
                database_url=options.database_url,
            )
            await storage.put_key(bootstrap.get_secret_value(), "admin")
            await listener.start()
            saver = await stack.enter_async_context(open_checkpointer(settings))
            store = await stack.enter_async_context(open_store(settings))
            theses = ThesisMemory(store)
            active_policy = approval_policy or ApprovalPolicy.from_settings(settings)
            active_registry = registry or default_registry()
            if registry is None:
                from fin_graph.integrations.mcp.runtime import with_configured_mcp

                active_registry = await with_configured_mcp(active_registry)
            delivery = DeliveryService.from_env(ledger=storage)
            stack.push_async_callback(delivery.aclose)
            # Paper orders share the server database; migration 0011 creates the tables.
            if paper is None:
                paper_context = active_registry.run_context()
                stack.push_async_callback(paper_context.aclose)
                active_paper = paper_broker(
                    Settings(), PaperLedgerStore(engine), context_quotes(paper_context)
                )
            else:
                active_paper = paper
            app.state.paper_broker = active_paper

            def context_factory() -> RunContext:
                context = active_registry.run_context()
                return replace(context, clients={**context.clients, "paper_broker": active_paper})

            app.state.reference_app_context_factory = context_factory

            analysis_services = _analysis_services(settings)
            graph = make_analysis_graph(
                analysis_services,
                active_registry,
                checkpointer=saver,
                memory=theses,
                delivery=delivery,
                settings=settings,
            )
            if chat_graph is not None:
                active_chat = chat_graph
            else:
                try:
                    model = get_model(ModelRole.REASONING, settings)
                except (ValueError, TypeError, ImportError):
                    _LOGGER.warning("chat model unavailable at startup")
                    active_chat = None
                else:
                    app.state.reference_app_quant_llm = model
                    from fin_graph.adapters.reference_app.runtime import (
                        make_reference_analysis_supervisor,
                    )

                    chat_analysis_context = context_factory()
                    stack.push_async_callback(chat_analysis_context.aclose)
                    chat_analysis = make_reference_analysis_supervisor(
                        model,
                        active_registry,
                        chat_analysis_context,
                        MarketSnapshotServices(),
                        checkpointer=False,  # Disable the inherited chat saver.
                        settings=settings,
                    )
                    app.state.reference_app_analysis_graph = chat_analysis
                    app.state.reference_app_backtest_runner = make_v1_backtest_runner(
                        chat_analysis, context_factory
                    )
                    from fin_graph.agents.chat.supervisor import make_chat_graph

                    chat_context = context_factory()
                    stack.push_async_callback(chat_context.aclose)
                    active_chat = make_chat_graph(
                        model,
                        active_registry,
                        chat_context,
                        theses,
                        policy=active_policy,
                        analysis=chat_analysis,
                        checkpointer=saver,
                        store=store,
                        settings=settings,
                    )
            active_orders = order_graph or make_order_graph(
                OrderDesk(active_paper), active_policy, checkpointer=saver
            )
            active_manager = RunManager(
                graph,
                storage,
                context_factory,
                max_concurrent=options.max_concurrent_runs,
                max_pending=options.max_pending_runs,
            )
            app.state.manager = active_manager
            app.state.registry = active_registry
            app.state.verifier = verifier or _verifier_from_config(options)
            app.state.chat_graph = active_chat
            app.state.order_graph = active_orders
            app.state.approval_policy = active_policy
            stack.push_async_callback(active_manager.stop)
            await active_manager.start()
            market_jobs = MarketJobs(
                storage,
                active_manager,
                context_factory,
                broker=active_paper,
                memory=theses,
                send_text=lambda message, destinations, key: send_digest_text(
                    {
                        "slack": delivery.clients["slack"],
                        "telegram": delivery.clients["telegram"],
                    },
                    storage,
                    message,
                    destinations,
                    key,
                ),
            )

            async def run_retention() -> None:
                await _retention_cycle(
                    active_manager,
                    saver,
                    datetime.now(UTC) - timedelta(days=options.thread_retention_days),
                    app.state.chat_locks,
                    app.state.order_locks,
                )

            async def run_expiry() -> None:
                await expire_pending_approvals(
                    storage=storage,
                    chat_graph=active_chat,
                    order_graph=cast(ApprovalGraph, active_orders),
                    chat_locks=app.state.chat_locks,
                    order_locks=app.state.order_locks,
                    context_factory=context_factory,
                    ttl=timedelta(hours=options.approval_ttl_hours),
                )

            async def run_reconcile() -> None:
                await active_scheduler.reconcile()

            async def run_gateway_replay() -> None:
                for gateway in (app.state.slack_gateway, app.state.telegram_gateway):
                    if isinstance(gateway, (SlackGateway, TelegramGateway)):
                        try:
                            await gateway.replay_pending()
                        except Exception:
                            _LOGGER.warning("gateway replay cycle failed")

            active_scheduler = scheduler_service or SchedulerService(
                storage,
                {
                    "market_watch": market_jobs.market_watch,
                    "portfolio_digest": market_jobs.portfolio_digest,
                    "thesis_review": market_jobs.thesis_review,
                    "watch_digest": market_jobs.watch_digest,
                },
                internal_jobs={
                    "retention": run_retention,
                    "expiry": run_expiry,
                    "reconcile": run_reconcile,
                    "gateway_replay": run_gateway_replay,
                },
                database_url=database_url,
                election_interval_seconds=options.scheduler_election_seconds,
            )
            stack.push_async_callback(active_scheduler.stop)
            app.state.scheduler = active_scheduler

            async def submit_gateway_request(
                text: str, platform: str, destination: str, remote_id: str, event_id: str
            ) -> GatewayResult:
                thread_id = str(uuid5(NAMESPACE_URL, f"fin-graph:gateway:{platform}:{event_id}"))
                fingerprint = hashlib.sha256(f"{platform}:{event_id}".encode()).hexdigest()
                run_id = str(uuid4())
                with active_manager.reserve() as reservation:
                    claimed, thread_id = await storage.claim_webhook_run(
                        "gateway",
                        fingerprint,
                        thread_id,
                        run_id,
                        datetime.now(UTC),
                        timedelta(days=365),
                    )
                    if claimed:
                        try:
                            active_manager.schedule_existing(
                                run_id, thread_id, text, None, reservation=reservation
                            )
                        except (Exception, asyncio.CancelledError):
                            await storage.set_status(run_id, "failed", "schedule_unavailable")
                            await storage.release_thread_fingerprints(thread_id)
                            raise
                if not claimed:
                    previous = await storage.latest_run(thread_id)
                    if previous is None:
                        raise RuntimeError("gateway run ledger is missing")
                    run_id = previous["run_id"]
                    if previous["status"] != "completed":
                        await active_manager.recover_existing(run_id, thread_id, text)
                await active_manager.wait(run_id)
                run = await storage.get_run(run_id)
                if run is not None and run["status"] in {"queued", "running", "interrupted"}:
                    raise GatewayRunPending()
                if run is not None and run["status"] == "recovering":
                    raise GatewayRunPending()
                if run is None or run["status"] != "completed":
                    return GatewayResult(thread_id, "El análisis falló.")
                snapshot = await graph.aget_state({"configurable": {"thread_id": thread_id}})
                report_data = snapshot.values.get("report") if snapshot.values else None
                if not report_data:
                    return GatewayResult(thread_id, "El mensaje no nombra ninguna acción.")
                report = AnalysisReport.model_validate(report_data)
                # One proposed order gets approval buttons; several stay in the API and CLI.
                orders = [section.order for section in report.sections if section.order]
                approval: tuple[str, str] | None = None
                if len(orders) == 1:
                    approval = await _start_order_thread(app, orders[0], thread_id)
                order_thread, approval_id = approval or (thread_id, None)
                kind = "orders" if approval_id else None
                if platform == "slack":
                    payload = cast(dict[str, Any], redact_value(render_analysis_slack(report)))
                    return GatewayResult(
                        order_thread,
                        str(payload["text"]),
                        approval_kind=kind,
                        report_blocks=cast(list[dict[str, Any]], payload["blocks"]),
                        approval_id=approval_id,
                    )
                if platform == "telegram":
                    return GatewayResult(
                        order_thread,
                        redact_secrets(render_analysis_telegram(report)),
                        approval_kind=kind,
                        approval_id=approval_id,
                    )
                raise ValueError("unsupported gateway platform")

            async def gateway_decide(
                kind: str, thread_id: str, choice: str, actor: str, approval_id: str
            ) -> str:
                return await _gateway_decide(app, kind, thread_id, choice, actor, approval_id)

            slack_options = SlackGatewaySettings()
            telegram_options = TelegramGatewaySettings()
            app.state.slack_gateway = slack_gateway or (
                SlackGateway(slack_options, storage, submit_gateway_request, gateway_decide)
                if slack_options.enabled
                else None
            )
            if app.state.slack_gateway is not None:
                stack.push_async_callback(app.state.slack_gateway.close)
            app.state.telegram_gateway = telegram_gateway or (
                TelegramGateway(telegram_options, storage, submit_gateway_request, gateway_decide)
                if telegram_options.enabled
                else None
            )
            if app.state.telegram_gateway is not None:
                stack.push_async_callback(app.state.telegram_gateway.close)
            await active_scheduler.start()
            if isinstance(app.state.slack_gateway, SlackGateway):
                await app.state.slack_gateway.start_socket_mode()
            try:
                yield
            finally:
                backtests = getattr(app.state, "reference_app_backtest_manager", None)
                if backtests is not None:
                    await backtests.close()

    app = FastAPI(title="fin-graph", lifespan=lifespan)
    app.state.reference_app_store = reference_app_store
    app.state.reference_app_profile = reference_app_profile or ReferenceAppProfile.from_env(
        os.environ
    )
    app.state.reference_app_export_dir = (
        reference_app_export_dir or Path(os.getenv("AI_HEDGE_FUND_OUTPUT_DIR", "outputs"))
    ).resolve()
    app.state.reference_app_ollama = reference_app_ollama or OllamaReferenceService(
        Settings(_env_file=None).ollama_base_url
    )
    app.state.reference_app_hedge_fund = reference_app_hedge_fund
    app.state.reference_app_analysis_graph = None
    app.state.reference_app_backtest_runner = None
    app.state.reference_app_context_factory = None
    app.state.reference_app_quant_data = None
    app.state.reference_app_quant_llm = None
    app.state.reference_app_quant_artifact_dir = Path(
        os.getenv("FIN_GRAPH_QUANT_ARTIFACT_DIR", ".v2_cache/models")
    ).resolve()
    app.state.reference_app_backtest_artifact_dir = Path(
        os.getenv("FIN_GRAPH_BACKTEST_ARTIFACT_DIR", ".v2_cache/backtests")
    ).resolve()
    app.state.reference_app_backtest_manager = None
    app.state.reference_app_key_role = None
    from fin_graph.server.http_guards import install_http_guards

    install_http_guards(
        app,
        max_requests=options.max_http_requests,
        max_streams=options.max_streams,
        max_body_bytes=_MAX_BODY,
    )

    @app.exception_handler(RunOverloaded)
    async def overloaded(request: Request, exc: RunOverloaded) -> JSONResponse:
        return JSONResponse(
            {"detail": "run capacity exhausted"}, status_code=429, headers={"Retry-After": "1"}
        )

    app.state.chat_locks = WeakValueDictionary()
    app.state.order_locks = WeakValueDictionary()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(options.cors_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["X-API-Key", "Last-Event-ID", "Content-Type"],
    )
    app.add_middleware(GZipMiddleware, minimum_size=500, compresslevel=6)

    @app.get("/health")
    @app.get("/v1/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/ready")
    @app.get("/v1/ready")
    async def ready(request: Request) -> dict[str, str]:
        active = _manager(request)
        try:
            async with active.storage.engine.connect() as connection:
                await connection.execute(sql_text("SELECT 1"))
        except Exception as exc:
            raise HTTPException(503, "database unavailable") from exc
        return {"status": "ready"}

    @app.get("/v1/session")
    async def session(
        request: Request, x_api_key: str | None = Header(default=None)
    ) -> dict[str, str]:
        role = await _authorize(request, x_api_key, "viewer")
        return {"role": role}

    @app.post("/v1/api-keys", response_model=None)
    async def create_key(
        request: Request,
        body: CreateKeyRequest,
        x_api_key: str | None = Header(default=None),
    ) -> JSONResponse:
        await _authorize(request, x_api_key, "admin")
        key = secrets.token_urlsafe(32)
        await _manager(request).storage.put_key(key, body.role)
        return JSONResponse(
            {"key": key, "role": body.role},
            status_code=201,
            headers={"Cache-Control": "no-store"},
        )

    @app.post("/v1/analyses", status_code=202)
    async def analyze(
        request: Request, x_api_key: str | None = Header(default=None)
    ) -> dict[str, Any]:
        await _authorize(request, x_api_key, "operator")
        _, payload = await _bounded_json_object(request)
        try:
            body = AnalysisRequestBody.model_validate(payload)
        except ValidationError as exc:
            raise HTTPException(422, "invalid analysis request") from exc
        if (body.text is None) == (body.request is None):
            raise HTTPException(422, "provide exactly one of text or request")
        if body.request is not None:
            try:
                request_from_payload(body.request)
            except ValueError as exc:
                raise HTTPException(422, "the request names no valid ticker") from exc
            return await _manager(request).submit(
                body.request, RequestSource.JSON.value, destinations=body.destinations
            )
        return await _manager(request).submit(body.text or "", None, destinations=body.destinations)

    @app.post("/v1/chat/threads/{thread_id}/messages")
    async def chat_message(
        request: Request, thread_id: str, x_api_key: str | None = Header(default=None)
    ) -> StreamingResponse:
        await _authorize(request, x_api_key, "operator")
        graph = _chat_graph(request)
        if not 1 <= len(thread_id) <= 64:
            raise HTTPException(422, "invalid thread id")
        _, payload = await _bounded_json_object(request)
        try:
            body = ChatMessageRequest.model_validate(payload)
        except ValidationError as exc:
            raise HTTPException(422, "invalid chat message") from exc
        config = _graph_config("chat", thread_id)
        locks: MutableMapping[str, asyncio.Lock] = request.app.state.chat_locks
        lock = locks.setdefault(thread_id, asyncio.Lock())
        existing = await graph.aget_state(config)
        if any(task.interrupts for task in existing.tasks):
            raise HTTPException(409, "approval pending for this thread")

        async def events() -> AsyncIterator[str]:
            async with lock, _manager(request).storage.approval_lock("chat", thread_id):
                try:
                    current = await graph.aget_state(config)
                    if any(task.interrupts for task in current.tasks):
                        yield _sse("error", {"error": "approval_pending"})
                        return
                    await _manager(request).storage.touch_checkpoint_thread(thread_id, "chat")
                    seen: set[str] = set()
                    context = _manager(request).context_factory()
                    async with close_run_context(context):
                        async for _, snapshot in graph.astream(
                            {"messages": [HumanMessage(body.message)]},
                            config=config,
                            context=context,
                            stream_mode=["values"],
                        ):
                            if not isinstance(snapshot, dict):
                                continue
                            messages = snapshot.get("messages") or []
                            if messages and isinstance(messages[-1], AIMessage):
                                message = messages[-1]
                                if message.tool_calls or message.id in seen:
                                    continue
                                if message.id:
                                    seen.add(message.id)
                                content = redact_secrets(str(message.text))
                                yield _sse("message", {"text": content, "thread_id": thread_id})
                    state = await graph.aget_state(config)
                    pending_items = [item for task in state.tasks for item in task.interrupts]
                    pending = [item.value for item in pending_items]
                    if pending:
                        await _notify_gateways(request.app, "chat", thread_id, pending_items)
                        yield _sse(
                            "approval",
                            {
                                "requests": redact_value(pending),
                                "approval_ids": [item.id for item in pending_items],
                            },
                        )
                    else:
                        yield _sse("done", {"thread_id": thread_id})
                except Exception as exc:
                    _LOGGER.warning("chat turn failed: %s", type(exc).__name__)
                    yield _sse("error", {"error": type(exc).__name__})

        return StreamingResponse(events(), media_type="text/event-stream")

    @app.get("/v1/chat/threads/{thread_id}")
    async def chat_status(
        request: Request, thread_id: str, x_api_key: str | None = Header(default=None)
    ) -> dict[str, Any]:
        await _authorize(request, x_api_key, "operator")
        if not 1 <= len(thread_id) <= 64:
            raise HTTPException(422, "invalid thread id")
        state = await _chat_graph(request).aget_state(_graph_config("chat", thread_id))
        if not state.values and not state.tasks:
            raise HTTPException(404, "chat thread not found")
        pending_items = [item for task in state.tasks for item in task.interrupts]
        pending = [item.value for item in pending_items]
        return {
            "thread_id": thread_id,
            "status": "awaiting_approval" if pending else "completed",
            "requests": redact_value(pending),
            "approval_ids": [item.id for item in pending_items],
            "messages": _public_messages(state.values.get("messages", [])),
        }

    @app.post("/v1/chat/threads/{thread_id}/approve")
    async def approve_chat(
        request: Request, thread_id: str, x_api_key: str | None = Header(default=None)
    ) -> dict[str, Any]:
        role = await _authorize(request, x_api_key, "approver")
        graph = _chat_graph(request)
        if not 1 <= len(thread_id) <= 64:
            raise HTTPException(422, "invalid thread id")
        _, payload = await _bounded_json_object(request)
        decisions = payload.get("decisions")
        if (
            not isinstance(decisions, list)
            or not 1 <= len(decisions) <= 10
            or any(
                not isinstance(item, dict) or item.get("type") not in {"approve", "edit", "reject"}
                for item in decisions
            )
        ):
            raise HTTPException(422, "invalid approval decisions")
        locks: MutableMapping[str, asyncio.Lock] = request.app.state.chat_locks
        lock = locks.setdefault(thread_id, asyncio.Lock())
        config = _graph_config("chat", thread_id)
        async with lock, _manager(request).storage.approval_lock("chat", thread_id):
            state = await graph.aget_state(config)
            pending = [item for task in state.tasks for item in task.interrupts]
            if not pending:
                raise HTTPException(409, "no pending approval")
            _check_approval_ids(payload, pending)
            if len(decisions) != sum(
                len(item.value.get("action_requests", []))
                for item in pending
                if isinstance(item.value, dict)
            ):
                raise HTTPException(422, "decision count does not match pending actions")
            actor = hashlib.sha256((x_api_key or "").encode()).hexdigest()
            if not request.app.state.approval_policy.allows_approver(actor, role):
                raise HTTPException(403, "approver is not allowlisted")
            base_context = _manager(request).context_factory()
            if not isinstance(base_context, RunContext):
                raise HTTPException(503, "integration context unavailable")
            context = replace(base_context, actor=actor, role=role)
            approval_records = _approval_records(pending, decisions)
            async with close_run_context(context):
                await _manager(request).storage.touch_checkpoint_thread(thread_id, "chat")
                for decision, action_name, args_digest in approval_records:
                    await _manager(request).storage.record_approval(
                        thread_id, "chat", actor, decision, action_name, args_digest
                    )
                try:
                    result = await graph.ainvoke(
                        Command(resume={"decisions": decisions}), config, context=context
                    )
                except (ValueError, PermissionError) as exc:
                    raise HTTPException(422, type(exc).__name__) from exc
                except RuntimeError as exc:
                    raise HTTPException(409, "the paper book kept changing; retry") from exc
            remaining = result.get("__interrupt__", ())
            if remaining:
                await _notify_gateways(request.app, "chat", thread_id, remaining)
            messages = result.get("messages") or []
            reply = (
                str(messages[-1].text) if messages and isinstance(messages[-1], AIMessage) else ""
            )
            return {
                "status": "interrupted" if remaining else "completed",
                "thread_id": thread_id,
                "reply": redact_secrets(reply),
                "requests": redact_value([item.value for item in remaining]),
                "approval_ids": [item.id for item in remaining],
            }

    @app.post("/v1/orders", status_code=202)
    async def propose_order(
        request: Request, x_api_key: str | None = Header(default=None)
    ) -> dict[str, Any]:
        await _authorize(request, x_api_key, "operator")
        broker = _paper(request)
        _, payload = await _bounded_json_object(request)
        try:
            body = OrderRequest.model_validate(payload)
            symbol = normalize_ticker(body.ticker)
        except (ValidationError, RequestParseError) as exc:
            raise HTTPException(422, "invalid order request") from exc
        if body.action is OrderAction.HOLD:
            raise HTTPException(422, "hold is not an order")
        try:
            quote = await broker.quotes(symbol)
        except (IntegrationError, TypeError) as exc:
            raise HTTPException(503, "market data unavailable") from exc
        if not quote or quote <= 0:
            raise HTTPException(503, f"no quote for {symbol}")
        thread_id = str(uuid4())
        today = datetime.now(UTC).date()
        order = ProposedOrder(
            client_order_id=client_order_id(
                f"api:{thread_id}", symbol, body.action, body.quantity, today
            ),
            ticker=symbol,
            action=body.action,
            quantity=body.quantity,
            reference_price=quote,
            as_of=today,
            reasoning=body.reasoning,
        )
        result = await _run_order(request.app, thread_id, {"order": order.model_dump(mode="json")})
        return _order_view(thread_id, result)

    @app.post("/v1/analyses/{thread_id}/orders", status_code=202)
    async def propose_analysis_orders(
        request: Request, thread_id: str, x_api_key: str | None = Header(default=None)
    ) -> list[dict[str, Any]]:
        """Put every order an analysis proposed up for approval, once per order."""
        await _authorize(request, x_api_key, "operator")
        active = _manager(request)
        if await active.storage.latest_run(thread_id) is None:
            raise HTTPException(404, "analysis not found")
        snapshot = await active.graph.aget_state(active._config(thread_id))
        orders = [
            ProposedOrder.model_validate(item) for item in (snapshot.values or {}).get("orders", [])
        ]
        views = []
        for order in orders:
            order_thread = str(uuid5(NAMESPACE_URL, f"{thread_id}:{order.client_order_id}"))
            state = await _order_graph(request).aget_state(_graph_config("orders", order_thread))
            if state.values:
                views.append(_order_view(order_thread, dict(state.values), state))
                continue
            result = await _run_order(
                request.app, order_thread, {"order": order.model_dump(mode="json")}
            )
            views.append(_order_view(order_thread, result))
        return views

    @app.get("/v1/orders")
    async def list_orders(
        request: Request,
        x_api_key: str | None = Header(default=None),
        status: OrderStatus | None = None,
    ) -> list[dict[str, Any]]:
        await _authorize(request, x_api_key, "viewer")
        executions = await _paper(request).orders(status=status)
        return [item.model_dump(mode="json") for item in executions]

    @app.get("/v1/orders/pending")
    async def pending_orders(
        request: Request, x_api_key: str | None = Header(default=None)
    ) -> list[dict[str, Any]]:
        await _authorize(request, x_api_key, "viewer")
        graph = _order_graph(request)
        views = []
        for order_thread in await _manager(request).storage.checkpoint_threads("orders"):
            state = await graph.aget_state(_graph_config("orders", order_thread))
            if any(task.interrupts for task in state.tasks):
                views.append(_order_view(order_thread, dict(state.values), state))
        return views

    @app.post("/v1/orders/settle")
    async def settle_orders(
        request: Request, x_api_key: str | None = Header(default=None)
    ) -> list[dict[str, Any]]:
        await _authorize(request, x_api_key, "operator")
        try:
            settled = await _paper(request).settle()
        except PaperBrokerError as exc:
            raise HTTPException(422, str(exc)) from exc
        return [item.model_dump(mode="json") for item in settled]

    @app.post("/v1/orders/{client_order_id}/cancel")
    async def cancel_order(
        request: Request, client_order_id: str, x_api_key: str | None = Header(default=None)
    ) -> dict[str, Any]:
        await _authorize(request, x_api_key, "approver")
        try:
            execution = await _paper(request).cancel(client_order_id)
        except PaperBrokerError as exc:
            raise HTTPException(409, str(exc)) from exc
        return execution.model_dump(mode="json")

    @app.get("/v1/orders/{thread_id}")
    async def order_status(
        request: Request, thread_id: str, x_api_key: str | None = Header(default=None)
    ) -> dict[str, Any]:
        await _authorize(request, x_api_key, "viewer")
        state = await _order_graph(request).aget_state(_graph_config("orders", thread_id))
        if not state.values:
            raise HTTPException(404, "order thread not found")
        return _order_view(thread_id, dict(state.values), state)

    @app.post("/v1/orders/{thread_id}/approve")
    async def approve_order(
        request: Request, thread_id: str, x_api_key: str | None = Header(default=None)
    ) -> dict[str, Any]:
        role = await _authorize(request, x_api_key, "approver")
        graph = _order_graph(request)
        _, payload = await _bounded_json_object(request)
        decisions = payload.get("decisions")
        if (
            not isinstance(decisions, list)
            or len(decisions) != 1
            or not isinstance(decisions[0], dict)
            or decisions[0].get("type") not in {"approve", "edit", "reject"}
        ):
            raise HTTPException(422, "one valid decision is required")
        if decisions[0]["type"] == "edit":
            edited = decisions[0].get("edited_action")
            args = edited.get("args") if isinstance(edited, dict) else None
            quantity = args.get("quantity") if isinstance(args, dict) else None
            if (
                not isinstance(edited, dict)
                or edited.get("name") != PLACE_ORDER
                or set(args or {}) != {"quantity"}
                or not isinstance(quantity, int)
                or isinstance(quantity, bool)
                or quantity <= 0
            ):
                raise HTTPException(422, "an edit may only change the positive integer quantity")
        locks: MutableMapping[str, asyncio.Lock] = request.app.state.order_locks
        lock = locks.setdefault(thread_id, asyncio.Lock())
        config = _graph_config("orders", thread_id)
        async with lock, _manager(request).storage.approval_lock("orders", thread_id):
            state = await graph.aget_state(config)
            pending = [item for task in state.tasks for item in task.interrupts]
            if not pending:
                raise HTTPException(409, "no pending approval")
            _check_approval_ids(payload, pending)
            actor = hashlib.sha256((x_api_key or "").encode()).hexdigest()
            if not request.app.state.approval_policy.allows_approver(actor, role):
                raise HTTPException(403, "approver is not allowlisted")
            base_context = _manager(request).context_factory()
            if not isinstance(base_context, RunContext):
                raise HTTPException(503, "integration context unavailable")
            context = replace(base_context, actor=actor, role=role)
            approval_records = _approval_records(pending, decisions)
            async with close_run_context(context):
                await _manager(request).storage.touch_checkpoint_thread(thread_id, "orders")
                for decision, action_name, args_digest in approval_records:
                    await _manager(request).storage.record_approval(
                        thread_id, "orders", actor, decision, action_name, args_digest
                    )
                try:
                    result = await graph.ainvoke(
                        Command(resume={"decisions": decisions}), config, context=context
                    )
                except (ValueError, ValidationError, PermissionError, PaperBrokerError) as exc:
                    raise HTTPException(422, type(exc).__name__) from exc
                except IntegrationNotConfiguredError as exc:
                    raise HTTPException(503, "paper broker unavailable") from exc
                except RuntimeError as exc:
                    raise HTTPException(409, "the paper book kept changing; retry") from exc
            pending_after = result.get("__interrupt__", ())
            if pending_after:
                await _notify_gateways(request.app, "orders", thread_id, pending_after)
            return _order_view(thread_id, result)

    @app.get("/v1/portfolio")
    async def portfolio(
        request: Request, x_api_key: str | None = Header(default=None)
    ) -> dict[str, Any]:
        await _authorize(request, x_api_key, "viewer")
        broker = _paper(request)
        book = await broker.portfolio()
        quotes: dict[str, float | None] = {}
        for ticker in book.positions:
            try:
                quotes[ticker] = await broker.quotes(ticker)
            except (IntegrationError, TypeError):
                quotes[ticker] = None
        prices = {ticker: price for ticker, price in quotes.items() if price}
        positions = []
        for ticker, held in sorted(book.positions.items()):
            price = quotes.get(ticker)
            for side, shares, cost in (
                ("long", held.long, held.long_cost_basis),
                ("short", held.short, held.short_cost_basis),
            ):
                if not shares:
                    continue
                pnl = None if price is None else (price - cost) * shares
                positions.append(
                    {
                        "ticker": ticker,
                        "side": side,
                        "shares": shares,
                        "cost_basis": cost,
                        "price": price,
                        "market_value": None if price is None else shares * price,
                        "unrealized_pnl": None
                        if pnl is None
                        else round(pnl if side == "long" else -pnl, 2),
                    }
                )
        queued = await broker.orders(status=OrderStatus.QUEUED)
        return {
            "currency": book.currency,
            "cash": book.cash,
            "margin_used": book.margin_used,
            "net_liquidation_value": round(book.net_liquidation_value(prices), 2),
            "positions": positions,
            "queued_orders": len(queued),
            "paper": True,
        }

    @app.get("/v1/analyses/{thread_id}/explain")
    async def explain_analysis(
        request: Request, thread_id: str, x_api_key: str | None = Header(default=None)
    ) -> dict[str, Any]:
        await _authorize(request, x_api_key, "viewer")
        active = _manager(request)
        run = await active.storage.latest_run(thread_id)
        if run is None:
            raise HTTPException(404, "thread not found")
        snapshot = await active.graph.aget_state(active._config(thread_id))
        events = await active.storage.events(thread_id, 0, MAX_EXPLAIN_EVENTS + 1)
        return cast(
            dict[str, Any],
            redact_value(
                build_explanation(
                    thread_id,
                    run,
                    events[:MAX_EXPLAIN_EVENTS],
                    snapshot.values or {},
                    events_truncated=len(events) > MAX_EXPLAIN_EVENTS,
                )
            ),
        )

    @app.get("/v1/analyses/{thread_id}")
    async def analysis(
        request: Request, thread_id: str, x_api_key: str | None = Header(default=None)
    ) -> dict[str, Any]:
        await _authorize(request, x_api_key, "viewer")
        active = _manager(request)
        run = await active.storage.latest_run(thread_id)
        if run is None:
            raise HTTPException(404, "thread not found")
        snapshot = await active.graph.aget_state(active._config(thread_id))
        values = snapshot.values or {}
        return cast(
            dict[str, Any],
            redact_value(
                {
                    "run": run,
                    "report": values.get("report") or None,
                    "request": values.get("request") or None,
                    "harness": values.get("harness") or None,
                    **{
                        key: values.get(key)
                        for key in (
                            "plan",
                            "findings",
                            "evidence",
                            "theses",
                            "verification",
                            "limits",
                            "decisions",
                            "orders",
                            "degraded",
                            "errors",
                        )
                    },
                }
            ),
        )

    @app.get("/v1/threads")
    async def threads(
        request: Request, x_api_key: str | None = Header(default=None), limit: int = 50
    ) -> list[dict[str, Any]]:
        await _authorize(request, x_api_key, "viewer")
        if not 1 <= limit <= 200:
            raise HTTPException(422, "limit must be 1..200")
        return await _manager(request).storage.list_runs(limit)

    @app.get("/v1/threads/{thread_id}/history")
    async def history(
        request: Request,
        thread_id: str,
        x_api_key: str | None = Header(default=None),
        after_id: int = 0,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        await _authorize(request, x_api_key, "viewer")
        if after_id < 0 or not 1 <= limit <= 500:
            raise HTTPException(422, "invalid history cursor or limit")
        active = _manager(request)
        if await active.storage.latest_run(thread_id) is None:
            raise HTTPException(404, "thread not found")
        return await active.storage.events(thread_id, after_id, limit)

    @app.get("/v1/threads/{thread_id}/stream")
    async def stream(
        request: Request,
        thread_id: str,
        x_api_key: str | None = Header(default=None),
        last_event_id: str | None = Header(default=None),
    ) -> StreamingResponse:
        await _authorize(request, x_api_key, "viewer")
        active = _manager(request)
        if await active.storage.latest_run(thread_id) is None:
            raise HTTPException(404, "thread not found")
        try:
            cursor = int(last_event_id) if last_event_id else 0
            if cursor < 0:
                raise ValueError
        except ValueError as exc:
            raise HTTPException(400, "invalid Last-Event-ID") from exc

        async def events() -> AsyncIterator[str]:
            nonlocal cursor
            async with request.app.state.events.subscribe(thread_id) as signal:
                while not await request.is_disconnected():
                    signal.clear()
                    batch = await active.storage.events(thread_id, cursor)
                    for item in batch:
                        cursor = item["id"]
                        yield f"id: {cursor}\nevent: {item['type']}\ndata: {json.dumps(item)}\n\n"
                    latest = await active.storage.latest_run(thread_id)
                    if (
                        not batch
                        and latest
                        and latest["status"] in {"completed", "failed", "interrupted"}
                    ):
                        # The terminal transaction can commit between the empty event
                        # read and the status read; drain that commit before closing.
                        if await active.storage.events(thread_id, cursor):
                            continue
                        break
                    if batch:
                        continue
                    try:
                        await asyncio.wait_for(
                            signal.wait(), timeout=options.stream_fallback_seconds
                        )
                    except TimeoutError:
                        yield ": keepalive\n\n"

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/v1/threads/{thread_id}/resume", status_code=202)
    async def resume(
        request: Request, thread_id: str, x_api_key: str | None = Header(default=None)
    ) -> dict[str, Any]:
        await _authorize(request, x_api_key, "operator")
        try:
            return await _manager(request).resume(thread_id)
        except LookupError as exc:
            raise HTTPException(404, "thread not found") from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/v1/signals/{source}", status_code=202, response_model=None)
    async def signal(request: Request, source: SignalSource) -> dict[str, Any] | JSONResponse:
        raw = await _bounded_body(request)
        active_verifier: WebhookVerifier = request.app.state.verifier
        header_ok = active_verifier.header_authenticated(source, raw, request.headers)
        try:
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError("signal must be an object")
        except (ValueError, UnicodeError) as exc:
            raise HTTPException(400 if header_ok else 401, "invalid JSON signal") from exc
        try:
            verified = active_verifier.verify(source, raw, request.headers, payload)
        except ValueError as exc:
            raise HTTPException(401, "signal authentication failed") from exc
        kind = RequestSource(REQUEST_SOURCE[source])
        try:
            parsed = request_from_payload(verified, source=kind)
        except ValueError as exc:
            raise HTTPException(422, "the signal names no valid ticker") from exc
        fingerprint = request_fingerprint(parsed.tickers, parsed.as_of, kind)
        active = _manager(request)
        thread_id = str(uuid4())
        run_id = str(uuid4())
        with active.reserve() as reservation:
            claimed, prior = await active.storage.claim_webhook_run(
                source.value,
                fingerprint,
                thread_id,
                run_id,
                datetime.now(UTC),
                timedelta(minutes=options.signal_dedupe_minutes),
            )
            if not claimed:
                return JSONResponse({"thread_id": prior, "deduplicated": True})
            try:
                result = active.schedule_existing(
                    run_id,
                    thread_id,
                    verified,
                    kind.value,
                    destinations=options.signal_delivery_routes.get(
                        source.value, options.signal_delivery_routes.get("default", {})
                    ),
                    user_requested=False,
                    reservation=reservation,
                )
            except (Exception, asyncio.CancelledError):
                await active.storage.set_status(run_id, "failed", "schedule_failed")
                await active.storage.release_fingerprint(source.value, fingerprint, thread_id)
                raise

        return {**result, "deduplicated": False}

    @app.get("/v1/integrations")
    async def integrations(
        request: Request, x_api_key: str | None = Header(default=None)
    ) -> list[dict[str, Any]]:
        await _authorize(request, x_api_key, "viewer")
        active_registry: IntegrationRegistry = request.app.state.registry
        context = active_registry.run_context()
        async with close_run_context(context):
            return [
                {
                    "id": identity,
                    "status": (
                        "available"
                        if identity in context.clients
                        else "failed"
                        if identity in context.unavailable
                        else "not_configured"
                    ),
                }
                for identity in active_registry.ids
            ]

    @app.post("/v1/integrations/verify")
    async def verify_integrations(
        request: Request, x_api_key: str | None = Header(default=None)
    ) -> list[dict[str, Any]]:
        await _authorize(request, x_api_key, "operator")
        active_registry: IntegrationRegistry = request.app.state.registry
        context = active_registry.run_context()
        try:
            results = await active_registry.verify_all(context)
        finally:
            await context.aclose()
        return [
            {"id": item.integration, "status": item.status, "message": item.detail}
            for item in results
        ]

    @app.get("/v1/schedules")
    async def list_schedules(
        request: Request, x_api_key: str | None = Header(default=None)
    ) -> list[dict[str, Any]]:
        await _authorize(request, x_api_key, "viewer")
        return await _manager(request).storage.list_schedules()

    @app.post("/v1/schedules", status_code=201)
    async def create_schedule(
        request: Request, x_api_key: str | None = Header(default=None)
    ) -> dict[str, Any]:
        await _authorize(request, x_api_key, "admin")
        _, payload = await _bounded_json_object(request)
        try:
            body = CreateScheduleRequest.model_validate(payload)
            definition = validate_schedule(body.definition)
        except (ValueError, ValidationError) as exc:
            raise HTTPException(422, str(exc)) from exc
        storage = _manager(request).storage
        if not await storage.create_schedule(body.id, definition, enabled=body.enabled):
            raise HTTPException(409, "schedule already exists")
        scheduler = request.app.state.scheduler
        if scheduler is not None:
            await scheduler.sync_schedule(body.id)
        return {"id": body.id, "definition": definition, "enabled": body.enabled}

    @app.get("/v1/schedules/{schedule_id}")
    async def get_schedule(
        request: Request, schedule_id: str, x_api_key: str | None = Header(default=None)
    ) -> dict[str, Any]:
        await _authorize(request, x_api_key, "viewer")
        row = await _manager(request).storage.get_schedule(schedule_id)
        if row is None:
            raise HTTPException(404, "schedule not found")
        return row

    @app.put("/v1/schedules/{schedule_id}")
    async def update_schedule(
        request: Request,
        schedule_id: str,
        x_api_key: str | None = Header(default=None),
    ) -> dict[str, Any]:
        await _authorize(request, x_api_key, "admin")
        storage = _manager(request).storage
        _, payload = await _bounded_json_object(request)
        try:
            body = UpdateScheduleRequest.model_validate(payload)
            definition = validate_schedule(body.definition)
        except (ValueError, ValidationError) as exc:
            raise HTTPException(422, str(exc)) from exc
        if not await storage.update_schedule(schedule_id, definition, enabled=body.enabled):
            raise HTTPException(404, "schedule not found")
        scheduler = request.app.state.scheduler
        if scheduler is not None:
            await scheduler.sync_schedule(schedule_id)
        return {"id": schedule_id, "definition": definition, "enabled": body.enabled}

    @app.delete("/v1/schedules/{schedule_id}", status_code=204)
    async def delete_schedule(
        request: Request, schedule_id: str, x_api_key: str | None = Header(default=None)
    ) -> Response:
        await _authorize(request, x_api_key, "admin")
        if not await _manager(request).storage.delete_schedule(schedule_id):
            raise HTTPException(404, "schedule not found")
        scheduler = request.app.state.scheduler
        if scheduler is not None:
            await scheduler.sync_schedule(schedule_id)
        return Response(status_code=204)

    @app.post("/v1/schedules/{schedule_id}/run")
    async def trigger_schedule(
        request: Request, schedule_id: str, x_api_key: str | None = Header(default=None)
    ) -> dict[str, str]:
        await _authorize(request, x_api_key, "operator")
        scheduler = request.app.state.scheduler
        if scheduler is None or not scheduler.is_leader:
            raise HTTPException(503, "scheduler leader unavailable")
        row = await _manager(request).storage.get_schedule(schedule_id)
        if row is None or not row["enabled"]:
            raise HTTPException(404, "enabled schedule not found")
        await scheduler.run_schedule(schedule_id)
        return {"id": schedule_id, "status": "completed"}

    @app.post("/v1/gateway/slack")
    async def slack_event(request: Request) -> Response:
        gateway = request.app.state.slack_gateway
        if gateway is None:
            raise HTTPException(503, "Slack gateway is not configured")
        return cast(Response, await gateway.handle(request))

    @app.post("/v1/gateway/telegram")
    async def telegram_event(
        request: Request,
        x_telegram_bot_api_secret_token: str | None = Header(default=None),
    ) -> Response:
        gateway = request.app.state.telegram_gateway
        if gateway is None:
            raise HTTPException(503, "Telegram gateway is not configured")
        _, update = await _bounded_json_object(request)
        status = await gateway.handle(update, x_telegram_bot_api_secret_token)
        return Response(status_code=status)

    static_root = (ui_dir if ui_dir is not None else Path(__file__).parent / "static").resolve()
    app.include_router(
        reference_app_router(
            include_root=ui_dir is None and not static_root.is_dir(),
            include_health=False,
        )
    )
    if static_root.is_dir():

        @app.get("/{path:path}", include_in_schema=False)
        async def web_console(path: str) -> FileResponse:
            target = (static_root / path).resolve()
            if not target.is_relative_to(static_root):
                raise HTTPException(404, "not found")
            first = path.split("/", 1)[0]
            if first in {"v1", "health", "ready", "docs", "openapi.json", "redoc"}:
                raise HTTPException(404, "not found")
            if target.is_file():
                headers = (
                    {"Cache-Control": "public, max-age=31536000, immutable"}
                    if first == "assets"
                    else {"Cache-Control": "no-cache"}
                    if target == static_root / "index.html"
                    else None
                )
                return FileResponse(target, headers=headers)
            if first not in {
                "",
                "analyses",
                "chat",
                "orders",
                "portfolio",
                "backtests",
                "schedules",
                "integrations",
            }:
                raise HTTPException(404, "not found")
            index = static_root / "index.html"
            if not index.is_file() or Path(path).suffix:
                raise HTTPException(404, "not found")
            return FileResponse(index, headers={"Cache-Control": "no-cache"})

    return app


def _check_approval_ids(payload: dict[str, Any], pending: list[Any]) -> None:
    if "approval_ids" not in payload:
        return
    ids = payload["approval_ids"]
    if not isinstance(ids, list) or any(not isinstance(item, str) for item in ids):
        raise HTTPException(422, "invalid approval ids")
    if ids != [item.id for item in pending]:
        raise HTTPException(409, "approval was resolved or replaced; reload the thread")


def _public_messages(messages: Any) -> list[dict[str, str]]:
    return [
        {
            "role": "user" if isinstance(message, HumanMessage) else "assistant",
            "text": redact_secrets(str(message.text)),
        }
        for message in messages
        if isinstance(message, HumanMessage)
        or (isinstance(message, AIMessage) and not message.tool_calls)
    ]


def _manager(request: Request) -> RunManager:
    return request.app.state.manager  # type: ignore[no-any-return]


def _chat_graph(request: Request) -> Any:
    graph = request.app.state.chat_graph
    if graph is None:
        raise HTTPException(503, "chat model unavailable")
    return graph


def _order_graph(request: Request) -> Any:
    graph = request.app.state.order_graph
    if graph is None:
        raise HTTPException(503, "paper orders unavailable")
    return graph


def _paper(request: Request) -> PaperBroker:
    broker = request.app.state.paper_broker
    if not isinstance(broker, PaperBroker):
        raise HTTPException(503, "paper broker unavailable")
    return broker


def _graph_config(kind: Literal["chat", "orders"], thread_id: str) -> dict[str, Any]:
    return {"configurable": {"thread_id": f"{kind}:{thread_id}"}}


def _order_view(thread_id: str, values: dict[str, Any], state: Any = None) -> dict[str, Any]:
    """An order thread for the API: the outcome, its preview and fill, and what waits."""
    items = (
        [item for task in state.tasks for item in task.interrupts]
        if state is not None
        else list(values.get("__interrupt__", ()))
    )
    return {
        "thread_id": thread_id,
        "status": "awaiting_approval" if items else "completed",
        "outcome": values.get("outcome"),
        "order": redact_value(values.get("order")),
        "preview": redact_value(values.get("preview")),
        "execution": redact_value(values.get("execution")),
        "denial": values.get("denial") or None,
        "requests": redact_value([item.value for item in items]),
        "approval_ids": [item.id for item in items],
    }


async def _run_order(app: FastAPI, thread_id: str, value: dict[str, Any]) -> dict[str, Any]:
    """Start an order thread under its lock; notify the gateways when it waits for a human."""
    graph = app.state.order_graph
    if graph is None:
        raise HTTPException(503, "paper orders unavailable")
    manager: RunManager = app.state.manager
    locks: MutableMapping[str, asyncio.Lock] = app.state.order_locks
    lock = locks.setdefault(thread_id, asyncio.Lock())
    async with lock, manager.storage.approval_lock("orders", thread_id):
        await manager.storage.touch_checkpoint_thread(thread_id, "orders")
        context = manager.context_factory()
        async with close_run_context(context):
            try:
                result: dict[str, Any] = await graph.ainvoke(
                    value, _graph_config("orders", thread_id), context=context
                )
            except (ValueError, ValidationError) as exc:
                raise HTTPException(422, "invalid order") from exc
            except IntegrationNotConfiguredError as exc:
                raise HTTPException(503, "paper broker unavailable") from exc
    pending = result.get("__interrupt__", ())
    if pending:
        await _notify_gateways(app, "orders", thread_id, pending)
    return result


async def _start_order_thread(
    app: FastAPI, order: ProposedOrder, analysis_thread: str
) -> tuple[str, str] | None:
    """The order thread and approval id for an analysis' single proposed order, if it waits."""
    order_thread = str(uuid5(NAMESPACE_URL, f"{analysis_thread}:{order.client_order_id}"))
    try:
        result = await _run_order(app, order_thread, {"order": order.model_dump(mode="json")})
    except HTTPException:
        return None
    pending = result.get("__interrupt__", ())
    return (order_thread, pending[0].id) if len(pending) == 1 else None


def _approval_records(pending: list[Any], decisions: list[Any]) -> list[tuple[str, str, str]]:
    requests = [
        action
        for item in pending
        if isinstance(item.value, dict)
        for action in item.value.get("action_requests", [])
    ]
    if len(requests) != len(decisions):
        raise HTTPException(422, "decision count does not match pending actions")
    records: list[tuple[str, str, str]] = []
    for action, decision in zip(requests, decisions, strict=True):
        if not isinstance(action, dict) or not isinstance(decision, dict):
            raise HTTPException(422, "invalid approval action")
        name = action.get("name")
        args = action.get("args", action.get("arguments"))
        if not isinstance(name, str) or not isinstance(args, dict):
            raise HTTPException(422, "invalid approval action")
        if decision["type"] == "edit":
            edited = decision.get("edited_action")
            if not isinstance(edited, dict) or edited.get("name") != name:
                raise HTTPException(422, "edited action must keep the same tool")
            args = edited.get("args")
            if not isinstance(args, dict):
                raise HTTPException(422, "edited action arguments must be an object")
        digest = hashlib.sha256(json.dumps(args, sort_keys=True).encode()).hexdigest()
        records.append((decision["type"], name, digest))
    return records


async def _gateway_decide(
    app: FastAPI, kind: str, thread_id: str, choice: str, actor: str, approval_id: str
) -> str:
    """Resume the same checkpoint under the same lock and audit as the API."""
    if kind not in {"chat", "orders"} or choice not in {"approve", "reject"}:
        return "Invalid approval request."
    if not 1 <= len(thread_id) <= 64 or not actor.startswith(("slack:", "telegram:")):
        return "Invalid approval request."
    if not app.state.approval_policy.allows_approver(actor, "approver"):
        return "Not authorized to approve."
    graph = app.state.chat_graph if kind == "chat" else app.state.order_graph
    if graph is None:
        return "Approval service unavailable."
    locks: MutableMapping[str, asyncio.Lock] = (
        app.state.chat_locks if kind == "chat" else app.state.order_locks
    )
    lock = locks.setdefault(thread_id, asyncio.Lock())
    config = _graph_config(cast(Literal["chat", "orders"], kind), thread_id)
    async with lock, app.state.manager.storage.approval_lock(kind, thread_id):
        state = await graph.aget_state(config)
        pending = [item for task in state.tasks for item in task.interrupts]
        if not pending:
            return "Approval already resolved."
        if len(pending) != 1 or not approval_id or pending[0].id != approval_id:
            return "Approval already resolved or replaced; use the current request."
        decisions = [{"type": choice}]
        try:
            records = _approval_records(pending, decisions)
        except HTTPException:
            return "Use the API or CLI for this multi-action approval."
        context = app.state.manager.context_factory()
        if not isinstance(context, RunContext):
            return "Approval service unavailable."
        context = replace(context, actor=actor, role="approver")
        async with close_run_context(context):
            await app.state.manager.storage.touch_checkpoint_thread(thread_id, kind)
            for decision, action_name, args_digest in records:
                await app.state.manager.storage.record_approval(
                    thread_id, kind, actor, decision, action_name, args_digest
                )
            try:
                result = await graph.ainvoke(
                    Command(resume={"decisions": decisions}), config, context=context
                )
            except Exception:
                return "Approval could not be completed; inspect the thread in the API."
        if result.get("__interrupt__"):
            await _notify_gateways(app, kind, thread_id, result["__interrupt__"])
            return "Decision recorded; another approval is pending."
        denial = result.get("denial")
        if denial:
            return f"Decisión rechazada: {denial}"
        return "Rechazada." if choice == "reject" else f"Aprobada: {result.get('outcome', '')}."


async def _notify_gateways(app: FastAPI, kind: str, thread_id: str, pending: Any) -> None:
    """Publish a single pending request; multi-action decisions stay in API/CLI."""
    pending = list(pending)
    if len(pending) != 1:
        return
    requests = [
        action
        for item in pending
        if isinstance(item.value, dict)
        for action in item.value.get("action_requests", [])
    ]
    if len(requests) != 1 or not isinstance(requests[0], dict):
        return
    action = requests[0]
    name = action.get("name", "action")
    arguments = action.get("arguments", action.get("args", {}))
    summary = redact_secrets(
        f"Approval requested for {name}: {json.dumps(redact_value(arguments), ensure_ascii=False)}"
    )[:3000]
    for gateway in (app.state.slack_gateway, app.state.telegram_gateway):
        if gateway is not None:
            try:
                await gateway.publish_approval(kind, thread_id, summary, pending[0].id)
            except Exception:
                _LOGGER.warning("approval notification failed")


def _sse(kind: str, data: dict[str, Any]) -> str:
    return f"event: {kind}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def _bounded_body(request: Request) -> bytes:
    chunks = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > _MAX_BODY:
            raise HTTPException(413, "request exceeds 1 MiB")
        chunks.append(chunk)
    return b"".join(chunks)


async def _bounded_json_object(request: Request) -> tuple[bytes, dict[str, Any]]:
    raw = await _bounded_body(request)
    try:
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("JSON object required")
        return raw, payload
    except (ValueError, UnicodeError) as exc:
        raise HTTPException(400, "invalid JSON object") from exc


async def _authorize(request: Request, key: str | None, minimum: str) -> str:
    if not key:
        raise HTTPException(401, "API key required")
    role = await _manager(request).storage.key_role(key)
    if role is None:
        raise HTTPException(401, "invalid API key")
    if _ROLE_LEVEL[role] < _ROLE_LEVEL[minimum]:
        raise HTTPException(403, "insufficient role")
    return role


def _verifier_from_config(config: ServerConfig) -> WebhookVerifier:
    return WebhookVerifier(
        tokens={key: value.get_secret_value() for key, value in config.webhook_tokens.items()},
        hmac_secrets={
            key: value.get_secret_value() for key, value in config.webhook_hmac_secrets.items()
        },
    )


async def _retention_loop(
    manager: RunManager,
    saver: Any,
    config: ServerConfig,
    chat_locks: MutableMapping[str, asyncio.Lock] | None = None,
    order_locks: MutableMapping[str, asyncio.Lock] | None = None,
) -> None:
    chat_locks = chat_locks if chat_locks is not None else {}
    order_locks = order_locks if order_locks is not None else {}
    while True:
        await asyncio.sleep(3600)
        try:
            cutoff = datetime.now(UTC) - timedelta(days=config.thread_retention_days)
            await _retention_cycle(manager, saver, cutoff, chat_locks, order_locks)
        except Exception:
            _LOGGER.warning("retention cycle failed")


async def _retention_cycle(
    manager: RunManager,
    saver: Any,
    cutoff: datetime,
    chat_locks: MutableMapping[str, asyncio.Lock],
    order_locks: MutableMapping[str, asyncio.Lock],
) -> None:
    for thread_id in await manager.storage.expired_threads(cutoff):
        analysis_lock = manager.locks.setdefault(thread_id, asyncio.Lock())
        chat_lock = chat_locks.setdefault(thread_id, asyncio.Lock())
        order_lock = order_locks.setdefault(thread_id, asyncio.Lock())
        async with (
            analysis_lock,
            chat_lock,
            order_lock,
            manager.storage.approval_lock("chat", thread_id),
            manager.storage.approval_lock("orders", thread_id),
        ):
            try:
                if not await manager.storage.thread_expired(thread_id, cutoff):
                    continue
                for checkpoint_id in checkpoint_thread_ids(thread_id):
                    await saver.adelete_thread(checkpoint_id)
                await manager.storage.delete_thread(thread_id, cutoff)
            except Exception:
                _LOGGER.warning("retention failed for one thread")
    await manager.storage.purge_metadata_before(cutoff)
