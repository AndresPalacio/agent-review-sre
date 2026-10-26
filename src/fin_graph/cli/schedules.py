"""Remote schedule administration through the authenticated server API."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Annotated, Any

import httpx
import typer
from rich.console import Console

app = typer.Typer(help="Administra schedules en la API remota.", no_args_is_help=True)
_console = Console()
_server = typer.Option("--server", help="Origen HTTP(S) de la API.")
_key = typer.Option("--api-key", envvar="FIN_GRAPH_API_KEY", help="API key remota.")


def _origin(server: str) -> httpx.URL:
    try:
        url = httpx.URL(server)
    except httpx.InvalidURL as exc:
        raise ValueError("invalid server URL") from exc
    if (
        url.scheme not in {"http", "https"}
        or not url.host
        or url.username
        or url.password
        or url.query
        or url.fragment
        or url.path not in {"", "/"}
    ):
        raise ValueError("server URL must be an HTTP(S) origin without credentials")
    if url.scheme == "http" and url.host not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("remote API keys require HTTPS outside loopback")
    return url


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("schedule file must contain a JSON object")
    return value


async def _request(
    method: str, path: str, server: str, api_key: str | None, body: dict[str, Any] | None = None
) -> None:
    if not api_key:
        raise ValueError("--api-key or FIN_GRAPH_API_KEY is required")
    url = _origin(server)
    async with httpx.AsyncClient(base_url=url, timeout=30.0) as client:
        response = await client.request(method, path, headers={"x-api-key": api_key}, json=body)
    if response.is_error:
        raise ValueError(f"API returned HTTP {response.status_code}")
    if response.status_code == 204:
        _console.print("Deleted")
    else:
        _console.print(json.dumps(response.json(), ensure_ascii=False), markup=False)


def _run_with_error(
    method: str,
    path: str,
    server: str,
    api_key: str | None,
    body: dict[str, Any] | None = None,
) -> None:
    try:
        asyncio.run(_request(method, path, server, api_key, body))
    except ValueError as exc:
        _console.print(f"Error: {exc}")
        raise typer.Exit(code=1) from None
    except (OSError, httpx.HTTPError):
        _console.print("Schedule request failed")
        raise typer.Exit(code=1) from None


@app.command("list")
def list_schedules(
    server: Annotated[str, _server], api_key: Annotated[str | None, _key] = None
) -> None:
    _run_with_error("GET", "/v1/schedules", server, api_key)


@app.command()
def get(
    schedule_id: str, server: Annotated[str, _server], api_key: Annotated[str | None, _key] = None
) -> None:
    _run_with_error("GET", f"/v1/schedules/{schedule_id}", server, api_key)


@app.command()
def create(
    file: Path, server: Annotated[str, _server], api_key: Annotated[str | None, _key] = None
) -> None:
    try:
        body = _load(file)
    except (OSError, ValueError, json.JSONDecodeError):
        _console.print("Invalid schedule JSON file")
        raise typer.Exit(code=1) from None
    _run_with_error("POST", "/v1/schedules", server, api_key, body)


@app.command()
def update(
    schedule_id: str,
    file: Path,
    server: Annotated[str, _server],
    api_key: Annotated[str | None, _key] = None,
) -> None:
    try:
        body = _load(file)
    except (OSError, ValueError, json.JSONDecodeError):
        _console.print("Invalid schedule JSON file")
        raise typer.Exit(code=1) from None
    body.pop("id", None)
    _run_with_error("PUT", f"/v1/schedules/{schedule_id}", server, api_key, body)


@app.command()
def delete(
    schedule_id: str, server: Annotated[str, _server], api_key: Annotated[str | None, _key] = None
) -> None:
    _run_with_error("DELETE", f"/v1/schedules/{schedule_id}", server, api_key)


@app.command()
def run(
    schedule_id: str, server: Annotated[str, _server], api_key: Annotated[str | None, _key] = None
) -> None:
    _run_with_error("POST", f"/v1/schedules/{schedule_id}/run", server, api_key)
