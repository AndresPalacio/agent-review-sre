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
    integration="pager",
    domains=frozenset({Domain.PORTFOLIO}),
    evidence_type=EvidenceType.CHANGES,
)


class PagerClient:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail

    async def incidents(self, service: str) -> list[dict[str, Any]]:
        if self.fail:
            raise IntegrationError("pager API error 503: try again later")
        return [{"id": "P1", "service": service}]


@integration_tool(META)
async def pager_incidents(service: str, runtime: ToolRuntime[RunContext]) -> tuple[str, Any]:
    """List open incidents for a service.

    Args:
        service: Service name as the pager knows it.
    """
    incidents = await require_client(runtime, "pager", PagerClient).incidents(service)
    return f"{len(incidents)} open incident(s)", incidents


def _agent(model: ScriptedChatModel) -> CompiledStateGraph[Any, Any, Any, Any]:
    return make_agent(name="pager", model=model, tools=[pager_incidents], context_schema=RunContext)


def _script() -> ScriptedChatModel:
    return ScriptedChatModel(
        responses=[tool_call_message("pager_incidents", {"service": "api"}, "c1"), AIMessage("ok")]
    )


def test_the_docstring_describes_the_tool_and_hides_the_runtime() -> None:
    schema = pager_incidents.tool_call_schema.model_json_schema()

    assert pager_incidents.description == "List open incidents for a service."
    assert list(schema["properties"]) == ["service"]
    assert schema["properties"]["service"]["description"] == "Service name as the pager knows it."


def test_the_tool_carries_its_metadata() -> None:
    assert read_tool_meta(pager_incidents) == META


async def test_content_goes_to_the_model_and_data_to_the_artifact() -> None:
    model = _script()

    result = await _agent(model).ainvoke(
        {"messages": [HumanMessage("incidents?")]},
        context=RunContext(clients={"pager": PagerClient()}),
    )

    message = next(m for m in result["messages"] if isinstance(m, ToolMessage))
    assert message.content == "1 open incident(s)"
    assert message.artifact == [{"id": "P1", "service": "api"}]


async def test_an_integration_error_reaches_the_model_and_the_run_continues() -> None:
    model = _script()

    result = await _agent(model).ainvoke(
        {"messages": [HumanMessage("incidents?")]},
        context=RunContext(clients={"pager": PagerClient(fail=True)}),
    )

    message = next(m for m in result["messages"] if isinstance(m, ToolMessage))
    assert message.status == "error"
    assert message.content == "pager API error 503: try again later"
    assert len(model.calls) == 2


async def test_a_run_without_integration_clients_gets_a_readable_error() -> None:
    result = await _agent(_script()).ainvoke({"messages": [HumanMessage("incidents?")]})

    message = next(m for m in result["messages"] if isinstance(m, ToolMessage))
    assert message.status == "error"
    assert "started without integration clients" in str(message.content)
