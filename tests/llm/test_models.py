from __future__ import annotations

import pytest
from langchain_anthropic import ChatAnthropic
from langchain_ollama import ChatOllama

from fin_graph.config.settings import Settings
from fin_graph.llm.models import (
    ModelRole,
    ModelSpec,
    build_chat_model,
    get_model,
    parse_model_spec,
    resolve_model_spec,
)


def test_anthropic_defaults_differ_by_role() -> None:
    settings = Settings(_env_file=None)

    assert resolve_model_spec(ModelRole.REASONING, settings) == ModelSpec(
        "anthropic", "claude-sonnet-5-5"
    )
    assert resolve_model_spec(ModelRole.FAST, settings) == ModelSpec(
        "anthropic", "claude-haiku-4-5-20251001"
    )


def test_role_override_keeps_colons_inside_model_names() -> None:
    settings = Settings(_env_file=None, llm_provider="ollama", llm_model_worker="llama3.2:3b")

    assert resolve_model_spec(ModelRole.WORKER, settings) == ModelSpec("ollama", "llama3.2:3b")


def test_provider_without_defaults_requires_an_explicit_model() -> None:
    settings = Settings(_env_file=None, llm_provider="openai")

    with pytest.raises(ValueError, match="LLM_MODEL_JUDGE"):
        resolve_model_spec(ModelRole.JUDGE, settings)


def test_get_model_builds_the_provider_class_without_network() -> None:
    model = get_model(ModelRole.FAST, Settings(_env_file=None, llm_temperature=0.2))

    assert isinstance(model, ChatAnthropic)
    assert model.model == "claude-haiku-4-5-20251001"
    assert model.temperature == 0.2


def test_blank_role_variable_falls_back_to_the_provider_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_MODEL_FAST", "")

    spec = resolve_model_spec(ModelRole.FAST, Settings(_env_file=None))

    assert spec == ModelSpec("anthropic", "claude-haiku-4-5-20251001")


def test_ollama_runs_every_role_on_the_local_3b_model() -> None:
    settings = Settings(_env_file=None, llm_provider="ollama")

    assert {resolve_model_spec(role, settings) for role in ModelRole} == {
        ModelSpec("ollama", "llama3.2:3b")
    }


def test_ollama_model_gets_endpoint_context_window_and_http_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://ollama.internal:11434")
    monkeypatch.setenv("LLM_NUM_CTX", "4096")
    settings = Settings(_env_file=None, llm_provider="ollama", llm_timeout_seconds=30)

    model = get_model(ModelRole.REASONING, settings)

    assert isinstance(model, ChatOllama)
    assert model.model == "llama3.2:3b"
    assert model.base_url == "http://ollama.internal:11434"
    assert model.num_ctx == 4096
    assert model.num_predict == 1024
    assert model.client_kwargs == {"timeout": 30}


def test_parse_model_spec_splits_on_the_first_colon_only() -> None:
    assert parse_model_spec("Ollama:llama3.2:3b") == ModelSpec("ollama", "llama3.2:3b")
    with pytest.raises(ValueError, match="provider:model"):
        parse_model_spec("llama3.2")


def test_explicit_temperature_overrides_the_setting() -> None:
    spec = ModelSpec("ollama", "llama3.2:3b")

    model = build_chat_model(spec, Settings(_env_file=None, llm_temperature=0.7), temperature=0)

    assert isinstance(model, ChatOllama)
    assert model.temperature == 0
