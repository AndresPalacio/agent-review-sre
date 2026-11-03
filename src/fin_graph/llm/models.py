"""Chat models per agent role, built with LangChain's ``init_chat_model``."""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel

from fin_graph.compat.settings import normalize_model_provider
from fin_graph.config.settings import Settings


class ModelRole(StrEnum):
    """The job a model does; every role can point at a different model."""

    REASONING = "reasoning"
    WORKER = "worker"
    FAST = "fast"
    JUDGE = "judge"


@dataclass(frozen=True)
class ModelSpec:
    """A provider plus a model name exactly as that provider spells it."""

    provider: str
    model: str


#: The only local model with tool support; every role runs on it.
OLLAMA_DEFAULT_MODEL = "llama3.2:3b"

#: Defaults per provider. Any other provider must set ``LLM_MODEL_<ROLE>`` explicitly.
DEFAULT_MODELS: dict[str, dict[ModelRole, str]] = {
    "anthropic": {
        ModelRole.REASONING: "claude-sonnet-5-5",
        ModelRole.WORKER: "claude-sonnet-5-5",
        ModelRole.FAST: "claude-haiku-4-5-20251001",
        ModelRole.JUDGE: "claude-sonnet-5-5",
    },
    "ollama": dict.fromkeys(ModelRole, OLLAMA_DEFAULT_MODEL),
}


def _role_override(role: ModelRole, settings: Settings) -> str | None:
    return {
        ModelRole.REASONING: settings.llm_model_reasoning,
        ModelRole.WORKER: settings.llm_model_worker,
        ModelRole.FAST: settings.llm_model_fast,
        ModelRole.JUDGE: settings.llm_model_judge,
    }[role]


#: Environment variable each hosted provider reads its key from; local providers need none.
PROVIDER_KEY_ENV = {
    "anthropic": "ANTHROPIC_API_KEY",
    "azure_openai": "AZURE_OPENAI_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "gigachat": "GIGACHAT_API_KEY",
    "groq": "GROQ_API_KEY",
    "kimi": "MOONSHOT_API_KEY",
    "mistralai": "MISTRAL_API_KEY",
    "openai": "OPENAI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "google_genai": "GOOGLE_API_KEY",
    "xai": "XAI_API_KEY",
}


def missing_credentials(provider: str) -> str | None:
    """The key variable a hosted provider needs and the process lacks, else ``None``."""
    name = PROVIDER_KEY_ENV.get(provider)
    return name if name and not os.environ.get(name) else None


def resolve_model_spec(role: ModelRole, settings: Settings) -> ModelSpec:
    """Pick the model for ``role``: an explicit setting wins over the provider default."""
    override = _role_override(role, settings)
    if override:
        return ModelSpec(provider=settings.llm_provider, model=override)
    defaults = DEFAULT_MODELS.get(settings.llm_provider)
    if defaults is None:
        raise ValueError(
            f"No default models for provider {settings.llm_provider!r}; "
            f"set LLM_MODEL_{role.value.upper()}"
        )
    return ModelSpec(provider=settings.llm_provider, model=defaults[role])


def provider_kwargs(provider: str, settings: Settings) -> dict[str, Any]:
    """Client options per provider; ChatOllama has no retry option and takes httpx kwargs."""
    if provider == "ollama":
        return {
            "base_url": settings.ollama_base_url,
            "num_ctx": settings.llm_num_ctx,
            "num_predict": settings.llm_num_predict,
            "client_kwargs": {"timeout": settings.llm_timeout_seconds},
        }
    return {"timeout": settings.llm_timeout_seconds, "max_retries": settings.llm_max_retries}


def build_chat_model(
    spec: ModelSpec, settings: Settings, *, temperature: float | None = None
) -> BaseChatModel:
    """Build ``spec`` with its provider options; nothing reaches the network until invoked."""
    model = init_chat_model(
        spec.model,
        model_provider=spec.provider,
        temperature=settings.llm_temperature if temperature is None else temperature,
        **provider_kwargs(spec.provider, settings),
    )
    if not isinstance(model, BaseChatModel):
        raise TypeError(f"init_chat_model returned {type(model).__name__}, not a chat model")
    return model


def parse_model_spec(value: str) -> ModelSpec:
    """Parse ``provider:model``; only the first colon splits, so ``ollama:llama3.2:3b`` works."""
    provider, separator, name = value.partition(":")
    model_name = name.strip()
    if not separator or not provider.strip() or not model_name:
        raise ValueError("model must be provider:model")
    return ModelSpec(provider=normalize_model_provider(provider), model=model_name)


def get_model(role: ModelRole, settings: Settings) -> BaseChatModel:
    """Build the chat model for ``role``; nothing reaches the network until it is invoked."""
    return build_chat_model(resolve_model_spec(role, settings), settings)
