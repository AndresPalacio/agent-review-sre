"""Run/debug the optional async supervisor with an actual local Agent Server.

python examples/demo_supervisor_async.py --serve
python examples/demo_supervisor_async.py --scenario lifecycle
python examples/demo_supervisor_async.py --scenario cancel --external-server
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import socket
import subprocess
import sys
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

import httpx

from sre_graph.config.tracing import disable_external_tracing
from sre_graph.work.contracts import CancelWork, StartWork, UpdateWork, WorkView
from sre_graph.work.engine import AgentServerEngine
from sre_graph.work.policy import OpenAIDecisionPolicy, RulesPolicy
from sre_graph.work.service import WorkService
from sre_graph.work.store import WorkStore

ROOT = Path(__file__).resolve().parents[1]
ACTOR = "local-demo"


@asynccontextmanager
async def agent_server(args: argparse.Namespace) -> AsyncIterator[None]:
    process = None
    logfile = None
    try:
        if not args.external_server:
            with socket.socket() as probe:
                try:
                    probe.bind(("127.0.0.1", args.agent_port))
                except OSError:
                    raise RuntimeError(
                        "Puerto del Agent Server ocupado; usa --external-server o --agent-port"
                    ) from None
            logpath = ROOT / "i" / "async-pilot-server.log"
            logpath.parent.mkdir(exist_ok=True)
            logfile = logpath.open("w", encoding="utf-8")
            env = {
                **os.environ,
                "PYTHONUTF8": "1",
                "PYTHONIOENCODING": "utf-8",
                "LANGSMITH_TRACING": "false",
                "LANGCHAIN_TRACING_V2": "false",
            }
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "langgraph_cli",
                "dev",
                "--config",
                "examples/langgraph.async.json",
                "--no-browser",
                "--no-reload",
                "--host",
                "127.0.0.1",
                "--port",
                str(args.agent_port),
                "--n-jobs-per-worker",
                "4",
                "--server-log-level",
                "warning",
                cwd=ROOT,
                env=env,
                stdout=logfile,
                stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        async with httpx.AsyncClient(timeout=1) as http:
            for _ in range(80):
                if process and process.returncode is not None:
                    raise RuntimeError("Agent Server no arrancó; revisa i/async-pilot-server.log")
                try:
                    response = await http.get(f"http://127.0.0.1:{args.agent_port}/ok")
                    response.raise_for_status()
                    break
                except httpx.HTTPError:
                    await asyncio.sleep(0.25)
            else:
                raise RuntimeError("Agent Server no disponible en el puerto configurado")
        yield
    finally:
        if process is not None and process.returncode is None:
            # Windows venv launchers spawn another Python process. Terminate only
            # this invocation's process tree, never a process found by port/name.
            if os.name == "nt":
                cleanup = await asyncio.create_subprocess_exec(
                    "taskkill",
                    "/PID",
                    str(process.pid),
                    "/T",
                    "/F",
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                await cleanup.wait()
            else:
                process.terminate()
            try:
                async with asyncio.timeout(10):
                    await process.wait()
            except TimeoutError:
                process.kill()
                await process.wait()
        if logfile:
            logfile.close()


def report(event: str, work: WorkView) -> None:
    print(
        json.dumps({"event": event, **work.model_dump(mode="json")}, ensure_ascii=False), flush=True
    )


async def wait_for(
    service: WorkService, work_id: str, condition: Callable[[WorkView], bool], budget: float = 90
) -> WorkView:
    until = time.monotonic() + budget
    while time.monotonic() < until:
        view = await service.get(work_id, actor=ACTOR)
        if view.status == "failed":
            raise RuntimeError("El grafo falló; revisa el log del Agent Server")
        if condition(view):
            return view
        await asyncio.sleep(0.25)
    raise TimeoutError(f"El trabajo {work_id} no alcanzó el estado esperado")


async def scenario(service: WorkService, args: argparse.Namespace) -> None:
    command = StartWork(
        command_id=f"demo-{uuid4().hex}",
        text="Investiga la latencia de pagos con métricas y logs",
        delay_seconds=6,
    )
    work = await service.start(command, actor=ACTOR)
    report("started", work)
    work = await wait_for(service, work.work_id, lambda view: len(view.children) == 2)
    report("children_started", work)
    if args.scenario == "cancel":
        work = await service.cancel(
            work.work_id,
            CancelWork(command_id=f"cancel-{uuid4().hex}", expected_revision=work.revision),
            actor=ACTOR,
        )
        work = await wait_for(service, work.work_id, lambda view: view.status == "cancelled")
        assert work.result is None
        report("cancelled", work)
        return
    child_threads = {child["agent_name"]: child["thread_id"] for child in work.children}
    work = await service.update(
        work.work_id,
        UpdateWork(
            command_id=f"update-{uuid4().hex}",
            expected_revision=work.revision,
            message="Actualiza el hijo de logs para revisar últimos 5 minutos",
        ),
        actor=ACTOR,
    )
    report("updated", work)
    # Reconstruct the gateway against the same ledger while the server keeps working.
    restarted = WorkService(WorkStore(service.store.path), service.engine, service.policy)
    resumed = await restarted.start(command, actor=ACTOR)
    assert resumed.work_id == work.work_id
    report("gateway_reconnected", resumed)
    final = await wait_for(restarted, work.work_id, lambda view: view.status == "completed")
    assert final.revision == 2
    assert {c["agent_name"]: c["thread_id"] for c in final.children} == child_threads
    if os.environ.get("SRE_ASYNC_MODEL", "offline") == "offline":
        assert any(call["tool"] == "update_async_task" for call in final.decision_trace)
        assert "5m" in (final.result or "")
        assert "metrics" in (final.result or "") and "logs" in (final.result or "")
    report("completed", final)


async def run(args: argparse.Namespace) -> None:
    disable_external_tracing()
    policy = OpenAIDecisionPolicy() if args.policy == "decisions" else RulesPolicy()
    service = WorkService(
        WorkStore(args.ledger), AgentServerEngine(f"http://127.0.0.1:{args.agent_port}"), policy
    )
    async with agent_server(args):
        if args.serve:
            import uvicorn
            from fastapi.responses import HTMLResponse

            from sre_graph.work.api import create_app

            app = create_app(service, actor=ACTOR)
            html = (ROOT / "examples" / "async_pilot.html").read_text(encoding="utf-8")

            @app.get("/", response_class=HTMLResponse)
            async def dashboard() -> str:
                return html

            print(f"Panel: http://127.0.0.1:{args.port} | MCP: /mcp/ | API: /docs", flush=True)
            await uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=args.port)).serve()
        else:
            await scenario(service, args)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serve", action="store_true", help="HTTP + MCP en un solo gateway local")
    parser.add_argument("--scenario", choices=["lifecycle", "cancel"], default="lifecycle")
    parser.add_argument("--external-server", action="store_true")
    parser.add_argument("--agent-port", type=int, default=8011)
    parser.add_argument("--port", type=int, default=8012)
    parser.add_argument("--policy", choices=["rules", "decisions"], default="rules")
    parser.add_argument("--ledger", type=Path, default=ROOT / "i" / "async-pilot.sqlite")
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
