from __future__ import annotations

from langchain_core.embeddings import Embeddings
from langgraph.store.memory import InMemoryStore

from fin_graph.memory.service import IncidentMemory


async def test_recall_incident_by_metadata_without_embeddings() -> None:
    store = InMemoryStore()
    memory = IncidentMemory(store)
    await memory.save_incident(
        "incident-1",
        title="Checkout OOMKilled",
        service="checkout",
        category="pod_oomkilled",
        summary="Deployment exhausted memory",
        evidence_ids=["e1"],
    )
    await memory.save_incident(
        "incident-2",
        title="Gateway timeout",
        service="gateway",
        category="network",
        summary="DNS failed",
        evidence_ids=["e2"],
    )

    matches = await memory.search_incidents("checkout memory", limit=5)

    assert [match["id"] for match in matches] == ["incident-1"]
    assert "evidence_ids" in matches[0]
    assert "messages" not in matches[0]


async def test_note_is_redacted_and_kept_in_notes_namespace() -> None:
    store = InMemoryStore()
    memory = IncidentMemory(store)

    note_id = await memory.save_note("token=abcdefghijk checkout fix")

    item = await store.aget(("org", "notes"), note_id)
    assert item is not None
    assert "abcdefghijk" not in str(item.value)
    assert "checkout fix" in str(item.value)


async def test_note_call_id_is_idempotent_on_replay() -> None:
    store = InMemoryStore()
    memory = IncidentMemory(store)

    first = await memory.save_note("Checked rollout", note_id="tool-call-1")
    second = await memory.save_note("Checked rollout", note_id="tool-call-1")

    assert first == second == "tool-call-1"
    assert len(await store.asearch(("org", "notes"))) == 1


async def test_recurring_fingerprint_keeps_separate_incident_records() -> None:
    store = InMemoryStore()
    memory = IncidentMemory(store)
    for incident_id in ("run-1", "run-2"):
        await memory.save_incident(
            incident_id,
            fingerprint="same-alert",
            title="Checkout CPU high",
            service="checkout",
            category="cpu_saturation",
            summary=f"Investigation {incident_id}",
            evidence_ids=["e1"],
        )

    matches = await memory.search_incidents("checkout CPU", limit=5)
    assert {item["id"] for item in matches} == {"run-1", "run-2"}
    assert {item["fingerprint"] for item in matches} == {"same-alert"}


async def test_configured_index_finds_semantic_match_without_lexical_overlap() -> None:
    class LocalEmbeddings(Embeddings):
        def embed_documents(self, texts: list[str]) -> list[list[float]]:
            return [self.embed_query(text) for text in texts]

        def embed_query(self, text: str) -> list[float]:
            return [1.0, 0.0] if "canine" in text or "dog" in text else [0.0, 1.0]

    store = InMemoryStore(index={"dims": 2, "embed": LocalEmbeddings(), "fields": ["summary"]})
    memory = IncidentMemory(store)
    await memory.save_incident(
        "run-dog",
        title="Service incident",
        service="api",
        category="unknown",
        summary="dog error",
        evidence_ids=[],
    )

    matches = await memory.search_incidents("canine")
    assert [item["id"] for item in matches] == ["run-dog"]
