"""Keep agent runs from being traced to third-party services."""

from __future__ import annotations

import langsmith


def disable_external_tracing() -> None:
    """Turn LangSmith tracing off for this process, whatever its environment variables say.

    Every entry point calls this before running graphs: ``LANGSMITH_TRACING=false``
    alone is not enough, because ``LANGCHAIN_TRACING_V2=true`` takes precedence.
    """
    langsmith.configure(enabled=False)
