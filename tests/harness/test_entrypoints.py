"""Exercise product entry points with local storage and provider-free services."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from tests.server.finance_app import (
    REQUEST,
    analysis_manager,
    client_for,
    fixture_context,
    make_storage,
)
from typer.testing import CliRunner

from fin_graph.scheduler.market import MarketJobs
from fin_graph.server.app import create_app
from fin_graph.server.security import WebhookVerifier


@pytest.mark.parametrize("source", ["api", "webhook", "scheduler"])
async def test_api_webhook_and_market_watch_expose_same_analysis_runtime(tmp_path, source):
    engine, storage = await make_storage(tmp_path / "entrypoints.sqlite")
    context = fixture_context()
    manager = analysis_manager(storage, context)
    app = create_app(
        manager=manager,
        verifier=WebhookVerifier(tokens={"generic": "webhook-key"}),
    )
    try:
        async with client_for(app) as client:
            if source == "api":
                response = await client.post(
                    "/v1/analyses",
                    json={"request": REQUEST},
                    headers={"x-api-key": "operator-key"},
                )
                assert response.status_code == 202
                run = response.json()
            elif source == "webhook":
                response = await client.post(
                    "/v1/signals/generic",
                    headers={"x-fin-webhook-token": "webhook-key"},
                    json=REQUEST,
                )
                assert response.status_code == 202, response.text
                run = response.json()
            else:
                jobs = MarketJobs(
                    storage,
                    manager,
                    context,
                    now=lambda: datetime(2024, 6, 28, 14, 0, tzinfo=UTC),
                )
                await jobs.market_watch(
                    "deep-watch",
                    {
                        "kind": "market_watch",
                        "interval_minutes": 5,
                        "rules": [{"ticker": "DEEP", "max_daily_move_pct": 0.01}],
                    },
                )
                run = (await storage.list_runs(10))[0]
            await manager.wait(run["run_id"])
            result = await client.get(
                f"/v1/analyses/{run['thread_id']}", headers={"x-api-key": "operator-key"}
            )
            assert result.status_code == 200
            body = result.json()
            assert body["run"]["status"] == "completed"
            assert body["report"]
            assert body.get("harness", {}).get("mode") == "analysis"
            assert body["harness"]["variant"].startswith("sha256:")
            assert "harness_trace" not in body
    finally:
        await engine.dispose()


def test_cli_runtime_is_machine_readable_and_needs_no_optimizer(monkeypatch):
    import builtins

    from fin_graph.cli.main import app

    original = builtins.__import__

    def guarded(name, *args, **kwargs):
        if name.split(".")[0] in {"better_harness", "deepagents"}:
            raise ImportError("optional optimizer not installed")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded)
    result = CliRunner().invoke(app, ["harness", "runtime"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["mode"] == "analysis"
