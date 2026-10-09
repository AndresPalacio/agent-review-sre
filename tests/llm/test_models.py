from __future__ import annotations

import pytest
from langchain_anthropic import ChatAnthropic

from sre_graph.config.settings import Settings
from sre_graph.llm.models import ModelRole, ModelSpec, get_model, resolve_model_spec


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
