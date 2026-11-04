from __future__ import annotations

from typing import Any

from langchain.tools import ToolRuntime
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.graph.state import CompiledStateGraph

from fin_graph.agents.common.factory import make_agent
from fin_graph.agents.common.testing.scripted_model import ScriptedChatModel, tool_call_message
from fin_graph.domain.tools import Domain, EvidenceType, ToolMeta, read_tool_meta
from fin_graph.integrations.base import IntegrationError
from fin_graph.integrations.context import RunContext, require_client
from fin_graph.integrations.tooling import integration_tool

META = ToolMeta(
    integration="market_data",
    domains=frozenset({Domain.FUNDAMENTALS}),
    evidence_type=EvidenceType.PRICES,
)


class MarketDataClient:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail

    async def quotes(self, ticker: str) -> list[dict[str, Any]]:
        if self.fail:
            raise IntegrationError("market data API error 503: try again later")
        return [{"ticker": ticker, "price": 189.2}]


@integration_tool(META)
async def market_quotes(ticker: str, runtime: ToolRuntime[RunContext]) -> tuple[str, Any]:
    """Get current quotes for a ticker.

    Args:
        ticker: Public market ticker symbol.
    """
    quotes = await require_client(runtime, "market_data", MarketDataClient).quotes(ticker)
    return f"{len(quotes)} quote(s)", quotes


def _agent(model: ScriptedChatModel) -> CompiledStateGraph[Any, Any, Any, Any]:
    return make_agent(name="market", model=model, tools=[market_quotes], context_schema=RunContext)


def _script() -> ScriptedChatModel:
    return ScriptedChatModel(
        responses=[tool_call_message("market_quotes", {"ticker": "AAPL"}, "c1"), AIMessage("ok")]
    )


def test_the_docstring_describes_the_tool_and_hides_the_runtime() -> None:
    schema = market_quotes.tool_call_schema.model_json_schema()

    assert market_quotes.description == "Get current quotes for a ticker."
    assert list(schema["properties"]) == ["ticker"]
    assert schema["properties"]["ticker"]["description"] == "Public market ticker symbol."


def test_the_tool_carries_its_metadata() -> None:
    assert read_tool_meta(market_quotes) == META


async def test_content_goes_to_the_model_and_data_to_the_artifact() -> None:
    model = _script()

    result = await _agent(model).ainvoke(
        {"messages": [HumanMessage("AAPL quote?")]},
        context=RunContext(clients={"market_data": MarketDataClient()}),
    )

    message = next(m for m in result["messages"] if isinstance(m, ToolMessage))
    assert message.content == "1 quote(s)"
    assert message.artifact == [{"ticker": "AAPL", "price": 189.2}]


async def test_an_integration_error_reaches_the_model_and_the_run_continues() -> None:
    model = _script()

    result = await _agent(model).ainvoke(
        {"messages": [HumanMessage("AAPL quote?")]},
        context=RunContext(clients={"market_data": MarketDataClient(fail=True)}),
    )

    message = next(m for m in result["messages"] if isinstance(m, ToolMessage))
    assert message.status == "error"
    assert message.content == "market data API error 503: try again later"
    assert len(model.calls) == 2


async def test_a_run_without_integration_clients_gets_a_readable_error() -> None:
    result = await _agent(_script()).ainvoke({"messages": [HumanMessage("AAPL quote?")]})

    message = next(m for m in result["messages"] if isinstance(m, ToolMessage))
    assert message.status == "error"
    assert "started without integration clients" in str(message.content)
