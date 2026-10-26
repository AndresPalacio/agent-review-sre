"""Reversible masking of client, account and infrastructure identifiers before a model sees them.

Financial identifiers (account and card numbers, IBANs, routing numbers, tax ids, phones,
portfolio ids and client names) are validated where a checksum exists (Luhn, IBAN mod 97, ABA),
so prices, volumes and market caps are not mistaken for them. Ticker symbols are never masked.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from secrets import token_bytes
from typing import Any

from fin_graph.config.settings import Settings


class IdentifierKind(StrEnum):
    """Identifier families the built-in detectors recognise."""

    POD = "pod"
    NAMESPACE = "namespace"
    CLUSTER = "cluster"
    HOSTNAME = "hostname"
    ACCOUNT_ID = "account_id"
    IP_ADDRESS = "ip_address"
    EMAIL = "email"
    SERVICE_NAME = "service_name"
    ACCOUNT_NUMBER = "account_number"
    IBAN = "iban"
    CARD_NUMBER = "card_number"
    ROUTING_NUMBER = "routing_number"
    TAX_ID = "tax_id"
    PHONE = "phone"
    PORTFOLIO_ID = "portfolio_id"
    CLIENT_NAME = "client_name"


#: Between a label and its value: ``=``, ``:`` or spaces, with optional JSON quotes.
_LABEL_SEPARATOR = r"[\"']?(?:\s*[=:]\s*|\s+)[\"']?"
#: Contextual detectors capture only the value (group 1), not the "namespace:" label.
_DETECTORS: dict[IdentifierKind, re.Pattern[str]] = {
    IdentifierKind.POD: re.compile(
        r"\b([a-z0-9](?:[-a-z0-9]*[a-z0-9])?-[a-f0-9]{5,10}(?:-[a-z0-9]{3,10})?)\b"
    ),
    IdentifierKind.NAMESPACE: re.compile(
        r"\b(?:kube_namespace|namespace|ns)" + _LABEL_SEPARATOR + r"([a-z0-9][-a-z0-9]*)\b",
        re.IGNORECASE,
    ),
    IdentifierKind.CLUSTER: re.compile(
        r"\b(?:kube_cluster|eks_cluster|cluster(?:_name)?)"
        + _LABEL_SEPARATOR
        + r"([a-zA-Z0-9][-a-zA-Z0-9_]{1,})\b",
        re.IGNORECASE,
    ),
    IdentifierKind.SERVICE_NAME: re.compile(
        r"\b(?:service|service_name|app|deployment)"
        + _LABEL_SEPARATOR
        + r"([a-zA-Z0-9][-a-zA-Z0-9_]{1,})\b",
        re.IGNORECASE,
    ),
    IdentifierKind.HOSTNAME: re.compile(
        r"\b(kind-[a-z0-9][-a-z0-9]*"
        r"|ip-\d+-\d+-\d+-\d+(?:\.[a-z0-9][a-z0-9-]*)*"
        r"|[a-z0-9][-a-z0-9]*(?:\.[a-z0-9][-a-z0-9]*)+\.(?:com|net|org|io|internal|local|cloud))\b",
        re.IGNORECASE,
    ),
    # AWS account ids only in context (an ARN or an "account" label): a bare 12-digit number is
    # usually a revenue or a market cap, and masking it would corrupt the financial data.
    IdentifierKind.ACCOUNT_ID: re.compile(
        r"(?:arn:aws[a-z-]*:[a-z0-9-]*:[a-z0-9-]*:|\b(?:aws[_ ]?)?account(?:[_ ]?id)?"
        + _LABEL_SEPARATOR
        + r")(\d{12})\b",
        re.IGNORECASE,
    ),
    IdentifierKind.IP_ADDRESS: re.compile(
        r"\b((?:25[0-5]|2[0-4]\d|[01]?\d?\d)(?:\.(?:25[0-5]|2[0-4]\d|[01]?\d?\d)){3})\b"
    ),
    IdentifierKind.EMAIL: re.compile(r"\b([\w.+-]+@[\w-]+\.[\w.-]+)\b"),
    IdentifierKind.ACCOUNT_NUMBER: re.compile(
        r"\b(?:account(?:\s+(?:number|no\.?|#))?|acct|cuenta(?:\s+(?:n[uú]mero|no\.?))?|"
        r"n[uú]mero de cuenta)" + _LABEL_SEPARATOR + r"(\d[\d\- ]{4,18}\d)\b",
        re.IGNORECASE,
    ),
    IdentifierKind.IBAN: re.compile(
        r"\b([A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,4})?)\b"
    ),
    IdentifierKind.CARD_NUMBER: re.compile(r"(?<![\d.,])((?:\d[ -]?){12,18}\d)(?![\d.,]\d)"),
    IdentifierKind.ROUTING_NUMBER: re.compile(
        r"\b(?:routing(?:\s+number)?|aba|rtn)" + _LABEL_SEPARATOR + r"(\d{9})\b", re.IGNORECASE
    ),
    IdentifierKind.TAX_ID: re.compile(
        r"\b(\d{3}-\d{2}-\d{4})\b"
        r"|\b(?:tax[ _]?id|tin|ein|ssn|nit|rut|rfc|cuit|curp)"
        + _LABEL_SEPARATOR
        + r"([A-Z0-9][A-Z0-9.\-]{5,17}[A-Z0-9])\b",
        re.IGNORECASE,
    ),
    IdentifierKind.PHONE: re.compile(
        r"(?<![\w+])(\+\d{1,3}[ .-]?(?:\(?\d{1,4}\)?[ .-]?){2,4}\d{2,4})\b"
        r"|\b(?:phone|tel(?:[eé]fono)?|cel(?:ular)?|m[oó]vil|whatsapp)"
        + _LABEL_SEPARATOR
        + r"(\(?\d[\d ().-]{6,16}\d)\b",
        re.IGNORECASE,
    ),
    IdentifierKind.PORTFOLIO_ID: re.compile(
        r"\b(?:portfolio(?:_id)?|portafolio|cartera)(?:\s+id)?"
        + _LABEL_SEPARATOR
        + r"([A-Za-z0-9][A-Za-z0-9_\-]{3,40})\b",
        re.IGNORECASE,
    ),
    IdentifierKind.CLIENT_NAME: re.compile(
        r"\b(?:[Cc]lient|[Cc]ustomer|[Cc]liente|[Tt]itular|[Aa]ccount holder|[Bb]eneficiar(?:y|io))"
        + _LABEL_SEPARATOR
        + r"([A-ZÁÉÍÓÚÑ][a-záéíóúñ]+(?: [A-ZÁÉÍÓÚÑ][a-záéíóúñ]+){1,3})"
    ),
}


def luhn_valid(digits: str) -> bool:
    """The Luhn checksum card numbers carry."""
    total = 0
    for index, char in enumerate(reversed(digits)):
        value = int(char)
        if index % 2:
            value = value * 2 - 9 if value > 4 else value * 2
        total += value
    return total % 10 == 0


def card_number_valid(raw: str) -> bool:
    """A Visa, Mastercard, Amex or Discover number: known prefix and length, valid Luhn."""
    digits = re.sub(r"[ -]", "", raw)
    length = len(digits)
    known = (
        (digits.startswith("4") and length in (16, 19))
        or (51 <= int(digits[:2]) <= 55 and length == 16)
        or (2221 <= int(digits[:4]) <= 2720 and length == 16)
        or (digits[:2] in ("34", "37") and length == 15)
        or (digits.startswith(("6011", "65")) and length == 16)
    )
    return known and luhn_valid(digits)


def iban_valid(raw: str) -> bool:
    """ISO 13616 mod-97 check on an IBAN with or without spaces."""
    compact = raw.replace(" ", "").upper()
    if not 15 <= len(compact) <= 34:
        return False
    rearranged = compact[4:] + compact[:4]
    numeric = "".join(str(int(char, 36)) for char in rearranged)
    return int(numeric) % 97 == 1


def aba_valid(digits: str) -> bool:
    """The ABA routing-number checksum (weights 3, 7, 1)."""
    weights = (3, 7, 1) * 3
    return sum(int(char) * weight for char, weight in zip(digits, weights, strict=True)) % 10 == 0


#: Kinds whose regex match must also pass a checksum before it is masked.
_VALIDATORS: dict[IdentifierKind, Callable[[str], bool]] = {
    IdentifierKind.CARD_NUMBER: card_number_valid,
    IdentifierKind.IBAN: iban_valid,
    IdentifierKind.ROUTING_NUMBER: aba_valid,
}
_TOKEN_RE = re.compile(r"<[A-Z0-9_]+_[0-9a-f]{10}>")
#: Token key when MASK_KEY is unset: tokens are stable within this process only.
_PROCESS_KEY = token_bytes(32)
#: Dict keys whose string value is that kind of identifier (tool arguments, structured data).
_KEYED_KINDS: dict[str, IdentifierKind] = {
    "namespace": IdentifierKind.NAMESPACE,
    "kube_namespace": IdentifierKind.NAMESPACE,
    "cluster": IdentifierKind.CLUSTER,
    "cluster_name": IdentifierKind.CLUSTER,
    "service": IdentifierKind.SERVICE_NAME,
    "service_name": IdentifierKind.SERVICE_NAME,
    "app": IdentifierKind.SERVICE_NAME,
    "deployment": IdentifierKind.SERVICE_NAME,
    "account_number": IdentifierKind.ACCOUNT_NUMBER,
    "account_no": IdentifierKind.ACCOUNT_NUMBER,
    "iban": IdentifierKind.IBAN,
    "card_number": IdentifierKind.CARD_NUMBER,
    "pan": IdentifierKind.CARD_NUMBER,
    "routing_number": IdentifierKind.ROUTING_NUMBER,
    "tax_id": IdentifierKind.TAX_ID,
    "ssn": IdentifierKind.TAX_ID,
    "phone": IdentifierKind.PHONE,
    "portfolio_id": IdentifierKind.PORTFOLIO_ID,
    "client_name": IdentifierKind.CLIENT_NAME,
    "account_holder": IdentifierKind.CLIENT_NAME,
    "email": IdentifierKind.EMAIL,
}


@dataclass(frozen=True)
class MaskingPolicy:
    """Whether to mask, which identifier kinds, extra labelled patterns, and the token key.

    Tokens are an HMAC of the value under ``key``: stable while the key is,
    and impossible to recompute by hashing guessed values without it.
    """

    enabled: bool = False
    kinds: frozenset[IdentifierKind] = frozenset(IdentifierKind)
    extra_patterns: Mapping[str, re.Pattern[str]] = field(default_factory=dict)
    key: bytes = field(default=_PROCESS_KEY, repr=False)

    @classmethod
    def from_settings(cls, settings: Settings) -> MaskingPolicy:
        """Build from ``MASK_ENABLED``, ``MASK_KINDS`` (empty = all), ``MASK_EXTRA_REGEX``
        and ``MASK_KEY`` (empty = a random key for this process).
        """
        kinds = frozenset(IdentifierKind(kind) for kind in settings.mask_kinds)
        key = settings.mask_key.get_secret_value().encode() if settings.mask_key else None
        return cls(
            enabled=settings.mask_enabled,
            kinds=kinds or frozenset(IdentifierKind),
            extra_patterns={
                label: re.compile(pattern) for label, pattern in settings.mask_extra_regex.items()
            },
            key=key or _PROCESS_KEY,
        )


@dataclass(frozen=True)
class _Match:
    label: str
    start: int
    end: int
    value: str


class Masker:
    """Mask identifiers with stable tokens and restore exactly the tokens it issued.

    A token is the identifier kind plus a short keyed hash (HMAC) of the value,
    so a value gets the same token in every call under one key and nothing is
    stored between calls. Use one ``Masker`` per model call: mask the request,
    then unmask the reply.
    """

    def __init__(self, policy: MaskingPolicy) -> None:
        self._policy = policy
        self._originals: dict[str, str] = {}

    def mask(self, text: str) -> str:
        """Replace every detected identifier in ``text`` with its token."""
        if not self._policy.enabled or not text:
            return text
        parts: list[str] = []
        cursor = 0
        for match in self._find(text):
            parts.append(text[cursor : match.start])
            parts.append(self._token(match.label, match.value))
            cursor = match.end
        parts.append(text[cursor:])
        return "".join(parts)

    def unmask(self, text: str) -> str:
        """Restore the tokens this masker issued; any other ``<…>`` text is left as is."""
        if not self._originals or "<" not in text:
            return text
        return _TOKEN_RE.sub(
            lambda match: self._originals.get(match.group(0), match.group(0)), text
        )

    def mask_value(self, value: Any) -> Any:
        """Mask every string inside nested dicts, lists and tuples.

        A string stored under an identifier key (``namespace``, ``cluster``,
        ``app``…) is masked whole, since no label precedes it in the text.
        """
        if isinstance(value, dict):
            return {key: self._mask_entry(key, item) for key, item in value.items()}
        if isinstance(value, list):
            return [self.mask_value(item) for item in value]
        if isinstance(value, tuple):
            return tuple(self.mask_value(item) for item in value)
        if isinstance(value, str):
            return self.mask(value)
        return value

    def unmask_value(self, value: Any) -> Any:
        """Unmask every string inside nested dicts, lists and tuples."""
        return _map_strings(value, self.unmask)

    def _mask_entry(self, key: object, item: Any) -> Any:
        kind = _KEYED_KINDS.get(key.lower()) if isinstance(key, str) else None
        if (
            self._policy.enabled
            and kind is not None
            and kind in self._policy.kinds
            and isinstance(item, str)
            and item
            and not _TOKEN_RE.fullmatch(item)
        ):
            return self._token(kind.value, item)
        return self.mask_value(item)

    def _token(self, label: str, value: str) -> str:
        message = f"{label}:{value}".encode()
        digest = hmac.new(self._policy.key, message, hashlib.sha256).hexdigest()[:10]
        token = f"<{re.sub(r'[^A-Z0-9_]', '_', label.upper())}_{digest}>"
        self._originals[token] = value
        return token

    def _find(self, text: str) -> list[_Match]:
        found: list[_Match] = []
        for kind in IdentifierKind:
            if kind in self._policy.kinds:
                _collect(_DETECTORS[kind], kind.value, text, found, _VALIDATORS.get(kind))
        for label, pattern in self._policy.extra_patterns.items():
            _collect(pattern, label, text, found)
        return _without_overlaps(found)


def _collect(
    pattern: re.Pattern[str],
    label: str,
    text: str,
    out: list[_Match],
    valid: Callable[[str], bool] | None = None,
) -> None:
    """Record each match's value: the first group that matched, or the whole match."""
    for match in pattern.finditer(text):
        group = next((index for index, part in enumerate(match.groups(), 1) if part), 0)
        value = match.group(group)
        if value and (valid is None or valid(value)):
            start, end = match.span(group)
            out.append(_Match(label=label, start=start, end=end, value=value))


def _without_overlaps(matches: list[_Match]) -> list[_Match]:
    """Keep the longest match at each position and drop any match that overlaps a kept one."""
    kept: list[_Match] = []
    last_end = -1
    for match in sorted(matches, key=lambda m: (m.start, -(m.end - m.start), m.label)):
        if match.start >= last_end:
            kept.append(match)
            last_end = match.end
    return kept


def _map_strings(value: Any, transform: Callable[[str], str]) -> Any:
    if isinstance(value, str):
        return transform(value)
    if isinstance(value, dict):
        return {key: _map_strings(item, transform) for key, item in value.items()}
    if isinstance(value, list):
        return [_map_strings(item, transform) for item in value]
    if isinstance(value, tuple):
        return tuple(_map_strings(item, transform) for item in value)
    return value
