"""Translate the historical model catalog onto native provider configuration."""

from __future__ import annotations

from typing import Any

from fin_graph.compat.ai_hedge_fund.schemas import ModelProvider
from fin_graph.config.settings import Settings
from fin_graph.llm.models import ModelRole
from fin_graph.llm.models import get_model as _native_get_model
from fin_graph.server.compat.models import CLOUD_MODELS


def get_default_model_config() -> tuple[str, str]:
    options = Settings()
    return options.llm_model_reasoning or "gpt-4.1", options.llm_provider


def get_models_list() -> list[dict[str, str]]:
    return [dict(item) for item in CLOUD_MODELS]


def find_model_by_name(model_name: str) -> dict[str, str] | None:
    return next((item for item in get_models_list() if item["model_name"] == model_name), None)


def get_model_info(model_name: str, model_provider: str) -> dict[str, str] | None:
    return next(
        (
            item
            for item in get_models_list()
            if item["model_name"] == model_name and item["provider"] == model_provider
        ),
        None,
    )


def get_model(
    model_name: str,
    model_provider: ModelProvider | str,
    api_keys: dict[str, str] | None = None,
) -> Any:
    del api_keys
    provider = model_provider.value if isinstance(model_provider, ModelProvider) else model_provider
    settings = Settings(
        llm_provider=provider,
        llm_model_reasoning=model_name,
        _env_file=None,
    )
    return _native_get_model(ModelRole.REASONING, settings)


normalize_model_provider = lambda value: ModelProvider(value)  # noqa: E731

__all__ = [
    "ModelProvider",
    "find_model_by_name",
    "get_default_model_config",
    "get_model",
    "get_model_info",
    "get_models_list",
    "normalize_model_provider",
]
