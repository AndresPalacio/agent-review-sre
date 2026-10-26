"""Agent tools over the paper broker: read the portfolio and orders, cancel a queued order.

There is deliberately no tool that places an order. An order is placed only by the orders graph
(``graphs/orders``), after its limits check and a human approval, so a model can never reach the
broker's ``place`` directly. Cancelling only withdraws an order that has not filled yet; it is
still marked mutating, so the approval middleware asks a human first.
"""

from __future__ import annotations

from typing import Any

from langchain.tools import ToolRuntime
from langchain_core.tools import BaseTool

from fin_graph.domain.fills import OrderStatus, PaperExecution
from fin_graph.domain.tools import Domain, EvidenceType, SideEffectLevel, ToolMeta
from fin_graph.integrations.base import IntegrationError
from fin_graph.integrations.broker.paper.broker import PaperBroker, PaperBrokerError
from fin_graph.integrations.context import RunContext, require_client
from fin_graph.integrations.payload import tool_payload
from fin_graph.integrations.tooling import integration_tool

INTEGRATION = "paper_broker"
_READ = ToolMeta(
    integration=INTEGRATION,
    domains=frozenset({Domain.PORTFOLIO}),
    evidence_type=EvidenceType.POSITIONS,
)
_CANCEL = ToolMeta(
    integration=INTEGRATION,
    domains=frozenset({Domain.PORTFOLIO}),
    evidence_type=EvidenceType.POSITIONS,
    side_effect_level=SideEffectLevel.MUTATING,
    idempotent=True,
)


def _broker(runtime: ToolRuntime[RunContext]) -> PaperBroker:
    return require_client(runtime, INTEGRATION, PaperBroker)


def _summary(execution: PaperExecution) -> dict[str, Any]:
    order = execution.order
    return {
        "client_order_id": order.client_order_id,
        "ticker": order.ticker,
        "action": order.action.value,
        "quantity": order.quantity,
        "status": execution.status.value,
        "fill_price": execution.fill_price,
        "realized_pnl": execution.realized_pnl,
        "reason": execution.reason,
    }


@integration_tool(_READ)
async def get_paper_portfolio(runtime: ToolRuntime[RunContext]) -> tuple[str, dict[str, Any]]:
    """Read the paper portfolio: free cash, short collateral and each position with its cost."""
    portfolio = await _broker(runtime).portfolio()
    data = portfolio.model_dump(mode="json")
    return tool_payload(data), data


@integration_tool(_READ)
async def list_paper_orders(
    runtime: ToolRuntime[RunContext], status: str = ""
) -> tuple[str, list[dict[str, Any]]]:
    """List paper orders, oldest first.

    Args:
        status: Only orders in this state: queued, filled, cancelled or rejected. Empty for all.
    """
    try:
        wanted = OrderStatus(status) if status else None
    except ValueError as exc:
        raise IntegrationError(f"unknown status {status!r}") from exc
    rows = [_summary(item) for item in await _broker(runtime).orders(status=wanted)]
    return tool_payload(rows), rows


@integration_tool(_CANCEL)
async def cancel_paper_order(
    runtime: ToolRuntime[RunContext], client_order_id: str
) -> tuple[str, dict[str, Any]]:
    """Cancel a queued paper order before it fills. A filled order is undone by its inverse.

    Args:
        client_order_id: The id the order was approved with (``ord_...``).
    """
    try:
        execution = await _broker(runtime).cancel(client_order_id.strip())
    except PaperBrokerError as exc:
        raise IntegrationError(str(exc)) from exc
    data = _summary(execution)
    return tool_payload(data), data


PAPER_BROKER_TOOLS: tuple[BaseTool, ...] = (
    get_paper_portfolio,
    list_paper_orders,
    cancel_paper_order,
)
