# Fase 0 — Fundaciones de sre-graph: plan de implementación

> **Para agentes:** SUB-SKILL REQUERIDA: usa superpowers:subagent-driven-development (recomendado) o superpowers:executing-plans para implementar este plan tarea por tarea. Los pasos usan checkboxes (`- [ ]`).

**Goal:** dejar listo el repo `sre-graph` verificado offline, con:
- configuración, modelos LLM por rol y persistencia (memoria/SQLite/Postgres);
- el kit de agentes portado del starter kit y un middleware de caché de tools;
- tests de contrato de LangGraph y un grafo demo visible en LangGraph Studio.

**Architecture:**
- Paquete Python `sre_graph` (layout `src/`) en capas con dependencias solo hacia abajo: `graphs → agents → llm | persistence → config`, validado por import-linter.
- Los agentes se construyen con `create_agent` (LangChain v1) a través de una única costura, `make_agent`.
- El patrón supervisor/subagentes se porta del starter kit.
- Los tests corren grafos reales sin red con un modelo guionado (`ScriptedChatModel`).

**Tech Stack:** Python 3.12, uv, LangGraph 1.2.14, LangChain 1.4.3 / langchain-core 1.6.7, langgraph-checkpoint-sqlite 3.1.1, langgraph-checkpoint-postgres 3.1.2 (psycopg 3), pydantic-settings 2.15, pytest 9 + pytest-asyncio 1.4, ruff 0.16, mypy 2.4, import-linter 2.15, Docker (`pgvector/pgvector:pg17`), langgraph-cli 0.4.33.

**Spec:** `docs/specs/2026-10-07-sre-graph-design.md`

> **Alcance:** solo la Fase 0. Las Fases 1 y 2 reciben su propio plan al cerrar esta, porque dependen de lo que confirme el spike y de las versiones que fije `uv.lock`.
> Todo el código de este plan ya se ejecutó en un borrador sobre Windows 11 con: 43 tests (incluidos 2 contra Postgres en Docker), ruff, mypy `strict` e import-linter en verde.

## Global Constraints

- **Lenguaje:** Python 3.12. El servidor FastAPI propio llega en la Fase 4; `langgraph.json` es solo para LangGraph Studio en desarrollo.
- **Modelos:** nativos de LangChain (`init_chat_model`), con el proveedor por entorno (`LLM_PROVIDER`, `LLM_MODEL_<ROL>`).
  - Defaults de Anthropic: `claude-sonnet-5-5` en reasoning/worker/judge y `claude-haiku-4-5-20251001` en fast.
- **Sin opensre:** cero dependencia (ni import ni paquete); opensre es solo referencia.
- **Logs y trazas:**
  - Nada persistido; logging operativo a stdout.
  - Sin LangSmith ni OTel: `LANGSMITH_TRACING=false` en `.env.example`, y el plugin de pytest de LangSmith desactivado.
- **Estado del grafo:** `TypedDict` con valores JSON-serializables; Pydantic solo en las fronteras.
- **Secretos:**
  - Nunca en el estado ni en los args del modelo; los clientes viajan en el runtime context.
  - El context se reconstruye en cada invoke y en cada resume.
- **Checkpointer:**
  - Solo el grafo más externo lo lleva.
  - `langgraph.json` exporta fábricas compiladas sin checkpointer.
- **Capas:** dependencias solo hacia abajo, con contrato de import-linter.
- **Repo:** `C:\Users\USUARIO\Desktop\devops-telemetri\sre-graph`, paquete `sre_graph`, rama de trabajo `fase-0`.
- **Atribución:** `NOTICE` (Apache-2.0) para el material adaptado.
- **Commits:** cada commit termina con el trailer `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **`.env` guardado con BOM** (Bloc de notas en Windows) → se lee igual. Test: Tarea 1, `test_dotenv_saved_with_a_bom_is_still_read`.
2. **Typo en `PERSISTENCE_BACKEND`** (p. ej. `postgresql`) → error al arrancar que lista los valores válidos, no un fallback silencioso. Test: Tarea 1, `test_unknown_persistence_backend_lists_the_valid_values`.
3. **Valores vacíos copiados de `.env.example`** (`LLM_MODEL_FAST=`) → se usa el default del proveedor, no un nombre de modelo vacío. Test: Tarea 2, `test_blank_role_variable_falls_back_to_the_provider_default`.
4. **`SQLITE_PATH` dentro de una carpeta que todavía no existe** (`data/…`) → se crea la carpeta, en vez de fallar con `unable to open database file`. Test: Tarea 3, `test_sqlite_path_in_a_missing_folder_is_created`.
5. **El modelo repite una tool con los mismos argumentos en otro orden** → acierto de caché, sin re-ejecución. Test: Tarea 7, `test_same_arguments_in_a_different_order_hit_the_cache`.

## Decisiones tomadas al validar (ajustes menores a la spec)

- **Persistencia explícita.** No se porta el "resolvedor de persistencia" implícito del starter kit. En Python, las raíces de composición abren `open_checkpointer` y `open_store` explícitamente, sin estado global.
- **Middleware real, no esqueletos.** La Fase 0 implementa solo `ToolResultCacheMiddleware`, que además sirve como spike de `wrap_tool_call`. El resto llega con su código de dominio: masking y guardrails en la Fase 1, evidencia en la 2, evidence-gate en la 5.
- **Sin handoffs/swarm.** No se portan del starter kit porque el diseño no los usa (YAGNI).
- **Variables de entorno sin prefijo**, igual que en la spec.

## Mapa de archivos

| Archivo | Responsabilidad |
|---|---|
| `pyproject.toml` | Dependencias, build (hatchling) y config de pytest, ruff, mypy e import-linter |
| `.python-version`, `.gitignore`, `.env.example`, `LICENSE`, `NOTICE`, `README.md` | Entorno y documentación base |
| `docker-compose.yml` | Postgres + pgvector local en el puerto 5433 |
| `langgraph.json` | Grafos expuestos a LangGraph Studio |
| `.github/workflows/ci.yml` | Gate de calidad en CI, con Postgres de servicio |
| `src/sre_graph/config/settings.py` | `Settings` y `PersistenceBackend` |
| `src/sre_graph/llm/models.py` | `ModelRole`, `ModelSpec`, `resolve_model_spec`, `get_model` |
| `src/sre_graph/persistence/paths.py` | `prepare_sqlite_path` |
| `src/sre_graph/persistence/checkpointer.py` | `open_checkpointer` |
| `src/sre_graph/persistence/store.py` | `open_store` |
| `src/sre_graph/agents/testing/scripted_model.py` | `ScriptedChatModel`, `tool_call_message`, `ScriptedModelExhaustedError` |
| `src/sre_graph/agents/factory.py` | `make_agent` |
| `src/sre_graph/agents/subagents.py` | `SubagentSpec`, `subagent_tool`, `make_supervisor` |
| `src/sre_graph/agents/middleware/tool_cache.py` | `ToolResultCacheMiddleware` |
| `src/sre_graph/graphs/demo.py` | `make_demo_graph` (se retira en la Fase 2) |
| `tests/**` | Un archivo de test por módulo, más `tests/spikes/` (contratos de LangGraph) |
| `docs/spikes/fase-0-mecanismos.md` | Hallazgos del spike |

---

### Task 1: Esqueleto del repo, toolchain y `Settings`

**Files:**
- Create: `pyproject.toml`, `.python-version`, `.gitignore`, `.env.example`, `LICENSE`, `NOTICE`, `README.md`
- Create: `src/sre_graph/__init__.py`, `src/sre_graph/py.typed`, y `__init__.py` en `config/`, `llm/`, `persistence/`, `agents/`, `agents/middleware/`, `agents/testing/`, `graphs/`
- Create: `src/sre_graph/config/settings.py`
- Test: `tests/conftest.py`, `tests/config/test_settings.py`

**Interfaces:**
- Consumes: nada.
- Produces:
  - `class PersistenceBackend(StrEnum)` con los valores `MEMORY="memory"`, `SQLITE="sqlite"` y `POSTGRES="postgres"`.
  - `class Settings(BaseSettings)` con los campos `llm_provider: str`, `llm_model_reasoning/worker/fast/judge: str | None`, `llm_temperature: float`, `llm_timeout_seconds: float`, `llm_max_retries: int`, `persistence_backend: PersistenceBackend`, `sqlite_path: str` y `database_url: str | None`.
  - El método `Settings.require_database_url() -> str`.
  - Los tests construyen `Settings(_env_file=None, **overrides)` para no leer el `.env` del desarrollador.

- [ ] **Step 1: Prerrequisitos**

Run: `uv --version`
Expected: `uv 0.x.y`. Si falla, instalar uv y abrir una terminal nueva:

```bash
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
export PATH="$HOME/.local/bin:$PATH"   # en Git Bash, si la terminal no se reinicia
uv --version
```

Run: `git config user.email`
Expected: un email. Si está vacío, configurar `git config --global user.name` y `user.email` antes de seguir.

- [ ] **Step 2: Rama de trabajo**

```bash
cd /c/Users/USUARIO/Desktop/devops-telemetri/sre-graph
git checkout -b fase-0
```

Expected: `Switched to a new branch 'fase-0'`

- [ ] **Step 3: Archivos de proyecto**

`pyproject.toml`:

```toml
[project]
name = "sre-graph"
version = "0.1.0"
description = "Multi-agent incident investigation (RCA) built natively on LangGraph"
readme = "README.md"
requires-python = ">=3.12,<3.13"
license = "Apache-2.0"
dependencies = [
    "langgraph>=1.2.14,<1.3",
    "langchain>=1.4.3,<1.5",
    "langchain-core>=1.6.7,<1.7",
    "langchain-anthropic>=1.7.5",
    "langchain-openai>=1.6.7",
    "langgraph-checkpoint-sqlite>=3.1.1",
    "langgraph-checkpoint-postgres>=3.1.2",
    "psycopg[binary,pool]>=3.3.6",
    "aiosqlite>=0.22.1",
    "pydantic>=2.13",
    "pydantic-settings>=2.15",
]

[dependency-groups]
dev = [
    "pytest>=9.1",
    "pytest-asyncio>=1.4",
    "ruff>=0.16",
    "mypy>=2.4",
    "import-linter>=2.15",
    "langgraph-cli[inmem]>=0.4.33",
]

[build-system]
requires = ["hatchling>=1.27"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/sre_graph"]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-ra --import-mode=importlib -p no:langsmith_plugin"
asyncio_mode = "auto"
asyncio_default_fixture_loop_scope = "function"
markers = [
    "postgres: needs SRE_GRAPH_TEST_DATABASE_URL pointing at Postgres with pgvector",
]

[tool.ruff]
line-length = 100
target-version = "py312"
src = ["src", "tests"]

[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP", "SIM", "RUF", "ASYNC"]

[tool.mypy]
python_version = "3.12"
strict = true
plugins = ["pydantic.mypy"]
mypy_path = "src"
packages = ["sre_graph"]

[tool.importlinter]
root_package = "sre_graph"

[[tool.importlinter.contracts]]
name = "sre-graph layers (dependencies point downward only)"
type = "layers"
layers = [
    "sre_graph.graphs",
    "sre_graph.agents",
    "sre_graph.llm | sre_graph.persistence",
    "sre_graph.config",
]
```

`.python-version`:

```text
3.12
```

`.gitignore`:

```gitignore
.venv/
__pycache__/
*.py[cod]
.pytest_cache/
.mypy_cache/
.ruff_cache/
.env
*.sqlite
*.sqlite-shm
*.sqlite-wal
data/
.langgraph_api/
dist/
build/
```

`.env.example`:

```dotenv
# Proveedor LLM y modelos por rol. Deja vacío un LLM_MODEL_* para usar el default del proveedor.
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=
LLM_MODEL_REASONING=
LLM_MODEL_WORKER=
LLM_MODEL_FAST=
LLM_MODEL_JUDGE=
LLM_TEMPERATURE=0

# Persistencia: memory | sqlite | postgres
PERSISTENCE_BACKEND=memory
SQLITE_PATH=data/sre-graph.sqlite
# Postgres de docker compose; connect_timeout evita esperas largas si no responde.
DATABASE_URL=postgresql://sre_graph:sre_graph@localhost:5433/sre_graph?connect_timeout=5

# Sin trazas a terceros.
LANGSMITH_TRACING=false
```

`LICENSE` (texto estándar de Apache-2.0):

```bash
cp ../opensre/LICENSE LICENSE
head -3 LICENSE
```

Expected: las primeras líneas contienen `Apache License` y `Version 2.0, January 2004`.

`NOTICE`:

```text
sre-graph
Copyright 2026 The sre-graph authors

This product includes material adapted from the following Apache-2.0 licensed projects:

* OpenSRE - https://github.com/Tracer-Cloud/opensre
  Investigation prompts, heuristics, domain rules and synthetic evaluation scenarios,
  adapted from its git history as they are ported. Nothing is imported at runtime.

* LangGraph Starter Kit - https://github.com/ac12644/langgraph-starter-kit
  Agent composition patterns (supervisor with subagents as tools) and the scripted test model.
```

`README.md` (provisional; la Tarea 10 lo completa):

```markdown
# sre-graph

Investigación multiagente de incidencias (RCA) construida de forma nativa sobre LangGraph.
Diseño: [docs/specs/2026-10-07-sre-graph-design.md](docs/specs/2026-10-07-sre-graph-design.md).
```

- [ ] **Step 4: Esqueleto del paquete y `conftest.py`**

```bash
mkdir -p src/sre_graph/{config,llm,persistence,agents/middleware,agents/testing,graphs} tests/config
printf '"""sre-graph: multi-agent incident investigation built natively on LangGraph."""\n' > src/sre_graph/__init__.py
printf '"""Runtime settings."""\n' > src/sre_graph/config/__init__.py
printf '"""Chat models per agent role."""\n' > src/sre_graph/llm/__init__.py
printf '"""Checkpointer and long-term store factories."""\n' > src/sre_graph/persistence/__init__.py
printf '"""Agent building blocks: factory, subagents, middleware and test doubles."""\n' > src/sre_graph/agents/__init__.py
printf '"""Custom agent middleware."""\n' > src/sre_graph/agents/middleware/__init__.py
printf '"""Test doubles for agents (offline, deterministic)."""\n' > src/sre_graph/agents/testing/__init__.py
printf '"""LangGraph graphs (the orchestration layer)."""\n' > src/sre_graph/graphs/__init__.py
: > src/sre_graph/py.typed
```

`tests/conftest.py`:

```python
"""Shared test configuration: event loop choice and environment isolation."""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import Callable, Mapping

import pytest

_SETTINGS_ENV = (
    "LLM_PROVIDER",
    "LLM_MODEL_REASONING",
    "LLM_MODEL_WORKER",
    "LLM_MODEL_FAST",
    "LLM_MODEL_JUDGE",
    "LLM_TEMPERATURE",
    "PERSISTENCE_BACKEND",
    "SQLITE_PATH",
    "DATABASE_URL",
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
)


def pytest_asyncio_loop_factories(
    config: pytest.Config, item: pytest.Item
) -> Mapping[str, Callable[[], asyncio.AbstractEventLoop]]:
    """psycopg's async driver needs a selector loop; Windows defaults to Proactor."""
    if sys.platform == "win32":
        return {"selector": asyncio.SelectorEventLoop}
    return {"default": asyncio.new_event_loop}


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep a developer's real settings and provider keys out of every test."""
    for name in _SETTINGS_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LANGSMITH_TRACING", "false")


@pytest.fixture
def postgres_url() -> str:
    """Connection string for the Postgres used by ``postgres``-marked tests."""
    url = os.environ.get("SRE_GRAPH_TEST_DATABASE_URL")
    if not url:
        pytest.skip("SRE_GRAPH_TEST_DATABASE_URL is not set")
    return url
```

- [ ] **Step 5: Instalar el entorno**

Run: `uv sync`
Expected: crea `.venv/` y `uv.lock`, e instala `sre-graph` en modo editable sin errores (puede tardar unos minutos por `langgraph-cli[inmem]`).

- [ ] **Step 6: Escribir los tests de `Settings` (fallan)**

`tests/config/test_settings.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from sre_graph.config.settings import PersistenceBackend, Settings


def test_defaults_without_environment() -> None:
    settings = Settings(_env_file=None)

    assert settings.llm_provider == "anthropic"
    assert settings.persistence_backend is PersistenceBackend.MEMORY
    assert settings.database_url is None


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
    (tmp_path / ".env").write_bytes("\ufeffLLM_PROVIDER=ollama\n".encode())
    monkeypatch.chdir(tmp_path)

    assert Settings().llm_provider == "ollama"


def test_unknown_persistence_backend_lists_the_valid_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PERSISTENCE_BACKEND", "postgresql")

    with pytest.raises(ValidationError, match="'memory', 'sqlite' or 'postgres'"):
        Settings(_env_file=None)
```

- [ ] **Step 7: Verificar que fallan**

Run: `uv run pytest tests/config -q`
Expected: FAIL con `ModuleNotFoundError: No module named 'sre_graph.config.settings'`.

- [ ] **Step 8: Implementar `Settings`**

`src/sre_graph/config/settings.py`:

```python
"""Runtime settings read from environment variables and an optional ``.env`` file."""

from __future__ import annotations

from enum import StrEnum

from pydantic_settings import BaseSettings, SettingsConfigDict


class PersistenceBackend(StrEnum):
    """Where graph checkpoints and the long-term store live."""

    MEMORY = "memory"
    SQLITE = "sqlite"
    POSTGRES = "postgres"


class Settings(BaseSettings):
    """Non-secret configuration; provider API keys stay in their standard variables."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    llm_provider: str = "anthropic"
    llm_model_reasoning: str | None = None
    llm_model_worker: str | None = None
    llm_model_fast: str | None = None
    llm_model_judge: str | None = None
    llm_temperature: float = 0.0
    llm_timeout_seconds: float = 120.0
    llm_max_retries: int = 2
    persistence_backend: PersistenceBackend = PersistenceBackend.MEMORY
    sqlite_path: str = "sre-graph.sqlite"
    database_url: str | None = None

    def require_database_url(self) -> str:
        """Return ``DATABASE_URL`` or fail naming the variable the operator must set."""
        if not self.database_url:
            raise ValueError("DATABASE_URL must be set when PERSISTENCE_BACKEND=postgres")
        return self.database_url
```

`env_file_encoding="utf-8"` es explícito porque el default de Windows es cp1252. El BOM ya lo descarta python-dotenv; el test lo deja fijado.

- [ ] **Step 9: Verificar que pasan y el gate de calidad**

Run: `uv run pytest tests/config -q`
Expected: `6 passed`

Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run lint-imports`
Expected: `All checks passed!`, archivos ya formateados, `Success: no issues found`, `Contracts: 1 kept, 0 broken.`

- [ ] **Step 10: Commit**

```bash
git add pyproject.toml uv.lock .python-version .gitignore .env.example LICENSE NOTICE README.md src tests docs
git commit -m "chore: scaffold sre-graph with uv, quality gates and env-driven settings" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Modelos LLM por rol

**Files:**
- Create: `src/sre_graph/llm/models.py`
- Test: `tests/llm/test_models.py`

**Interfaces:**
- Consumes: `Settings` (Tarea 1).
- Produces:
  - `class ModelRole(StrEnum)`: `REASONING`, `WORKER`, `FAST`, `JUDGE`.
  - `@dataclass(frozen=True) class ModelSpec(provider: str, model: str)`.
  - `DEFAULT_MODELS: dict[str, dict[ModelRole, str]]`.
  - `resolve_model_spec(role: ModelRole, settings: Settings) -> ModelSpec`.
  - `get_model(role: ModelRole, settings: Settings) -> BaseChatModel`.

- [ ] **Step 1: Escribir los tests (fallan)**

`tests/llm/test_models.py`:

```python
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
```

- [ ] **Step 2: Verificar que fallan**

Run: `uv run pytest tests/llm -q`
Expected: FAIL con `ModuleNotFoundError: No module named 'sre_graph.llm.models'`.

- [ ] **Step 3: Implementar**

`src/sre_graph/llm/models.py`:

```python
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
```

`model_provider` se pasa aparte para que nombres como `llama3.2:3b` no se confundan con un prefijo `provider:`.

- [ ] **Step 4: Verificar**

Run: `uv run pytest tests/llm -q`
Expected: `5 passed`

Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run lint-imports`
Expected: todo en verde.

- [ ] **Step 5: Commit**

```bash
git add src/sre_graph/llm tests/llm
git commit -m "feat(llm): resolve chat models per agent role via init_chat_model" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Persistencia (checkpointer y store) + Postgres local

**Files:**
- Create: `src/sre_graph/persistence/paths.py`, `src/sre_graph/persistence/checkpointer.py`, `src/sre_graph/persistence/store.py`, `docker-compose.yml`
- Test: `tests/persistence/test_checkpointer.py`, `tests/persistence/test_store.py`

**Interfaces:**
- Consumes: `Settings`, `PersistenceBackend` (Tarea 1).
- Produces:
  - `prepare_sqlite_path(path: str) -> str`.
  - `open_checkpointer(settings: Settings)`: async context manager que produce un `BaseCheckpointSaver[Any]`.
  - `open_store(settings: Settings)`: async context manager que produce un `BaseStore`.
  - Ambos crean sus tablas al abrir. Con SQLite, checkpointer y store comparten el mismo archivo.

- [ ] **Step 1: Escribir los tests (fallan)**

`tests/persistence/test_checkpointer.py`:

```python
from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any, TypedDict

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from sre_graph.config.settings import PersistenceBackend, Settings
from sre_graph.persistence.checkpointer import open_checkpointer


class CounterState(TypedDict, total=False):
    n: int


def _counter_graph() -> StateGraph[CounterState]:
    def increment(state: CounterState) -> dict[str, Any]:
        return {"n": state.get("n", 0) + 1}

    graph = StateGraph(CounterState)
    graph.add_node("increment", increment)
    graph.add_edge(START, "increment")
    graph.add_edge("increment", END)
    return graph


async def _write_then_reopen_and_read(settings: Settings) -> int:
    config = {"configurable": {"thread_id": f"t-{uuid.uuid4()}"}}
    async with open_checkpointer(settings) as saver:
        await _counter_graph().compile(checkpointer=saver).ainvoke({"n": 1}, config)
    async with open_checkpointer(settings) as saver:
        snapshot = await _counter_graph().compile(checkpointer=saver).aget_state(config)
    return int(snapshot.values["n"])


async def test_memory_backend_yields_in_memory_saver() -> None:
    async with open_checkpointer(Settings(_env_file=None)) as saver:
        assert isinstance(saver, InMemorySaver)


async def test_sqlite_thread_state_survives_reopening(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        persistence_backend=PersistenceBackend.SQLITE,
        sqlite_path=str(tmp_path / "state.sqlite"),
    )

    assert await _write_then_reopen_and_read(settings) == 2


@pytest.mark.postgres
async def test_postgres_thread_state_survives_reopening(postgres_url: str) -> None:
    settings = Settings(
        _env_file=None,
        persistence_backend=PersistenceBackend.POSTGRES,
        database_url=postgres_url,
    )

    assert await _write_then_reopen_and_read(settings) == 2


async def test_postgres_without_url_fails_before_connecting() -> None:
    settings = Settings(_env_file=None, persistence_backend=PersistenceBackend.POSTGRES)

    with pytest.raises(ValueError, match="DATABASE_URL"):
        async with open_checkpointer(settings):
            pass


async def test_sqlite_path_in_a_missing_folder_is_created(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        persistence_backend=PersistenceBackend.SQLITE,
        sqlite_path=str(tmp_path / "data" / "nested" / "state.sqlite"),
    )

    assert await _write_then_reopen_and_read(settings) == 2
```

`tests/persistence/test_store.py`:

```python
from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from sre_graph.config.settings import PersistenceBackend, Settings
from sre_graph.persistence.checkpointer import open_checkpointer
from sre_graph.persistence.store import open_store


async def _put_then_reopen_and_get(settings: Settings) -> object:
    key = f"note-{uuid.uuid4()}"
    async with open_store(settings) as store:
        await store.aput(("org", "notes"), key, {"text": "rollback fixed it"})
    async with open_store(settings) as store:
        item = await store.aget(("org", "notes"), key)
    return None if item is None else item.value


async def test_sqlite_store_survives_reopening(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        persistence_backend=PersistenceBackend.SQLITE,
        sqlite_path=str(tmp_path / "state.sqlite"),
    )

    assert await _put_then_reopen_and_get(settings) == {"text": "rollback fixed it"}


async def test_store_and_checkpointer_share_one_sqlite_file(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        persistence_backend=PersistenceBackend.SQLITE,
        sqlite_path=str(tmp_path / "state.sqlite"),
    )

    async with open_checkpointer(settings), open_store(settings) as store:
        await store.aput(("org", "notes"), "n1", {"text": "shared file"})
        item = await store.aget(("org", "notes"), "n1")

    assert item is not None
    assert item.value == {"text": "shared file"}


@pytest.mark.postgres
async def test_postgres_store_survives_reopening(postgres_url: str) -> None:
    settings = Settings(
        _env_file=None,
        persistence_backend=PersistenceBackend.POSTGRES,
        database_url=postgres_url,
    )

    assert await _put_then_reopen_and_get(settings) == {"text": "rollback fixed it"}
```

- [ ] **Step 2: Verificar que fallan**

Run: `uv run pytest tests/persistence -q`
Expected: FAIL con `ModuleNotFoundError: No module named 'sre_graph.persistence.checkpointer'`.

- [ ] **Step 3: Implementar**

`src/sre_graph/persistence/paths.py`:

```python
"""Filesystem preparation shared by the SQLite-backed persistence factories."""

from __future__ import annotations

from pathlib import Path

SQLITE_IN_MEMORY = ":memory:"


def prepare_sqlite_path(path: str) -> str:
    """Create the database file's parent folder so SQLite can open it, and return the path."""
    if path != SQLITE_IN_MEMORY:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    return path
```

`src/sre_graph/persistence/checkpointer.py`:

```python
"""Checkpointer per backend: memory for tests, SQLite for the local CLI, Postgres for the server."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from sre_graph.config.settings import PersistenceBackend, Settings
from sre_graph.persistence.paths import prepare_sqlite_path


@asynccontextmanager
async def open_checkpointer(settings: Settings) -> AsyncIterator[BaseCheckpointSaver[Any]]:
    """Yield a ready checkpointer for the configured backend, creating its tables if missing."""
    backend = settings.persistence_backend
    if backend is PersistenceBackend.MEMORY:
        yield InMemorySaver()
    elif backend is PersistenceBackend.SQLITE:
        path = prepare_sqlite_path(settings.sqlite_path)
        async with AsyncSqliteSaver.from_conn_string(path) as sqlite_saver:
            await sqlite_saver.setup()
            yield sqlite_saver
    else:
        url = settings.require_database_url()
        async with AsyncPostgresSaver.from_conn_string(url) as postgres_saver:
            await postgres_saver.setup()
            yield postgres_saver
```

`src/sre_graph/persistence/store.py`:

```python
"""Long-term store per backend, chosen by the same setting as the checkpointer."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from langgraph.store.base import BaseStore
from langgraph.store.memory import InMemoryStore
from langgraph.store.postgres.aio import AsyncPostgresStore
from langgraph.store.sqlite.aio import AsyncSqliteStore

from sre_graph.config.settings import PersistenceBackend, Settings
from sre_graph.persistence.paths import prepare_sqlite_path


@asynccontextmanager
async def open_store(settings: Settings) -> AsyncIterator[BaseStore]:
    """Yield a ready store; semantic indexing arrives with embeddings in Fase 5."""
    backend = settings.persistence_backend
    if backend is PersistenceBackend.MEMORY:
        yield InMemoryStore()
    elif backend is PersistenceBackend.SQLITE:
        path = prepare_sqlite_path(settings.sqlite_path)
        async with AsyncSqliteStore.from_conn_string(path) as sqlite_store:
            await sqlite_store.setup()
            yield sqlite_store
    else:
        url = settings.require_database_url()
        async with AsyncPostgresStore.from_conn_string(url) as postgres_store:
            await postgres_store.setup()
            yield postgres_store
```

`docker-compose.yml`:

```yaml
services:
  postgres:
    image: pgvector/pgvector:pg17
    environment:
      POSTGRES_USER: sre_graph
      POSTGRES_PASSWORD: sre_graph
      POSTGRES_DB: sre_graph
    ports:
      - "5433:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U sre_graph -d sre_graph"]
      interval: 5s
      timeout: 3s
      retries: 20

volumes:
  pgdata:
```

Se usa el puerto 5433 para no chocar con un Postgres local en el 5432.

- [ ] **Step 4: Verificar sin Postgres**

Run: `uv run pytest tests/persistence -q`
Expected: `6 passed, 2 skipped` (los skips son los tests `postgres`).

- [ ] **Step 5: Verificar contra Postgres real (requiere Docker Desktop iniciado)**

```bash
docker compose up -d --wait
SRE_GRAPH_TEST_DATABASE_URL="postgresql://sre_graph:sre_graph@localhost:5433/sre_graph?connect_timeout=5" uv run pytest -m postgres -q
docker compose down
```

Expected: `2 passed, … deselected`. Si Docker no está corriendo, pedir al usuario que lo inicie; no saltar este paso.

- [ ] **Step 6: Gate y commit**

Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run lint-imports`
Expected: todo en verde.

```bash
git add src/sre_graph/persistence tests/persistence docker-compose.yml
git commit -m "feat(persistence): checkpointer and store per backend with local Postgres" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Modelo guionado para tests (`ScriptedChatModel`)

**Files:**
- Create: `src/sre_graph/agents/testing/scripted_model.py`
- Test: `tests/agents/test_scripted_model.py`

**Interfaces:**
- Consumes: nada del proyecto.
- Produces:
  - `class ScriptedChatModel(BaseChatModel)`:
    - campo `responses: list[AIMessage]`;
    - propiedades `remaining: int` y `calls: list[list[BaseMessage]]`;
    - `bind_tools(...) -> ScriptedChatModel` devuelve `self`.
  - `class ScriptedModelExhaustedError(RuntimeError)`.
  - `tool_call_message(name: str, args: dict[str, Any], call_id: str) -> AIMessage`.
  - Regla: una instancia por rama paralela (`Send`), para que el orden sea determinista.

- [ ] **Step 1: Escribir los tests (fallan)**

`tests/agents/test_scripted_model.py`:

```python
from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage
from langchain_core.tools import tool

from sre_graph.agents.testing.scripted_model import (
    ScriptedChatModel,
    ScriptedModelExhaustedError,
    tool_call_message,
)


@tool
def add(a: int, b: int) -> int:
    """Add two integers."""
    return a + b


def test_replays_responses_in_order_and_records_each_call() -> None:
    model = ScriptedChatModel(responses=[AIMessage("one"), AIMessage("two")])

    assert model.invoke("first").content == "one"
    assert model.invoke("second").content == "two"
    assert model.remaining == 0
    assert [call[-1].content for call in model.calls] == ["first", "second"]


def test_running_out_of_script_raises_a_named_error() -> None:
    model = ScriptedChatModel(responses=[])

    with pytest.raises(ScriptedModelExhaustedError):
        model.invoke("anything")


def test_binding_tools_keeps_the_same_script() -> None:
    model = ScriptedChatModel(responses=[tool_call_message("add", {"a": 1, "b": 2}, "c1")])

    reply = model.bind_tools([add]).invoke("add please")

    assert isinstance(reply, AIMessage)
    assert reply.tool_calls[0]["name"] == "add"
    assert reply.tool_calls[0]["args"] == {"a": 1, "b": 2}
```

- [ ] **Step 2: Verificar que fallan**

Run: `uv run pytest tests/agents/test_scripted_model.py -q`
Expected: FAIL con `ModuleNotFoundError: No module named 'sre_graph.agents.testing.scripted_model'`.

- [ ] **Step 3: Implementar**

`src/sre_graph/agents/testing/scripted_model.py`:

```python
"""Offline chat model for tests: replays scripted AI messages in call order."""

from __future__ import annotations

import threading
from collections import deque
from collections.abc import Sequence
from typing import Any

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import PrivateAttr


class ScriptedModelExhaustedError(RuntimeError):
    """An agent asked for more responses than the script holds."""


class ScriptedChatModel(BaseChatModel):
    """Return ``responses`` one per call; bound tools never change the output.

    Agents sharing one instance consume the queue in call order. Give each
    parallel branch (for example each ``Send`` target) its own instance so the
    order stays deterministic.
    """

    responses: list[AIMessage]
    _queue: deque[AIMessage] = PrivateAttr(default_factory=deque)
    _calls: list[list[BaseMessage]] = PrivateAttr(default_factory=list)
    _lock: threading.Lock = PrivateAttr(default_factory=threading.Lock)

    def model_post_init(self, context: Any, /) -> None:
        super().model_post_init(context)
        self._queue.extend(self.responses)

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> ScriptedChatModel:
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        with self._lock:
            self._calls.append(list(messages))
            if not self._queue:
                raise ScriptedModelExhaustedError("ScriptedChatModel ran out of responses")
            message = self._queue.popleft()
        return ChatResult(generations=[ChatGeneration(message=message)])

    @property
    def remaining(self) -> int:
        """How many scripted responses are still queued."""
        return len(self._queue)

    @property
    def calls(self) -> list[list[BaseMessage]]:
        """The message list each call received, in call order."""
        return self._calls


def tool_call_message(name: str, args: dict[str, Any], call_id: str) -> AIMessage:
    """An AI message that requests exactly one tool call."""
    return AIMessage(content="", tool_calls=[{"id": call_id, "name": name, "args": args}])
```

- [ ] **Step 4: Verificar**

Run: `uv run pytest tests/agents/test_scripted_model.py -q`
Expected: `3 passed`

Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run lint-imports`
Expected: todo en verde.

- [ ] **Step 5: Commit**

```bash
git add src/sre_graph/agents/testing tests/agents/test_scripted_model.py
git commit -m "test(agents): scripted chat model to drive real graphs offline" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Fábrica de agentes (`make_agent`)

**Files:**
- Create: `src/sre_graph/agents/factory.py`
- Test: `tests/agents/test_factory.py`

**Interfaces:**
- Consumes: `ScriptedChatModel`, `tool_call_message` (Tarea 4).
- Produces: `make_agent(*, name: str, model: BaseChatModel, tools: Sequence[BaseTool] = (), system_prompt: str | None = None, response_format: Any = None, middleware: Sequence[AgentMiddleware[Any, Any]] = (), context_schema: type[Any] | None = None, checkpointer: Checkpointer = None, store: BaseStore | None = None) -> CompiledStateGraph[Any, Any, Any, Any]`.
  - Es la única costura para construir agentes en todo el proyecto.

- [ ] **Step 1: Escribir los tests (fallan)**

`tests/agents/test_factory.py`:

```python
from __future__ import annotations

from dataclasses import dataclass

from langchain.agents.middleware import ModelCallLimitMiddleware
from langchain.agents.structured_output import ToolStrategy
from langchain.tools import ToolRuntime
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool
from pydantic import BaseModel

from sre_graph.agents.factory import make_agent
from sre_graph.agents.testing.scripted_model import ScriptedChatModel, tool_call_message


class Finding(BaseModel):
    summary: str
    confidence: float


@dataclass
class TenantContext:
    tenant: str


@tool
def noop() -> str:
    """Do nothing."""
    return "ok"


def test_structured_response_comes_back_as_the_schema_type() -> None:
    model = ScriptedChatModel(
        responses=[tool_call_message("Finding", {"summary": "oom", "confidence": 0.9}, "s1")]
    )
    agent = make_agent(name="analyst", model=model, response_format=ToolStrategy(Finding))

    result = agent.invoke({"messages": [HumanMessage("analyze")]})

    assert result["structured_response"] == Finding(summary="oom", confidence=0.9)


def test_middleware_caps_a_looping_model() -> None:
    model = ScriptedChatModel(responses=[tool_call_message("noop", {}, f"c{i}") for i in range(12)])
    agent = make_agent(
        name="looper",
        model=model,
        tools=[noop],
        middleware=[ModelCallLimitMiddleware(run_limit=2, exit_behavior="end")],
    )

    result = agent.invoke({"messages": [HumanMessage("go")]})

    ai_messages = [m for m in result["messages"] if isinstance(m, AIMessage)]
    assert len(ai_messages) < 12
    assert model.remaining > 0


def test_tools_read_the_runtime_context() -> None:
    @tool
    def whoami(runtime: ToolRuntime[TenantContext]) -> str:
        """Return the tenant this run belongs to."""
        return runtime.context.tenant

    model = ScriptedChatModel(responses=[tool_call_message("whoami", {}, "w1"), AIMessage("done")])
    agent = make_agent(name="ctx", model=model, tools=[whoami], context_schema=TenantContext)

    result = agent.invoke({"messages": [HumanMessage("who")]}, context=TenantContext("acme"))

    tool_messages = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert [m.content for m in tool_messages] == ["acme"]
```

- [ ] **Step 2: Verificar que fallan**

Run: `uv run pytest tests/agents/test_factory.py -q`
Expected: FAIL con `ModuleNotFoundError: No module named 'sre_graph.agents.factory'`.

- [ ] **Step 3: Implementar**

`src/sre_graph/agents/factory.py`:

```python
"""The single construction seam for agents: a thin, explicit wrapper over ``create_agent``."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool
from langgraph.graph.state import CompiledStateGraph
from langgraph.store.base import BaseStore
from langgraph.types import Checkpointer


def make_agent(
    *,
    name: str,
    model: BaseChatModel,
    tools: Sequence[BaseTool] = (),
    system_prompt: str | None = None,
    response_format: Any = None,
    middleware: Sequence[AgentMiddleware[Any, Any]] = (),
    context_schema: type[Any] | None = None,
    checkpointer: Checkpointer = None,
    store: BaseStore | None = None,
) -> CompiledStateGraph[Any, Any, Any, Any]:
    """Build a tool-calling agent graph.

    Give ``checkpointer`` and ``store`` only to the outermost graph: nested
    agents inherit them at run time, which is what lets ``interrupt()`` inside a
    nested agent pause the whole run.
    """
    return create_agent(
        model,
        tools=list(tools),
        system_prompt=system_prompt,
        middleware=list(middleware),
        response_format=response_format,
        context_schema=context_schema,
        checkpointer=checkpointer,
        store=store,
        name=name,
    )
```

- [ ] **Step 4: Verificar**

Run: `uv run pytest tests/agents/test_factory.py -q`
Expected: `3 passed`

Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run lint-imports`
Expected: todo en verde.

- [ ] **Step 5: Commit**

```bash
git add src/sre_graph/agents/factory.py tests/agents/test_factory.py
git commit -m "feat(agents): make_agent as the single construction seam over create_agent" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Supervisor con subagentes como tools

**Files:**
- Create: `src/sre_graph/agents/subagents.py`
- Test: `tests/agents/test_subagents.py`

**Interfaces:**
- Consumes: `make_agent` (Tarea 5), `ScriptedChatModel`, `tool_call_message` (Tarea 4).
- Produces:
  - `class SubagentTask(BaseModel)`, con `task: str`.
  - `@dataclass(frozen=True) class SubagentSpec(name: str, description: str, agent: CompiledStateGraph)`.
  - `subagent_result_text(result: Mapping[str, Any]) -> str`.
  - `subagent_tool(spec: SubagentSpec) -> BaseTool`: sync y async; reenvía `runtime.context`.
  - `default_supervisor_prompt(subagents) -> str`.
  - `make_supervisor(*, subagents, model, name="supervisor", system_prompt=None, tools=(), middleware=(), context_schema=None, checkpointer=None, store=None) -> CompiledStateGraph`.

- [ ] **Step 1: Escribir los tests (fallan)**

Port de `langgraph-starter-kit/tests/agents/supervisor.test.ts`, más resultado estructurado y propagación del context.

`tests/agents/test_subagents.py`:

```python
from __future__ import annotations

import json
from dataclasses import dataclass

from langchain.agents.structured_output import ToolStrategy
from langchain.tools import ToolRuntime
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command, interrupt
from pydantic import BaseModel

from sre_graph.agents.factory import make_agent
from sre_graph.agents.subagents import SubagentSpec, make_supervisor
from sre_graph.agents.testing.scripted_model import ScriptedChatModel, tool_call_message


@tool
def add(a: int, b: int) -> int:
    """Add two integers."""
    return a + b


class Finding(BaseModel):
    summary: str
    confidence: float


@dataclass
class TenantContext:
    tenant: str


def _tool_contents(result: dict[str, object]) -> list[object]:
    messages = result["messages"]
    assert isinstance(messages, list)
    return [m.content for m in messages if isinstance(m, ToolMessage)]


async def test_supervisor_sees_only_each_subagents_final_answer() -> None:
    model = ScriptedChatModel(
        responses=[
            tool_call_message("math_expert", {"task": "sum 10 and 15"}, "c1"),
            tool_call_message("add", {"a": 10, "b": 15}, "c2"),
            AIMessage("The sum of 10 and 15 is 25."),
            tool_call_message("writer", {"task": "One line: 10 + 15 = 25."}, "c3"),
            AIMessage("10 + 15 = 25, tidy."),
            AIMessage("Sum is 25. 10 + 15 = 25, tidy."),
        ]
    )
    math = make_agent(name="math_expert", model=model, tools=[add])
    writer = make_agent(name="writer", model=model)
    app = make_supervisor(
        subagents=[
            SubagentSpec("math_expert", "Delegate calculations.", math),
            SubagentSpec("writer", "Delegate writing.", writer),
        ],
        model=model,
        checkpointer=InMemorySaver(),
    )

    result = await app.ainvoke(
        {"messages": [HumanMessage("sum 10 and 15, then summarize")]},
        {"configurable": {"thread_id": "delegation"}},
    )

    assert result["messages"][-1].content == "Sum is 25. 10 + 15 = 25, tidy."
    assert _tool_contents(result) == ["The sum of 10 and 15 is 25.", "10 + 15 = 25, tidy."]


def test_synchronous_invoke_also_delegates() -> None:
    model = ScriptedChatModel(
        responses=[
            tool_call_message("math_expert", {"task": "sum 1 and 2"}, "c1"),
            AIMessage("3"),
            AIMessage("The answer is 3."),
        ]
    )
    math = make_agent(name="math_expert", model=model, tools=[add])
    app = make_supervisor(
        subagents=[SubagentSpec("math_expert", "Delegate calculations.", math)], model=model
    )

    result = app.invoke({"messages": [HumanMessage("1 + 2?")]})

    assert result["messages"][-1].content == "The answer is 3."
    assert _tool_contents(result) == ["3"]


async def test_conversation_memory_lives_on_the_supervisor_thread() -> None:
    model = ScriptedChatModel(responses=[AIMessage("Hello!"), AIMessage("You said hi.")])
    worker = make_agent(name="worker", model=model)
    app = make_supervisor(
        subagents=[SubagentSpec("worker", "Does work.", worker)],
        model=model,
        checkpointer=InMemorySaver(),
    )
    config = {"configurable": {"thread_id": "memory"}}

    await app.ainvoke({"messages": [HumanMessage("hi")]}, config)
    second = await app.ainvoke({"messages": [HumanMessage("what did I say?")]}, config)

    assert len(second["messages"]) == 4


async def test_interrupt_in_a_subagent_tool_pauses_and_resumes_the_supervisor() -> None:
    @tool
    def delete_record(record_id: str) -> str:
        """Delete a record after human approval."""
        decision = interrupt({"type": "approval_required", "record_id": record_id})
        return f"Record {record_id} deleted." if decision == "yes" else "Deletion rejected."

    model = ScriptedChatModel(
        responses=[
            tool_call_message("db_admin", {"task": "delete rec_2"}, "c1"),
            tool_call_message("delete_record", {"record_id": "rec_2"}, "c2"),
            AIMessage("Record rec_2 deleted."),
            AIMessage("Done: rec_2 was deleted after approval."),
        ]
    )
    admin = make_agent(name="db_admin", model=model, tools=[delete_record])
    app = make_supervisor(
        subagents=[SubagentSpec("db_admin", "Manages records.", admin)],
        model=model,
        checkpointer=InMemorySaver(),
    )
    config = {"configurable": {"thread_id": "approval"}}

    paused = await app.ainvoke({"messages": [HumanMessage("delete rec_2")]}, config)
    snapshot = await app.aget_state(config)
    pending = [i.value for task in snapshot.tasks for i in task.interrupts]

    assert "__interrupt__" in paused
    assert snapshot.next
    assert pending == [{"type": "approval_required", "record_id": "rec_2"}]

    resumed = await app.ainvoke(Command(resume="yes"), config)

    assert resumed["messages"][-1].content == "Done: rec_2 was deleted after approval."


async def test_structured_subagent_result_reaches_the_supervisor_as_json() -> None:
    specialist_model = ScriptedChatModel(
        responses=[tool_call_message("Finding", {"summary": "OOMKilled", "confidence": 0.8}, "f1")]
    )
    supervisor_model = ScriptedChatModel(
        responses=[
            tool_call_message("logs_specialist", {"task": "check logs"}, "c1"),
            AIMessage("Logs show OOMKilled."),
        ]
    )
    specialist = make_agent(
        name="logs_specialist", model=specialist_model, response_format=ToolStrategy(Finding)
    )
    app = make_supervisor(
        subagents=[SubagentSpec("logs_specialist", "Reads logs.", specialist)],
        model=supervisor_model,
    )

    result = await app.ainvoke({"messages": [HumanMessage("why did it crash?")]})

    [content] = _tool_contents(result)
    assert isinstance(content, str)
    assert json.loads(content) == {"summary": "OOMKilled", "confidence": 0.8}


async def test_runtime_context_reaches_the_subagents_tools() -> None:
    seen: list[str] = []

    @tool
    def whoami(runtime: ToolRuntime[TenantContext]) -> str:
        """Report which tenant this run belongs to."""
        seen.append(runtime.context.tenant)
        return runtime.context.tenant

    model = ScriptedChatModel(
        responses=[
            tool_call_message("identity", {"task": "who am I?"}, "c1"),
            tool_call_message("whoami", {}, "c2"),
            AIMessage("You are acme."),
            AIMessage("Tenant: acme."),
        ]
    )
    identity = make_agent(
        name="identity", model=model, tools=[whoami], context_schema=TenantContext
    )
    app = make_supervisor(
        subagents=[SubagentSpec("identity", "Knows the tenant.", identity)],
        model=model,
        context_schema=TenantContext,
    )

    await app.ainvoke({"messages": [HumanMessage("tenant?")]}, context=TenantContext("acme"))

    assert seen == ["acme"]
```

- [ ] **Step 2: Verificar que fallan**

Run: `uv run pytest tests/agents/test_subagents.py -q`
Expected: FAIL con `ModuleNotFoundError: No module named 'sre_graph.agents.subagents'`.

- [ ] **Step 3: Implementar**

`src/sre_graph/agents/subagents.py`:

```python
"""Subagents pattern: compiled agents that a supervisor calls as tools."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain.tools import ToolRuntime
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage
from langchain_core.tools import BaseTool, StructuredTool
from langgraph.graph.state import CompiledStateGraph
from langgraph.store.base import BaseStore
from langgraph.types import Checkpointer
from pydantic import BaseModel, Field

from sre_graph.agents.factory import make_agent


class SubagentTask(BaseModel):
    """The input a supervisor writes for a subagent."""

    task: str = Field(description="A self-contained task with all the context the agent needs.")


@dataclass(frozen=True)
class SubagentSpec:
    """A compiled agent plus the routing text the supervisor's model reads."""

    name: str
    description: str
    agent: CompiledStateGraph[Any, Any, Any, Any]


def subagent_result_text(result: Mapping[str, Any]) -> str:
    """Render a subagent run for its supervisor: structured JSON first, else the last message."""
    structured = result.get("structured_response")
    if isinstance(structured, BaseModel):
        return structured.model_dump_json()
    if structured is not None:
        return json.dumps(structured, default=str)
    messages = result.get("messages") or []
    if not messages:
        return "(no response)"
    content = messages[-1].content
    return content if isinstance(content, str) else json.dumps(content, default=str)


def subagent_tool(spec: SubagentSpec) -> BaseTool:
    """Expose ``spec.agent`` as a tool; the subagent sees only the task, in its own context.

    The caller's runtime context (where clients and credentials live) is
    forwarded so the subagent's tools can use it.
    """
    agent = spec.agent

    def _run(task: str, runtime: ToolRuntime) -> str:
        result = agent.invoke({"messages": [HumanMessage(task)]}, context=runtime.context)
        return subagent_result_text(result)

    async def _arun(task: str, runtime: ToolRuntime) -> str:
        result = await agent.ainvoke({"messages": [HumanMessage(task)]}, context=runtime.context)
        return subagent_result_text(result)

    return StructuredTool.from_function(
        func=_run,
        coroutine=_arun,
        name=spec.name,
        description=spec.description,
        args_schema=SubagentTask,
    )


def default_supervisor_prompt(subagents: Sequence[SubagentSpec]) -> str:
    """The coordinator prompt used when the caller supplies none."""
    names = ", ".join(spec.name for spec in subagents)
    return (
        "You coordinate a team of specialists. Delegate work to them via your tools "
        f"({names}) and answer the user only once the delegated work is done."
    )


def make_supervisor(
    *,
    subagents: Sequence[SubagentSpec],
    model: BaseChatModel,
    name: str = "supervisor",
    system_prompt: str | None = None,
    tools: Sequence[BaseTool] = (),
    middleware: Sequence[AgentMiddleware[Any, Any]] = (),
    context_schema: type[Any] | None = None,
    checkpointer: Checkpointer = None,
    store: BaseStore | None = None,
) -> CompiledStateGraph[Any, Any, Any, Any]:
    """A supervisor agent whose tools are its subagents, plus any direct ``tools``."""
    return make_agent(
        name=name,
        model=model,
        tools=[*(subagent_tool(spec) for spec in subagents), *tools],
        system_prompt=system_prompt or default_supervisor_prompt(subagents),
        middleware=middleware,
        context_schema=context_schema,
        checkpointer=checkpointer,
        store=store,
    )
```

- [ ] **Step 4: Verificar**

Run: `uv run pytest tests/agents/test_subagents.py -q`
Expected: `6 passed`

Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run lint-imports`
Expected: todo en verde.

- [ ] **Step 5: Commit**

```bash
git add src/sre_graph/agents/subagents.py tests/agents/test_subagents.py
git commit -m "feat(agents): supervisor with subagents as tools, forwarding runtime context" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Middleware de caché de resultados de tools

**Files:**
- Create: `src/sre_graph/agents/middleware/tool_cache.py`
- Test: `tests/agents/middleware/test_tool_cache.py`

**Interfaces:**
- Consumes: `make_agent` (Tarea 5), `ScriptedChatModel`, `tool_call_message` (Tarea 4).
- Produces: `class ToolResultCacheMiddleware(AgentMiddleware[Any, Any])`.
  - `__init__(*, ttl_seconds: float = 300.0, max_entries: int = 512, clock: Callable[[], float] = time.monotonic)`.
  - Atributo `hits: int`.
  - Implementa `wrap_tool_call` y `awrap_tool_call`. Un middleware solo síncrono falla bajo `ainvoke`, como confirmó el spike.
  - Clave = (`thread_id`, tool, args) con orden de claves canónico.
  - No cachea errores ni resultados `Command`.

- [ ] **Step 1: Escribir los tests (fallan)**

`tests/agents/middleware/test_tool_cache.py`:

```python
from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import ToolException, tool

from sre_graph.agents.factory import make_agent
from sre_graph.agents.middleware.tool_cache import ToolResultCacheMiddleware
from sre_graph.agents.testing.scripted_model import ScriptedChatModel, tool_call_message


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _two_identical_calls() -> ScriptedChatModel:
    return ScriptedChatModel(
        responses=[
            tool_call_message("get_pods", {"namespace": "payments"}, "first"),
            tool_call_message("get_pods", {"namespace": "payments"}, "second"),
            AIMessage("final"),
        ]
    )


def _one_call(call_id: str) -> ScriptedChatModel:
    return ScriptedChatModel(
        responses=[
            tool_call_message("get_pods", {"namespace": "payments"}, call_id),
            AIMessage("final"),
        ]
    )


def _thread(thread_id: str) -> dict[str, Any]:
    return {"configurable": {"thread_id": thread_id}}


def test_repeated_identical_call_is_served_from_cache() -> None:
    executions: list[str] = []

    @tool
    def get_pods(namespace: str) -> str:
        """List pods in a namespace."""
        executions.append(namespace)
        return f"3 pods in {namespace}"

    cache = ToolResultCacheMiddleware()
    agent = make_agent(
        name="k8s", model=_two_identical_calls(), tools=[get_pods], middleware=[cache]
    )

    result = agent.invoke({"messages": [HumanMessage("go")]}, _thread("t1"))

    tool_messages = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert executions == ["payments"]
    assert cache.hits == 1
    assert [m.tool_call_id for m in tool_messages] == ["first", "second"]
    assert [m.content for m in tool_messages] == ["3 pods in payments", "3 pods in payments"]


async def test_async_runs_use_the_cache_too() -> None:
    executions: list[str] = []

    @tool
    async def get_pods(namespace: str) -> str:
        """List pods in a namespace."""
        executions.append(namespace)
        return f"3 pods in {namespace}"

    cache = ToolResultCacheMiddleware()
    agent = make_agent(
        name="k8s", model=_two_identical_calls(), tools=[get_pods], middleware=[cache]
    )

    await agent.ainvoke({"messages": [HumanMessage("go")]}, _thread("t1"))

    assert executions == ["payments"]
    assert cache.hits == 1


def test_other_threads_do_not_share_entries() -> None:
    executions: list[str] = []

    @tool
    def get_pods(namespace: str) -> str:
        """List pods in a namespace."""
        executions.append(namespace)
        return f"3 pods in {namespace}"

    cache = ToolResultCacheMiddleware()
    for thread_id in ("incident-a", "incident-b"):
        agent = make_agent(
            name="k8s", model=_one_call(thread_id), tools=[get_pods], middleware=[cache]
        )
        agent.invoke({"messages": [HumanMessage("go")]}, _thread(thread_id))

    assert executions == ["payments", "payments"]
    assert cache.hits == 0


def test_entries_expire_after_the_ttl() -> None:
    executions: list[str] = []

    @tool
    def get_pods(namespace: str) -> str:
        """List pods in a namespace."""
        executions.append(namespace)
        return f"3 pods in {namespace}"

    clock = FakeClock()
    cache = ToolResultCacheMiddleware(ttl_seconds=300, clock=clock)
    make_agent(name="k8s", model=_one_call("a"), tools=[get_pods], middleware=[cache]).invoke(
        {"messages": [HumanMessage("go")]}, _thread("t1")
    )
    clock.now = 301.0
    make_agent(name="k8s", model=_one_call("b"), tools=[get_pods], middleware=[cache]).invoke(
        {"messages": [HumanMessage("go")]}, _thread("t1")
    )

    assert executions == ["payments", "payments"]
    assert cache.hits == 0


def test_error_results_are_retried_not_cached() -> None:
    attempts: list[str] = []

    @tool
    def get_pods(namespace: str) -> str:
        """List pods in a namespace."""
        attempts.append(namespace)
        raise ToolException("cluster unreachable")

    get_pods.handle_tool_error = True
    cache = ToolResultCacheMiddleware()
    agent = make_agent(
        name="k8s", model=_two_identical_calls(), tools=[get_pods], middleware=[cache]
    )

    result = agent.invoke({"messages": [HumanMessage("go")]}, _thread("t1"))

    tool_messages = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert attempts == ["payments", "payments"]
    assert cache.hits == 0
    assert all(m.status == "error" for m in tool_messages)


def test_same_arguments_in_a_different_order_hit_the_cache() -> None:
    executions: list[tuple[str, int]] = []

    @tool
    def get_logs(service: str, limit: int) -> str:
        """Fetch recent log lines for a service."""
        executions.append((service, limit))
        return f"{limit} lines from {service}"

    model = ScriptedChatModel(
        responses=[
            tool_call_message("get_logs", {"service": "checkout", "limit": 50}, "first"),
            tool_call_message("get_logs", {"limit": 50, "service": "checkout"}, "second"),
            AIMessage("final"),
        ]
    )
    cache = ToolResultCacheMiddleware()
    agent = make_agent(name="logs", model=model, tools=[get_logs], middleware=[cache])

    agent.invoke({"messages": [HumanMessage("go")]}, _thread("t1"))

    assert executions == [("checkout", 50)]
    assert cache.hits == 1
```

- [ ] **Step 2: Verificar que fallan**

Run: `uv run pytest tests/agents/middleware -q`
Expected: FAIL con `ModuleNotFoundError: No module named 'sre_graph.agents.middleware.tool_cache'`.

- [ ] **Step 3: Implementar**

`src/sre_graph/agents/middleware/tool_cache.py`:

```python
"""Serve repeated identical tool calls from a short-lived, thread-scoped cache."""

from __future__ import annotations

import json
import threading
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ToolCallRequest
from langchain_core.messages import ToolMessage
from langgraph.types import Command

ToolCallResult = ToolMessage | Command[Any]


class ToolResultCacheMiddleware(AgentMiddleware[Any, Any]):
    """Reuse a successful result when the same tool runs again with the same arguments.

    Entries are keyed by graph thread, tool name and arguments, and expire
    after ``ttl_seconds`` so a later turn that re-checks live data still
    reaches the tool. Error results are never cached.
    """

    def __init__(
        self,
        *,
        ttl_seconds: float = 300.0,
        max_entries: int = 512,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__()
        self._ttl_seconds = ttl_seconds
        self._max_entries = max_entries
        self._clock = clock
        self._entries: OrderedDict[str, tuple[float, ToolMessage]] = OrderedDict()
        self._lock = threading.Lock()
        self.hits = 0

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolCallResult],
    ) -> ToolCallResult:
        key = self._key(request)
        cached = self._lookup(key, request)
        if cached is not None:
            return cached
        result = handler(request)
        self._remember(key, result)
        return result

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolCallResult]],
    ) -> ToolCallResult:
        key = self._key(request)
        cached = self._lookup(key, request)
        if cached is not None:
            return cached
        result = await handler(request)
        self._remember(key, result)
        return result

    @staticmethod
    def _key(request: ToolCallRequest) -> str:
        config = request.runtime.config if request.runtime is not None else {}
        thread_id = str(config.get("configurable", {}).get("thread_id", ""))
        call = request.tool_call
        return json.dumps([thread_id, call["name"], call["args"]], sort_keys=True, default=str)

    def _lookup(self, key: str, request: ToolCallRequest) -> ToolMessage | None:
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            stored_at, message = entry
            if self._clock() - stored_at > self._ttl_seconds:
                del self._entries[key]
                return None
            self._entries.move_to_end(key)
            self.hits += 1
        return ToolMessage(
            content=message.content,
            tool_call_id=request.tool_call["id"] or "",
            name=request.tool_call["name"],
            artifact=message.artifact,
        )

    def _remember(self, key: str, result: ToolCallResult) -> None:
        if not isinstance(result, ToolMessage) or result.status == "error":
            return
        with self._lock:
            self._entries[key] = (self._clock(), result)
            self._entries.move_to_end(key)
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)
```

- [ ] **Step 4: Verificar**

Run: `uv run pytest tests/agents/middleware -q`
Expected: `6 passed`

Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run lint-imports`
Expected: todo en verde.

- [ ] **Step 5: Commit**

```bash
git add src/sre_graph/agents/middleware/tool_cache.py tests/agents/middleware
git commit -m "feat(agents): thread-scoped TTL cache for repeated identical tool calls" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Tests de contrato de LangGraph y documento del spike

Estos tests caracterizan comportamientos de LangGraph de los que depende el diseño. Deben pasar a la primera. Si alguno falla, **detener y revisar el diseño**: significa que la API cambió.

**Files:**
- Test: `tests/spikes/test_send_fanout.py`, `tests/spikes/test_task_in_node.py`, `tests/spikes/test_context_on_resume.py`
- Create: `docs/spikes/fase-0-mecanismos.md`

**Interfaces:**
- Consumes: `make_agent` (Tarea 5), `ScriptedChatModel`, `tool_call_message` (Tarea 4).
- Produces: contratos documentados que usarán las Fases 2, 4 y 5 (fan-out con `Send`, `@task` en `execute`, context al reanudar).

- [ ] **Step 1: Escribir los tests de contrato**

`tests/spikes/test_send_fanout.py`:

```python
"""Spike: parallel specialists via Send, each a create_agent subagent, merged by a reducer."""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict

from langchain.agents.structured_output import ToolStrategy
from langchain_core.messages import HumanMessage
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send
from pydantic import BaseModel

from sre_graph.agents.factory import make_agent
from sre_graph.agents.testing.scripted_model import ScriptedChatModel, tool_call_message


class Finding(BaseModel):
    summary: str
    confidence: float


class InvestigationState(TypedDict, total=False):
    domains: list[str]
    findings: Annotated[list[dict[str, Any]], operator.add]


async def test_each_send_branch_runs_its_own_subagent_and_findings_merge() -> None:
    scripts = {
        "logs": [tool_call_message("Finding", {"summary": "OOMKilled", "confidence": 0.8}, "l")],
        "metrics": [
            tool_call_message("Finding", {"summary": "memory 99%", "confidence": 0.7}, "m")
        ],
    }
    specialists = {
        domain: make_agent(
            name=domain,
            model=ScriptedChatModel(responses=responses),
            response_format=ToolStrategy(Finding),
        )
        for domain, responses in scripts.items()
    }

    def plan(state: InvestigationState) -> dict[str, Any]:
        return {"domains": ["logs", "metrics"]}

    def dispatch(state: InvestigationState) -> list[Send]:
        return [Send("specialist", {"domain": domain}) for domain in state["domains"]]

    async def specialist(task: dict[str, Any]) -> dict[str, Any]:
        domain = task["domain"]
        result = await specialists[domain].ainvoke(
            {"messages": [HumanMessage(f"investigate {domain}")]}
        )
        return {"findings": [result["structured_response"].model_dump()]}

    graph = StateGraph(InvestigationState)
    graph.add_node("plan", plan)
    graph.add_node("specialist", specialist)
    graph.add_node("synthesize", lambda state: {})
    graph.add_edge(START, "plan")
    graph.add_conditional_edges("plan", dispatch, ["specialist"])
    graph.add_edge("specialist", "synthesize")
    graph.add_edge("synthesize", END)

    result = await graph.compile().ainvoke({})

    assert sorted(f["summary"] for f in result["findings"]) == ["OOMKilled", "memory 99%"]
```

`tests/spikes/test_task_in_node.py`:

```python
"""Spike: a node re-runs from the top on resume; only @task results are replayed, not redone."""

from __future__ import annotations

from typing import Any, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.func import task
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

EXECUTED_ACTIONS: list[str] = []


@task
def execute_action(action_id: str) -> str:
    EXECUTED_ACTIONS.append(action_id)
    return f"done:{action_id}"


class ExecutionState(TypedDict, total=False):
    results: list[str]
    decision: Any


def _single_node_graph(node: Any) -> Any:
    graph = StateGraph(ExecutionState)
    graph.add_node("execute", node)
    graph.add_edge(START, "execute")
    graph.add_edge("execute", END)
    return graph.compile(checkpointer=InMemorySaver())


def test_task_side_effect_is_not_repeated_when_the_node_resumes() -> None:
    EXECUTED_ACTIONS.clear()

    def execute(state: ExecutionState) -> dict[str, Any]:
        result = execute_action("rollback-1").result()
        decision = interrupt({"confirm_after": result})
        return {"results": [result], "decision": decision}

    app = _single_node_graph(execute)
    config = {"configurable": {"thread_id": "task"}}

    app.invoke({}, config)
    resumed = app.invoke(Command(resume="ok"), config)

    assert EXECUTED_ACTIONS == ["rollback-1"]
    assert resumed["results"] == ["done:rollback-1"]


def test_plain_code_before_interrupt_runs_again_on_resume() -> None:
    calls: list[str] = []

    def execute(state: ExecutionState) -> dict[str, Any]:
        calls.append("side effect")
        return {"decision": interrupt("approve?")}

    app = _single_node_graph(execute)
    config = {"configurable": {"thread_id": "plain"}}

    app.invoke({}, config)
    app.invoke(Command(resume="ok"), config)

    assert calls == ["side effect", "side effect"]
```

`tests/spikes/test_context_on_resume.py`:

```python
"""Spike: runtime context is not checkpointed, so every resume must pass it again."""

from __future__ import annotations

import operator
from dataclasses import dataclass
from typing import Annotated, Any, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langgraph.types import Command, interrupt


@dataclass
class RunContext:
    tenant: str


class ContextState(TypedDict, total=False):
    seen: Annotated[list[str], operator.add]


def _graph() -> Any:
    def node(state: ContextState, runtime: Runtime[RunContext]) -> dict[str, Any]:
        tenant = runtime.context.tenant if runtime.context is not None else "<missing>"
        interrupt("pause")
        return {"seen": [tenant]}

    graph = StateGraph(ContextState, context_schema=RunContext)
    graph.add_node("node", node)
    graph.add_edge(START, "node")
    graph.add_edge("node", END)
    return graph.compile(checkpointer=InMemorySaver())


def test_resume_without_context_runs_with_none() -> None:
    app = _graph()
    config = {"configurable": {"thread_id": "no-context"}}

    app.invoke({}, config, context=RunContext("acme"))
    resumed = app.invoke(Command(resume="go"), config)

    assert resumed["seen"] == ["<missing>"]


def test_resume_with_context_sees_it() -> None:
    app = _graph()
    config = {"configurable": {"thread_id": "with-context"}}

    app.invoke({}, config, context=RunContext("acme"))
    resumed = app.invoke(Command(resume="go"), config, context=RunContext("acme"))

    assert resumed["seen"] == ["acme"]
```

- [ ] **Step 2: Ejecutarlos (deben pasar)**

Run: `uv run pytest tests/spikes -q`
Expected: `5 passed`. Si alguno falla, detener y reportar qué contrato cambió antes de seguir.

- [ ] **Step 3: Documentar el spike**

`docs/spikes/fase-0-mecanismos.md`:

```markdown
# Spike de la Fase 0: mecanismos de LangGraph verificados

Versiones: langgraph 1.2.14, langchain 1.4.3, langchain-core 1.6.7, langgraph-checkpoint 4.2.0,
langgraph-checkpoint-sqlite 3.1.1, langgraph-checkpoint-postgres 3.1.2, langgraph-cli 0.4.33.
Entorno: Python 3.12 sobre Windows 11.

| # | Mecanismo | Resultado | Consecuencia para el diseño | Test |
|---|---|---|---|---|
| 1 | `Send` hacia un nodo que corre un subagente `create_agent` | Cada rama corre su subagente; los hallazgos se fusionan con un reducer `operator.add` | El nodo `specialist` de la Fase 2 sigue este patrón; en tests, una instancia de modelo por rama | `tests/spikes/test_send_fanout.py` |
| 2 | `interrupt()` dentro de una tool de un subagente | Burbujea al supervisor: `ainvoke` devuelve `__interrupt__` y `aget_state().next` no está vacío; `Command(resume=...)` reanuda las dos capas | Las aprobaciones de chat y remediación usan este camino; solo el grafo externo lleva checkpointer | `tests/agents/test_subagents.py` |
| 3 | `@task` dentro de un nodo de `StateGraph` | Al reanudar, el nodo se re-ejecuta desde el inicio, pero el `@task` ya completado se reproduce desde el checkpoint sin repetir el efecto; el código normal antes de `interrupt()` sí se repite | El nodo `execute` de remediación envuelve cada acción en `@task`, y nada con efectos va antes de `interrupt()` | `tests/spikes/test_task_in_node.py` |
| 4 | Runtime context al reanudar | No se persiste: un resume sin `context=` corre con `runtime.context = None` | El RunManager de la Fase 4 reconstruye y pasa el context en cada invoke y en cada resume | `tests/spikes/test_context_on_resume.py` |
| 5 | Middleware propio con `wrap_tool_call` | Funciona; un middleware solo síncrono falla con `NotImplementedError` bajo `ainvoke` | Todo middleware propio implementa `wrap_tool_call` y `awrap_tool_call` | `tests/agents/middleware/test_tool_cache.py` |
| 6 | Salida estructurada con `ToolStrategy` | `structured_response` trae la instancia Pydantic; la tool se llama como la clase | Los especialistas devuelven su hallazgo con `ToolStrategy`; el supervisor lo recibe como JSON | `tests/agents/test_factory.py`, `tests/agents/test_subagents.py` |
| 7 | Context hacia subagentes | `subagent_tool` reenvía `runtime.context` y las tools del subagente lo ven | Clientes y credenciales viajan en el context hasta los especialistas | `tests/agents/test_subagents.py` |

## Hallazgos de entorno

- **psycopg async en Windows** requiere un loop selector. Los tests lo fijan con el hook `pytest_asyncio_loop_factories`; el override de `event_loop_policy` está deprecado en pytest-asyncio 1.4. El servidor en Windows lo necesitará en la Fase 4.
- **`langgraph dev`** corre en Windows sin licencia ni LangSmith (`GET /ok` → `{"ok":true}`). Si se lanza desde un script, al detenerlo hay que matar también el proceso hijo del reloader; en una terminal basta con Ctrl+C.
- **`init_chat_model`** construye el modelo sin API key y falla recién al invocar, así que los grafos de Studio importan sin claves.
- **pydantic-settings no exporta `.env` a `os.environ`.** Las claves de proveedor (`ANTHROPIC_API_KEY`, …) llegan por entorno: `langgraph dev` las carga vía `"env": ".env"`, y el CLI y el servidor llamarán `load_dotenv()` en su raíz de composición (Fase 2).
- **python-dotenv ya ignora el BOM** de un `.env` guardado con el Bloc de notas.
```

- [ ] **Step 4: Gate y commit**

Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run lint-imports`
Expected: todo en verde.

```bash
git add tests/spikes docs/spikes
git commit -m "test(spikes): pin the LangGraph contracts the design relies on" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Grafo demo y `langgraph.json` para Studio

**Files:**
- Create: `src/sre_graph/graphs/demo.py`, `langgraph.json`
- Test: `tests/test_langgraph_json.py`

**Interfaces:**
- Consumes: `Settings` (T1), `ModelRole` y `get_model` (T2), `make_agent` (T5), `SubagentSpec` y `make_supervisor` (T6).
- Produces: `make_demo_graph() -> CompiledStateGraph`, sin checkpointer.
  - Se retira en la Fase 2, cuando `langgraph.json` apunte a los grafos reales.

- [ ] **Step 1: Escribir el test (falla)**

`tests/test_langgraph_json.py`:

```python
from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _graph_targets() -> dict[str, str]:
    config = json.loads((ROOT / "langgraph.json").read_text(encoding="utf-8"))
    graphs = config["graphs"]
    assert isinstance(graphs, dict)
    return graphs


def _module_name(path: str) -> str:
    relative = Path(path).as_posix().removeprefix("./").removeprefix("src/")
    return relative.removesuffix(".py").replace("/", ".")


@pytest.mark.parametrize("graph_id", sorted(_graph_targets()))
def test_studio_graph_compiles_without_checkpointer(
    graph_id: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    path, attribute = _graph_targets()[graph_id].rsplit(":", 1)

    graph = getattr(importlib.import_module(_module_name(path)), attribute)()

    assert graph.checkpointer is None
    assert graph.get_graph().nodes
```

- [ ] **Step 2: Verificar que falla**

Run: `uv run pytest tests/test_langgraph_json.py -q`
Expected: error de colección con `FileNotFoundError` sobre `langgraph.json`.

- [ ] **Step 3: Implementar**

`src/sre_graph/graphs/demo.py`:

```python
"""Demo graph for LangGraph Studio until the investigation graph lands (removed in Fase 2)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from langchain_core.tools import tool
from langgraph.graph.state import CompiledStateGraph

from sre_graph.agents.factory import make_agent
from sre_graph.agents.subagents import SubagentSpec, make_supervisor
from sre_graph.config.settings import Settings
from sre_graph.llm.models import ModelRole, get_model


@tool
def current_utc_time() -> str:
    """Return the current UTC time in ISO 8601."""
    return datetime.now(UTC).isoformat()


def make_demo_graph() -> CompiledStateGraph[Any, Any, Any, Any]:
    """A supervisor with one clock subagent, compiled without a checkpointer for Studio."""
    settings = Settings()
    clock = make_agent(
        name="clock",
        model=get_model(ModelRole.WORKER, settings),
        tools=[current_utc_time],
        system_prompt="You answer questions about the current time using your tool.",
    )
    return make_supervisor(
        subagents=[
            SubagentSpec(name="clock", description="Ask for the current UTC time.", agent=clock)
        ],
        model=get_model(ModelRole.REASONING, settings),
        name="demo_supervisor",
    )
```

`langgraph.json`:

```json
{
  "python_version": "3.12",
  "dependencies": ["."],
  "graphs": {
    "demo_supervisor": "./src/sre_graph/graphs/demo.py:make_demo_graph"
  },
  "env": ".env"
}
```

- [ ] **Step 4: Verificar el test**

Run: `uv run pytest tests/test_langgraph_json.py -q`
Expected: `1 passed`

- [ ] **Step 5: Verificación manual de Studio**

```bash
cp -n .env.example .env
uv run langgraph dev --no-browser
```

En otra terminal: `curl -s http://127.0.0.1:2024/ok` → Expected: `{"ok":true}`.

- Para la prueba conversacional en Studio (opcional), completar `ANTHROPIC_API_KEY` en `.env`.
- Detener con Ctrl+C.
- Si se lanzó en background desde un script, detener también el proceso hijo `python … spawn_main(parent_pid=…)` que queda escuchando en el 2024.

- [ ] **Step 6: Gate y commit**

Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run lint-imports`
Expected: todo en verde.

```bash
git add src/sre_graph/graphs/demo.py langgraph.json tests/test_langgraph_json.py
git commit -m "feat(graphs): demo supervisor graph exposed to LangGraph Studio" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: CI, README y gate final

**Files:**
- Create: `.github/workflows/ci.yml`
- Modify: `README.md` (reemplazo completo)

**Interfaces:**
- Consumes: todo lo anterior.
- Produces: un gate de CI reproducible (`uv sync --locked`) y la documentación de arranque.

- [ ] **Step 1: Workflow de CI**

`.github/workflows/ci.yml`:

```yaml
name: ci

on:
  push:
  pull_request:

jobs:
  quality:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: pgvector/pgvector:pg17
        env:
          POSTGRES_USER: sre_graph
          POSTGRES_PASSWORD: sre_graph
          POSTGRES_DB: sre_graph
        ports:
          - 5432:5432
        options: >-
          --health-cmd "pg_isready -U sre_graph -d sre_graph"
          --health-interval 5s
          --health-timeout 3s
          --health-retries 20
    env:
      SRE_GRAPH_TEST_DATABASE_URL: postgresql://sre_graph:sre_graph@localhost:5432/sre_graph
      LANGSMITH_TRACING: "false"
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v6
        with:
          python-version: "3.12"
      - run: uv sync --locked
      - run: uv run ruff check .
      - run: uv run ruff format --check .
      - run: uv run mypy
      - run: uv run lint-imports
      - run: uv run pytest
```

- [ ] **Step 2: README completo**

`README.md`:

````markdown
# sre-graph

Investigación multiagente de incidencias (RCA) construida de forma nativa sobre LangGraph.
Diseño completo: [docs/specs/2026-10-07-sre-graph-design.md](docs/specs/2026-10-07-sre-graph-design.md).

> Estado: Fase 0 (fundaciones). El grafo de investigación llega en la Fase 2.

## Requisitos

- Python 3.12 y [uv](https://docs.astral.sh/uv/)
- Docker Desktop (solo para Postgres)

## Inicio rápido

```bash
uv sync
cp .env.example .env    # completa ANTHROPIC_API_KEY para usar modelos reales
uv run pytest
```

## Postgres local

```bash
docker compose up -d --wait
SRE_GRAPH_TEST_DATABASE_URL="postgresql://sre_graph:sre_graph@localhost:5433/sre_graph?connect_timeout=5" uv run pytest -m postgres
```

Para usarlo desde la app, pon `PERSISTENCE_BACKEND=postgres` en `.env`.

## LangGraph Studio (desarrollo)

```bash
uv run langgraph dev --no-browser
```

`http://127.0.0.1:2024/ok` responde `{"ok":true}` cuando el servidor está listo. Detén con Ctrl+C.

## Calidad

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run lint-imports && uv run pytest
```

## Licencia

Apache-2.0. `NOTICE` lista el material adaptado de OpenSRE y de LangGraph Starter Kit.
````

- [ ] **Step 3: Gate final completo**

Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run lint-imports && uv run pytest -q`
Expected:
- ruff y mypy en verde;
- `Contracts: 1 kept, 0 broken.`;
- `41 passed, 2 skipped`.

```bash
docker compose up -d --wait
SRE_GRAPH_TEST_DATABASE_URL="postgresql://sre_graph:sre_graph@localhost:5433/sre_graph?connect_timeout=5" uv run pytest -q
docker compose down
```

Expected: `43 passed`.

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/ci.yml README.md
git commit -m "ci: quality gate with Postgres service; document local setup" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

- [ ] **Step 5: Cierre de la rama**

Usar la skill `superpowers:finishing-a-development-branch` para decidir cómo integrar `fase-0` (merge a `main`, PR o dejarla abierta). Después, escribir el plan de la Fase 1 con `superpowers:writing-plans`.
