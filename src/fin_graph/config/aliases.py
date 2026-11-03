"""Shared normalization for model providers and environment aliases."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

_PROVIDER_ALIASES = {
    "alibaba": "alibaba",
    "anthropic": "anthropic",
    "azure openai": "azure_openai",
    "azure_openai": "azure_openai",
    "deepseek": "deepseek",
    "gigachat": "gigachat",
    "google": "google_genai",
    "google_genai": "google_genai",
    "groq": "groq",
    "kimi": "kimi",
    "meta": "meta",
    "mistral": "mistralai",
    "mistralai": "mistralai",
    "ollama": "ollama",
    "openai": "openai",
    "openrouter": "openrouter",
    "xai": "xai",
}


def normalize_model_provider(value: str) -> str:
    """Map a provider label to the runtime provider identifier."""

    normalized = " ".join(value.strip().lower().split())
    canonical = _PROVIDER_ALIASES.get(normalized)
    if canonical is None:
        raise ValueError(f"unsupported model provider: {value.strip()}")
    return canonical


def resolve_alias(
    env: Mapping[str, str],
    modern: str,
    legacy: Sequence[str],
) -> str | None:
    """Resolve one setting while rejecting contradictory named variables."""

    names = tuple(dict.fromkeys((modern, *legacy)))
    present = {name: env[name] for name in names if env.get(name, "").strip()}
    if not present:
        return None

    selected_name = modern if modern in present else next(name for name in names if name in present)
    selected = present[selected_name]
    conflicts = [name for name, value in present.items() if value != selected]
    if conflicts:
        conflicting_names = ", ".join(name for name in names if name in present)
        raise ValueError(f"conflicting environment variables: {conflicting_names}")
    return selected
