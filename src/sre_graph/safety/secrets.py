"""Redact credentials from text before a model, a checkpoint or a channel can see them."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

_REDACTED_PREFIX = "[REDACTED:"
#: Key names that hold a credential when the name ends with one of these words.
_SECRET_NAME = (
    r"password|passwd|pwd|(?<![a-z])pass|passphrase|secret|credentials?|token"
    r"|(?:api|access|private|secret|encryption|signing|master)[_-]?key"
)
_SECRET_KEY = re.compile(rf"(?i)(?:{_SECRET_NAME}|authorization|auth)$")
_SINGLE_MARKER = re.compile(r"\[REDACTED:[a-z_]+\]")


@dataclass(frozen=True)
class SecretPattern:
    """A credential format.

    When the regex has a ``secret`` (or ``quoted``) group, only that part is
    replaced, so names, hosts and surrounding quotes stay as evidence.
    """

    kind: str
    regex: re.Pattern[str]


#: Most specific formats first; the generic ``key=value`` rule runs last.
SECRET_PATTERNS: tuple[SecretPattern, ...] = (
    # A key cut short by a log tail is redacted to the end of the text.
    SecretPattern(
        "private_key",
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----"
            r"(?:[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----|[\s\S]*\Z)"
        ),
    ),
    # A key whose header was cut off: the base64 lines right before an orphaned footer.
    SecretPattern(
        "private_key",
        re.compile(r"(?m)^(?:[A-Za-z0-9+/=]{16,}[ \t]*\r?\n)+-----END [A-Z ]*PRIVATE KEY-----"),
    ),
    SecretPattern("aws_access_key_id", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    SecretPattern(
        "github_token",
        re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,255}|github_pat_[A-Za-z0-9_]{22,255})\b"),
    ),
    SecretPattern("slack_token", re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}\b")),
    SecretPattern("llm_api_key", re.compile(r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_-]{20,}\b")),
    SecretPattern("stripe_key", re.compile(r"\b(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{16,}\b")),
    SecretPattern("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    SecretPattern(
        "jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")
    ),
    SecretPattern("bearer_token", re.compile(r"(?i)\bbearer\s+(?P<secret>[A-Za-z0-9\-._~+/]+=*)")),
    SecretPattern(
        "basic_auth",
        re.compile(
            r"(?i)\bauthorization[\"']?\s*[:=]\s*[\"']?basic\s+(?P<secret>[A-Za-z0-9+/]+={0,2})"
        ),
    ),
    # The user may be empty (redis://:password@host) and the password may contain '/'.
    SecretPattern(
        "url_credentials",
        re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://(?P<secret>[^\s/:@]*:[^\s@]+)@"),
    ),
    SecretPattern(
        "assigned_secret",
        re.compile(
            rf"(?i)\b[a-z0-9_.-]*(?:{_SECRET_NAME})\\?[\"']?\s*[:=]\s*"
            r"(?:(?P<quote>\\?[\"'])(?P<quoted>.*?)(?P=quote)|(?P<secret>[^\s\"'}\]]+))"
        ),
    ),
)


def redact_secrets(text: str) -> str:
    """Replace every recognised credential in ``text`` with ``[REDACTED:<kind>]``."""
    for pattern in SECRET_PATTERNS:
        text = pattern.regex.sub(_replacer(pattern.kind), text)
    return text


def redact_text(text: str) -> str:
    """Redact a tool's text output; a JSON object or array is redacted value by value.

    Working on parsed values instead of serialized text catches secrets that
    JSON escaping would hide (``\\"password\\":\\"…\\"``) and keeps the JSON valid.
    """
    if text.lstrip()[:1] in ("{", "["):
        try:
            parsed = json.loads(text)
        except ValueError:
            return redact_secrets(text)
        return json.dumps(redact_value(parsed), ensure_ascii=False, separators=(",", ":"))
    return redact_secrets(text)


def redact_value(value: Any) -> Any:
    """Redact credentials inside nested dicts, lists and tuples.

    Strings are scanned for credential formats, and a scalar stored under a
    credential-like key (``DB_PASSWORD``, ``client_secret``…) is replaced whole.
    """
    if isinstance(value, str):
        return redact_secrets(value)
    if isinstance(value, dict):
        return {key: _redact_entry(key, item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_value(item) for item in value)
    return value


def _redact_entry(key: object, item: Any) -> Any:
    if isinstance(key, str) and _SECRET_KEY.search(key) and _is_secret_scalar(item):
        specific = redact_secrets(item) if isinstance(item, str) else ""
        return (
            specific
            if _SINGLE_MARKER.fullmatch(specific)
            else f"{_REDACTED_PREFIX}assigned_secret]"
        )
    return redact_value(item)


def _is_secret_scalar(item: Any) -> bool:
    if isinstance(item, bool) or item is None:
        return False
    if isinstance(item, str):
        return bool(item) and not item.startswith(_REDACTED_PREFIX)
    return isinstance(item, int | float)


def _replacer(kind: str) -> Callable[[re.Match[str]], str]:
    marker = f"{_REDACTED_PREFIX}{kind}]"

    def replace(match: re.Match[str]) -> str:
        group = next(
            (
                name
                for name in ("quoted", "secret")
                if name in match.re.groupindex and match.group(name) is not None
            ),
            None,
        )
        if group is None:
            return marker
        if match.group(group).startswith(_REDACTED_PREFIX):
            return match.group(0)
        start, end = match.span(group)
        whole, offset = match.group(0), match.start()
        return whole[: start - offset] + marker + whole[end - offset :]

    return replace
