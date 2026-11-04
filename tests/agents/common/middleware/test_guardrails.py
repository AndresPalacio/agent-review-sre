from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver

from fin_graph.agents.common.factory import make_agent
from fin_graph.agents.common.middleware.guardrails import GuardrailsMiddleware
from fin_graph.agents.common.testing.scripted_model import ScriptedChatModel, tool_call_message

# Built at runtime so this file holds no string that looks like a real credential.
GITHUB_TOKEN = "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"


@tool(response_format="content_and_artifact")
def read_market_config(provider: str) -> tuple[str, dict[str, Any]]:
    """Read a market data provider's configuration."""
    return f"GITHUB_TOKEN={GITHUB_TOKEN}", {"env": {"GITHUB_TOKEN": GITHUB_TOKEN}}


def _model() -> ScriptedChatModel:
    return ScriptedChatModel(
        responses=[
            tool_call_message("read_market_config", {"provider": "yahoo"}, "c1"),
            AIMessage("done"),
        ]
    )


def test_the_model_never_sees_a_secret_returned_by_a_tool() -> None:
    model = _model()
    agent = make_agent(
        name="market", model=model, tools=[read_market_config], middleware=[GuardrailsMiddleware()]
    )

    agent.invoke({"messages": [HumanMessage("check market provider")]})

    second_call = model.calls[1]
    assert GITHUB_TOKEN not in str([message.content for message in second_call])
    assert "[REDACTED:github_token]" in str(second_call[-1].content)


def test_the_checkpoint_holds_the_redacted_content_and_artifact() -> None:
    agent = make_agent(
        name="market",
        model=_model(),
        tools=[read_market_config],
        middleware=[GuardrailsMiddleware()],
        checkpointer=InMemorySaver(),
    )
    config: Any = {"configurable": {"thread_id": "t1"}}

    agent.invoke({"messages": [HumanMessage("check market provider")]}, config)

    saved = agent.get_state(config).values["messages"]
    tool_message = next(m for m in saved if isinstance(m, ToolMessage))
    assert GITHUB_TOKEN not in str(tool_message.content)
    assert tool_message.artifact == {"env": {"GITHUB_TOKEN": "[REDACTED:github_token]"}}


async def test_redaction_also_applies_to_async_runs() -> None:
    agent = make_agent(
        name="market",
        model=_model(),
        tools=[read_market_config],
        middleware=[GuardrailsMiddleware()],
    )

    result = await agent.ainvoke({"messages": [HumanMessage("check market provider")]})

    tool_message = next(m for m in result["messages"] if isinstance(m, ToolMessage))
    assert GITHUB_TOKEN not in str(tool_message.content)


def test_secrets_inside_json_content_are_redacted_and_the_json_stays_valid() -> None:
    @tool
    def read_provider_status(provider: str) -> str:
        """Read a market data provider's status document."""
        nested = json.dumps({"api_key": "hunter2-too"})
        return json.dumps({"provider": provider, "password": "hunter2", "detail": nested})

    model = ScriptedChatModel(
        responses=[
            tool_call_message("read_provider_status", {"provider": "yahoo"}, "c1"),
            AIMessage("done"),
        ]
    )
    agent = make_agent(
        name="market",
        model=model,
        tools=[read_provider_status],
        middleware=[GuardrailsMiddleware()],
    )

    agent.invoke({"messages": [HumanMessage("market provider status?")]})

    content = str(model.calls[1][-1].content)
    assert "hunter2" not in content
    assert json.loads(content)["provider"] == "yahoo"
