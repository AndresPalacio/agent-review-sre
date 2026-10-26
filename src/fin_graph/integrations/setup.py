"""Validated atomic dotenv setup; proposed values never inherit ambient overrides."""

from __future__ import annotations

import importlib
import io
import json
import os
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

from dotenv import dotenv_values
from dotenv.parser import parse_stream
from pydantic_settings import BaseSettings

_BUILTINS = {
    "yahoo": ("yahoo", "YahooSettings"),
    "financial_datasets": ("financial_datasets", "FinancialDatasetsSettings"),
    "kubernetes": ("kubernetes", "KubernetesSettings"),
    "prometheus": ("prometheus", "PrometheusSettings"),
    "mimir": ("prometheus", "MimirSettings"),
    "alertmanager": ("alertmanager", "AlertmanagerSettings"),
    "grafana": ("grafana", "GrafanaSettings"),
    "loki": ("loki", "LokiSettings"),
    "tempo": ("tempo", "TempoSettings"),
    "datadog": ("datadog", "DatadogSettings"),
    "newrelic": ("newrelic", "NewRelicSettings"),
    "aws": ("aws", "AwsSettings"),
    "sentry": ("sentry", "SentrySettings"),
    "github": ("github", "GitHubSettings"),
}


def settings_models() -> dict[str, type[BaseSettings]]:
    models = {
        name: getattr(importlib.import_module(f"fin_graph.integrations.{module}.settings"), cls)
        for name, (module, cls) in _BUILTINS.items()
    }
    try:
        from fin_graph.integrations.wave2.settings import WAVE2_SETTINGS

        models.update(WAVE2_SETTINGS)
    except ImportError:
        pass
    return models


def update_env(
    integration: str,
    proposed: dict[str, Any],
    env_file: Path,
    *,
    dry_run: bool = False,
    verify: Callable[[BaseSettings], bool] | None = None,
) -> dict[str, Any]:
    models = settings_models()
    if integration not in models:
        raise ValueError("unknown integration")
    model = models[integration]
    if not proposed or set(proposed) - set(model.model_fields):
        raise ValueError("unknown or empty setup fields")
    if env_file.is_symlink():
        raise ValueError("symlink env files are refused")
    text = env_file.read_text(encoding="utf-8") if env_file.exists() else ""
    prefix = str(model.model_config.get("env_prefix", ""))
    existing = dotenv_values(stream=io.StringIO(text), interpolate=False)
    values: dict[str, Any] = {
        name: existing[prefix + name.upper()]
        for name in model.model_fields
        if prefix + name.upper() in existing
    }
    values.update(proposed)

    # model_validate does not consult BaseSettings sources: no process env can hide bad proposals.
    def sources(
        cls: type[BaseSettings], /, settings_cls: type[BaseSettings], **kwargs: Any
    ) -> tuple[Any, ...]:
        return (kwargs["init_settings"],)

    isolated = cast(
        type[BaseSettings],
        type(
            "IsolatedSetupSettings", (model,), {"settings_customise_sources": classmethod(sources)}
        ),
    )
    validated = isolated(**values)
    if verify is not None and not verify(validated):
        raise ValueError("verification failed; configuration unchanged")
    updates = {prefix + name.upper(): _serialize(value) for name, value in proposed.items()}
    summary = {"integration": integration, "fields": sorted(updates), "dry_run": dry_run}
    if dry_run:
        return summary
    bindings = list(parse_stream(io.StringIO(text)))
    if any(binding.error for binding in bindings):
        raise ValueError("invalid existing dotenv syntax")
    seen = set()
    output = []
    for binding in bindings:
        key = binding.key
        if key in updates:
            if key not in seen:
                output.append(f"{key}={updates[key]}\n")
            seen.add(key)
        else:
            output.append(binding.original.string)
    if output and not output[-1].endswith("\n"):
        output[-1] += "\n"
    output.extend(f"{key}={value}\n" for key, value in updates.items() if key not in seen)
    descriptor, temporary = tempfile.mkstemp(prefix=".fin-setup-", dir=env_file.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            handle.write("".join(output))
            handle.flush()
            os.fsync(handle.fileno())
        current_text = env_file.read_text(encoding="utf-8") if env_file.exists() else ""
        if current_text != text:
            raise ValueError("configuration changed during setup; retry")
        os.replace(temporary, env_file)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return summary


def _serialize(value: Any) -> str:
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, (int, float)):
        return str(value)
    if value is None:
        return ""
    text = str(value)
    if "\x00" in text:
        raise ValueError("invalid dotenv value")
    if "${" in text:
        raise ValueError("dotenv interpolation is refused")
    return json.dumps(text, ensure_ascii=False)


async def verify_proposed(integration: str, settings: BaseSettings) -> bool:
    """Verify only the validated proposed integration, never ambient credentials."""
    import asyncio

    from fin_graph.integrations.base import VerifyStatus

    if integration in _BUILTINS:
        module, settings_name = _BUILTINS[integration]
        adapter = getattr(
            importlib.import_module(f"fin_graph.integrations.{module}.integration"),
            settings_name.replace("Settings", "Integration"),
        )()
    else:
        from fin_graph.integrations.wave2 import wave2_integrations

        adapter = next(item for item in wave2_integrations() if item.id == integration)
    client = adapter.build_client(settings)
    async with asyncio.timeout(20):
        result = await adapter.verify(client)
    return bool(result.status == VerifyStatus.OK)
