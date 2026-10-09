"""Bounded Helm reads using allowlisted argv, without a shell."""

from __future__ import annotations

import asyncio
import json
from contextlib import suppress
from typing import Any

import yaml
from pydantic import ValidationError

from sre_graph.integrations.base import IntegrationError
from sre_graph.integrations.http import MAX_RESPONSE_BYTES
from sre_graph.integrations.wave2 import arguments as a
from sre_graph.integrations.wave2.client import sanitize
from sre_graph.integrations.wave2.settings import HelmSettings

HELM_OPERATIONS: dict[str, type[a.Arguments]] = {
    "releases": a.Page,
    "status": a.Identifier,
    "history": a.ReleaseHistory,
    "values": a.Release,
    "manifest": a.Release,
}


class HelmClient:
    def __init__(self, settings: HelmSettings) -> None:
        self.settings = settings

    async def _run(self, command: list[str]) -> str:
        flags = ["--namespace", self.settings.namespace]
        if self.settings.context:
            flags.extend(["--kube-context", self.settings.context])
        if self.settings.kubeconfig:
            flags.extend(["--kubeconfig", self.settings.kubeconfig])
        process: asyncio.subprocess.Process | None = None

        async def read(stream: asyncio.StreamReader | None) -> bytes:
            if stream is None:
                raise IntegrationError("Helm output pipe is unavailable")
            result = bytearray()
            while chunk := await stream.read(65536):
                result.extend(chunk)
                if len(result) > MAX_RESPONSE_BYTES:
                    raise IntegrationError("Helm output exceeds the byte limit")
            return bytes(result)

        tasks: list[asyncio.Task[bytes]] = []
        try:
            async with asyncio.timeout(self.settings.request_timeout_seconds):
                process = await asyncio.create_subprocess_exec(
                    self.settings.executable,
                    *command,
                    *flags,
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                tasks = [
                    asyncio.create_task(read(process.stdout)),
                    asyncio.create_task(read(process.stderr)),
                ]
                stdout, _ = await asyncio.gather(*tasks)
                code = await process.wait()
                if code != 0:
                    raise IntegrationError(f"Helm read exited with code {code}")
                return stdout.decode("utf-8")
        except (OSError, UnicodeError, TimeoutError) as exc:
            raise IntegrationError(f"Helm read failed: {type(exc).__name__}") from None
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            if process and process.returncode is None:
                with suppress(ProcessLookupError):
                    process.kill()
                await process.wait()

    async def call(self, operation: str, **kwargs: Any) -> dict[str, Any]:
        schema = HELM_OPERATIONS.get(operation)
        if schema is None:
            raise IntegrationError("unsupported Helm read operation")
        try:
            args = schema.model_validate(kwargs).model_dump()
        except ValidationError:
            raise IntegrationError("invalid Helm read arguments") from None
        if args.get("cursor"):
            raise IntegrationError("Helm page cursors are not supported")
        name = args.get("identifier", "")
        limit = args.get("limit", 100)
        revision = ["--revision", str(args["revision"])] if args.get("revision") else []
        commands = {
            "releases": ["list", "--all", "--max", str(limit), "--output", "json"],
            "status": ["status", name, "--output", "json"],
            "history": ["history", name, "--max", str(limit), "--output", "json"],
            "values": ["get", "values", name, *revision, "--output", "json"],
            "manifest": ["get", "manifest", name, *revision],
        }
        output = await self._run(commands[operation])
        try:
            if operation == "manifest":
                if any(isinstance(event, yaml.AliasEvent) for event in yaml.parse(output)):
                    raise IntegrationError("Helm YAML aliases are not supported")
                rows = list(yaml.safe_load_all(output))
                if any(not isinstance(row, dict) for row in rows):
                    raise ValueError("invalid manifest")
            else:
                data = json.loads(output)
                if operation == "values" and data is None:
                    data = {}
                rows = data if isinstance(data, list) else [data]
                if any(not isinstance(row, dict) for row in rows):
                    raise ValueError("invalid output")
        except (ValueError, yaml.YAMLError, RecursionError):
            raise IntegrationError("Helm returned invalid structured output") from None
        try:
            safe = sanitize(rows[:limit])
        except RecursionError:
            raise IntegrationError("Helm output nesting exceeds the supported depth") from None
        return {
            "source": "helm",
            "items": safe,
            "next_cursor": None,
            "truncated": len(rows) >= limit
            if operation in {"releases", "history"}
            else len(rows) > limit,
        }

    async def verify(self) -> None:
        await self.call("releases", limit=1)
