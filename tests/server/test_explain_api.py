from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import create_async_engine

from fin_graph.server.app import create_app
from fin_graph.server.manager import RunManager
from fin_graph.server.storage import Base, Storage


class SnapshotGraph:
    async def aget_state(self, config: dict[str, Any]) -> SimpleNamespace:
        return SimpleNamespace(
            values={
                "wave": 2,
                "plan": {
                    "tasks": [
                        {
                            "domain": "valuation",
                            "objective": "private agent prompt password=private",
                            "hypothesis_ids": ["H1"],
                        }
                    ]
                },
                "findings": [
                    {
                        "domain": "fundamentals",
                        "wave": 1,
                        "summary": "ROE and margins are strong",
                        "confidence": 0.8,
                        "signals": [{"ticker": "DEEP", "signal": "bullish"}],
                        "evidence_ids": ["ev_fundamentals"],
                        "verdicts": [],
                    },
                    {
                        "domain": "valuation",
                        "wave": 2,
                        "summary": "The stock trades below intrinsic value",
                        "confidence": 0.7,
                        "signals": [{"ticker": "DEEP", "signal": "bullish"}],
                        "evidence_ids": ["ev_valuation"],
                        "verdicts": [],
                    },
                ],
                "evidence": [
                    {
                        "id": "ev_valuation",
                        "integration": "financial_datasets",
                        "tool": "get_financial_metrics",
                        "summary": "undervalued",
                        "args": {"password": "private"},
                        "data": {"raw": "sensitive market payload"},
                    }
                ],
                "verification": {
                    "DEEP": {
                        "passed": True,
                        "score": 0.71,
                        "missing_sections": [],
                        "claims": [
                            {
                                "statement": "DEEP has a margin of safety",
                                "status": "supported",
                                "reason": "ok",
                            }
                        ],
                    }
                },
                "theses": [
                    {
                        "ticker": "DEEP",
                        "stance": "bullish",
                        "category": "deep_value",
                        "confidence": 80,
                        "summary": "Balance sheet strength supports the thesis.",
                    }
                ],
                "decisions": {"DEEP": {"action": "buy", "quantity": 10, "reasoning": "risk sized"}},
                "orders": [
                    {
                        "client_order_id": "order-1",
                        "ticker": "DEEP",
                        "action": "buy",
                        "quantity": 10,
                    }
                ],
            }
        )


async def test_explain_shows_observed_events_and_derived_checkpoint_without_tool_payloads(
    tmp_path: Path,
) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'explain.sqlite'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    storage = Storage(engine)
    await storage.put_key("viewer-key", "viewer")
    await storage.create_run("run-1", "thread-1")
    await storage.add_event("run-1", "stage", {"stage": "intake", "prompt": "private"})
    await storage.add_event(
        "run-1",
        "plan",
        {
            "stage": "plan",
            "wave": 1,
            "tasks": [{"domain": "fundamentals", "hypothesis_ids": ["H1"]}],
            "objective": "private agent prompt",
        },
    )
    await storage.add_event(
        "run-1",
        "analyst",
        {
            "stage": "fundamentals",
            "wave": 1,
            "domain": "fundamentals",
            "evidence_ids": ["ev_fundamentals"],
            "signals": {"DEEP": "bullish"},
            "data": "sensitive raw market data",
        },
    )
    manager = RunManager(SnapshotGraph(), storage, lambda: object())
    app = create_app(manager=manager)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client,
    ):
        path = "/v1/analyses/thread-1/explain"
        assert (await client.get(path)).status_code == 401
        assert (
            await client.get("/v1/analyses/missing/explain", headers={"x-api-key": "viewer-key"})
        ).status_code == 404
        response = await client.get(path, headers={"x-api-key": "viewer-key"})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["thread_id"] == "thread-1"
        assert body["run"]["run_id"] == "run-1"
        assert "plan" in [node["id"] for node in body["topology"]["nodes"]]
        assert {"source": "verify", "target": "refine", "label": "needs_more_evidence"} in body[
            "topology"
        ]["edges"]
        execution = body["execution"]
        assert execution["event_scope"] == "thread"
        assert execution["steps_limit"] == 500
        assert execution["steps_truncated"] is False
        assert execution["next_after_id"] is None
        assert [step["type"] for step in execution["steps"]] == [
            "stage",
            "plan",
            "analyst",
        ]
        assert all(step["provenance"] == "observed" for step in execution["steps"])
        assert execution["waves"][0] == {
            "number": 1,
            "provenance": "observed",
            "tasks": [{"domain": "fundamentals", "provenance": "observed"}],
            "analysts": [
                {
                    "domain": "fundamentals",
                    "evidence_ids": ["ev_fundamentals"],
                    "signals": {"DEEP": "bullish"},
                    "provenance": "observed",
                }
            ],
        }
        assert execution["waves"][1]["number"] == 2
        assert execution["waves"][1]["tasks"] == []
        assert execution["findings"][1]["evidence_ids"] == ["ev_valuation"]
        assert execution["findings"][1]["provenance"] == "derived"
        assert execution["evidence"][0] == {
            "id": "ev_valuation",
            "integration": "financial_datasets",
            "tool": "get_financial_metrics",
            "provenance": "derived",
        }
        assert execution["theses"][0]["ticker"] == "DEEP"
        assert execution["theses"][0]["verified"] is True
        assert execution["decisions"]["DEEP"]["action"] == "buy"
        assert execution["orders"][0]["client_order_id"] == "order-1"
        for forbidden in (
            "private agent prompt",
            "sensitive raw market data",
            "sensitive market payload",
            "undervalued",
        ):
            assert forbidden not in response.text
    await engine.dispose()
