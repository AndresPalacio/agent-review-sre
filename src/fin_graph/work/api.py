"""Local pilot gateway: one process and a trusted actor, served on loopback."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from fin_graph.work.contracts import (
    CancelWork,
    StartWork,
    UpdateWork,
    WorkConflict,
    WorkUnavailable,
    WorkView,
)
from fin_graph.work.mcp import create_mcp_server
from fin_graph.work.service import WorkService


def create_app(service: WorkService, *, actor: str = "local-demo") -> FastAPI:
    """Use the supplied service; actor identity never comes from a caller's request.

    This unauthenticated, single-actor pilot must be bound to loopback. It is not
    a public multiuser API and must run with one gateway worker.
    """
    server = create_mcp_server(service, actor=actor)
    mcp_app = server.streamable_http_app()

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        async with server.session_manager.run():
            yield

    app = FastAPI(title="fin-graph Work local pilot", lifespan=lifespan)

    @app.exception_handler(WorkConflict)
    @app.exception_handler(WorkUnavailable)
    @app.exception_handler(PermissionError)
    async def domain_error(_request: Request, exc: Exception) -> JSONResponse:
        if isinstance(exc, WorkConflict):
            status = 409
        elif isinstance(exc, WorkUnavailable):
            status = 503
        else:
            status = 403
        return JSONResponse(status_code=status, content={"detail": str(exc)})

    @app.post("/work", response_model=WorkView)
    async def work_start(command: StartWork) -> WorkView:
        return await service.start(command, actor=actor)

    @app.get("/work/{work_id}", response_model=WorkView)
    async def work_get(work_id: str) -> WorkView:
        return await service.get(work_id, actor=actor)

    @app.post("/work/{work_id}/update", response_model=WorkView)
    async def work_update(work_id: str, command: UpdateWork) -> WorkView:
        return await service.update(work_id, command, actor=actor)

    @app.post("/work/{work_id}/cancel", response_model=WorkView)
    async def work_cancel(work_id: str, command: CancelWork) -> WorkView:
        return await service.cancel(work_id, command, actor=actor)

    app.mount("/mcp", mcp_app)
    return app
