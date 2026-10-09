"""Chat models per agent role, built with LangChain's ``init_chat_model``."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel

from sre_graph.config.settings import Settings


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


#: Defaults per provider. Any other provider must set ``LLM_MODEL_<ROLE>`` explicitly.
DEFAULT_MODELS: dict[str, dict[ModelRole, str]] = {
    "anthropic": {
        ModelRole.REASONING: "claude-sonnet-5-5",
        ModelRole.WORKER: "claude-sonnet-5-5",
        ModelRole.FAST: "claude-haiku-4-5-20251001",
        ModelRole.JUDGE: "claude-sonnet-5-5",
    },
}


def _role_override(role: ModelRole, settings: Settings) -> str | None:
    return {
        ModelRole.REASONING: settings.llm_model_reasoning,
        ModelRole.WORKER: settings.llm_model_worker,
        ModelRole.FAST: settings.llm_model_fast,
        ModelRole.JUDGE: settings.llm_model_judge,
    }[role]


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


def get_model(role: ModelRole, settings: Settings) -> BaseChatModel:
    """Build the chat model for ``role``; nothing reaches the network until it is invoked."""
    spec = resolve_model_spec(role, settings)
    model = init_chat_model(
        spec.model,
        model_provider=spec.provider,
        temperature=settings.llm_temperature,
        timeout=settings.llm_timeout_seconds,
        max_retries=settings.llm_max_retries,
    )
    if not isinstance(model, BaseChatModel):
        raise TypeError(f"init_chat_model returned {type(model).__name__}, not a chat model")
    return model
