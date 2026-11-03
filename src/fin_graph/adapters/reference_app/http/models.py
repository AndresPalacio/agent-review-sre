"""Frozen language-model catalog with live local Ollama additions."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

router = APIRouter(prefix="/language-models")

CLOUD_MODELS = (
    {"display_name": "Fable 5", "model_name": "claude-fable-5", "provider": "Anthropic"},
    {"display_name": "Opus 4.8", "model_name": "claude-opus-4-8", "provider": "Anthropic"},
    {"display_name": "Grok 4.3", "model_name": "grok-4.3", "provider": "xAI"},
    {
        "display_name": "DeepSeek V4 Pro",
        "model_name": "deepseek-v4-pro",
        "provider": "DeepSeek",
    },
    {"display_name": "GPT-5.5", "model_name": "gpt-5.5", "provider": "OpenAI"},
    {"display_name": "Kimi K2.6", "model_name": "kimi-k2.6", "provider": "Kimi"},
    {
        "display_name": "Gemini 3.1 Pro",
        "model_name": "gemini-3.1-pro-preview",
        "provider": "Google",
    },
)


@router.get("/")
async def language_models(request: Request) -> dict[str, list[dict[str, str]]]:
    values = [dict(item) for item in CLOUD_MODELS]
    ollama = getattr(request.app.state, "reference_app_ollama", None)
    if ollama is not None:
        try:
            values.extend(await ollama.get_available_models())
        except Exception as exc:
            raise HTTPException(500, f"Failed to retrieve models: {exc}") from exc
    return {"models": values}


@router.get("/providers")
async def language_model_providers() -> dict[str, list[dict[str, object]]]:
    providers: dict[str, dict[str, object]] = {}
    for model in CLOUD_MODELS:
        name = model["provider"]
        provider = providers.setdefault(name, {"name": name, "models": []})
        models = provider["models"]
        if isinstance(models, list):
            models.append(
                {"display_name": model["display_name"], "model_name": model["model_name"]}
            )
    return {"providers": list(providers.values())}
