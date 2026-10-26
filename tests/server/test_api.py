from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path

from tests.server.finance_app import (
    AS_OF,
    REQUEST,
    analysis_manager,
    client_for,
    fixture_context,
    make_storage,
)

from fin_graph.server.app import create_app
from fin_graph.server.security import WebhookVerifier

TRADINGVIEW = {
    "ticker": "NASDAQ:DEEP",
    "exchange": "NASDAQ",
    "interval": "1D",
    "close": 8.0,
    "time": f"{AS_OF}T20:00:00Z",
    "message": "cruce de medias",
}


async def test_signal_to_report_sse_dedupe_and_auth(tmp_path: Path) -> None:
    engine, storage = await make_storage(tmp_path / "api.sqlite")
    manager = analysis_manager(storage, fixture_context(), max_concurrent=1)
    app = create_app(
        manager=manager,
        verifier=WebhookVerifier(tokens={"tradingview": "tv-key", "generic": "generic-key"}),
    )
    async with client_for(app) as client:
        assert (await client.get("/v1/threads")).status_code == 401
        assert (
            await client.post(
                "/v1/analyses", json={"text": "x"}, headers={"x-api-key": "viewer-key"}
            )
        ).status_code == 403
        assert (
            await client.post(
                "/v1/api-keys", json={"role": "viewer"}, headers={"x-api-key": "operator-key"}
            )
        ).status_code == 403
        created_key = await client.post(
            "/v1/api-keys", json={"role": "viewer"}, headers={"x-api-key": "admin-key"}
        )
        assert created_key.status_code == 201
        assert (
            await client.get("/v1/threads", headers={"x-api-key": created_key.json()["key"]})
        ).status_code == 200
        assert (await client.post("/v1/signals/tradingview", json=TRADINGVIEW)).status_code == 401
        assert (
            await client.post("/v1/signals/tradingview", content=b"not-json")
        ).status_code == 401
        assert (
            await client.post(
                "/v1/signals/generic",
                json={"message": "sin ticker"},
                headers={"x-fin-webhook-token": "generic-key"},
            )
        ).status_code == 422
        headers = {"x-fin-webhook-token": "tv-key"}
        response = await client.post("/v1/signals/tradingview", json=TRADINGVIEW, headers=headers)
        assert response.status_code == 202, response.text
        run = response.json()
        await manager.wait(run["run_id"])
        detail = await client.get(
            f"/v1/analyses/{run['thread_id']}", headers={"x-api-key": "viewer-key"}
        )
        assert detail.status_code == 200
        report = detail.json()["report"]
        assert report["request"]["source"] == "tradingview"
        assert [section["ticker"] for section in report["sections"]] == ["DEEP"]
        assert "cruce de medias" in report["request"]["trigger"]
        stream = await client.get(
            f"/v1/threads/{run['thread_id']}/stream", headers={"x-api-key": "viewer-key"}
        )
        assert "event: stage" in stream.text and "event: analyst" in stream.text
        assert "event: status" in stream.text
        history = await client.get(
            f"/v1/threads/{run['thread_id']}/history", headers={"x-api-key": "viewer-key"}
        )
        last_id = history.json()[-1]["id"]
        replay = await client.get(
            f"/v1/threads/{run['thread_id']}/stream",
            headers={"x-api-key": "viewer-key", "Last-Event-ID": str(last_id - 1)},
        )
        assert f"id: {last_id}\n" in replay.text
        assert "event: stage" not in replay.text
        for index in range(501):
            await storage.add_event(run["run_id"], "bulk", {"index": index})
        bulk = await client.get(
            f"/v1/threads/{run['thread_id']}/stream",
            headers={"x-api-key": "viewer-key", "Last-Event-ID": str(last_id)},
        )
        assert bulk.text.count("event: bulk") == 501
        page = await client.get(
            f"/v1/threads/{run['thread_id']}/history?after_id={last_id}&limit=500",
            headers={"x-api-key": "viewer-key"},
        )
        assert len(page.json()) == 500
        tail = await client.get(
            f"/v1/threads/{run['thread_id']}/history?after_id={page.json()[-1]['id']}",
            headers={"x-api-key": "viewer-key"},
        )
        assert len(tail.json()) == 1
        assert (
            await client.post(
                "/v1/signals/tradingview", content=b"x" * (1024 * 1024 + 1), headers=headers
            )
        ).status_code == 413

        async def oversized_chunks():
            yield b"x" * (512 * 1024)
            yield b"y" * (512 * 1024 + 1)

        assert (
            await client.post(
                "/v1/signals/tradingview", content=oversized_chunks(), headers=headers
            )
        ).status_code == 413
        again = await client.post("/v1/signals/tradingview", json=TRADINGVIEW, headers=headers)
        assert again.json() == {"thread_id": run["thread_id"], "deduplicated": True}
        assert (await client.get("/v1/ready")).status_code == 200
    await engine.dispose()


async def test_every_signal_source_starts_an_analysis(tmp_path: Path) -> None:
    engine, storage = await make_storage(tmp_path / "sources.sqlite")
    manager = analysis_manager(storage, fixture_context())
    secret = "hmac-secret"
    verifier = WebhookVerifier(
        tokens={"tradingview": "tv-key", "generic": "generic-key"},
        hmac_secrets={"price_alert": secret},
    )
    app = create_app(manager=manager, verifier=verifier)
    price_alert = json.dumps(
        {"symbol": "DEEP", "change_pct": -7.5, "price": 8.0, "date": AS_OF}
    ).encode()
    signature = "sha256=" + hmac.new(secret.encode(), price_alert, hashlib.sha256).hexdigest()
    calls = [
        ("tradingview", {"content": json.dumps({**TRADINGVIEW, "token": "tv-key"})}, {}),
        ("price_alert", {"content": price_alert}, {"x-fin-signature": signature}),
        ("generic", {"json": REQUEST}, {"x-fin-webhook-token": "generic-key"}),
    ]
    async with client_for(app) as client:
        for source, body, headers in calls:
            response = await client.post(f"/v1/signals/{source}", headers=headers, **body)
            assert response.status_code == 202, (source, response.text)
            run = response.json()
            await manager.wait(run["run_id"])
            detail = await client.get(
                f"/v1/analyses/{run['thread_id']}", headers={"x-api-key": "viewer-key"}
            )
            request = detail.json()["report"]["request"]
            assert request["source"] == {"generic": "json"}.get(source, source)
            assert request["tickers"] == ["DEEP"] and request["as_of"] == AS_OF
        assert (
            await client.post(
                "/v1/signals/price_alert", content=price_alert, headers={"x-fin-signature": "bad"}
            )
        ).status_code == 401
    await engine.dispose()


async def test_analysis_requests_accept_text_or_json_but_not_both(tmp_path: Path) -> None:
    engine, storage = await make_storage(tmp_path / "requests.sqlite")
    manager = analysis_manager(storage, fixture_context())
    app = create_app(manager=manager)
    operator = {"x-api-key": "operator-key"}
    async with client_for(app) as client:
        both = await client.post(
            "/v1/analyses", json={"text": "DEEP", "request": REQUEST}, headers=operator
        )
        nameless = await client.post(
            "/v1/analyses", json={"request": {"question": "hola"}}, headers=operator
        )
        started = await client.post("/v1/analyses", json={"request": REQUEST}, headers=operator)
        await manager.wait(started.json()["run_id"])
        detail = await client.get(
            f"/v1/analyses/{started.json()['thread_id']}", headers={"x-api-key": "viewer-key"}
        )
        explained = await client.get(
            f"/v1/analyses/{started.json()['thread_id']}/explain",
            headers={"x-api-key": "viewer-key"},
        )

    assert both.status_code == 422 and nameless.status_code == 422
    body = detail.json()
    assert body["run"]["status"] == "completed"
    assert {item["domain"] for item in body["findings"]} >= {"valuation", "graham"}
    assert body["theses"][0]["ticker"] == "DEEP"
    execution = explained.json()["execution"]
    assert execution["theses"][0]["ticker"] == "DEEP"
    assert any(wave["analysts"] for wave in execution["waves"])
    await engine.dispose()
