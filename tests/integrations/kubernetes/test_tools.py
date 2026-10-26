from __future__ import annotations

import copy
import json
from typing import Any

import pytest
from kubernetes.config.config_exception import ConfigException
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver

from fin_graph.agents.factory import make_agent
from fin_graph.agents.middleware.guardrails import GuardrailsMiddleware
from fin_graph.agents.testing.scripted_model import ScriptedChatModel, tool_call_message
from fin_graph.domain.tools import Domain, SideEffectLevel, read_tool_meta
from fin_graph.integrations.context import RunContext
from fin_graph.integrations.kubernetes.client import KubernetesClient
from fin_graph.integrations.kubernetes.tools import TOOLS

CRASHING_POD = "checkout-7d9f8b6c5-x2x9z"
PODS = "/api/v1/namespaces/payments/pods"


async def _call(
    tool_name: str, args: dict[str, Any], context: RunContext, **agent_options: Any
) -> ToolMessage:
    model = ScriptedChatModel(
        responses=[tool_call_message(tool_name, args, "c1"), AIMessage("done")]
    )
    agent = make_agent(
        name="kubernetes", model=model, tools=TOOLS, context_schema=RunContext, **agent_options
    )
    result = await agent.ainvoke({"messages": [HumanMessage("investigate")]}, context=context)
    return next(m for m in result["messages"] if isinstance(m, ToolMessage))


def _context(kube_client: KubernetesClient) -> RunContext:
    return RunContext(clients={"kubernetes": kube_client})


def test_every_kubernetes_tool_is_read_only_and_prefixed() -> None:
    for candidate in TOOLS:
        meta = read_tool_meta(candidate)
        assert meta is not None
        assert candidate.name.startswith("k8s_")
        assert meta.integration == "kubernetes"
        assert Domain.KUBERNETES in meta.domains
        assert meta.side_effect_level is SideEffectLevel.READ_ONLY


@pytest.mark.parametrize(
    ("tool_name", "domain"),
    [
        ("k8s_pod_logs", Domain.LOGS),
        ("k8s_rollout_history", Domain.CHANGES),
        ("k8s_pod_usage", Domain.METRICS),
    ],
)
def test_some_tools_also_serve_other_specialists(tool_name: str, domain: Domain) -> None:
    meta = read_tool_meta(next(t for t in TOOLS if t.name == tool_name))

    assert meta is not None
    assert domain in meta.domains


async def test_list_pods_sends_compact_json_and_keeps_the_data(
    kube_client: KubernetesClient,
) -> None:
    message = await _call("k8s_list_pods", {"namespace": "payments"}, _context(kube_client))

    assert json.loads(str(message.content))[0]["name"] == CRASHING_POD
    assert message.artifact[0]["name"] == CRASHING_POD


async def test_logs_reach_the_model_as_text(kube_client: KubernetesClient) -> None:
    message = await _call(
        "k8s_pod_logs",
        {"namespace": "payments", "pod": CRASHING_POD, "previous": True},
        _context(kube_client),
    )

    assert str(message.content).endswith("fatal: OOM\n")
    assert message.artifact["previous"] is True


async def test_a_huge_listing_is_cut_for_the_model_but_kept_whole_in_the_artifact(
    kube_client: KubernetesClient, kube_rest: Any
) -> None:
    status, listing = kube_rest.routes[PODS]
    pod = listing["items"][0]
    many = [
        {**pod, "metadata": {**pod["metadata"], "name": f"web-{index:03d}"}} for index in range(120)
    ]
    kube_rest.routes[PODS] = (status, {**listing, "items": many})

    message = await _call("k8s_list_pods", {"namespace": "payments"}, _context(kube_client))

    content = json.loads(str(message.content))
    shown = len(content) - 1
    assert content[-1] == f"…[{120 - shown} more items]"
    assert 0 < shown <= 50
    assert len(message.artifact) == 120


async def test_an_api_error_reaches_the_model_instead_of_crashing_the_run(
    kube_client: KubernetesClient, kube_rest: Any
) -> None:
    kube_rest.fail(
        PODS, 403, "Forbidden", 'pods is forbidden: User "dev" cannot list resource "pods"'
    )

    message = await _call("k8s_list_pods", {"namespace": "payments"}, _context(kube_client))

    assert message.status == "error"
    assert "Kubernetes API error 403" in str(message.content)


async def test_an_unavailable_cluster_is_explained_to_the_model() -> None:
    context = RunContext(unavailable={"kubernetes": "ConfigException: Invalid kube-config file."})

    message = await _call("k8s_list_pods", {"namespace": "payments"}, context)

    assert message.status == "error"
    assert str(message.content) == (
        "kubernetes is unavailable in this run: ConfigException: Invalid kube-config file."
    )


async def test_secrets_in_container_logs_are_redacted_by_the_guardrails(
    kube_client: KubernetesClient, kube_rest: Any
) -> None:
    token = "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"
    kube_rest.routes[f"{PODS}/{CRASHING_POD}/log"] = (200, f"auth failed for {token}\n")

    message = await _call(
        "k8s_pod_logs",
        {"namespace": "payments", "pod": CRASHING_POD},
        _context(kube_client),
        middleware=[GuardrailsMiddleware()],
    )

    assert token not in str(message.content)
    assert token not in json.dumps(message.artifact)


async def test_a_password_in_a_json_termination_message_never_reaches_the_model_or_checkpoint(
    kube_client: KubernetesClient, kube_rest: Any
) -> None:
    secret = "hunter2-" + "termination-message"
    status, listing = kube_rest.routes[PODS]
    crashing = copy.deepcopy(listing["items"][1])
    terminated = crashing["status"]["containerStatuses"][0]["lastState"]["terminated"]
    terminated["message"] = json.dumps({"level": "fatal", "password": secret})
    kube_rest.routes[PODS] = (status, {**listing, "items": [crashing]})
    model = ScriptedChatModel(
        responses=[
            tool_call_message("k8s_list_pods", {"namespace": "payments"}, "c1"),
            AIMessage("done"),
        ]
    )
    agent = make_agent(
        name="kubernetes",
        model=model,
        tools=TOOLS,
        context_schema=RunContext,
        middleware=[GuardrailsMiddleware()],
        checkpointer=InMemorySaver(),
    )
    config: Any = {"configurable": {"thread_id": "t1"}}

    await agent.ainvoke(
        {"messages": [HumanMessage("investigate")]}, config, context=_context(kube_client)
    )

    saved = (await agent.aget_state(config)).values["messages"]
    tool_message = next(m for m in saved if isinstance(m, ToolMessage))
    assert secret not in str(model.calls[1])
    assert secret not in str(tool_message.content)
    assert json.loads(str(tool_message.content))[0]["name"] == CRASHING_POD


async def test_an_expired_exec_plugin_login_reaches_the_model_and_the_run_continues(
    kube_client: KubernetesClient, kube_rest: Any
) -> None:
    kube_rest.failure = ConfigException("exec plugin failed: run aws sso login")

    message = await _call("k8s_list_pods", {"namespace": "payments"}, _context(kube_client))

    assert message.status == "error"
    assert "aws sso login" in str(message.content)


async def test_the_model_is_told_when_the_api_server_cut_the_listing(
    kube_client: KubernetesClient, kube_rest: Any
) -> None:
    status, listing = kube_rest.routes[PODS]
    kube_rest.routes[PODS] = (status, {**listing, "metadata": {"continue": "page-2"}})

    message = await _call("k8s_list_pods", {"namespace": "payments"}, _context(kube_client))

    content = json.loads(str(message.content))
    assert content[0].startswith("…[partial: the API server has more items")
    assert content[1]["name"] == CRASHING_POD
    assert len(message.artifact) == 2
