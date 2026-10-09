"""Compact, redacted incident summaries in LangGraph Store."""

from __future__ import annotations

import re
from typing import Any
from uuid import uuid4

from langgraph.store.base import BaseStore

from sre_graph.safety.secrets import redact_secrets

INCIDENTS = ("org", "incidents")
NOTES = ("org", "notes")


class IncidentMemory:
    def __init__(self, store: BaseStore) -> None:
        self.store = store

    async def save_incident(
        self,
        incident_id: str,
        *,
        fingerprint: str | None = None,
        title: str,
        service: str,
        category: str,
        summary: str,
        evidence_ids: list[str],
    ) -> None:
        if not incident_id or len(incident_id) > 128:
            raise ValueError("invalid incident id")
        await self.store.aput(
            INCIDENTS,
            incident_id,
            {
                "id": incident_id,
                "fingerprint": fingerprint or "",
                "title": redact_secrets(title)[:240],
                "service": redact_secrets(service)[:120],
                "category": category[:120],
                "summary": redact_secrets(summary)[:2000],
                "evidence_ids": evidence_ids[:20],
            },
        )

    async def search_incidents(self, query: str, *, limit: int = 5) -> list[dict[str, Any]]:
        if not 1 <= limit <= 20:
            raise ValueError("limit must be between 1 and 20")
        query = redact_secrets(query)[:500]
        terms = set(re.findall(r"[a-z0-9_]+", query.lower()))
        if not terms:
            return []
        if getattr(self.store, "index_config", None) is not None:
            try:
                matches = await self.store.asearch(INCIDENTS, query=query, limit=limit)
                return [item.value for item in matches]
            except Exception:
                # Keep recall available when an embedding service is unavailable.
                pass
        candidates = await self.store.asearch(INCIDENTS, limit=200)
        ranked: list[tuple[int, dict[str, Any]]] = []
        for item in candidates:
            value = item.value
            fields = ("title", "service", "category", "summary")
            text = " ".join(str(value.get(key, "")) for key in fields)
            score = len(terms & set(re.findall(r"[a-z0-9_]+", text.lower())))
            if score:
                ranked.append((score, value))
        ranked.sort(key=lambda pair: (-pair[0], pair[1]["id"]))
        return [value for _, value in ranked[:limit]]

    async def save_note(self, text: str, *, note_id: str | None = None) -> str:
        if not text.strip() or len(text) > 4000:
            raise ValueError("note must contain 1-4000 characters")
        key = note_id or str(uuid4())
        if len(key) > 128:
            raise ValueError("invalid note id")
        value = {"text": redact_secrets(text)}
        existing = await self.store.aget(NOTES, key)
        if existing is not None:
            if existing.value != value:
                raise ValueError("note id was already used for different content")
            return key
        await self.store.aput(NOTES, key, value)
        return key
