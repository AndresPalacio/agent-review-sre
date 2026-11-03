"""Durable algorithm-backtest job, signal-page, event, and cancel routes."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, cast

from fastapi import APIRouter, Header, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse

from fin_graph.adapters.reference_app.mapping import to_native_strategy_backtest_request
from fin_graph.adapters.reference_app.schemas import (
    AlgorithmBacktestConfig,
    AlgorithmBacktestEvent,
    AlgorithmBacktestJob,
    AlgorithmBacktestResult,
    AlgorithmSignalPage,
    AlgorithmSignalPoint,
)
from fin_graph.backtesting import (
    BacktestArtifactStore,
    BacktestEventKind,
    BacktestJobManager,
    BacktestRunOutput,
    run_strategy_backtest,
)
from fin_graph.conformance.contracts import JsonObject
from fin_graph.persistence.reference_app import BacktestJobRecord, JobStatus, ReferenceStore
from fin_graph.quant.data import MarketDataClient
from fin_graph.strategies.catalog import ParameterValue, strategy_catalog

router = APIRouter(prefix="/v2/backtests")


def _detail(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


def _metadata(config: AlgorithmBacktestConfig) -> tuple[str, str, dict[str, ParameterValue]]:
    factory = strategy_catalog().get(config.algorithm_key)
    if factory is None:
        raise HTTPException(
            404,
            detail=_detail("algorithm-not-found", f"Unknown algorithm {config.algorithm_key!r}"),
        )
    try:
        resolved = factory.registry.resolve(config.algorithm_key, config.parameters)
    except ValueError as exc:
        raise HTTPException(422, detail=_detail("parameter-invalid", str(exc))) from exc
    specification = factory.specification
    digest = hashlib.sha256(specification.model_dump_json().encode("utf-8")).hexdigest()
    return specification.version, digest, resolved


def make_strategy_job_manager(
    store: ReferenceStore,
    artifact_root: Path,
    data: MarketDataClient,
    *,
    max_workers: int = 2,
) -> BacktestJobManager:
    async def runner(
        request: JsonObject,
        *,
        event_sink: Any,
        cancellation: Any,
    ) -> BacktestRunOutput:
        config = AlgorithmBacktestConfig.model_validate(request)
        native = to_native_strategy_backtest_request(config)
        factory = strategy_catalog().get(config.algorithm_key)
        if factory is None:
            raise KeyError(f"Unknown algorithm {config.algorithm_key!r}")
        output = await run_strategy_backtest(
            native,
            factory.create(config.parameters),
            data,
            event_sink=event_sink,
            cancellation=cancellation,
        )
        return BacktestRunOutput(
            result=cast(JsonObject, output.result.model_dump(mode="json")),
            signals=tuple(
                cast(JsonObject, item.model_dump(mode="json")) for item in output.signals
            ),
        )

    return BacktestJobManager(
        store,
        BacktestArtifactStore(artifact_root),
        {"strategy": runner},
        max_workers=max_workers,
    )


async def _manager(request: Request) -> BacktestJobManager:
    manager = getattr(request.app.state, "reference_app_backtest_manager", None)
    if isinstance(manager, BacktestJobManager):
        return manager
    store = getattr(request.app.state, "reference_app_store", None)
    data = getattr(request.app.state, "reference_app_quant_data", None)
    if not isinstance(store, ReferenceStore) or not isinstance(data, MarketDataClient):
        raise HTTPException(503, "backtest job service unavailable")
    configured_root = getattr(
        request.app.state, "reference_app_backtest_artifact_dir", ".v2_cache/backtests"
    )
    root = await asyncio.to_thread(lambda: Path(configured_root).resolve())
    manager = make_strategy_job_manager(store, root, data)
    request.app.state.reference_app_backtest_manager = manager
    await manager.recover()
    return manager


def _status(value: JobStatus) -> str:
    return {
        JobStatus.QUEUED: "queued",
        JobStatus.RUNNING: "running",
        JobStatus.SUCCEEDED: "complete",
        JobStatus.FAILED: "failed",
        JobStatus.CANCELLED: "cancelled",
    }[value]


async def _result(
    manager: BacktestJobManager,
    record: BacktestJobRecord,
    config: AlgorithmBacktestConfig,
    version: str,
    digest: str,
    resolved: dict[str, ParameterValue],
) -> AlgorithmBacktestResult | None:
    if record.status not in {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED}:
        return None
    raw = record.result if isinstance(record.result, dict) else {}
    artifacts = await manager.store.list_artifacts(record.id)
    return AlgorithmBacktestResult(
        run_id=record.id,
        algorithm_key=config.algorithm_key,
        algorithm_version=version,
        algorithm_hash=digest,
        resolved_parameters=resolved,
        status=cast(Any, _status(record.status)),
        metrics=raw.get("metrics"),
        equity_curve=raw.get("equity_curve", []),
        trades=raw.get("trades", []),
        signal_summary=raw.get("signal_summary", []),
        data_coverage=raw.get("data_coverage", {}),
        artifact_paths={item.kind: item.path for item in artifacts},
        warnings=raw.get("warnings", []),
        error_code=(
            "cancelled"
            if record.status is JobStatus.CANCELLED
            else "worker-failed"
            if record.status is JobStatus.FAILED
            else None
        ),
        error=record.error,
    )


async def _job(manager: BacktestJobManager, record: BacktestJobRecord) -> AlgorithmBacktestJob:
    config = AlgorithmBacktestConfig.model_validate(record.request)
    version, digest, resolved = _metadata(config)
    events = await manager.events(record.id)
    progress = events[-1].progress if events else 0
    return AlgorithmBacktestJob(
        run_id=record.id,
        status=cast(Any, _status(record.status)),
        config=config,
        algorithm_version=version,
        algorithm_hash=digest,
        resolved_parameters=resolved,
        created_at=record.created_at,
        updated_at=record.updated_at,
        progress=progress,
        error_code=(
            "cancelled"
            if record.status is JobStatus.CANCELLED
            else "worker-failed"
            if record.status is JobStatus.FAILED
            else None
        ),
        error=record.error,
        result=await _result(manager, record, config, version, digest, resolved),
    )


async def _record(manager: BacktestJobManager, run_id: str) -> BacktestJobRecord:
    try:
        value = await manager.get(run_id)
    except ValueError as exc:
        raise HTTPException(
            404, detail=_detail("result-unavailable", "Backtest run was not found")
        ) from exc
    if value is None:
        raise HTTPException(404, detail=_detail("result-unavailable", "Backtest run was not found"))
    return value


@router.post("", response_model=AlgorithmBacktestJob, status_code=status.HTTP_202_ACCEPTED)
async def start(config: AlgorithmBacktestConfig, request: Request) -> AlgorithmBacktestJob:
    _metadata(config)
    manager = await _manager(request)
    record = await manager.start("strategy", cast(JsonObject, config.model_dump(mode="json")))
    return await _job(manager, record)


@router.get("/{run_id}", response_model=AlgorithmBacktestJob)
async def get(run_id: str, request: Request) -> AlgorithmBacktestJob:
    manager = await _manager(request)
    return await _job(manager, await _record(manager, run_id))


@router.get("/{run_id}/signals", response_model=AlgorithmSignalPage)
async def signals(
    run_id: str,
    request: Request,
    symbol: str | None = None,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=500, ge=1, le=2_000),
) -> AlgorithmSignalPage:
    manager = await _manager(request)
    record = await _record(manager, run_id)
    if record.status is not JobStatus.SUCCEEDED:
        raise HTTPException(
            404, detail=_detail("result-unavailable", "Signal result is unavailable")
        )
    try:
        values = manager.artifacts.load_signals(run_id)
    except ValueError as exc:
        raise HTTPException(422, detail=_detail("parameter-invalid", str(exc))) from exc
    if symbol is not None:
        selected = symbol.strip().upper()
        values = tuple(item for item in values if item.get("symbol") == selected)
    end = min(offset + limit, len(values))
    return AlgorithmSignalPage(
        run_id=run_id,
        items=[AlgorithmSignalPoint.model_validate(item) for item in values[offset:end]],
        offset=offset,
        limit=limit,
        total=len(values),
        next_offset=end if end < len(values) else None,
    )


def _event(record: BacktestJobRecord, event: Any) -> AlgorithmBacktestEvent:
    event_type = {
        BacktestEventKind.RUNNING: "progress",
        BacktestEventKind.PROGRESS: "progress",
        BacktestEventKind.COMPLETE: "complete",
        BacktestEventKind.CANCELLED: "cancelled",
        BacktestEventKind.ERROR: "error",
    }[event.kind]
    event_status = {
        BacktestEventKind.RUNNING: "running",
        BacktestEventKind.PROGRESS: "running",
        BacktestEventKind.COMPLETE: "complete",
        BacktestEventKind.CANCELLED: "cancelled",
        BacktestEventKind.ERROR: "failed",
    }[event.kind]
    return AlgorithmBacktestEvent(
        sequence=event.sequence,
        type=cast(Any, event_type),
        status=cast(Any, event_status),
        message=event.message,
        progress=event.progress,
    )


@router.get("/{run_id}/events")
async def events(
    run_id: str,
    request: Request,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
) -> StreamingResponse:
    manager = await _manager(request)
    record = await _record(manager, run_id)
    try:
        after = -1 if last_event_id is None else int(last_event_id)
    except ValueError as exc:
        raise HTTPException(400, "Last-Event-ID must be an integer") from exc

    async def stream() -> AsyncIterator[bytes]:
        sequence = after
        while True:
            current = await _record(manager, run_id)
            values = await manager.events(run_id)
            for item in values:
                if item.sequence <= sequence:
                    continue
                sequence = item.sequence
                wire = _event(current, item)
                payload = json.dumps(
                    wire.model_dump(mode="json"),
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                yield f"id: {item.sequence}\nevent: {wire.type}\ndata: {payload}\n\n".encode()
                if item.terminal:
                    return
            if current.status in {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED}:
                return
            await asyncio.sleep(0.05)

    del record
    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/{run_id}/cancel", response_model=AlgorithmBacktestJob)
async def cancel(run_id: str, request: Request) -> AlgorithmBacktestJob:
    manager = await _manager(request)
    await _record(manager, run_id)
    record = await manager.cancel(run_id)
    if record is None:
        raise HTTPException(404, detail=_detail("result-unavailable", "Backtest run was not found"))
    return await _job(manager, record)
