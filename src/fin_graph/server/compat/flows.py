"""Legacy visual-flow CRUD and run routes backed by immutable revisions."""

from __future__ import annotations

from typing import Any, cast

from fastapi import APIRouter, HTTPException, Query, Request

from fin_graph.compat.ai_hedge_fund.schemas import (
    FlowCreateRequest,
    FlowResponse,
    FlowRunCreateRequest,
    FlowRunResponse,
    FlowRunStatus,
    FlowRunSummaryResponse,
    FlowRunUpdateRequest,
    FlowSummaryResponse,
    FlowUpdateRequest,
)
from fin_graph.compat.contracts import JsonObject
from fin_graph.compat.storage import (
    CompatibilityStore,
    ConcurrentUpdateError,
    DuplicateNameError,
    FlowRecord,
    FlowRevision,
    FlowRunRecord,
    RecordNotFoundError,
    RunStatus,
)
from fin_graph.flows import FlowCompileError, FlowService
from fin_graph.flows.service import public_id

router = APIRouter(prefix="/flows")


def _service(request: Request) -> FlowService:
    existing = getattr(request.app.state, "compatibility_flow_service", None)
    if isinstance(existing, FlowService):
        return existing
    store = getattr(request.app.state, "compatibility_store", None)
    if not isinstance(store, CompatibilityStore):
        raise HTTPException(503, "compatibility store unavailable")
    created = FlowService(store)
    request.app.state.compatibility_flow_service = created
    return created


def _definition(body: FlowCreateRequest) -> JsonObject:
    return cast(
        JsonObject,
        body.model_dump(
            mode="json",
            include={"nodes", "edges", "viewport", "data", "is_template", "tags"},
        ),
    )


def _flow_response(flow: FlowRecord, revision: FlowRevision) -> FlowResponse:
    value = revision.definition
    return FlowResponse(
        id=public_id(flow.id),
        name=flow.name,
        description=flow.description or None,
        nodes=cast(list[dict[str, Any]], value.get("nodes", [])),
        edges=cast(list[dict[str, Any]], value.get("edges", [])),
        viewport=cast(dict[str, Any] | None, value.get("viewport")),
        data=cast(dict[str, Any] | None, value.get("data")),
        is_template=bool(value.get("is_template", False)),
        tags=cast(list[str] | None, value.get("tags")),
        created_at=flow.created_at,
        updated_at=flow.updated_at,
    )


def _flow_summary(flow: FlowRecord, revision: FlowRevision) -> FlowSummaryResponse:
    value = revision.definition
    return FlowSummaryResponse(
        id=public_id(flow.id),
        name=flow.name,
        description=flow.description or None,
        is_template=bool(value.get("is_template", False)),
        tags=cast(list[str] | None, value.get("tags")),
        created_at=flow.created_at,
        updated_at=flow.updated_at,
    )


def _legacy_status(status: RunStatus) -> FlowRunStatus:
    return {
        RunStatus.PENDING: FlowRunStatus.IDLE,
        RunStatus.RUNNING: FlowRunStatus.IN_PROGRESS,
        RunStatus.SUCCEEDED: FlowRunStatus.COMPLETE,
        RunStatus.FAILED: FlowRunStatus.ERROR,
        RunStatus.CANCELLED: FlowRunStatus.ERROR,
    }[status]


async def _run_number(service: FlowService, run: FlowRunRecord) -> int:
    runs = await service.store.list_flow_runs(run.flow_id)
    return next(index for index, item in enumerate(runs, start=1) if item.id == run.id)


async def _run_response(service: FlowService, run: FlowRunRecord) -> FlowRunResponse:
    started = run.created_at if run.status is not RunStatus.PENDING else None
    completed = (
        run.updated_at
        if run.status in {RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED}
        else None
    )
    return FlowRunResponse(
        id=public_id(run.id),
        flow_id=public_id(run.flow_id),
        status=_legacy_status(run.status),
        run_number=await _run_number(service, run),
        created_at=run.created_at,
        updated_at=run.updated_at,
        started_at=started,
        completed_at=completed,
        request_data=run.request or None,
        results=run.result if isinstance(run.result, dict) else None,
        error_message=run.error,
    )


async def _run_summary(service: FlowService, run: FlowRunRecord) -> FlowRunSummaryResponse:
    full = await _run_response(service, run)
    return FlowRunSummaryResponse.model_validate(full.model_dump())


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, RecordNotFoundError):
        return HTTPException(404, "Flow not found")
    if isinstance(exc, DuplicateNameError):
        return HTTPException(409, str(exc))
    if isinstance(exc, ConcurrentUpdateError):
        return HTTPException(409, str(exc))
    if isinstance(exc, (FlowCompileError, ValueError)):
        return HTTPException(400, str(exc))
    return HTTPException(500, "Flow operation failed")


@router.post("/", response_model=FlowResponse)
async def create_flow(body: FlowCreateRequest, request: Request) -> FlowResponse:
    try:
        flow, revision = await _service(request).create(
            body.name, _definition(body), description=body.description or ""
        )
        return _flow_response(flow, revision)
    except Exception as exc:
        raise _http_error(exc) from exc


@router.get("/", response_model=list[FlowSummaryResponse])
async def list_flows(request: Request, include_templates: bool = True) -> list[FlowSummaryResponse]:
    values = await _service(request).list()
    return [
        _flow_summary(flow, revision)
        for flow, revision in reversed(values)
        if include_templates or not bool(revision.definition.get("is_template", False))
    ]


@router.get("/search/{name}", response_model=list[FlowSummaryResponse])
async def search_flows(name: str, request: Request) -> list[FlowSummaryResponse]:
    token = name.casefold()
    return [
        _flow_summary(flow, revision)
        for flow, revision in reversed(await _service(request).list())
        if token in flow.name.casefold()
    ]


@router.get("/{flow_id}", response_model=FlowResponse)
async def get_flow(flow_id: int, request: Request) -> FlowResponse:
    try:
        return _flow_response(*await _service(request).get(flow_id))
    except Exception as exc:
        raise _http_error(exc) from exc


@router.put("/{flow_id}", response_model=FlowResponse)
async def update_flow(flow_id: int, body: FlowUpdateRequest, request: Request) -> FlowResponse:
    service = _service(request)
    try:
        current, revision = await service.get(flow_id)
        value = dict(revision.definition)
        for field in ("nodes", "edges", "viewport", "data", "is_template", "tags"):
            if field in body.model_fields_set:
                value[field] = getattr(body, field)
        flow, changed = await service.update(
            flow_id,
            value,
            name=body.name,
            description=(
                body.description if "description" in body.model_fields_set else current.description
            ),
        )
        return _flow_response(flow, changed)
    except Exception as exc:
        raise _http_error(exc) from exc


@router.delete("/{flow_id}")
async def delete_flow(flow_id: int, request: Request) -> dict[str, str]:
    try:
        if not await _service(request).delete(flow_id):
            raise ConcurrentUpdateError("flow changed concurrently")
        return {"message": "Flow deleted successfully"}
    except Exception as exc:
        raise _http_error(exc) from exc


@router.post("/{flow_id}/duplicate", response_model=FlowResponse)
async def duplicate_flow(
    flow_id: int, request: Request, new_name: str | None = None
) -> FlowResponse:
    try:
        return _flow_response(*await _service(request).duplicate(flow_id, new_name))
    except Exception as exc:
        raise _http_error(exc) from exc


@router.post("/{flow_id}/runs/", response_model=FlowRunResponse)
async def create_run(flow_id: int, body: FlowRunCreateRequest, request: Request) -> FlowRunResponse:
    service = _service(request)
    try:
        run = await service.start_run(flow_id, body.request_data or {})
        return await _run_response(service, run)
    except Exception as exc:
        raise _http_error(exc) from exc


@router.get("/{flow_id}/runs/", response_model=list[FlowRunSummaryResponse])
async def list_runs(
    flow_id: int,
    request: Request,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> list[FlowRunSummaryResponse]:
    service = _service(request)
    try:
        runs = list(reversed(await service.list_runs(flow_id)))[offset : offset + limit]
        return [await _run_summary(service, run) for run in runs]
    except Exception as exc:
        raise _http_error(exc) from exc


@router.get("/{flow_id}/runs/active", response_model=FlowRunResponse | None)
async def active_run(flow_id: int, request: Request) -> FlowRunResponse | None:
    service = _service(request)
    runs = await service.list_runs(flow_id)
    active = next((run for run in reversed(runs) if run.status is RunStatus.RUNNING), None)
    return None if active is None else await _run_response(service, active)


@router.get("/{flow_id}/runs/latest", response_model=FlowRunResponse | None)
async def latest_run(flow_id: int, request: Request) -> FlowRunResponse | None:
    service = _service(request)
    runs = await service.list_runs(flow_id)
    return None if not runs else await _run_response(service, runs[-1])


@router.get("/{flow_id}/runs/count")
async def run_count(flow_id: int, request: Request) -> dict[str, int]:
    runs = await _service(request).list_runs(flow_id)
    return {"flow_id": flow_id, "total_runs": len(runs)}


@router.get("/{flow_id}/runs/{run_id}/cycles")
async def run_cycles(flow_id: int, run_id: int, request: Request) -> list[dict[str, Any]]:
    try:
        cycles = await _service(request).list_cycles(flow_id, run_id)
        return [
            {
                "id": public_id(item.id),
                "run_id": run_id,
                "sequence": item.sequence,
                "event": item.event,
                "payload": item.payload,
                "created_at": item.created_at,
            }
            for item in cycles
        ]
    except Exception as exc:
        raise _http_error(exc) from exc


@router.post("/{flow_id}/runs/{run_id}/cancel", response_model=FlowRunResponse)
async def cancel_run(flow_id: int, run_id: int, request: Request) -> FlowRunResponse:
    service = _service(request)
    try:
        return await _run_response(service, await service.cancel(flow_id, run_id))
    except Exception as exc:
        raise _http_error(exc) from exc


@router.get("/{flow_id}/runs/{run_id}", response_model=FlowRunResponse)
async def get_run(flow_id: int, run_id: int, request: Request) -> FlowRunResponse:
    service = _service(request)
    try:
        return await _run_response(service, await service.get_run(flow_id, run_id))
    except Exception as exc:
        raise _http_error(exc) from exc


@router.put("/{flow_id}/runs/{run_id}", response_model=FlowRunResponse)
async def update_run(
    flow_id: int,
    run_id: int,
    body: FlowRunUpdateRequest,
    request: Request,
) -> FlowRunResponse:
    service = _service(request)
    statuses = {
        FlowRunStatus.IDLE: RunStatus.PENDING,
        FlowRunStatus.IN_PROGRESS: RunStatus.RUNNING,
        FlowRunStatus.COMPLETE: RunStatus.SUCCEEDED,
        FlowRunStatus.ERROR: RunStatus.FAILED,
    }
    try:
        run = await service.update_run(
            flow_id,
            run_id,
            status=None if body.status is None else statuses[body.status],
            result=body.results,
            error=body.error_message,
        )
        return await _run_response(service, run)
    except Exception as exc:
        raise _http_error(exc) from exc


@router.delete("/{flow_id}/runs/{run_id}")
async def delete_run(flow_id: int, run_id: int, request: Request) -> dict[str, str]:
    try:
        if not await _service(request).delete_run(flow_id, run_id):
            raise RecordNotFoundError("Flow run not found")
        return {"message": "Flow run deleted successfully"}
    except Exception as exc:
        raise _http_error(exc) from exc


@router.delete("/{flow_id}/runs/")
async def delete_runs(flow_id: int, request: Request) -> dict[str, str]:
    try:
        count = await _service(request).delete_runs(flow_id)
        return {"message": f"Deleted {count} flow runs successfully"}
    except Exception as exc:
        raise _http_error(exc) from exc
