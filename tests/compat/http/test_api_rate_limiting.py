from __future__ import annotations

import httpx
from fastapi import FastAPI
from tests.compat.http.test_hedge_fund_routes import FakeRunner, request_body

from fin_graph.compat.settings import CompatibilityProfile
from fin_graph.server.compat import compatibility_router
from fin_graph.server.compat.hedge_fund import LegacyRunLimiter


async def test_legacy_stream_capacity_returns_retryable_429() -> None:
    app = FastAPI()
    app.state.compatibility_profile = CompatibilityProfile()
    app.state.compatibility_store = None
    app.state.compatibility_hedge_fund = FakeRunner()
    limiter = LegacyRunLimiter(1)
    assert limiter.try_acquire()
    app.state.compatibility_run_limiter = limiter
    app.include_router(compatibility_router())

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post("/hedge-fund/run", json=request_body())

    assert response.status_code == 429
    assert response.json() == {"detail": "run capacity exhausted"}
    assert response.headers["retry-after"] == "1"
