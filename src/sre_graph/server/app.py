"""FastAPI API for one server process and its persisted LangGraph investigations."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
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

from sre_graph.config.settings import PersistenceBackend, Settings
from sre_graph.config.tracing import disable_external_tracing
from sre_graph.delivery.service import DeliveryService
from sre_graph.delivery.settings import DeliveryDestinations
from sre_graph.domain.alerts.model import AlertParseError, AlertSource
from sre_graph.domain.alerts.parsing import detect_source, parse_alert
from sre_graph.domain.rendering.slack import render_slack
from sre_graph.domain.rendering.telegram import render_telegram
from sre_graph.domain.report import Report
from sre_graph.gateway import GatewayResult, GatewayRunPending
from sre_graph.gateway.settings import SlackGatewaySettings, TelegramGatewaySettings
from sre_graph.gateway.slack import SlackGateway
from sre_graph.gateway.telegram import TelegramGateway
from sre_graph.graphs.chat.graph import make_chat_graph
from sre_graph.graphs.remediation.actions import default_action_catalog
from sre_graph.graphs.remediation.graph import (
    ActionCatalog,
    UncertainActionOutcome,
    make_remediation_graph,
)
from sre_graph.harness.runtime import _services, make_investigation_graph
from sre_graph.integrations.base import IntegrationError, IntegrationNotConfiguredError
from sre_graph.integrations.catalog import default_registry
from sre_graph.integrations.context import RunContext
from sre_graph.integrations.registry import IntegrationRegistry
from sre_graph.llm.models import ModelRole, get_model
from sre_graph.memory.service import IncidentMemory
from sre_graph.persistence.checkpointer import open_checkpointer
from sre_graph.persistence.store import open_store
from sre_graph.safety.approval import ApprovalPolicy
from sre_graph.safety.secrets import redact_secrets, redact_value
from sre_graph.scheduler.delivery import send_digest_text
from sre_graph.scheduler.expiry import ApprovalGraph, expire_pending_approvals
from sre_graph.scheduler.jobs import HealthJobs
from sre_graph.scheduler.service import SchedulerService, validate_schedule
from sre_graph.server.config import ServerConfig
from sre_graph.server.events import EventListener
from sre_graph.server.explain import MAX_EXPLAIN_EVENTS, build_explanation
from sre_graph.server.manager import RunManager, RunOverloaded
from sre_graph.server.migrate import upgrade
from sre_graph.server.security import WebhookVerifier
from sre_graph.server.storage import Storage

_MAX_BODY = 1024 * 1024
_ROLE_LEVEL = {"viewer": 0, "operator": 1, "approver": 2, "admin": 3}
_LOGGER = logging.getLogger(__name__)


class InvestigationRequest(BaseModel):
    text: str | None = Field(default=None, min_length=1, max_length=_MAX_BODY)
    alert: dict[str, Any] | None = None
    source: AlertSource | None = None
    destinations: DeliveryDestinations = Field(default_factory=dict)


class CreateKeyRequest(BaseModel):
    role: Literal["viewer", "operator", "approver", "admin"]


class ChatMessageRequest(BaseModel):
    message: str = Field(min_length=1, max_length=16000)


class RemediationRequest(BaseModel):
    tool: str = Field(min_length=1, max_length=100)
    args: dict[str, Any]


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
    remediation_graph: Any | None = None,
    approval_policy: ApprovalPolicy | None = None,
    scheduler_service: Any | None = None,
    slack_gateway: Any | None = None,
    telegram_gateway: Any | None = None,
    action_catalog: ActionCatalog | None = None,
    ui_dir: Path | None = None,
) -> FastAPI:
    """Create a production Postgres server or an injected offline ASGI test app."""
    disable_external_tracing()
    options = config or ServerConfig()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if manager is not None:
            app.state.action_catalog = action_catalog or default_action_catalog(
                ledger=manager.storage
            )
            app.state.manager = manager
            app.state.registry = registry or IntegrationRegistry([], env_file=None)
            app.state.verifier = verifier or WebhookVerifier()
            app.state.chat_graph = chat_graph
            app.state.remediation_graph = remediation_graph
            app.state.approval_policy = approval_policy or ApprovalPolicy.from_settings(Settings())
            app.state.scheduler = scheduler_service
            app.state.slack_gateway = slack_gateway
            app.state.telegram_gateway = telegram_gateway
            app.state.events = EventListener()
            await app.state.events.start()
            try:
                await manager.start()
                yield
            finally:
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
            settings = Settings(
                persistence_backend=PersistenceBackend.POSTGRES,
                database_url=options.database_url,
            )
            await storage.put_key(bootstrap.get_secret_value(), "admin")
            await listener.start()
            saver = await stack.enter_async_context(open_checkpointer(settings))
            store = await stack.enter_async_context(open_store(settings))
            memory = IncidentMemory(store)
            actions = action_catalog or default_action_catalog(ledger=storage)
            app.state.action_catalog = actions
            active_policy = approval_policy or ApprovalPolicy.from_settings(settings)
            active_registry = registry or default_registry()
            if registry is None:
                from sre_graph.integrations.mcp.runtime import with_configured_mcp

                active_registry = await with_configured_mcp(active_registry)
            delivery = DeliveryService.from_env(ledger=storage)

            def context_factory() -> RunContext:
                return delivery.extend_run_context(active_registry.run_context())

            investigation_services = _services(settings)
            graph = make_investigation_graph(
                investigation_services,
                active_registry,
                checkpointer=saver,
                memory=memory,
                delivery=delivery,
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
                    chat_investigation = make_investigation_graph(
                        investigation_services,
                        active_registry,
                        memory=memory,
                        checkpointer=False,  # Disable the inherited chat saver.
                    )
                    active_chat = make_chat_graph(
                        model,
                        active_registry,
                        context_factory(),
                        memory,
                        actions,
                        policy=active_policy,
                        investigation=chat_investigation,
                        checkpointer=saver,
                        store=store,
                        settings=settings,
                    )
            active_remediation = remediation_graph or make_remediation_graph(
                actions,
                active_policy,
                checkpointer=saver,
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
            app.state.remediation_graph = active_remediation
            app.state.approval_policy = active_policy
            stack.push_async_callback(active_manager.stop)
            await active_manager.start()
            health_jobs = HealthJobs(
                storage,
                active_manager,
                context_factory,
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
                    app.state.remediation_locks,
                )

            async def run_expiry() -> None:
                await expire_pending_approvals(
                    storage=storage,
                    chat_graph=active_chat,
                    remediation_graph=cast(ApprovalGraph, active_remediation),
                    chat_locks=app.state.chat_locks,
                    remediation_locks=app.state.remediation_locks,
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
                {"health_check": health_jobs.health_check, "digest": health_jobs.digest},
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

            async def submit_gateway_alert(
                text: str, platform: str, destination: str, remote_id: str, event_id: str
            ) -> GatewayResult:
                thread_id = str(uuid5(NAMESPACE_URL, f"sre-graph:gateway:{platform}:{event_id}"))
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
                    return GatewayResult(thread_id, "Investigation failed.")
                snapshot = await graph.aget_state({"configurable": {"thread_id": thread_id}})
                report_data = snapshot.values.get("report") if snapshot.values else None
                if not report_data:
                    return GatewayResult(thread_id, "No report was produced.")
                report = Report.model_validate(report_data)
                if platform == "slack":
                    payload = cast(dict[str, Any], redact_value(render_slack(report)))
                    return GatewayResult(
                        thread_id,
                        str(payload["text"]),
                        report_blocks=cast(list[dict[str, Any]], payload["blocks"]),
                    )
                if platform == "telegram":
                    return GatewayResult(thread_id, redact_secrets(render_telegram(report)))
                raise ValueError("unsupported gateway platform")

            async def gateway_decide(
                kind: str, thread_id: str, choice: str, actor: str, approval_id: str
            ) -> str:
                return await _gateway_decide(app, kind, thread_id, choice, actor, approval_id)

            slack_options = SlackGatewaySettings()
            telegram_options = TelegramGatewaySettings()
            app.state.slack_gateway = slack_gateway or (
                SlackGateway(slack_options, storage, submit_gateway_alert, gateway_decide)
                if slack_options.enabled
                else None
            )
            if app.state.slack_gateway is not None:
                stack.push_async_callback(app.state.slack_gateway.close)
            app.state.telegram_gateway = telegram_gateway or (
                TelegramGateway(telegram_options, storage, submit_gateway_alert, gateway_decide)
                if telegram_options.enabled
                else None
            )
            if app.state.telegram_gateway is not None:
                stack.push_async_callback(app.state.telegram_gateway.close)
            await active_scheduler.start()
            if isinstance(app.state.slack_gateway, SlackGateway):
                await app.state.slack_gateway.start_socket_mode()
            yield

    app = FastAPI(title="sre-graph", lifespan=lifespan)
    from sre_graph.server.http_guards import install_http_guards

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
    app.state.remediation_locks = WeakValueDictionary()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(options.cors_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["X-API-Key", "Last-Event-ID", "Content-Type"],
    )

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

    @app.post("/v1/investigations", status_code=202)
    async def investigate(
        request: Request, x_api_key: str | None = Header(default=None)
    ) -> dict[str, Any]:
        await _authorize(request, x_api_key, "operator")
        _, payload = await _bounded_json_object(request)
        try:
            body = InvestigationRequest.model_validate(payload)
        except ValidationError as exc:
            raise HTTPException(422, "invalid investigation request") from exc
        if (body.text is None) == (body.alert is None):
            raise HTTPException(422, "provide exactly one of text or alert")
        raw: str | dict[str, Any] = body.text if body.text is not None else body.alert or {}
        try:
            parse_alert(raw, source_hint=body.source.value if body.source else None)
        except AlertParseError as exc:
            raise HTTPException(422, "invalid alert") from exc
        return await _manager(request).submit(
            raw, body.source.value if body.source else None, destinations=body.destinations
        )

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
                    async for _, snapshot in graph.astream(
                        {"messages": [HumanMessage(body.message)]},
                        config=config,
                        context=_manager(request).context_factory(),
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
            except UncertainActionOutcome as exc:
                raise HTTPException(409, "action outcome is uncertain; reconcile manually") from exc
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

    @app.post("/v1/remediations", status_code=202)
    async def start_remediation(
        request: Request, x_api_key: str | None = Header(default=None)
    ) -> dict[str, Any]:
        await _authorize(request, x_api_key, "operator")
        graph = _remediation_graph(request)
        _, payload = await _bounded_json_object(request)
        try:
            body = RemediationRequest.model_validate(payload)
        except ValidationError as exc:
            raise HTTPException(422, "invalid remediation request") from exc
        thread_id = str(uuid4())
        config = _graph_config("remediation", thread_id)
        locks: MutableMapping[str, asyncio.Lock] = request.app.state.remediation_locks
        lock = locks.setdefault(thread_id, asyncio.Lock())
        async with lock, _manager(request).storage.approval_lock("remediation", thread_id):
            await _manager(request).storage.touch_checkpoint_thread(thread_id, "remediation")
            try:
                result = await graph.ainvoke(
                    {"tool": body.tool, "args": body.args, "action_id": thread_id},
                    config,
                    context=_manager(request).context_factory(),
                )
            except (ValueError, ValidationError) as exc:
                raise HTTPException(422, "invalid action or arguments") from exc
            except IntegrationNotConfiguredError as exc:
                raise HTTPException(503, "action integration unavailable") from exc
            except IntegrationError as exc:
                raise HTTPException(502, "action preparation failed") from exc
        pending = result.get("__interrupt__", ())
        if pending:
            await _notify_gateways(request.app, "remediation", thread_id, pending)
        return {
            "thread_id": thread_id,
            "status": "awaiting_approval" if pending else "completed",
            "requests": redact_value([item.value for item in pending]),
            "approval_ids": [item.id for item in pending],
            "outcome": result.get("outcome"),
        }

    @app.get("/v1/remediations/actions")
    async def remediation_actions(
        request: Request, x_api_key: str | None = Header(default=None)
    ) -> list[dict[str, Any]]:
        await _authorize(request, x_api_key, "operator")
        catalog: ActionCatalog = request.app.state.action_catalog
        return [
            {
                "name": action.name,
                "schema": action.args_schema.model_json_schema(),
                "rollback": redact_secrets(action.rollback),
            }
            for action in catalog.actions.values()
        ]

    @app.get("/v1/remediations/{thread_id}")
    async def remediation_status(
        request: Request, thread_id: str, x_api_key: str | None = Header(default=None)
    ) -> dict[str, Any]:
        await _authorize(request, x_api_key, "viewer")
        state = await _remediation_graph(request).aget_state(
            _graph_config("remediation", thread_id)
        )
        if not state.values:
            raise HTTPException(404, "remediation thread not found")
        pending_items = [item for task in state.tasks for item in task.interrupts]
        pending = [item.value for item in pending_items]
        return {
            "thread_id": thread_id,
            "status": "awaiting_approval" if pending else "completed",
            "outcome": redact_value(state.values.get("outcome")),
            "result": redact_value(state.values.get("result")),
            "rollback": redact_value(state.values.get("rollback")),
            "requests": redact_value(pending),
            "approval_ids": [item.id for item in pending_items],
        }

    @app.post("/v1/remediations/{thread_id}/approve")
    async def approve_remediation(
        request: Request, thread_id: str, x_api_key: str | None = Header(default=None)
    ) -> dict[str, Any]:
        role = await _authorize(request, x_api_key, "approver")
        graph = _remediation_graph(request)
        _, payload = await _bounded_json_object(request)
        decisions = payload.get("decisions")
        if (
            not isinstance(decisions, list)
            or len(decisions) != 1
            or not isinstance(decisions[0], dict)
            or decisions[0].get("type") not in {"approve", "edit", "reject"}
        ):
            raise HTTPException(422, "one valid decision is required")
        locks: MutableMapping[str, asyncio.Lock] = request.app.state.remediation_locks
        lock = locks.setdefault(thread_id, asyncio.Lock())
        config = _graph_config("remediation", thread_id)
        async with lock, _manager(request).storage.approval_lock("remediation", thread_id):
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
            await _manager(request).storage.touch_checkpoint_thread(thread_id, "remediation")
            for decision, action_name, args_digest in approval_records:
                await _manager(request).storage.record_approval(
                    thread_id, "remediation", actor, decision, action_name, args_digest
                )
            try:
                result = await graph.ainvoke(
                    Command(resume={"decisions": decisions}), config, context=context
                )
            except (ValueError, ValidationError, PermissionError) as exc:
                raise HTTPException(422, type(exc).__name__) from exc
            except IntegrationNotConfiguredError as exc:
                raise HTTPException(503, "action integration unavailable") from exc
            except IntegrationError as exc:
                raise HTTPException(502, "action integration failed") from exc
            except UncertainActionOutcome as exc:
                raise HTTPException(409, "action outcome is uncertain; reconcile manually") from exc
            pending_after = result.get("__interrupt__", ())
            if pending_after:
                await _notify_gateways(request.app, "remediation", thread_id, pending_after)
            return {
                "thread_id": thread_id,
                "status": "awaiting_approval" if pending_after else "completed",
                "outcome": result.get("outcome"),
                "result": redact_value(result.get("result")),
                "rollback": redact_value(result.get("rollback")),
                "requests": redact_value([item.value for item in pending_after]),
                "approval_ids": [item.id for item in pending_after],
            }

    @app.get("/v1/investigations/{thread_id}/explain")
    async def explain_investigation(
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

    @app.get("/v1/investigations/{thread_id}")
    async def investigation(
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
                    "alert": values.get("alert") or None,
                    "harness": values.get("harness") or None,
                    **{
                        key: values.get(key)
                        for key in (
                            "plan",
                            "findings",
                            "evidence",
                            "incident_window",
                            "diagnosis",
                            "verification",
                            "actions",
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

    @app.post("/v1/alerts/{source}", status_code=202, response_model=None)
    async def alert(request: Request, source: AlertSource) -> dict[str, Any] | JSONResponse:
        raw = await _bounded_body(request)
        active_verifier: WebhookVerifier = request.app.state.verifier
        header_ok = active_verifier.header_authenticated(source, raw, request.headers)
        if not header_ok and not (
            source is AlertSource.CLOUDWATCH and active_verifier.sns_available
        ):
            raise HTTPException(401, "webhook authentication failed")
        try:
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError("alert must be an object")
        except (ValueError, UnicodeError) as exc:
            raise HTTPException(400 if header_ok else 401, "invalid JSON alert") from exc
        try:
            verified = active_verifier.verify(source, raw, request.headers, payload)
        except ValueError as exc:
            raise HTTPException(401, "webhook authentication failed") from exc
        try:
            detected = detect_source(verified)
            if detected is not AlertSource.GENERIC and detected is not source:
                raise AlertParseError("source mismatch")
            normalized = parse_alert(verified, source_hint=source.value)
            if normalized.source is not source:
                raise AlertParseError("source mismatch")
        except AlertParseError as exc:
            raise HTTPException(422, "alert does not match source") from exc
        active = _manager(request)
        thread_id = str(uuid4())
        run_id = str(uuid4())
        with active.reserve() as reservation:
            claimed, prior = await active.storage.claim_webhook_run(
                source.value,
                normalized.fingerprint,
                thread_id,
                run_id,
                datetime.now(UTC),
                timedelta(minutes=options.alert_dedupe_minutes),
            )
            if not claimed:
                return JSONResponse({"thread_id": prior, "deduplicated": True})
            try:
                result = active.schedule_existing(
                    run_id,
                    thread_id,
                    verified,
                    source.value,
                    destinations=options.alert_delivery_routes.get(
                        normalized.severity.value, options.alert_delivery_routes.get("default", {})
                    ),
                    user_requested=False,
                    reservation=reservation,
                )
            except (Exception, asyncio.CancelledError):
                await active.storage.set_status(run_id, "failed", "schedule_failed")
                await active.storage.release_fingerprint(
                    source.value, normalized.fingerprint, thread_id
                )
                raise

        return {**result, "deduplicated": False}

    @app.get("/v1/integrations")
    async def integrations(
        request: Request, x_api_key: str | None = Header(default=None)
    ) -> list[dict[str, Any]]:
        await _authorize(request, x_api_key, "viewer")
        active_registry: IntegrationRegistry = request.app.state.registry
        context = active_registry.run_context()
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
        results = await active_registry.verify_all(active_registry.run_context())
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
                return FileResponse(target)
            if first not in {
                "",
                "investigations",
                "chat",
                "remediations",
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


def _remediation_graph(request: Request) -> Any:
    graph = request.app.state.remediation_graph
    if graph is None:
        raise HTTPException(503, "remediation unavailable")
    return graph


def _graph_config(kind: Literal["chat", "remediation"], thread_id: str) -> dict[str, Any]:
    return {"configurable": {"thread_id": f"{kind}:{thread_id}"}}


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
        if name == "execute_remediation":
            action_name = args.get("name")
            action_args = args.get("action_args")
            if not isinstance(action_name, str) or not isinstance(action_args, dict):
                raise HTTPException(422, "invalid remediation action")
            name, args = action_name, action_args
        digest = hashlib.sha256(json.dumps(args, sort_keys=True).encode()).hexdigest()
        records.append((decision["type"], name, digest))
    return records


async def _gateway_decide(
    app: FastAPI, kind: str, thread_id: str, choice: str, actor: str, approval_id: str
) -> str:
    """Resume the same checkpoint under the same lock and audit as the API."""
    if kind not in {"chat", "remediation"} or choice not in {"approve", "reject"}:
        return "Invalid approval request."
    if not 1 <= len(thread_id) <= 64 or not actor.startswith(("slack:", "telegram:")):
        return "Invalid approval request."
    if not app.state.approval_policy.allows_approver(actor, "approver"):
        return "Not authorized to approve."
    graph = app.state.chat_graph if kind == "chat" else app.state.remediation_graph
    if graph is None:
        return "Approval service unavailable."
    locks: MutableMapping[str, asyncio.Lock] = (
        app.state.chat_locks if kind == "chat" else app.state.remediation_locks
    )
    lock = locks.setdefault(thread_id, asyncio.Lock())
    config = _graph_config(cast(Literal["chat", "remediation"], kind), thread_id)
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
        await app.state.manager.storage.touch_checkpoint_thread(thread_id, kind)
        for decision, action_name, args_digest in records:
            await app.state.manager.storage.record_approval(
                thread_id, kind, actor, decision, action_name, args_digest
            )
        try:
            result = await graph.ainvoke(
                Command(resume={"decisions": decisions}), config, context=context
            )
        except UncertainActionOutcome:
            return "Action outcome is uncertain; reconcile manually."
        except Exception:
            return "Approval could not be completed; inspect the thread in the API."
        if result.get("__interrupt__"):
            await _notify_gateways(app, kind, thread_id, result["__interrupt__"])
            return "Decision recorded; another approval is pending."
        return "Rejected." if choice == "reject" else "Approved."


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
    public_key = (
        Path(config.sns_public_key_path).read_bytes() if config.sns_public_key_path else None
    )
    return WebhookVerifier(
        tokens={key: value.get_secret_value() for key, value in config.webhook_tokens.items()},
        hmac_secrets={
            key: value.get_secret_value() for key, value in config.webhook_hmac_secrets.items()
        },
        sns_topic_arn=config.sns_topic_arn,
        sns_cert_url=config.sns_cert_url,
        sns_public_key_pem=public_key,
    )


async def _retention_loop(
    manager: RunManager,
    saver: Any,
    config: ServerConfig,
    chat_locks: MutableMapping[str, asyncio.Lock] | None = None,
    remediation_locks: MutableMapping[str, asyncio.Lock] | None = None,
) -> None:
    chat_locks = chat_locks if chat_locks is not None else {}
    remediation_locks = remediation_locks if remediation_locks is not None else {}
    while True:
        await asyncio.sleep(3600)
        try:
            cutoff = datetime.now(UTC) - timedelta(days=config.thread_retention_days)
            await _retention_cycle(manager, saver, cutoff, chat_locks, remediation_locks)
        except Exception:
            _LOGGER.warning("retention cycle failed")


async def _retention_cycle(
    manager: RunManager,
    saver: Any,
    cutoff: datetime,
    chat_locks: MutableMapping[str, asyncio.Lock],
    remediation_locks: MutableMapping[str, asyncio.Lock],
) -> None:
    for thread_id in await manager.storage.expired_threads(cutoff):
        investigation_lock = manager.locks.setdefault(thread_id, asyncio.Lock())
        chat_lock = chat_locks.setdefault(thread_id, asyncio.Lock())
        remediation_lock = remediation_locks.setdefault(thread_id, asyncio.Lock())
        async with (
            investigation_lock,
            chat_lock,
            remediation_lock,
            manager.storage.approval_lock("chat", thread_id),
            manager.storage.approval_lock("remediation", thread_id),
        ):
            try:
                if thread_id not in await manager.storage.expired_threads(cutoff):
                    continue
                for checkpoint_id in (
                    thread_id,
                    f"chat:{thread_id}",
                    f"remediation:{thread_id}",
                ):
                    await saver.adelete_thread(checkpoint_id)
                await manager.storage.delete_thread(thread_id, cutoff)
            except Exception:
                _LOGGER.warning("retention failed for one thread")
    await manager.storage.purge_metadata_before(cutoff)
