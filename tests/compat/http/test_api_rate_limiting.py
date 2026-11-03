from __future__ import annotations

import httpx
from fastapi import FastAPI
from tests.compat.http.test_hedge_fund_routes import FakeRunner, request_body

from fin_graph.adapters.reference_app.http import reference_app_router
from fin_graph.adapters.reference_app.http.hedge_fund import ReferenceRunLimiter
from fin_graph.adapters.reference_app.settings import ReferenceAppProfile


async def test_legacy_stream_capacity_returns_retryable_429() -> None:
    app = FastAPI()
    app.state.reference_app_profile = ReferenceAppProfile()
    app.state.reference_app_store = None
    app.state.reference_app_hedge_fund = FakeRunner()
    limiter = ReferenceRunLimiter(1)
    assert limiter.try_acquire()
    app.state.reference_app_run_limiter = limiter
    app.include_router(reference_app_router())

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post("/hedge-fund/run", json=request_body())

    assert response.status_code == 429
    assert response.json() == {"detail": "run capacity exhausted"}
    assert response.headers["retry-after"] == "1"
