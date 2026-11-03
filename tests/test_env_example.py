from __future__ import annotations

from pathlib import Path

import pytest
from dotenv import dotenv_values

from fin_graph.config.settings import Settings

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "switch", ["LANGSMITH_TRACING", "LANGSMITH_TRACING_V2", "LANGCHAIN_TRACING_V2"]
)
def test_env_example_turns_every_tracing_switch_off(switch: str) -> None:
    values = dotenv_values(ROOT / ".env.example", encoding="utf-8")

    assert values.get(switch) == "false"


def test_env_example_documents_every_setting() -> None:
    values = dotenv_values(ROOT / ".env.example", encoding="utf-8")
    expected = [name.upper() for name in Settings.model_fields]
    assert [name for name in expected if name not in values] == []


def test_env_example_loads_as_is() -> None:
    settings = Settings(_env_file=ROOT / ".env.example")
    assert settings.environment == "dev"


def test_env_example_runs_on_the_local_ollama_model() -> None:
    settings = Settings(_env_file=ROOT / ".env.example")

    assert settings.llm_provider == "ollama"
    assert settings.ollama_base_url == "http://localhost:11434"
    assert settings.llm_num_ctx == 8192
