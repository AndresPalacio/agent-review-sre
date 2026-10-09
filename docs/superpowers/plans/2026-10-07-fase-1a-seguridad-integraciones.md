# Fase 1a — Seguridad, marco de integraciones y Kubernetes: plan de implementación

> **Para agentes:** SUB-SKILL REQUERIDA: usa superpowers:subagent-driven-development (recomendado) o superpowers:executing-plans para implementar este plan tarea por tarea. Los pasos usan checkboxes (`- [ ]`).

**Goal:** dejar listas las piezas que toda integración necesita y una integración de referencia completa:
- pagar las deudas menores que dejó la revisión de la Fase 0;
- metadatos de tools en el dominio (`ToolMeta`), redacción de secretos, masking reversible y política de aprobación HITL, cada una con su middleware;
- el marco de integraciones: contexto por corrida, registro explícito, payload compacto para el modelo y verificación de conectividad;
- Kubernetes de solo lectura (9 tools) como integración de referencia para los planes 1b–1d.

**Architecture:**
- Tres capas nuevas, con dependencias solo hacia abajo y contrato de import-linter: `graphs → agents → integrations → safety → domain → llm | persistence → config`.
- `domain` y `safety` son Python puro. `safety` llega a los agentes como middleware:
  - `GuardrailsMiddleware` redacta secretos en cada resultado de tool (`wrap_tool_call`).
  - `MaskingMiddleware` enmascara identificadores antes del modelo y los restaura en la respuesta (`wrap_model_call`).
  - `approval_middleware` configura el `HumanInTheLoopMiddleware` de LangChain según el nivel de efecto de cada tool.
- Cada vendor es un `Integration[SettingsT, ClientT]`:
  - `IntegrationRegistry` arma un `RunContext` por corrida con los clientes de las integraciones configuradas, más el motivo de las que fallaron.
  - Las tools leen su cliente del runtime context y devuelven `(content, artifact)`: JSON compacto para el modelo y los datos completos para la evidencia.
- Kubernetes usa el SDK oficial con una `Configuration` privada y llamadas async vía `asyncio.to_thread`. Los tests sirven respuestas grabadas a través de la capa HTTP real del SDK.

**Tech Stack:** lo de la Fase 0 (Python 3.12, uv, LangGraph 1.2.14, LangChain 1.4.3 / langchain-core 1.6.7, pydantic-settings 2.15, pytest 9 + pytest-asyncio 1.4, ruff 0.16, mypy 2.4, import-linter 2.15), más `kubernetes` 37.0.0 (modelos Pydantic) y `urllib3` 2.8.

**Spec:** `docs/specs/2026-10-07-sre-graph-design.md`

> **Alcance:** la Fase 1 se divide en cuatro planes, cada uno con software que funciona y se prueba por sí solo:
> - **1a (este):** deudas de la Fase 0, seguridad, marco de integraciones y Kubernetes.
> - **1b:** dominio de incidentes (alertas normalizadas, ventana, taxonomía, evidencia, diagnóstico, reporte).
> - **1c:** observabilidad (Prometheus/Alertmanager, Grafana Loki/Tempo/Mimir, Datadog, New Relic).
> - **1d:** nube, errores y cambios (AWS, Sentry, GitHub), entrega (Slack, Telegram, PagerDuty), loader MCP y `verify` contra endpoints reales.
>
> Todo el código de este plan ya corrió en un borrador sobre Windows 11 con 193 tests en verde (2 de ellos contra Postgres en Docker), además de ruff, mypy `strict` e import-linter. Las 11 pruebas nuevas sobre módulos existentes fallan contra `main` antes del cambio.

## Global Constraints

- **Lenguaje y versiones:**
  - Python 3.12 (`requires-python = ">=3.12,<3.13"`) y versiones fijadas en `uv.lock`.
  - Se mantienen los pins de la Fase 0: `langgraph>=1.2.14,<1.3`, `langchain>=1.4.3,<1.5` y `langchain-core>=1.6.7,<1.7`.
- **Sin opensre:** cero dependencia (ni import ni paquete); su lógica se reescribe y se atribuye en `NOTICE`.
- **Logs y trazas:** nada persistido; sin LangSmith ni OTel (`LANGSMITH_TRACING=false`, `LANGSMITH_TRACING_V2=false` y `LANGCHAIN_TRACING_V2=false` en `.env.example`).
- **Secretos:**
  - Nunca en el estado ni en los args del modelo.
  - Los clientes con credenciales viajan en el runtime context (`RunContext`), que LangGraph no persiste y se reconstruye en cada invoke y en cada resume.
- **Estado serializable:** los resúmenes y artifacts de las tools son `dict`/`list` con valores JSON-friendly (los timestamps van como texto ISO-8601).
- **Aprobación:** `read_only` nunca interrumpe; `mutating`/`external` interrumpe salvo allowlist por entorno; `prod` fuerza la aprobación.
- **Masking:** apagado por defecto (`MASK_ENABLED=false`).
- **Capas:** `graphs → agents → integrations → safety → domain → llm | persistence → config`, con contrato de import-linter.
- **Variables de entorno:** sin prefijo global (decisión de la Fase 0); cada integración usa su propio prefijo (`K8S_`).
- **Async:**
  - Todo middleware propio implementa el hook síncrono y el async.
  - Las tools de integración son async; los agentes que las usan corren con `ainvoke`/`astream`.
- **Gate de calidad** al cerrar cada tarea: `uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run lint-imports && uv run pytest`.
- **Idioma:** prosa de documentos en español; código, identificadores y docstrings en inglés.
- **Repo:**
  - `C:\Users\USUARIO\Desktop\devops-telemetri\sre-graph`, rama de trabajo `fase-1a` creada desde `main`.
  - Todo local: sin remoto y sin push.
- **Commits:** cada commit termina con el trailer `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **Un secreto en la salida de una tool** (un token en los logs de un pod, una contraseña en una URL):
   - Esperado: se redacta antes de llegar al modelo y al checkpoint, tanto en `content` como en `artifact`.
   - Tests: Tarea 4, `test_the_checkpoint_holds_the_redacted_content_and_artifact`; Tarea 9, `test_secrets_in_container_logs_are_redacted_by_the_guardrails`.
2. **Masking activo y el modelo pide una tool con un identificador enmascarado:**
   - Esperado: la tool recibe el valor real y el estado guarda valores reales.
   - Test: Tarea 5, `test_the_model_sees_tokens_and_the_tool_receives_the_real_pod`.
3. **Kubeconfig inexistente o cluster inalcanzable:**
   - Esperado: la integración queda "no disponible" con un motivo claro, `verify` lo reporta, las tools devuelven un mensaje de error al modelo y la corrida sigue.
   - Tests: Tarea 9, `test_a_missing_kubeconfig_makes_kubernetes_unavailable_not_the_run` y `test_an_unavailable_cluster_is_explained_to_the_model`; Tarea 8, `test_an_unreachable_cluster_is_an_integration_error`.
4. **Una salida enorme** (cientos de pods o eventos):
   - Esperado: el modelo recibe JSON válido con las entradas más relevantes primero y un marcador de lo omitido; el artifact conserva los datos completos (con tope).
   - Tests: Tarea 7, `test_a_list_too_long_for_the_limit_keeps_whole_entries_and_valid_json`; Tarea 9, `test_a_huge_listing_is_cut_for_the_model_but_kept_whole_in_the_artifact`.
5. **Variables de entorno con valores literales en el pod** (`DB_PASSWORD=...`, también dentro de la anotación `last-applied-configuration`):
   - Esperado: al modelo solo llegan los nombres.
   - Test: Tarea 8, `test_pod_detail_names_env_vars_but_never_their_values`.

## Decisiones tomadas al validar

Estas decisiones ajustan la spec o difieren partes de ella. Cada una dice qué cuesta si resulta equivocada.

- **Masking sin mapa en el estado.**
  - La spec pedía un mapa por hilo en el estado. El token se deriva del valor (`<POD_` + 10 hex de SHA-256), así que el mismo valor da el mismo token en cada llamada sin guardar nada. Las respuestas se desenmascaran antes de guardarse, por lo que el estado solo tiene valores reales.
  - Coste si falla: si el modelo cita un token que no viene en la petición actual (por ejemplo, tras resumir el historial), ese token no se restaura.
- **Guardrails en 1a = resultados de tools** (`content` y `artifact`, incluidos los errores). La redacción de mensajes salientes a canales llega con la entrega (1d) y usa el mismo `redact_secrets`.
- **Aprobación en 1a = política por nivel de efecto, entorno y allowlist de tools.** Las allowlists de aprobadores por superficie llegan con las superficies (Fases 5 y 6).
- **`RunContext` vive en `integrations/context.py`.** Los grafos de la Fase 2 lo usan como `context_schema` (pueden extenderlo por herencia; `require_client` acepta subclases).
- **Integraciones activadas de forma explícita.** Cada integración se activa con `<PREFIJO>_ENABLED=true` (aquí `K8S_ENABLED`). Si no está configurada, no aporta tools. Si está configurada pero rota (kubeconfig inexistente, contexto desconocido), queda en `RunContext.unavailable` con el motivo redactado.
- **Kubernetes:**
  - Los listados se cortan en `K8S_MAX_ITEMS` (200) sin paginar. El orden "lo problemático primero" se aplica a la página recibida.
  - El detalle de pod omite `command`/`args`, que pueden llevar credenciales, y la anotación `last-applied-configuration`.
  - Los logs se decodifican desde los bytes crudos (`_preload_content=False`), para que un log de líneas JSON nunca se interprete como JSON.
  - `verify` no se fía de `/version`, que muchos clusters sirven a anónimos: además lista pods (`limit=1`) en `K8S_VERIFY_NAMESPACE`.
- **Clientes por corrida sin cierre explícito.** El pool HTTP se libera con el recolector de basura; el cierre ordenado llega con el `RunManager` (Fase 4).
- **`verify` es un método de cada `Integration`** (en `integration.py`), no un `verify.py` aparte como sugería la spec: comparte el cliente ya construido y el registro lo invoca con timeout.
- **Sin `evidence_id` todavía.** La spec pide JSON compacto más un `evidence_id`, con el payload completo en el colector de evidencia. En 1a el payload completo (con tope) viaja en el `artifact` del `ToolMessage`. El `evidence_id` y el colector llegan con `EvidenceCaptureMiddleware` (Fase 2).
- **Inyección de prompts, en la Fase 2.** Las reglas anti-inyección y la delimitación de salidas de tools como datos viven en los prompts de especialistas y del chat. En 1a las tools ya entregan JSON o texto acotado, sin instrucciones propias.
- **Diferido de la revisión de la Fase 0:**
  - Deduplicar llamadas concurrentes idénticas en la caché va a la Fase 2.
  - El comando `sre-graph integrations list|verify` va a la Fase 2 (CLI); aquí queda `IntegrationRegistry.verify_all`.
- **`parse_docstring=True` en las tools de integración.** La primera línea del docstring y la sección `Args:` son lo que el modelo lee: se redactan para el modelo.

## Mapa de archivos

| Archivo | Responsabilidad |
|---|---|
| `src/sre_graph/config/settings.py` | `Settings` con vacíos = default, nombres normalizados, `DATABASE_URL` como `SecretStr` y los campos de seguridad |
| `src/sre_graph/persistence/paths.py` | `prepare_sqlite_path` con `~` y ruta vacía rechazada |
| `src/sre_graph/agents/testing/scripted_model.py`, `src/sre_graph/agents/subagents.py` | Arreglos menores de la Fase 0 |
| `src/sre_graph/domain/tools.py` | `Domain`, `SideEffectLevel`, `EvidenceType`, `ToolMeta`, `attach_tool_meta`, `read_tool_meta` |
| `src/sre_graph/agents/middleware/tool_cache.py` | Caché basada en `ToolMeta` |
| `src/sre_graph/safety/secrets.py` | `redact_secrets`, `redact_value`, `SECRET_PATTERNS` |
| `src/sre_graph/safety/masking.py` | `IdentifierKind`, `MaskingPolicy`, `Masker` |
| `src/sre_graph/safety/approval.py` | `ApprovalPolicy` |
| `src/sre_graph/agents/middleware/guardrails.py` | `GuardrailsMiddleware` |
| `src/sre_graph/agents/middleware/masking.py` | `MaskingMiddleware` |
| `src/sre_graph/agents/middleware/approval.py` | `approval_middleware` |
| `src/sre_graph/integrations/base.py` | `Integration`, `IntegrationError`, `IntegrationNotConfiguredError`, `VerifyStatus`, `VerifyResult` |
| `src/sre_graph/integrations/context.py` | `RunContext`, `require_client` |
| `src/sre_graph/integrations/payload.py` | `tool_payload`, `text_payload` |
| `src/sre_graph/integrations/tooling.py` | `integration_tool` |
| `src/sre_graph/integrations/registry.py` | `IntegrationRegistry` |
| `src/sre_graph/integrations/catalog.py` | `default_registry` |
| `src/sre_graph/integrations/kubernetes/settings.py` | `KubernetesSettings`, `INTEGRATION_ID` |
| `src/sre_graph/integrations/kubernetes/summaries.py` | Objetos de la API → dicts compactos y sin secretos |
| `src/sre_graph/integrations/kubernetes/client.py` | `KubernetesClient` (async, solo lectura) |
| `src/sre_graph/integrations/kubernetes/tools.py` | Las 9 tools `k8s_*` y `TOOLS` |
| `src/sre_graph/integrations/kubernetes/integration.py` | `KubernetesIntegration` |
| `tests/**` | Un archivo de test por módulo; `tests/integrations/kubernetes/conftest.py` sirve el namespace grabado |
| `.env.example`, `README.md`, `.github/workflows/ci.yml`, `docs/spikes/fase-0-mecanismos.md` | Configuración documentada, guía y CI |

---

## Antes de empezar

Todas las tareas cierran con el mismo gate. Las pruebas de Postgres corren contra el contenedor de `docker compose`; `SRE_GRAPH_REQUIRE_POSTGRES=1` hace que fallen, en vez de omitirse, si falta la URL. En Git Bash cada comando del gate se escribe completo, porque las variables exportadas no sobreviven entre llamadas del agente:

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run lint-imports && SRE_GRAPH_TEST_DATABASE_URL="postgresql://sre_graph:sre_graph@localhost:5433/sre_graph?connect_timeout=5" SRE_GRAPH_REQUIRE_POSTGRES=1 uv run pytest
```

---

### Task 1: Configuración y persistencia: deudas de la Fase 0

Esta tarea paga deudas menores de la revisión de la Fase 0:
- Una variable vacía ahora toma el default.
- Los nombres de proveedor y de entorno se normalizan.
- `DATABASE_URL` pasa a `SecretStr`.
- `SQLITE_PATH` expande `~` y rechaza el valor vacío.
- El test del BOM escribe bytes reales.
- El aislamiento de entorno de los tests sale de los campos de `Settings`.
- CI exige Postgres.
- El README se corrige.

También agrega los campos de seguridad que consumen las Tareas 5 y 6.

**Files:**
- Modify: `src/sre_graph/config/settings.py`, `src/sre_graph/persistence/paths.py`
- Modify: `tests/conftest.py`, `tests/config/test_settings.py`, `tests/persistence/test_store.py`, `tests/test_env_example.py`
- Create: `tests/persistence/test_paths.py`
- Modify: `.env.example`, `.gitignore`, `.github/workflows/ci.yml`, `README.md`

**Interfaces:**
- Consumes: `Settings`, `PersistenceBackend` y `prepare_sqlite_path` de la Fase 0.
- Produces:
  - Campos nuevos de `Settings`:
    - `environment: str` (default `"dev"`, normalizado a minúsculas);
    - `auto_approve_tools: tuple[str, ...]` (lista separada por comas);
    - `mask_enabled: bool`;
    - `mask_kinds: tuple[str, ...]` (lista separada por comas);
    - `mask_extra_regex: dict[str, str]` (JSON).
  - `database_url: SecretStr | None`. `require_database_url() -> str` sigue devolviendo el texto plano.
  - `prepare_sqlite_path(path: str) -> str` expande `~` y lanza `ValueError("SQLITE_PATH must not be empty")` ante un valor vacío.

- [ ] **Step 1: Rama, Postgres y línea base**

```bash
cd /c/Users/USUARIO/Desktop/devops-telemetri/sre-graph
git status --short
git checkout -b fase-1a
git add docs/superpowers/plans/2026-10-07-fase-1a-seguridad-integraciones.md
git commit -m "docs: add phase 1a implementation plan" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
docker compose up -d --wait
uv sync --locked
SRE_GRAPH_TEST_DATABASE_URL="postgresql://sre_graph:sre_graph@localhost:5433/sre_graph?connect_timeout=5" uv run pytest
```

Expected:
- `git status --short` muestra solo `?? docs/superpowers/plans/2026-10-07-fase-1a-seguridad-integraciones.md`. Este plan entra como primer commit de la rama, igual que en la Fase 0.
- Aparece `Switched to a new branch 'fase-1a'` y el commit del plan.
- El contenedor `sre-graph-postgres-1` queda `Healthy`.
- pytest termina con `54 passed`.

- [ ] **Step 2: Tests que fallan**

Reemplaza el contenido de `tests/config/test_settings.py` por:

```python
from __future__ import annotations

import codecs
from pathlib import Path

import pytest
from pydantic import ValidationError

from sre_graph.config.settings import PersistenceBackend, Settings


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
    assert settings.sqlite_path == "sre-graph.sqlite"


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
```

Crea `tests/persistence/test_paths.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest

from sre_graph.persistence.paths import prepare_sqlite_path


def test_home_shortcut_is_expanded_and_its_folder_created(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    # Left unexpanded, "~" would become a folder in the working directory: keep it in tmp_path.
    monkeypatch.chdir(tmp_path)

    resolved = prepare_sqlite_path("~/sre-data/state.sqlite")

    assert Path(resolved) == tmp_path / "sre-data" / "state.sqlite"
    assert (tmp_path / "sre-data").is_dir()


def test_empty_path_is_rejected() -> None:
    with pytest.raises(ValueError, match="SQLITE_PATH"):
        prepare_sqlite_path("  ")


def test_in_memory_path_is_left_alone() -> None:
    assert prepare_sqlite_path(":memory:") == ":memory:"
```

En `tests/persistence/test_store.py`, agrega `import sqlite3` debajo de `from __future__ import annotations` y reemplaza `test_store_and_checkpointer_share_one_sqlite_file` por esta versión, que además comprueba que las tablas del checkpointer y del store viven en el mismo archivo:

```python
async def test_store_and_checkpointer_share_one_sqlite_file(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        persistence_backend=PersistenceBackend.SQLITE,
        sqlite_path=str(tmp_path / "state.sqlite"),
    )

    async with open_checkpointer(settings), open_store(settings) as store:
        await store.aput(("org", "notes"), "n1", {"text": "shared file"})
        item = await store.aget(("org", "notes"), "n1")
    with sqlite3.connect(tmp_path / "state.sqlite") as database:
        tables = {row[0] for row in database.execute("select name from sqlite_master")}

    assert item is not None
    assert item.value == {"text": "shared file"}
    assert {"checkpoints", "writes", "store"} <= tables
```

Reemplaza el contenido de `tests/test_env_example.py` por:

```python
from __future__ import annotations

from pathlib import Path

import pytest
from dotenv import dotenv_values

from sre_graph.config.settings import Settings

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
```

- [ ] **Step 3: Verificar que fallan**

Run: `uv run pytest tests/config/test_settings.py tests/persistence/test_paths.py tests/persistence/test_store.py tests/test_env_example.py -v`

Expected: `10 failed`.
- En `test_settings.py` fallan 6:
  - `test_defaults_without_environment` (`AttributeError: 'Settings' object has no attribute 'environment'`);
  - `test_blank_variables_fall_back_to_defaults` (`assert '' == 'anthropic'`);
  - `test_provider_and_environment_names_are_normalized`;
  - `test_database_password_never_appears_in_repr`;
  - `test_comma_separated_lists_are_split`;
  - `test_extra_mask_patterns_are_read_as_json`.
- En `test_paths.py` fallan 2: `test_home_shortcut_is_expanded_and_its_folder_created` y `test_empty_path_is_rejected` (`DID NOT RAISE`).
- En `test_env_example.py` fallan 2:
  - `test_env_example_documents_every_setting` (faltan `LLM_TIMEOUT_SECONDS` y `LLM_MAX_RETRIES`);
  - `test_env_example_loads_as_is` (`AttributeError ... 'environment'`).
- `test_store_and_checkpointer_share_one_sqlite_file` pasa: fija un comportamiento que ya existía.

- [ ] **Step 4: Implementación**

Reemplaza el contenido de `src/sre_graph/config/settings.py` por:

```python
"""Runtime settings read from environment variables and an optional ``.env`` file."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class PersistenceBackend(StrEnum):
    """Where graph checkpoints and the long-term store live."""

    MEMORY = "memory"
    SQLITE = "sqlite"
    POSTGRES = "postgres"


class Settings(BaseSettings):
    """Runtime configuration; secrets are ``SecretStr`` and provider keys stay in their own vars.

    A variable set to an empty string counts as unset, so blank lines copied
    from ``.env.example`` fall back to the defaults below.
    """

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", env_ignore_empty=True
    )

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
    database_url: SecretStr | None = None
    environment: str = "dev"
    auto_approve_tools: Annotated[tuple[str, ...], NoDecode] = ()
    mask_enabled: bool = False
    mask_kinds: Annotated[tuple[str, ...], NoDecode] = ()
    mask_extra_regex: dict[str, str] = Field(default_factory=dict)

    @field_validator("llm_provider", "environment")
    @classmethod
    def _normalize_name(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not normalized:
            raise ValueError("must not be blank")
        return normalized

    @field_validator("auto_approve_tools", "mask_kinds", mode="before")
    @classmethod
    def _split_comma_separated(cls, value: object) -> object:
        if isinstance(value, str):
            return tuple(part.strip() for part in value.split(",") if part.strip())
        return value

    def require_database_url(self) -> str:
        """Return ``DATABASE_URL`` or fail naming the variable the operator must set."""
        if self.database_url is None or not self.database_url.get_secret_value():
            raise ValueError("DATABASE_URL must be set when PERSISTENCE_BACKEND=postgres")
        return self.database_url.get_secret_value()
```

Reemplaza el contenido de `src/sre_graph/persistence/paths.py` por:

```python
"""Filesystem preparation shared by the SQLite-backed persistence factories."""

from __future__ import annotations

from pathlib import Path

SQLITE_IN_MEMORY = ":memory:"


def prepare_sqlite_path(path: str) -> str:
    """Expand ``~``, create the file's parent folder, and return a path SQLite can open.

    An empty path is rejected: SQLite would silently open a private temporary
    database and nothing would persist.
    """
    if not path.strip():
        raise ValueError("SQLITE_PATH must not be empty")
    if path == SQLITE_IN_MEMORY:
        return path
    resolved = Path(path).expanduser()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    return str(resolved)
```

Reemplaza el contenido de `.env.example` por:

```bash
# Proveedor LLM y modelos por rol. Deja vacío un LLM_MODEL_* para usar el default del proveedor.
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=
LLM_MODEL_REASONING=
LLM_MODEL_WORKER=
LLM_MODEL_FAST=
LLM_MODEL_JUDGE=
LLM_TEMPERATURE=0
LLM_TIMEOUT_SECONDS=120
LLM_MAX_RETRIES=2

# Persistencia: memory | sqlite | postgres
PERSISTENCE_BACKEND=memory
SQLITE_PATH=data/sre-graph.sqlite
# Postgres de docker compose; connect_timeout evita esperas largas si no responde.
DATABASE_URL=postgresql://sre_graph:sre_graph@localhost:5433/sre_graph?connect_timeout=5

# Entorno: en prod toda tool que cambia estado pide aprobación humana.
ENVIRONMENT=dev
# Tools que cambian estado y corren sin aprobación fuera de prod, separadas por comas.
AUTO_APPROVE_TOOLS=
# Enmascarar identificadores (pods, namespaces, IPs, emails...) antes de enviarlos al modelo.
MASK_ENABLED=false
# Tipos a enmascarar; vacío = todos: pod,namespace,cluster,hostname,account_id,ip_address,email,service_name
MASK_KINDS=
# Patrones extra en JSON, por ejemplo {"ticket": "TCK-[0-9]+"}
MASK_EXTRA_REGEX=

# Sin trazas a terceros: apaga los tres interruptores (LANGCHAIN_TRACING_V2 tiene prioridad).
LANGSMITH_TRACING=false
LANGSMITH_TRACING_V2=false
LANGCHAIN_TRACING_V2=false
```

Reemplaza el contenido de `tests/conftest.py` por esta versión. El aislamiento de entorno sale de los campos de `Settings`, así que un campo nuevo queda aislado sin tocar la lista. Además, `postgres_url` falla en vez de omitir cuando CI exige Postgres:

```python
"""Shared test configuration: event loop choice, environment isolation, no external tracing."""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import Callable, Iterator, Mapping

import langsmith
import pytest
from langsmith import utils as langsmith_utils

from sre_graph.config.settings import Settings
from sre_graph.config.tracing import disable_external_tracing

_PROVIDER_AND_TRACING_ENV = (
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "LANGCHAIN_TRACING_V2",
    "LANGSMITH_TRACING_V2",
    "LANGCHAIN_TRACING",
    "LANGSMITH_API_KEY",
    "LANGCHAIN_API_KEY",
    "LANGSMITH_OTEL_ENABLED",
)


def pytest_asyncio_loop_factories(
    config: pytest.Config, item: pytest.Item
) -> Mapping[str, Callable[[], asyncio.AbstractEventLoop]]:
    """psycopg's async driver needs a selector loop; Windows defaults to Proactor."""
    if sys.platform == "win32":
        return {"selector": asyncio.SelectorEventLoop}
    return {"default": asyncio.new_event_loop}


@pytest.fixture(autouse=True, scope="session")
def _no_external_tracing() -> None:
    """Nothing a test runs may reach LangSmith, whatever the developer's shell exports."""
    disable_external_tracing()


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep a developer's real settings and provider keys out of every test."""
    for name in (*(field.upper() for field in Settings.model_fields), *_PROVIDER_AND_TRACING_ENV):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LANGSMITH_TRACING", "false")


@pytest.fixture
def tracing_enabled_by_environment(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """LangSmith tracing switched on through the environment, as a developer shell might.

    langsmith caches environment lookups (``get_env_var`` is an ``lru_cache``), so
    the cache is cleared on the way in and out. The process override is reset on
    entry, the precondition is asserted, and tracing is disabled again on exit.
    """
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")
    monkeypatch.setenv("LANGSMITH_API_KEY", "lsv2-test-key")
    langsmith_utils.get_env_var.cache_clear()
    langsmith.configure(enabled=None)
    assert langsmith_utils.tracing_is_enabled(), "precondition: the environment enables tracing"
    yield
    disable_external_tracing()
    langsmith_utils.get_env_var.cache_clear()


@pytest.fixture
def postgres_url() -> str:
    """Connection string for ``postgres``-marked tests; CI sets ``SRE_GRAPH_REQUIRE_POSTGRES=1``.

    With that flag a missing URL fails the test instead of skipping it, so a
    misnamed variable in CI cannot silently drop the Postgres coverage.
    """
    url = os.environ.get("SRE_GRAPH_TEST_DATABASE_URL")
    if url:
        return url
    if os.environ.get("SRE_GRAPH_REQUIRE_POSTGRES") == "1":
        pytest.fail("SRE_GRAPH_REQUIRE_POSTGRES=1 but SRE_GRAPH_TEST_DATABASE_URL is not set")
    pytest.skip("SRE_GRAPH_TEST_DATABASE_URL is not set")
```

En `.gitignore`, cambia la línea `data/` por `/data/`, para que solo se ignore la carpeta de datos de la raíz (la de `SQLITE_PATH=data/...`) y no cualquier `data/` futura con fixtures.

En `.github/workflows/ci.yml`, agrega `SRE_GRAPH_REQUIRE_POSTGRES: "1"` al bloque `env` del job:

```yaml
    env:
      SRE_GRAPH_TEST_DATABASE_URL: postgresql://sre_graph:sre_graph@localhost:5432/sre_graph
      SRE_GRAPH_REQUIRE_POSTGRES: "1"
      LANGSMITH_TRACING: "false"
```

Reemplaza el contenido de `README.md` por esta versión:
- documenta la sintaxis de PowerShell y el `.env` en UTF-8;
- desactiva las estadísticas de la CLI de LangGraph;
- quita la frase prematura sobre usar Postgres "desde la app".

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

`.env` debe estar en UTF-8. En PowerShell copia con `Copy-Item .env.example .env`; si lo
reescribes, usa `Set-Content -Encoding utf8`.

## Postgres local

```bash
docker compose up -d --wait
SRE_GRAPH_TEST_DATABASE_URL="postgresql://sre_graph:sre_graph@localhost:5433/sre_graph?connect_timeout=5" uv run pytest -m postgres
```

En PowerShell:

```powershell
$env:SRE_GRAPH_TEST_DATABASE_URL = "postgresql://sre_graph:sre_graph@localhost:5433/sre_graph?connect_timeout=5"
uv run pytest -m postgres
```

## LangGraph Studio (desarrollo)

```bash
LANGGRAPH_CLI_NO_ANALYTICS=1 uv run langgraph dev --no-browser
```

`LANGGRAPH_CLI_NO_ANALYTICS=1` evita que la CLI envíe estadísticas de uso (en PowerShell:
`$env:LANGGRAPH_CLI_NO_ANALYTICS = "1"` antes del comando).

`http://127.0.0.1:2024/ok` responde `{"ok":true}` cuando el servidor está listo. Detén con Ctrl+C.

## Calidad

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run lint-imports && uv run pytest
```

## Licencia

Apache-2.0. `NOTICE` lista el material adaptado de OpenSRE y de LangGraph Starter Kit.
````

- [ ] **Step 5: Verificar que pasan**

Run: `uv run pytest tests/config/test_settings.py tests/persistence/test_paths.py tests/persistence/test_store.py tests/test_env_example.py -v`

Expected: `21 passed, 1 skipped` (el skip es la prueba de Postgres de `test_store.py`, porque este comando no lleva la URL).

- [ ] **Step 6: Verificar la exigencia de Postgres**

Run: `SRE_GRAPH_REQUIRE_POSTGRES=1 uv run pytest -m postgres`

Expected: `2 errors`, con `SRE_GRAPH_REQUIRE_POSTGRES=1 but SRE_GRAPH_TEST_DATABASE_URL is not set`.

Run: `uv run pytest -m postgres`

Expected: `2 skipped`.

- [ ] **Step 7: Gate**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run lint-imports && SRE_GRAPH_TEST_DATABASE_URL="postgresql://sre_graph:sre_graph@localhost:5433/sre_graph?connect_timeout=5" SRE_GRAPH_REQUIRE_POSTGRES=1 uv run pytest
```

Expected: `All checks passed!`, archivos ya formateados, `Success: no issues found`, `Contracts: 1 kept, 0 broken.` y `64 passed`.

- [ ] **Step 8: Commit**

```bash
git add src/sre_graph/config/settings.py src/sre_graph/persistence/paths.py tests/conftest.py tests/config/test_settings.py tests/persistence/test_paths.py tests/persistence/test_store.py tests/test_env_example.py .env.example .gitignore .github/workflows/ci.yml README.md
git commit -m "fix(config): blank vars use defaults, secret DATABASE_URL, safety settings and stricter test isolation" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Kit de agentes: deudas de la Fase 0 y dos spikes nuevos

Arreglos menores de la revisión de la Fase 0:
- El modelo guionado devuelve copias, para que reusar un mismo `AIMessage` en el guion no colapse dos respuestas en una por ID repetido.
- `subagent_result_text` pasa al supervisor solo el texto de la respuesta (sin bloques de razonamiento) y serializa los dataclasses como JSON.
- Se refuerzan dos tests.

Además, dos spikes fijan contratos que la Fase 2 y la Fase 5 dan por hechos.

**Files:**
- Modify: `src/sre_graph/agents/testing/scripted_model.py`, `src/sre_graph/agents/subagents.py`
- Modify: `tests/agents/test_scripted_model.py`, `tests/agents/test_subagents.py`, `tests/agents/test_factory.py`
- Create: `tests/spikes/test_middleware_async_contract.py`, `tests/spikes/test_subgraph_node_interrupt.py`
- Modify: `docs/spikes/fase-0-mecanismos.md`

**Interfaces:**
- Consumes: `ScriptedChatModel`, `tool_call_message`, `make_agent`, `make_supervisor`, `SubagentSpec` y `subagent_result_text` de la Fase 0.
- Produces:
  - `ScriptedChatModel._generate` devuelve `message.model_copy(deep=True)`.
  - `subagent_result_text(result: Mapping[str, Any]) -> str` evalúa en este orden:
    1. un `BaseModel` → `model_dump_json()`;
    2. un dataclass → `json.dumps(asdict(...))`;
    3. otro valor estructurado → `json.dumps`;
    4. si no hay valor estructurado → `str(messages[-1].text)`, o `"(no response)"` cuando no hay mensajes.

- [ ] **Step 1: Tests que fallan**

En `tests/agents/test_scripted_model.py`, cambia los imports por:

```python
import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver

from sre_graph.agents.factory import make_agent
from sre_graph.agents.testing.scripted_model import (
    ScriptedChatModel,
    ScriptedModelExhaustedError,
    tool_call_message,
)
```

y agrega al final:

```python
def test_reusing_one_scripted_message_keeps_both_replies_in_the_thread() -> None:
    reply = AIMessage("ok")
    model = ScriptedChatModel(responses=[reply, reply])
    agent = make_agent(name="echo", model=model, checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "reuse"}}

    agent.invoke({"messages": [HumanMessage("one")]}, config)
    second = agent.invoke({"messages": [HumanMessage("two")]}, config)

    assert len(second["messages"]) == 4
```

En `tests/agents/test_subagents.py`, cambia el import de `sre_graph.agents.subagents` por:

```python
from sre_graph.agents.subagents import SubagentSpec, make_supervisor, subagent_result_text
```

y agrega al final:

```python
def test_synchronous_invoke_forwards_runtime_context_too() -> None:
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

    app.invoke({"messages": [HumanMessage("tenant?")]}, context=TenantContext("acme"))

    assert seen == ["acme"]


def test_only_the_text_of_a_multi_block_reply_reaches_the_supervisor() -> None:
    reply = AIMessage(
        content=[
            {"type": "thinking", "thinking": "internal notes", "signature": "sig-123"},
            {"type": "text", "text": "Logs show OOMKilled."},
        ]
    )

    assert subagent_result_text({"messages": [reply]}) == "Logs show OOMKilled."


@dataclass
class DataclassFinding:
    summary: str
    confidence: float


def test_a_dataclass_structured_response_is_sent_as_json() -> None:
    text = subagent_result_text({"structured_response": DataclassFinding("OOMKilled", 0.8)})

    assert json.loads(text) == {"summary": "OOMKilled", "confidence": 0.8}
```

En `tests/agents/test_factory.py`, reemplaza el final de `test_middleware_caps_a_looping_model` (desde `result = agent.invoke(...)`) por una aserción exacta:

```python
    agent.invoke({"messages": [HumanMessage("go")]})

    assert len(model.calls) == 2
```

Crea `tests/spikes/test_middleware_async_contract.py`:

```python
"""Spike: custom middleware must implement both the sync and the async hook."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ToolCallRequest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.types import Command

from sre_graph.agents.factory import make_agent
from sre_graph.agents.testing.scripted_model import ScriptedChatModel, tool_call_message

ToolCallResult = ToolMessage | Command[Any]


@tool
async def ping() -> str:
    """Answer pong."""
    return "pong"


class SyncOnlyMiddleware(AgentMiddleware[Any, Any]):
    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolCallResult],
    ) -> ToolCallResult:
        return handler(request)


class SyncAndAsyncMiddleware(SyncOnlyMiddleware):
    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolCallResult]],
    ) -> ToolCallResult:
        return await handler(request)


def _script() -> ScriptedChatModel:
    return ScriptedChatModel(responses=[tool_call_message("ping", {}, "p1"), AIMessage("done")])


async def test_sync_only_middleware_fails_under_ainvoke() -> None:
    agent = make_agent(name="a", model=_script(), tools=[ping], middleware=[SyncOnlyMiddleware()])

    with pytest.raises(NotImplementedError):
        await agent.ainvoke({"messages": [HumanMessage("go")]})


async def test_middleware_with_both_hooks_works_under_ainvoke() -> None:
    agent = make_agent(
        name="a", model=_script(), tools=[ping], middleware=[SyncAndAsyncMiddleware()]
    )

    result = await agent.ainvoke({"messages": [HumanMessage("go")]})

    assert result["messages"][-1].content == "done"
```

Crea `tests/spikes/test_subgraph_node_interrupt.py`:

```python
"""Spike: an interrupt inside a compiled subgraph used as a node pauses and resumes the parent."""

from __future__ import annotations

from typing import Any, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt


class RemediationState(TypedDict, total=False):
    action: str
    decision: str
    executed: list[str]


def _remediation_subgraph() -> Any:
    def approve(state: RemediationState) -> dict[str, Any]:
        return {"decision": interrupt({"approve": state["action"]})}

    def execute(state: RemediationState) -> dict[str, Any]:
        done = [state["action"]] if state["decision"] == "approve" else []
        return {"executed": done}

    graph = StateGraph(RemediationState)
    graph.add_node("approve", approve)
    graph.add_node("execute", execute)
    graph.add_edge(START, "approve")
    graph.add_edge("approve", "execute")
    graph.add_edge("execute", END)
    return graph.compile()


def _parent() -> Any:
    graph = StateGraph(RemediationState)
    graph.add_node("propose", lambda state: {"action": "rollout-undo api"})
    graph.add_node("remediate", _remediation_subgraph())
    graph.add_edge(START, "propose")
    graph.add_edge("propose", "remediate")
    graph.add_edge("remediate", END)
    return graph.compile(checkpointer=InMemorySaver())


def test_parent_pauses_on_the_subgraph_interrupt_and_resumes_through_it() -> None:
    app = _parent()
    config = {"configurable": {"thread_id": "remediation"}}

    paused = app.invoke({}, config)
    resumed = app.invoke(Command(resume="approve"), config)

    assert paused["__interrupt__"][0].value == {"approve": "rollout-undo api"}
    assert resumed["executed"] == ["rollout-undo api"]


def test_a_rejected_decision_executes_nothing() -> None:
    app = _parent()
    config = {"configurable": {"thread_id": "rejected"}}

    app.invoke({}, config)
    resumed = app.invoke(Command(resume="reject"), config)

    assert resumed["executed"] == []
```

- [ ] **Step 2: Verificar que fallan**

Run: `uv run pytest tests/agents/test_scripted_model.py tests/agents/test_subagents.py tests/agents/test_factory.py tests/spikes/test_middleware_async_contract.py tests/spikes/test_subgraph_node_interrupt.py -v`

Expected: `3 failed, 18 passed`.
- `test_reusing_one_scripted_message_keeps_both_replies_in_the_thread` falla con `assert 3 == 4`: la segunda respuesta reemplaza a la primera porque comparten ID.
- `test_only_the_text_of_a_multi_block_reply_reaches_the_supervisor` falla porque llega el JSON de los bloques, `thinking` incluido.
- `test_a_dataclass_structured_response_is_sent_as_json` falla porque llega el `repr` del dataclass.
- Pasan, porque fijan comportamiento existente: `test_synchronous_invoke_forwards_runtime_context_too`, `test_middleware_caps_a_looping_model` y los 4 tests de spikes.

- [ ] **Step 3: Implementación**

En `src/sre_graph/agents/testing/scripted_model.py`, la última línea de `_generate` pasa a devolver una copia:

```python
        return ChatResult(generations=[ChatGeneration(message=message.model_copy(deep=True))])
```

En `src/sre_graph/agents/subagents.py`, cambia el import de `dataclasses` por:

```python
from dataclasses import asdict, dataclass, is_dataclass
```

y reemplaza `subagent_result_text` por:

```python
def subagent_result_text(result: Mapping[str, Any]) -> str:
    """Render a subagent run for its supervisor: structured JSON first, else the reply's text.

    Only the text of the last message is passed on, so provider blocks such as
    thinking or citations never reach the supervisor.
    """
    structured = result.get("structured_response")
    if isinstance(structured, BaseModel):
        return structured.model_dump_json()
    if is_dataclass(structured) and not isinstance(structured, type):
        return json.dumps(asdict(structured), default=str)
    if structured is not None:
        return json.dumps(structured, default=str)
    messages = result.get("messages") or []
    if not messages:
        return "(no response)"
    return str(messages[-1].text)
```

- [ ] **Step 4: Verificar que pasan**

Run: `uv run pytest tests/agents/test_scripted_model.py tests/agents/test_subagents.py tests/agents/test_factory.py tests/spikes/test_middleware_async_contract.py tests/spikes/test_subgraph_node_interrupt.py -v`

Expected: `21 passed`.

- [ ] **Step 5: Documento del spike**

Reemplaza el contenido de `docs/spikes/fase-0-mecanismos.md` por esta versión, que suma la fila 8 y el test de contrato de la fila 5:

````markdown
# Spike de la Fase 0: mecanismos de LangGraph verificados

Versiones: langgraph 1.2.14, langchain 1.4.3, langchain-core 1.6.7, langgraph-checkpoint 4.2.0,
langgraph-checkpoint-sqlite 3.1.1, langgraph-checkpoint-postgres 3.1.2, langgraph-cli 0.4.33.
Entorno: Python 3.12 sobre Windows 11. La fila 8 y el test de contrato de la fila 5 se agregaron en la Fase 1a.

| # | Mecanismo | Resultado | Consecuencia para el diseño | Test |
|---|---|---|---|---|
| 1 | `Send` hacia un nodo que corre un subagente `create_agent` | Cada rama corre su subagente; los hallazgos se fusionan con un reducer `operator.add` | El nodo `specialist` de la Fase 2 sigue este patrón; en tests, una instancia de modelo por rama | `tests/spikes/test_send_fanout.py` |
| 2 | `interrupt()` dentro de una tool de un subagente | Burbujea al supervisor: `ainvoke` devuelve `__interrupt__` y `aget_state().next` no está vacío; `Command(resume=...)` reanuda las dos capas | Las aprobaciones de chat y remediación usan este camino; solo el grafo externo lleva checkpointer | `tests/agents/test_subagents.py` |
| 3 | `@task` dentro de un nodo de `StateGraph` | Al reanudar, el nodo se re-ejecuta desde el inicio, pero el `@task` ya completado se reproduce desde el checkpoint sin repetir el efecto; el código normal antes de `interrupt()` sí se repite | El nodo `execute` de remediación envuelve cada acción en `@task`, y nada con efectos va antes de `interrupt()` | `tests/spikes/test_task_in_node.py` |
| 4 | Runtime context al reanudar | No se persiste: un resume sin `context=` corre con `runtime.context = None` | El RunManager de la Fase 4 reconstruye y pasa el context en cada invoke y en cada resume | `tests/spikes/test_context_on_resume.py` |
| 5 | Middleware propio con `wrap_tool_call` | Funciona; un middleware solo síncrono falla con `NotImplementedError` bajo `ainvoke` | Todo middleware propio implementa `wrap_tool_call` y `awrap_tool_call` | `tests/agents/middleware/test_tool_cache.py`, `tests/spikes/test_middleware_async_contract.py` |
| 6 | Salida estructurada con `ToolStrategy` | `structured_response` trae la instancia Pydantic; la tool se llama como la clase | Los especialistas devuelven su hallazgo con `ToolStrategy`; el supervisor lo recibe como JSON | `tests/agents/test_factory.py`, `tests/agents/test_subagents.py` |
| 7 | Context hacia subagentes | `subagent_tool` reenvía `runtime.context` y las tools del subagente lo ven | Clientes y credenciales viajan en el context hasta los especialistas | `tests/agents/test_subagents.py` |
| 8 | `interrupt()` dentro de un subgrafo compilado usado como nodo | El padre, que es el único con checkpointer, se pausa con el payload del subgrafo; `Command(resume=...)` reanuda dentro del subgrafo, y un rechazo no ejecuta nada | El subgrafo de remediación de la Fase 5 se monta como nodo del grafo de investigación, sin checkpointer propio | `tests/spikes/test_subgraph_node_interrupt.py` |

## Hallazgos de entorno

- **psycopg async en Windows** requiere un loop selector. Los tests lo fijan con el hook `pytest_asyncio_loop_factories`; el override de `event_loop_policy` está deprecado en pytest-asyncio 1.4. El servidor en Windows lo necesitará en la Fase 4.
- **`langgraph dev`** corre en Windows sin licencia ni LangSmith (`GET /ok` → `{"ok":true}`). Si se lanza desde un script, al detenerlo hay que matar también el proceso hijo del reloader; en una terminal basta con Ctrl+C.
- **`init_chat_model`** construye el modelo sin API key y falla recién al invocar, así que los grafos de Studio importan sin claves.
- **pydantic-settings no exporta `.env` a `os.environ`.** Las claves de proveedor (`ANTHROPIC_API_KEY`, …) llegan por entorno: `langgraph dev` las carga vía `"env": ".env"`, y el CLI y el servidor llamarán `load_dotenv()` en su raíz de composición (Fase 2).
- **python-dotenv ya ignora el BOM** de un `.env` guardado con el Bloc de notas.
````

- [ ] **Step 6: Gate**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run lint-imports && SRE_GRAPH_TEST_DATABASE_URL="postgresql://sre_graph:sre_graph@localhost:5433/sre_graph?connect_timeout=5" SRE_GRAPH_REQUIRE_POSTGRES=1 uv run pytest
```

Expected: todo en verde y `72 passed`.

- [ ] **Step 7: Commit**

```bash
git add src/sre_graph/agents/testing/scripted_model.py src/sre_graph/agents/subagents.py tests/agents/test_scripted_model.py tests/agents/test_subagents.py tests/agents/test_factory.py tests/spikes/test_middleware_async_contract.py tests/spikes/test_subgraph_node_interrupt.py docs/spikes/fase-0-mecanismos.md
git commit -m "fix(agents): copy scripted replies, pass only reply text to supervisors, pin async and subgraph contracts" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Metadatos de tools en el dominio y caché sobre ellos

Cada tool de integración declara para qué sirve y qué puede cambiar. Ese contrato vive en `domain/tools.py`, porque lo leen la caché, la política de aprobación y el registro de integraciones. La caché de la Fase 0 deja de leer claves sueltas de `metadata` y pasa a usar `ToolMeta`.

**Files:**
- Create: `src/sre_graph/domain/__init__.py`, `src/sre_graph/domain/tools.py`
- Modify: `src/sre_graph/agents/middleware/tool_cache.py`, `pyproject.toml`
- Test: `tests/domain/test_tools.py`, `tests/agents/middleware/test_tool_cache.py`

**Interfaces:**
- Consumes: `ToolResultCacheMiddleware` y `CACHE_SCOPE_KEY` de la Fase 0.
- Produces:
  - Enumeraciones (`StrEnum`):
    - `Domain`: `kubernetes`, `metrics`, `logs`, `traces`, `cloud`, `changes`, `errors`;
    - `SideEffectLevel`: `read_only`, `mutating`, `external`;
    - `EvidenceType`: `logs`, `metrics`, `traces`, `events`, `topology`, `changes`, `errors`.
  - `@dataclass(frozen=True) class ToolMeta(integration: str, domains: frozenset[Domain], evidence_type: EvidenceType, side_effect_level: SideEffectLevel = READ_ONLY, idempotent: bool = True)`, con:
    - `as_metadata() -> dict[str, Any]`, que da un dict plano con `domains` ordenado;
    - `ToolMeta.from_metadata(metadata) -> ToolMeta | None`, que devuelve `None` sin metadatos y lanza `ValueError` si están mal formados.
  - `attach_tool_meta(tool: ToolT, meta: ToolMeta) -> ToolT` conserva la metadata ajena; `read_tool_meta(tool: BaseTool | None) -> ToolMeta | None`.
  - `tool_cache.py` pierde `SIDE_EFFECT_LEVEL_KEY`, `READ_ONLY` e `is_read_only_tool`, y solo cachea tools con `ToolMeta` de nivel `READ_ONLY`.

- [ ] **Step 1: Tests que fallan**

Crea `tests/domain/test_tools.py`:

```python
from __future__ import annotations

from typing import Any

import pytest
from langchain_core.tools import tool

from sre_graph.domain.tools import (
    Domain,
    EvidenceType,
    SideEffectLevel,
    ToolMeta,
    attach_tool_meta,
    read_tool_meta,
)


@tool
def list_pods(namespace: str) -> str:
    """List pods."""
    return namespace


def _meta(**overrides: Any) -> ToolMeta:
    values: dict[str, Any] = {
        "integration": "kubernetes",
        "domains": frozenset({Domain.KUBERNETES, Domain.LOGS}),
        "evidence_type": EvidenceType.TOPOLOGY,
    }
    values.update(overrides)
    return ToolMeta(**values)


def test_metadata_is_flat_and_json_friendly() -> None:
    assert _meta().as_metadata() == {
        "integration": "kubernetes",
        "domains": ["kubernetes", "logs"],
        "evidence_type": "topology",
        "side_effect_level": "read_only",
        "idempotent": True,
    }


def test_metadata_round_trips_through_a_tool() -> None:
    meta = _meta(side_effect_level=SideEffectLevel.MUTATING, idempotent=False)
    tagged = attach_tool_meta(list_pods.model_copy(), meta)

    assert read_tool_meta(tagged) == meta


def test_attaching_keeps_unrelated_metadata() -> None:
    plain = list_pods.model_copy(update={"metadata": {"owner": "sre"}})

    tagged = attach_tool_meta(plain, _meta())

    assert tagged.metadata is not None
    assert tagged.metadata["owner"] == "sre"


def test_a_tool_without_metadata_declares_nothing() -> None:
    assert read_tool_meta(list_pods.model_copy()) is None
    assert read_tool_meta(None) is None


def test_malformed_metadata_is_rejected() -> None:
    broken = {**_meta().as_metadata(), "side_effect_level": "sometimes"}

    with pytest.raises(ValueError, match="sometimes"):
        ToolMeta.from_metadata(broken)
```

Reemplaza el contenido de `tests/agents/middleware/test_tool_cache.py` por esta versión. Sus tools declaran `ToolMeta` y el test de tools no cacheables cubre "sin metadatos", `MUTATING` y `EXTERNAL`:

```python
from __future__ import annotations

from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import BaseTool, ToolException, tool

from sre_graph.agents.factory import make_agent
from sre_graph.agents.middleware.tool_cache import CACHE_SCOPE_KEY, ToolResultCacheMiddleware
from sre_graph.agents.testing.scripted_model import ScriptedChatModel, tool_call_message
from sre_graph.domain.tools import Domain, EvidenceType, SideEffectLevel, ToolMeta, attach_tool_meta


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _two_identical_calls(name: str = "get_pods") -> ScriptedChatModel:
    return ScriptedChatModel(
        responses=[
            tool_call_message(name, {"namespace": "payments"}, "first"),
            tool_call_message(name, {"namespace": "payments"}, "second"),
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


def _run_config(scope: str | None, thread_id: str = "t1") -> dict[str, Any]:
    configurable: dict[str, Any] = {"thread_id": thread_id}
    if scope is not None:
        configurable[CACHE_SCOPE_KEY] = scope
    return {"configurable": configurable}


def _meta(level: SideEffectLevel) -> ToolMeta:
    return ToolMeta(
        integration="test",
        domains=frozenset({Domain.KUBERNETES}),
        evidence_type=EvidenceType.TOPOLOGY,
        side_effect_level=level,
    )


def _read_only(declared: BaseTool) -> BaseTool:
    return attach_tool_meta(declared, _meta(SideEffectLevel.READ_ONLY))


def test_repeated_identical_read_only_call_is_served_from_cache() -> None:
    executions: list[str] = []

    @tool
    def get_pods(namespace: str) -> str:
        """List pods in a namespace."""
        executions.append(namespace)
        return f"3 pods in {namespace}"

    cache = ToolResultCacheMiddleware()
    agent = make_agent(
        name="k8s", model=_two_identical_calls(), tools=[_read_only(get_pods)], middleware=[cache]
    )

    result = agent.invoke({"messages": [HumanMessage("go")]}, _run_config("run-1"))

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
        name="k8s", model=_two_identical_calls(), tools=[_read_only(get_pods)], middleware=[cache]
    )

    await agent.ainvoke({"messages": [HumanMessage("go")]}, _run_config("run-1"))

    assert executions == ["payments"]
    assert cache.hits == 1


def test_a_new_run_on_the_same_thread_reaches_the_tool_again() -> None:
    executions: list[str] = []

    @tool
    def get_pods(namespace: str) -> str:
        """List pods in a namespace."""
        executions.append(namespace)
        return f"3 pods in {namespace}"

    cache = ToolResultCacheMiddleware()
    for turn in ("turn-1", "turn-2"):
        agent = make_agent(
            name="k8s", model=_one_call(turn), tools=[_read_only(get_pods)], middleware=[cache]
        )
        agent.invoke({"messages": [HumanMessage("go")]}, _run_config(turn, thread_id="chat-1"))

    assert executions == ["payments", "payments"]
    assert cache.hits == 0


def test_calls_without_a_scope_are_never_cached() -> None:
    executions: list[str] = []

    @tool
    def get_pods(namespace: str) -> str:
        """List pods in a namespace."""
        executions.append(namespace)
        return f"3 pods in {namespace}"

    cache = ToolResultCacheMiddleware()
    agent = make_agent(
        name="k8s", model=_two_identical_calls(), tools=[_read_only(get_pods)], middleware=[cache]
    )

    agent.invoke({"messages": [HumanMessage("go")]}, _run_config(None))

    assert executions == ["payments", "payments"]
    assert cache.hits == 0


@pytest.mark.parametrize("level", [None, SideEffectLevel.MUTATING, SideEffectLevel.EXTERNAL])
def test_tools_not_declared_read_only_always_run(level: SideEffectLevel | None) -> None:
    restarts: list[str] = []

    @tool
    def rollout_restart(namespace: str) -> str:
        """Restart every deployment in a namespace."""
        restarts.append(namespace)
        return f"restarted {namespace}"

    if level is not None:
        attach_tool_meta(rollout_restart, _meta(level))
    cache = ToolResultCacheMiddleware()
    agent = make_agent(
        name="ops",
        model=_two_identical_calls("rollout_restart"),
        tools=[rollout_restart],
        middleware=[cache],
    )

    agent.invoke({"messages": [HumanMessage("go")]}, _run_config("run-1"))

    assert restarts == ["payments", "payments"]
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
    read_only_pods = _read_only(get_pods)
    make_agent(name="k8s", model=_one_call("a"), tools=[read_only_pods], middleware=[cache]).invoke(
        {"messages": [HumanMessage("go")]}, _run_config("run-1")
    )
    clock.now = 301.0
    make_agent(name="k8s", model=_one_call("b"), tools=[read_only_pods], middleware=[cache]).invoke(
        {"messages": [HumanMessage("go")]}, _run_config("run-1")
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
        name="k8s", model=_two_identical_calls(), tools=[_read_only(get_pods)], middleware=[cache]
    )

    result = agent.invoke({"messages": [HumanMessage("go")]}, _run_config("run-1"))

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
    agent = make_agent(name="logs", model=model, tools=[_read_only(get_logs)], middleware=[cache])

    agent.invoke({"messages": [HumanMessage("go")]}, _run_config("run-1"))

    assert executions == [("checkout", 50)]
    assert cache.hits == 1
```

- [ ] **Step 2: Verificar que fallan**

Run: `uv run pytest tests/domain/test_tools.py tests/agents/middleware/test_tool_cache.py -v`

Expected: `2 errors` durante la colección, con `ModuleNotFoundError: No module named 'sre_graph.domain'`.

- [ ] **Step 3: Implementación**

`src/sre_graph/domain/__init__.py`:

```python
"""Pure domain types shared by integrations, agents and graphs."""
```

`src/sre_graph/domain/tools.py`:

```python
"""The metadata every integration tool carries: what it is for and what it may change."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from langchain_core.tools import BaseTool


class Domain(StrEnum):
    """The specialist area a tool serves; each domain gets one specialist agent."""

    KUBERNETES = "kubernetes"
    METRICS = "metrics"
    LOGS = "logs"
    TRACES = "traces"
    CLOUD = "cloud"
    CHANGES = "changes"
    ERRORS = "errors"


class SideEffectLevel(StrEnum):
    """What a call can change; anything other than ``READ_ONLY`` needs human approval."""

    READ_ONLY = "read_only"
    MUTATING = "mutating"
    EXTERNAL = "external"


class EvidenceType(StrEnum):
    """The kind of evidence a tool returns."""

    LOGS = "logs"
    METRICS = "metrics"
    TRACES = "traces"
    EVENTS = "events"
    TOPOLOGY = "topology"
    CHANGES = "changes"
    ERRORS = "errors"


@dataclass(frozen=True)
class ToolMeta:
    """Routing and safety facts about one tool, stored flat in ``BaseTool.metadata``."""

    integration: str
    domains: frozenset[Domain]
    evidence_type: EvidenceType
    side_effect_level: SideEffectLevel = SideEffectLevel.READ_ONLY
    idempotent: bool = True

    def as_metadata(self) -> dict[str, Any]:
        """The flat, JSON-friendly form kept on the tool."""
        return {
            "integration": self.integration,
            "domains": sorted(domain.value for domain in self.domains),
            "evidence_type": self.evidence_type.value,
            "side_effect_level": self.side_effect_level.value,
            "idempotent": self.idempotent,
        }

    @classmethod
    def from_metadata(cls, metadata: Mapping[str, Any] | None) -> ToolMeta | None:
        """Read the facts back; None when the tool carries none, ValueError when malformed."""
        if not metadata or "integration" not in metadata:
            return None
        return cls(
            integration=str(metadata["integration"]),
            domains=frozenset(Domain(value) for value in metadata.get("domains", ())),
            evidence_type=EvidenceType(metadata["evidence_type"]),
            side_effect_level=SideEffectLevel(metadata["side_effect_level"]),
            idempotent=bool(metadata.get("idempotent", True)),
        )


def attach_tool_meta[ToolT: BaseTool](tool: ToolT, meta: ToolMeta) -> ToolT:
    """Store ``meta`` on ``tool`` (keeping any unrelated metadata) and return the tool."""
    tool.metadata = {**(tool.metadata or {}), **meta.as_metadata()}
    return tool


def read_tool_meta(tool: BaseTool | None) -> ToolMeta | None:
    """The tool's metadata, or None for a missing tool or one that declares nothing."""
    return None if tool is None else ToolMeta.from_metadata(tool.metadata)
```

Reemplaza el contenido de `src/sre_graph/agents/middleware/tool_cache.py` por:

```python
"""Serve repeated identical read-only tool calls from a short-lived, run-scoped cache."""

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

from sre_graph.domain.tools import SideEffectLevel, read_tool_meta

#: ``configurable`` key an entry point sets to a fresh id for every run it starts.
CACHE_SCOPE_KEY = "tool_cache_scope"

ToolCallResult = ToolMessage | Command[Any]


class ToolResultCacheMiddleware(AgentMiddleware[Any, Any]):
    """Reuse a read-only tool's successful result when it runs again with the same arguments.

    Entries are shared only inside one caching scope: an entry point puts a
    fresh ``configurable[CACHE_SCOPE_KEY]`` on every run, so a later run (such
    as the next chat turn) re-reads live data, and a call without a scope is
    never cached. Tools that do not declare themselves read-only always run, so
    an approved action is never skipped. Entries also expire after
    ``ttl_seconds``, and error results are never cached.
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
        if key is None:
            return handler(request)
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
        if key is None:
            return await handler(request)
        cached = self._lookup(key, request)
        if cached is not None:
            return cached
        result = await handler(request)
        self._remember(key, result)
        return result

    @staticmethod
    def _key(request: ToolCallRequest) -> str | None:
        """The cache key for this call, or None when the call must not be cached."""
        meta = read_tool_meta(request.tool)
        if meta is None or meta.side_effect_level is not SideEffectLevel.READ_ONLY:
            return None
        config = request.runtime.config if request.runtime is not None else {}
        scope = config.get("configurable", {}).get(CACHE_SCOPE_KEY)
        if not scope:
            return None
        call = request.tool_call
        return json.dumps([str(scope), call["name"], call["args"]], sort_keys=True, default=str)

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

En `pyproject.toml`, agrega la capa `domain` al contrato de import-linter:

```toml
layers = [
    "sre_graph.graphs",
    "sre_graph.agents",
    "sre_graph.domain",
    "sre_graph.llm | sre_graph.persistence",
    "sre_graph.config",
]
```

- [ ] **Step 4: Verificar que pasan**

Run: `uv run pytest tests/domain/test_tools.py tests/agents/middleware/test_tool_cache.py -v`

Expected: `15 passed`.

- [ ] **Step 5: Gate**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run lint-imports && SRE_GRAPH_TEST_DATABASE_URL="postgresql://sre_graph:sre_graph@localhost:5433/sre_graph?connect_timeout=5" SRE_GRAPH_REQUIRE_POSTGRES=1 uv run pytest
```

Expected: todo en verde y `78 passed`.

- [ ] **Step 6: Commit**

```bash
git add src/sre_graph/domain src/sre_graph/agents/middleware/tool_cache.py tests/domain tests/agents/middleware/test_tool_cache.py pyproject.toml
git commit -m "feat(domain): ToolMeta contract for tool routing and side effects; cache reads it" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Redacción de secretos y `GuardrailsMiddleware`

Toda salida de tool pasa por la redacción antes de que la vean el modelo o el checkpoint. Las reglas cubren formatos conocidos (llaves privadas, AWS, GitHub, Slack, claves de LLM, Google, JWT, Bearer, credenciales en URLs) y asignaciones del tipo `password=...`. Solo se reemplaza el secreto: el nombre de la variable o el host quedan, porque son evidencia útil.

**Files:**
- Create: `src/sre_graph/safety/__init__.py`, `src/sre_graph/safety/secrets.py`, `src/sre_graph/agents/middleware/guardrails.py`
- Modify: `pyproject.toml`
- Test: `tests/safety/test_secrets.py`, `tests/agents/middleware/test_guardrails.py`

**Interfaces:**
- Consumes: `make_agent`, `ScriptedChatModel` y `tool_call_message`.
- Produces:
  - `redact_secrets(text: str) -> str`. Cada secreto pasa a `[REDACTED:<kind>]`; un valor ya redactado por una regla específica conserva su `kind`.
  - `redact_value(value: Any) -> Any`, recursivo sobre `dict`, `list` y `tuple`.
  - `SecretPattern(kind: str, regex: re.Pattern[str])` y `SECRET_PATTERNS: tuple[SecretPattern, ...]`.
  - `GuardrailsMiddleware()` (sin argumentos) redacta `content` y `artifact` de cada `ToolMessage`, sync y async; un `Command` pasa intacto.

- [ ] **Step 1: Tests que fallan**

Crea `tests/safety/test_secrets.py`:

```python
from __future__ import annotations

import pytest

from sre_graph.safety.secrets import redact_secrets, redact_value

# Built at runtime so this file holds no string that looks like a real credential.
AWS_KEY = "AKIA" + "ABCDEFGHIJKLMNOP"
GITHUB_TOKEN = "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"
SLACK_TOKEN = "xoxb-" + "123456789012-abcdefghijkl"
ANTHROPIC_KEY = "sk-ant-" + "api03-abcdefghijklmnopqrstuvwxyz012345"
JWT = "eyJhbGciOiJIUzI1NiJ9" + ".eyJzdWIiOiIxMjM0NTY3ODkwIn0" + ".c2lnbmF0dXJlMTIzNDU2"
PRIVATE_KEY = "-----BEGIN RSA PRIVATE KEY-----\nMIIEow\n-----END RSA PRIVATE KEY-----"


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        (f"key id {AWS_KEY} in use", "aws_access_key_id"),
        (f"token {GITHUB_TOKEN}", "github_token"),
        (f"slack {SLACK_TOKEN}", "slack_token"),
        (f"anthropic {ANTHROPIC_KEY}", "llm_api_key"),
        (f"session {JWT}", "jwt"),
        (f"key:\n{PRIVATE_KEY}\nend", "private_key"),
    ],
)
def test_known_credential_formats_are_redacted(text: str, kind: str) -> None:
    redacted = redact_secrets(text)

    assert f"[REDACTED:{kind}]" in redacted
    for secret in (AWS_KEY, GITHUB_TOKEN, SLACK_TOKEN, ANTHROPIC_KEY, JWT, "MIIEow"):
        assert secret not in redacted


def test_only_the_secret_part_of_a_bearer_header_is_replaced() -> None:
    redacted = redact_secrets("Authorization: Bearer abcdefghijklmnop0123456789")

    assert redacted == "Authorization: Bearer [REDACTED:bearer_token]"


def test_url_credentials_are_removed_but_the_host_stays() -> None:
    redacted = redact_secrets("dsn=postgres://app:hunter2@db.internal:5432/orders")

    assert "hunter2" not in redacted
    assert "@db.internal:5432/orders" in redacted


@pytest.mark.parametrize(
    "text",
    [
        "DB_PASSWORD=hunter2 next",
        "api_key: hunter2",
        '"client_secret": "hunter2"',
        "x-access-key='hunter2'",
    ],
)
def test_assigned_secrets_lose_their_value_and_keep_their_name(text: str) -> None:
    redacted = redact_secrets(text)

    assert "hunter2" not in redacted
    assert "[REDACTED:assigned_secret]" in redacted


def test_a_value_already_redacted_by_a_specific_rule_keeps_its_kind() -> None:
    redacted = redact_secrets(f'token: "{GITHUB_TOKEN}"')

    assert redacted == 'token: "[REDACTED:github_token]"'


def test_ordinary_operational_text_is_left_alone() -> None:
    text = "pod checkout-7d9f8b6c5-x2x9z restarted 3 times; max_tokens=4096; tokens: 12"

    assert redact_secrets(text) == text


def test_nested_values_are_redacted_and_non_strings_kept() -> None:
    value = {"env": [{"name": "TOKEN", "value": f"token={GITHUB_TOKEN}"}], "restarts": 3}

    redacted = redact_value(value)

    assert redacted == {
        "env": [{"name": "TOKEN", "value": "token=[REDACTED:github_token]"}],
        "restarts": 3,
    }
```

Crea `tests/agents/middleware/test_guardrails.py`:

```python
from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver

from sre_graph.agents.factory import make_agent
from sre_graph.agents.middleware.guardrails import GuardrailsMiddleware
from sre_graph.agents.testing.scripted_model import ScriptedChatModel, tool_call_message

# Built at runtime so this file holds no string that looks like a real credential.
GITHUB_TOKEN = "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"


@tool(response_format="content_and_artifact")
def read_config(service: str) -> tuple[str, dict[str, Any]]:
    """Read a service's configuration."""
    return f"GITHUB_TOKEN={GITHUB_TOKEN}", {"env": {"GITHUB_TOKEN": GITHUB_TOKEN}}


def _model() -> ScriptedChatModel:
    return ScriptedChatModel(
        responses=[tool_call_message("read_config", {"service": "api"}, "c1"), AIMessage("done")]
    )


def test_the_model_never_sees_a_secret_returned_by_a_tool() -> None:
    model = _model()
    agent = make_agent(
        name="k8s", model=model, tools=[read_config], middleware=[GuardrailsMiddleware()]
    )

    agent.invoke({"messages": [HumanMessage("check api")]})

    second_call = model.calls[1]
    assert GITHUB_TOKEN not in str([message.content for message in second_call])
    assert "[REDACTED:github_token]" in str(second_call[-1].content)


def test_the_checkpoint_holds_the_redacted_content_and_artifact() -> None:
    agent = make_agent(
        name="k8s",
        model=_model(),
        tools=[read_config],
        middleware=[GuardrailsMiddleware()],
        checkpointer=InMemorySaver(),
    )
    config: Any = {"configurable": {"thread_id": "t1"}}

    agent.invoke({"messages": [HumanMessage("check api")]}, config)

    saved = agent.get_state(config).values["messages"]
    tool_message = next(m for m in saved if isinstance(m, ToolMessage))
    assert GITHUB_TOKEN not in str(tool_message.content)
    assert tool_message.artifact == {"env": {"GITHUB_TOKEN": "[REDACTED:github_token]"}}


async def test_redaction_also_applies_to_async_runs() -> None:
    agent = make_agent(
        name="k8s", model=_model(), tools=[read_config], middleware=[GuardrailsMiddleware()]
    )

    result = await agent.ainvoke({"messages": [HumanMessage("check api")]})

    tool_message = next(m for m in result["messages"] if isinstance(m, ToolMessage))
    assert GITHUB_TOKEN not in str(tool_message.content)
```

- [ ] **Step 2: Verificar que fallan**

Run: `uv run pytest tests/safety/test_secrets.py tests/agents/middleware/test_guardrails.py -v`

Expected: `2 errors` durante la colección, con `ModuleNotFoundError: No module named 'sre_graph.safety'` y `No module named 'sre_graph.agents.middleware.guardrails'`.

- [ ] **Step 3: Implementación**

`src/sre_graph/safety/__init__.py`:

```python
"""Secret redaction, identifier masking and the approval policy."""
```

`src/sre_graph/safety/secrets.py`:

```python
"""Redact credentials from text before a model, a checkpoint or a channel can see them."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

_REDACTED_PREFIX = "[REDACTED:"


@dataclass(frozen=True)
class SecretPattern:
    """A credential format. When the regex has a ``secret`` group only that part is replaced."""

    kind: str
    regex: re.Pattern[str]


#: Most specific formats first; the generic ``key=value`` rule runs last.
SECRET_PATTERNS: tuple[SecretPattern, ...] = (
    SecretPattern(
        "private_key",
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
    ),
    SecretPattern("aws_access_key_id", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    SecretPattern(
        "github_token",
        re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,255}|github_pat_[A-Za-z0-9_]{22,255})\b"),
    ),
    SecretPattern("slack_token", re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}\b")),
    SecretPattern("llm_api_key", re.compile(r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_-]{20,}\b")),
    SecretPattern("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    SecretPattern(
        "jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")
    ),
    SecretPattern(
        "bearer_token", re.compile(r"(?i)\bbearer\s+(?P<secret>[A-Za-z0-9\-._~+/]{16,}=*)")
    ),
    SecretPattern(
        "url_credentials",
        re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://(?P<secret>[^\s/:@]+:[^\s/@]+)@"),
    ),
    SecretPattern(
        "assigned_secret",
        re.compile(
            r"(?i)\b[a-z0-9_.-]*(?:password|passwd|pwd|secret|api[_-]?key|access[_-]?key"
            r"|private[_-]?key|token)\"?\s*[:=]\s*(?P<secret>\"[^\"]*\"|'[^']*'|[^\s,;&\"']+)"
        ),
    ),
)


def redact_secrets(text: str) -> str:
    """Replace every recognised credential in ``text`` with ``[REDACTED:<kind>]``."""
    for pattern in SECRET_PATTERNS:
        text = pattern.regex.sub(_replacer(pattern.kind), text)
    return text


def redact_value(value: Any) -> Any:
    """Redact credentials in every string inside nested dicts, lists and tuples."""
    if isinstance(value, str):
        return redact_secrets(value)
    if isinstance(value, dict):
        return {key: redact_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_value(item) for item in value)
    return value


def _replacer(kind: str) -> Callable[[re.Match[str]], str]:
    marker = f"{_REDACTED_PREFIX}{kind}]"

    def replace(match: re.Match[str]) -> str:
        if "secret" not in match.re.groupindex:
            return marker
        if match.group("secret").strip("\"'").startswith(_REDACTED_PREFIX):
            return match.group(0)
        start, end = match.span("secret")
        whole, offset = match.group(0), match.start()
        return whole[: start - offset] + marker + whole[end - offset :]

    return replace
```

`src/sre_graph/agents/middleware/guardrails.py`:

```python
"""Redact credentials from every tool result before the model or the checkpoint sees it."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ToolCallRequest
from langchain_core.messages import ToolMessage
from langgraph.types import Command

from sre_graph.safety.secrets import redact_value

ToolCallResult = ToolMessage | Command[Any]


class GuardrailsMiddleware(AgentMiddleware[Any, Any]):
    """Replace secrets in tool results (content and artifact) with ``[REDACTED:<kind>]``."""

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolCallResult],
    ) -> ToolCallResult:
        return _redacted(handler(request))

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolCallResult]],
    ) -> ToolCallResult:
        return _redacted(await handler(request))


def _redacted(result: ToolCallResult) -> ToolCallResult:
    if not isinstance(result, ToolMessage):
        return result
    return result.model_copy(
        update={
            "content": redact_value(result.content),
            "artifact": redact_value(result.artifact),
        }
    )
```

En `pyproject.toml`, agrega la capa `safety` encima de `domain`:

```toml
layers = [
    "sre_graph.graphs",
    "sre_graph.agents",
    "sre_graph.safety",
    "sre_graph.domain",
    "sre_graph.llm | sre_graph.persistence",
    "sre_graph.config",
]
```

- [ ] **Step 4: Verificar que pasan**

Run: `uv run pytest tests/safety/test_secrets.py tests/agents/middleware/test_guardrails.py -v`

Expected: `18 passed`.

- [ ] **Step 5: Gate**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run lint-imports && SRE_GRAPH_TEST_DATABASE_URL="postgresql://sre_graph:sre_graph@localhost:5433/sre_graph?connect_timeout=5" SRE_GRAPH_REQUIRE_POSTGRES=1 uv run pytest
```

Expected: todo en verde y `96 passed`.

- [ ] **Step 6: Commit**

```bash
git add src/sre_graph/safety src/sre_graph/agents/middleware/guardrails.py tests/safety/test_secrets.py tests/agents/middleware/test_guardrails.py pyproject.toml
git commit -m "feat(safety): redact credentials from every tool result before the model or checkpoint" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Masking reversible y `MaskingMiddleware`

Con `MASK_ENABLED=true`, los identificadores de infraestructura (pods, namespaces, clusters, hosts, cuentas, IPs, emails, servicios y patrones extra) se reemplazan por tokens estables antes de cada llamada al modelo. La respuesta del modelo (texto, args de tool calls y salida estructurada) se desenmascara antes de guardarse o de que corra una tool. Los detectores se portan de opensre (`infrastructure/safety/masking/detectors.py`), reescritos.

**Files:**
- Create: `src/sre_graph/safety/masking.py`, `src/sre_graph/agents/middleware/masking.py`
- Test: `tests/safety/test_masking.py`, `tests/agents/middleware/test_masking.py`

**Interfaces:**
- Consumes: `Settings.mask_enabled`, `Settings.mask_kinds` y `Settings.mask_extra_regex` (Tarea 1).
- Produces:
  - `IdentifierKind(StrEnum)`: `pod`, `namespace`, `cluster`, `hostname`, `account_id`, `ip_address`, `email`, `service_name`.
  - `@dataclass(frozen=True) class MaskingPolicy(enabled: bool = False, kinds: frozenset[IdentifierKind] = all, extra_patterns: Mapping[str, re.Pattern[str]] = {})`, con `from_settings(settings)`:
    - `MASK_KINDS` vacío significa todos los tipos;
    - un tipo desconocido lanza `ValueError`.
  - `Masker(policy)` con `mask(text)`, `unmask(text)`, `mask_value(value)` y `unmask_value(value)`:
    - el token tiene la forma `<LABEL_` + 10 hex de SHA-256 de `label:valor` + `>`;
    - `unmask` solo restaura los tokens que emitió esa instancia.
  - `MaskingMiddleware(policy)`:
    - enmascara mensajes y system prompt (sync y async);
    - desenmascara `AIMessage`, `ModelResponse` (resultado y `structured_response` Pydantic o dataclass) y `ExtendedModelResponse`;
    - con la política apagada, pasa la llamada sin tocarla.

- [ ] **Step 1: Tests que fallan**

Crea `tests/safety/test_masking.py`:

```python
from __future__ import annotations

import re

import pytest

from sre_graph.config.settings import Settings
from sre_graph.safety.masking import IdentifierKind, Masker, MaskingPolicy

POD = "checkout-7d9f8b6c5-x2x9z"
ENABLED = MaskingPolicy(enabled=True)


def test_a_disabled_policy_changes_nothing() -> None:
    text = f"pod {POD} in namespace: payments"

    assert Masker(MaskingPolicy()).mask(text) == text


def test_identifiers_are_replaced_and_their_labels_kept() -> None:
    masked = Masker(ENABLED).mask(f"pod {POD} in namespace: payments is CrashLoopBackOff")

    assert POD not in masked
    assert "payments" not in masked
    assert re.fullmatch(
        r"pod <POD_[0-9a-f]{10}> in namespace: <NAMESPACE_[0-9a-f]{10}> is CrashLoopBackOff",
        masked,
    )


def test_unmasking_restores_the_original_text() -> None:
    masker = Masker(ENABLED)
    text = "node ip-10-0-1-5.ec2.internal at 10.0.1.5, account 123456789012, oncall@acme.io"

    assert masker.unmask(masker.mask(text)) == text


def test_the_same_value_gets_the_same_token_in_every_call() -> None:
    first = Masker(ENABLED).mask(f"restart {POD}")
    second = Masker(ENABLED).mask(f"logs for {POD}")

    assert first.split()[-1] == second.split()[-1]


def test_tokens_this_masker_did_not_issue_are_left_alone() -> None:
    masker = Masker(ENABLED)
    masker.mask(f"pod {POD}")

    assert masker.unmask("compare <POD_0123456789>") == "compare <POD_0123456789>"


def test_an_email_is_masked_whole_not_as_a_hostname() -> None:
    masked = Masker(ENABLED).mask("page oncall@acme.io now")

    assert re.fullmatch(r"page <EMAIL_[0-9a-f]{10}> now", masked)


def test_only_the_selected_kinds_are_masked() -> None:
    policy = MaskingPolicy(enabled=True, kinds=frozenset({IdentifierKind.IP_ADDRESS}))

    masked = Masker(policy).mask(f"{POD} at 10.0.1.5")

    assert masked.startswith(POD)
    assert "10.0.1.5" not in masked


def test_extra_patterns_mask_with_their_own_label() -> None:
    policy = MaskingPolicy(
        enabled=True, kinds=frozenset(), extra_patterns={"ticket": re.compile(r"TCK-\d+")}
    )

    masked = Masker(policy).mask("see TCK-4411")

    assert re.fullmatch(r"see <TICKET_[0-9a-f]{10}>", masked)


def test_nested_values_are_masked_and_restored() -> None:
    masker = Masker(ENABLED)
    value = {"pods": [POD], "namespace": "namespace: payments", "restarts": 3}

    masked = masker.mask_value(value)

    assert POD not in str(masked)
    assert masked["restarts"] == 3
    assert masker.unmask_value(masked) == value


def test_policy_from_settings_defaults_to_every_kind() -> None:
    policy = MaskingPolicy.from_settings(Settings(_env_file=None, mask_enabled=True))

    assert policy.enabled is True
    assert policy.kinds == frozenset(IdentifierKind)


def test_policy_from_settings_reads_kinds_and_extra_patterns() -> None:
    settings = Settings(
        _env_file=None,
        mask_enabled=True,
        mask_kinds=("pod", "email"),
        mask_extra_regex={"ticket": r"TCK-\d+"},
    )

    policy = MaskingPolicy.from_settings(settings)

    assert policy.kinds == {IdentifierKind.POD, IdentifierKind.EMAIL}
    assert policy.extra_patterns["ticket"].pattern == r"TCK-\d+"


def test_an_unknown_kind_in_settings_is_rejected() -> None:
    with pytest.raises(ValueError, match="podz"):
        MaskingPolicy.from_settings(Settings(_env_file=None, mask_kinds=("podz",)))
```

Crea `tests/agents/middleware/test_masking.py`:

```python
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from langchain.agents.structured_output import ToolStrategy
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import BaseTool, tool
from langgraph.graph.state import CompiledStateGraph
from pydantic import BaseModel

from sre_graph.agents.factory import make_agent
from sre_graph.agents.middleware.masking import MaskingMiddleware
from sre_graph.agents.testing.scripted_model import ScriptedChatModel, tool_call_message
from sre_graph.safety.masking import Masker, MaskingPolicy

POD = "checkout-7d9f8b6c5-x2x9z"
POLICY = MaskingPolicy(enabled=True)
POD_TOKEN = Masker(POLICY).mask(POD)


class Finding(BaseModel):
    summary: str


@dataclass
class DataclassFinding:
    summary: str


def _pod_logs(received: list[str]) -> BaseTool:
    @tool
    def pod_logs(pod: str) -> str:
        """Fetch a pod's logs."""
        received.append(pod)
        return f"{pod}: OOMKilled"

    return pod_logs


def _masked_agent(
    model: ScriptedChatModel, **kwargs: Any
) -> CompiledStateGraph[Any, Any, Any, Any]:
    return make_agent(name="k8s", model=model, middleware=[MaskingMiddleware(POLICY)], **kwargs)


def test_the_token_is_a_pod_token() -> None:
    assert re.fullmatch(r"<POD_[0-9a-f]{10}>", POD_TOKEN)


def test_the_model_sees_tokens_and_the_tool_receives_the_real_pod() -> None:
    received: list[str] = []
    model = ScriptedChatModel(
        responses=[
            tool_call_message("pod_logs", {"pod": POD_TOKEN}, "c1"),
            AIMessage(f"{POD_TOKEN} was OOMKilled"),
        ]
    )
    agent = _masked_agent(model, tools=[_pod_logs(received)])

    result = agent.invoke({"messages": [HumanMessage(f"why does {POD} restart?")]})

    assert received == [POD]
    assert POD not in str([message.content for call in model.calls for message in call])
    assert result["messages"][-1].content == f"{POD} was OOMKilled"


def test_the_system_prompt_is_masked_too() -> None:
    model = ScriptedChatModel(responses=[AIMessage("ok")])
    agent = _masked_agent(model, system_prompt=f"Focus on {POD}.")

    agent.invoke({"messages": [HumanMessage("go")]})

    assert model.calls[0][0].content == f"Focus on {POD_TOKEN}."


def test_a_pydantic_structured_response_is_unmasked() -> None:
    model = ScriptedChatModel(
        responses=[tool_call_message("Finding", {"summary": f"{POD_TOKEN} leaks"}, "s1")]
    )
    agent = _masked_agent(model, response_format=ToolStrategy(Finding))

    result = agent.invoke({"messages": [HumanMessage(f"look at {POD}")]})

    assert result["structured_response"] == Finding(summary=f"{POD} leaks")


def test_a_dataclass_structured_response_is_unmasked() -> None:
    model = ScriptedChatModel(
        responses=[tool_call_message("DataclassFinding", {"summary": f"{POD_TOKEN} leaks"}, "s1")]
    )
    agent = _masked_agent(model, response_format=ToolStrategy(DataclassFinding))

    result = agent.invoke({"messages": [HumanMessage(f"look at {POD}")]})

    assert result["structured_response"] == DataclassFinding(summary=f"{POD} leaks")


def test_a_disabled_policy_sends_real_values() -> None:
    model = ScriptedChatModel(responses=[AIMessage("ok")])
    agent = make_agent(
        name="k8s", model=model, middleware=[MaskingMiddleware(MaskingPolicy(enabled=False))]
    )

    agent.invoke({"messages": [HumanMessage(f"look at {POD}")]})

    assert model.calls[0][-1].content == f"look at {POD}"


async def test_masking_also_applies_to_async_runs() -> None:
    received: list[str] = []
    model = ScriptedChatModel(
        responses=[tool_call_message("pod_logs", {"pod": POD_TOKEN}, "c1"), AIMessage("done")]
    )
    agent = _masked_agent(model, tools=[_pod_logs(received)])

    await agent.ainvoke({"messages": [HumanMessage(f"why does {POD} restart?")]})

    assert received == [POD]
    assert POD not in str(model.calls[0][-1].content)
```

- [ ] **Step 2: Verificar que fallan**

Run: `uv run pytest tests/safety/test_masking.py tests/agents/middleware/test_masking.py -v`

Expected: `2 errors` durante la colección, con `ModuleNotFoundError: No module named 'sre_graph.safety.masking'` y `No module named 'sre_graph.agents.middleware.masking'`.

- [ ] **Step 3: Implementación**

`src/sre_graph/safety/masking.py`:

```python
"""Reversible masking of infrastructure identifiers before text reaches an external model."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from sre_graph.config.settings import Settings


class IdentifierKind(StrEnum):
    """Identifier families the built-in detectors recognise."""

    POD = "pod"
    NAMESPACE = "namespace"
    CLUSTER = "cluster"
    HOSTNAME = "hostname"
    ACCOUNT_ID = "account_id"
    IP_ADDRESS = "ip_address"
    EMAIL = "email"
    SERVICE_NAME = "service_name"


#: Contextual detectors capture only the value (group 1), not the "namespace:" label.
_DETECTORS: dict[IdentifierKind, re.Pattern[str]] = {
    IdentifierKind.POD: re.compile(
        r"\b([a-z0-9](?:[-a-z0-9]*[a-z0-9])?-[a-f0-9]{5,10}(?:-[a-z0-9]{3,10})?)\b"
    ),
    IdentifierKind.NAMESPACE: re.compile(
        r"\b(?:kube_namespace|namespace|ns)[=:\s]+([a-z0-9][-a-z0-9]*)\b", re.IGNORECASE
    ),
    IdentifierKind.CLUSTER: re.compile(
        r"\b(?:kube_cluster|eks_cluster|cluster(?:_name)?)[=:\s]+([a-zA-Z0-9][-a-zA-Z0-9_]{1,})\b",
        re.IGNORECASE,
    ),
    IdentifierKind.SERVICE_NAME: re.compile(
        r"\b(?:service|service_name|app|deployment)[=:\s]+([a-zA-Z0-9][-a-zA-Z0-9_]{1,})\b",
        re.IGNORECASE,
    ),
    IdentifierKind.HOSTNAME: re.compile(
        r"\b(kind-[a-z0-9][-a-z0-9]*"
        r"|ip-\d+-\d+-\d+-\d+(?:\.[a-z0-9][a-z0-9-]*)*"
        r"|[a-z0-9][-a-z0-9]*(?:\.[a-z0-9][-a-z0-9]*)+\.(?:com|net|org|io|internal|local|cloud))\b",
        re.IGNORECASE,
    ),
    IdentifierKind.ACCOUNT_ID: re.compile(r"\b(\d{12})\b"),
    IdentifierKind.IP_ADDRESS: re.compile(
        r"\b((?:25[0-5]|2[0-4]\d|[01]?\d?\d)(?:\.(?:25[0-5]|2[0-4]\d|[01]?\d?\d)){3})\b"
    ),
    IdentifierKind.EMAIL: re.compile(r"\b([\w.+-]+@[\w-]+\.[\w.-]+)\b"),
}
_TOKEN_RE = re.compile(r"<[A-Z0-9_]+_[0-9a-f]{10}>")


@dataclass(frozen=True)
class MaskingPolicy:
    """Whether to mask, which identifier kinds, and any extra labelled patterns."""

    enabled: bool = False
    kinds: frozenset[IdentifierKind] = frozenset(IdentifierKind)
    extra_patterns: Mapping[str, re.Pattern[str]] = field(default_factory=dict)

    @classmethod
    def from_settings(cls, settings: Settings) -> MaskingPolicy:
        """Build from ``MASK_ENABLED``, ``MASK_KINDS`` (empty = all) and ``MASK_EXTRA_REGEX``."""
        kinds = frozenset(IdentifierKind(kind) for kind in settings.mask_kinds)
        return cls(
            enabled=settings.mask_enabled,
            kinds=kinds or frozenset(IdentifierKind),
            extra_patterns={
                label: re.compile(pattern) for label, pattern in settings.mask_extra_regex.items()
            },
        )


@dataclass(frozen=True)
class _Match:
    label: str
    start: int
    end: int
    value: str


class Masker:
    """Mask identifiers with stable tokens and restore exactly the tokens it issued.

    A token is the identifier kind plus a short hash of the value, so a value
    gets the same token in every call and nothing is stored between calls. Use
    one ``Masker`` per model call: mask the request, then unmask the reply.
    """

    def __init__(self, policy: MaskingPolicy) -> None:
        self._policy = policy
        self._originals: dict[str, str] = {}

    def mask(self, text: str) -> str:
        """Replace every detected identifier in ``text`` with its token."""
        if not self._policy.enabled or not text:
            return text
        parts: list[str] = []
        cursor = 0
        for match in self._find(text):
            parts.append(text[cursor : match.start])
            parts.append(self._token(match.label, match.value))
            cursor = match.end
        parts.append(text[cursor:])
        return "".join(parts)

    def unmask(self, text: str) -> str:
        """Restore the tokens this masker issued; any other ``<…>`` text is left as is."""
        if not self._originals or "<" not in text:
            return text
        return _TOKEN_RE.sub(
            lambda match: self._originals.get(match.group(0), match.group(0)), text
        )

    def mask_value(self, value: Any) -> Any:
        """Mask every string inside nested dicts, lists and tuples."""
        return _map_strings(value, self.mask)

    def unmask_value(self, value: Any) -> Any:
        """Unmask every string inside nested dicts, lists and tuples."""
        return _map_strings(value, self.unmask)

    def _token(self, label: str, value: str) -> str:
        digest = hashlib.sha256(f"{label}:{value}".encode()).hexdigest()[:10]
        token = f"<{re.sub(r'[^A-Z0-9_]', '_', label.upper())}_{digest}>"
        self._originals[token] = value
        return token

    def _find(self, text: str) -> list[_Match]:
        found: list[_Match] = []
        for kind in IdentifierKind:
            if kind in self._policy.kinds:
                _collect(_DETECTORS[kind], kind.value, text, found)
        for label, pattern in self._policy.extra_patterns.items():
            _collect(pattern, label, text, found)
        return _without_overlaps(found)


def _collect(pattern: re.Pattern[str], label: str, text: str, out: list[_Match]) -> None:
    for match in pattern.finditer(text):
        group = 1 if match.groups() else 0
        value = match.group(group)
        if value:
            start, end = match.span(group)
            out.append(_Match(label=label, start=start, end=end, value=value))


def _without_overlaps(matches: list[_Match]) -> list[_Match]:
    """Keep the longest match at each position and drop any match that overlaps a kept one."""
    kept: list[_Match] = []
    last_end = -1
    for match in sorted(matches, key=lambda m: (m.start, -(m.end - m.start), m.label)):
        if match.start >= last_end:
            kept.append(match)
            last_end = match.end
    return kept


def _map_strings(value: Any, transform: Callable[[str], str]) -> Any:
    if isinstance(value, str):
        return transform(value)
    if isinstance(value, dict):
        return {key: _map_strings(item, transform) for key, item in value.items()}
    if isinstance(value, list):
        return [_map_strings(item, transform) for item in value]
    if isinstance(value, tuple):
        return tuple(_map_strings(item, transform) for item in value)
    return value
```

`src/sre_graph/agents/middleware/masking.py`:

```python
"""Hide infrastructure identifiers from the model and restore them in everything it returns."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import fields, is_dataclass, replace
from typing import Any

from langchain.agents.middleware import (
    AgentMiddleware,
    ExtendedModelResponse,
    ModelRequest,
    ModelResponse,
)
from langchain_core.messages import AIMessage, BaseMessage
from pydantic import BaseModel

from sre_graph.safety.masking import Masker, MaskingPolicy

ModelCallResult = ModelResponse[Any] | AIMessage | ExtendedModelResponse[Any]


class MaskingMiddleware(AgentMiddleware[Any, Any]):
    """Mask identifiers in each model request; unmask the reply, its tool calls and its output.

    Agent state always holds real values: only what is sent to the model is
    masked, and the reply is unmasked before it is stored or any tool runs, so
    tools receive real identifiers. Disabled policies pass calls through.
    """

    def __init__(self, policy: MaskingPolicy) -> None:
        super().__init__()
        self._policy = policy

    def wrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], ModelCallResult],
    ) -> ModelCallResult:
        if not self._policy.enabled:
            return handler(request)
        masker = Masker(self._policy)
        return _unmask_result(handler(_mask_request(request, masker)), masker)

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelCallResult]],
    ) -> ModelCallResult:
        if not self._policy.enabled:
            return await handler(request)
        masker = Masker(self._policy)
        return _unmask_result(await handler(_mask_request(request, masker)), masker)


def _mask_request(request: ModelRequest[Any], masker: Masker) -> ModelRequest[Any]:
    messages = [_map_message(message, masker.mask_value) for message in request.messages]
    if request.system_message is None:
        return request.override(messages=messages)
    system_message = _map_message(request.system_message, masker.mask_value)
    return request.override(messages=messages, system_message=system_message)


def _unmask_result(result: ModelCallResult, masker: Masker) -> ModelCallResult:
    if isinstance(result, AIMessage):
        return _map_message(result, masker.unmask_value)
    if isinstance(result, ExtendedModelResponse):
        return replace(result, model_response=_unmask_response(result.model_response, masker))
    return _unmask_response(result, masker)


def _unmask_response(response: ModelResponse[Any], masker: Masker) -> ModelResponse[Any]:
    return ModelResponse(
        result=[_map_message(message, masker.unmask_value) for message in response.result],
        structured_response=_unmask_structured(response.structured_response, masker),
    )


def _map_message[MessageT: BaseMessage](
    message: MessageT, transform: Callable[[Any], Any]
) -> MessageT:
    update: dict[str, Any] = {"content": transform(message.content)}
    if isinstance(message, AIMessage) and message.tool_calls:
        update["tool_calls"] = [
            {**call, "args": transform(call["args"])} for call in message.tool_calls
        ]
    return message.model_copy(update=update)


def _unmask_structured(value: Any, masker: Masker) -> Any:
    if isinstance(value, BaseModel):
        return type(value).model_validate(masker.unmask_value(value.model_dump()))
    if is_dataclass(value) and not isinstance(value, type):
        changes = {
            item.name: _unmask_structured(getattr(value, item.name), masker)
            for item in fields(value)
        }
        return replace(value, **changes)
    return masker.unmask_value(value)
```

- [ ] **Step 4: Verificar que pasan**

Run: `uv run pytest tests/safety/test_masking.py tests/agents/middleware/test_masking.py -v`

Expected: `19 passed`.

- [ ] **Step 5: Gate**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run lint-imports && SRE_GRAPH_TEST_DATABASE_URL="postgresql://sre_graph:sre_graph@localhost:5433/sre_graph?connect_timeout=5" SRE_GRAPH_REQUIRE_POSTGRES=1 uv run pytest
```

Expected: todo en verde y `115 passed`.

- [ ] **Step 6: Commit**

```bash
git add src/sre_graph/safety/masking.py src/sre_graph/agents/middleware/masking.py tests/safety/test_masking.py tests/agents/middleware/test_masking.py
git commit -m "feat(safety): reversible identifier masking around every model call" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Política de aprobación y middleware HITL

La política decide qué llamadas esperan a un humano:
- `read_only` nunca espera;
- en `prod`, todo lo que cambia estado espera;
- fuera de `prod`, `AUTO_APPROVE_TOOLS` permite saltar la espera;
- una tool sin metadatos se trata como capaz de cambiar estado.

`approval_middleware` traduce la política al `HumanInTheLoopMiddleware` de LangChain. Así el formato de aprobación (`action_requests` → `decisions` approve/edit/reject) es el mismo para el chat, la remediación, la UI, el CLI y los bots.

**Files:**
- Create: `src/sre_graph/safety/approval.py`, `src/sre_graph/agents/middleware/approval.py`
- Test: `tests/safety/test_approval.py`, `tests/agents/middleware/test_approval.py`

**Interfaces:**
- Consumes: `ToolMeta`, `SideEffectLevel` y `read_tool_meta` (Tarea 3); `Settings.environment` y `Settings.auto_approve_tools` (Tarea 1).
- Produces:
  - `PRODUCTION = "prod"`.
  - `@dataclass(frozen=True) class ApprovalPolicy(environment: str = "dev", auto_approve: frozenset[str] = frozenset())`, con `from_settings(settings)` y `requires_approval(tool_name: str, meta: ToolMeta | None) -> bool`.
  - `approval_middleware(policy: ApprovalPolicy, tools: Iterable[BaseTool]) -> HumanInTheLoopMiddleware`:
    - interrumpe exactamente las tools que la política marca;
    - permite las decisiones `["approve", "edit", "reject"]`;
    - describe cada acción como `"<tool> (<nivel>) needs approval before it runs"`.

- [ ] **Step 1: Tests que fallan**

Crea `tests/safety/test_approval.py`:

```python
from __future__ import annotations

import pytest

from sre_graph.config.settings import Settings
from sre_graph.domain.tools import Domain, EvidenceType, SideEffectLevel, ToolMeta
from sre_graph.safety.approval import ApprovalPolicy


def _meta(level: SideEffectLevel) -> ToolMeta:
    return ToolMeta(
        integration="kubernetes",
        domains=frozenset({Domain.KUBERNETES}),
        evidence_type=EvidenceType.TOPOLOGY,
        side_effect_level=level,
    )


@pytest.mark.parametrize("environment", ["dev", "prod"])
def test_read_only_calls_never_wait(environment: str) -> None:
    policy = ApprovalPolicy(environment=environment)

    assert policy.requires_approval("k8s_list_pods", _meta(SideEffectLevel.READ_ONLY)) is False


@pytest.mark.parametrize("level", [SideEffectLevel.MUTATING, SideEffectLevel.EXTERNAL])
def test_state_changing_calls_wait_by_default(level: SideEffectLevel) -> None:
    assert ApprovalPolicy().requires_approval("k8s_rollout_undo", _meta(level)) is True


def test_auto_approved_tools_skip_the_wait_outside_production() -> None:
    policy = ApprovalPolicy(environment="staging", auto_approve=frozenset({"k8s_rollout_undo"}))

    assert policy.requires_approval("k8s_rollout_undo", _meta(SideEffectLevel.MUTATING)) is False
    assert policy.requires_approval("k8s_scale", _meta(SideEffectLevel.MUTATING)) is True


def test_production_ignores_the_auto_approve_list() -> None:
    policy = ApprovalPolicy(environment="prod", auto_approve=frozenset({"k8s_rollout_undo"}))

    assert policy.requires_approval("k8s_rollout_undo", _meta(SideEffectLevel.MUTATING)) is True


def test_a_tool_without_metadata_is_treated_as_changing_state() -> None:
    assert ApprovalPolicy().requires_approval("mystery", None) is True


def test_policy_from_settings() -> None:
    settings = Settings(_env_file=None, environment="Prod", auto_approve_tools=("a", "b"))

    policy = ApprovalPolicy.from_settings(settings)

    assert policy == ApprovalPolicy(environment="prod", auto_approve=frozenset({"a", "b"}))
```

Crea `tests/agents/middleware/test_approval.py`:

```python
from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import BaseTool, tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command

from sre_graph.agents.factory import make_agent
from sre_graph.agents.middleware.approval import approval_middleware
from sre_graph.agents.testing.scripted_model import ScriptedChatModel, tool_call_message
from sre_graph.domain.tools import Domain, EvidenceType, SideEffectLevel, ToolMeta, attach_tool_meta
from sre_graph.safety.approval import ApprovalPolicy

CONFIG: Any = {"configurable": {"thread_id": "t1"}}
UNDO_API = tool_call_message("k8s_rollout_undo", {"deployment": "api"}, "c1")


def _meta(level: SideEffectLevel) -> ToolMeta:
    return ToolMeta(
        integration="kubernetes",
        domains=frozenset({Domain.KUBERNETES}),
        evidence_type=EvidenceType.TOPOLOGY,
        side_effect_level=level,
    )


def _tools(executed: list[str]) -> list[BaseTool]:
    @tool
    def k8s_list_pods(namespace: str) -> str:
        """List pods."""
        executed.append(f"list {namespace}")
        return "3 pods"

    @tool
    def k8s_rollout_undo(deployment: str) -> str:
        """Roll a deployment back to its previous revision."""
        executed.append(f"undo {deployment}")
        return "rolled back"

    return [
        attach_tool_meta(k8s_list_pods, _meta(SideEffectLevel.READ_ONLY)),
        attach_tool_meta(k8s_rollout_undo, _meta(SideEffectLevel.MUTATING)),
    ]


def _agent(
    policy: ApprovalPolicy, executed: list[str], call: AIMessage
) -> CompiledStateGraph[Any, Any, Any, Any]:
    tools = _tools(executed)
    return make_agent(
        name="operator",
        model=ScriptedChatModel(responses=[call, AIMessage("done")]),
        tools=tools,
        middleware=[approval_middleware(policy, tools)],
        checkpointer=InMemorySaver(),
    )


def test_a_mutating_call_pauses_with_the_standard_payload() -> None:
    executed: list[str] = []
    agent = _agent(ApprovalPolicy(), executed, UNDO_API)

    paused = agent.invoke({"messages": [HumanMessage("roll back api")]}, CONFIG)

    request = paused["__interrupt__"][0].value
    assert executed == []
    assert request["action_requests"][0]["name"] == "k8s_rollout_undo"
    assert request["action_requests"][0]["args"] == {"deployment": "api"}
    assert request["review_configs"][0]["allowed_decisions"] == ["approve", "edit", "reject"]


def test_an_approved_call_runs_after_resume() -> None:
    executed: list[str] = []
    agent = _agent(ApprovalPolicy(), executed, UNDO_API)
    agent.invoke({"messages": [HumanMessage("roll back api")]}, CONFIG)

    agent.invoke(Command(resume={"decisions": [{"type": "approve"}]}), CONFIG)

    assert executed == ["undo api"]


def test_a_rejected_call_never_runs_and_the_model_is_told_why() -> None:
    executed: list[str] = []
    agent = _agent(ApprovalPolicy(), executed, UNDO_API)
    agent.invoke({"messages": [HumanMessage("roll back api")]}, CONFIG)

    result = agent.invoke(
        Command(resume={"decisions": [{"type": "reject", "message": "not during peak"}]}), CONFIG
    )

    assert executed == []
    rejection = next(m for m in result["messages"] if isinstance(m, ToolMessage))
    assert "not during peak" in str(rejection.content)


def test_read_only_calls_run_without_pausing_even_in_production() -> None:
    executed: list[str] = []
    list_pods = tool_call_message("k8s_list_pods", {"namespace": "payments"}, "c1")
    agent = _agent(ApprovalPolicy(environment="prod"), executed, list_pods)

    result = agent.invoke({"messages": [HumanMessage("list pods")]}, CONFIG)

    assert "__interrupt__" not in result
    assert executed == ["list payments"]


def test_an_auto_approved_call_runs_without_pausing() -> None:
    executed: list[str] = []
    policy = ApprovalPolicy(environment="dev", auto_approve=frozenset({"k8s_rollout_undo"}))
    agent = _agent(policy, executed, UNDO_API)

    result = agent.invoke({"messages": [HumanMessage("roll back api")]}, CONFIG)

    assert "__interrupt__" not in result
    assert executed == ["undo api"]
```

- [ ] **Step 2: Verificar que fallan**

Run: `uv run pytest tests/safety/test_approval.py tests/agents/middleware/test_approval.py -v`

Expected: `2 errors` durante la colección, con `ModuleNotFoundError: No module named 'sre_graph.safety.approval'` y `No module named 'sre_graph.agents.middleware.approval'`.

- [ ] **Step 3: Implementación**

`src/sre_graph/safety/approval.py`:

```python
"""Which tool calls need a human decision before they run."""

from __future__ import annotations

from dataclasses import dataclass

from sre_graph.config.settings import Settings
from sre_graph.domain.tools import SideEffectLevel, ToolMeta

PRODUCTION = "prod"


@dataclass(frozen=True)
class ApprovalPolicy:
    """Read-only calls run freely; everything else waits for a human.

    Outside production, tools named in ``auto_approve`` skip the wait. In
    production nothing that changes state is ever auto-approved. A tool that
    declares no metadata is treated as able to change state.
    """

    environment: str = "dev"
    auto_approve: frozenset[str] = frozenset()

    @classmethod
    def from_settings(cls, settings: Settings) -> ApprovalPolicy:
        """Build from ``ENVIRONMENT`` and ``AUTO_APPROVE_TOOLS``."""
        return cls(
            environment=settings.environment, auto_approve=frozenset(settings.auto_approve_tools)
        )

    def requires_approval(self, tool_name: str, meta: ToolMeta | None) -> bool:
        """Whether a call to ``tool_name`` must pause for a human decision."""
        if meta is not None and meta.side_effect_level is SideEffectLevel.READ_ONLY:
            return False
        if self.environment == PRODUCTION:
            return True
        return tool_name not in self.auto_approve
```

`src/sre_graph/agents/middleware/approval.py`:

```python
"""Pause tool calls that need a human decision, using LangChain's human-in-the-loop middleware."""

from __future__ import annotations

from collections.abc import Iterable

from langchain.agents.middleware import HumanInTheLoopMiddleware, InterruptOnConfig
from langchain_core.tools import BaseTool

from sre_graph.domain.tools import read_tool_meta
from sre_graph.safety.approval import ApprovalPolicy


def approval_middleware(
    policy: ApprovalPolicy, tools: Iterable[BaseTool]
) -> HumanInTheLoopMiddleware:
    """A HITL middleware that interrupts exactly the ``tools`` the policy says need approval.

    The interrupt carries LangChain's standard ``action_requests`` /
    ``review_configs`` payload; resume with ``{"decisions": [{"type": ...}]}``.
    """
    interrupt_on: dict[str, bool | InterruptOnConfig] = {}
    for tool in tools:
        meta = read_tool_meta(tool)
        if policy.requires_approval(tool.name, meta):
            level = meta.side_effect_level.value if meta is not None else "undeclared"
            interrupt_on[tool.name] = InterruptOnConfig(
                allowed_decisions=["approve", "edit", "reject"],
                description=f"{tool.name} ({level}) needs approval before it runs",
            )
    return HumanInTheLoopMiddleware(interrupt_on=interrupt_on)
```

- [ ] **Step 4: Verificar que pasan**

Run: `uv run pytest tests/safety/test_approval.py tests/agents/middleware/test_approval.py -v`

Expected: `13 passed`.

- [ ] **Step 5: Gate**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run lint-imports && SRE_GRAPH_TEST_DATABASE_URL="postgresql://sre_graph:sre_graph@localhost:5433/sre_graph?connect_timeout=5" SRE_GRAPH_REQUIRE_POSTGRES=1 uv run pytest
```

Expected: todo en verde y `128 passed`.

- [ ] **Step 6: Commit**

```bash
git add src/sre_graph/safety/approval.py src/sre_graph/agents/middleware/approval.py tests/safety/test_approval.py tests/agents/middleware/test_approval.py
git commit -m "feat(safety): approval policy by side-effect level and environment, wired to LangChain HITL" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Marco de integraciones

El contrato que siguen todas las integraciones de los planes 1a–1d:
- **`Integration`:** settings, cliente por corrida, tools y `verify`.
- **`RunContext`:** clientes de la corrida y motivo de los que faltan.
- **`require_client`:** cómo una tool obtiene su cliente.
- **`integration_tool`:** decorador que produce tools async con metadatos, salida `(content, artifact)` y errores legibles.
- **`tool_payload` / `text_payload`:** recortan para el modelo sin romper el JSON.
- **`IntegrationRegistry`:** registro explícito, que filtra por integraciones configuradas y por dominio.

**Files:**
- Create: `src/sre_graph/integrations/__init__.py`, `base.py`, `context.py`, `payload.py`, `tooling.py`, `registry.py` (todos bajo `src/sre_graph/integrations/`)
- Modify: `pyproject.toml`
- Test: `tests/integrations/test_payload.py`, `test_context.py`, `test_tooling.py`, `test_registry.py` (bajo `tests/integrations/`)

**Interfaces:**
- Consumes:
  - `ToolMeta`, `Domain`, `attach_tool_meta` y `read_tool_meta` (Tarea 3);
  - `redact_secrets` (Tarea 4);
  - `make_agent`, `ScriptedChatModel` y `tool_call_message`.
- Produces:
  - **Errores:**
    - `class IntegrationError(ToolException)`: su mensaje lo lee el modelo;
    - `class IntegrationNotConfiguredError(IntegrationError)`.
  - **Verificación:**
    - `class VerifyStatus(StrEnum)`: `ok`, `failed`, `not_configured`;
    - `@dataclass(frozen=True) class VerifyResult(integration: str, status: VerifyStatus, detail: str)`.
  - **`class Integration[SettingsT, ClientT](ABC)`**, con:
    - `ClassVar` `id: str` y `display_name: str`;
    - `load_settings(env_file: str | None) -> SettingsT | None`;
    - `build_client(settings) -> ClientT`;
    - `tools() -> list[BaseTool]`;
    - `async verify(client) -> VerifyResult`.
  - **Contexto:**
    - `@dataclass(frozen=True) class RunContext(clients: Mapping[str, object] = {}, unavailable: Mapping[str, str] = {})`, con `client(integration, expected_type) -> ClientT`. Si falta el cliente lanza `IntegrationNotConfiguredError`; si el tipo es incorrecto, `TypeError`.
    - `require_client(runtime: ToolRuntime[Any, Any], integration: str, expected_type: type[ClientT]) -> ClientT`.
  - **Recorte de salida:**
    - `tool_payload(data, *, max_items=50, max_chars=8000) -> str` y `text_payload(text, *, max_chars=8000) -> str`;
    - constantes `MAX_ITEMS` y `MAX_CHARS`.
  - **`integration_tool(meta: ToolMeta) -> Callable[[ToolFunction], BaseTool]`.**
  - **`IntegrationRegistry(integrations, *, env_file: str | None = ".env")`**, con:
    - `ids`;
    - `run_context() -> RunContext`;
    - `tools(context, domains: Collection[Domain] | None = None) -> list[BaseTool]`;
    - `async verify_all(context, *, timeout_seconds: float = 15.0) -> list[VerifyResult]`.

- [ ] **Step 1: Tests que fallan**

Crea `tests/integrations/test_payload.py`:

```python
from __future__ import annotations

import json
from datetime import UTC, datetime

from sre_graph.integrations.payload import text_payload, tool_payload


def test_small_data_becomes_compact_json() -> None:
    assert tool_payload({"pods": ["a", "b"], "ok": True}) == '{"pods":["a","b"],"ok":true}'


def test_long_lists_keep_their_head_and_count_the_rest() -> None:
    payload = json.loads(tool_payload({"pods": list(range(120))}, max_items=50))

    assert payload["pods"][:50] == list(range(50))
    assert payload["pods"][50] == "…[70 more items]"
    assert len(payload["pods"]) == 51


def test_nested_lists_are_capped_too() -> None:
    payload = json.loads(tool_payload([{"events": list(range(10))}], max_items=3))

    assert payload == [{"events": [0, 1, 2, "…[7 more items]"]}]


def test_a_list_too_long_for_the_limit_keeps_whole_entries_and_valid_json() -> None:
    payload = tool_payload([{"line": "x" * 90}] * 40, max_chars=1000)

    entries = json.loads(payload)
    kept = len(entries) - 1
    assert 0 < kept < 40
    assert entries[-1] == f"…[{40 - kept} more items]"
    assert len(payload) <= 1000


def test_other_data_over_the_limit_is_cut_with_a_marker() -> None:
    payload = tool_payload({"blob": "x" * 5000}, max_chars=1000)

    assert payload.startswith('{"blob":"xxxx')
    assert payload.endswith("…[truncated 4011 more characters]")


def test_values_json_cannot_encode_are_written_as_text() -> None:
    moment = datetime(2026, 10, 7, 10, 0, tzinfo=UTC)

    assert tool_payload({"at": moment}) == '{"at":"2026-10-07 10:00:00+00:00"}'


def test_short_text_is_returned_unchanged() -> None:
    assert text_payload("line 1\nline 2") == "line 1\nline 2"


def test_long_text_keeps_the_most_recent_whole_lines() -> None:
    text = "\n".join(f"line {number:04d}" for number in range(2000))

    payload = text_payload(text, max_chars=200)

    first, *rest = payload.splitlines()
    assert first.startswith("[…") and first.endswith("earlier characters omitted]")
    assert rest[-1] == "line 1999"
    assert all(line.startswith("line ") and len(line) == 9 for line in rest)
```

Crea `tests/integrations/test_context.py`:

```python
from __future__ import annotations

import pytest

from sre_graph.integrations.base import IntegrationNotConfiguredError
from sre_graph.integrations.context import RunContext


class PagerClient:
    pass


def test_a_registered_client_is_returned_with_its_type() -> None:
    pager = PagerClient()

    assert RunContext(clients={"pager": pager}).client("pager", PagerClient) is pager


def test_a_missing_client_explains_it_is_not_configured() -> None:
    with pytest.raises(
        IntegrationNotConfiguredError, match=r"pager is unavailable.*not configured"
    ):
        RunContext().client("pager", PagerClient)


def test_a_client_that_failed_to_build_explains_why() -> None:
    context = RunContext(unavailable={"pager": "ConfigException: no kubeconfig"})

    with pytest.raises(IntegrationNotConfiguredError, match="no kubeconfig"):
        context.client("pager", PagerClient)


def test_a_client_of_the_wrong_type_is_a_programming_error() -> None:
    with pytest.raises(TypeError, match="expected PagerClient"):
        RunContext(clients={"pager": object()}).client("pager", PagerClient)
```

Crea `tests/integrations/test_tooling.py`:

```python
from __future__ import annotations

from typing import Any

from langchain.tools import ToolRuntime
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.graph.state import CompiledStateGraph

from sre_graph.agents.factory import make_agent
from sre_graph.agents.testing.scripted_model import ScriptedChatModel, tool_call_message
from sre_graph.domain.tools import Domain, EvidenceType, ToolMeta, read_tool_meta
from sre_graph.integrations.base import IntegrationError
from sre_graph.integrations.context import RunContext, require_client
from sre_graph.integrations.tooling import integration_tool

META = ToolMeta(
    integration="pager",
    domains=frozenset({Domain.CHANGES}),
    evidence_type=EvidenceType.CHANGES,
)


class PagerClient:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail

    async def incidents(self, service: str) -> list[dict[str, Any]]:
        if self.fail:
            raise IntegrationError("pager API error 503: try again later")
        return [{"id": "P1", "service": service}]


@integration_tool(META)
async def pager_incidents(service: str, runtime: ToolRuntime[RunContext]) -> tuple[str, Any]:
    """List open incidents for a service.

    Args:
        service: Service name as the pager knows it.
    """
    incidents = await require_client(runtime, "pager", PagerClient).incidents(service)
    return f"{len(incidents)} open incident(s)", incidents


def _agent(model: ScriptedChatModel) -> CompiledStateGraph[Any, Any, Any, Any]:
    return make_agent(name="pager", model=model, tools=[pager_incidents], context_schema=RunContext)


def _script() -> ScriptedChatModel:
    return ScriptedChatModel(
        responses=[tool_call_message("pager_incidents", {"service": "api"}, "c1"), AIMessage("ok")]
    )


def test_the_docstring_describes_the_tool_and_hides_the_runtime() -> None:
    schema = pager_incidents.tool_call_schema.model_json_schema()

    assert pager_incidents.description == "List open incidents for a service."
    assert list(schema["properties"]) == ["service"]
    assert schema["properties"]["service"]["description"] == "Service name as the pager knows it."


def test_the_tool_carries_its_metadata() -> None:
    assert read_tool_meta(pager_incidents) == META


async def test_content_goes_to_the_model_and_data_to_the_artifact() -> None:
    model = _script()

    result = await _agent(model).ainvoke(
        {"messages": [HumanMessage("incidents?")]},
        context=RunContext(clients={"pager": PagerClient()}),
    )

    message = next(m for m in result["messages"] if isinstance(m, ToolMessage))
    assert message.content == "1 open incident(s)"
    assert message.artifact == [{"id": "P1", "service": "api"}]


async def test_an_integration_error_reaches_the_model_and_the_run_continues() -> None:
    model = _script()

    result = await _agent(model).ainvoke(
        {"messages": [HumanMessage("incidents?")]},
        context=RunContext(clients={"pager": PagerClient(fail=True)}),
    )

    message = next(m for m in result["messages"] if isinstance(m, ToolMessage))
    assert message.status == "error"
    assert message.content == "pager API error 503: try again later"
    assert len(model.calls) == 2


async def test_a_run_without_integration_clients_gets_a_readable_error() -> None:
    result = await _agent(_script()).ainvoke({"messages": [HumanMessage("incidents?")]})

    message = next(m for m in result["messages"] if isinstance(m, ToolMessage))
    assert message.status == "error"
    assert "started without integration clients" in str(message.content)
```

Crea `tests/integrations/test_registry.py`:

```python
from __future__ import annotations

import asyncio
from typing import Any

import pytest
from langchain_core.tools import BaseTool, tool

from sre_graph.domain.tools import Domain, EvidenceType, ToolMeta
from sre_graph.integrations.base import Integration, VerifyResult, VerifyStatus
from sre_graph.integrations.context import RunContext
from sre_graph.integrations.registry import IntegrationRegistry
from sre_graph.integrations.tooling import integration_tool

# Built at runtime so this file holds no string that looks like a real credential.
GITHUB_TOKEN = "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"


def _meta(integration: str, *domains: Domain) -> ToolMeta:
    return ToolMeta(
        integration=integration, domains=frozenset(domains), evidence_type=EvidenceType.LOGS
    )


@integration_tool(_meta("logs", Domain.LOGS))
async def logs_query(query: str) -> tuple[str, Any]:
    """Run a log query.

    Args:
        query: The query text.
    """
    return query, None


@integration_tool(_meta("metrics", Domain.METRICS))
async def metrics_query(query: str) -> tuple[str, Any]:
    """Run a metrics query.

    Args:
        query: The query text.
    """
    return query, None


class FakeClient:
    pass


class FakeIntegration(Integration[str, FakeClient]):
    """Configured unless told otherwise; ``verify`` does what the test asks."""

    def __init__(
        self,
        *,
        configured: bool = True,
        build_error: Exception | None = None,
        tools: list[BaseTool] | None = None,
        verify_error: Exception | None = None,
        verify_delay: float = 0.0,
    ) -> None:
        self._configured = configured
        self._build_error = build_error
        self._tools = tools or []
        self._verify_error = verify_error
        self._verify_delay = verify_delay

    def load_settings(self, env_file: str | None) -> str | None:
        return "settings" if self._configured else None

    def build_client(self, settings: str) -> FakeClient:
        if self._build_error is not None:
            raise self._build_error
        return FakeClient()

    def tools(self) -> list[BaseTool]:
        return self._tools

    async def verify(self, client: FakeClient) -> VerifyResult:
        await asyncio.sleep(self._verify_delay)
        if self._verify_error is not None:
            raise self._verify_error
        return VerifyResult(self.id, VerifyStatus.OK, "reachable")


class LogsIntegration(FakeIntegration):
    id = "logs"
    display_name = "Logs"


class MetricsIntegration(FakeIntegration):
    id = "metrics"
    display_name = "Metrics"


def test_two_integrations_with_one_id_are_rejected() -> None:
    with pytest.raises(ValueError, match="duplicate integration id: logs"):
        IntegrationRegistry([LogsIntegration(), LogsIntegration()], env_file=None)


def test_only_configured_integrations_get_a_client() -> None:
    registry = IntegrationRegistry(
        [LogsIntegration(), MetricsIntegration(configured=False)], env_file=None
    )

    context = registry.run_context()

    assert list(context.clients) == ["logs"]
    assert context.unavailable == {}


def test_an_integration_that_fails_to_build_is_unavailable_with_a_redacted_reason() -> None:
    broken = MetricsIntegration(build_error=RuntimeError(f"bad token {GITHUB_TOKEN}"))
    registry = IntegrationRegistry([LogsIntegration(), broken], env_file=None)

    context = registry.run_context()

    assert list(context.clients) == ["logs"]
    assert context.unavailable == {"metrics": "RuntimeError: bad token [REDACTED:github_token]"}


def test_tools_come_only_from_integrations_with_a_client() -> None:
    registry = IntegrationRegistry(
        [
            LogsIntegration(tools=[logs_query]),
            MetricsIntegration(configured=False, tools=[metrics_query]),
        ],
        env_file=None,
    )

    tools = registry.tools(registry.run_context())

    assert [candidate.name for candidate in tools] == ["logs_query"]


def test_tools_can_be_filtered_by_domain() -> None:
    registry = IntegrationRegistry(
        [LogsIntegration(tools=[logs_query]), MetricsIntegration(tools=[metrics_query])],
        env_file=None,
    )

    tools = registry.tools(registry.run_context(), domains={Domain.METRICS})

    assert [candidate.name for candidate in tools] == ["metrics_query"]


def test_a_tool_without_metadata_is_a_programming_error() -> None:
    @tool
    def bare(query: str) -> str:
        """No metadata."""
        return query

    registry = IntegrationRegistry([LogsIntegration(tools=[bare])], env_file=None)

    with pytest.raises(ValueError, match="bare carries no ToolMeta"):
        registry.tools(registry.run_context())


def test_two_tools_with_one_name_are_rejected() -> None:
    registry = IntegrationRegistry(
        [LogsIntegration(tools=[logs_query]), MetricsIntegration(tools=[logs_query])],
        env_file=None,
    )

    with pytest.raises(ValueError, match="duplicate tool name: logs_query"):
        registry.tools(registry.run_context())


async def test_verify_reports_every_integration_in_order() -> None:
    registry = IntegrationRegistry(
        [LogsIntegration(), MetricsIntegration(configured=False)], env_file=None
    )

    results = await registry.verify_all(registry.run_context())

    assert results == [
        VerifyResult("logs", VerifyStatus.OK, "reachable"),
        VerifyResult("metrics", VerifyStatus.NOT_CONFIGURED, "not configured"),
    ]


async def test_verify_reports_why_a_client_could_not_be_built() -> None:
    registry = IntegrationRegistry(
        [LogsIntegration(build_error=FileNotFoundError("no kubeconfig at /x"))], env_file=None
    )

    [result] = await registry.verify_all(registry.run_context())

    assert result == VerifyResult(
        "logs", VerifyStatus.FAILED, "FileNotFoundError: no kubeconfig at /x"
    )


async def test_a_failing_check_is_reported_not_raised() -> None:
    registry = IntegrationRegistry(
        [LogsIntegration(verify_error=ConnectionError(f"refused, token {GITHUB_TOKEN}"))],
        env_file=None,
    )

    [result] = await registry.verify_all(registry.run_context())

    assert result.status is VerifyStatus.FAILED
    assert result.detail == "refused, token [REDACTED:github_token]"


async def test_a_check_that_hangs_times_out() -> None:
    registry = IntegrationRegistry([LogsIntegration(verify_delay=5)], env_file=None)

    [result] = await registry.verify_all(registry.run_context(), timeout_seconds=0.05)

    assert result == VerifyResult("logs", VerifyStatus.FAILED, "no answer within 0.05 seconds")


def test_an_empty_context_offers_no_tools() -> None:
    registry = IntegrationRegistry([LogsIntegration(tools=[logs_query])], env_file=None)

    assert registry.tools(RunContext()) == []
```

- [ ] **Step 2: Verificar que fallan**

Run: `uv run pytest tests/integrations -v`

Expected: `4 errors` durante la colección, con `ModuleNotFoundError: No module named 'sre_graph.integrations'`.

- [ ] **Step 3: Implementación**

`src/sre_graph/integrations/__init__.py`:

```python
"""External systems: settings, per-run clients, tools and connectivity checks."""
```

`src/sre_graph/integrations/base.py`:

```python
"""What every integration provides: settings, a per-run client, tools and a connectivity check."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar

from langchain_core.tools import BaseTool, ToolException


class IntegrationError(ToolException):
    """A call to an external system failed.

    Tools built with ``integration_tool`` turn this into an error message the
    model reads, so the message must be factual and free of credentials.
    """


class IntegrationNotConfiguredError(IntegrationError):
    """The integration a tool needs is not configured, or could not be built for this run."""


class VerifyStatus(StrEnum):
    """Outcome of an integration's connectivity check."""

    OK = "ok"
    FAILED = "failed"
    NOT_CONFIGURED = "not_configured"


@dataclass(frozen=True)
class VerifyResult:
    """What a connectivity check found, in words an operator can act on."""

    integration: str
    status: VerifyStatus
    detail: str


class Integration[SettingsT, ClientT](ABC):
    """One external system. Instances hold no state; a client is built for every run."""

    id: ClassVar[str]
    display_name: ClassVar[str]

    @abstractmethod
    def load_settings(self, env_file: str | None) -> SettingsT | None:
        """Read settings from the environment and ``env_file``; None when not configured."""

    @abstractmethod
    def build_client(self, settings: SettingsT) -> ClientT:
        """A client for one run. Raises when the configuration cannot be used."""

    @abstractmethod
    def tools(self) -> list[BaseTool]:
        """Every tool this integration offers, each tagged with ``ToolMeta``."""

    @abstractmethod
    async def verify(self, client: ClientT) -> VerifyResult:
        """Prove the client can reach the system and read from it."""
```

`src/sre_graph/integrations/context.py`:

```python
"""The per-run context that carries integration clients to tools."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from langchain.tools import ToolRuntime

from sre_graph.integrations.base import IntegrationNotConfiguredError


@dataclass(frozen=True)
class RunContext:
    """Clients for the integrations available in this run, and why the others are not.

    Pass it as the graph's runtime context. LangGraph never persists the
    context, so credentials stay out of checkpoints; build a fresh one for
    every invoke and every resume.
    """

    clients: Mapping[str, object] = field(default_factory=dict)
    unavailable: Mapping[str, str] = field(default_factory=dict)

    def client[ClientT](self, integration: str, expected_type: type[ClientT]) -> ClientT:
        """The client for ``integration``; an error the model can read when there is none."""
        found = self.clients.get(integration)
        if found is None:
            reason = self.unavailable.get(integration, "it is not configured")
            raise IntegrationNotConfiguredError(
                f"{integration} is unavailable in this run: {reason}"
            )
        if not isinstance(found, expected_type):
            raise TypeError(
                f"{integration} client is {type(found).__name__}, expected {expected_type.__name__}"
            )
        return found


def require_client[ClientT](
    runtime: ToolRuntime[Any, Any], integration: str, expected_type: type[ClientT]
) -> ClientT:
    """The ``integration`` client from a tool's runtime context."""
    context = runtime.context
    if not isinstance(context, RunContext):
        raise IntegrationNotConfiguredError(
            f"{integration} is unavailable: this run was started without integration clients"
        )
    return context.client(integration, expected_type)
```

`src/sre_graph/integrations/payload.py`:

```python
"""Keep tool output small enough for the model, and say what was left out."""

from __future__ import annotations

import json
from typing import Any

MAX_ITEMS = 50
MAX_CHARS = 8000


def tool_payload(data: Any, *, max_items: int = MAX_ITEMS, max_chars: int = MAX_CHARS) -> str:
    """Compact JSON for the model, at most ``max_chars`` long.

    Every list keeps its first ``max_items`` entries plus a marker counting the
    rest. A top-level list that is still too long keeps as many whole entries
    as fit, so the JSON stays valid; anything else is cut at ``max_chars``
    with a marker. Put the most important entries first: the start survives.
    """
    text = _dumps(_cap_lists(data, max_items))
    if len(text) <= max_chars:
        return text
    if isinstance(data, list):
        fitted = _fit_list(data, max_items, max_chars)
        if fitted is not None:
            return fitted
    return f"{text[:max_chars]}…[truncated {len(text) - max_chars} more characters]"


def text_payload(text: str, *, max_chars: int = MAX_CHARS) -> str:
    """The end of ``text`` (for logs, the most recent lines), marked when the start was cut."""
    if len(text) <= max_chars:
        return text
    tail = text[-max_chars:]
    first_break = tail.find("\n")
    if 0 <= first_break < len(tail) - 1:
        tail = tail[first_break + 1 :]
    return f"[…{len(text) - len(tail)} earlier characters omitted]\n{tail}"


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _cap_lists(value: Any, max_items: int) -> Any:
    if isinstance(value, list):
        kept = [_cap_lists(item, max_items) for item in value[:max_items]]
        if len(value) > max_items:
            kept.append(f"…[{len(value) - max_items} more items]")
        return kept
    if isinstance(value, dict):
        return {key: _cap_lists(item, max_items) for key, item in value.items()}
    return value


def _fit_list(items: list[Any], max_items: int, max_chars: int) -> str | None:
    """The longest head of ``items`` that fits in ``max_chars`` with its marker (binary search)."""
    best: str | None = None
    low, high = 0, min(len(items), max_items)
    while low <= high:
        middle = (low + high) // 2
        head = [_cap_lists(item, max_items) for item in items[:middle]]
        text = _dumps([*head, f"…[{len(items) - middle} more items]"])
        if len(text) <= max_chars:
            best, low = text, middle + 1
        else:
            high = middle - 1
    return best
```

`src/sre_graph/integrations/tooling.py`:

```python
"""Turn an async function into an integration tool with metadata and readable errors."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from langchain_core.tools import BaseTool, tool

from sre_graph.domain.tools import ToolMeta, attach_tool_meta

ToolFunction = Callable[..., Awaitable[tuple[str, Any]]]


def integration_tool(meta: ToolMeta) -> Callable[[ToolFunction], BaseTool]:
    """Decorate an async function into a tool tagged with ``meta``.

    The function returns ``(content, artifact)``: compact text for the model
    and the full (capped) data kept on the ``ToolMessage``. Its docstring needs
    a Google-style ``Args:`` section; those lines become the argument
    descriptions the model sees. Raising ``IntegrationError`` gives the model
    an error message instead of failing the run. The tool is async-only, so
    run agents with ``ainvoke`` or ``astream``.
    """

    def decorate(func: ToolFunction) -> BaseTool:
        built = tool(func, response_format="content_and_artifact", parse_docstring=True)
        built.handle_tool_error = True
        return attach_tool_meta(built, meta)

    return decorate
```

`src/sre_graph/integrations/registry.py`:

```python
"""The integrations a deployment knows about, and the clients and tools of one run."""

from __future__ import annotations

import asyncio
from collections.abc import Collection, Iterable
from typing import Any

from langchain_core.tools import BaseTool

from sre_graph.domain.tools import Domain, read_tool_meta
from sre_graph.integrations.base import Integration, VerifyResult, VerifyStatus
from sre_graph.integrations.context import RunContext
from sre_graph.safety.secrets import redact_secrets


class IntegrationRegistry:
    """An explicit list of integrations; nothing is discovered by import side effects."""

    def __init__(
        self, integrations: Iterable[Integration[Any, Any]], *, env_file: str | None = ".env"
    ) -> None:
        self._integrations: dict[str, Integration[Any, Any]] = {}
        for integration in integrations:
            if integration.id in self._integrations:
                raise ValueError(f"duplicate integration id: {integration.id}")
            self._integrations[integration.id] = integration
        self._env_file = env_file

    @property
    def ids(self) -> tuple[str, ...]:
        """Registered integration ids, in registration order."""
        return tuple(self._integrations)

    def run_context(self) -> RunContext:
        """Build a client for every configured integration.

        One broken integration never stops the run: its error goes to
        ``unavailable`` (credentials redacted) and its tools stay out.
        """
        clients: dict[str, object] = {}
        unavailable: dict[str, str] = {}
        for integration_id, integration in self._integrations.items():
            try:
                settings = integration.load_settings(self._env_file)
                if settings is not None:
                    clients[integration_id] = integration.build_client(settings)
            except Exception as exc:
                unavailable[integration_id] = redact_secrets(f"{type(exc).__name__}: {exc}")
        return RunContext(clients=clients, unavailable=unavailable)

    def tools(
        self, context: RunContext, domains: Collection[Domain] | None = None
    ) -> list[BaseTool]:
        """Tools of the integrations that have a client in ``context``, optionally by domain."""
        selected: dict[str, BaseTool] = {}
        for integration_id, integration in self._integrations.items():
            if integration_id not in context.clients:
                continue
            for candidate in integration.tools():
                meta = read_tool_meta(candidate)
                if meta is None:
                    raise ValueError(f"{integration_id} tool {candidate.name} carries no ToolMeta")
                if candidate.name in selected:
                    raise ValueError(f"duplicate tool name: {candidate.name}")
                if domains is None or meta.domains & set(domains):
                    selected[candidate.name] = candidate
        return list(selected.values())

    async def verify_all(
        self, context: RunContext, *, timeout_seconds: float = 15.0
    ) -> list[VerifyResult]:
        """Check every registered integration concurrently, in registration order."""
        checks = (
            self._verify(integration, context, timeout_seconds)
            for integration in self._integrations.values()
        )
        return list(await asyncio.gather(*checks))

    @staticmethod
    async def _verify(
        integration: Integration[Any, Any], context: RunContext, timeout_seconds: float
    ) -> VerifyResult:
        if integration.id in context.unavailable:
            return VerifyResult(
                integration.id, VerifyStatus.FAILED, context.unavailable[integration.id]
            )
        client = context.clients.get(integration.id)
        if client is None:
            return VerifyResult(integration.id, VerifyStatus.NOT_CONFIGURED, "not configured")
        try:
            async with asyncio.timeout(timeout_seconds):
                return await integration.verify(client)
        except TimeoutError:
            return VerifyResult(
                integration.id, VerifyStatus.FAILED, f"no answer within {timeout_seconds:g} seconds"
            )
        except Exception as exc:
            return VerifyResult(integration.id, VerifyStatus.FAILED, redact_secrets(str(exc)))
```

En `pyproject.toml`, agrega la capa `integrations` entre `agents` y `safety`:

```toml
layers = [
    "sre_graph.graphs",
    "sre_graph.agents",
    "sre_graph.integrations",
    "sre_graph.safety",
    "sre_graph.domain",
    "sre_graph.llm | sre_graph.persistence",
    "sre_graph.config",
]
```

- [ ] **Step 4: Verificar que pasan**

Run: `uv run pytest tests/integrations -v`

Expected: `29 passed`.

- [ ] **Step 5: Gate**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run lint-imports && SRE_GRAPH_TEST_DATABASE_URL="postgresql://sre_graph:sre_graph@localhost:5433/sre_graph?connect_timeout=5" SRE_GRAPH_REQUIRE_POSTGRES=1 uv run pytest
```

Expected: todo en verde y `157 passed`.

- [ ] **Step 6: Commit**

```bash
git add src/sre_graph/integrations tests/integrations pyproject.toml
git commit -m "feat(integrations): per-run context, explicit registry, compact payloads and verify" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Cliente de Kubernetes con respuestas grabadas

El cliente lee un cluster:
- carga las credenciales en una `Configuration` privada, sin tocar la global del SDK;
- ejecuta cada llamada en un hilo;
- convierte los objetos de la API en resúmenes compactos, con lo problemático primero;
- traduce los fallos a `IntegrationError` con la explicación del API server.

Los tests no usan mocks del cliente: `FakeKubeRest` reemplaza solo `ApiClient.rest_client`, así que el SDK sigue armando URLs, validando parámetros y deserializando JSON igual que contra un cluster real. El namespace grabado `payments` tiene:
- un pod sano y otro en `CrashLoopBackOff` tras un OOM;
- un nodo `NotReady` con presión de memoria;
- un HPA clavado en su máximo.

Referencia funcional: opensre `integrations/kubernetes/client.py`, que se reescribe.

**Files:**
- Modify: `pyproject.toml`, `uv.lock`
- Create: `src/sre_graph/integrations/kubernetes/__init__.py`, `settings.py`, `summaries.py`, `client.py` (bajo `src/sre_graph/integrations/kubernetes/`)
- Modify: `tests/conftest.py`
- Test: `tests/integrations/kubernetes/conftest.py`, `tests/integrations/kubernetes/test_client.py`

**Interfaces:**
- Consumes: `IntegrationError` (Tarea 7).
- Produces:
  - `INTEGRATION_ID = "kubernetes"`.
  - `class KubernetesSettings(BaseSettings)` con prefijo `K8S_`:
    - campos `enabled=False`, `kubeconfig: str | None`, `context: str | None`, `in_cluster=False`, `verify_namespace="default"`;
    - `request_timeout_seconds=30.0` (`> 0`) y `max_items=200` (entre 1 y 1000).
  - `summaries.py`, con funciones puras sobre los modelos del SDK:
    - pods: `pod_summary`, `pod_is_healthy`, `container_status`, `container_state`, `pod_detail`, `container_detail`;
    - eventos: `event_last_seen`, `event_summary`;
    - workloads: `deployment_summary`, `statefulset_summary`, `daemonset_summary`, `workload_is_healthy`, `selector_text`, `is_owned_by`, `revision_summary`;
    - nodos, HPAs y uso: `node_summary`, `node_is_healthy`, `hpa_summary`, `pod_usage`;
    - auxiliares: `conditions`, `compact`, `iso`.
  - `MAX_LOG_LINES = 1000`.
  - `class KubernetesClient(api_client, *, request_timeout=30.0, max_items=200, verify_namespace="default")`, con:
    - el constructor alternativo `from_settings(settings)` y las propiedades `host` y `verify_namespace`;
    - los métodos async:
      - `version() -> str`;
      - `list_pods(namespace, label_selector=None, *, limit=None)`;
      - `describe_pod(namespace, name)`;
      - `pod_logs(namespace, name, *, container=None, previous=False, tail_lines=200, since_seconds=None) -> str`;
      - `list_events(namespace, *, involved_object=None, warnings_only=False)`;
      - `list_workloads(namespace, label_selector=None)`;
      - `rollout_history(namespace, deployment)`;
      - `list_nodes()`;
      - `list_hpas(namespace)`;
      - `pod_usage(namespace, label_selector=None)`.

- [ ] **Step 1: Tests que fallan**

Reemplaza el contenido de `tests/conftest.py` por esta versión:
- borra las variables `K8S_*` del entorno de cada test;
- borra `KUBECONFIG`, porque el SDK la lee directamente.

```python
"""Shared test configuration: event loop choice, environment isolation, no external tracing."""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import Callable, Iterator, Mapping

import langsmith
import pytest
from langsmith import utils as langsmith_utils

from sre_graph.config.settings import Settings
from sre_graph.config.tracing import disable_external_tracing

_PROVIDER_AND_TRACING_ENV = (
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "LANGCHAIN_TRACING_V2",
    "LANGSMITH_TRACING_V2",
    "LANGCHAIN_TRACING",
    "LANGSMITH_API_KEY",
    "LANGCHAIN_API_KEY",
    "LANGSMITH_OTEL_ENABLED",
)
#: Every integration reads its settings from variables with its own prefix.
_INTEGRATION_ENV_PREFIXES = ("K8S_",)
#: Read by client libraries directly (the Kubernetes SDK falls back to KUBECONFIG).
_CLIENT_LIBRARY_ENV = ("KUBECONFIG",)


def pytest_asyncio_loop_factories(
    config: pytest.Config, item: pytest.Item
) -> Mapping[str, Callable[[], asyncio.AbstractEventLoop]]:
    """psycopg's async driver needs a selector loop; Windows defaults to Proactor."""
    if sys.platform == "win32":
        return {"selector": asyncio.SelectorEventLoop}
    return {"default": asyncio.new_event_loop}


@pytest.fixture(autouse=True, scope="session")
def _no_external_tracing() -> None:
    """Nothing a test runs may reach LangSmith, whatever the developer's shell exports."""
    disable_external_tracing()


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep a developer's real settings, integration config and provider keys out of tests."""
    for name in (
        *(field.upper() for field in Settings.model_fields),
        *_PROVIDER_AND_TRACING_ENV,
        *_CLIENT_LIBRARY_ENV,
    ):
        monkeypatch.delenv(name, raising=False)
    for name in list(os.environ):
        if name.startswith(_INTEGRATION_ENV_PREFIXES):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LANGSMITH_TRACING", "false")


@pytest.fixture
def tracing_enabled_by_environment(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """LangSmith tracing switched on through the environment, as a developer shell might.

    langsmith caches environment lookups (``get_env_var`` is an ``lru_cache``), so
    the cache is cleared on the way in and out. The process override is reset on
    entry, the precondition is asserted, and tracing is disabled again on exit.
    """
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")
    monkeypatch.setenv("LANGSMITH_API_KEY", "lsv2-test-key")
    langsmith_utils.get_env_var.cache_clear()
    langsmith.configure(enabled=None)
    assert langsmith_utils.tracing_is_enabled(), "precondition: the environment enables tracing"
    yield
    disable_external_tracing()
    langsmith_utils.get_env_var.cache_clear()


@pytest.fixture
def postgres_url() -> str:
    """Connection string for ``postgres``-marked tests; CI sets ``SRE_GRAPH_REQUIRE_POSTGRES=1``.

    With that flag a missing URL fails the test instead of skipping it, so a
    misnamed variable in CI cannot silently drop the Postgres coverage.
    """
    url = os.environ.get("SRE_GRAPH_TEST_DATABASE_URL")
    if url:
        return url
    if os.environ.get("SRE_GRAPH_REQUIRE_POSTGRES") == "1":
        pytest.fail("SRE_GRAPH_REQUIRE_POSTGRES=1 but SRE_GRAPH_TEST_DATABASE_URL is not set")
    pytest.skip("SRE_GRAPH_TEST_DATABASE_URL is not set")
```

Crea `tests/integrations/kubernetes/conftest.py`:

```python
"""A recorded "payments" namespace served through the Kubernetes SDK's real HTTP seam.

``FakeKubeRest`` replaces only ``ApiClient.rest_client``: the SDK still builds
the URLs, validates parameters and deserializes the JSON, exactly as it does
against a live cluster. The checkout Deployment has one healthy pod and one
pod in CrashLoopBackOff after an OOM kill; node-2 is NotReady under memory
pressure; the checkout autoscaler is pinned at its maximum.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from kubernetes import client as k8s
from kubernetes.client import rest

from sre_graph.integrations.kubernetes.client import KubernetesClient

CRASHING_POD = "checkout-7d9f8b6c5-x2x9z"
HEALTHY_POD = "checkout-7d9f8b6c5-abcde"
LITERAL_DB_PASSWORD = "hunter2-literal-db-password"
LOG_TEXT = '{"level":"error","msg":"allocating 64MiB: out of memory"}\nfatal: OOM\n'
NAMESPACE = "/api/v1/namespaces/payments"
APPS = "/apis/apps/v1/namespaces/payments"


@dataclass
class FakeHttpResponse:
    status: int
    reason: str
    data: bytes
    headers: dict[str, str] = field(default_factory=dict)


class FakeKubeRest:
    """Answers each request path with a recorded body and remembers what was asked."""

    def __init__(self, routes: dict[str, tuple[int, Any]]) -> None:
        self.routes = dict(routes)
        self.requests: list[tuple[str, dict[str, list[str]]]] = []
        self.failure: Exception | None = None

    def fail(self, path: str, status: int, reason: str, message: str) -> None:
        """Answer ``path`` with an API server error Status."""
        self.routes[path] = (status, status_body(status, reason, message))

    def query(self, path: str) -> dict[str, list[str]]:
        """Query parameters of the most recent request to ``path``."""
        return next(query for requested, query in reversed(self.requests) if requested == path)

    def request(
        self,
        method: str,
        url: str,
        headers: Any = None,
        body: Any = None,
        post_params: Any = None,
        _request_timeout: Any = None,
    ) -> rest.RESTResponse:
        parts = urlsplit(url)
        self.requests.append((parts.path, parse_qs(parts.query)))
        if self.failure is not None:
            raise self.failure
        status, payload = self.routes.get(
            parts.path, (404, status_body(404, "NotFound", f"{parts.path} not found"))
        )
        if isinstance(payload, str):
            data, content_type = payload.encode(), "text/plain"
        else:
            data, content_type = json.dumps(payload).encode(), "application/json"
        reason = "OK" if status < 400 else "Error"
        return rest.RESTResponse(
            FakeHttpResponse(status, reason, data, {"content-type": content_type})
        )

    def close(self) -> None:
        """The SDK calls this when its ApiClient closes."""


def status_body(code: int, reason: str, message: str) -> dict[str, Any]:
    return {
        "kind": "Status",
        "apiVersion": "v1",
        "status": "Failure",
        "message": message,
        "reason": reason,
        "code": code,
    }


def _listing(kind: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    return {"kind": kind, "apiVersion": "v1", "metadata": {}, "items": items}


def _template(app: str, image: str) -> dict[str, Any]:
    return {
        "metadata": {"labels": {"app": app}},
        "spec": {"containers": [{"name": "app", "image": image}]},
    }


def _pod(
    name: str,
    *,
    ready: bool,
    restarts: int,
    state: dict[str, Any],
    last_state: dict[str, Any] | None,
) -> dict[str, Any]:
    container_status: dict[str, Any] = {
        "name": "app",
        "image": "registry.local/checkout:1.4.2",
        "imageID": "sha256:0f1e",
        "ready": ready,
        "restartCount": restarts,
        "state": state,
    }
    if last_state is not None:
        container_status["lastState"] = last_state
    applied = {
        "spec": {"containers": [{"env": [{"name": "DB_PASSWORD", "value": LITERAL_DB_PASSWORD}]}]}
    }
    return {
        "metadata": {
            "name": name,
            "namespace": "payments",
            "labels": {"app": "checkout"},
            "annotations": {
                "kubectl.kubernetes.io/last-applied-configuration": json.dumps(applied),
                "team": "payments",
            },
            "ownerReferences": [
                {
                    "apiVersion": "apps/v1",
                    "kind": "ReplicaSet",
                    "name": "checkout-7d9f8b6c5",
                    "uid": "rs-12",
                }
            ],
        },
        "spec": {
            "nodeName": "node-1",
            "containers": [
                {
                    "name": "app",
                    "image": "registry.local/checkout:1.4.2",
                    "resources": {
                        "requests": {"cpu": "100m", "memory": "128Mi"},
                        "limits": {"memory": "256Mi"},
                    },
                    "env": [
                        {"name": "DB_PASSWORD", "value": LITERAL_DB_PASSWORD},
                        {
                            "name": "API_KEY",
                            "valueFrom": {"secretKeyRef": {"name": "checkout", "key": "api-key"}},
                        },
                    ],
                }
            ],
        },
        "status": {
            "phase": "Running",
            "qosClass": "Burstable",
            "startTime": "2026-10-07T09:00:00Z",
            "conditions": [{"type": "Ready", "status": "True" if ready else "False"}],
            "containerStatuses": [container_status],
        },
    }


CRASHING = _pod(
    CRASHING_POD,
    ready=False,
    restarts=7,
    state={
        "waiting": {
            "reason": "CrashLoopBackOff",
            "message": "back-off 5m0s restarting failed container",
        }
    },
    last_state={
        "terminated": {"reason": "OOMKilled", "exitCode": 137, "finishedAt": "2026-10-07T10:05:00Z"}
    },
)
HEALTHY = _pod(
    HEALTHY_POD,
    ready=True,
    restarts=0,
    state={"running": {"startedAt": "2026-10-07T09:00:05Z"}},
    last_state=None,
)


def _event(name: str, *, kind: str, reason: str, message: str, last: str) -> dict[str, Any]:
    return {
        "metadata": {"name": name, "namespace": "payments"},
        "involvedObject": {"kind": "Pod", "name": CRASHING_POD, "namespace": "payments"},
        "type": kind,
        "reason": reason,
        "message": message,
        "count": 3,
        "firstTimestamp": "2026-10-07T09:30:00Z",
        "lastTimestamp": last,
    }


DEPLOYMENT = {
    "metadata": {"name": "checkout", "namespace": "payments"},
    "spec": {
        "replicas": 2,
        "selector": {"matchLabels": {"app": "checkout"}},
        "template": _template("checkout", "registry.local/checkout:1.4.2"),
    },
    "status": {
        "replicas": 2,
        "readyReplicas": 1,
        "updatedReplicas": 2,
        "availableReplicas": 1,
        "conditions": [
            {
                "type": "Available",
                "status": "False",
                "reason": "MinimumReplicasUnavailable",
                "message": "Deployment does not have minimum availability.",
            }
        ],
    },
}
STATEFULSET = {
    "metadata": {"name": "redis", "namespace": "payments"},
    "spec": {
        "replicas": 3,
        "serviceName": "redis",
        "selector": {"matchLabels": {"app": "redis"}},
        "template": _template("redis", "redis:7.4"),
    },
    "status": {"replicas": 3, "readyReplicas": 3, "updatedReplicas": 3, "availableReplicas": 3},
}
DAEMONSET = {
    "metadata": {"name": "node-exporter", "namespace": "payments"},
    "spec": {
        "selector": {"matchLabels": {"app": "node-exporter"}},
        "template": _template("node-exporter", "prom/node-exporter:1.8"),
    },
    "status": {
        "currentNumberScheduled": 3,
        "desiredNumberScheduled": 3,
        "numberMisscheduled": 0,
        "numberReady": 3,
        "numberAvailable": 3,
        "updatedNumberScheduled": 3,
    },
}


def _replica_set(
    name: str,
    *,
    owner: str,
    revision: int,
    image: str,
    created: str,
    replicas: int,
    change_cause: str | None = None,
) -> dict[str, Any]:
    annotations = {"deployment.kubernetes.io/revision": str(revision)}
    if change_cause is not None:
        annotations["kubernetes.io/change-cause"] = change_cause
    return {
        "metadata": {
            "name": name,
            "namespace": "payments",
            "creationTimestamp": created,
            "annotations": annotations,
            "ownerReferences": [
                {
                    "apiVersion": "apps/v1",
                    "kind": "Deployment",
                    "name": owner,
                    "uid": f"{owner}-uid",
                }
            ],
        },
        "spec": {
            "replicas": replicas,
            "selector": {"matchLabels": {"app": "checkout"}},
            "template": _template("checkout", image),
        },
        "status": {"replicas": replicas, "readyReplicas": replicas},
    }


def _node(
    name: str, *, ready: bool, memory_pressure: bool, taints: list[dict[str, str]]
) -> dict[str, Any]:
    return {
        "metadata": {"name": name},
        "spec": {"taints": taints},
        "status": {
            "conditions": [
                {"type": "MemoryPressure", "status": "True" if memory_pressure else "False"},
                {"type": "DiskPressure", "status": "False"},
                {"type": "Ready", "status": "True" if ready else "False"},
            ],
            "allocatable": {
                "cpu": "3920m",
                "memory": "15Gi",
                "pods": "110",
                "ephemeral-storage": "95Gi",
            },
            "nodeInfo": {
                "architecture": "amd64",
                "bootID": "boot",
                "containerRuntimeVersion": "containerd://1.7.22",
                "kernelVersion": "6.1.0",
                "kubeProxyVersion": "v1.31.2",
                "kubeletVersion": "v1.31.2",
                "machineID": "machine",
                "operatingSystem": "linux",
                "osImage": "Ubuntu 24.04",
                "systemUUID": "uuid",
            },
        },
    }


HPA = {
    "metadata": {"name": "checkout", "namespace": "payments"},
    "spec": {
        "scaleTargetRef": {"apiVersion": "apps/v1", "kind": "Deployment", "name": "checkout"},
        "minReplicas": 2,
        "maxReplicas": 10,
        "metrics": [
            {
                "type": "Resource",
                "resource": {
                    "name": "cpu",
                    "target": {"type": "Utilization", "averageUtilization": 80},
                },
            }
        ],
    },
    "status": {
        "currentReplicas": 10,
        "desiredReplicas": 10,
        "currentMetrics": [
            {
                "type": "Resource",
                "resource": {
                    "name": "cpu",
                    "current": {"averageUtilization": 92, "averageValue": "740m"},
                },
            }
        ],
        "conditions": [
            {
                "type": "ScalingLimited",
                "status": "True",
                "reason": "TooManyReplicas",
                "message": "the desired replica count is more than the maximum replica count",
            }
        ],
    },
}
VERSION = {
    "major": "1",
    "minor": "31",
    "gitVersion": "v1.31.2",
    "gitCommit": "5864a46",
    "gitTreeState": "clean",
    "buildDate": "2026-01-01T00:00:00Z",
    "goVersion": "go1.22.8",
    "compiler": "gc",
    "platform": "linux/amd64",
}


def recorded_routes() -> dict[str, tuple[int, Any]]:
    """Every path the client calls in the payments namespace, with its recorded answer."""
    return {
        "/version/": (200, VERSION),
        f"{NAMESPACE}/pods": (200, _listing("PodList", [HEALTHY, CRASHING])),
        f"{NAMESPACE}/pods/{CRASHING_POD}": (200, CRASHING),
        f"{NAMESPACE}/pods/{CRASHING_POD}/log": (200, LOG_TEXT),
        f"{NAMESPACE}/events": (
            200,
            _listing(
                "EventList",
                [
                    _event(
                        "e1",
                        kind="Normal",
                        reason="Pulled",
                        message="Container image already present",
                        last="2026-10-07T09:40:00Z",
                    ),
                    _event(
                        "e2",
                        kind="Warning",
                        reason="BackOff",
                        message="Back-off restarting failed container app",
                        last="2026-10-07T10:06:00Z",
                    ),
                ],
            ),
        ),
        f"{APPS}/deployments": (200, _listing("DeploymentList", [DEPLOYMENT])),
        f"{APPS}/deployments/checkout": (200, DEPLOYMENT),
        f"{APPS}/statefulsets": (200, _listing("StatefulSetList", [STATEFULSET])),
        f"{APPS}/daemonsets": (200, _listing("DaemonSetList", [DAEMONSET])),
        f"{APPS}/replicasets": (
            200,
            _listing(
                "ReplicaSetList",
                [
                    _replica_set(
                        "checkout-5c6d7e8f9",
                        owner="checkout",
                        revision=11,
                        image="registry.local/checkout:1.4.1",
                        created="2026-10-01T08:00:00Z",
                        replicas=0,
                    ),
                    _replica_set(
                        "checkout-7d9f8b6c5",
                        owner="checkout",
                        revision=12,
                        image="registry.local/checkout:1.4.2",
                        created="2026-10-07T08:55:00Z",
                        replicas=2,
                        change_cause="deploy 1.4.2",
                    ),
                    _replica_set(
                        "checkout-canary-6b5a4",
                        owner="checkout-canary",
                        revision=3,
                        image="registry.local/checkout:2.0.0-rc1",
                        created="2026-10-06T12:00:00Z",
                        replicas=1,
                    ),
                ],
            ),
        ),
        "/api/v1/nodes": (
            200,
            _listing(
                "NodeList",
                [
                    _node("node-1", ready=True, memory_pressure=False, taints=[]),
                    _node(
                        "node-2",
                        ready=False,
                        memory_pressure=True,
                        taints=[
                            {"key": "node.kubernetes.io/memory-pressure", "effect": "NoSchedule"}
                        ],
                    ),
                ],
            ),
        ),
        "/apis/autoscaling/v2/namespaces/payments/horizontalpodautoscalers": (
            200,
            _listing("HorizontalPodAutoscalerList", [HPA]),
        ),
        "/apis/metrics.k8s.io/v1beta1/namespaces/payments/pods": (
            200,
            {
                "kind": "PodMetricsList",
                "items": [
                    {
                        "metadata": {"name": CRASHING_POD, "namespace": "payments"},
                        "containers": [
                            {"name": "app", "usage": {"cpu": "250m", "memory": "255Mi"}}
                        ],
                    }
                ],
            },
        ),
    }


KUBECONFIG = """\
apiVersion: v1
kind: Config
clusters:
- name: test
  cluster:
    server: https://k8s.test:6443
    insecure-skip-tls-verify: true
users:
- name: tester
  user:
    token: test-token
contexts:
- name: test
  context:
    cluster: test
    user: tester
current-context: test
"""


@pytest.fixture
def kube_rest() -> FakeKubeRest:
    return FakeKubeRest(recorded_routes())


@pytest.fixture
def kube_client(kube_rest: FakeKubeRest) -> KubernetesClient:
    api_client = k8s.ApiClient(k8s.Configuration(host="https://k8s.test:6443"))
    api_client.rest_client = kube_rest
    return KubernetesClient(api_client, request_timeout=5.0, verify_namespace="payments")


@pytest.fixture
def kubeconfig_file(tmp_path: Path) -> Path:
    path = tmp_path / "kubeconfig.yaml"
    path.write_text(KUBECONFIG, encoding="utf-8")
    return path
```

Crea `tests/integrations/kubernetes/test_client.py`:

```python
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import urllib3
from kubernetes import client as k8s
from kubernetes.config.config_exception import ConfigException

from sre_graph.integrations.base import IntegrationError
from sre_graph.integrations.kubernetes.client import KubernetesClient
from sre_graph.integrations.kubernetes.settings import KubernetesSettings

CRASHING_POD = "checkout-7d9f8b6c5-x2x9z"
HEALTHY_POD = "checkout-7d9f8b6c5-abcde"
PODS = "/api/v1/namespaces/payments/pods"


def test_a_kubeconfig_is_loaded_into_a_private_configuration(kubeconfig_file: Path) -> None:
    kube = KubernetesClient.from_settings(
        KubernetesSettings(_env_file=None, enabled=True, kubeconfig=str(kubeconfig_file))
    )

    assert kube.host == "https://k8s.test:6443"
    assert k8s.Configuration.get_default_copy().host != "https://k8s.test:6443"


def test_an_unknown_context_is_a_configuration_error(kubeconfig_file: Path) -> None:
    settings = KubernetesSettings(
        _env_file=None, enabled=True, kubeconfig=str(kubeconfig_file), context="nope"
    )

    with pytest.raises(ConfigException):
        KubernetesClient.from_settings(settings)


async def test_pods_come_back_unhealthy_first_with_container_states(
    kube_client: KubernetesClient, kube_rest: Any
) -> None:
    pods = await kube_client.list_pods("payments", "app=checkout")

    assert [pod["name"] for pod in pods] == [CRASHING_POD, HEALTHY_POD]
    crashing = pods[0]
    assert crashing["ready"] is False
    assert crashing["restarts"] == 7
    assert crashing["containers"][0]["state"] == {
        "waiting": "CrashLoopBackOff",
        "message": "back-off 5m0s restarting failed container",
    }
    assert crashing["containers"][0]["last_state"] == {
        "terminated": "OOMKilled",
        "exit_code": 137,
        "finished": "2026-10-07T10:05:00+00:00",
    }
    assert kube_rest.query(PODS) == {"labelSelector": ["app=checkout"], "limit": ["200"]}


async def test_pod_detail_names_env_vars_but_never_their_values(
    kube_client: KubernetesClient,
) -> None:
    detail = await kube_client.describe_pod("payments", CRASHING_POD)

    container = detail["containers"][0]
    assert container["env"] == ["DB_PASSWORD", "API_KEY"]
    assert container["limits"] == {"memory": "256Mi"}
    assert detail["annotations"] == {"team": "payments"}
    assert detail["owners"] == ["ReplicaSet/checkout-7d9f8b6c5"]
    assert "hunter2" not in json.dumps(detail)


async def test_logs_pass_every_option_and_keep_json_lines_verbatim(
    kube_client: KubernetesClient, kube_rest: Any
) -> None:
    text = await kube_client.pod_logs(
        "payments", CRASHING_POD, container="app", previous=True, tail_lines=50, since_seconds=600
    )

    assert text.startswith('{"level":"error","msg":"allocating 64MiB: out of memory"}\n')
    assert kube_rest.query(f"{PODS}/{CRASHING_POD}/log") == {
        "container": ["app"],
        "previous": ["true"],
        "sinceSeconds": ["600"],
        "tailLines": ["50"],
    }


async def test_log_requests_are_capped_at_1000_lines(
    kube_client: KubernetesClient, kube_rest: Any
) -> None:
    await kube_client.pod_logs("payments", CRASHING_POD, tail_lines=50_000)

    assert kube_rest.query(f"{PODS}/{CRASHING_POD}/log")["tailLines"] == ["1000"]


async def test_events_come_newest_first(kube_client: KubernetesClient, kube_rest: Any) -> None:
    events = await kube_client.list_events(
        "payments", involved_object=CRASHING_POD, warnings_only=True
    )

    assert [event["reason"] for event in events] == ["BackOff", "Pulled"]
    assert events[0] == {
        "type": "Warning",
        "reason": "BackOff",
        "object": f"Pod/{CRASHING_POD}",
        "message": "Back-off restarting failed container app",
        "count": 3,
        "first_seen": "2026-10-07T09:30:00+00:00",
        "last_seen": "2026-10-07T10:06:00+00:00",
    }
    assert kube_rest.query("/api/v1/namespaces/payments/events")["fieldSelector"] == [
        f"involvedObject.name={CRASHING_POD},type=Warning"
    ]


async def test_workloads_missing_replicas_come_first(kube_client: KubernetesClient) -> None:
    workloads = await kube_client.list_workloads("payments")

    assert [(item["kind"], item["name"]) for item in workloads] == [
        ("Deployment", "checkout"),
        ("DaemonSet", "node-exporter"),
        ("StatefulSet", "redis"),
    ]
    assert workloads[0]["desired"] == 2
    assert workloads[0]["ready"] == 1
    assert workloads[0]["conditions"][0]["reason"] == "MinimumReplicasUnavailable"


async def test_rollout_history_lists_this_deployments_revisions_newest_first(
    kube_client: KubernetesClient, kube_rest: Any
) -> None:
    revisions = await kube_client.rollout_history("payments", "checkout")

    assert [(item["revision"], item["images"]) for item in revisions] == [
        (12, ["registry.local/checkout:1.4.2"]),
        (11, ["registry.local/checkout:1.4.1"]),
    ]
    assert revisions[0]["change_cause"] == "deploy 1.4.2"
    assert kube_rest.query("/apis/apps/v1/namespaces/payments/replicasets")["labelSelector"] == [
        "app=checkout"
    ]


async def test_nodes_in_trouble_come_first(kube_client: KubernetesClient) -> None:
    nodes = await kube_client.list_nodes()

    assert [node["name"] for node in nodes] == ["node-2", "node-1"]
    assert nodes[0]["ready"] is False
    assert nodes[0]["pressure"] == ["MemoryPressure"]
    assert nodes[0]["taints"] == ["node.kubernetes.io/memory-pressure:NoSchedule"]
    assert nodes[1]["allocatable"] == {"cpu": "3920m", "memory": "15Gi", "pods": "110"}


async def test_an_autoscaler_pinned_at_its_maximum_says_so(kube_client: KubernetesClient) -> None:
    [autoscaler] = await kube_client.list_hpas("payments")

    assert autoscaler["target"] == "Deployment/checkout"
    assert autoscaler["at_max"] is True
    assert autoscaler["metrics"] == ["cpu 92%"]
    assert autoscaler["targets"] == ["cpu target 80%"]
    assert autoscaler["conditions"][0]["reason"] == "TooManyReplicas"


async def test_pod_usage_comes_from_metrics_server(kube_client: KubernetesClient) -> None:
    usage = await kube_client.pod_usage("payments")

    assert usage == [{"pod": CRASHING_POD, "container": "app", "cpu": "250m", "memory": "255Mi"}]


async def test_a_missing_metrics_server_is_explained(
    kube_client: KubernetesClient, kube_rest: Any
) -> None:
    del kube_rest.routes["/apis/metrics.k8s.io/v1beta1/namespaces/payments/pods"]

    with pytest.raises(IntegrationError, match="metrics-server is not installed"):
        await kube_client.pod_usage("payments")


async def test_a_forbidden_call_carries_the_api_servers_explanation(
    kube_client: KubernetesClient, kube_rest: Any
) -> None:
    kube_rest.fail(
        PODS, 403, "Forbidden", 'pods is forbidden: User "dev" cannot list resource "pods"'
    )

    with pytest.raises(IntegrationError) as caught:
        await kube_client.list_pods("payments")

    assert str(caught.value) == (
        "Kubernetes API error 403 while listing pods in payments: "
        'pods is forbidden: User "dev" cannot list resource "pods"'
    )


async def test_a_missing_pod_is_reported_by_name(kube_client: KubernetesClient) -> None:
    with pytest.raises(IntegrationError, match="404 while reading pod payments/ghost"):
        await kube_client.describe_pod("payments", "ghost")


@pytest.mark.parametrize(
    "failure",
    [
        ConnectionRefusedError("Connection refused"),
        urllib3.exceptions.ProtocolError("Connection aborted."),
    ],
)
async def test_an_unreachable_cluster_is_an_integration_error(
    kube_client: KubernetesClient, kube_rest: Any, failure: Exception
) -> None:
    kube_rest.failure = failure

    with pytest.raises(
        IntegrationError,
        match=r"cannot reach the Kubernetes API at https://k8s\.test:6443 while listing pods",
    ):
        await kube_client.list_pods("payments")
```

- [ ] **Step 2: Verificar que fallan**

Run: `uv run pytest tests/integrations/kubernetes/test_client.py -v`

Expected: `ImportError while loading conftest` en `tests/integrations/kubernetes/conftest.py`, con `ModuleNotFoundError: No module named 'kubernetes'`.

- [ ] **Step 3: Dependencias**

En `pyproject.toml`, agrega al final de `dependencies` el SDK y `urllib3`. El cliente importa `urllib3` directamente para reconocer los errores de conexión:

```toml
    "pydantic-settings>=2.15",
    "kubernetes>=37.0.0",
    "urllib3>=2.8,<3",
]
```

En `[tool.mypy]`, debajo de `packages = ["sre_graph"]`, agrega:

```toml
# The Kubernetes SDK types its API classes but not its kubeconfig loaders.
untyped_calls_exclude = ["kubernetes.config"]
```

Run: `uv lock && uv sync --locked`

Expected:
- `uv lock` agrega `kubernetes v37.0.0` con sus dependencias (entre ellas `durationpy`, `websocket-client`, `requests-oauthlib` y `aiohttp`).
- `uv sync` las instala sin errores.

Run: `uv run pytest tests/integrations/kubernetes/test_client.py -v`

Expected: `ImportError while loading conftest`, ahora con `ModuleNotFoundError: No module named 'sre_graph.integrations.kubernetes'`.

- [ ] **Step 4: Implementación**

`src/sre_graph/integrations/kubernetes/__init__.py`:

```python
"""Kubernetes: read-only pods, logs, events, workloads, rollouts, nodes, autoscalers and usage."""
```

`src/sre_graph/integrations/kubernetes/settings.py`:

```python
"""``K8S_*`` settings. The integration stays off until ``K8S_ENABLED=true``."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

INTEGRATION_ID = "kubernetes"


class KubernetesSettings(BaseSettings):
    """Where the cluster credentials come from and how far each call may go."""

    model_config = SettingsConfigDict(
        env_prefix="K8S_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        env_ignore_empty=True,
    )

    enabled: bool = False
    #: Path to a kubeconfig file; unset means ``KUBECONFIG`` or ``~/.kube/config``.
    kubeconfig: str | None = None
    #: Kubeconfig context to use; unset means the file's current context.
    context: str | None = None
    #: Use the service account of the pod this process runs in instead of a kubeconfig.
    in_cluster: bool = False
    #: Namespace the connectivity check lists pods in.
    verify_namespace: str = "default"
    request_timeout_seconds: float = Field(default=30.0, gt=0)
    #: Most items any single list call asks the API server for.
    max_items: int = Field(default=200, ge=1, le=1000)
```

`src/sre_graph/integrations/kubernetes/summaries.py`:

```python
"""Turn Kubernetes API objects into small dicts a model can read: facts only, no secrets.

Environment variable values never leave this module, only their names, and
the ``kubectl apply`` annotation (a full copy of the manifest, env values
included) is dropped.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import Any

from kubernetes.client import (
    CoreV1Event,
    V1Container,
    V1ContainerState,
    V1ContainerStatus,
    V1DaemonSet,
    V1Deployment,
    V1Node,
    V1ObjectMeta,
    V1Pod,
    V1PodSpec,
    V1ReplicaSet,
    V1StatefulSet,
    V2HorizontalPodAutoscaler,
    V2MetricSpec,
    V2MetricStatus,
    V2MetricTarget,
    V2MetricValueStatus,
)

LAST_APPLIED_ANNOTATION = "kubectl.kubernetes.io/last-applied-configuration"
REVISION_ANNOTATION = "deployment.kubernetes.io/revision"
CHANGE_CAUSE_ANNOTATION = "kubernetes.io/change-cause"
PRESSURE_CONDITIONS = ("MemoryPressure", "DiskPressure", "PIDPressure", "NetworkUnavailable")
_NEVER = datetime.min.replace(tzinfo=UTC)


def compact(values: Mapping[str, Any]) -> dict[str, Any]:
    """Drop keys whose value is None or empty; zero and False are facts and stay."""
    return {key: value for key, value in values.items() if value not in (None, "", [], {})}


def iso(moment: datetime | None) -> str | None:
    """ISO-8601 text for a timestamp, None when there is none."""
    return moment.isoformat() if moment else None


def pod_summary(pod: V1Pod) -> dict[str, Any]:
    """Phase, readiness, restarts and per-container state of one pod."""
    status = pod.status
    container_statuses = (status.container_statuses or []) if status else []
    return compact(
        {
            "name": _name(pod.metadata),
            "phase": status.phase if status else None,
            "ready": _condition_true(status.conditions if status else None, "Ready"),
            "restarts": sum(item.restart_count for item in container_statuses),
            "node": pod.spec.node_name if pod.spec else None,
            "started": iso(status.start_time) if status else None,
            "containers": [container_status(item) for item in container_statuses],
        }
    )


def pod_is_healthy(summary: Mapping[str, Any]) -> bool:
    """Succeeded, or running and ready without restarts."""
    if summary.get("phase") == "Succeeded":
        return True
    return (
        summary.get("phase") == "Running"
        and summary.get("ready") is True
        and summary.get("restarts", 0) == 0
    )


def container_status(status: V1ContainerStatus) -> dict[str, Any]:
    """Readiness, restarts, current state and the state before the last restart."""
    return compact(
        {
            "name": status.name,
            "ready": status.ready,
            "restarts": status.restart_count,
            "state": container_state(status.state),
            "last_state": container_state(status.last_state),
        }
    )


def container_state(state: V1ContainerState | None) -> dict[str, Any] | None:
    """Waiting (with reason), terminated (with reason and exit code) or running."""
    if state is None:
        return None
    if state.waiting is not None:
        return compact(
            {"waiting": state.waiting.reason or "Waiting", "message": state.waiting.message}
        )
    if state.terminated is not None:
        ended = state.terminated
        return compact(
            {
                "terminated": ended.reason or "Terminated",
                "exit_code": ended.exit_code,
                "finished": iso(ended.finished_at),
                "message": ended.message,
            }
        )
    if state.running is not None:
        return compact({"running_since": iso(state.running.started_at)})
    return None


def pod_detail(pod: V1Pod) -> dict[str, Any]:
    """Everything useful about one pod: conditions, owners, containers with limits and env names."""
    meta, spec, status = pod.metadata, pod.spec, pod.status
    statuses = {
        item.name: item
        for item in [
            *((status.init_container_statuses or []) if status else []),
            *((status.container_statuses or []) if status else []),
        ]
    }
    annotations = (meta.annotations or {}) if meta else {}
    return compact(
        {
            "name": _name(meta),
            "namespace": meta.namespace if meta else None,
            "phase": status.phase if status else None,
            "reason": status.reason if status else None,
            "message": status.message if status else None,
            "node": spec.node_name if spec else None,
            "qos_class": status.qos_class if status else None,
            "started": iso(status.start_time) if status else None,
            "labels": dict((meta.labels or {}) if meta else {}),
            "annotations": {k: v for k, v in annotations.items() if k != LAST_APPLIED_ANNOTATION},
            "owners": [
                f"{ref.kind}/{ref.name}" for ref in ((meta.owner_references or []) if meta else [])
            ],
            "conditions": conditions(status.conditions if status else None),
            "init_containers": [
                container_detail(item, statuses.get(item.name))
                for item in ((spec.init_containers or []) if spec else [])
            ],
            "containers": [
                container_detail(item, statuses.get(item.name))
                for item in (spec.containers if spec else [])
            ],
        }
    )


def container_detail(container: V1Container, status: V1ContainerStatus | None) -> dict[str, Any]:
    """Image, resources, env var names (never values) and the live status of one container."""
    resources = container.resources
    return compact(
        {
            **(container_status(status) if status is not None else {"name": container.name}),
            "image": container.image,
            "requests": dict(resources.requests or {}) if resources else None,
            "limits": dict(resources.limits or {}) if resources else None,
            "env": [item.name for item in (container.env or [])],
        }
    )


def conditions(items: Iterable[Any] | None) -> list[dict[str, Any]]:
    """Type, status, reason and message of each condition (pods, workloads, nodes, HPAs)."""
    return [
        compact(
            {
                "type": item.type,
                "status": item.status,
                "reason": item.reason,
                "message": item.message,
            }
        )
        for item in (items or [])
    ]


def event_last_seen(event: CoreV1Event) -> datetime:
    """When the event last happened, from whichever field the API server filled in."""
    series = event.series
    return (
        event.last_timestamp
        or (series.last_observed_time if series else None)
        or event.event_time
        or event.metadata.creation_timestamp
        or _NEVER
    )


def event_summary(event: CoreV1Event) -> dict[str, Any]:
    """What happened to which object, how often, and when."""
    involved = event.involved_object
    series = event.series
    last_seen = event_last_seen(event)
    return compact(
        {
            "type": event.type,
            "reason": event.reason,
            "object": f"{involved.kind}/{involved.name}",
            "message": event.message,
            "count": event.count or (series.count if series else None),
            "first_seen": iso(event.first_timestamp),
            "last_seen": iso(last_seen) if last_seen is not _NEVER else None,
        }
    )


def deployment_summary(deployment: V1Deployment) -> dict[str, Any]:
    """Desired versus ready replicas, images and conditions of a Deployment."""
    spec, status = deployment.spec, deployment.status
    desired = spec.replicas if spec and spec.replicas is not None else 1
    return _workload(
        "Deployment",
        deployment.metadata,
        desired=desired,
        ready=(status.ready_replicas or 0) if status else 0,
        updated=(status.updated_replicas or 0) if status else 0,
        available=(status.available_replicas or 0) if status else 0,
        pod_spec=spec.template.spec if spec else None,
        status_conditions=status.conditions if status else None,
    )


def statefulset_summary(statefulset: V1StatefulSet) -> dict[str, Any]:
    """Desired versus ready replicas, images and conditions of a StatefulSet."""
    spec, status = statefulset.spec, statefulset.status
    desired = spec.replicas if spec and spec.replicas is not None else 1
    return _workload(
        "StatefulSet",
        statefulset.metadata,
        desired=desired,
        ready=(status.ready_replicas or 0) if status else 0,
        updated=(status.updated_replicas or 0) if status else 0,
        available=(status.available_replicas or 0) if status else 0,
        pod_spec=spec.template.spec if spec else None,
        status_conditions=status.conditions if status else None,
    )


def daemonset_summary(daemonset: V1DaemonSet) -> dict[str, Any]:
    """Scheduled versus ready pods, images and conditions of a DaemonSet."""
    spec, status = daemonset.spec, daemonset.status
    return _workload(
        "DaemonSet",
        daemonset.metadata,
        desired=status.desired_number_scheduled if status else 0,
        ready=status.number_ready if status else 0,
        updated=(status.updated_number_scheduled or 0) if status else 0,
        available=(status.number_available or 0) if status else 0,
        pod_spec=spec.template.spec if spec else None,
        status_conditions=status.conditions if status else None,
    )


def workload_is_healthy(summary: Mapping[str, Any]) -> bool:
    """Every desired replica is ready and available."""
    desired = summary.get("desired", 0)
    return bool(summary.get("ready", 0) >= desired and summary.get("available", 0) >= desired)


def selector_text(deployment: V1Deployment) -> str:
    """The Deployment's ``matchLabels`` as a label selector string ("" when there are none)."""
    selector = deployment.spec.selector if deployment.spec else None
    labels = (selector.match_labels or {}) if selector else {}
    return ",".join(f"{key}={value}" for key, value in sorted(labels.items()))


def is_owned_by(replica_set: V1ReplicaSet, kind: str, name: str) -> bool:
    """Whether ``replica_set`` belongs to the workload ``kind/name``."""
    owners = (replica_set.metadata.owner_references or []) if replica_set.metadata else []
    return any(owner.kind == kind and owner.name == name for owner in owners)


def revision_summary(replica_set: V1ReplicaSet) -> dict[str, Any]:
    """One rollout revision: number, when it was created, its images and replicas."""
    meta, spec, status = replica_set.metadata, replica_set.spec, replica_set.status
    annotations = (meta.annotations or {}) if meta else {}
    revision = annotations.get(REVISION_ANNOTATION, "")
    return compact(
        {
            "revision": int(revision) if revision.isdigit() else None,
            "replica_set": _name(meta),
            "created": iso(meta.creation_timestamp) if meta else None,
            "images": _images(spec.template.spec if spec and spec.template else None),
            "desired": spec.replicas if spec else None,
            "ready": (status.ready_replicas or 0) if status else 0,
            "change_cause": annotations.get(CHANGE_CAUSE_ANNOTATION),
        }
    )


def node_summary(node: V1Node) -> dict[str, Any]:
    """Readiness, pressure, schedulability, taints and allocatable capacity of a node."""
    spec, status = node.spec, node.status
    node_conditions = (status.conditions or []) if status else []
    allocatable = (status.allocatable or {}) if status else {}
    return compact(
        {
            "name": _name(node.metadata),
            "ready": _condition_true(node_conditions, "Ready"),
            "pressure": [
                item.type
                for item in node_conditions
                if item.type in PRESSURE_CONDITIONS and item.status == "True"
            ],
            "unschedulable": bool(spec.unschedulable) if spec else False,
            "taints": [
                f"{taint.key}={taint.value}:{taint.effect}"
                if taint.value
                else f"{taint.key}:{taint.effect}"
                for taint in ((spec.taints or []) if spec else [])
            ],
            "kubelet": status.node_info.kubelet_version if status and status.node_info else None,
            "allocatable": {
                key: allocatable[key] for key in ("cpu", "memory", "pods") if key in allocatable
            },
        }
    )


def node_is_healthy(summary: Mapping[str, Any]) -> bool:
    """Ready, schedulable and under no pressure."""
    return (
        summary.get("ready") is True
        and not summary.get("pressure")
        and summary.get("unschedulable") is not True
    )


def hpa_summary(hpa: V2HorizontalPodAutoscaler) -> dict[str, Any]:
    """Replica bounds, current and target metrics, and whether the autoscaler is pinned at max."""
    spec, status = hpa.spec, hpa.status
    current = status.current_replicas if status else None
    return compact(
        {
            "name": _name(hpa.metadata),
            "target": f"{spec.scale_target_ref.kind}/{spec.scale_target_ref.name}"
            if spec
            else None,
            "min": (spec.min_replicas or 1) if spec else None,
            "max": spec.max_replicas if spec else None,
            "current": current,
            "desired": status.desired_replicas if status else None,
            "at_max": bool(spec and current is not None and current >= spec.max_replicas),
            "metrics": [
                _metric_status_text(item)
                for item in ((status.current_metrics or []) if status else [])
            ],
            "targets": [_metric_spec_text(item) for item in ((spec.metrics or []) if spec else [])],
            "conditions": conditions(status.conditions if status else None),
        }
    )


def pod_usage(item: Mapping[str, Any]) -> list[dict[str, Any]]:
    """CPU and memory per container from one ``metrics.k8s.io`` PodMetrics object."""
    pod = (item.get("metadata") or {}).get("name")
    return [
        compact(
            {
                "pod": pod,
                "container": container.get("name"),
                "cpu": (container.get("usage") or {}).get("cpu"),
                "memory": (container.get("usage") or {}).get("memory"),
            }
        )
        for container in item.get("containers") or []
    ]


def _workload(
    kind: str,
    meta: V1ObjectMeta | None,
    *,
    desired: int,
    ready: int,
    updated: int,
    available: int,
    pod_spec: V1PodSpec | None,
    status_conditions: Iterable[Any] | None,
) -> dict[str, Any]:
    return compact(
        {
            "kind": kind,
            "name": _name(meta),
            "desired": desired,
            "ready": ready,
            "updated": updated,
            "available": available,
            "images": _images(pod_spec),
            "conditions": conditions(status_conditions),
        }
    )


def _name(meta: V1ObjectMeta | None) -> str | None:
    return meta.name if meta else None


def _images(pod_spec: V1PodSpec | None) -> list[str]:
    return [item.image for item in (pod_spec.containers if pod_spec else []) if item.image]


def _condition_true(items: Iterable[Any] | None, condition_type: str) -> bool | None:
    for item in items or []:
        if item.type == condition_type:
            return bool(item.status == "True")
    return None


def _metric_spec_text(metric: V2MetricSpec) -> str:
    resource = metric.resource or metric.container_resource
    if resource is not None:
        return f"{resource.name} target {_target_text(resource.target)}"
    named = metric.pods or metric.object or metric.external
    if named is not None:
        return f"{named.metric.name} target {_target_text(named.target)}"
    return metric.type


def _metric_status_text(metric: V2MetricStatus) -> str:
    resource = metric.resource or metric.container_resource
    if resource is not None:
        return f"{resource.name} {_value_text(resource.current)}"
    named = metric.pods or metric.object or metric.external
    if named is not None:
        return f"{named.metric.name} {_value_text(named.current)}"
    return metric.type


def _target_text(target: V2MetricTarget) -> str:
    if target.average_utilization is not None:
        return f"{target.average_utilization}%"
    return str(target.average_value or target.value or "?")


def _value_text(current: V2MetricValueStatus) -> str:
    if current.average_utilization is not None:
        return f"{current.average_utilization}%"
    return str(current.average_value or current.value or "?")
```

`src/sre_graph/integrations/kubernetes/client.py`:

```python
"""Read-only access to one Kubernetes cluster, async over the blocking SDK."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import urllib3
from kubernetes import client as k8s
from kubernetes.client.exceptions import ApiException
from kubernetes.config.incluster_config import load_incluster_config
from kubernetes.config.kube_config import load_kube_config

from sre_graph.integrations.base import IntegrationError
from sre_graph.integrations.kubernetes import summaries
from sre_graph.integrations.kubernetes.settings import KubernetesSettings

MAX_LOG_LINES = 1000


class KubernetesClient:
    """One cluster's API, read-only. Every call runs the blocking SDK in a worker thread.

    Lists come back with the entries that most need attention first (unhealthy
    pods, workloads and nodes; newest events), because long lists are cut
    before they reach the model. Failures raise ``IntegrationError`` with the
    API server's own explanation.
    """

    def __init__(
        self,
        api_client: k8s.ApiClient,
        *,
        request_timeout: float = 30.0,
        max_items: int = 200,
        verify_namespace: str = "default",
    ) -> None:
        self._api_client = api_client
        self._core = k8s.CoreV1Api(api_client)
        self._apps = k8s.AppsV1Api(api_client)
        self._autoscaling = k8s.AutoscalingV2Api(api_client)
        self._custom = k8s.CustomObjectsApi(api_client)
        self._version = k8s.VersionApi(api_client)
        self._timeout = request_timeout
        self._max_items = max_items
        self.verify_namespace = verify_namespace

    @classmethod
    def from_settings(cls, settings: KubernetesSettings) -> KubernetesClient:
        """Load credentials into a private ``Configuration``, never the SDK's global default."""
        configuration = k8s.Configuration()
        kubeconfig = Path(settings.kubeconfig).expanduser() if settings.kubeconfig else None
        if settings.in_cluster:
            load_incluster_config(client_configuration=configuration)
        else:
            if kubeconfig is not None and not kubeconfig.is_file():
                raise FileNotFoundError(f"kubeconfig {kubeconfig} does not exist")
            load_kube_config(
                config_file=str(kubeconfig) if kubeconfig else None,
                context=settings.context,
                client_configuration=configuration,
                persist_config=False,
            )
        return cls(
            k8s.ApiClient(configuration),
            request_timeout=settings.request_timeout_seconds,
            max_items=settings.max_items,
            verify_namespace=settings.verify_namespace,
        )

    @property
    def host(self) -> str:
        """The API server URL this client talks to."""
        return str(self._api_client.configuration.host)

    async def version(self) -> str:
        """The API server's version, for example ``v1.31.2``."""
        info = await self._call("reading the server version", self._version.get_code)
        return str(info.git_version)

    async def list_pods(
        self, namespace: str, label_selector: str | None = None, *, limit: int | None = None
    ) -> list[dict[str, Any]]:
        """Pod summaries, unhealthy pods first."""
        listing = await self._call(
            f"listing pods in {namespace}",
            self._core.list_namespaced_pod,
            namespace,
            label_selector=label_selector,
            limit=limit or self._max_items,
        )
        pods = [summaries.pod_summary(pod) for pod in listing.items]
        return sorted(pods, key=lambda pod: (summaries.pod_is_healthy(pod), pod.get("name", "")))

    async def describe_pod(self, namespace: str, name: str) -> dict[str, Any]:
        """One pod in detail, with environment variable names but never their values."""
        pod = await self._call(
            f"reading pod {namespace}/{name}", self._core.read_namespaced_pod, name, namespace
        )
        return summaries.pod_detail(pod)

    async def pod_logs(
        self,
        namespace: str,
        name: str,
        *,
        container: str | None = None,
        previous: bool = False,
        tail_lines: int = 200,
        since_seconds: int | None = None,
    ) -> str:
        """The last ``tail_lines`` log lines (at most 1000) of a container."""
        # The raw body is decoded here so a log made of JSON lines is never parsed as JSON.
        response: Any = await self._call(
            f"reading logs of pod {namespace}/{name}",
            self._core.read_namespaced_pod_log,
            name,
            namespace,
            container=container,
            previous=previous,
            tail_lines=min(max(tail_lines, 1), MAX_LOG_LINES),
            since_seconds=since_seconds,
            _preload_content=False,
        )
        return bytes(response.data).decode("utf-8", errors="replace")

    async def list_events(
        self, namespace: str, *, involved_object: str | None = None, warnings_only: bool = False
    ) -> list[dict[str, Any]]:
        """Events, newest first, optionally for one object or only warnings."""
        selectors = []
        if involved_object:
            selectors.append(f"involvedObject.name={involved_object}")
        if warnings_only:
            selectors.append("type=Warning")
        listing = await self._call(
            f"listing events in {namespace}",
            self._core.list_namespaced_event,
            namespace,
            field_selector=",".join(selectors) or None,
            limit=self._max_items,
        )
        ordered = sorted(listing.items, key=summaries.event_last_seen, reverse=True)
        return [summaries.event_summary(event) for event in ordered]

    async def list_workloads(
        self, namespace: str, label_selector: str | None = None
    ) -> list[dict[str, Any]]:
        """Deployments, StatefulSets and DaemonSets, the ones missing replicas first."""
        deployments, statefulsets, daemonsets = await asyncio.gather(
            self._call(
                f"listing deployments in {namespace}",
                self._apps.list_namespaced_deployment,
                namespace,
                label_selector=label_selector,
                limit=self._max_items,
            ),
            self._call(
                f"listing statefulsets in {namespace}",
                self._apps.list_namespaced_stateful_set,
                namespace,
                label_selector=label_selector,
                limit=self._max_items,
            ),
            self._call(
                f"listing daemonsets in {namespace}",
                self._apps.list_namespaced_daemon_set,
                namespace,
                label_selector=label_selector,
                limit=self._max_items,
            ),
        )
        workloads = [
            *(summaries.deployment_summary(item) for item in deployments.items),
            *(summaries.statefulset_summary(item) for item in statefulsets.items),
            *(summaries.daemonset_summary(item) for item in daemonsets.items),
        ]
        return sorted(
            workloads,
            key=lambda item: (
                summaries.workload_is_healthy(item),
                item["kind"],
                item.get("name", ""),
            ),
        )

    async def rollout_history(self, namespace: str, deployment: str) -> list[dict[str, Any]]:
        """The Deployment's revisions (its ReplicaSets), newest first."""
        target = await self._call(
            f"reading deployment {namespace}/{deployment}",
            self._apps.read_namespaced_deployment,
            deployment,
            namespace,
        )
        listing = await self._call(
            f"listing replica sets of {namespace}/{deployment}",
            self._apps.list_namespaced_replica_set,
            namespace,
            label_selector=summaries.selector_text(target) or None,
            limit=self._max_items,
        )
        revisions = [
            summaries.revision_summary(item)
            for item in listing.items
            if summaries.is_owned_by(item, "Deployment", deployment)
        ]
        return sorted(revisions, key=lambda item: item.get("revision", 0), reverse=True)

    async def list_nodes(self) -> list[dict[str, Any]]:
        """Node summaries, the not-ready or pressured ones first."""
        listing = await self._call("listing nodes", self._core.list_node, limit=self._max_items)
        nodes = [summaries.node_summary(node) for node in listing.items]
        return sorted(
            nodes, key=lambda node: (summaries.node_is_healthy(node), node.get("name", ""))
        )

    async def list_hpas(self, namespace: str) -> list[dict[str, Any]]:
        """HorizontalPodAutoscalers with their bounds, metrics and conditions."""
        listing = await self._call(
            f"listing autoscalers in {namespace}",
            self._autoscaling.list_namespaced_horizontal_pod_autoscaler,
            namespace,
            limit=self._max_items,
        )
        return [summaries.hpa_summary(item) for item in listing.items]

    async def pod_usage(
        self, namespace: str, label_selector: str | None = None
    ) -> list[dict[str, Any]]:
        """Current CPU and memory per container, from ``metrics.k8s.io``."""
        listing = await self._call(
            f"reading pod usage in {namespace}",
            self._custom.list_namespaced_custom_object,
            "metrics.k8s.io",
            "v1beta1",
            namespace,
            "pods",
            label_selector=label_selector,
            not_found=(
                f"pod usage is unavailable: metrics.k8s.io answered 404 (metrics-server is not "
                f"installed, or namespace {namespace} does not exist)"
            ),
        )
        items = listing.get("items", []) if isinstance(listing, dict) else []
        return [usage for item in items for usage in summaries.pod_usage(item)]

    async def _call[T](
        self,
        action: str,
        method: Callable[..., T],
        /,
        *args: Any,
        not_found: str | None = None,
        **kwargs: Any,
    ) -> T:
        def run() -> T:
            try:
                return method(*args, _request_timeout=self._timeout, **kwargs)
            except ApiException as exc:
                raise self._api_error(exc, action, not_found) from exc
            except (urllib3.exceptions.HTTPError, OSError) as exc:
                raise IntegrationError(
                    f"cannot reach the Kubernetes API at {self.host} while {action}: {exc}"
                ) from exc

        return await asyncio.to_thread(run)

    def _api_error(self, exc: ApiException, action: str, not_found: str | None) -> IntegrationError:
        if not exc.status:
            return IntegrationError(
                f"cannot reach the Kubernetes API at {self.host} while {action}: {exc.reason}"
            )
        if exc.status == 404 and not_found:
            return IntegrationError(not_found)
        return IntegrationError(
            f"Kubernetes API error {exc.status} while {action}: {_status_message(exc)}"
        )


def _status_message(exc: ApiException) -> str:
    """The ``message`` of the API server's Status body, else the HTTP reason."""
    try:
        body = json.loads(exc.body or "")
    except ValueError:
        body = None
    if isinstance(body, dict) and body.get("message"):
        return str(body["message"])
    return str(exc.reason or "no details")
```

- [ ] **Step 5: Verificar que pasan**

Run: `uv run pytest tests/integrations/kubernetes/test_client.py -v`

Expected: `17 passed`.

- [ ] **Step 6: Gate**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run lint-imports && SRE_GRAPH_TEST_DATABASE_URL="postgresql://sre_graph:sre_graph@localhost:5433/sre_graph?connect_timeout=5" SRE_GRAPH_REQUIRE_POSTGRES=1 uv run pytest
```

Expected: todo en verde y `174 passed`.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml uv.lock src/sre_graph/integrations/kubernetes tests/conftest.py tests/integrations/kubernetes/conftest.py tests/integrations/kubernetes/test_client.py
git commit -m "feat(kubernetes): read-only async client with compact, secret-free summaries" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Tools e integración de Kubernetes, catálogo y documentación

Las 9 tools `k8s_*` exponen el cliente a los especialistas. Todas son de solo lectura y sirven al dominio `kubernetes`; además:
- logs → `logs`;
- historial de rollouts → `changes`;
- uso de pods → `metrics`.

`KubernetesIntegration` une settings, cliente, tools y `verify`, y `default_registry()` registra las integraciones incorporadas. Con esto queda cubierta la aceptación de la Fase 1 para Kubernetes. El `verify` contra un cluster real es opcional (Step 6).

**Files:**
- Create: `src/sre_graph/integrations/kubernetes/tools.py`, `src/sre_graph/integrations/kubernetes/integration.py`, `src/sre_graph/integrations/catalog.py`
- Test: `tests/integrations/kubernetes/test_tools.py`, `tests/integrations/kubernetes/test_integration.py`
- Modify: `tests/test_env_example.py`, `.env.example`, `README.md`

**Interfaces:**
- Consumes:
  - `KubernetesClient` y `KubernetesSettings` (Tarea 8);
  - `integration_tool`, `require_client`, `RunContext`, `tool_payload`, `text_payload`, `Integration`, `VerifyResult`, `VerifyStatus` e `IntegrationRegistry` (Tarea 7);
  - `GuardrailsMiddleware` (Tarea 4), usado en los tests.
- Produces:
  - `TOOLS: tuple[BaseTool, ...]`, en orden: `k8s_list_pods`, `k8s_describe_pod`, `k8s_pod_logs`, `k8s_list_events`, `k8s_list_workloads`, `k8s_rollout_history`, `k8s_list_nodes`, `k8s_list_hpas`, `k8s_pod_usage`.
  - `class KubernetesIntegration(Integration[KubernetesSettings, KubernetesClient])`, con `id = "kubernetes"` y `display_name = "Kubernetes"`.
  - `default_registry(env_file: str | None = ".env") -> IntegrationRegistry`.

- [ ] **Step 1: Tests que fallan**

Crea `tests/integrations/kubernetes/test_tools.py`:

```python
from __future__ import annotations

import json
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from sre_graph.agents.factory import make_agent
from sre_graph.agents.middleware.guardrails import GuardrailsMiddleware
from sre_graph.agents.testing.scripted_model import ScriptedChatModel, tool_call_message
from sre_graph.domain.tools import Domain, SideEffectLevel, read_tool_meta
from sre_graph.integrations.context import RunContext
from sre_graph.integrations.kubernetes.client import KubernetesClient
from sre_graph.integrations.kubernetes.tools import TOOLS

CRASHING_POD = "checkout-7d9f8b6c5-x2x9z"
PODS = "/api/v1/namespaces/payments/pods"


async def _call(
    tool_name: str, args: dict[str, Any], context: RunContext, **agent_options: Any
) -> ToolMessage:
    model = ScriptedChatModel(
        responses=[tool_call_message(tool_name, args, "c1"), AIMessage("done")]
    )
    agent = make_agent(
        name="kubernetes", model=model, tools=TOOLS, context_schema=RunContext, **agent_options
    )
    result = await agent.ainvoke({"messages": [HumanMessage("investigate")]}, context=context)
    return next(m for m in result["messages"] if isinstance(m, ToolMessage))


def _context(kube_client: KubernetesClient) -> RunContext:
    return RunContext(clients={"kubernetes": kube_client})


def test_every_kubernetes_tool_is_read_only_and_prefixed() -> None:
    for candidate in TOOLS:
        meta = read_tool_meta(candidate)
        assert meta is not None
        assert candidate.name.startswith("k8s_")
        assert meta.integration == "kubernetes"
        assert Domain.KUBERNETES in meta.domains
        assert meta.side_effect_level is SideEffectLevel.READ_ONLY


@pytest.mark.parametrize(
    ("tool_name", "domain"),
    [
        ("k8s_pod_logs", Domain.LOGS),
        ("k8s_rollout_history", Domain.CHANGES),
        ("k8s_pod_usage", Domain.METRICS),
    ],
)
def test_some_tools_also_serve_other_specialists(tool_name: str, domain: Domain) -> None:
    meta = read_tool_meta(next(t for t in TOOLS if t.name == tool_name))

    assert meta is not None
    assert domain in meta.domains


async def test_list_pods_sends_compact_json_and_keeps_the_data(
    kube_client: KubernetesClient,
) -> None:
    message = await _call("k8s_list_pods", {"namespace": "payments"}, _context(kube_client))

    assert json.loads(str(message.content))[0]["name"] == CRASHING_POD
    assert message.artifact[0]["name"] == CRASHING_POD


async def test_logs_reach_the_model_as_text(kube_client: KubernetesClient) -> None:
    message = await _call(
        "k8s_pod_logs",
        {"namespace": "payments", "pod": CRASHING_POD, "previous": True},
        _context(kube_client),
    )

    assert str(message.content).endswith("fatal: OOM\n")
    assert message.artifact["previous"] is True


async def test_a_huge_listing_is_cut_for_the_model_but_kept_whole_in_the_artifact(
    kube_client: KubernetesClient, kube_rest: Any
) -> None:
    status, listing = kube_rest.routes[PODS]
    pod = listing["items"][0]
    many = [
        {**pod, "metadata": {**pod["metadata"], "name": f"web-{index:03d}"}} for index in range(120)
    ]
    kube_rest.routes[PODS] = (status, {**listing, "items": many})

    message = await _call("k8s_list_pods", {"namespace": "payments"}, _context(kube_client))

    content = json.loads(str(message.content))
    shown = len(content) - 1
    assert content[-1] == f"…[{120 - shown} more items]"
    assert 0 < shown <= 50
    assert len(message.artifact) == 120


async def test_an_api_error_reaches_the_model_instead_of_crashing_the_run(
    kube_client: KubernetesClient, kube_rest: Any
) -> None:
    kube_rest.fail(
        PODS, 403, "Forbidden", 'pods is forbidden: User "dev" cannot list resource "pods"'
    )

    message = await _call("k8s_list_pods", {"namespace": "payments"}, _context(kube_client))

    assert message.status == "error"
    assert "Kubernetes API error 403" in str(message.content)


async def test_an_unavailable_cluster_is_explained_to_the_model() -> None:
    context = RunContext(unavailable={"kubernetes": "ConfigException: Invalid kube-config file."})

    message = await _call("k8s_list_pods", {"namespace": "payments"}, context)

    assert message.status == "error"
    assert str(message.content) == (
        "kubernetes is unavailable in this run: ConfigException: Invalid kube-config file."
    )


async def test_secrets_in_container_logs_are_redacted_by_the_guardrails(
    kube_client: KubernetesClient, kube_rest: Any
) -> None:
    token = "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"
    kube_rest.routes[f"{PODS}/{CRASHING_POD}/log"] = (200, f"auth failed for {token}\n")

    message = await _call(
        "k8s_pod_logs",
        {"namespace": "payments", "pod": CRASHING_POD},
        _context(kube_client),
        middleware=[GuardrailsMiddleware()],
    )

    assert token not in str(message.content)
    assert token not in json.dumps(message.artifact)
```

Crea `tests/integrations/kubernetes/test_integration.py`:

```python
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from sre_graph.integrations.base import IntegrationError, VerifyResult, VerifyStatus
from sre_graph.integrations.catalog import default_registry
from sre_graph.integrations.kubernetes.client import KubernetesClient
from sre_graph.integrations.kubernetes.integration import KubernetesIntegration
from sre_graph.integrations.kubernetes.settings import KubernetesSettings


def test_settings_default_to_disabled() -> None:
    settings = KubernetesSettings(_env_file=None)

    assert settings.enabled is False
    assert settings.verify_namespace == "default"
    assert settings.max_items == 200


def test_settings_read_k8s_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("K8S_ENABLED", "true")
    monkeypatch.setenv("K8S_CONTEXT", "staging")
    monkeypatch.setenv("K8S_KUBECONFIG", "")

    settings = KubernetesSettings(_env_file=None)

    assert settings.enabled is True
    assert settings.context == "staging"
    assert settings.kubeconfig is None


def test_settings_reject_a_non_positive_timeout() -> None:
    with pytest.raises(ValueError, match="request_timeout_seconds"):
        KubernetesSettings(_env_file=None, request_timeout_seconds=0)


def test_a_disabled_integration_is_not_configured() -> None:
    assert KubernetesIntegration().load_settings(None) is None


def test_an_enabled_integration_builds_a_client(
    monkeypatch: pytest.MonkeyPatch, kubeconfig_file: Path
) -> None:
    monkeypatch.setenv("K8S_ENABLED", "true")
    monkeypatch.setenv("K8S_KUBECONFIG", str(kubeconfig_file))
    integration = KubernetesIntegration()

    settings = integration.load_settings(None)

    assert settings is not None
    assert integration.build_client(settings).host == "https://k8s.test:6443"


async def test_verify_reports_version_and_pod_access(
    kube_client: KubernetesClient, kube_rest: Any
) -> None:
    result = await KubernetesIntegration().verify(kube_client)

    assert result == VerifyResult(
        "kubernetes",
        VerifyStatus.OK,
        "Kubernetes v1.31.2 at https://k8s.test:6443; can list pods in payments",
    )
    assert kube_rest.query("/api/v1/namespaces/payments/pods")["limit"] == ["1"]


async def test_verify_fails_when_the_credentials_cannot_list_pods(
    kube_client: KubernetesClient, kube_rest: Any
) -> None:
    kube_rest.fail("/api/v1/namespaces/payments/pods", 401, "Unauthorized", "Unauthorized")

    with pytest.raises(IntegrationError, match="401"):
        await KubernetesIntegration().verify(kube_client)


async def test_a_missing_kubeconfig_makes_kubernetes_unavailable_not_the_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("K8S_ENABLED", "true")
    monkeypatch.setenv("K8S_KUBECONFIG", str(tmp_path / "missing.yaml"))
    registry = default_registry(env_file=None)

    context = registry.run_context()
    [result] = await registry.verify_all(context)

    assert context.clients == {}
    assert result.status is VerifyStatus.FAILED
    assert "missing.yaml" in result.detail
    assert registry.tools(context) == []


def test_the_default_registry_offers_kubernetes_tools_once_enabled(
    monkeypatch: pytest.MonkeyPatch, kubeconfig_file: Path
) -> None:
    monkeypatch.setenv("K8S_ENABLED", "true")
    monkeypatch.setenv("K8S_KUBECONFIG", str(kubeconfig_file))
    registry = default_registry(env_file=None)

    tools = registry.tools(registry.run_context())

    assert registry.ids == ("kubernetes",)
    assert len(tools) == 9
```

Reemplaza el contenido de `tests/test_env_example.py` por esta versión, que también exige las variables `K8S_*`:

```python
from __future__ import annotations

from pathlib import Path

import pytest
from dotenv import dotenv_values

from sre_graph.config.settings import Settings
from sre_graph.integrations.kubernetes.settings import KubernetesSettings

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
    expected += [f"K8S_{name.upper()}" for name in KubernetesSettings.model_fields]

    assert [name for name in expected if name not in values] == []


def test_env_example_loads_as_is() -> None:
    settings = Settings(_env_file=ROOT / ".env.example")
    kubernetes = KubernetesSettings(_env_file=ROOT / ".env.example")

    assert settings.environment == "dev"
    assert kubernetes.enabled is False
```

- [ ] **Step 2: Verificar que fallan**

Run: `uv run pytest tests/integrations/kubernetes/test_tools.py tests/integrations/kubernetes/test_integration.py -v`

Expected: `2 errors` durante la colección, con `No module named 'sre_graph.integrations.kubernetes.tools'` y `No module named 'sre_graph.integrations.catalog'`.

Run: `uv run pytest tests/test_env_example.py -v`

Expected: `1 failed, 4 passed`. `test_env_example_documents_every_setting` lista las variables `K8S_*` que faltan.

- [ ] **Step 3: Implementación**

`src/sre_graph/integrations/kubernetes/tools.py`:

```python
"""Read-only Kubernetes tools for the kubernetes, logs, metrics and changes specialists."""

from __future__ import annotations

from typing import Any

from langchain.tools import ToolRuntime
from langchain_core.tools import BaseTool

from sre_graph.domain.tools import Domain, EvidenceType, ToolMeta
from sre_graph.integrations.context import RunContext, require_client
from sre_graph.integrations.kubernetes.client import KubernetesClient
from sre_graph.integrations.kubernetes.settings import INTEGRATION_ID
from sre_graph.integrations.payload import text_payload, tool_payload
from sre_graph.integrations.tooling import integration_tool


def _meta(evidence_type: EvidenceType, *extra_domains: Domain) -> ToolMeta:
    return ToolMeta(
        integration=INTEGRATION_ID,
        domains=frozenset({Domain.KUBERNETES, *extra_domains}),
        evidence_type=evidence_type,
    )


def _client(runtime: ToolRuntime[RunContext]) -> KubernetesClient:
    return require_client(runtime, INTEGRATION_ID, KubernetesClient)


@integration_tool(_meta(EvidenceType.TOPOLOGY))
async def k8s_list_pods(
    namespace: str, runtime: ToolRuntime[RunContext], label_selector: str | None = None
) -> tuple[str, Any]:
    """List pods with phase, readiness, restarts and container states, unhealthy pods first.

    Args:
        namespace: Kubernetes namespace.
        label_selector: Optional label selector, for example "app=checkout".
    """
    pods = await _client(runtime).list_pods(namespace, label_selector)
    return tool_payload(pods), pods


@integration_tool(_meta(EvidenceType.TOPOLOGY))
async def k8s_describe_pod(
    namespace: str, pod: str, runtime: ToolRuntime[RunContext]
) -> tuple[str, Any]:
    """Describe one pod: conditions, owners, and per container image, limits, state, last exit.

    Args:
        namespace: Kubernetes namespace.
        pod: Pod name.
    """
    detail = await _client(runtime).describe_pod(namespace, pod)
    return tool_payload(detail), detail


@integration_tool(_meta(EvidenceType.LOGS, Domain.LOGS))
async def k8s_pod_logs(
    namespace: str,
    pod: str,
    runtime: ToolRuntime[RunContext],
    container: str | None = None,
    previous: bool = False,
    tail_lines: int = 200,
    since_seconds: int | None = None,
) -> tuple[str, Any]:
    """Read the most recent log lines of a pod's container.

    Args:
        namespace: Kubernetes namespace.
        pod: Pod name.
        container: Container name; required when the pod has more than one container.
        previous: Read the logs of the previous, crashed instance of the container.
        tail_lines: How many of the last lines to return, at most 1000.
        since_seconds: Only lines from the last this-many seconds.
    """
    text = await _client(runtime).pod_logs(
        namespace,
        pod,
        container=container,
        previous=previous,
        tail_lines=tail_lines,
        since_seconds=since_seconds,
    )
    artifact = {
        "namespace": namespace,
        "pod": pod,
        "container": container,
        "previous": previous,
        "text": text,
    }
    return (text_payload(text) if text else "(no log lines)"), artifact


@integration_tool(_meta(EvidenceType.EVENTS))
async def k8s_list_events(
    namespace: str,
    runtime: ToolRuntime[RunContext],
    involved_object: str | None = None,
    warnings_only: bool = False,
) -> tuple[str, Any]:
    """List events (BackOff, OOMKilling, FailedScheduling, probe failures…), newest first.

    Args:
        namespace: Kubernetes namespace.
        involved_object: Only events about the object with this name, for example a pod.
        warnings_only: Only events of type Warning.
    """
    events = await _client(runtime).list_events(
        namespace, involved_object=involved_object, warnings_only=warnings_only
    )
    return tool_payload(events), events


@integration_tool(_meta(EvidenceType.TOPOLOGY))
async def k8s_list_workloads(
    namespace: str, runtime: ToolRuntime[RunContext], label_selector: str | None = None
) -> tuple[str, Any]:
    """List Deployments, StatefulSets and DaemonSets: desired vs ready replicas, images, conditions.

    Args:
        namespace: Kubernetes namespace.
        label_selector: Optional label selector, for example "app=checkout".
    """
    workloads = await _client(runtime).list_workloads(namespace, label_selector)
    return tool_payload(workloads), workloads


@integration_tool(_meta(EvidenceType.CHANGES, Domain.CHANGES))
async def k8s_rollout_history(
    namespace: str, deployment: str, runtime: ToolRuntime[RunContext]
) -> tuple[str, Any]:
    """List a Deployment's rollout revisions, newest first, with images and creation times.

    Args:
        namespace: Kubernetes namespace.
        deployment: Deployment name.
    """
    revisions = await _client(runtime).rollout_history(namespace, deployment)
    return tool_payload(revisions), revisions


@integration_tool(_meta(EvidenceType.TOPOLOGY))
async def k8s_list_nodes(runtime: ToolRuntime[RunContext]) -> tuple[str, Any]:
    """List nodes with readiness, memory/disk/PID pressure, taints and allocatable capacity."""
    nodes = await _client(runtime).list_nodes()
    return tool_payload(nodes), nodes


@integration_tool(_meta(EvidenceType.METRICS))
async def k8s_list_hpas(namespace: str, runtime: ToolRuntime[RunContext]) -> tuple[str, Any]:
    """List HorizontalPodAutoscalers: replica bounds, current vs target metrics, conditions.

    Args:
        namespace: Kubernetes namespace.
    """
    autoscalers = await _client(runtime).list_hpas(namespace)
    return tool_payload(autoscalers), autoscalers


@integration_tool(_meta(EvidenceType.METRICS, Domain.METRICS))
async def k8s_pod_usage(
    namespace: str, runtime: ToolRuntime[RunContext], label_selector: str | None = None
) -> tuple[str, Any]:
    """Show current CPU and memory use per container (needs metrics-server).

    Args:
        namespace: Kubernetes namespace.
        label_selector: Optional label selector, for example "app=checkout".
    """
    usage = await _client(runtime).pod_usage(namespace, label_selector)
    return tool_payload(usage), usage


TOOLS: tuple[BaseTool, ...] = (
    k8s_list_pods,
    k8s_describe_pod,
    k8s_pod_logs,
    k8s_list_events,
    k8s_list_workloads,
    k8s_rollout_history,
    k8s_list_nodes,
    k8s_list_hpas,
    k8s_pod_usage,
)
```

`src/sre_graph/integrations/kubernetes/integration.py`:

```python
"""The Kubernetes integration: kubeconfig or in-cluster credentials, read-only tools."""

from __future__ import annotations

from langchain_core.tools import BaseTool

from sre_graph.integrations.base import Integration, VerifyResult, VerifyStatus
from sre_graph.integrations.kubernetes.client import KubernetesClient
from sre_graph.integrations.kubernetes.settings import INTEGRATION_ID, KubernetesSettings
from sre_graph.integrations.kubernetes.tools import TOOLS


class KubernetesIntegration(Integration[KubernetesSettings, KubernetesClient]):
    """Pods, logs, events, workloads, rollouts, nodes, autoscalers and usage of one cluster."""

    id = INTEGRATION_ID
    display_name = "Kubernetes"

    def load_settings(self, env_file: str | None) -> KubernetesSettings | None:
        settings = KubernetesSettings(_env_file=env_file)
        return settings if settings.enabled else None

    def build_client(self, settings: KubernetesSettings) -> KubernetesClient:
        return KubernetesClient.from_settings(settings)

    def tools(self) -> list[BaseTool]:
        return list(TOOLS)

    async def verify(self, client: KubernetesClient) -> VerifyResult:
        """Reach the API server, then prove the credentials can list pods.

        ``/version`` alone proves nothing about credentials: many clusters
        serve it to anonymous users.
        """
        version = await client.version()
        await client.list_pods(client.verify_namespace, limit=1)
        return VerifyResult(
            self.id,
            VerifyStatus.OK,
            f"Kubernetes {version} at {client.host}; can list pods in {client.verify_namespace}",
        )
```

`src/sre_graph/integrations/catalog.py`:

```python
"""The built-in integrations, registered explicitly."""

from __future__ import annotations

from sre_graph.integrations.kubernetes.integration import KubernetesIntegration
from sre_graph.integrations.registry import IntegrationRegistry


def default_registry(env_file: str | None = ".env") -> IntegrationRegistry:
    """Every built-in integration; each stays inactive until its settings enable it."""
    return IntegrationRegistry([KubernetesIntegration()], env_file=env_file)
```

Reemplaza el contenido de `.env.example` por esta versión, que documenta el bloque `K8S_*`:

```bash
# Proveedor LLM y modelos por rol. Deja vacío un LLM_MODEL_* para usar el default del proveedor.
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=
LLM_MODEL_REASONING=
LLM_MODEL_WORKER=
LLM_MODEL_FAST=
LLM_MODEL_JUDGE=
LLM_TEMPERATURE=0
LLM_TIMEOUT_SECONDS=120
LLM_MAX_RETRIES=2

# Persistencia: memory | sqlite | postgres
PERSISTENCE_BACKEND=memory
SQLITE_PATH=data/sre-graph.sqlite
# Postgres de docker compose; connect_timeout evita esperas largas si no responde.
DATABASE_URL=postgresql://sre_graph:sre_graph@localhost:5433/sre_graph?connect_timeout=5

# Entorno: en prod toda tool que cambia estado pide aprobación humana.
ENVIRONMENT=dev
# Tools que cambian estado y corren sin aprobación fuera de prod, separadas por comas.
AUTO_APPROVE_TOOLS=
# Enmascarar identificadores (pods, namespaces, IPs, emails...) antes de enviarlos al modelo.
MASK_ENABLED=false
# Tipos a enmascarar; vacío = todos: pod,namespace,cluster,hostname,account_id,ip_address,email,service_name
MASK_KINDS=
# Patrones extra en JSON, por ejemplo {"ticket": "TCK-[0-9]+"}
MASK_EXTRA_REGEX=

# Kubernetes (solo lectura); apagado hasta K8S_ENABLED=true.
K8S_ENABLED=false
# Vacío = KUBECONFIG o ~/.kube/config; K8S_CONTEXT vacío = el contexto actual del archivo.
K8S_KUBECONFIG=
K8S_CONTEXT=
K8S_IN_CLUSTER=false
K8S_VERIFY_NAMESPACE=default
K8S_REQUEST_TIMEOUT_SECONDS=30
K8S_MAX_ITEMS=200

# Sin trazas a terceros: apaga los tres interruptores (LANGCHAIN_TRACING_V2 tiene prioridad).
LANGSMITH_TRACING=false
LANGSMITH_TRACING_V2=false
LANGCHAIN_TRACING_V2=false
```

Reemplaza el contenido de `README.md` por esta versión, que actualiza el estado y explica cómo activar integraciones y qué protege la capa de seguridad:

````markdown
# sre-graph

Investigación multiagente de incidencias (RCA) construida de forma nativa sobre LangGraph.
Diseño completo: [docs/specs/2026-10-07-sre-graph-design.md](docs/specs/2026-10-07-sre-graph-design.md).

> Estado: Fase 1a (dominio, seguridad, marco de integraciones y Kubernetes).
> El grafo de investigación llega en la Fase 2.

## Requisitos

- Python 3.12 y [uv](https://docs.astral.sh/uv/)
- Docker Desktop (solo para Postgres)

## Inicio rápido

```bash
uv sync
cp .env.example .env    # completa ANTHROPIC_API_KEY para usar modelos reales
uv run pytest
```

`.env` debe estar en UTF-8. En PowerShell copia con `Copy-Item .env.example .env`; si lo
reescribes, usa `Set-Content -Encoding utf8`.

## Postgres local

```bash
docker compose up -d --wait
SRE_GRAPH_TEST_DATABASE_URL="postgresql://sre_graph:sre_graph@localhost:5433/sre_graph?connect_timeout=5" uv run pytest -m postgres
```

En PowerShell:

```powershell
$env:SRE_GRAPH_TEST_DATABASE_URL = "postgresql://sre_graph:sre_graph@localhost:5433/sre_graph?connect_timeout=5"
uv run pytest -m postgres
```

## LangGraph Studio (desarrollo)

```bash
LANGGRAPH_CLI_NO_ANALYTICS=1 uv run langgraph dev --no-browser
```

`LANGGRAPH_CLI_NO_ANALYTICS=1` evita que la CLI envíe estadísticas de uso (en PowerShell:
`$env:LANGGRAPH_CLI_NO_ANALYTICS = "1"` antes del comando).

`http://127.0.0.1:2024/ok` responde `{"ok":true}` cuando el servidor está listo. Detén con Ctrl+C.

## Integraciones

Cada integración se activa con sus variables en `.env` y solo lee. La primera es Kubernetes:
`K8S_ENABLED=true` y, si no usas `~/.kube/config`, `K8S_KUBECONFIG` y `K8S_CONTEXT`.
Una integración mal configurada queda no disponible y la corrida sigue con las demás.

## Seguridad

- Los secretos (tokens, llaves, contraseñas en URLs) se redactan en la salida de cada tool
  antes de que la vea el modelo o se guarde.
- `MASK_ENABLED=true` reemplaza identificadores (pods, namespaces, IPs...) por tokens
  estables antes de enviarlos al modelo; las tools siguen recibiendo los valores reales.
- Toda tool que cambia estado pide aprobación humana; `AUTO_APPROVE_TOOLS` la salta fuera
  de `ENVIRONMENT=prod`.

## Calidad

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run lint-imports && uv run pytest
```

## Licencia

Apache-2.0. `NOTICE` lista el material adaptado de OpenSRE y de LangGraph Starter Kit.
````

- [ ] **Step 4: Verificar que pasan**

Run: `uv run pytest tests/integrations/kubernetes/test_tools.py tests/integrations/kubernetes/test_integration.py tests/test_env_example.py -v`

Expected: `24 passed`.

- [ ] **Step 5: Gate**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run lint-imports && SRE_GRAPH_TEST_DATABASE_URL="postgresql://sre_graph:sre_graph@localhost:5433/sre_graph?connect_timeout=5" SRE_GRAPH_REQUIRE_POSTGRES=1 uv run pytest
```

Expected: todo en verde y `193 passed`.

- [ ] **Step 6 (opcional): `verify` contra un cluster real**

Solo si hay un cluster accesible (kind, minikube o uno de trabajo). Pon `K8S_ENABLED=true` en `.env` (y `K8S_KUBECONFIG`/`K8S_CONTEXT` si no usas `~/.kube/config`) y corre:

```bash
uv run python -c "import asyncio; from sre_graph.integrations.catalog import default_registry; r = default_registry(); print(asyncio.run(r.verify_all(r.run_context())))"
```

Expected:
- Con acceso: `[VerifyResult(integration='kubernetes', status=<VerifyStatus.OK: 'ok'>, detail='Kubernetes v1.x.y at https://...; can list pods in default')]`.
- Sin acceso: `status=<VerifyStatus.FAILED: 'failed'>` y el motivo. Por ejemplo, `FileNotFoundError: kubeconfig ... does not exist`, o `Kubernetes API error 401 ...`.

Si no hay cluster, sáltalo y anótalo en el ledger. Vuelve a dejar `K8S_ENABLED=false` en `.env` si lo cambiaste.

- [ ] **Step 7: Commit**

```bash
git add src/sre_graph/integrations/kubernetes/tools.py src/sre_graph/integrations/kubernetes/integration.py src/sre_graph/integrations/catalog.py tests/integrations/kubernetes/test_tools.py tests/integrations/kubernetes/test_integration.py tests/test_env_example.py .env.example README.md
git commit -m "feat(kubernetes): nine read-only tools, the integration and the default registry" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## Verificación final

1. Corre el gate completo:

   ```bash
   uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run lint-imports && SRE_GRAPH_TEST_DATABASE_URL="postgresql://sre_graph:sre_graph@localhost:5433/sre_graph?connect_timeout=5" SRE_GRAPH_REQUIRE_POSTGRES=1 uv run pytest
   ```

   Expected: `193 passed`. Sin la URL de Postgres: `191 passed, 2 skipped`.

2. Run: `git log --oneline main..fase-1a`

   Expected: 10 commits: el del plan y uno por tarea.

3. Revisa a mano los cinco puntos de **Review Focus** contra los tests que los fijan.

4. La rama queda local. La integración a `main` se decide al cerrar (finishing-a-development-branch), sin remoto ni push.
