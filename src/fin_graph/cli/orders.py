"""Paper orders from the terminal: start, review, approve or reject, list, cancel and settle.

Every order runs in its own orders-graph thread (checkpoints in ``orders.sqlite`` next to the
paper ledger), so an order proposed by ``analyze --propose`` waits for a decision across CLI
sessions. Deciding here acts as the local user with the approver role.
"""

from __future__ import annotations

import asyncio
import getpass
import json
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Annotated, Any

import typer
from langgraph.types import Command
from rich.console import Console
from rich.table import Table

from fin_graph.config.settings import PersistenceBackend, Settings
from fin_graph.domain.fills import OrderStatus
from fin_graph.domain.orders import OrderAction, ProposedOrder, client_order_id
from fin_graph.domain.requests.model import normalize_ticker
from fin_graph.graphs.orders.desk import OrderDesk
from fin_graph.graphs.orders.graph import PLACE_ORDER, make_order_graph
from fin_graph.integrations.broker.paper.broker import PaperBroker, PaperBrokerError
from fin_graph.integrations.broker.paper.runtime import ledger_path, open_trading_context
from fin_graph.integrations.catalog import default_registry
from fin_graph.integrations.context import RunContext
from fin_graph.persistence.checkpointer import open_checkpointer
from fin_graph.safety.approval import ApprovalPolicy
from fin_graph.safety.secrets import redact_secrets

app = typer.Typer(
    help="Órdenes paper: proponer, aprobar o rechazar, listar, cancelar y liquidar.",
    no_args_is_help=True,
)
console = Console()


@dataclass(frozen=True)
class OrdersRuntime:
    graph: Any
    broker: PaperBroker
    context: RunContext
    saver: Any

    def approver(self) -> RunContext:
        return replace(self.context, actor=f"local:{getpass.getuser()}", role="approver")


def thread_for(order: ProposedOrder) -> str:
    return f"order-{order.client_order_id}"


@asynccontextmanager
async def open_orders_runtime(
    settings: Settings | None = None, context: RunContext | None = None
) -> AsyncIterator[OrdersRuntime]:
    """The broker, the order graph and its checkpoints, beside the paper ledger."""
    active = settings or Settings()
    base = context or default_registry().run_context()
    threads = ledger_path(active).with_name("orders.sqlite")
    checkpoints = Settings(persistence_backend=PersistenceBackend.SQLITE, sqlite_path=str(threads))
    async with (
        open_checkpointer(checkpoints) as saver,
        open_trading_context(active, base) as (broker, trading),
    ):
        graph = make_order_graph(
            OrderDesk(broker), ApprovalPolicy.from_settings(active), checkpointer=saver
        )
        yield OrdersRuntime(graph, broker, trading, saver)


async def submit(runtime: OrdersRuntime, order: ProposedOrder) -> dict[str, Any]:
    """Start the order's thread; it stops at the approval unless the policy auto-approves."""
    config = {"configurable": {"thread_id": thread_for(order)}}
    state = await runtime.graph.aget_state(config)
    if state.values:
        return dict(state.values) | {
            "__interrupt__": [i for t in state.tasks for i in t.interrupts]
        }
    result: dict[str, Any] = await runtime.graph.ainvoke(
        {"order": order.model_dump(mode="json")}, config, context=runtime.context
    )
    return result


async def pending(runtime: OrdersRuntime) -> list[tuple[str, dict[str, Any]]]:
    """Order threads waiting for a decision, oldest first."""
    seen: dict[str, None] = {}
    async for item in runtime.saver.alist(None):
        thread = item.config["configurable"]["thread_id"]
        if isinstance(thread, str) and thread.startswith("order-"):
            seen.setdefault(thread, None)
    waiting = []
    for thread in reversed(list(seen)):
        state = await runtime.graph.aget_state({"configurable": {"thread_id": thread}})
        requests = [item.value for task in state.tasks for item in task.interrupts]
        if requests:
            waiting.append((thread, requests[0]))
    return waiting


async def decide(
    runtime: OrdersRuntime, thread: str, choice: str, quantity: int | None = None
) -> dict[str, Any]:
    decision: dict[str, Any] = {"type": choice}
    if choice == "edit":
        if quantity is None:
            raise ValueError("edit needs --quantity")
        decision["edited_action"] = {"name": PLACE_ORDER, "args": {"quantity": quantity}}
    result: dict[str, Any] = await runtime.graph.ainvoke(
        Command(resume={"decisions": [decision]}),
        {"configurable": {"thread_id": thread}},
        context=runtime.approver(),
    )
    return result


def outcome_line(result: dict[str, Any]) -> str:
    """One line for the terminal: the outcome, or what is waiting and why."""
    waiting = result.get("__interrupt__") or ()
    if waiting:
        request = waiting[0].value if hasattr(waiting[0], "value") else waiting[0]
        text = request["action_requests"][0]["description"]
        denial = request.get("denial") or result.get("denial")
        return f"Esperando aprobación: {text}" + (f" (rechazada antes: {denial})" if denial else "")
    execution = result.get("execution") or {}
    reason = execution.get("reason") or "; ".join(
        (result.get("preview") or {}).get("violations", [])
    )
    return f"{result.get('outcome', 'desconocido')}: {reason}".strip(": ")


def _run(coroutine: Any) -> Any:
    try:
        return asyncio.run(coroutine)
    except (ValueError, PaperBrokerError, PermissionError) as exc:
        console.print(f"Error: {redact_secrets(str(exc))}", markup=False)
        raise typer.Exit(1) from None


@app.command("new")
def new_order(
    ticker: Annotated[str, typer.Option("--ticker")],
    action: Annotated[str, typer.Option("--action", help="buy, sell, short o cover.")],
    quantity: Annotated[int, typer.Option("--quantity", min=1)],
    reason: Annotated[str, typer.Option("--reason")] = "orden manual",
) -> None:
    """Propone una orden paper manual al precio actual; queda pendiente de aprobación."""

    async def main() -> str:
        async with open_orders_runtime() as runtime:
            symbol, side = normalize_ticker(ticker), OrderAction(action.lower())
            quote = await runtime.broker.quotes(symbol)
            if quote is None:
                raise ValueError(f"sin cotización para {symbol}")
            now = datetime.now(UTC)
            order = ProposedOrder(
                client_order_id=client_order_id(
                    f"manual:{now.isoformat()}", symbol, side, quantity, now.date()
                ),
                ticker=symbol,
                action=side,
                quantity=quantity,
                reference_price=quote,
                as_of=now.date(),
                reasoning=reason,
            )
            result = await submit(runtime, order)
            return f"{thread_for(order)}\n{outcome_line(result)}"

    console.print(_run(main()), markup=False)


@app.command("pending")
def pending_command() -> None:
    """Lista las órdenes que esperan aprobación."""

    async def main() -> list[tuple[str, dict[str, Any]]]:
        async with open_orders_runtime() as runtime:
            return await pending(runtime)

    rows = _run(main())
    if not rows:
        console.print("No hay órdenes pendientes.")
    for thread, request in rows:
        console.print(f"{thread}\n  {request['action_requests'][0]['description']}", markup=False)


@app.command("decide")
def decide_command(
    thread: Annotated[str, typer.Argument(help="Hilo de la orden (order-ord_...).")],
    choice: Annotated[str, typer.Argument(help="approve, reject o edit.")],
    quantity: Annotated[int | None, typer.Option("--quantity", min=1)] = None,
) -> None:
    """Aprueba, rechaza o cambia la cantidad de una orden pendiente."""
    if choice not in {"approve", "reject", "edit"}:
        raise typer.BadParameter("usa approve, reject o edit")

    async def main() -> str:
        async with open_orders_runtime() as runtime:
            return outcome_line(await decide(runtime, thread, choice, quantity))

    console.print(_run(main()), markup=False)


@app.command("list")
def list_command(
    status: Annotated[str | None, typer.Option("--status")] = None,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Muestra las órdenes del libro paper."""

    async def main() -> list[Any]:
        async with open_orders_runtime() as runtime:
            return await runtime.broker.orders(status=OrderStatus(status) if status else None)

    executions = _run(main())
    if as_json:
        console.print(
            json.dumps([item.model_dump(mode="json") for item in executions], indent=2),
            markup=False,
        )
        return
    table = Table("Orden", "Ticker", "Acción", "Cant.", "Estado", "Precio", "P&L", "Motivo")
    for item in executions:
        order = item.order
        table.add_row(
            order.client_order_id,
            order.ticker,
            order.action.value,
            str(order.quantity),
            item.status.value,
            f"{item.fill_price:,.2f}" if item.fill_price else "—",
            f"{item.realized_pnl:,.2f}",
            item.reason[:60],
        )
    console.print(table)


@app.command("cancel")
def cancel_command(client_order_id: Annotated[str, typer.Argument()]) -> None:
    """Cancela una orden en cola (fuera de horario de mercado) antes de que se ejecute."""

    async def main() -> str:
        async with open_orders_runtime() as runtime:
            execution = await runtime.broker.cancel(client_order_id)
            return f"{execution.client_order_id}: {execution.status.value}"

    console.print(_run(main()), markup=False)


@app.command("settle")
def settle_command() -> None:
    """Ejecuta las órdenes en cola si el mercado está abierto."""

    async def main() -> list[Any]:
        async with open_orders_runtime() as runtime:
            return await runtime.broker.settle()

    settled = _run(main())
    console.print(
        "\n".join(f"{item.client_order_id}: {item.status.value}" for item in settled)
        or "Mercado cerrado o nada en cola.",
        markup=False,
    )


def portfolio_rows(
    portfolio: Any, quotes: dict[str, float | None]
) -> tuple[list[Sequence[str]], float]:
    """Rows of ticker, shares, cost, price, value and open P&L; plus the net liquidation value."""
    rows: list[Sequence[str]] = []
    prices = {ticker: price for ticker, price in quotes.items() if price}
    for ticker, held in sorted(portfolio.positions.items()):
        price = quotes.get(ticker)
        if held.long:
            pnl = (price - held.long_cost_basis) * held.long if price else None
            rows.append(_row(ticker, held.long, held.long_cost_basis, price, pnl))
        if held.short:
            pnl = (held.short_cost_basis - price) * held.short if price else None
            rows.append(_row(f"{ticker} (corto)", -held.short, held.short_cost_basis, price, pnl))
    return rows, portfolio.net_liquidation_value(prices)


def _row(
    label: str, shares: int, cost: float, price: float | None, pnl: float | None
) -> Sequence[str]:
    return (
        label,
        str(shares),
        f"{cost:,.2f}",
        f"{price:,.2f}" if price else "—",
        f"{shares * price:,.2f}" if price else "—",
        f"{pnl:+,.2f}" if pnl is not None else "—",
    )


def show_portfolio(as_json: bool = False) -> None:
    async def main() -> tuple[Any, dict[str, float | None]]:
        async with open_orders_runtime() as runtime:
            book = await runtime.broker.portfolio()
            quotes = {ticker: await runtime.broker.quotes(ticker) for ticker in book.positions}
            return book, quotes

    book, quotes = _run(main())
    if as_json:
        console.print(book.model_dump_json(indent=2), markup=False)
        return
    rows, nlv = portfolio_rows(book, quotes)
    table = Table("Ticker", "Acciones", "Coste medio", "Precio", "Valor", "P&L abierto")
    for row in rows:
        table.add_row(*row)
    console.print(table)
    console.print(
        f"Efectivo {book.cash:,.2f} {book.currency} · garantía de cortos {book.margin_used:,.2f}"
        f" · valor neto {nlv:,.2f}. Cartera paper: no es dinero real.",
        markup=False,
    )
