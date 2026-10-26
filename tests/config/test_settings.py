from __future__ import annotations

import codecs
from pathlib import Path

import pytest
from pydantic import ValidationError

from fin_graph.config.settings import PersistenceBackend, Settings


def test_defaults_without_environment() -> None:
    settings = Settings(_env_file=None)

    assert settings.llm_provider == "anthropic"
    assert settings.persistence_backend is PersistenceBackend.MEMORY
    assert settings.database_url is None
    assert settings.environment == "dev"
    assert settings.mask_enabled is False


def test_environment_variables_override_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("PERSISTENCE_BACKEND", "postgres")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@db:5432/sre")

    settings = Settings(_env_file=None)

    assert settings.llm_provider == "openai"
    assert settings.persistence_backend is PersistenceBackend.POSTGRES
    assert settings.require_database_url() == "postgresql://u:p@db:5432/sre"


def test_dotenv_file_in_working_directory_is_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".env").write_text("LLM_PROVIDER=ollama\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    assert Settings().llm_provider == "ollama"


def test_missing_database_url_names_the_variable_to_set() -> None:
    with pytest.raises(ValueError, match="DATABASE_URL"):
        Settings(_env_file=None).require_database_url()


def test_dotenv_saved_with_a_bom_is_still_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".env").write_bytes(codecs.BOM_UTF8 + b"LLM_PROVIDER=ollama\n")
    monkeypatch.chdir(tmp_path)

    assert Settings().llm_provider == "ollama"


def test_unknown_persistence_backend_lists_the_valid_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PERSISTENCE_BACKEND", "postgresql")

    with pytest.raises(ValidationError, match="'memory', 'sqlite' or 'postgres'"):
        Settings(_env_file=None)


def test_blank_variables_fall_back_to_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "")
    monkeypatch.setenv("SQLITE_PATH", "")

    settings = Settings(_env_file=None)

    assert settings.llm_provider == "anthropic"
    assert settings.sqlite_path == "fin-graph.sqlite"


def test_provider_and_environment_names_are_normalized(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "  Anthropic ")
    monkeypatch.setenv("ENVIRONMENT", "PROD")

    settings = Settings(_env_file=None)

    assert settings.llm_provider == "anthropic"
    assert settings.environment == "prod"


def test_database_password_never_appears_in_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://sre:s3cr3t-pw@db:5432/sre")

    assert "s3cr3t-pw" not in repr(Settings(_env_file=None))


def test_comma_separated_lists_are_split(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTO_APPROVE_TOOLS", "alertmanager_create_silence, k8s_scale")
    monkeypatch.setenv("MASK_KINDS", "pod,ip_address")

    settings = Settings(_env_file=None)

    assert settings.auto_approve_tools == ("alertmanager_create_silence", "k8s_scale")
    assert settings.mask_kinds == ("pod", "ip_address")


def test_extra_mask_patterns_are_read_as_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MASK_EXTRA_REGEX", '{"ticket": "TCK-[0-9]+"}')

    assert Settings(_env_file=None).mask_extra_regex == {"ticket": "TCK-[0-9]+"}


def test_embedding_provider_is_optional_and_normalized(monkeypatch: pytest.MonkeyPatch) -> None:
    assert Settings(_env_file=None).embeddings_provider is None
    monkeypatch.setenv("EMBEDDINGS_PROVIDER", "  OPENAI ")
    assert Settings(_env_file=None).embeddings_provider == "openai"


def test_finance_settings_defaults_and_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    defaults = Settings(_env_file=None)

    assert defaults.base_currency == "USD" and defaults.initial_cash == 100_000
    assert defaults.market_data_provider == "yahoo" and defaults.fin_analyst_mode == "auto"
    assert defaults.trading_short_selling_enabled is False and defaults.trading_kill_switch is False

    monkeypatch.setenv("BASE_CURRENCY", "cop")
    monkeypatch.setenv("TRADING_ALLOWED_ACTIONS", "buy, sell")
    monkeypatch.setenv("FIN_ANALYST_MODE", "tool_plan")
    configured = Settings(_env_file=None)

    assert configured.base_currency == "COP"
    assert configured.trading_allowed_actions == ("buy", "sell")
    assert configured.fin_analyst_mode == "tool_plan"
    with pytest.raises(ValueError):
        Settings(_env_file=None, base_currency="dollars")
    with pytest.raises(ValueError):
        Settings(_env_file=None, margin_requirement=1.5)
