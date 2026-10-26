"""Shared test configuration: event loop choice, environment isolation, no external tracing."""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import Callable, Iterator, Mapping

import langsmith
import pytest
from langsmith import utils as langsmith_utils

from fin_graph.config.settings import Settings
from fin_graph.config.tracing import disable_external_tracing

_PROVIDER_AND_TRACING_ENV = (
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "LANGCHAIN_TRACING_V2",
    "LANGSMITH_TRACING_V2",
    "LANGCHAIN_TRACING",
    "LANGSMITH_API_KEY",
    "LANGCHAIN_API_KEY",
    "LANGSMITH_OTEL_ENABLED",
)
#: Every integration reads its settings from variables with its own prefix.
_INTEGRATION_ENV_PREFIXES = ("K8S_", "YAHOO_", "FINANCIAL_DATASETS_")
#: Read by client libraries directly (the Kubernetes SDK falls back to KUBECONFIG).
_CLIENT_LIBRARY_ENV = ("KUBECONFIG",)


def pytest_asyncio_loop_factories(
    config: pytest.Config, item: pytest.Item
) -> Mapping[str, Callable[[], asyncio.AbstractEventLoop]]:
    """psycopg's async driver needs a selector loop; Windows defaults to Proactor."""
    if sys.platform == "win32":
        return {"selector": asyncio.SelectorEventLoop}
    return {"default": asyncio.new_event_loop}


@pytest.fixture(autouse=True, scope="session")
def _no_external_tracing() -> None:
    """Nothing a test runs may reach LangSmith, whatever the developer's shell exports."""
    disable_external_tracing()


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep a developer's real settings, integration config and provider keys out of tests."""
    for name in (
        *(field.upper() for field in Settings.model_fields),
        *_PROVIDER_AND_TRACING_ENV,
        *_CLIENT_LIBRARY_ENV,
    ):
        monkeypatch.delenv(name, raising=False)
    for name in list(os.environ):
        if name.startswith(_INTEGRATION_ENV_PREFIXES):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LANGSMITH_TRACING", "false")


@pytest.fixture
def tracing_enabled_by_environment(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """LangSmith tracing switched on through the environment, as a developer shell might.

    langsmith caches environment lookups (``get_env_var`` is an ``lru_cache``), so
    the cache is cleared on the way in and out. The process override is reset on
    entry, the precondition is asserted, and tracing is disabled again on exit.
    """
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")
    monkeypatch.setenv("LANGSMITH_API_KEY", "lsv2-test-key")
    langsmith_utils.get_env_var.cache_clear()
    langsmith.configure(enabled=None)
    assert langsmith_utils.tracing_is_enabled(), "precondition: the environment enables tracing"
    yield
    disable_external_tracing()
    langsmith_utils.get_env_var.cache_clear()


@pytest.fixture
def postgres_url() -> str:
    """Connection string for ``postgres``-marked tests; CI sets ``FIN_GRAPH_REQUIRE_POSTGRES=1``.

    With that flag a missing URL fails the test instead of skipping it, so a
    misnamed variable in CI cannot silently drop the Postgres coverage.
    """
    url = os.environ.get("FIN_GRAPH_TEST_DATABASE_URL")
    if url:
        return url
    if os.environ.get("FIN_GRAPH_REQUIRE_POSTGRES") == "1":
        pytest.fail("FIN_GRAPH_REQUIRE_POSTGRES=1 but FIN_GRAPH_TEST_DATABASE_URL is not set")
    pytest.skip("FIN_GRAPH_TEST_DATABASE_URL is not set")
