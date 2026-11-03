"""V2 JSON adapter over the existing durable paper broker."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fin_graph.domain.fills import PaperExecution
from fin_graph.domain.orders import ProposedOrder
from fin_graph.integrations.broker.paper.broker import PaperBroker


@dataclass(frozen=True)
class V2PaperBroker:
    native: PaperBroker

    async def place(self, order: ProposedOrder) -> PaperExecution:
        return await self.native.place(order)

    async def snapshot(self) -> dict[str, Any]:
        portfolio = await self.native.portfolio()
        executions = await self.native.orders()
        positions: dict[str, dict[str, float | int]] = {}
        for ticker, held in portfolio.positions.items():
            row: dict[str, float | int] = {
                "shares": held.long,
                "avg_price": held.long_cost_basis,
            }
            if held.short:
                row.update(
                    {
                        "short_shares": held.short,
                        "short_avg_price": held.short_cost_basis,
                    }
                )
            positions[ticker] = row
        return {
            "cash": portfolio.cash,
            "positions": positions,
            "trade_history": [item.model_dump(mode="json") for item in executions],
            "margin_used": portfolio.margin_used,
            "currency": portfolio.currency,
        }
