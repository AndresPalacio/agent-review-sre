"""The integrations a deployment knows about, and the clients and tools of one run."""

from __future__ import annotations

import asyncio
from collections.abc import Collection, Iterable
from typing import Any

from langchain_core.tools import BaseTool
from pydantic import ValidationError

from fin_graph.domain.tools import Domain, read_tool_meta
from fin_graph.integrations.base import Integration, VerifyResult, VerifyStatus
from fin_graph.integrations.context import RunContext
from fin_graph.safety.secrets import redact_secrets


class IntegrationRegistry:
    """An explicit list of integrations; nothing is discovered by import side effects."""

    def __init__(
        self, integrations: Iterable[Integration[Any, Any]], *, env_file: str | None = ".env"
    ) -> None:
        self._integrations: dict[str, Integration[Any, Any]] = {}
        for integration in integrations:
            if integration.id in self._integrations:
                raise ValueError(f"duplicate integration id: {integration.id}")
            self._integrations[integration.id] = integration
        self._env_file = env_file

    @property
    def ids(self) -> tuple[str, ...]:
        """Registered integration ids, in registration order."""
        return tuple(self._integrations)

    @property
    def env_file(self) -> str | None:
        return self._env_file

    def extended(self, integrations: Iterable[Integration[Any, Any]]) -> IntegrationRegistry:
        """Return a registry with extra explicit integrations, retaining duplicate checks."""
        return IntegrationRegistry(
            [*self._integrations.values(), *integrations], env_file=self._env_file
        )

    def run_context(self) -> RunContext:
        """Build a client for every configured integration.

        One broken integration never stops the run: its error goes to
        ``unavailable`` (credentials redacted) and its tools stay out.
        """
        clients: dict[str, object] = {}
        unavailable: dict[str, str] = {}
        for integration_id, integration in self._integrations.items():
            try:
                settings = integration.load_settings(self._env_file)
                if settings is not None:
                    clients[integration_id] = integration.build_client(settings)
            except ValidationError as exc:
                # Pydantic can include a truncated input dict in str(exc), losing
                # the credential field name while still exposing part of its value.
                issues = []
                for issue in exc.errors(include_input=False, include_context=False):
                    location = ".".join(str(part) for part in issue["loc"]) or "configuration"
                    issues.append(f"{location} ({issue['type']})")
                unavailable[integration_id] = "ValidationError: invalid settings: " + ", ".join(
                    issues
                )
            except Exception as exc:
                unavailable[integration_id] = redact_secrets(f"{type(exc).__name__}: {exc}")
        return RunContext(clients=clients, unavailable=unavailable)

    def tools(
        self, context: RunContext, domains: Collection[Domain] | None = None
    ) -> list[BaseTool]:
        """Tools of the integrations that have a client in ``context``, optionally by domain."""
        selected: dict[str, BaseTool] = {}
        for integration_id, integration in self._integrations.items():
            if integration_id not in context.clients:
                continue
            for candidate in integration.tools():
                meta = read_tool_meta(candidate)
                if meta is None:
                    raise ValueError(f"{integration_id} tool {candidate.name} carries no ToolMeta")
                if candidate.name in selected:
                    raise ValueError(f"duplicate tool name: {candidate.name}")
                if domains is None or meta.domains & set(domains):
                    selected[candidate.name] = candidate
        return list(selected.values())

    async def verify_all(
        self, context: RunContext, *, timeout_seconds: float = 15.0
    ) -> list[VerifyResult]:
        """Check every registered integration concurrently, in registration order."""
        checks = (
            self._verify(integration, context, timeout_seconds)
            for integration in self._integrations.values()
        )
        return list(await asyncio.gather(*checks))

    @staticmethod
    async def _verify(
        integration: Integration[Any, Any], context: RunContext, timeout_seconds: float
    ) -> VerifyResult:
        if integration.id in context.unavailable:
            return VerifyResult(
                integration.id, VerifyStatus.FAILED, context.unavailable[integration.id]
            )
        client = context.clients.get(integration.id)
        if client is None:
            return VerifyResult(integration.id, VerifyStatus.NOT_CONFIGURED, "not configured")
        try:
            async with asyncio.timeout(timeout_seconds):
                return await integration.verify(client)
        except TimeoutError:
            return VerifyResult(
                integration.id, VerifyStatus.FAILED, f"no answer within {timeout_seconds:g} seconds"
            )
        except Exception as exc:
            return VerifyResult(integration.id, VerifyStatus.FAILED, redact_secrets(str(exc)))
