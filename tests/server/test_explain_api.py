from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import create_async_engine

from sre_graph.server.app import create_app
from sre_graph.server.manager import RunManager
from sre_graph.server.storage import Base, Storage


class SnapshotGraph:
    async def aget_state(self, config: dict[str, Any]) -> SimpleNamespace:
        return SimpleNamespace(
            values={
                "wave": 2,
                "plan": {
                    "tasks": [
                        {
                            "domain": "logs",
                            "objective": "private agent prompt password=private",
                            "hypothesis_ids": ["H1"],
                        }
                    ]
                },
                "findings": [
                    {
                        "domain": "metrics",
                        "wave": 1,
                        "summary": "CPU rose after deploy",
                        "confidence": 0.8,
                        "evidence_ids": ["ev_metric"],
                        "verdicts": [],
                    },
                    {
                        "domain": "logs",
                        "wave": 2,
                        "summary": "Errors followed the deploy",
                        "confidence": 0.7,
                        "evidence_ids": ["ev_log"],
                        "verdicts": [],
                    },
                ],
                "evidence": [
                    {
                        "id": "ev_log",
                        "integration": "loki",
                        "tool": "query_logs",
                        "summary": "5xx burst",
                        "args": {"password": "private"},
                        "data": {"raw": "sensitive log content"},
                    }
                ],
                "verification": {
                    "passed": True,
                    "score": 0.71,
                    "missing_sections": [],
                    "claims": [
                        {"statement": "5xx errors rose", "status": "supported", "reason": "ok"}
                    ],
                },
                "diagnosis": {
                    "category": "application",
                    "root_cause": "Bad deploy",
                    "confidence": 0.8,
                },
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
            "tasks": [{"domain": "metrics", "hypothesis_ids": ["H1"]}],
            "objective": "private agent prompt",
        },
    )
    await storage.add_event(
        "run-1",
        "specialist",
        {
            "stage": "metrics",
            "wave": 1,
            "domain": "metrics",
            "evidence_ids": ["ev_metric"],
            "data": "sensitive raw metrics",
        },
    )
    manager = RunManager(SnapshotGraph(), storage, lambda: object())
    app = create_app(manager=manager)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client,
    ):
        path = "/v1/investigations/thread-1/explain"
        assert (await client.get(path)).status_code == 401
        assert (
            await client.get(
                "/v1/investigations/missing/explain", headers={"x-api-key": "viewer-key"}
            )
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
            "specialist",
        ]
        assert all(step["provenance"] == "observed" for step in execution["steps"])
        assert execution["waves"][0] == {
            "number": 1,
            "provenance": "observed",
            "tasks": [{"domain": "metrics", "hypothesis_ids": ["H1"], "provenance": "observed"}],
            "specialists": [
                {
                    "domain": "metrics",
                    "evidence_ids": ["ev_metric"],
                    "provenance": "observed",
                }
            ],
        }
        assert execution["waves"][1]["number"] == 2
        assert execution["waves"][1]["tasks"] == [
            {"domain": "logs", "hypothesis_ids": ["H1"], "provenance": "derived"}
        ]
        assert execution["findings"][1]["evidence_ids"] == ["ev_log"]
        assert execution["findings"][1]["provenance"] == "derived"
        assert execution["evidence"][0] == {
            "id": "ev_log",
            "integration": "loki",
            "tool": "query_logs",
            "provenance": "derived",
        }
        assert execution["verification"]["passed"] is True
        assert execution["verification"]["provenance"] == "derived"
        for forbidden in (
            "private agent prompt",
            "sensitive raw metrics",
            "sensitive log content",
            "5xx burst",
        ):
            assert forbidden not in response.text
    await engine.dispose()
