from __future__ import annotations

import json

import httpx
from typer.testing import CliRunner

from sre_graph.cli.main import app


def test_schedule_cli_crud_and_run(monkeypatch, tmp_path) -> None:
    calls: list[tuple[str, str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(
            (
                request.method,
                request.url.path,
                json.loads(request.content) if request.content else None,
            )
        )
        assert request.headers["x-api-key"] == "admin"
        if request.method == "GET" and request.url.path == "/v1/schedules":
            return httpx.Response(200, json=[{"id": "cpu", "enabled": True}])
        if request.method == "DELETE":
            return httpx.Response(204)
        return httpx.Response(200, json={"id": "cpu", "enabled": True})

    original = httpx.AsyncClient

    def client_factory(*args, **kwargs):
        return original(*args, transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr("sre_graph.cli.schedules.httpx.AsyncClient", client_factory)
    source = tmp_path / "schedule.json"
    source.write_text(json.dumps({"id": "cpu", "enabled": True, "definition": {"kind": "digest"}}))
    runner = CliRunner()
    common = ["--server", "http://localhost:8000", "--api-key", "admin"]
    for args in (
        ["list"],
        ["get", "cpu"],
        ["create", str(source)],
        ["update", "cpu", str(source)],
        ["run", "cpu"],
        ["delete", "cpu"],
    ):
        result = runner.invoke(app, ["schedules", *args, *common])
        assert result.exit_code == 0, result.output
    assert [item[:2] for item in calls] == [
        ("GET", "/v1/schedules"),
        ("GET", "/v1/schedules/cpu"),
        ("POST", "/v1/schedules"),
        ("PUT", "/v1/schedules/cpu"),
        ("POST", "/v1/schedules/cpu/run"),
        ("DELETE", "/v1/schedules/cpu"),
    ]
    assert calls[3][2] == {"enabled": True, "definition": {"kind": "digest"}}


def test_schedule_cli_rejects_insecure_remote_and_reports_http_failure(monkeypatch) -> None:
    runner = CliRunner()
    result = runner.invoke(
        app, ["schedules", "list", "--server", "http://example.com", "--api-key", "secret"]
    )
    assert result.exit_code != 0
    assert "HTTPS" in result.output

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"detail": "forbidden"})

    original = httpx.AsyncClient
    monkeypatch.setattr(
        "sre_graph.cli.schedules.httpx.AsyncClient",
        lambda *args, **kwargs: original(*args, transport=httpx.MockTransport(handler), **kwargs),
    )
    result = runner.invoke(
        app, ["schedules", "list", "--server", "http://localhost", "--api-key", "secret"]
    )
    assert result.exit_code != 0
    assert "403" in result.output
    assert "secret" not in result.output
