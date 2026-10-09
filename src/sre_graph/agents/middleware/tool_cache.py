"""Serve repeated identical read-only tool calls from a short-lived, run-scoped cache."""

from __future__ import annotations

import json
import threading
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ToolCallRequest
from langchain_core.messages import ToolMessage
from langgraph.types import Command

from sre_graph.domain.tools import SideEffectLevel, read_tool_meta

#: ``configurable`` key an entry point sets to a fresh id for every run it starts.
CACHE_SCOPE_KEY = "tool_cache_scope"

ToolCallResult = ToolMessage | Command[Any]


class ToolResultCacheMiddleware(AgentMiddleware[Any, Any]):
    """Reuse a read-only tool's successful result when it runs again with the same arguments.

    Entries are shared only inside one caching scope: an entry point puts a
    fresh ``configurable[CACHE_SCOPE_KEY]`` on every run, so a later run (such
    as the next chat turn) re-reads live data, and a call without a scope is
    never cached. Tools that do not declare themselves read-only always run, so
    an approved action is never skipped. Entries also expire after
    ``ttl_seconds``, and error results are never cached.
    """

    def __init__(
        self,
        *,
        ttl_seconds: float = 300.0,
        max_entries: int = 512,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__()
        self._ttl_seconds = ttl_seconds
        self._max_entries = max_entries
        self._clock = clock
        self._entries: OrderedDict[str, tuple[float, ToolMessage]] = OrderedDict()
        self._lock = threading.Lock()
        self.hits = 0

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolCallResult],
    ) -> ToolCallResult:
        key = self._key(request)
        if key is None:
            return handler(request)
        cached = self._lookup(key, request)
        if cached is not None:
            return cached
        result = handler(request)
        self._remember(key, result)
        return result

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolCallResult]],
    ) -> ToolCallResult:
        key = self._key(request)
        if key is None:
            return await handler(request)
        cached = self._lookup(key, request)
        if cached is not None:
            return cached
        result = await handler(request)
        self._remember(key, result)
        return result

    @staticmethod
    def _key(request: ToolCallRequest) -> str | None:
        """The cache key for this call, or None when the call must not be cached."""
        meta = read_tool_meta(request.tool)
        if meta is None or meta.side_effect_level is not SideEffectLevel.READ_ONLY:
            return None
        config = request.runtime.config if request.runtime is not None else {}
        scope = config.get("configurable", {}).get(CACHE_SCOPE_KEY)
        if not scope:
            return None
        call = request.tool_call
        return json.dumps([str(scope), call["name"], call["args"]], sort_keys=True, default=str)

    def _lookup(self, key: str, request: ToolCallRequest) -> ToolMessage | None:
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            stored_at, message = entry
            if self._clock() - stored_at > self._ttl_seconds:
                del self._entries[key]
                return None
            self._entries.move_to_end(key)
            self.hits += 1
        return ToolMessage(
            content=message.content,
            tool_call_id=request.tool_call["id"] or "",
            name=request.tool_call["name"],
            artifact=message.artifact,
        )

    def _remember(self, key: str, result: ToolCallResult) -> None:
        if not isinstance(result, ToolMessage) or result.status == "error":
            return
        with self._lock:
            self._entries[key] = (self._clock(), result)
            self._entries.move_to_end(key)
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)
