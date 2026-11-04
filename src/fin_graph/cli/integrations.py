"""Integration discovery, read verification and secret-safe configuration."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console

from fin_graph.integrations.catalog import default_registry
from fin_graph.integrations.setup import settings_models, update_env, verify_proposed

app = typer.Typer(no_args_is_help=True)
console = Console()


async def _registry(env_file: Path, mcp_config: Path | None) -> Any:
    registry = default_registry(str(env_file))
    from fin_graph.integrations.mcp.runtime import with_configured_mcp

    return (
        await with_configured_mcp(registry, config_path=mcp_config)
        if mcp_config
        else await with_configured_mcp(registry)
    )


@app.command("list")
def list_integrations(
    env_file: Annotated[Path, typer.Option("--env-file")] = Path(".env"),
    mcp_config: Annotated[Path | None, typer.Option("--mcp-config")] = None,
) -> None:
    try:
        registry = asyncio.run(_registry(env_file, mcp_config))
        context = registry.run_context()
        for name in registry.ids:
            state = (
                "configured"
                if name in context.clients
                else "invalid"
                if name in context.unavailable
                else "not_configured"
            )
            console.print(f"{name}: {state}")
    except Exception as exc:
        console.print(f"Integration listing failed ({type(exc).__name__})")
        raise typer.Exit(1) from None


@app.command("verify")
def verify_integrations(
    env_file: Annotated[Path, typer.Option("--env-file")] = Path(".env"),
    mcp_config: Annotated[Path | None, typer.Option("--mcp-config")] = None,
) -> None:
    async def verify() -> Any:
        registry = await _registry(env_file, mcp_config)
        context = registry.run_context()
        try:
            return await registry.verify_all(context)
        finally:
            await context.aclose()

    try:
        results = asyncio.run(verify())
        for result in results:
            console.print(f"{result.integration}: {result.status}")
        if any(str(result.status) == "failed" for result in results):
            raise typer.Exit(1)
    except typer.Exit:
        raise
    except Exception as exc:
        console.print(f"Integration verification failed ({type(exc).__name__})")
        raise typer.Exit(1) from None


@app.command("setup")
def setup(
    integration: str,
    env_file: Annotated[Path, typer.Option("--env-file")],
    input_file: Annotated[Path | None, typer.Option("--input-file")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
    verify: Annotated[bool, typer.Option("--verify")] = False,
) -> None:
    try:
        models = settings_models()
        if integration not in models:
            raise ValueError("unknown integration")
        model = models[integration]
        if input_file:
            if input_file.stat().st_size > 65536:
                raise ValueError("setup file too large")
            proposed = json.loads(input_file.read_text(encoding="utf-8"))
            if not isinstance(proposed, dict):
                raise ValueError("setup input must be an object")
            for name, value in proposed.items():
                if isinstance(value, dict) and set(value) == {"env"}:
                    reference = value["env"]
                    if not isinstance(reference, str) or reference not in os.environ:
                        raise ValueError("missing environment reference")
                    proposed[name] = os.environ[reference]
                elif (
                    name in model.model_fields
                    and "SecretStr" in str(model.model_fields[name].annotation)
                    and value is not None
                ):
                    raise ValueError("secret fields require environment references")
        else:
            proposed = {"enabled": True}
            for name, field in model.model_fields.items():
                if name == "enabled":
                    continue
                secret = "SecretStr" in str(field.annotation)
                value = typer.prompt(name, default="", hide_input=secret, show_default=False)
                if value:
                    proposed[name] = value
        summary = update_env(
            integration,
            proposed,
            env_file,
            dry_run=dry_run,
            verify=(lambda settings: asyncio.run(verify_proposed(integration, settings)))
            if verify
            else None,
        )
        console.print(json.dumps(summary), markup=False)
    except Exception as exc:
        console.print(f"Setup failed ({type(exc).__name__}); configuration unchanged")
        raise typer.Exit(2) from None
