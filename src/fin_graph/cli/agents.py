"""Read-only navigation for the executable multi-agent catalog."""

from __future__ import annotations

import json
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from fin_graph.agents.catalog import AgentDescriptor, agent_by_id, agent_catalog

app = typer.Typer(help="Inspect agents, relationships, tools and limits without building models.")
console = Console()


@app.command("list")
def list_agents(
    as_json: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """List every catalogued supervisor, subagent and optional optimizer."""
    items = agent_catalog()
    if as_json:
        typer.echo(json.dumps([item.to_dict() for item in items], ensure_ascii=False))
        return
    table = Table(title="fin-graph agents")
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Kind", no_wrap=True)
    table.add_column("Tools", no_wrap=True)
    for item in items:
        table.add_row(item.id, item.kind, item.tool_policy)
    console.print(table)


def _descriptor_table(item: AgentDescriptor) -> Table:
    table = Table(title=item.display_name, show_header=False)
    table.add_column("Field", style="cyan", no_wrap=True)
    table.add_column("Value")
    for label, value in (
        ("id", item.id),
        ("kind", item.kind),
        ("purpose", item.purpose),
        ("factory", item.factory_path),
        ("children", ", ".join(item.children) or "—"),
        ("tool policy", item.tool_policy),
        ("middleware", ", ".join(item.middleware) or "—"),
        ("limits", ", ".join(item.limits) or "—"),
        ("activation", item.activation or "—"),
        ("surfaces", ", ".join(item.surfaces) or "—"),
    ):
        table.add_row(label, value)
    return table


@app.command()
def describe(
    agent_id: Annotated[str, typer.Argument(help="Stable catalog ID.")],
    as_json: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")] = False,
) -> None:
    """Describe one catalog entry without resolving its model factory."""
    try:
        item = agent_by_id(agent_id)
    except KeyError:
        typer.echo(f"Unknown agent id: {agent_id}", err=True)
        raise typer.Exit(2) from None
    if as_json:
        typer.echo(json.dumps(item.to_dict(), ensure_ascii=False))
        return
    console.print(_descriptor_table(item))
