from __future__ import annotations

from hashlib import sha256

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore
from langgraph.types import Command
from pydantic import BaseModel

from fin_graph.agents.testing.scripted_model import ScriptedChatModel, tool_call_message
from fin_graph.graphs.chat.graph import make_chat_graph
from fin_graph.graphs.remediation.graph import ActionCatalog, ActionSpec
from fin_graph.integrations.context import RunContext
from fin_graph.memory.service import IncidentMemory
from fin_graph.safety.approval import ApprovalPolicy


class EmptyRegistry:
    def tools(self, context: RunContext) -> list[object]:
        return []


async def test_chat_keeps_thread_and_recalls_an_incident() -> None:
    store = InMemoryStore()
    memory = IncidentMemory(store)
    await memory.save_incident(
        "inc-1",
        title="Checkout OOMKilled",
        service="checkout",
        category="pod_oomkilled",
        summary="Memory limit exceeded",
        evidence_ids=["e1"],
    )
    model = ScriptedChatModel(
        responses=[
            tool_call_message("search_incident_memory", {"query": "checkout memory"}, "m1"),
            AIMessage("Incidente similar: checkout OOMKilled."),
            AIMessage("Seguimos en el mismo hilo."),
        ]
    )
    graph = make_chat_graph(
        model,
        EmptyRegistry(),
        RunContext(),
        memory,
        ActionCatalog([]),
        policy=ApprovalPolicy(environment="prod"),
        checkpointer=InMemorySaver(),
        store=store,
    )
    config = {"configurable": {"thread_id": "chat-1"}}

    first = await graph.ainvoke({"messages": [HumanMessage("¿Incidentes similares?")]}, config)
    second = await graph.ainvoke({"messages": [HumanMessage("Gracias")]}, config)

    assert "checkout OOMKilled" in first["messages"][-1].text
    assert len(second["messages"]) > len(first["messages"])


async def test_saving_a_note_requires_approval_in_production() -> None:
    store = InMemoryStore()
    model = ScriptedChatModel(
        responses=[
            tool_call_message("save_note", {"text": "Scale restored checkout"}, "note-1"),
            AIMessage("Nota guardada."),
        ]
    )
    graph = make_chat_graph(
        model,
        EmptyRegistry(),
        RunContext(),
        IncidentMemory(store),
        ActionCatalog([]),
        policy=ApprovalPolicy(environment="prod"),
        checkpointer=InMemorySaver(),
        store=store,
    )
    config = {"configurable": {"thread_id": "note"}}

    paused = await graph.ainvoke({"messages": [HumanMessage("Guarda la nota")]}, config)
    assert paused["__interrupt__"][0].value["action_requests"][0]["name"] == "save_note"
    assert await store.asearch(("org", "notes")) == []
    await graph.ainvoke(
        Command(resume={"decisions": [{"type": "approve"}]}),
        config,
        context=RunContext(actor="approver-1", role="approver"),
    )
    note_id = sha256(b"note:note-1").hexdigest()
    assert (await store.aget(("org", "notes"), note_id)) is not None


async def test_chat_mutation_pauses_before_tool_execution() -> None:
    class Args(BaseModel):
        namespace: str
        replicas: int

    calls: list[str] = []

    async def prepare(args: Args, context: RunContext) -> dict[str, object]:
        calls.append("dry-run")
        return {"replicas": args.replicas}

    async def execute(args: Args, key: str, context: RunContext) -> dict[str, object]:
        calls.append("write")
        return {"replicas": args.replicas}

    async def verify(args: Args, context: RunContext) -> bool:
        return True

    catalog = ActionCatalog([ActionSpec("scale", Args, prepare, execute, verify)])
    model = ScriptedChatModel(
        responses=[
            tool_call_message(
                "execute_remediation",
                {
                    "name": "scale",
                    "action_args": {
                        "namespace": "prod",
                        "replicas": 2,
                    },
                },
                "c1",
            ),
            AIMessage("Se verificó la escala."),
        ]
    )
    graph = make_chat_graph(
        model,
        EmptyRegistry(),
        RunContext(),
        IncidentMemory(InMemoryStore()),
        catalog,
        policy=ApprovalPolicy(environment="prod"),
        checkpointer=InMemorySaver(),
    )
    config = {"configurable": {"thread_id": "chat-mut"}}

    paused = await graph.ainvoke({"messages": [HumanMessage("Escala a dos")]}, config)
    assert paused["__interrupt__"][0].value["action_requests"][0]["name"] == "execute_remediation"
    assert calls == []
    await graph.ainvoke(
        Command(resume={"decisions": [{"type": "reject"}]}),
        config,
        context=RunContext(actor="approver-1", role="approver"),
    )
    assert calls == []


async def test_chat_approved_mutation_prepares_then_executes_once() -> None:
    class Args(BaseModel):
        replicas: int

    calls: list[str] = []

    async def prepare(args: Args, context: RunContext) -> dict[str, object]:
        calls.append("dry-run")
        return {"replicas": args.replicas}

    async def execute(args: Args, key: str, context: RunContext) -> dict[str, object]:
        calls.append(f"write:{key}")
        return {"replicas": args.replicas}

    async def verify(args: Args, context: RunContext) -> bool:
        calls.append("verify")
        return True

    catalog = ActionCatalog([ActionSpec("scale", Args, prepare, execute, verify)])
    model = ScriptedChatModel(
        responses=[
            tool_call_message(
                "execute_remediation", {"name": "scale", "action_args": {"replicas": 2}}, "c1"
            ),
            AIMessage("Escala verificada."),
        ]
    )
    graph = make_chat_graph(
        model,
        EmptyRegistry(),
        RunContext(),
        IncidentMemory(InMemoryStore()),
        catalog,
        policy=ApprovalPolicy(environment="prod"),
        checkpointer=InMemorySaver(),
    )
    config = {"configurable": {"thread_id": "approved"}}

    await graph.ainvoke({"messages": [HumanMessage("Escala")]}, config)
    result = await graph.ainvoke(
        Command(resume={"decisions": [{"type": "approve"}]}),
        config,
        context=RunContext(actor="approver-1", role="approver"),
    )

    assert result["messages"][-1].text == "Escala verificada."
    key = sha256(b"approved:c1").hexdigest()
    assert calls == ["dry-run", f"write:{key}", "verify"]
