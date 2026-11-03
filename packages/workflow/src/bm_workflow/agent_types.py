"""Host-owned invocation resources and safe, explicit agent version references."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from .runtime import RunContext


@dataclass(frozen=True)
class AgentInvocation:
    """Snapshot config's outer container, retaining live values and context identity.

    Config and context are runtime resources, never definition metadata. The host
    owns their lifecycle and any synchronization needed by nested mutable values.
    """

    config: Mapping[str, Any] = field(default_factory=dict, repr=False)
    context: Any = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not isinstance(self.config, Mapping):
            raise TypeError("config must be a mapping")
        object.__setattr__(self, "config", MappingProxyType(dict(self.config)))


@dataclass(frozen=True)
class AgentManifest:
    """JSON-only version references chosen by the host, never live providers.

    References may identify models, tools, prompts, skills or verifier versions.
    Credentials and runtime configuration belong in AgentInvocation instead.
    """

    revisions: Mapping[str, str]

    def __post_init__(self) -> None:
        if not isinstance(self.revisions, Mapping):
            raise TypeError("revisions must be a mapping of version references")
        snapshot = dict(self.revisions)
        if any(
            not isinstance(key, str) or not key or not isinstance(value, str) or not value
            for key, value in snapshot.items()
        ):
            raise ValueError("revision names and version references must be nonempty strings")
        object.__setattr__(self, "revisions", MappingProxyType(snapshot))


type InvocationFactory = Callable[
    [dict[str, Any], RunContext | None], AgentInvocation | Awaitable[AgentInvocation]
]
