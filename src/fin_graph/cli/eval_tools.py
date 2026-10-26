"""Explicit recording, offline replay and quality regression CLI commands."""

from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import urlparse

import httpx
import typer
from rich.console import Console

from fin_graph.evals.comparison import compare_results
from fin_graph.evals.recording import load_recording, record_scenario, replay_graph

console = Console()


def _read(path: Path, limit: int = 8 * 1024 * 1024) -> dict[str, Any]:
    if path.stat().st_size > limit:
        raise ValueError("file exceeds limit")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("JSON object required")
    return value


def register_eval_tools(app: typer.Typer) -> None:
    app.command("compare")(compare)
    app.command("record")(record)
    app.command("replay")(replay)


def compare(
    baseline: Path, current: Path, tolerance: Annotated[float, typer.Option("--tolerance")] = 0.0
) -> None:
    try:
        result = compare_results(_read(baseline), _read(current), tolerance=tolerance)
        console.print(json.dumps(result), markup=False)
        if not result["passed"]:
            raise typer.Exit(1)
    except (OSError, ValueError, TypeError):
        console.print("Evaluations are invalid or incomparable")
        raise typer.Exit(2) from None


def record(
    output: Path,
    answer_file: Annotated[Path, typer.Option("--answer-file")],
    reviewer: Annotated[str, typer.Option("--reviewer")],
    snapshot_file: Annotated[Path | None, typer.Option("--snapshot-file")] = None,
    server: Annotated[str | None, typer.Option("--server")] = None,
    thread_id: Annotated[str | None, typer.Option("--thread-id")] = None,
    api_key_env: Annotated[str, typer.Option("--api-key-env")] = "FIN_GRAPH_API_KEY",
) -> None:
    try:
        if snapshot_file is not None and (server is not None or thread_id is not None):
            raise ValueError("choose snapshot or API")
        if snapshot_file:
            snapshot = _read(snapshot_file)
        else:
            if not server or not thread_id or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", thread_id):
                raise ValueError("API requires server and valid thread")
            parsed = urlparse(server)
            if (
                parsed.username
                or parsed.password
                or parsed.query
                or parsed.fragment
                or parsed.scheme not in {"https", "http"}
            ):
                raise ValueError("unsafe server URL")
            if parsed.scheme != "https" and parsed.hostname not in {
                "localhost",
                "127.0.0.1",
                "::1",
            }:
                raise ValueError("HTTPS required")
            key = os.environ.get(api_key_env)
            if not key:
                raise ValueError("API key environment variable missing")
            with (
                httpx.Client(
                    base_url=server, timeout=20, follow_redirects=False, trust_env=False
                ) as client,
                client.stream(
                    "GET", f"/v1/investigations/{thread_id}", headers={"X-API-Key": key}
                ) as response,
            ):
                response.raise_for_status()
                chunks = []
                size = 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > 8 * 1024 * 1024:
                        raise ValueError("snapshot exceeds limit")
                    chunks.append(chunk)
                snapshot = json.loads(b"".join(chunks))
            snapshot.setdefault("thread_id", thread_id)
            if "run" in snapshot:
                snapshot.setdefault("run_id", snapshot["run"].get("run_id"))
        record_scenario(snapshot, _read(answer_file, 65536), output, reviewer=reviewer)
        console.print("Recording saved; no quality baseline inferred")
    except (OSError, ValueError, TypeError, httpx.HTTPError):
        console.print("Recording failed: invalid snapshot, reference, directory or API response")
        raise typer.Exit(2) from None


def replay(
    directory: Path,
    output: Annotated[Path | None, typer.Option("--output")] = None,
    model: Annotated[str, typer.Option("--model")] = "scripted",
) -> None:
    try:
        result = asyncio.run(replay_graph(load_recording(directory), model=model))
        if output:
            with output.open("x", encoding="utf-8") as handle:
                json.dump(result, handle, indent=2)
        console.print(json.dumps(result), markup=False)
        if any(run["status"] != "completed" for run in result["runs"]):
            raise typer.Exit(1)
    except typer.Exit:
        raise
    except Exception:
        console.print("Offline replay failed; no live provider fallback")
        raise typer.Exit(2) from None
