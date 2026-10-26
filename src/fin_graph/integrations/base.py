"""What every integration provides: settings, a per-run client, tools and a connectivity check."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar

from langchain_core.tools import BaseTool, ToolException


class IntegrationError(ToolException):
    """A call to an external system failed.

    Tools built with ``integration_tool`` turn this into an error message the
    model reads, so the message must be factual and free of credentials.
    """


class HttpStatusError(IntegrationError):
    """An HTTP call returned a non-success status; ``status`` lets callers retry rate limits."""

    def __init__(self, message: str, status: int) -> None:
        super().__init__(message)
        self.status = status


class IntegrationNotConfiguredError(IntegrationError):
    """The integration a tool needs is not configured, or could not be built for this run."""


class VerifyStatus(StrEnum):
    """Outcome of an integration's connectivity check."""

    OK = "ok"
    FAILED = "failed"
    NOT_CONFIGURED = "not_configured"


@dataclass(frozen=True)
class VerifyResult:
    """What a connectivity check found, in words an operator can act on."""

    integration: str
    status: VerifyStatus
    detail: str


class Integration[SettingsT, ClientT](ABC):
    """One external system. Instances hold no state; a client is built for every run."""

    id: ClassVar[str]
    display_name: ClassVar[str]

    @abstractmethod
    def load_settings(self, env_file: str | None) -> SettingsT | None:
        """Read settings from the environment and ``env_file``; None when not configured."""

    @abstractmethod
    def build_client(self, settings: SettingsT) -> ClientT:
        """A client for one run. Raises when the configuration cannot be used."""

    @abstractmethod
    def tools(self) -> list[BaseTool]:
        """Every tool this integration offers, each tagged with ``ToolMeta``."""

    @abstractmethod
    async def verify(self, client: ClientT) -> VerifyResult:
        """Prove the client can reach the system and read from it."""
