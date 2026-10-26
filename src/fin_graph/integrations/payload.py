"""Keep tool output small enough for the model, and say what was left out."""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

from fin_graph.safety.secrets import redact_secrets, redact_value

MAX_ITEMS = 50
MAX_CHARS = 8000


class PartialList(list[Any]):
    """Entries from a source that holds more than it returned; ``note`` says so."""

    def __init__(self, items: Iterable[Any] = (), *, note: str) -> None:
        super().__init__(items)
        self.note = note


def tool_payload(data: Any, *, max_items: int = MAX_ITEMS, max_chars: int = MAX_CHARS) -> str:
    """Compact JSON for the model, at most ``max_chars`` long.

    Every list keeps its first ``max_items`` entries plus a marker counting the
    rest. A top-level list that is still too long keeps as many whole entries
    as fit, so the JSON stays valid; anything else is cut at ``max_chars``
    with a marker. Put the most important entries first: the start survives.
    Credentials are redacted from the values before anything is serialized or cut.
    A ``PartialList`` starts with its note, so the model knows the list is incomplete.
    """
    note = data.note if isinstance(data, PartialList) else ""
    safe = redact_value(data)
    if note:
        safe = [note, *safe]
    text = _dumps(_cap_lists(safe, max_items))
    if len(text) <= max_chars:
        return text
    if isinstance(safe, list):
        fitted = _fit_list(safe, max_items, max_chars)
        if fitted is not None:
            return fitted
    return f"{text[:max_chars]}…[truncated {len(text) - max_chars} more characters]"


def text_payload(text: str, *, max_chars: int = MAX_CHARS) -> str:
    """The end of ``text`` (for logs, the most recent lines), marked when the start was cut.

    Credentials are redacted before the cut, so a key split by it cannot leak.
    """
    text = redact_secrets(text)
    if len(text) <= max_chars:
        return text
    tail = text[-max_chars:]
    first_break = tail.find("\n")
    if 0 <= first_break < len(tail) - 1:
        tail = tail[first_break + 1 :]
    return f"[…{len(text) - len(tail)} earlier characters omitted]\n{tail}"


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _cap_lists(value: Any, max_items: int) -> Any:
    if isinstance(value, list):
        kept = [_cap_lists(item, max_items) for item in value[:max_items]]
        if len(value) > max_items:
            kept.append(f"…[{len(value) - max_items} more items]")
        return kept
    if isinstance(value, dict):
        return {key: _cap_lists(item, max_items) for key, item in value.items()}
    return value


def _fit_list(items: list[Any], max_items: int, max_chars: int) -> str | None:
    """The longest head of ``items`` that fits in ``max_chars`` with its marker (binary search)."""
    best: str | None = None
    low, high = 0, min(len(items), max_items)
    while low <= high:
        middle = (low + high) // 2
        head = [_cap_lists(item, max_items) for item in items[:middle]]
        text = _dumps([*head, f"…[{len(items) - middle} more items]"])
        if len(text) <= max_chars:
            best, low = text, middle + 1
        else:
            high = middle - 1
    return best
