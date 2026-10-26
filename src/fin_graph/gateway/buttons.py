"""Compact, authenticated approval button values for both bot platforms."""

from __future__ import annotations

import hashlib
import hmac

_KIND = {"chat": "c", "orders": "o"}
_DECISION = {"approve": "a", "reject": "x"}


def button_value(
    secret: str,
    platform: str,
    destination: str,
    remote_id: str,
    kind: str,
    thread_id: str,
    choice: str,
) -> str:
    if kind not in _KIND or choice not in _DECISION or ":" in thread_id or not thread_id:
        raise ValueError("invalid approval button")
    prefix = f"{_KIND[kind]}:{thread_id}:{_DECISION[choice]}"
    data = f"{platform}:{destination}:{remote_id}:{prefix}".encode()
    nonce = hmac.new(secret.encode(), data, hashlib.sha256).hexdigest()[:8]
    value = f"{prefix}:{nonce}"
    if len(value.encode()) > 64:
        raise ValueError("approval button exceeds platform limit")
    return value


def parse_button(
    secret: str, platform: str, destination: str, remote_id: str, value: str
) -> tuple[str, str, str] | None:
    parts = value.split(":")
    if len(parts) != 4:
        return None
    kind = {"c": "chat", "o": "orders"}.get(parts[0])
    choice = {"a": "approve", "x": "reject"}.get(parts[2])
    if kind is None or choice is None:
        return None
    try:
        expected = button_value(secret, platform, destination, remote_id, kind, parts[1], choice)
    except ValueError:
        return None
    if not hmac.compare_digest(value, expected):
        return None
    return kind, parts[1], choice
