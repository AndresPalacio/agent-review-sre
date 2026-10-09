# Fase 1b — Dominio de incidentes: plan de implementación

> **Para agentes:** SUB-SKILL REQUERIDA: usa superpowers:subagent-driven-development (recomendado) o superpowers:executing-plans para implementar este plan tarea por tarea. Los pasos usan checkboxes (`- [ ]`).

**Goal:** dejar en `domain/` el modelo del incidente completo, puro y probado, que el grafo de investigación de la Fase 2 usará en cada nodo:
- alertas de ocho fuentes llevadas a una forma única, más la compuerta de ruido y el esquema de extracción para texto libre;
- la ventana del incidente;
- la taxonomía de causa raíz;
- la evidencia citable;
- el plan de investigación y los hallazgos de especialistas;
- el diagnóstico en formato incident-command y su verificación determinista;
- las acciones de remediación;
- el reporte con sus cuatro renderers.

**Architecture:**
- Todo vive en la capa `domain` (`graphs → agents → integrations → safety → domain → llm | persistence → config`): modelos Pydantic y funciones puras, sin red ni LangChain nuevos.
- **Las alertas** pasan por un parser determinista por fuente (Alertmanager, Grafana, Datadog, CloudWatch por SNS o EventBridge, Sentry, PagerDuty, New Relic y genérico) que produce un `NormalizedAlert` congelado. La detección elige el parser por la forma del payload.
- **El razonamiento** se apoya en contratos tipados:
  - `InvestigationPlan` y `SpecialistFinding`;
  - `Diagnosis`, cuya categoría se cierra a la taxonomía con un `Literal` dinámico;
  - `VerificationReport`, que revisa citas, completitud, coherencia y la regla "healthy" sin LLM.
- **El reporte** reúne alerta, diagnóstico, verificación, acciones y la evidencia citada, renumerada E1, E2… Cuatro renderers puros lo convierten para cada canal.

**Tech Stack:** lo de las Fases 0 y 1a (Python 3.12, uv, pydantic 2.13, pytest 9, ruff 0.16, mypy 2.4 `strict`, import-linter 2.15). Sin dependencias nuevas.

**Spec:** `docs/specs/2026-10-07-sre-graph-design.md`

> **Alcance:** es el plan 1b de la Fase 1. El 1a (seguridad, marco de integraciones, Kubernetes) ya está en `main`. El 1c (observabilidad) y el 1d (nube, errores, cambios, entrega, MCP) vienen después. Los nodos del grafo que usan este dominio llegan en la Fase 2.
>
> Todo el código de este plan ya corrió en un borrador sobre Windows 11, con 398 tests en verde (2 de ellos contra Postgres en Docker), ruff, mypy `strict` e import-linter. Además se ensayó tarea por tarea sobre un clon limpio de `main`: cada tarea falla donde el plan dice y termina en verde con el conteo indicado.
>
> La taxonomía (96 categorías) y las plantillas de remediación (69) salen del historial de opensre (`ce77b4a0b^`, `57a5cbe0c^`) mediante un script, con los nombres intactos para los escenarios de evaluación de la Fase 3.

## Global Constraints

- **Lenguaje y versiones:** Python 3.12 (`requires-python = ">=3.12,<3.13"`) y versiones fijadas en `uv.lock`. Este plan no agrega dependencias.
- **Sin opensre:** cero dependencia (ni import ni paquete); lo portado se reescribe y se atribuye en `NOTICE`.
- **Capas:** todo el código nuevo vive en `sre_graph.domain` (`graphs → agents → integrations → safety → domain → llm | persistence → config`, contrato de import-linter). `domain` no importa `safety`, `integrations` ni nada más alto.
- **Estado serializable:** los modelos se guardan en el estado del grafo con `model_dump(mode="json")` y se reconstruyen con `model_validate`. Los timestamps son UTC con zona horaria.
- **Secretos:** el dominio no redacta, porque está por debajo de `safety`. Quien lo usa (intake en la Fase 2, entrega en 1d) redacta antes de guardar o enviar.
- **Gate de calidad** al cerrar cada tarea: `uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run lint-imports && uv run pytest`.
- **Idioma:** prosa de documentos en español; código, identificadores y docstrings en inglés.
- **Repo:**
  - `C:\Users\USUARIO\Desktop\devops-telemetri\sre-graph`, rama de trabajo `fase-1b` creada desde `main`.
  - Todo local: sin remoto y sin push.
- **Commits:** cada commit termina con el trailer `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **Un webhook con campos vacíos o ausentes** (sin severidad, sin timestamps, sin alertas, una confirmación de suscripción SNS):
   - Esperado: se parsea con defaults razonables o falla con un `AlertParseError` claro, nunca con un error inesperado; una pista de fuente equivocada cae a la detección.
   - Tests:
     - Tarea 2: `test_an_alertmanager_payload_without_alerts_is_rejected`, `test_an_sns_subscription_confirmation_is_not_an_alert`;
     - Tarea 3: `test_a_hint_whose_parser_rejects_the_payload_falls_back_to_detection`, `test_an_empty_message_is_not_an_alert`.
2. **Timestamps en formatos y zonas mezclados** (epoch en ms, `+0000`, sin zona, el `0001-01-01` de Alertmanager):
   - Esperado: todo termina en UTC y el tiempo cero cuenta como ausente.
   - Tests: Tarea 1, `test_timestamps_in_every_format_become_utc` y `test_unusable_timestamps_are_none`; Tarea 4, `test_naive_inputs_are_read_as_utc`.
3. **Una categoría que el modelo inventa o escribe distinto:**
   - Esperado: se normaliza o termina en `unknown`, nunca se guarda fuera de la taxonomía; el esquema estructurado la rechaza.
   - Tests: Tarea 5, `test_model_labels_are_normalized_and_fail_closed`; Tarea 7, `test_the_schema_rejects_a_category_outside_the_allowed_names`.
4. **Un claim que no cita evidencia o cita evidencia inventada:**
   - Esperado: queda `unsupported`, baja el score, la verificación no pasa y el reporte lo marca "unverified".
   - Tests: Tarea 7, `test_a_claim_citing_evidence_never_collected_is_unsupported` y `test_unsupported_claims_lower_the_score_and_fail_verification`; Tarea 9, `test_markdown_has_every_section_with_citations_and_flags`.
5. **Texto hostil o enorme en el reporte** (`<!channel>`, `&`, miles de caracteres):
   - Esperado: se escapa para cada canal y respeta sus límites (Slack 150/3000, Telegram 4096).
   - Tests: Tarea 9, `test_slack_blocks_respect_limits_and_escape_markup`, `test_telegram_html_is_escaped_and_bold` y `test_a_telegram_message_over_the_limit_drops_whole_sections`.

## Decisiones tomadas al validar

Cada decisión dice qué cuesta si resulta equivocada.

- **Una alerta por notificación de grupo** (Alertmanager y Grafana):
  - Se usan las etiquetas comunes del grupo (o las de la primera alerta que dispara si están vacías), `started_at` es el disparo más temprano y `alert_count` cuenta las alertas.
  - Coste: si dentro del grupo hay pods distintos, la investigación arranca por el primero.
- **Forma canónica de opensre.** Los escenarios de evaluación (`title`, `state`, `alert_source`, `commonLabels`, `commonAnnotations`) pasan por el parser genérico, que respeta el `alert_source` declarado si es una fuente conocida.
- **Texto libre:**
  - En este plan quedan la compuerta determinista mínima (vacío, emoji o un acuse como "ok" o "gracias") y el esquema `AlertExtraction` para el modelo del intake de la Fase 2.
  - `parse_alert(text)` es el respaldo determinista: título = primera línea.
  - Un JSON que no es objeto (por ejemplo, una lista) se trata como texto.
- **CloudWatch también por EventBridge.** La spec decía CloudWatch/SNS; se agregó el evento "CloudWatch Alarm State Change" porque es la otra vía habitual y cuesta poco.
- **Taxonomía:**
  - Se portan las 96 categorías generales de opensre con nombres idénticos; quedan fuera las del scope "hermes" (monitoreo de agentes).
  - No se portan los perfiles de taxonomía por fuente; solo los usaba "hermes", y `diagnosis_schema(categories)` permite restringir si hace falta.
- **IDs de evidencia deterministas:**
  - Se usa `ev_` + 10 hex de SHA-256 de herramienta y argumentos, en lugar de E1, E2… secuenciales. Los especialistas en paralelo los generan sin coordinarse y una llamada repetida se deduplica.
  - El reporte renumera E1, E2… por orden de cita.
- **Validación de claims por citas y tipo de evidencia,** en lugar de las heurísticas por claves de vendor de opensre.
  - Un claim que habla de logs y no cita logs queda `weak`, que cuenta como medio.
  - Las palabras se comparan completas: "login" no cuenta como "log".
- **Score de verificación:**
  - Se calcula como confianza × respaldo − 0,15 si la categoría contradice el texto.
  - Pasa con score ≥ 0,5, todas las secciones llenas, ningún claim `unsupported` y sin conflicto "healthy".
  - Coste: un umbral mal calibrado provoca refinamientos de más o de menos en la Fase 2; se recalibra con la evaluación de la Fase 3.
- **Regla "healthy" portada** (resuelta, severidad informativa, sin error y con evidencia recolectada). Además, un veredicto "healthy" sobre una alerta crítica o alta que sigue disparando es un conflicto.
- **Plantillas de remediación:** 69 de opensre. Las etiquetas se traducen a IDs de integración (`eks → kubernetes`); los pasos de una integración ausente se omiten.
- **Renderers en el dominio** (funciones puras). El envío y la redacción de mensajes salientes llegan con la entrega (1d).
- **Ventana:** se mantienen la semántica y los topes de opensre (2 h hacia atrás, 10 min de margen, máximo 7 días). Al expandir ×2 el fin no se mueve.

## Mapa de archivos

| Archivo | Responsabilidad |
|---|---|
| `src/sre_graph/domain/alerts/model.py` | `AlertSource`, `Severity`, `AlertStatus`, `AlertParseError`, `NormalizedAlert` y helpers de texto, severidad, estado, tiempo, identidad y fingerprint |
| `src/sre_graph/domain/alerts/sources.py` | Un parser por fuente |
| `src/sre_graph/domain/alerts/parsing.py` | `detect_source`, `parse_alert`, `text_alert`, `PARSERS` |
| `src/sre_graph/domain/alerts/noise.py` | `is_trivial_message`, `AlertExtraction`, `alert_from_extraction` |
| `src/sre_graph/domain/window.py` | `IncidentWindow`, `resolve_window` |
| `src/sre_graph/domain/taxonomy.py` | `CategoryGroup`, `RootCauseCategory`, `CATEGORIES`, `normalize_category`, `render_taxonomy`, `category_text_mismatch` |
| `src/sre_graph/domain/evidence.py` | `EvidenceItem`, `evidence_id`, `merge_evidence` |
| `src/sre_graph/domain/investigation.py` | `Hypothesis`, `SpecialistTask`, `InvestigationPlan`, `Verdict`, `SpecialistFinding`, `constrain_plan`, `fallback_plan` |
| `src/sre_graph/domain/diagnosis.py` | `Diagnosis` (incident-command), `diagnosis_schema`, `unknown_diagnosis` |
| `src/sre_graph/domain/verification.py` | `check_claim`, `missing_sections`, `is_clearly_healthy`, `verify_diagnosis` |
| `src/sre_graph/domain/remediation.py`, `remediation_templates.py` | `RemediationAction`, `order_actions`, `template_steps`, `TEMPLATES` |
| `src/sre_graph/domain/report.py` | `Report`, `ReportClaim`, `build_report` |
| `src/sre_graph/domain/rendering/` | `render_markdown`, `render_slack`, `render_telegram`, `render_pagerduty_note` |
| `tests/domain/**` | Un archivo de test por módulo; `tests/domain/conftest.py` trae el incidente de ejemplo (alerta, evidencia, diagnóstico) |

---

## Antes de empezar

Todas las tareas cierran con el mismo gate. En Git Bash cada comando se escribe completo, porque las variables exportadas no sobreviven entre llamadas del agente:

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run lint-imports && SRE_GRAPH_TEST_DATABASE_URL="postgresql://sre_graph:sre_graph@localhost:5433/sre_graph?connect_timeout=5" SRE_GRAPH_REQUIRE_POSTGRES=1 uv run pytest
```

---

### Task 1: Alerta normalizada y sus helpers

`NormalizedAlert` es la forma única que produce cada parser. Esta tarea agrega el modelo y las piezas que todos los parsers comparten:
- severidad en una escala (critical, high, warning, info);
- estado (firing, resolved);
- timestamps a UTC;
- etiquetas desde dicts, listas o cadenas de tags;
- identidades (servicio, namespace, cluster, workload y pod) sacadas de las etiquetas;
- fingerprints estables para deduplicar.

**Files:**
- Create: `src/sre_graph/domain/alerts/__init__.py`, `src/sre_graph/domain/alerts/model.py`
- Test: `tests/domain/alerts/test_model.py`

**Interfaces:**
- Consumes: nada nuevo.
- Produces:
  - Enumeraciones (`StrEnum`):
    - `AlertSource`: `alertmanager`, `grafana`, `datadog`, `cloudwatch`, `sentry`, `pagerduty`, `newrelic`, `generic`;
    - `Severity`: `critical`, `high`, `warning`, `info`;
    - `AlertStatus`: `firing`, `resolved`.
  - `class AlertParseError(ValueError)`.
  - `class NormalizedAlert(BaseModel)`, congelado, con los campos `source`, `title`, `fingerprint`, `status=FIRING`, `severity=WARNING`, `started_at=None`, `ended_at=None`, `summary=""`, `error=""`, `labels={}`, `annotations={}`, `service`/`namespace`/`cluster`/`workload`/`pod=None`, `url=None` y `alert_count=1`.
  - `IDENTITY_KEYS: dict[str, tuple[str, ...]]`.
  - Helpers de texto y etiquetas:
    - `clean_text(value) -> str` y `first_text(*values) -> str`;
    - `string_map(value) -> dict[str, str]`.
  - Helpers de severidad, estado y tiempo:
    - `parse_severity(*values, default=Severity.WARNING) -> Severity`;
    - `parse_status(*values) -> AlertStatus`;
    - `parse_timestamp(value) -> datetime | None`.
  - Helpers de identidad y fingerprint:
    - `identity(*sources) -> dict[str, str | None]`;
    - `fingerprint(source, *parts) -> str` (16 hex) y `labels_key(labels) -> str`;
    - `build_alert(*, source, title, fingerprint_parts, labels, annotations, **fields) -> NormalizedAlert`.

- [ ] **Step 1: Rama y línea base**

```bash
cd /c/Users/USUARIO/Desktop/devops-telemetri/sre-graph
git status --short
git checkout -b fase-1b
git add docs/superpowers/plans/2026-10-07-fase-1b-dominio-de-incidentes.md
git commit -m "docs: add phase 1b implementation plan" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
docker compose up -d --wait
uv sync --locked
SRE_GRAPH_TEST_DATABASE_URL="postgresql://sre_graph:sre_graph@localhost:5433/sre_graph?connect_timeout=5" uv run pytest
```

Expected:
- `git status --short` muestra `?? docs/superpowers/plans/2026-10-07-fase-1b-dominio-de-incidentes.md`; si el repo se abrió en un IDE de JetBrains, también `?? .idea/`, que no se agrega.
- Aparece `Switched to a new branch 'fase-1b'` y el commit del plan.
- `sre-graph-postgres-1` queda `Healthy`.
- pytest termina con `240 passed`.

- [ ] **Step 2: Test que falla**

Crea `tests/domain/alerts/test_model.py`:

```python
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from sre_graph.domain.alerts.model import (
    AlertSource,
    AlertStatus,
    Severity,
    fingerprint,
    identity,
    parse_severity,
    parse_status,
    parse_timestamp,
    string_map,
)

MOMENT = datetime(2026, 10, 7, 10, 5, tzinfo=UTC)


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        (("critical",), Severity.CRITICAL),
        (("P1",), Severity.CRITICAL),
        (("error",), Severity.HIGH),
        (("MEDIUM",), Severity.WARNING),
        (("low",), Severity.INFO),
        ((None, "", "normal", "warning"), Severity.WARNING),
        (("bogus",), Severity.WARNING),
    ],
)
def test_severity_words_from_every_source_map_to_one_scale(
    values: tuple[object, ...], expected: Severity
) -> None:
    assert parse_severity(*values) is expected


def test_an_unknown_severity_falls_back_to_the_given_default() -> None:
    assert parse_severity("whatever", default=Severity.HIGH) is Severity.HIGH


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        (("resolved",), AlertStatus.RESOLVED),
        (("OK",), AlertStatus.RESOLVED),
        (("normal",), AlertStatus.RESOLVED),
        (("alerting",), AlertStatus.FIRING),
        ((None, "", "firing"), AlertStatus.FIRING),
        ((), AlertStatus.FIRING),
    ],
)
def test_status_is_resolved_only_when_the_first_present_value_says_so(
    values: tuple[object, ...], expected: AlertStatus
) -> None:
    assert parse_status(*values) is expected


@pytest.mark.parametrize(
    "value",
    [
        "2026-10-07T10:05:00Z",
        "2026-10-07T10:05:00.000+0000",
        "2026-10-07T12:05:00+02:00",
        1791367500,
        "1791367500000",
        MOMENT.replace(tzinfo=None),
    ],
)
def test_timestamps_in_every_format_become_utc(value: object) -> None:
    assert parse_timestamp(value) == MOMENT


@pytest.mark.parametrize("value", [None, "", "yesterday", "0001-01-01T00:00:00Z", True])
def test_unusable_timestamps_are_none(value: object) -> None:
    assert parse_timestamp(value) is None


@pytest.mark.parametrize(
    "value",
    [
        {"env": "prod", "service": "payments", "empty": ""},
        ["env:prod", "service:payments", "no-colon"],
        "env:prod, service:payments",
    ],
)
def test_labels_are_read_from_dicts_tag_lists_and_tag_strings(value: object) -> None:
    assert string_map(value) == {"env": "prod", "service": "payments"}


def test_identity_prefers_specific_keys_and_labels_over_annotations() -> None:
    labels = {"job": "node-exporter", "kube_namespace": "payments"}
    annotations = {"service": "checkout", "namespace": "other"}

    found = identity(labels, annotations)

    assert found["service"] == "checkout"
    assert found["namespace"] == "other"
    assert found["pod"] is None


def test_fingerprints_are_stable_and_source_specific() -> None:
    first = fingerprint(AlertSource.ALERTMANAGER, "group-1")

    assert first == fingerprint(AlertSource.ALERTMANAGER, "group-1")
    assert first != fingerprint(AlertSource.GRAFANA, "group-1")
    assert len(first) == 16
```

- [ ] **Step 3: Verificar que falla**

Run: `uv run pytest tests/domain/alerts/test_model.py -v`

Expected: `1 error` durante la colección, con `ModuleNotFoundError: No module named 'sre_graph.domain.alerts'`.

- [ ] **Step 4: Implementación**

`src/sre_graph/domain/alerts/__init__.py`:

```python
"""Alerts from every source in one shape, and the gate that tells chat from alerts."""
```

`src/sre_graph/domain/alerts/model.py`:

```python
"""The normalized alert every intake path produces, whatever system fired it."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class AlertSource(StrEnum):
    """Systems whose alert payloads have a dedicated parser."""

    ALERTMANAGER = "alertmanager"
    GRAFANA = "grafana"
    DATADOG = "datadog"
    CLOUDWATCH = "cloudwatch"
    SENTRY = "sentry"
    PAGERDUTY = "pagerduty"
    NEWRELIC = "newrelic"
    GENERIC = "generic"


class Severity(StrEnum):
    """How urgent an alert is, on one scale for every source."""

    CRITICAL = "critical"
    HIGH = "high"
    WARNING = "warning"
    INFO = "info"


class AlertStatus(StrEnum):
    """Whether the alerting condition is still active."""

    FIRING = "firing"
    RESOLVED = "resolved"


class AlertParseError(ValueError):
    """The payload is not an alert this parser understands."""


class NormalizedAlert(BaseModel):
    """One alert in a source-independent shape.

    ``labels`` and ``annotations`` keep the source's own key/value context;
    ``service`` through ``pod`` are the identities the planner and the
    specialists need, pulled out of them. Store it in graph state with
    ``model_dump(mode="json")``.
    """

    model_config = ConfigDict(frozen=True)

    source: AlertSource
    title: str
    fingerprint: str
    status: AlertStatus = AlertStatus.FIRING
    severity: Severity = Severity.WARNING
    started_at: datetime | None = None
    ended_at: datetime | None = None
    summary: str = ""
    error: str = ""
    labels: dict[str, str] = Field(default_factory=dict)
    annotations: dict[str, str] = Field(default_factory=dict)
    service: str | None = None
    namespace: str | None = None
    cluster: str | None = None
    workload: str | None = None
    pod: str | None = None
    url: str | None = None
    alert_count: int = 1


_SEVERITY_WORDS: dict[str, Severity] = {
    **dict.fromkeys(
        ("critical", "crit", "fatal", "emergency", "disaster", "page", "p1", "sev1", "sev-1"),
        Severity.CRITICAL,
    ),
    **dict.fromkeys(("high", "error", "major", "p2", "sev2", "sev-2"), Severity.HIGH),
    **dict.fromkeys(
        ("warning", "warn", "medium", "moderate", "minor", "p3", "sev3", "sev-3"),
        Severity.WARNING,
    ),
    **dict.fromkeys(
        ("info", "informational", "low", "none", "debug", "success", "p4", "p5", "sev4", "sev5"),
        Severity.INFO,
    ),
}
_RESOLVED_WORDS = frozenset({"resolved", "ok", "normal", "recovered", "closed", "inactive"})
_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")
#: Below this an epoch number is seconds; above it, milliseconds (year 5138 vs 1973).
_EPOCH_MILLIS_THRESHOLD = 1e11

#: Label keys naming each identity, most specific first.
IDENTITY_KEYS: dict[str, tuple[str, ...]] = {
    "service": ("service", "service_name", "app", "app_kubernetes_io_name", "job"),
    "namespace": ("namespace", "kube_namespace", "exported_namespace", "k8s_namespace_name"),
    "cluster": ("cluster", "cluster_name", "kube_cluster", "eks_cluster", "k8s_cluster_name"),
    "workload": ("deployment", "kube_deployment", "workload_name", "statefulset", "daemonset"),
    "pod": ("pod", "kube_pod", "pod_name", "exported_pod"),
}


def clean_text(value: object) -> str:
    """``value`` as stripped text; empty for None."""
    return "" if value is None else str(value).strip()


def first_text(*values: object) -> str:
    """The first value that is not empty once stripped."""
    for value in values:
        text = clean_text(value)
        if text:
            return text
    return ""


def string_map(value: object) -> dict[str, str]:
    """Labels from a dict, a ``["key:value", ...]`` list or a ``"key:value,key:value"`` string."""
    if isinstance(value, Mapping):
        pairs = [(clean_text(key), clean_text(item)) for key, item in value.items()]
    elif isinstance(value, list | tuple | str):
        items = value.split(",") if isinstance(value, str) else [str(item) for item in value]
        pairs = [
            (clean_text(key), clean_text(item))
            for key, _, item in (entry.partition(":") for entry in items if ":" in entry)
        ]
    else:
        return {}
    return {key: item for key, item in pairs if key and item}


def parse_severity(*values: object, default: Severity = Severity.WARNING) -> Severity:
    """The first value that names a known severity; ``default`` when none does."""
    for value in values:
        severity = _SEVERITY_WORDS.get(clean_text(value).lower())
        if severity is not None:
            return severity
    return default


def parse_status(*values: object) -> AlertStatus:
    """RESOLVED when the first present value says the condition cleared, else FIRING."""
    for value in values:
        text = clean_text(value).lower()
        if text:
            return AlertStatus.RESOLVED if text in _RESOLVED_WORDS else AlertStatus.FIRING
    return AlertStatus.FIRING


def parse_timestamp(value: object) -> datetime | None:
    """A UTC datetime from ISO-8601 text or epoch seconds/milliseconds; None when unusable.

    Times at or before 1970 count as unset: Alertmanager sends
    ``0001-01-01T00:00:00Z`` as the end of a firing alert.
    """
    moment: datetime | None
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, int | float):
        moment = _from_epoch(float(value))
    else:
        text = clean_text(value)
        if _NUMBER.fullmatch(text):
            moment = _from_epoch(float(text))
        else:
            try:
                moment = datetime.fromisoformat(text)
            except ValueError:
                return None
    if moment is None:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    moment = moment.astimezone(UTC)
    return moment if moment.year > 1970 else None


def identity(*sources: Mapping[str, str]) -> dict[str, str | None]:
    """Service, namespace, cluster, workload and pod named in ``sources``.

    Keys are tried most specific first; for each key the sources are tried in
    the order given (labels before annotations).
    """
    found: dict[str, str | None] = {}
    for name, keys in IDENTITY_KEYS.items():
        found[name] = next(
            (source[key] for key in keys for source in sources if source.get(key)), None
        )
    return found


def fingerprint(source: AlertSource, *parts: object) -> str:
    """A stable 16-hex id for one alert identity, used to deduplicate notifications."""
    joined = "|".join([source.value, *(clean_text(part) for part in parts)])
    return hashlib.sha256(joined.encode()).hexdigest()[:16]


def labels_key(labels: Mapping[str, str]) -> str:
    """A canonical text form of ``labels`` for fingerprints."""
    return ",".join(f"{key}={labels[key]}" for key in sorted(labels))


def _from_epoch(number: float) -> datetime | None:
    seconds = number / 1000 if abs(number) > _EPOCH_MILLIS_THRESHOLD else number
    try:
        return datetime.fromtimestamp(seconds, UTC)
    except (OverflowError, OSError, ValueError):
        return None


def build_alert(
    *,
    source: AlertSource,
    title: str,
    fingerprint_parts: tuple[object, ...],
    labels: Mapping[str, str],
    annotations: Mapping[str, str],
    **fields: Any,
) -> NormalizedAlert:
    """A NormalizedAlert whose identities come from ``labels``/``annotations`` unless given."""
    names = identity(labels, annotations)
    for name, value in names.items():
        fields.setdefault(name, value)
    return NormalizedAlert(
        source=source,
        title=title,
        fingerprint=fingerprint(source, *fingerprint_parts),
        labels=dict(labels),
        annotations=dict(annotations),
        **fields,
    )
```

- [ ] **Step 5: Verificar que pasa**

Run: `uv run pytest tests/domain/alerts/test_model.py -v`

Expected: `30 passed`.

- [ ] **Step 6: Gate**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run lint-imports && SRE_GRAPH_TEST_DATABASE_URL="postgresql://sre_graph:sre_graph@localhost:5433/sre_graph?connect_timeout=5" SRE_GRAPH_REQUIRE_POSTGRES=1 uv run pytest
```

Expected: todo en verde y `270 passed`.

- [ ] **Step 7: Commit**

```bash
git add src/sre_graph/domain/alerts tests/domain/alerts/test_model.py
git commit -m "feat(domain): normalized alert model with severity, status, time and identity helpers" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Parsers por fuente

Un parser determinista por fuente. Cada uno lee los campos reales del webhook de esa fuente y devuelve un `NormalizedAlert`, o lanza `AlertParseError` si el payload no tiene su forma. El genérico entiende además la forma canónica de opensre que usan los escenarios de evaluación.

**Files:**
- Create: `src/sre_graph/domain/alerts/sources.py`
- Test: `tests/domain/alerts/test_sources.py`

**Interfaces:**
- Consumes: todo `model.py` (Tarea 1).
- Produces, todas con firma `(payload: Mapping[str, Any]) -> NormalizedAlert`:
  - `parse_alertmanager(payload, source=AlertSource.ALERTMANAGER)`, que agrupa la notificación en una sola alerta;
  - `parse_grafana(payload)`;
  - `parse_datadog(payload)`;
  - `parse_cloudwatch(payload)`, que acepta la alarma cruda, envuelta en SNS o como evento de EventBridge, y rechaza `SubscriptionConfirmation`;
  - `parse_sentry(payload)`, que acepta issue alert, issue, metric alert y el plugin legado;
  - `parse_pagerduty(payload)` (webhooks v3);
  - `parse_newrelic(payload)` (workflow por defecto);
  - `parse_generic(payload)`.

- [ ] **Step 1: Test que falla**

Crea `tests/domain/alerts/test_sources.py`:

```python
from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest

from sre_graph.domain.alerts.model import AlertParseError, AlertSource, AlertStatus, Severity
from sre_graph.domain.alerts.sources import (
    parse_alertmanager,
    parse_cloudwatch,
    parse_datadog,
    parse_generic,
    parse_grafana,
    parse_newrelic,
    parse_pagerduty,
    parse_sentry,
)

STARTED = datetime(2026, 10, 7, 10, 0, tzinfo=UTC)


def _alertmanager(status: str = "firing") -> dict[str, Any]:
    return {
        "version": "4",
        "groupKey": '{}:{alertname="KubePodCrashLooping"}',
        "status": status,
        "receiver": "sre-graph",
        "groupLabels": {"alertname": "KubePodCrashLooping"},
        "commonLabels": {
            "alertname": "KubePodCrashLooping",
            "severity": "critical",
            "namespace": "payments",
            "pod": "checkout-7d9f8b6c5-x2x9z",
            "cluster": "prod-eu-1",
        },
        "commonAnnotations": {"summary": "Pod is crash looping.", "description": "8 restarts."},
        "externalURL": "http://alertmanager:9093",
        "alerts": [
            {
                "status": status,
                "labels": {"alertname": "KubePodCrashLooping"},
                "annotations": {},
                "startsAt": "2026-10-07T10:02:00Z",
                "endsAt": "2026-10-07T10:30:00Z"
                if status == "resolved"
                else "0001-01-01T00:00:00Z",
                "generatorURL": "http://prometheus:9090/graph?g0.expr=up",
                "fingerprint": "b1",
            },
            {
                "status": status,
                "labels": {"alertname": "KubePodCrashLooping"},
                "annotations": {},
                "startsAt": "2026-10-07T10:00:00Z",
                "endsAt": "0001-01-01T00:00:00Z",
                "generatorURL": "http://prometheus:9090/graph?g0.expr=up",
                "fingerprint": "b2",
            },
        ],
    }


def test_an_alertmanager_group_becomes_one_alert_with_its_identities() -> None:
    alert = parse_alertmanager(_alertmanager())

    assert alert.source is AlertSource.ALERTMANAGER
    assert alert.title == "KubePodCrashLooping"
    assert alert.status is AlertStatus.FIRING
    assert alert.severity is Severity.CRITICAL
    assert alert.started_at == STARTED
    assert alert.ended_at is None
    assert (alert.namespace, alert.pod, alert.cluster) == (
        "payments",
        "checkout-7d9f8b6c5-x2x9z",
        "prod-eu-1",
    )
    assert alert.summary == "Pod is crash looping."
    assert alert.alert_count == 2
    assert alert.url == "http://prometheus:9090/graph?g0.expr=up"


def test_a_resolved_alertmanager_group_keeps_its_fingerprint_and_end() -> None:
    firing = parse_alertmanager(_alertmanager())
    resolved = parse_alertmanager(_alertmanager("resolved"))

    assert resolved.status is AlertStatus.RESOLVED
    assert resolved.ended_at == datetime(2026, 10, 7, 10, 30, tzinfo=UTC)
    assert resolved.fingerprint == firing.fingerprint


def test_an_alertmanager_payload_without_alerts_is_rejected() -> None:
    with pytest.raises(AlertParseError, match="alerts"):
        parse_alertmanager({"version": "4", "alerts": []})


def test_a_grafana_webhook_strips_the_status_prefix_and_links_the_panel() -> None:
    payload = {
        "receiver": "sre-graph",
        "status": "firing",
        "orgId": 1,
        "title": "[FIRING:1] HighLatency (checkout)",
        "commonLabels": {},
        "commonAnnotations": {},
        "alerts": [
            {
                "status": "firing",
                "labels": {"severity": "warning", "service": "checkout"},
                "annotations": {"summary": "p99 above 2s"},
                "startsAt": "2026-10-07T10:00:00Z",
                "panelURL": "https://grafana/d/abc?viewPanel=2",
                "generatorURL": "",
                "fingerprint": "g1",
            }
        ],
    }

    alert = parse_grafana(payload)

    assert alert.source is AlertSource.GRAFANA
    assert alert.title == "HighLatency (checkout)"
    assert alert.service == "checkout"
    assert alert.summary == "p99 above 2s"
    assert alert.url == "https://grafana/d/abc?viewPanel=2"


def test_a_datadog_monitor_reads_priority_tags_and_body() -> None:
    payload = {
        "title": "[P1] [Triggered on {service:checkout}] Error rate above 5%",
        "alert_transition": "Triggered",
        "alert_type": "error",
        "priority": "P1",
        "tags": "env:prod,service:checkout,kube_namespace:payments",
        "body": "%%%\nError rate is 7% on checkout.\n%%%",
        "date": "1791367200000",
        "alert_id": "1234",
        "link": "https://app.datadoghq.com/monitors/1234",
    }

    alert = parse_datadog(payload)

    assert alert.title == "Error rate above 5%"
    assert alert.severity is Severity.CRITICAL
    assert alert.status is AlertStatus.FIRING
    assert (alert.service, alert.namespace) == ("checkout", "payments")
    assert alert.summary == "Error rate is 7% on checkout."
    assert alert.started_at == STARTED
    assert alert.url == "https://app.datadoghq.com/monitors/1234"


def test_a_recovered_datadog_monitor_is_resolved() -> None:
    alert = parse_datadog({"title": "[Recovered] Error rate", "alert_transition": "Recovered"})

    assert alert.status is AlertStatus.RESOLVED


def _cloudwatch_alarm(state: str = "ALARM") -> dict[str, Any]:
    return {
        "AlarmName": "payments-prod-high-cpu",
        "AlarmDescription": "CPU above 90% on payments-prod",
        "AWSAccountId": "123456789012",
        "NewStateValue": state,
        "NewStateReason": "Threshold Crossed: 1 datapoint [97.2] was greater than 90.0",
        "StateChangeTime": "2026-10-07T10:00:00.000+0000",
        "Region": "us-east-1",
        "AlarmArn": "arn:aws:cloudwatch:us-east-1:123456789012:alarm:payments-prod-high-cpu",
        "Trigger": {
            "MetricName": "CPUUtilization",
            "Namespace": "AWS/RDS",
            "Dimensions": [{"value": "payments-prod", "name": "DBInstanceIdentifier"}],
        },
    }


def test_a_cloudwatch_alarm_inside_an_sns_notification_is_unwrapped() -> None:
    envelope = {
        "Type": "Notification",
        "TopicArn": "arn:aws:sns:us-east-1:123456789012:alarms",
        "Message": json.dumps(_cloudwatch_alarm()),
    }

    alert = parse_cloudwatch(envelope)

    assert alert.source is AlertSource.CLOUDWATCH
    assert alert.title == "payments-prod-high-cpu"
    assert alert.severity is Severity.HIGH
    assert alert.service == "payments-prod"
    assert alert.started_at == STARTED
    assert alert.labels["metric_namespace"] == "AWS/RDS"
    assert alert.namespace is None
    assert alert.error.startswith("Threshold Crossed")
    assert alert.url is not None and "alarmsV2:alarm/payments-prod-high-cpu" in alert.url


def test_a_cloudwatch_alarm_back_to_ok_is_resolved_and_informational() -> None:
    alert = parse_cloudwatch(_cloudwatch_alarm("OK"))

    assert (alert.status, alert.severity, alert.error) == (
        AlertStatus.RESOLVED,
        Severity.INFO,
        "",
    )


def test_an_sns_subscription_confirmation_is_not_an_alert() -> None:
    with pytest.raises(AlertParseError, match="subscription"):
        parse_cloudwatch({"Type": "SubscriptionConfirmation", "TopicArn": "arn:aws:sns:x"})


def test_a_sentry_issue_alert_reads_the_event() -> None:
    payload = {
        "action": "triggered",
        "data": {
            "triggered_rule": "High error volume",
            "event": {
                "event_id": "e1",
                "issue_id": "42",
                "title": "KeyError: 'customer_id'",
                "level": "error",
                "culprit": "checkout.views.pay",
                "datetime": "2026-10-07T10:00:00Z",
                "web_url": "https://sentry.io/issues/42/events/e1/",
                "project_slug": "checkout",
                "tags": [["environment", "production"], ["release", "1.4.2"]],
            },
        },
    }

    alert = parse_sentry(payload)

    assert alert.title == "KeyError: 'customer_id'"
    assert alert.severity is Severity.HIGH
    assert alert.service == "checkout"
    assert alert.labels["release"] == "1.4.2"
    assert alert.labels["rule"] == "High error volume"
    assert alert.summary == "checkout.views.pay"


def test_a_resolved_sentry_issue_is_resolved() -> None:
    payload = {
        "action": "resolved",
        "data": {
            "issue": {
                "id": "42",
                "title": "KeyError: 'customer_id'",
                "level": "error",
                "status": "resolved",
                "firstSeen": "2026-10-07T10:00:00Z",
                "project": {"slug": "checkout"},
            }
        },
    }

    alert = parse_sentry(payload)

    assert alert.status is AlertStatus.RESOLVED
    assert alert.started_at == STARTED


def test_a_sentry_metric_alert_maps_its_action_to_severity() -> None:
    payload = {
        "action": "critical",
        "data": {
            "metric_alert": {"title": "p95 latency", "date_started": "2026-10-07T10:00:00Z"},
            "description_text": "p95 latency above 3s",
            "web_url": "https://sentry.io/alerts/rules/details/7/",
        },
    }

    alert = parse_sentry(payload)

    assert (alert.title, alert.severity) == ("p95 latency", Severity.CRITICAL)


def test_a_payload_that_is_not_sentry_is_rejected() -> None:
    with pytest.raises(AlertParseError):
        parse_sentry({"data": {}})


def test_a_pagerduty_incident_reads_priority_urgency_and_service() -> None:
    payload = {
        "event": {
            "id": "01DEN",
            "event_type": "incident.triggered",
            "resource_type": "incident",
            "occurred_at": "2026-10-07T10:00:00.000Z",
            "data": {
                "id": "PGR0VU2",
                "type": "incident",
                "html_url": "https://acme.pagerduty.com/incidents/PGR0VU2",
                "number": 2,
                "status": "triggered",
                "title": "Checkout error rate above 5%",
                "created_at": "2026-10-07T10:00:00Z",
                "service": {"id": "PF9KMXH", "summary": "checkout"},
                "priority": {"summary": "P2"},
                "urgency": "high",
            },
        }
    }

    alert = parse_pagerduty(payload)

    assert alert.title == "Checkout error rate above 5%"
    assert alert.severity is Severity.HIGH
    assert alert.service == "checkout"
    assert alert.url == "https://acme.pagerduty.com/incidents/PGR0VU2"
    assert alert.started_at == STARTED


def test_a_resolved_pagerduty_incident_is_resolved() -> None:
    payload = {"event": {"event_type": "incident.resolved", "data": {"id": "P1", "title": "x"}}}

    assert parse_pagerduty(payload).status is AlertStatus.RESOLVED


def test_a_new_relic_issue_reads_entities_and_priority() -> None:
    payload = {
        "id": "a1b2",
        "issueUrl": "https://one.newrelic.com/alerts-ai/issues/a1b2",
        "title": "Checkout latency above threshold",
        "priority": "CRITICAL",
        "impactedEntities": ["checkout-api", "checkout-db"],
        "state": "ACTIVATED",
        "trigger": "STATE_CHANGE",
        "createdAt": 1791367200000,
        "alertPolicyNames": ["Checkout"],
        "alertConditionNames": ["Latency"],
    }

    alert = parse_newrelic(payload)

    assert alert.severity is Severity.CRITICAL
    assert alert.service == "checkout-api"
    assert alert.labels["policy"] == "Checkout"
    assert alert.started_at == STARTED


def test_a_closed_new_relic_issue_is_resolved() -> None:
    alert = parse_newrelic(
        {"id": "a1", "title": "x", "state": "CLOSED", "updatedAt": 1791367200000}
    )

    assert alert.status is AlertStatus.RESOLVED
    assert alert.ended_at == STARTED


def test_the_canonical_opensre_shape_keeps_its_declared_source() -> None:
    payload = {
        "title": "[synthetic-k8s] CrashLoopBackOff — payments-api-7f9dd8b6c4-x7gr9",
        "state": "alerting",
        "alert_source": "datadog",
        "commonLabels": {
            "alertname": "KubernetesPodCrashLooping",
            "severity": "critical",
            "service": "payments-api",
            "cluster_name": "payments-prod-eks",
            "namespace": "payments",
        },
        "commonAnnotations": {
            "summary": "Pod payments-api-7f9dd8b6c4-x7gr9 has restarted 8 times.",
            "error": "Container exit code 137 reported by kubelet.",
            "kube_pod": "payments-api-7f9dd8b6c4-x7gr9",
        },
    }

    alert = parse_generic(payload)

    assert alert.source is AlertSource.DATADOG
    assert alert.status is AlertStatus.FIRING
    assert alert.severity is Severity.CRITICAL
    assert alert.pod == "payments-api-7f9dd8b6c4-x7gr9"
    assert alert.cluster == "payments-prod-eks"
    assert alert.error == "Container exit code 137 reported by kubelet."


def test_a_generic_alert_with_an_unknown_declared_source_stays_generic() -> None:
    alert = parse_generic({"title": "Disk full", "alert_source": "zabbix", "state": "normal"})

    assert (alert.source, alert.status) == (AlertSource.GENERIC, AlertStatus.RESOLVED)


def test_a_cloudwatch_alarm_delivered_by_eventbridge_is_read() -> None:
    event = {
        "version": "0",
        "detail-type": "CloudWatch Alarm State Change",
        "source": "aws.cloudwatch",
        "account": "123456789012",
        "time": "2026-10-07T10:00:00Z",
        "region": "us-east-1",
        "resources": ["arn:aws:cloudwatch:us-east-1:123456789012:alarm:checkout-5xx"],
        "detail": {
            "alarmName": "checkout-5xx",
            "state": {
                "value": "ALARM",
                "reason": "Threshold Crossed",
                "timestamp": "2026-10-07T10:00:00.000+0000",
            },
            "configuration": {
                "description": "5xx above 1%",
                "metrics": [
                    {
                        "metricStat": {
                            "metric": {
                                "namespace": "AWS/ApplicationELB",
                                "name": "HTTPCode_Target_5XX_Count",
                                "dimensions": {"LoadBalancer": "app/checkout/abc"},
                            }
                        }
                    }
                ],
            },
        },
    }

    alert = parse_cloudwatch(event)

    assert (alert.title, alert.severity, alert.started_at) == (
        "checkout-5xx",
        Severity.HIGH,
        STARTED,
    )
    assert alert.labels["LoadBalancer"] == "app/checkout/abc"
    assert alert.summary == "5xx above 1%"
```

- [ ] **Step 2: Verificar que falla**

Run: `uv run pytest tests/domain/alerts/test_sources.py -v`

Expected: `1 error` durante la colección, con `ModuleNotFoundError: No module named 'sre_graph.domain.alerts.sources'`.

- [ ] **Step 3: Implementación**

`src/sre_graph/domain/alerts/sources.py`:

```python
"""One deterministic parser per alert source; each returns a NormalizedAlert or raises."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

from sre_graph.domain.alerts.model import (
    AlertParseError,
    AlertSource,
    AlertStatus,
    NormalizedAlert,
    Severity,
    build_alert,
    clean_text,
    first_text,
    labels_key,
    parse_severity,
    parse_status,
    parse_timestamp,
    string_map,
)

#: Leading "[FIRING:2]" (Grafana) or "[P1] [Triggered on {host:a}]" (Datadog) tags.
_BRACKET_PREFIX = re.compile(r"^(?:\[[^\]]*\]\s*)+")
#: CloudWatch dimensions that name an identity the investigation needs.
_AWS_DIMENSIONS: dict[str, str] = {
    "ClusterName": "cluster",
    "Namespace": "namespace",
    "PodName": "pod",
    "Service": "service",
    "ServiceName": "service",
    "FunctionName": "service",
    "DBInstanceIdentifier": "service",
    "DBClusterIdentifier": "service",
}
_SUMMARY_KEYS = ("summary", "description", "message")
_ERROR_KEYS = ("error", "error_message")


def parse_alertmanager(
    payload: Mapping[str, Any], source: AlertSource = AlertSource.ALERTMANAGER
) -> NormalizedAlert:
    """An Alertmanager (or Grafana) group notification as one alert.

    Common labels and annotations describe the group; when they are empty the
    first firing alert's own are used. The group started with its earliest
    firing alert.
    """
    alerts = [item for item in payload.get("alerts") or [] if isinstance(item, Mapping)]
    if not alerts:
        raise AlertParseError("an Alertmanager notification needs a non-empty 'alerts' list")
    firing = [item for item in alerts if clean_text(item.get("status")).lower() != "resolved"]
    relevant = firing or alerts
    first = relevant[0]
    labels = string_map(payload.get("commonLabels")) or string_map(first.get("labels"))
    annotations = string_map(payload.get("commonAnnotations")) or string_map(
        first.get("annotations")
    )
    started = [moment for item in relevant if (moment := parse_timestamp(item.get("startsAt")))]
    ended = [moment for item in alerts if (moment := parse_timestamp(item.get("endsAt")))]
    status = parse_status(payload.get("status"), first.get("status"))
    title = first_text(
        labels.get("alertname"),
        _BRACKET_PREFIX.sub("", clean_text(payload.get("title"))),
        annotations.get("summary"),
        "Alert",
    )
    return build_alert(
        source=source,
        title=title,
        fingerprint_parts=(payload.get("groupKey") or labels_key(labels),),
        labels=labels,
        annotations=annotations,
        status=status,
        severity=parse_severity(labels.get("severity"), labels.get("priority")),
        started_at=min(started) if started else None,
        ended_at=max(ended) if status is AlertStatus.RESOLVED and ended else None,
        summary=first_text(*(annotations.get(key) for key in _SUMMARY_KEYS)),
        error=first_text(*(annotations.get(key) for key in _ERROR_KEYS)),
        url=first_text(first.get("generatorURL"), payload.get("externalURL")) or None,
        alert_count=len(relevant),
    )


def parse_grafana(payload: Mapping[str, Any]) -> NormalizedAlert:
    """A Grafana unified-alerting webhook (Alertmanager-shaped, with panel links)."""
    alert = parse_alertmanager(payload, source=AlertSource.GRAFANA)
    alerts = [item for item in payload.get("alerts") or [] if isinstance(item, Mapping)]
    panel = first_text(*(item.get("panelURL") or item.get("dashboardURL") for item in alerts))
    return alert.model_copy(update={"url": alert.url or panel or None})


def parse_datadog(payload: Mapping[str, Any]) -> NormalizedAlert:
    """A Datadog monitor webhook: the default body plus the common ``$VARIABLE`` keys."""
    tags = string_map(payload.get("tags"))
    raw_title = first_text(
        payload.get("title"), payload.get("alert_title"), payload.get("event_title")
    )
    title = _BRACKET_PREFIX.sub("", raw_title).strip() or raw_title or "Datadog monitor"
    transition = clean_text(payload.get("alert_transition")).lower()
    alert_type = clean_text(payload.get("alert_type")).lower()
    resolved = transition in ("recovered", "resolved") or alert_type == "success"
    body = "\n".join(
        line for line in clean_text(payload.get("body")).splitlines() if line.strip() != "%%%"
    ).strip()
    return build_alert(
        source=AlertSource.DATADOG,
        title=title,
        fingerprint_parts=(
            first_text(payload.get("alert_id"), payload.get("aggreg_key"), payload.get("id"))
            or title,
        ),
        labels=tags,
        annotations={},
        status=AlertStatus.RESOLVED if resolved else AlertStatus.FIRING,
        severity=parse_severity(payload.get("priority"), tags.get("severity"), alert_type),
        started_at=parse_timestamp(payload.get("date") or payload.get("last_updated")),
        summary=first_text(body, payload.get("event_msg"), payload.get("message")),
        url=first_text(payload.get("link"), payload.get("url")) or None,
    )


def parse_cloudwatch(payload: Mapping[str, Any]) -> NormalizedAlert:
    """A CloudWatch alarm: raw, wrapped in an SNS notification, or as an EventBridge event."""
    if clean_text(payload.get("Type")) == "SubscriptionConfirmation":
        raise AlertParseError("an SNS subscription confirmation is not an alert")
    if clean_text(payload.get("detail-type")) == "CloudWatch Alarm State Change":
        payload = _eventbridge_alarm(payload)
    alarm: Any = payload.get("Message", payload)
    if isinstance(alarm, str):
        try:
            alarm = json.loads(alarm)
        except ValueError as exc:
            raise AlertParseError("the SNS message is not a CloudWatch alarm") from exc
    if not isinstance(alarm, Mapping) or not alarm.get("AlarmName"):
        raise AlertParseError("the SNS message is not a CloudWatch alarm")
    name = clean_text(alarm.get("AlarmName"))
    region = clean_text(alarm.get("Region"))
    state = clean_text(alarm.get("NewStateValue")).upper()
    trigger = _mapping(alarm.get("Trigger"))
    dimensions = {
        first_text(item.get("name"), item.get("Name")): first_text(
            item.get("value"), item.get("Value")
        )
        for item in trigger.get("Dimensions") or []
        if isinstance(item, Mapping)
    }
    labels = {
        key: value
        for key, value in {
            "alarm_name": name,
            "region": region,
            "account_id": clean_text(alarm.get("AWSAccountId")),
            "metric_name": clean_text(trigger.get("MetricName")),
            "metric_namespace": clean_text(trigger.get("Namespace")),
            **dimensions,
        }.items()
        if key and value
    }
    identities = {
        _AWS_DIMENSIONS[key]: value for key, value in dimensions.items() if key in _AWS_DIMENSIONS
    }
    return build_alert(
        source=AlertSource.CLOUDWATCH,
        title=name,
        fingerprint_parts=(alarm.get("AlarmArn") or f"{region}/{name}",),
        labels=labels,
        annotations={},
        status=AlertStatus.RESOLVED if state == "OK" else AlertStatus.FIRING,
        severity={"ALARM": Severity.HIGH, "OK": Severity.INFO}.get(state, Severity.WARNING),
        started_at=parse_timestamp(alarm.get("StateChangeTime")),
        summary=first_text(alarm.get("AlarmDescription"), alarm.get("NewStateReason")),
        error=clean_text(alarm.get("NewStateReason")) if state == "ALARM" else "",
        url=(
            f"https://console.aws.amazon.com/cloudwatch/home?region={region}#alarmsV2:alarm/{name}"
            if region
            else None
        ),
        **identities,
    )


def _eventbridge_alarm(event: Mapping[str, Any]) -> dict[str, Any]:
    """The SNS-style alarm fields carried by an EventBridge alarm state change."""
    detail = _mapping(event.get("detail"))
    state = _mapping(detail.get("state"))
    configuration = _mapping(detail.get("configuration"))
    metrics = configuration.get("metrics") or [{}]
    metric = _mapping(_mapping(_mapping(metrics[0]).get("metricStat")).get("metric"))
    resources = event.get("resources") or []
    return {
        "AlarmName": detail.get("alarmName"),
        "AlarmDescription": configuration.get("description"),
        "AWSAccountId": event.get("account"),
        "NewStateValue": state.get("value"),
        "NewStateReason": state.get("reason"),
        "StateChangeTime": state.get("timestamp") or event.get("time"),
        "Region": event.get("region"),
        "AlarmArn": resources[0] if resources else None,
        "Trigger": {
            "MetricName": metric.get("name"),
            "Namespace": metric.get("namespace"),
            "Dimensions": [
                {"name": name, "value": value}
                for name, value in _mapping(metric.get("dimensions")).items()
            ],
        },
    }


def parse_sentry(payload: Mapping[str, Any]) -> NormalizedAlert:
    """A Sentry issue alert, issue webhook, metric alert, or legacy plugin payload."""
    data = _mapping(payload.get("data"))
    action = clean_text(payload.get("action")).lower()
    if isinstance(data.get("event"), Mapping):
        return _sentry_event(data["event"], rule=clean_text(data.get("triggered_rule")))
    if isinstance(data.get("issue"), Mapping):
        return _sentry_issue(data["issue"], action)
    if isinstance(data.get("metric_alert"), Mapping):
        return _sentry_metric_alert(data, action)
    if payload.get("culprit") is not None or payload.get("project_name"):
        event = _mapping(payload.get("event"))
        return _sentry_event(
            {**event, **payload, "title": first_text(payload.get("message"), event.get("title"))},
            rule="",
        )
    raise AlertParseError("the payload is not a Sentry webhook")


def _sentry_event(event: Mapping[str, Any], *, rule: str) -> NormalizedAlert:
    tags = {
        clean_text(pair[0]): clean_text(pair[1])
        for pair in event.get("tags") or []
        if isinstance(pair, list | tuple) and len(pair) == 2
    }
    project = first_text(event.get("project_slug"), event.get("project_name"), event.get("project"))
    labels = {
        key: value
        for key, value in {**tags, "project": project, "rule": rule}.items()
        if key and value
    }
    title = first_text(event.get("title"), event.get("message"), "Sentry event")
    return build_alert(
        source=AlertSource.SENTRY,
        title=title,
        fingerprint_parts=(first_text(event.get("issue_id"), event.get("event_id")) or title,),
        labels=labels,
        annotations={},
        severity=parse_severity(event.get("level"), default=Severity.HIGH),
        started_at=parse_timestamp(event.get("datetime") or event.get("timestamp")),
        summary=clean_text(event.get("culprit")),
        error=title,
        url=first_text(event.get("web_url"), event.get("issue_url"), event.get("url")) or None,
        service=project or None,
    )


def _sentry_issue(issue: Mapping[str, Any], action: str) -> NormalizedAlert:
    project = _mapping(issue.get("project"))
    slug = first_text(project.get("slug"), project.get("name"))
    title = first_text(issue.get("title"), "Sentry issue")
    resolved = action == "resolved" or clean_text(issue.get("status")).lower() == "resolved"
    return build_alert(
        source=AlertSource.SENTRY,
        title=title,
        fingerprint_parts=(first_text(issue.get("id"), issue.get("shortId")) or title,),
        labels={key: value for key, value in {"project": slug}.items() if value},
        annotations={},
        status=AlertStatus.RESOLVED if resolved else AlertStatus.FIRING,
        severity=parse_severity(issue.get("level"), default=Severity.HIGH),
        started_at=parse_timestamp(issue.get("firstSeen")),
        summary=clean_text(issue.get("culprit")),
        error=title,
        url=first_text(issue.get("permalink"), issue.get("web_url")) or None,
        service=slug or None,
    )


def _sentry_metric_alert(data: Mapping[str, Any], action: str) -> NormalizedAlert:
    alert = data["metric_alert"]
    rule = _mapping(alert.get("alert_rule"))
    title = first_text(
        alert.get("title"), data.get("description_title"), rule.get("name"), "Sentry metric alert"
    )
    return build_alert(
        source=AlertSource.SENTRY,
        title=title,
        fingerprint_parts=(first_text(alert.get("id"), rule.get("id")) or title,),
        labels={},
        annotations={},
        status=AlertStatus.RESOLVED if action == "resolved" else AlertStatus.FIRING,
        severity=parse_severity(action, default=Severity.WARNING),
        started_at=parse_timestamp(alert.get("date_started") or alert.get("date_detected")),
        summary=clean_text(data.get("description_text")),
        url=clean_text(data.get("web_url")) or None,
    )


def parse_pagerduty(payload: Mapping[str, Any]) -> NormalizedAlert:
    """A PagerDuty v3 webhook for an incident event."""
    event = _mapping(payload.get("event"))
    data = _mapping(event.get("data"))
    if not data:
        raise AlertParseError("a PagerDuty webhook needs event.data")
    event_type = clean_text(event.get("event_type"))
    service = _mapping(data.get("service"))
    priority = _mapping(data.get("priority"))
    urgency = clean_text(data.get("urgency")).lower()
    resolved = (
        clean_text(data.get("status")).lower() == "resolved" or event_type == "incident.resolved"
    )
    service_name = clean_text(service.get("summary"))
    labels = {
        key: value
        for key, value in {
            "event_type": event_type,
            "urgency": urgency,
            "priority": clean_text(priority.get("summary")),
            "incident_number": clean_text(data.get("number")),
            "service": service_name,
        }.items()
        if value
    }
    title = first_text(data.get("title"), data.get("summary"), "PagerDuty incident")
    return build_alert(
        source=AlertSource.PAGERDUTY,
        title=title,
        fingerprint_parts=(first_text(data.get("id"), data.get("incident_key")) or title,),
        labels=labels,
        annotations={},
        status=AlertStatus.RESOLVED if resolved else AlertStatus.FIRING,
        severity=parse_severity(
            priority.get("summary"),
            default=Severity.HIGH if urgency == "high" else Severity.WARNING,
        ),
        started_at=parse_timestamp(data.get("created_at") or event.get("occurred_at")),
        url=clean_text(data.get("html_url")) or None,
    )


def parse_newrelic(payload: Mapping[str, Any]) -> NormalizedAlert:
    """A New Relic workflow webhook (the default payload template)."""
    entities = [clean_text(item) for item in payload.get("impactedEntities") or [] if item]
    conditions = [clean_text(item) for item in payload.get("alertConditionNames") or [] if item]
    policies = [clean_text(item) for item in payload.get("alertPolicyNames") or [] if item]
    state = clean_text(payload.get("state")).upper()
    title = first_text(payload.get("title"), *conditions, "New Relic issue")
    labels = {
        key: value
        for key, value in {
            "policy": policies[0] if policies else "",
            "condition": conditions[0] if conditions else "",
            "entities": ",".join(entities),
            "trigger": clean_text(payload.get("trigger")),
        }.items()
        if value
    }
    closed = state == "CLOSED"
    return build_alert(
        source=AlertSource.NEWRELIC,
        title=title,
        fingerprint_parts=(clean_text(payload.get("id")) or title,),
        labels=labels,
        annotations={},
        status=AlertStatus.RESOLVED if closed else AlertStatus.FIRING,
        severity=parse_severity(payload.get("priority")),
        started_at=parse_timestamp(payload.get("createdAt")),
        ended_at=parse_timestamp(payload.get("updatedAt")) if closed else None,
        url=clean_text(payload.get("issueUrl")) or None,
        service=entities[0] if entities else None,
    )


def parse_generic(payload: Mapping[str, Any]) -> NormalizedAlert:
    """Any other JSON alert: labels/annotations/tags plus the usual top-level keys.

    Also reads the canonical OpenSRE shape (``title``, ``state``,
    ``alert_source``, ``commonLabels``, ``commonAnnotations``) used by the
    evaluation scenarios; a known ``alert_source`` becomes the alert's source.
    """
    labels = {
        **string_map(payload.get("tags")),
        **(string_map(payload.get("commonLabels")) or string_map(payload.get("labels"))),
    }
    annotations = string_map(payload.get("commonAnnotations")) or string_map(
        payload.get("annotations")
    )
    title = first_text(
        payload.get("title"),
        payload.get("alert_name"),
        labels.get("alertname"),
        payload.get("name"),
        annotations.get("summary"),
        payload.get("message"),
        "Alert",
    )
    declared = clean_text(payload.get("alert_source")).lower()
    source = AlertSource(declared) if declared in set(AlertSource) else AlertSource.GENERIC
    return build_alert(
        source=source,
        title=title,
        fingerprint_parts=(
            first_text(payload.get("fingerprint"), payload.get("id"))
            or f"{title}|{labels_key(labels)}",
        ),
        labels=labels,
        annotations=annotations,
        status=parse_status(payload.get("status"), payload.get("state")),
        severity=parse_severity(
            payload.get("severity"),
            labels.get("severity"),
            labels.get("priority"),
            payload.get("priority"),
        ),
        started_at=parse_timestamp(
            payload.get("startsAt")
            or payload.get("started_at")
            or payload.get("firedAt")
            or payload.get("activatedAt")
            or payload.get("timestamp")
        ),
        summary=first_text(
            *(annotations.get(key) for key in _SUMMARY_KEYS),
            payload.get("description"),
            payload.get("message"),
            payload.get("body"),
            payload.get("text"),
        ),
        error=first_text(
            *(annotations.get(key) for key in _ERROR_KEYS),
            payload.get("error"),
            payload.get("error_message"),
        ),
        url=first_text(payload.get("url"), payload.get("link"), payload.get("generatorURL"))
        or None,
    )


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}
```

- [ ] **Step 4: Verificar que pasa**

Run: `uv run pytest tests/domain/alerts/test_sources.py -v`

Expected: `20 passed`.

- [ ] **Step 5: Gate**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run lint-imports && SRE_GRAPH_TEST_DATABASE_URL="postgresql://sre_graph:sre_graph@localhost:5433/sre_graph?connect_timeout=5" SRE_GRAPH_REQUIRE_POSTGRES=1 uv run pytest
```

Expected: todo en verde y `290 passed`.

- [ ] **Step 6: Commit**

```bash
git add src/sre_graph/domain/alerts/sources.py tests/domain/alerts/test_sources.py
git commit -m "feat(domain): deterministic alert parsers for eight sources" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Detección de fuente, `parse_alert` y compuerta de ruido

`parse_alert` es la entrada única del intake:
- una pista de fuente (la ruta del webhook) elige el parser;
- si no hay pista, o el parser de la pista rechaza el payload, la detección por forma lo elige;
- un texto que no es JSON se vuelve una alerta genérica.

La compuerta descarta lo trivial (vacío, emoji, acuses y saludos de una palabra). Todo lo demás lo decide el modelo del intake de la Fase 2 con `AlertExtraction`.

**Files:**
- Create: `src/sre_graph/domain/alerts/parsing.py`, `src/sre_graph/domain/alerts/noise.py`
- Test: `tests/domain/alerts/test_parsing.py`, `tests/domain/alerts/test_noise.py`

**Interfaces:**
- Consumes: `model.py` (Tarea 1) y los parsers de `sources.py` (Tarea 2).
- Produces:
  - En `parsing.py`:
    - `PARSERS: dict[AlertSource, Callable[[Mapping[str, Any]], NormalizedAlert]]`;
    - `detect_source(payload) -> AlertSource`;
    - `parse_alert(payload: Mapping[str, Any] | str, *, source_hint: str | None = None) -> NormalizedAlert`;
    - `text_alert(text) -> NormalizedAlert`.
  - En `noise.py`:
    - `is_trivial_message(text) -> bool`;
    - `class AlertExtraction(BaseModel)`, con `is_noise`, `alert_name`, `severity`, `source`, `service`, `namespace`, `cluster`, `workload`, `pod`, `error_message` y `started_at`;
    - `alert_from_extraction(extraction, text) -> NormalizedAlert`.

- [ ] **Step 1: Tests que fallan**

Crea `tests/domain/alerts/test_parsing.py`:

```python
from __future__ import annotations

import json
from typing import Any

import pytest

from sre_graph.domain.alerts.model import AlertParseError, AlertSource
from sre_graph.domain.alerts.parsing import detect_source, parse_alert, text_alert


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"version": "4", "groupKey": "g", "alerts": [{"labels": {}}]}, AlertSource.ALERTMANAGER),
        ({"orgId": 1, "alerts": [{"labels": {}}]}, AlertSource.GRAFANA),
        ({"alerts": [{"panelURL": "https://grafana/p"}], "receiver": "r"}, AlertSource.GRAFANA),
        ({"AlarmName": "cpu"}, AlertSource.CLOUDWATCH),
        (
            {"Type": "Notification", "TopicArn": "arn:aws:sns:x", "Message": "{}"},
            AlertSource.CLOUDWATCH,
        ),
        ({"detail-type": "CloudWatch Alarm State Change"}, AlertSource.CLOUDWATCH),
        ({"event": {"event_type": "incident.triggered"}}, AlertSource.PAGERDUTY),
        ({"action": "triggered", "data": {"event": {}}}, AlertSource.SENTRY),
        ({"culprit": "a.b", "project_name": "checkout"}, AlertSource.SENTRY),
        ({"issueUrl": "https://one.newrelic.com/x"}, AlertSource.NEWRELIC),
        ({"alert_transition": "Triggered", "title": "x"}, AlertSource.DATADOG),
        ({"event_type": "query_alert_monitor", "org": {"id": 1}}, AlertSource.DATADOG),
        ({"title": "Disk full", "labels": {}}, AlertSource.GENERIC),
    ],
)
def test_each_source_is_recognized_by_its_payload_shape(
    payload: dict[str, Any], expected: AlertSource
) -> None:
    assert detect_source(payload) is expected


def test_the_source_hint_picks_the_parser() -> None:
    payload = {"title": "[Triggered] CPU high", "alert_transition": "Triggered"}

    alert = parse_alert(payload, source_hint="Datadog")

    assert alert.source is AlertSource.DATADOG
    assert alert.title == "CPU high"


def test_a_hint_whose_parser_rejects_the_payload_falls_back_to_detection() -> None:
    payload = {"title": "Disk full", "severity": "critical"}

    alert = parse_alert(payload, source_hint="alertmanager")

    assert alert.source is AlertSource.GENERIC
    assert alert.title == "Disk full"


def test_a_json_string_is_parsed_like_its_object() -> None:
    payload = {"AlarmName": "cpu", "NewStateValue": "ALARM", "Region": "us-east-1"}

    alert = parse_alert(json.dumps(payload))

    assert alert.source is AlertSource.CLOUDWATCH


def test_free_text_becomes_a_generic_alert_titled_by_its_first_line() -> None:
    alert = parse_alert("checkout is returning 502s\nstarted after the 10:00 deploy")

    assert alert.source is AlertSource.GENERIC
    assert alert.title == "checkout is returning 502s"
    assert "10:00 deploy" in alert.summary


def test_a_very_long_first_line_is_shortened_for_the_title() -> None:
    alert = text_alert("x" * 300)

    assert len(alert.title) == 120
    assert alert.title.endswith("…")


def test_an_empty_message_is_not_an_alert() -> None:
    with pytest.raises(AlertParseError, match="empty"):
        parse_alert("   ")
```

Crea `tests/domain/alerts/test_noise.py`:

```python
from __future__ import annotations

import pytest

from sre_graph.domain.alerts.model import AlertSource, Severity
from sre_graph.domain.alerts.noise import AlertExtraction, alert_from_extraction, is_trivial_message


@pytest.mark.parametrize(
    "text", ["", "   ", "ok", "Thanks!", "thank you", "👍", "gracias", "LGTM."]
)
def test_empty_messages_and_bare_acknowledgements_are_trivial(text: str) -> None:
    assert is_trivial_message(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "checkout is down",
        "ok so the payments pods keep restarting",
        "health check passed",
        "CPU 97% on payments-prod",
    ],
)
def test_anything_that_could_be_an_alert_is_not_trivial(text: str) -> None:
    assert is_trivial_message(text) is False


def test_an_extraction_becomes_an_alert_with_its_identities() -> None:
    extraction = AlertExtraction(
        is_noise=False,
        alert_name="Payments pods restarting",
        severity=Severity.HIGH,
        source=AlertSource.DATADOG,
        service="payments-api",
        namespace="payments",
        error_message="exit code 137",
    )

    alert = alert_from_extraction(extraction, "payments pods keep restarting, exit code 137")

    assert alert.title == "Payments pods restarting"
    assert alert.severity is Severity.HIGH
    assert (alert.service, alert.namespace) == ("payments-api", "payments")
    assert alert.error == "exit code 137"
    assert alert.summary.startswith("payments pods keep restarting")


def test_an_extraction_without_a_name_is_titled_by_the_message() -> None:
    alert = alert_from_extraction(AlertExtraction(is_noise=False), "checkout 502s\nsince 10:00")

    assert alert.title == "checkout 502s"
```

- [ ] **Step 2: Verificar que fallan**

Run: `uv run pytest tests/domain/alerts/test_parsing.py tests/domain/alerts/test_noise.py -v`

Expected: `2 errors` durante la colección, con `No module named 'sre_graph.domain.alerts.parsing'` y `No module named 'sre_graph.domain.alerts.noise'`.

- [ ] **Step 3: Implementación**

`src/sre_graph/domain/alerts/parsing.py`:

```python
"""Turn any alert payload into a NormalizedAlert: detect the source, then parse it."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from typing import Any

from sre_graph.domain.alerts.model import (
    AlertParseError,
    AlertSource,
    NormalizedAlert,
    clean_text,
    fingerprint,
)
from sre_graph.domain.alerts.sources import (
    parse_alertmanager,
    parse_cloudwatch,
    parse_datadog,
    parse_generic,
    parse_grafana,
    parse_newrelic,
    parse_pagerduty,
    parse_sentry,
)

Parser = Callable[[Mapping[str, Any]], NormalizedAlert]

PARSERS: dict[AlertSource, Parser] = {
    AlertSource.ALERTMANAGER: parse_alertmanager,
    AlertSource.GRAFANA: parse_grafana,
    AlertSource.DATADOG: parse_datadog,
    AlertSource.CLOUDWATCH: parse_cloudwatch,
    AlertSource.SENTRY: parse_sentry,
    AlertSource.PAGERDUTY: parse_pagerduty,
    AlertSource.NEWRELIC: parse_newrelic,
    AlertSource.GENERIC: parse_generic,
}
_TITLE_LIMIT = 120
_TEXT_LIMIT = 4000


def detect_source(payload: Mapping[str, Any]) -> AlertSource:
    """The source whose payload shape ``payload`` has; GENERIC when none matches."""
    alerts = payload.get("alerts")
    if isinstance(alerts, list) and alerts:
        grafana_keys = ("dashboardURL", "panelURL", "silenceURL", "valueString")
        if "orgId" in payload or any(
            isinstance(item, Mapping) and any(key in item for key in grafana_keys)
            for item in alerts
        ):
            return AlertSource.GRAFANA
        if any(key in payload for key in ("groupKey", "receiver", "version")):
            return AlertSource.ALERTMANAGER
    if (
        "AlarmName" in payload
        or ("TopicArn" in payload and "Type" in payload)
        or clean_text(payload.get("detail-type")) == "CloudWatch Alarm State Change"
    ):
        return AlertSource.CLOUDWATCH
    event = payload.get("event")
    if isinstance(event, Mapping) and clean_text(event.get("event_type")).startswith("incident."):
        return AlertSource.PAGERDUTY
    data = payload.get("data")
    if (
        isinstance(data, Mapping)
        and "action" in payload
        and any(key in data for key in ("event", "issue", "metric_alert"))
    ) or ("culprit" in payload and "project_name" in payload):
        return AlertSource.SENTRY
    if any(key in payload for key in ("issueUrl", "alertPolicyNames", "impactedEntities")):
        return AlertSource.NEWRELIC
    if any(key in payload for key in ("alert_transition", "alert_type")) or (
        "event_type" in payload and "org" in payload
    ):
        return AlertSource.DATADOG
    return AlertSource.GENERIC


def parse_alert(
    payload: Mapping[str, Any] | str, *, source_hint: str | None = None
) -> NormalizedAlert:
    """Parse a webhook body or a pasted alert into a NormalizedAlert.

    ``source_hint`` (for example the source named in the webhook route) picks
    the parser directly; if that parser rejects the payload, detection runs as
    for an unhinted payload. Text that is not JSON becomes a GENERIC alert
    whose title is its first line.
    """
    if isinstance(payload, str):
        decoded = _json_object(payload)
        if decoded is None:
            return text_alert(payload)
        payload = decoded
    hint = clean_text(source_hint).lower()
    if hint in set(AlertSource) and hint != AlertSource.GENERIC:
        try:
            return PARSERS[AlertSource(hint)](payload)
        except AlertParseError:
            pass
    return PARSERS[detect_source(payload)](payload)


def text_alert(text: str) -> NormalizedAlert:
    """A GENERIC alert from free text: first line as title, the text as summary."""
    stripped = text.strip()
    if not stripped:
        raise AlertParseError("an empty message is not an alert")
    first_line = stripped.splitlines()[0].strip()
    title = first_line if len(first_line) <= _TITLE_LIMIT else first_line[: _TITLE_LIMIT - 1] + "…"
    return NormalizedAlert(
        source=AlertSource.GENERIC,
        title=title,
        fingerprint=fingerprint(AlertSource.GENERIC, stripped),
        summary=stripped[:_TEXT_LIMIT],
    )


def _json_object(text: str) -> dict[str, Any] | None:
    stripped = text.strip()
    if not stripped.startswith("{"):
        return None
    try:
        decoded = json.loads(stripped)
    except ValueError:
        return None
    return decoded if isinstance(decoded, dict) else None
```

`src/sre_graph/domain/alerts/noise.py`:

```python
"""Separate chat from alerts: a deterministic gate, then the schema the intake model fills."""

from __future__ import annotations

import re
from datetime import datetime

from pydantic import BaseModel, Field

from sre_graph.domain.alerts.model import (
    AlertSource,
    NormalizedAlert,
    Severity,
    clean_text,
    fingerprint,
)

_TRIVIAL_MESSAGES = frozenset(
    {
        "ok", "okay", "k", "thanks", "thank you", "thx", "ty", "hi", "hello", "hey", "hola",
        "gracias", "vale", "listo", "yes", "no", "si", "sí", "got it", "cool", "nice",
        "great", "perfect", "lgtm", "ack", "done",
    }
)  # fmt: skip
_NOT_WORDS = re.compile(r"[\W_]+")


def is_trivial_message(text: str) -> bool:
    """True for an empty message, bare punctuation/emoji, or a one-word ack or greeting.

    Anything else might be an alert, so it goes to the intake model, which
    decides with :class:`AlertExtraction`. Structured payloads never reach
    this gate: a webhook body is always an alert.
    """
    words = _NOT_WORDS.sub(" ", text).strip().lower()
    return not words or words in _TRIVIAL_MESSAGES


class AlertExtraction(BaseModel):
    """What the intake model reads out of a free-text message."""

    is_noise: bool = Field(
        description=(
            "True only for casual chat, greetings, thanks or replies to an earlier report. "
            "Any alert, error, failure, warning, incident or health check, even a healthy "
            "one, is not noise. When in doubt, false."
        )
    )
    alert_name: str = Field(default="", description="Short name of the alert or the problem.")
    severity: Severity = Field(default=Severity.WARNING, description="How urgent it is.")
    source: AlertSource = Field(
        default=AlertSource.GENERIC, description="The system that raised it, if named."
    )
    service: str | None = Field(default=None, description="Affected service or application.")
    namespace: str | None = Field(default=None, description="Kubernetes namespace, if named.")
    cluster: str | None = Field(default=None, description="Cluster name, if named.")
    workload: str | None = Field(default=None, description="Deployment or workload, if named.")
    pod: str | None = Field(default=None, description="Pod name, if named.")
    error_message: str = Field(default="", description="The error line quoted in the message.")
    started_at: datetime | None = Field(
        default=None, description="When the problem started, if the message says."
    )


def alert_from_extraction(extraction: AlertExtraction, text: str) -> NormalizedAlert:
    """The NormalizedAlert for a message the intake model judged to be an alert."""
    title = clean_text(extraction.alert_name) or clean_text(text).splitlines()[0][:120]
    return NormalizedAlert(
        source=extraction.source,
        title=title or "Alert",
        fingerprint=fingerprint(extraction.source, title, extraction.service),
        severity=extraction.severity,
        started_at=extraction.started_at,
        summary=clean_text(text)[:4000],
        error=clean_text(extraction.error_message),
        service=extraction.service,
        namespace=extraction.namespace,
        cluster=extraction.cluster,
        workload=extraction.workload,
        pod=extraction.pod,
    )
```

- [ ] **Step 4: Verificar que pasan**

Run: `uv run pytest tests/domain/alerts/test_parsing.py tests/domain/alerts/test_noise.py -v`

Expected: `33 passed`.

- [ ] **Step 5: Gate**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run lint-imports && SRE_GRAPH_TEST_DATABASE_URL="postgresql://sre_graph:sre_graph@localhost:5433/sre_graph?connect_timeout=5" SRE_GRAPH_REQUIRE_POSTGRES=1 uv run pytest
```

Expected: todo en verde y `323 passed`.

- [ ] **Step 6: Commit**

```bash
git add src/sre_graph/domain/alerts/parsing.py src/sre_graph/domain/alerts/noise.py tests/domain/alerts/test_parsing.py tests/domain/alerts/test_noise.py
git commit -m "feat(domain): source detection, alert parsing entry point and the noise gate" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Ventana del incidente

Todas las consultas con tiempo comparten una ventana semiabierta `[since, until)` en UTC. Termina 10 minutos después de que empezó la alerta (nunca después de ahora) y mira 2 horas hacia atrás. Si la evidencia vuelve vacía, la Fase 2 la expande ×2 sin mover el fin, con un máximo de 7 días.

**Files:**
- Create: `src/sre_graph/domain/window.py`
- Test: `tests/domain/test_window.py`

**Interfaces:**
- Consumes: nada.
- Produces:
  - Las constantes `DEFAULT_LOOKBACK` (120 min), `DEFAULT_FORWARD_BUFFER` (10 min), `MAX_LOOKBACK` (7 días) y `MIN_LOOKBACK` (1 min).
  - `class WindowSource(StrEnum)`: `alert`, `default`, `override`.
  - `class IncidentWindow(BaseModel)`, congelado, con `since`, `until` y `source`, que valida zona horaria y orden, y normaliza a UTC. Ofrece `lookback`, `contains(moment)` y `expanded(factor=2.0)`.
  - `resolve_window(started_at, *, now, lookback=DEFAULT_LOOKBACK, forward_buffer=DEFAULT_FORWARD_BUFFER) -> IncidentWindow`.

- [ ] **Step 1: Test que falla**

Crea `tests/domain/test_window.py`:

```python
from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from sre_graph.domain.window import (
    DEFAULT_LOOKBACK,
    MAX_LOOKBACK,
    IncidentWindow,
    WindowSource,
    resolve_window,
)

NOW = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)
STARTED = datetime(2026, 10, 7, 10, 0, tzinfo=UTC)


def test_an_alert_start_anchors_the_window_with_a_forward_buffer() -> None:
    window = resolve_window(STARTED, now=NOW)

    assert window.until == STARTED + timedelta(minutes=10)
    assert window.since == window.until - DEFAULT_LOOKBACK
    assert window.source is WindowSource.ALERT


def test_without_a_start_the_window_ends_now() -> None:
    window = resolve_window(None, now=NOW)

    assert (window.since, window.until, window.source) == (
        NOW - DEFAULT_LOOKBACK,
        NOW,
        WindowSource.DEFAULT,
    )


def test_a_start_in_the_future_is_clamped_to_now() -> None:
    window = resolve_window(NOW + timedelta(hours=1), now=NOW)

    assert window.until == NOW


def test_lookback_is_kept_between_one_minute_and_seven_days() -> None:
    assert resolve_window(None, now=NOW, lookback=timedelta(days=30)).lookback == MAX_LOOKBACK
    assert resolve_window(None, now=NOW, lookback=timedelta(0)).lookback == timedelta(minutes=1)


def test_naive_inputs_are_read_as_utc() -> None:
    window = resolve_window(STARTED.replace(tzinfo=None), now=NOW.replace(tzinfo=None))

    assert window.until == STARTED + timedelta(minutes=10)


def test_other_timezones_are_normalized_to_utc() -> None:
    bogota = timezone(timedelta(hours=-5))
    window = IncidentWindow(
        since=datetime(2026, 10, 7, 5, 0, tzinfo=bogota),
        until=datetime(2026, 10, 7, 7, 0, tzinfo=bogota),
        source=WindowSource.OVERRIDE,
    )

    assert window.since == STARTED
    assert window.since.tzinfo == UTC


@pytest.mark.parametrize(
    ("since", "until"),
    [(NOW, NOW), (NOW, NOW - timedelta(minutes=1)), (NOW.replace(tzinfo=None), NOW + timedelta(1))],
)
def test_empty_inverted_or_naive_windows_are_rejected(since: datetime, until: datetime) -> None:
    with pytest.raises(ValueError):
        IncidentWindow(since=since, until=until, source=WindowSource.OVERRIDE)


def test_the_window_is_half_open() -> None:
    window = resolve_window(STARTED, now=NOW)

    assert window.contains(window.since)
    assert not window.contains(window.until)


def test_expanding_moves_only_the_start_and_stops_at_seven_days() -> None:
    window = resolve_window(STARTED, now=NOW)

    wider = window.expanded()

    assert wider.until == window.until
    assert wider.lookback == 2 * window.lookback
    capped = resolve_window(None, now=NOW, lookback=MAX_LOOKBACK)
    assert capped.expanded() is capped


def test_expanding_by_one_or_less_is_an_error() -> None:
    with pytest.raises(ValueError, match="greater than 1"):
        resolve_window(None, now=NOW).expanded(1.0)


def test_the_window_round_trips_through_json() -> None:
    window = resolve_window(STARTED, now=NOW)

    assert IncidentWindow.model_validate(window.model_dump(mode="json")) == window
```

- [ ] **Step 2: Verificar que falla**

Run: `uv run pytest tests/domain/test_window.py -v`

Expected: `1 error` durante la colección, con `ModuleNotFoundError: No module named 'sre_graph.domain.window'`.

- [ ] **Step 3: Implementación**

`src/sre_graph/domain/window.py`:

```python
"""The incident time window every time-aware query shares."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, model_validator

DEFAULT_LOOKBACK = timedelta(minutes=120)
DEFAULT_FORWARD_BUFFER = timedelta(minutes=10)
MAX_LOOKBACK = timedelta(days=7)
MIN_LOOKBACK = timedelta(minutes=1)


class WindowSource(StrEnum):
    """Where the window's end came from."""

    ALERT = "alert"
    DEFAULT = "default"
    OVERRIDE = "override"


class IncidentWindow(BaseModel):
    """A half-open ``[since, until)`` interval in UTC.

    Evidence stamped exactly at ``since`` is inside; at ``until`` it is not,
    matching the range semantics of Loki, Prometheus and Datadog queries.
    """

    model_config = ConfigDict(frozen=True)

    since: datetime
    until: datetime
    source: WindowSource

    @model_validator(mode="after")
    def _utc_and_ordered(self) -> Self:
        if self.since.tzinfo is None or self.until.tzinfo is None:
            raise ValueError("an incident window needs timezone-aware datetimes")
        since, until = self.since.astimezone(UTC), self.until.astimezone(UTC)
        if since >= until:
            raise ValueError("an incident window needs since < until")
        object.__setattr__(self, "since", since)
        object.__setattr__(self, "until", until)
        return self

    @property
    def lookback(self) -> timedelta:
        """How much time the window covers."""
        return self.until - self.since

    def contains(self, moment: datetime) -> bool:
        """Whether ``moment`` falls inside the window (half-open)."""
        return self.since <= moment.astimezone(UTC) < self.until

    def expanded(self, factor: float = 2.0) -> IncidentWindow:
        """The same window reaching ``factor`` times further back, at most 7 days.

        ``until`` stays put: widening admits the lookback guess was too short,
        not that the incident moved. Returns ``self`` when already at the cap.
        """
        if factor <= 1.0:
            raise ValueError("expanded() needs a factor greater than 1")
        lookback = min(self.lookback * factor, MAX_LOOKBACK)
        if lookback <= self.lookback:
            return self
        return IncidentWindow(since=self.until - lookback, until=self.until, source=self.source)


def resolve_window(
    started_at: datetime | None,
    *,
    now: datetime,
    lookback: timedelta = DEFAULT_LOOKBACK,
    forward_buffer: timedelta = DEFAULT_FORWARD_BUFFER,
) -> IncidentWindow:
    """The window for an alert that started at ``started_at`` (None: unknown start).

    With a start, the window ends ``forward_buffer`` after it (never after
    ``now``, which absorbs clock skew) and reaches ``lookback`` back. Without
    one it ends now. ``lookback`` is kept between 1 minute and 7 days.
    """
    current = (now if now.tzinfo else now.replace(tzinfo=UTC)).astimezone(UTC)
    reach = min(max(lookback, MIN_LOOKBACK), MAX_LOOKBACK)
    if started_at is None:
        return IncidentWindow(since=current - reach, until=current, source=WindowSource.DEFAULT)
    start = (started_at if started_at.tzinfo else started_at.replace(tzinfo=UTC)).astimezone(UTC)
    until = min(start + max(forward_buffer, timedelta(0)), current)
    return IncidentWindow(since=until - reach, until=until, source=WindowSource.ALERT)
```

- [ ] **Step 4: Verificar que pasa**

Run: `uv run pytest tests/domain/test_window.py -v`

Expected: `13 passed`.

- [ ] **Step 5: Gate**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run lint-imports && SRE_GRAPH_TEST_DATABASE_URL="postgresql://sre_graph:sre_graph@localhost:5433/sre_graph?connect_timeout=5" SRE_GRAPH_REQUIRE_POSTGRES=1 uv run pytest
```

Expected: todo en verde y `336 passed`.

- [ ] **Step 6: Commit**

```bash
git add src/sre_graph/domain/window.py tests/domain/test_window.py
git commit -m "feat(domain): incident window with alert anchoring and bounded expansion" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Taxonomía de causa raíz

Las 96 categorías generales de opensre, en 10 grupos, con nombres idénticos a los de sus escenarios de evaluación. Las acompañan tres funciones:
- **Normalización:** tolera mayúsculas, espacios, guiones y alias, y lo que no reconoce termina en `unknown`.
- **Render para prompts:** lista la taxonomía por grupo.
- **Chequeo de coherencia:** detecta cuando el texto de la causa raíz habla de otro grupo.

**Files:**
- Create: `src/sre_graph/domain/taxonomy.py`
- Test: `tests/domain/test_taxonomy.py`

**Interfaces:**
- Consumes: nada.
- Produces:
  - `class CategoryGroup(StrEnum)`, con los grupos `database`, `kubernetes_workload`, `network_and_dns`, `cloud_storage`, `external_dependency`, `code_and_configuration`, `data_and_pipeline`, `workload_and_traffic`, `infrastructure` y `generic_fallback`.
  - `@dataclass(frozen=True) class RootCauseCategory(name, group, description)`.
  - Constantes: `HEALTHY = "healthy"`, `UNKNOWN = "unknown"`, `CATEGORIES: tuple[RootCauseCategory, ...]` (96 entradas) y `CATEGORY_NAMES: frozenset[str]`.
  - Funciones:
    - `category(name) -> RootCauseCategory | None`;
    - `normalize_category(raw, allowed=None) -> str`;
    - `render_taxonomy(names=None) -> str`;
    - `category_text_mismatch(root_cause, category_name) -> str | None`.

- [ ] **Step 1: Test que falla**

Crea `tests/domain/test_taxonomy.py`:

```python
from __future__ import annotations

import pytest

from sre_graph.domain.taxonomy import (
    CATEGORIES,
    CATEGORY_NAMES,
    HEALTHY,
    UNKNOWN,
    CategoryGroup,
    category,
    category_text_mismatch,
    normalize_category,
    render_taxonomy,
)


def test_the_taxonomy_has_the_ninety_six_general_categories_with_unique_names() -> None:
    assert len(CATEGORIES) == 96
    assert len(CATEGORY_NAMES) == 96
    assert {HEALTHY, UNKNOWN, "pod_oomkilled", "resource_exhaustion"} <= CATEGORY_NAMES


def test_categories_are_looked_up_by_name() -> None:
    oom = category("pod_oomkilled")

    assert oom is not None
    assert oom.group is CategoryGroup.KUBERNETES
    assert category("not_a_category") is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("pod_oomkilled", "pod_oomkilled"),
        (" Pod OOMKilled ", "pod_oomkilled"),
        ("dns-resolution-failure", "dns_resolution_failure"),
        ("oom_killed", "pod_oomkilled"),
        ("config_error", "configuration_error"),
        ("made_up_category", UNKNOWN),
        ("", UNKNOWN),
    ],
)
def test_model_labels_are_normalized_and_fail_closed(raw: str, expected: str) -> None:
    assert normalize_category(raw) == expected


def test_a_category_outside_the_allowed_set_becomes_unknown() -> None:
    assert normalize_category("pod_oomkilled", allowed={"connection_exhaustion"}) == UNKNOWN


def test_the_prompt_taxonomy_lists_groups_in_order_with_descriptions() -> None:
    rendered = render_taxonomy({"pod_oomkilled", "connection_exhaustion", "unknown"})

    lines = rendered.splitlines()
    assert lines[0] == "### database"
    assert lines[1].startswith("- connection_exhaustion: ")
    assert "### kubernetes_workload" in lines
    assert lines[-1].startswith("- unknown: ")


def test_root_cause_text_from_another_group_is_flagged() -> None:
    text = "Postgres max_connections was exhausted by a connection pool leak."

    reason = category_text_mismatch(text, "pod_oomkilled")

    assert reason is not None
    assert "database" in reason


@pytest.mark.parametrize(
    ("text", "name"),
    [
        (
            "Postgres max_connections was exhausted by a connection pool leak.",
            "connection_pool_leak",
        ),
        ("The pod was OOMKilled after a memory leak.", "pod_oomkilled"),
        ("A bad deploy changed the postgres connection pool size.", "bad_deploy"),
        ("Everything is fine.", HEALTHY),
        ("Only one postgres mention.", "pod_oomkilled"),
    ],
)
def test_text_that_agrees_or_is_inconclusive_is_not_flagged(text: str, name: str) -> None:
    assert category_text_mismatch(text, name) is None
```

- [ ] **Step 2: Verificar que falla**

Run: `uv run pytest tests/domain/test_taxonomy.py -v`

Expected: `1 error` durante la colección, con `ModuleNotFoundError: No module named 'sre_graph.domain.taxonomy'`.

- [ ] **Step 3: Implementación**

`src/sre_graph/domain/taxonomy.py`. La tabla `CATEGORIES` reproduce, entrada por entrada, las categorías de scope general de `ce77b4a0b^:core/domain/types/root_cause_categories.py` de opensre:

```python
"""Root-cause categories: the one taxonomy the diagnosis, the report and the evals share.

A category is deliberately narrow, so it points at the failing subsystem
("storage IOPS throttling", "OOMKilled") rather than a bucket that matches a
dozen failure modes. The generic group exists for when evidence cannot
support a narrower answer. Ported from OpenSRE's taxonomy; the names match
its evaluation scenarios exactly.
"""

from __future__ import annotations

import re
from collections.abc import Collection
from dataclasses import dataclass
from enum import StrEnum


class CategoryGroup(StrEnum):
    """Groups that order the taxonomy in prompts and reports."""

    DATABASE = "database"
    KUBERNETES = "kubernetes_workload"
    NETWORK = "network_and_dns"
    CLOUD_STORAGE = "cloud_storage"
    DEPENDENCY = "external_dependency"
    CODE_AND_CONFIG = "code_and_configuration"
    DATA_PIPELINE = "data_and_pipeline"
    WORKLOAD = "workload_and_traffic"
    INFRASTRUCTURE = "infrastructure"
    GENERIC = "generic_fallback"


@dataclass(frozen=True)
class RootCauseCategory:
    """One category: a stable name, its group, and a one-line meaning."""

    name: str
    group: CategoryGroup
    description: str


HEALTHY = "healthy"
UNKNOWN = "unknown"

_D = CategoryGroup.DATABASE
_K = CategoryGroup.KUBERNETES
_N = CategoryGroup.NETWORK
_S = CategoryGroup.CLOUD_STORAGE
_X = CategoryGroup.DEPENDENCY
_C = CategoryGroup.CODE_AND_CONFIG
_P = CategoryGroup.DATA_PIPELINE
_W = CategoryGroup.WORKLOAD
_I = CategoryGroup.INFRASTRUCTURE
_G = CategoryGroup.GENERIC

CATEGORIES: tuple[RootCauseCategory, ...] = (
    RootCauseCategory(
        "connection_exhaustion", _D, "max_connections ceiling reached; new sessions are rejected."
    ),
    RootCauseCategory(
        "connection_pool_leak", _D, "Application connection pool acquires faster than it releases."
    ),
    RootCauseCategory(
        "idle_in_transaction_session_leak",
        _D,
        "Sessions stuck idle-in-transaction hold locks and slots.",
    ),
    RootCauseCategory(
        "max_connections_misconfigured",
        _D,
        "Server-side max_connections value is too low for current load shape.",
    ),
    RootCauseCategory(
        "cpu_saturation_bad_query",
        _D,
        "One hot SQL pattern (missing index, full scan) saturates CPU.",
    ),
    RootCauseCategory(
        "cpu_saturation_workload_burst",
        _D,
        "Aggregate workload increase saturates CPU without one dominant query.",
    ),
    RootCauseCategory(
        "query_plan_regression",
        _D,
        "Planner choice changed (stats, parameter, version) and degraded.",
    ),
    RootCauseCategory(
        "stale_statistics", _D, "Outdated planner stats produce inefficient plans on changed data."
    ),
    RootCauseCategory(
        "missing_index", _D, "Required selective index absent; sequential scans dominate IO/CPU."
    ),
    RootCauseCategory(
        "lock_contention", _D, "Row/table locks queue; queries wait without saturating compute."
    ),
    RootCauseCategory(
        "deadlock_storm", _D, "Repeated deadlock cycles drive transaction failure rate."
    ),
    RootCauseCategory(
        "storage_exhaustion", _D, "Disk full; writes block, FreeStorageSpace approaches zero."
    ),
    RootCauseCategory(
        "storage_iops_throttling",
        _D,
        "EBS/storage IOPS or throughput limit reached, queueing reads/writes.",
    ),
    RootCauseCategory(
        "storage_burst_balance_depleted",
        _D,
        "gp2-style burst balance exhausted; baseline IOPS too low for load.",
    ),
    RootCauseCategory(
        "checkpoint_io_storm",
        _D,
        "Bursty checkpoint flushes saturate write I/O (LWLock:BufferMapping).",
    ),
    RootCauseCategory(
        "vacuum_freeze_storm", _D, "Autovacuum FREEZE on a large table dominates load and locks."
    ),
    RootCauseCategory(
        "autovacuum_blocked", _D, "Autovacuum cannot keep up due to long transactions or settings."
    ),
    RootCauseCategory(
        "transaction_id_wraparound_pressure",
        _D,
        "MaximumUsedTransactionIDs nearing wraparound; reads/writes degrade.",
    ),
    RootCauseCategory(
        "table_bloat", _D, "Heavy update/delete bloat degrades scans and inflates IO."
    ),
    RootCauseCategory(
        "replication_lag_wal_volume", _D, "Primary generates WAL faster than replica can replay."
    ),
    RootCauseCategory(
        "replication_lag_long_query_on_replica",
        _D,
        "Long-running query on replica blocks WAL apply.",
    ),
    RootCauseCategory(
        "replication_lag_replica_undersized",
        _D,
        "Replica instance class too small to keep up with steady write rate.",
    ),
    RootCauseCategory(
        "wal_archiving_failure", _D, "WAL archive backlog grows; threatens primary availability."
    ),
    RootCauseCategory(
        "failover_event", _D, "Multi-AZ or replica failover; outage explained by topology change."
    ),
    RootCauseCategory(
        "dual_resource_exhaustion",
        _D,
        "Two independent DB resources saturate simultaneously (compositional).",
    ),
    RootCauseCategory(
        "pod_oomkilled", _K, "Container exceeded memory limit and was killed by the kubelet."
    ),
    RootCauseCategory(
        "pod_cpu_throttled", _K, "Container hit CPU limit and is throttled; latency rises."
    ),
    RootCauseCategory(
        "pod_evicted_node_pressure", _K, "Pod evicted due to node memory/disk/PID pressure."
    ),
    RootCauseCategory(
        "pod_crashloop_backoff",
        _K,
        "Container exits non-zero on start; kubelet backs off restarts.",
    ),
    RootCauseCategory(
        "pod_imagepull_backoff", _K, "Image pull fails (auth, missing tag, registry outage)."
    ),
    RootCauseCategory(
        "pod_pending_insufficient_resources",
        _K,
        "Scheduler cannot place pod due to CPU/mem/quota constraints.",
    ),
    RootCauseCategory(
        "pod_pending_unschedulable",
        _K,
        "Scheduler cannot place pod due to taints/affinity/topology.",
    ),
    RootCauseCategory(
        "liveness_probe_misconfigured", _K, "Liveness probe kills otherwise-healthy containers."
    ),
    RootCauseCategory(
        "readiness_probe_misconfigured",
        _K,
        "Readiness probe failure removes pods from Service endpoints.",
    ),
    RootCauseCategory(
        "node_not_ready", _K, "Node went NotReady (kubelet, container runtime, network)."
    ),
    RootCauseCategory(
        "service_endpoint_mismatch",
        _K,
        "Service selector / target port doesn't match pod labels/ports.",
    ),
    RootCauseCategory(
        "deployment_rollout_stuck", _K, "Rollout blocked before any new pod becomes ready."
    ),
    RootCauseCategory(
        "hpa_misconfiguration", _K, "HPA cannot scale (missing metrics, wrong target)."
    ),
    RootCauseCategory(
        "pdb_blocking_drain", _K, "PodDisruptionBudget prevents node drain or scale-in."
    ),
    RootCauseCategory(
        "ingress_misconfiguration", _K, "Ingress/route rules don't match desired traffic path."
    ),
    RootCauseCategory(
        "dns_resolution_failure", _N, "Service or upstream DNS resolution fails or times out."
    ),
    RootCauseCategory(
        "tls_certificate_expired", _N, "Certificate expiry or rotation issue breaks TLS handshakes."
    ),
    RootCauseCategory(
        "mtls_handshake_failure", _N, "Mutual TLS misconfiguration prevents handshake completion."
    ),
    RootCauseCategory(
        "security_group_misconfiguration", _N, "VPC/SG/NACL rule blocks required traffic."
    ),
    RootCauseCategory(
        "load_balancer_unhealthy_targets",
        _N,
        "ALB/NLB/ELB target group health check failures drive traffic loss.",
    ),
    RootCauseCategory(
        "nat_gateway_throttling", _N, "NAT gateway port allocation or throughput limit reached."
    ),
    RootCauseCategory(
        "network_partition", _N, "Connectivity loss between subnets, VPCs, or regions."
    ),
    RootCauseCategory(
        "s3_object_missing", _S, "Expected S3 object missing (timing, prefix, or upstream skipped)."
    ),
    RootCauseCategory(
        "s3_access_denied", _S, "S3 permissions/IAM policy block expected reads or writes."
    ),
    RootCauseCategory(
        "s3_throttling", _S, "S3 prefix throttling (503 SlowDown) limits throughput."
    ),
    RootCauseCategory("ebs_volume_full", _S, "Attached EBS volume reached capacity; writes fail."),
    RootCauseCategory(
        "upstream_service_outage", _X, "External service is unavailable / SLO breached."
    ),
    RootCauseCategory(
        "upstream_schema_change", _X, "Upstream API contract changed and broke consumers."
    ),
    RootCauseCategory(
        "upstream_rate_limit", _X, "Upstream throttling (HTTP 429) is the proximate cause."
    ),
    RootCauseCategory(
        "upstream_authentication_failure",
        _X,
        "Token / cred rotation / expiry breaks downstream auth.",
    ),
    RootCauseCategory(
        "third_party_breaking_change",
        _X,
        "Third-party SDK or service made a backward-incompatible change.",
    ),
    RootCauseCategory(
        "bad_deploy", _C, "A specific deploy introduced the failure; rollback would restore."
    ),
    RootCauseCategory(
        "feature_flag_misconfiguration",
        _C,
        "Flag enables a broken path in the wrong env or cohort.",
    ),
    RootCauseCategory(
        "env_var_missing", _C, "Required env var not set; service fails on startup or first use."
    ),
    RootCauseCategory(
        "env_var_misconfiguration",
        _C,
        "Env var value points at wrong endpoint / region / credential.",
    ),
    RootCauseCategory(
        "secret_missing", _C, "Required secret not mounted/available to the workload."
    ),
    RootCauseCategory("secret_rotation_failure", _C, "Rotation produced stale or invalid secrets."),
    RootCauseCategory(
        "code_defect_null_handling",
        _C,
        "Unhandled null/None path breaks under specific input shape.",
    ),
    RootCauseCategory(
        "code_defect_concurrency_bug",
        _C,
        "Race / lost-update / deadlock surfaces under concurrent load.",
    ),
    RootCauseCategory(
        "code_defect_resource_leak", _C, "FD / connection / memory leak grows until exhaustion."
    ),
    RootCauseCategory(
        "code_defect_serialization", _C, "Encoder/decoder bug corrupts payloads or fails parse."
    ),
    RootCauseCategory(
        "data_schema_drift", _P, "Upstream schema changed; downstream consumers reject new shape."
    ),
    RootCauseCategory(
        "data_missing_required_field",
        _P,
        "Records missing a field declared as required by the consumer.",
    ),
    RootCauseCategory(
        "data_corrupted_record", _P, "Malformed bytes/values cause parse errors mid-pipeline."
    ),
    RootCauseCategory(
        "data_late_arrival", _P, "Expected partition / batch never arrived in time window."
    ),
    RootCauseCategory(
        "data_volume_anomaly", _P, "Surprise volume change (zero or huge) trips downstream limits."
    ),
    RootCauseCategory(
        "data_partition_skew", _P, "One partition holds disproportionate volume; hot-spots workers."
    ),
    RootCauseCategory("job_timeout", _P, "Job exceeded scheduler / runtime timeout."),
    RootCauseCategory(
        "job_dependency_failure", _P, "Upstream job failed; downstream cannot proceed."
    ),
    RootCauseCategory(
        "lambda_concurrent_executions_exceeded",
        _P,
        "Lambda hit account/function concurrency limit; invocations throttled.",
    ),
    RootCauseCategory("lambda_init_timeout", _P, "Cold start / init phase exceeded timeout."),
    RootCauseCategory(
        "application_tier_load_spike",
        _W,
        "Application tier traffic surge propagates pressure downstream.",
    ),
    RootCauseCategory(
        "traffic_burst_unprotected",
        _W,
        "Real traffic spike exceeds rate limits / autoscale headroom.",
    ),
    RootCauseCategory("abusive_traffic", _W, "Bot or scraper traffic dominates capacity."),
    RootCauseCategory(
        "ddos_event", _W, "Distributed denial-of-service traffic pattern is the proximate cause."
    ),
    RootCauseCategory(
        "cascading_failure", _W, "Backpressure between services cascades into broader degradation."
    ),
    RootCauseCategory(
        "az_outage", _I, "Single availability zone degraded; symptoms scoped to that AZ."
    ),
    RootCauseCategory("region_outage", _I, "Region-wide cloud provider event."),
    RootCauseCategory(
        "cloud_provider_event", _I, "Confirmed provider service event published on the status page."
    ),
    RootCauseCategory(
        "iam_policy_misconfiguration", _I, "IAM/role/policy denies an action the workload requires."
    ),
    RootCauseCategory(
        "service_quota_exceeded", _I, "AWS/cloud account-level quota or limit reached."
    ),
    RootCauseCategory(
        "configuration_error", _G, "Generic configuration root cause; prefer a narrower category."
    ),
    RootCauseCategory(
        "code_defect", _G, "Generic code defect; prefer a narrower code_defect_* category."
    ),
    RootCauseCategory(
        "data_quality", _G, "Generic data quality root cause; prefer a narrower data_* category."
    ),
    RootCauseCategory(
        "resource_exhaustion",
        _G,
        "Generic resource exhaustion fallback; prefer a narrower exhaustion category.",
    ),
    RootCauseCategory(
        "dependency_failure",
        _G,
        "Generic dependency failure; prefer a narrower upstream_* category.",
    ),
    RootCauseCategory(
        "infrastructure",
        _G,
        "Generic infrastructure fallback; prefer a narrower infrastructure category.",
    ),
    RootCauseCategory(
        "cpu_saturation",
        _G,
        ("Generic CPU saturation fallback; prefer cpu_saturation_bad_query / workload_burst."),
    ),
    RootCauseCategory(
        "replication_lag",
        _G,
        (
            "Generic replication lag fallback; prefer replication_lag_wal_volume / "
            "long_query_on_replica."
        ),
    ),
    RootCauseCategory(
        "healthy", _G, "All metrics within normal bounds; alert is informational or stale."
    ),
    RootCauseCategory("unknown", _G, "Insufficient evidence to commit to a category."),
)

CATEGORY_NAMES: frozenset[str] = frozenset(category.name for category in CATEGORIES)
_BY_NAME: dict[str, RootCauseCategory] = {category.name: category for category in CATEGORIES}

#: Adjacent labels older prompts or models emit, mapped onto a real category.
_ALIASES: dict[str, str] = {
    "code_bug": "code_defect_null_handling",
    "config_error": "configuration_error",
    "configuration": "configuration_error",
    "connection_pool_exhaustion": "connection_exhaustion",
    "cpu_throttling": "pod_cpu_throttled",
    "database": "connection_exhaustion",
    "database_connection_failure": "connection_exhaustion",
    "dns_failure": "dns_resolution_failure",
    "memory_pressure": "pod_oomkilled",
    "mysql_connection_pool_exhaustion": "connection_pool_leak",
    "network_delay": "network_partition",
    "network_latency_issue": "network_partition",
    "oom_killed": "pod_oomkilled",
    "oomkilled": "pod_oomkilled",
    "performance": "application_tier_load_spike",
    "pod_cpu_overload": "pod_cpu_throttled",
    "pod_oom_killed": "pod_oomkilled",
    "redis_connection_pool_exhaustion": "connection_pool_leak",
}
_SEPARATORS = re.compile(r"[\s\-/]+")

#: Keywords that strongly signal a group; two hits are needed to call a mismatch.
_GROUP_SIGNALS: dict[CategoryGroup, tuple[str, ...]] = {
    CategoryGroup.DATABASE: (
        "postgres", "postgresql", "mysql", "mariadb", "redis", "connection pool",
        "max_connections", "replication lag", "slow query", "sql database",
    ),
    CategoryGroup.KUBERNETES: (
        "oomkilled", "oom killed", "crashloop", "crash loop", "kubelet", "liveness probe",
        "readiness probe", "imagepull", "evicted",
    ),
    CategoryGroup.NETWORK: (
        "dns resolution", "dns failure", "tls certificate", "security group", "nat gateway",
        "network partition",
    ),
}  # fmt: skip
_MIN_SIGNAL_HITS = 2


def category(name: str) -> RootCauseCategory | None:
    """The category called ``name``, if it exists."""
    return _BY_NAME.get(name)


def normalize_category(raw: str, allowed: Collection[str] | None = None) -> str:
    """Map a model's category label onto an allowed category, or ``unknown``.

    Fails closed: a label outside the taxonomy is never kept. Case, spaces,
    dashes and known aliases are tolerated.
    """
    names = CATEGORY_NAMES if allowed is None else frozenset(allowed) & CATEGORY_NAMES
    cleaned = raw.strip()
    token = _SEPARATORS.sub("_", cleaned.lower()).strip("_")
    for candidate in (cleaned, token, _ALIASES.get(token, "")):
        if candidate in names:
            return candidate
    return UNKNOWN


def render_taxonomy(names: Collection[str] | None = None) -> str:
    """The taxonomy as a prompt section: groups in order, ``- name: description`` lines."""
    wanted = CATEGORY_NAMES if names is None else frozenset(names)
    lines: list[str] = []
    for group in CategoryGroup:
        members = [entry for entry in CATEGORIES if entry.group is group and entry.name in wanted]
        if members:
            lines.append(f"### {group.value}")
            lines.extend(f"- {entry.name}: {entry.description}" for entry in members)
    return "\n".join(lines)


def category_text_mismatch(root_cause: str, category_name: str) -> str | None:
    """Why the root-cause text seems to belong to another group, or None when it agrees.

    Only groups with strong keyword signals are compared, and the category's
    own words appearing in the text count as agreement.
    """
    entry = _BY_NAME.get(category_name.strip())
    if entry is None or entry.name in (HEALTHY, UNKNOWN) or entry.group not in _GROUP_SIGNALS:
        return None
    text = root_cause.lower()
    scores = {
        group: sum(keyword in text for keyword in keywords)
        for group, keywords in _GROUP_SIGNALS.items()
    }
    best = max(scores, key=lambda group: scores[group])
    if scores[best] < _MIN_SIGNAL_HITS or best is entry.group:
        return None
    if any(token in text for token in entry.name.split("_") if len(token) >= 3):
        return None
    return (
        f"the root cause reads as {best.value} ({scores[best]} keyword hits) "
        f"but {entry.name!r} is {entry.group.value}"
    )
```

- [ ] **Step 4: Verificar que pasa**

Run: `uv run pytest tests/domain/test_taxonomy.py -v`

Expected: `17 passed`.

- [ ] **Step 5: Gate**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run lint-imports && SRE_GRAPH_TEST_DATABASE_URL="postgresql://sre_graph:sre_graph@localhost:5433/sre_graph?connect_timeout=5" SRE_GRAPH_REQUIRE_POSTGRES=1 uv run pytest
```

Expected: todo en verde y `353 passed`.

- [ ] **Step 6: Commit**

```bash
git add src/sre_graph/domain/taxonomy.py tests/domain/test_taxonomy.py
git commit -m "feat(domain): root-cause taxonomy with normalization and text alignment check" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Evidencia y plan de investigación

**Evidencia.**
- Cada resultado de herramienta que importa se guarda como `EvidenceItem`, bajo un id determinista que los claims citan.
- El reducer `merge_evidence` deja una entrada por id en el estado del grafo.

**Plan de investigación.**
- El planner propone hipótesis y tareas por especialista.
- `constrain_plan` deja solo lo ejecutable.
- `fallback_plan` arma un plan determinista si el planner falla, con Kubernetes primero si la alerta nombra un pod.

**Hallazgos.** Los especialistas devuelven `SpecialistFinding`; si fallan, `degraded_finding`, para que la investigación siga.

**Files:**
- Create: `src/sre_graph/domain/evidence.py`, `src/sre_graph/domain/investigation.py`
- Test: `tests/domain/test_evidence.py`, `tests/domain/test_investigation.py`

**Interfaces:**
- Consumes: `Domain` y `EvidenceType` (`domain/tools.py`, Fase 1a); `AlertSource` y `NormalizedAlert` (Tarea 1).
- Produces:
  - En `evidence.py`:
    - `class EvidenceItem(BaseModel)`, congelado, con `id`, `integration`, `tool`, `evidence_type`, `domains`, `summary`, `args`, `data`, `collected_at` y `error`;
    - `evidence_id(tool, args) -> str` (`ev_` + 10 hex);
    - `merge_evidence(current, new) -> list[dict[str, Any]]`.
  - En `investigation.py`:
    - las constantes `MAX_HYPOTHESES = 4` y `SOURCE_DOMAINS: dict[AlertSource, tuple[Domain, ...]]`;
    - los modelos `Hypothesis(id, statement, confirm_if, rule_out_if)`, `SpecialistTask(domain, objective, hypothesis_ids)` e `InvestigationPlan(hypotheses, tasks)`;
    - `Verdict(StrEnum)`: `supports`, `refutes`, `inconclusive`;
    - `HypothesisVerdict(hypothesis_id, verdict, reason, evidence_ids)` y `SpecialistFinding(domain, summary, verdicts, evidence_ids, confidence, gaps)`;
    - `degraded_finding(domain, error) -> SpecialistFinding`;
    - `constrain_plan(plan, available, max_tasks) -> InvestigationPlan` y `fallback_plan(alert, available, max_tasks) -> InvestigationPlan`.

- [ ] **Step 1: Tests que fallan**

Crea `tests/domain/test_evidence.py`:

```python
from __future__ import annotations

from datetime import UTC, datetime

from sre_graph.domain.evidence import EvidenceItem, evidence_id, merge_evidence
from sre_graph.domain.tools import Domain, EvidenceType


def test_the_same_call_gets_the_same_id_whatever_the_argument_order() -> None:
    first = evidence_id("k8s_list_pods", {"namespace": "payments", "label_selector": "app=a"})
    second = evidence_id("k8s_list_pods", {"label_selector": "app=a", "namespace": "payments"})

    assert first == second
    assert first.startswith("ev_")
    assert len(first) == 13


def test_different_calls_get_different_ids() -> None:
    pods = evidence_id("k8s_list_pods", {"namespace": "payments"})

    assert pods != evidence_id("k8s_list_pods", {"namespace": "checkout"})
    assert pods != evidence_id("k8s_list_events", {"namespace": "payments"})


def test_merging_appends_new_ids_and_refreshes_repeated_ones_in_place() -> None:
    current = [{"id": "ev_1", "summary": "old"}, {"id": "ev_2", "summary": "two"}]
    new = [{"id": "ev_3", "summary": "three"}, {"id": "ev_1", "summary": "fresh"}]

    merged = merge_evidence(current, new)

    assert [item["id"] for item in merged] == ["ev_1", "ev_2", "ev_3"]
    assert merged[0]["summary"] == "fresh"


def test_merging_tolerates_an_empty_side() -> None:
    assert merge_evidence(None, [{"id": "ev_1"}]) == [{"id": "ev_1"}]
    assert merge_evidence([{"id": "ev_1"}], None) == [{"id": "ev_1"}]


def test_an_evidence_item_round_trips_through_json() -> None:
    item = EvidenceItem(
        id=evidence_id("k8s_list_pods", {"namespace": "payments"}),
        integration="kubernetes",
        tool="k8s_list_pods",
        evidence_type=EvidenceType.TOPOLOGY,
        domains=[Domain.KUBERNETES],
        summary="3 pods, 1 in CrashLoopBackOff",
        args={"namespace": "payments"},
        data=[{"name": "checkout-7d9f8b6c5-x2x9z"}],
        collected_at=datetime(2026, 10, 7, 10, 6, tzinfo=UTC),
    )

    assert EvidenceItem.model_validate(item.model_dump(mode="json")) == item
```

Crea `tests/domain/test_investigation.py`:

```python
from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from sre_graph.domain.alerts.model import AlertSource, NormalizedAlert
from sre_graph.domain.investigation import (
    Hypothesis,
    InvestigationPlan,
    SpecialistFinding,
    SpecialistTask,
    constrain_plan,
    degraded_finding,
    fallback_plan,
)
from sre_graph.domain.tools import Domain

ALL_DOMAINS = set(Domain)


def _hypothesis(identifier: str) -> Hypothesis:
    return Hypothesis(
        id=identifier, statement=f"{identifier} holds", confirm_if="seen", rule_out_if="not seen"
    )


def _alert(source: AlertSource = AlertSource.ALERTMANAGER, **fields: Any) -> NormalizedAlert:
    return NormalizedAlert(source=source, title="KubePodCrashLooping", fingerprint="f", **fields)


def test_tasks_for_unavailable_domains_are_dropped() -> None:
    plan = InvestigationPlan(
        hypotheses=[_hypothesis("H1")],
        tasks=[
            SpecialistTask(domain=Domain.KUBERNETES, objective="pods", hypothesis_ids=["H1"]),
            SpecialistTask(domain=Domain.TRACES, objective="traces", hypothesis_ids=["H1"]),
        ],
    )

    constrained = constrain_plan(plan, {Domain.KUBERNETES}, max_tasks=5)

    assert [task.domain for task in constrained.tasks] == [Domain.KUBERNETES]


def test_tasks_for_one_domain_merge_and_unknown_hypotheses_are_dropped() -> None:
    plan = InvestigationPlan(
        hypotheses=[_hypothesis("H1"), _hypothesis("H2")],
        tasks=[
            SpecialistTask(domain=Domain.LOGS, objective="errors", hypothesis_ids=["H1", "H9"]),
            SpecialistTask(domain=Domain.LOGS, objective="restarts", hypothesis_ids=["H2", "H1"]),
        ],
    )

    [task] = constrain_plan(plan, ALL_DOMAINS, max_tasks=5).tasks

    assert task.objective == "errors; restarts"
    assert task.hypothesis_ids == ["H1", "H2"]


def test_the_plan_is_capped_at_max_tasks_and_four_hypotheses() -> None:
    plan = InvestigationPlan(
        hypotheses=[_hypothesis(f"H{n}") for n in range(1, 7)],
        tasks=[SpecialistTask(domain=domain, objective="look") for domain in Domain],
    )

    constrained = constrain_plan(plan, ALL_DOMAINS, max_tasks=3)

    assert len(constrained.tasks) == 3
    assert [item.id for item in constrained.hypotheses] == ["H1", "H2", "H3", "H4"]


def test_the_fallback_plan_puts_kubernetes_first_for_a_pod_alert() -> None:
    alert = _alert(pod="checkout-7d9f8b6c5-x2x9z", workload="checkout")

    plan = fallback_plan(alert, ALL_DOMAINS, max_tasks=5)

    assert [task.domain for task in plan.tasks] == [
        Domain.KUBERNETES,
        Domain.METRICS,
        Domain.LOGS,
        Domain.CHANGES,
    ]
    assert [item.id for item in plan.hypotheses] == ["H1", "H2"]
    assert "checkout" in plan.hypotheses[0].statement


def test_the_fallback_plan_follows_the_source_and_what_is_available() -> None:
    plan = fallback_plan(_alert(AlertSource.SENTRY), {Domain.ERRORS, Domain.LOGS}, max_tasks=5)

    assert [task.domain for task in plan.tasks] == [Domain.ERRORS, Domain.LOGS]


def test_the_fallback_plan_with_nothing_available_has_no_tasks() -> None:
    assert fallback_plan(_alert(), set(), max_tasks=5).tasks == []


def test_a_degraded_finding_explains_the_gap_with_zero_confidence() -> None:
    finding = degraded_finding(Domain.LOGS, "Loki timed out")

    assert finding.confidence == 0.0
    assert "Loki timed out" in finding.summary
    assert finding.gaps


def test_finding_confidence_must_be_between_zero_and_one() -> None:
    with pytest.raises(ValidationError):
        SpecialistFinding(domain=Domain.LOGS, summary="x", confidence=1.5)
```

- [ ] **Step 2: Verificar que fallan**

Run: `uv run pytest tests/domain/test_evidence.py tests/domain/test_investigation.py -v`

Expected: `2 errors` durante la colección, con `No module named 'sre_graph.domain.evidence'` y `No module named 'sre_graph.domain.investigation'`.

- [ ] **Step 3: Implementación**

`src/sre_graph/domain/evidence.py`:

```python
"""Evidence: what a tool call returned, under an id that claims can cite."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from sre_graph.domain.tools import Domain, EvidenceType


class EvidenceItem(BaseModel):
    """One tool result kept for the diagnosis and the report.

    ``data`` holds the tool's artifact (already capped and redacted by the
    integration layer); ``summary`` is the line a report shows for it.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    integration: str
    tool: str
    evidence_type: EvidenceType
    domains: list[Domain] = Field(default_factory=list)
    summary: str
    args: dict[str, Any] = Field(default_factory=dict)
    data: Any = None
    collected_at: datetime
    error: str | None = None


def evidence_id(tool: str, args: Mapping[str, Any]) -> str:
    """A stable id for one call: the same tool with the same arguments gets the same id.

    Parallel specialists can mint ids without coordinating, and a repeated
    call deduplicates instead of piling up.
    """
    canonical = json.dumps(
        {"tool": tool, "args": args}, sort_keys=True, default=str, separators=(",", ":")
    )
    return "ev_" + hashlib.sha256(canonical.encode()).hexdigest()[:10]


def merge_evidence(
    current: Iterable[Mapping[str, Any]] | None, new: Iterable[Mapping[str, Any]] | None
) -> list[dict[str, Any]]:
    """Graph-state reducer: append evidence dicts, one per id, keeping first-seen order.

    A later item with an id already present replaces the earlier one (the
    same call, run again, returns the fresher result).
    """
    merged: dict[str, dict[str, Any]] = {}
    for item in [*(current or []), *(new or [])]:
        merged[str(item["id"])] = dict(item)
    return list(merged.values())
```

`src/sre_graph/domain/investigation.py`:

```python
"""The investigation plan (hypotheses and specialist tasks) and what specialists report back."""

from __future__ import annotations

from collections.abc import Collection
from enum import StrEnum

from pydantic import BaseModel, Field

from sre_graph.domain.alerts.model import AlertSource, NormalizedAlert
from sre_graph.domain.tools import Domain

MAX_HYPOTHESES = 4

#: Specialist domains worth asking first for each alert source, in priority order.
SOURCE_DOMAINS: dict[AlertSource, tuple[Domain, ...]] = {
    AlertSource.ALERTMANAGER: (Domain.METRICS, Domain.LOGS, Domain.CHANGES),
    AlertSource.GRAFANA: (Domain.METRICS, Domain.LOGS, Domain.TRACES, Domain.CHANGES),
    AlertSource.DATADOG: (Domain.METRICS, Domain.LOGS, Domain.TRACES, Domain.ERRORS),
    AlertSource.CLOUDWATCH: (Domain.CLOUD, Domain.METRICS, Domain.LOGS, Domain.CHANGES),
    AlertSource.SENTRY: (Domain.ERRORS, Domain.CHANGES, Domain.LOGS),
    AlertSource.PAGERDUTY: (Domain.METRICS, Domain.LOGS, Domain.CHANGES),
    AlertSource.NEWRELIC: (Domain.METRICS, Domain.ERRORS, Domain.TRACES, Domain.LOGS),
    AlertSource.GENERIC: (Domain.METRICS, Domain.LOGS, Domain.CHANGES),
}


class Hypothesis(BaseModel):
    """A candidate explanation and how to tell whether it holds."""

    id: str = Field(description='Short id the tasks and verdicts refer to, for example "H1".')
    statement: str = Field(description="The suspected cause, in one sentence.")
    confirm_if: str = Field(description="What evidence would confirm it.")
    rule_out_if: str = Field(description="What evidence would rule it out.")


class SpecialistTask(BaseModel):
    """What one domain specialist should look into."""

    domain: Domain
    objective: str = Field(description="What to check, concretely, in this domain.")
    hypothesis_ids: list[str] = Field(
        default_factory=list, description="Hypotheses this task tests."
    )


class InvestigationPlan(BaseModel):
    """The planner's output: 1 to 4 hypotheses and the tasks that test them."""

    hypotheses: list[Hypothesis]
    tasks: list[SpecialistTask]


class Verdict(StrEnum):
    """Where the evidence leaves a hypothesis."""

    SUPPORTS = "supports"
    REFUTES = "refutes"
    INCONCLUSIVE = "inconclusive"


class HypothesisVerdict(BaseModel):
    """A specialist's verdict on one hypothesis, with the evidence behind it."""

    hypothesis_id: str
    verdict: Verdict
    reason: str
    evidence_ids: list[str] = Field(default_factory=list)


class SpecialistFinding(BaseModel):
    """What a specialist found, citing the evidence ids its tool calls produced."""

    domain: Domain
    summary: str = Field(description="What the evidence shows, in two or three sentences.")
    verdicts: list[HypothesisVerdict] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0, description="0 = no idea, 1 = certain.")
    gaps: list[str] = Field(default_factory=list, description="What could not be checked, and why.")


def degraded_finding(domain: Domain, error: str) -> SpecialistFinding:
    """The finding a failed specialist contributes, so the investigation can carry on."""
    return SpecialistFinding(
        domain=domain,
        summary=f"The {domain.value} specialist could not finish: {error}",
        confidence=0.0,
        gaps=[f"{domain.value} evidence is missing because the specialist failed"],
    )


def constrain_plan(
    plan: InvestigationPlan, available: Collection[Domain], max_tasks: int
) -> InvestigationPlan:
    """Keep only what can run: tasks for available domains, one per domain, ``max_tasks`` total.

    Tasks for the same domain merge their objectives; references to
    hypotheses that do not exist are dropped; at most 4 hypotheses remain.
    """
    hypotheses = plan.hypotheses[:MAX_HYPOTHESES]
    known = {hypothesis.id for hypothesis in hypotheses}
    merged: dict[Domain, SpecialistTask] = {}
    for task in plan.tasks:
        if task.domain not in available:
            continue
        ids = [item for item in task.hypothesis_ids if item in known]
        previous = merged.get(task.domain)
        if previous is None:
            merged[task.domain] = task.model_copy(update={"hypothesis_ids": _unique(ids)})
        else:
            merged[task.domain] = previous.model_copy(
                update={
                    "objective": f"{previous.objective}; {task.objective}",
                    "hypothesis_ids": _unique([*previous.hypothesis_ids, *ids]),
                }
            )
    return InvestigationPlan(hypotheses=hypotheses, tasks=list(merged.values())[:max_tasks])


def fallback_plan(
    alert: NormalizedAlert, available: Collection[Domain], max_tasks: int
) -> InvestigationPlan:
    """A deterministic plan for when the planner model fails.

    Two generic hypotheses (a recent change, or a constrained resource or
    dependency) and one task per relevant available domain: Kubernetes first
    when the alert names a pod, workload or namespace, then the source's own
    priority list.
    """
    target = alert.workload or alert.service or alert.pod or "the affected service"
    hypotheses = [
        Hypothesis(
            id="H1",
            statement=f"A recent change to {target} (deploy, config or scaling) caused the alert.",
            confirm_if="A change landed shortly before the alert started.",
            rule_out_if="No change touched it in the incident window.",
        ),
        Hypothesis(
            id="H2",
            statement=f"{target} is starved of a resource or a dependency it needs.",
            confirm_if="Saturation, errors or restarts line up with the alert.",
            rule_out_if="Resources and dependencies look normal throughout the window.",
        ),
    ]
    wanted: list[Domain] = []
    if alert.pod or alert.workload or alert.namespace:
        wanted.append(Domain.KUBERNETES)
    wanted.extend(SOURCE_DOMAINS[alert.source])
    tasks = [
        SpecialistTask(
            domain=domain,
            objective=f"Check {domain.value} evidence for {target} around the alert: {alert.title}",
            hypothesis_ids=["H1", "H2"],
        )
        for domain in _unique(wanted)
        if domain in available
    ]
    return InvestigationPlan(hypotheses=hypotheses, tasks=tasks[:max_tasks])


def _unique[T](items: list[T]) -> list[T]:
    return list(dict.fromkeys(items))
```

- [ ] **Step 4: Verificar que pasan**

Run: `uv run pytest tests/domain/test_evidence.py tests/domain/test_investigation.py -v`

Expected: `13 passed`.

- [ ] **Step 5: Gate**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run lint-imports && SRE_GRAPH_TEST_DATABASE_URL="postgresql://sre_graph:sre_graph@localhost:5433/sre_graph?connect_timeout=5" SRE_GRAPH_REQUIRE_POSTGRES=1 uv run pytest
```

Expected: todo en verde y `366 passed`.

- [ ] **Step 6: Commit**

```bash
git add src/sre_graph/domain/evidence.py src/sre_graph/domain/investigation.py tests/domain/test_evidence.py tests/domain/test_investigation.py
git commit -m "feat(domain): evidence ids and the investigation plan with deterministic fallback" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Diagnóstico incident-command y verificación determinista

**Diagnóstico.** `Diagnosis` es la salida del sintetizador, en formato incident-command:
- triage, estado (confirmed/open/next/owner), hipótesis con veredicto, verificación, preguntas de seguimiento y trade-offs;
- causa raíz, categoría, cadena causal, claims con ids de evidencia, pasos y confianza.

`diagnosis_schema(categories)` devuelve la misma clase con `category` cerrada a un enum, para la salida estructurada del modelo.

**Verificación.** `verify_diagnosis` revisa sin LLM:
- las citas (`supported`, `weak` o `unsupported`);
- las secciones vacías;
- que la categoría no contradiga el texto;
- el conflicto "healthy".

Con eso calcula el score. `is_clearly_healthy` es la regla "healthy" de opensre.

**Files:**
- Create: `src/sre_graph/domain/diagnosis.py`, `src/sre_graph/domain/verification.py`
- Test: `tests/domain/conftest.py`, `tests/domain/test_diagnosis.py`, `tests/domain/test_verification.py`

**Interfaces:**
- Consumes:
  - `NormalizedAlert`, `AlertStatus` y `Severity` (Tarea 1);
  - `CATEGORY_NAMES`, `HEALTHY`, `UNKNOWN`, `render_taxonomy` y `category_text_mismatch` (Tarea 5);
  - `EvidenceItem` y `Verdict` (Tarea 6);
  - `EvidenceType` (Fase 1a).
- Produces:
  - En `diagnosis.py`:
    - `IncidentStatus(confirmed, open, next, owner)`, `HypothesisOutcome(hypothesis_id, statement, verdict, reason)` y `Claim(statement, evidence_ids)`;
    - `Diagnosis`, con `triage_summary`, `status`, `hypotheses`, `verification`, `follow_up_questions`, `remediation_tradeoffs`, `root_cause`, `category`, `causal_chain`, `claims`, `remediation_steps` y `confidence`;
    - `diagnosis_schema(categories=None) -> type[Diagnosis]`;
    - `unknown_diagnosis(alert, reason) -> Diagnosis`.
  - En `verification.py`:
    - las constantes `PASS_SCORE = 0.5` y `MISMATCH_PENALTY = 0.15`;
    - `ClaimStatus(StrEnum)`: `supported`, `weak`, `unsupported`;
    - los modelos `ClaimCheck(statement, status, reason)` y `VerificationReport(claims, missing_sections, category_mismatch, healthy_conflict, score, passed)`;
    - `check_claim(claim, evidence: dict[str, EvidenceItem]) -> ClaimCheck`;
    - `missing_sections(diagnosis) -> list[str]`;
    - `is_clearly_healthy(alert, evidence_count) -> bool`;
    - `verify_diagnosis(diagnosis, evidence, alert) -> VerificationReport`.
  - Fixtures en `tests/domain/conftest.py`: `crashloop_alert`, `pod_evidence` y `oom_diagnosis`.

- [ ] **Step 1: Tests que fallan**

Crea `tests/domain/conftest.py`, con el incidente de ejemplo que comparten esta tarea y la 9:

```python
"""A crash-looping checkout pod: its alert, the evidence collected and the diagnosis."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from sre_graph.domain.alerts.model import AlertSource, NormalizedAlert, Severity
from sre_graph.domain.diagnosis import Claim, Diagnosis, HypothesisOutcome, IncidentStatus
from sre_graph.domain.evidence import EvidenceItem
from sre_graph.domain.investigation import Verdict
from sre_graph.domain.tools import Domain, EvidenceType

COLLECTED = datetime(2026, 10, 7, 10, 6, tzinfo=UTC)


def _evidence(identifier: str, tool: str, kind: EvidenceType, summary: str) -> EvidenceItem:
    return EvidenceItem(
        id=identifier,
        integration="kubernetes",
        tool=tool,
        evidence_type=kind,
        domains=[Domain.KUBERNETES],
        summary=summary,
        collected_at=COLLECTED,
    )


@pytest.fixture
def crashloop_alert() -> NormalizedAlert:
    return NormalizedAlert(
        source=AlertSource.ALERTMANAGER,
        title="KubePodCrashLooping",
        fingerprint="f00d",
        severity=Severity.CRITICAL,
        started_at=datetime(2026, 10, 7, 10, 0, tzinfo=UTC),
        namespace="payments",
        workload="checkout",
        pod="checkout-7d9f8b6c5-x2x9z",
    )


@pytest.fixture
def pod_evidence() -> list[EvidenceItem]:
    return [
        _evidence("ev_pods", "k8s_list_pods", EvidenceType.TOPOLOGY, "1 of 3 pods restarting"),
        _evidence("ev_events", "k8s_list_events", EvidenceType.EVENTS, "BackOff after OOMKilled"),
        _evidence("ev_logs", "k8s_pod_logs", EvidenceType.LOGS, "fatal: out of memory"),
    ]


@pytest.fixture
def oom_diagnosis() -> Diagnosis:
    return Diagnosis(
        triage_summary="1 of 3 checkout replicas is crash looping; checkout still serves.",
        status=IncidentStatus(
            confirmed="checkout-7d9f8b6c5-x2x9z is OOMKilled on every start",
            open="why memory grew in release 1.4.2",
            next="raise the memory limit or roll back",
            owner="payments team",
        ),
        hypotheses=[
            HypothesisOutcome(
                hypothesis_id="H1",
                statement="The container exceeds its memory limit.",
                verdict=Verdict.SUPPORTS,
                reason="exit code 137 and OOMKilled events",
            )
        ],
        verification=["k8s_list_pods and k8s_list_events tested H1: both show OOMKilled."],
        follow_up_questions=["Did release 1.4.2 change memory settings?"],
        remediation_tradeoffs="Raising the limit is fast; rolling back also reverts features.",
        root_cause="The checkout container exceeds its 256Mi memory limit and is OOMKilled.",
        category="pod_oomkilled",
        causal_chain=["Memory grows past 256Mi", "The kernel OOM-kills PID 1", "kubelet backs off"],
        claims=[
            Claim(
                statement="The pod restarted 7 times with exit code 137.",
                evidence_ids=["ev_pods"],
            ),
            Claim(statement="Events show BackOff after OOMKilled.", evidence_ids=["ev_events"]),
            Claim(statement="The logs end with an out-of-memory error.", evidence_ids=["ev_logs"]),
        ],
        remediation_steps=["Raise the memory limit to 512Mi", "Profile memory in release 1.4.2"],
        confidence=0.9,
    )
```

Crea `tests/domain/test_diagnosis.py`:

```python
from __future__ import annotations

import pytest
from pydantic import ValidationError

from sre_graph.domain.alerts.model import NormalizedAlert
from sre_graph.domain.diagnosis import Diagnosis, diagnosis_schema, unknown_diagnosis
from sre_graph.domain.taxonomy import UNKNOWN


def test_the_schema_closes_the_category_to_the_allowed_names_plus_unknown(
    oom_diagnosis: Diagnosis,
) -> None:
    schema = diagnosis_schema({"pod_oomkilled", "connection_exhaustion", "not_a_category"})

    category = schema.model_json_schema()["properties"]["category"]

    assert category["enum"] == ["connection_exhaustion", "pod_oomkilled", "unknown"]
    assert "### kubernetes_workload" in category["description"]


def test_the_schema_accepts_an_allowed_category_and_is_still_a_diagnosis(
    oom_diagnosis: Diagnosis,
) -> None:
    schema = diagnosis_schema({"pod_oomkilled"})

    parsed = schema.model_validate(oom_diagnosis.model_dump())

    assert isinstance(parsed, Diagnosis)
    assert parsed.category == "pod_oomkilled"


def test_the_schema_rejects_a_category_outside_the_allowed_names(
    oom_diagnosis: Diagnosis,
) -> None:
    schema = diagnosis_schema({"connection_exhaustion"})

    with pytest.raises(ValidationError):
        schema.model_validate(oom_diagnosis.model_dump())


def test_without_a_restriction_every_category_is_allowed() -> None:
    enum = diagnosis_schema().model_json_schema()["properties"]["category"]["enum"]

    assert len(enum) == 96


def test_an_unknown_diagnosis_names_the_alert_and_the_reason(
    crashloop_alert: NormalizedAlert,
) -> None:
    diagnosis = unknown_diagnosis(crashloop_alert, "every specialist failed")

    assert diagnosis.category == UNKNOWN
    assert diagnosis.confidence == 0.0
    assert "every specialist failed" in diagnosis.root_cause
    assert diagnosis.triage_summary.startswith("KubePodCrashLooping")
```

Crea `tests/domain/test_verification.py`:

```python
from __future__ import annotations

from sre_graph.domain.alerts.model import AlertStatus, NormalizedAlert, Severity
from sre_graph.domain.diagnosis import Claim, Diagnosis
from sre_graph.domain.evidence import EvidenceItem
from sre_graph.domain.verification import (
    ClaimStatus,
    check_claim,
    is_clearly_healthy,
    missing_sections,
    verify_diagnosis,
)


def _by_id(evidence: list[EvidenceItem]) -> dict[str, EvidenceItem]:
    return {item.id: item for item in evidence}


def test_a_well_cited_complete_diagnosis_passes(
    oom_diagnosis: Diagnosis, pod_evidence: list[EvidenceItem], crashloop_alert: NormalizedAlert
) -> None:
    report = verify_diagnosis(oom_diagnosis, pod_evidence, crashloop_alert)

    assert [check.status for check in report.claims] == [ClaimStatus.SUPPORTED] * 3
    assert report.missing_sections == []
    assert report.score == 0.9
    assert report.passed is True


def test_a_claim_without_citations_is_unsupported(pod_evidence: list[EvidenceItem]) -> None:
    check = check_claim(Claim(statement="The pod is OOMKilled."), _by_id(pod_evidence))

    assert (check.status, check.reason) == (ClaimStatus.UNSUPPORTED, "cites no evidence")


def test_a_claim_citing_evidence_never_collected_is_unsupported(
    pod_evidence: list[EvidenceItem],
) -> None:
    claim = Claim(statement="A deploy happened at 09:55.", evidence_ids=["ev_made_up"])

    check = check_claim(claim, _by_id(pod_evidence))

    assert check.status is ClaimStatus.UNSUPPORTED
    assert "ev_made_up" in check.reason


def test_a_claim_about_logs_that_cites_only_pod_state_is_weak(
    pod_evidence: list[EvidenceItem],
) -> None:
    claim = Claim(statement="The logs show a stack trace.", evidence_ids=["ev_pods"])

    check = check_claim(claim, _by_id(pod_evidence))

    assert check.status is ClaimStatus.WEAK
    assert "logs" in check.reason


def test_unsupported_claims_lower_the_score_and_fail_verification(
    oom_diagnosis: Diagnosis, pod_evidence: list[EvidenceItem], crashloop_alert: NormalizedAlert
) -> None:
    diagnosis = oom_diagnosis.model_copy(
        update={"claims": [*oom_diagnosis.claims, Claim(statement="A deploy broke it.")]}
    )

    report = verify_diagnosis(diagnosis, pod_evidence, crashloop_alert)

    assert report.score == round(0.9 * 3 / 4, 2)
    assert report.passed is False


def test_empty_incident_command_sections_are_listed(oom_diagnosis: Diagnosis) -> None:
    diagnosis = oom_diagnosis.model_copy(
        update={"follow_up_questions": [], "remediation_tradeoffs": " ", "causal_chain": []}
    )

    assert missing_sections(diagnosis) == [
        "follow_up_questions",
        "remediation_tradeoffs",
        "causal_chain",
    ]


def test_a_healthy_or_unknown_verdict_needs_no_causal_chain_or_steps(
    oom_diagnosis: Diagnosis,
) -> None:
    diagnosis = oom_diagnosis.model_copy(
        update={"category": "healthy", "causal_chain": [], "remediation_steps": []}
    )

    assert missing_sections(diagnosis) == []


def test_a_category_that_contradicts_the_root_cause_text_is_penalized(
    oom_diagnosis: Diagnosis, pod_evidence: list[EvidenceItem], crashloop_alert: NormalizedAlert
) -> None:
    diagnosis = oom_diagnosis.model_copy(
        update={"root_cause": "Postgres max_connections was exhausted by a connection pool."}
    )

    report = verify_diagnosis(diagnosis, pod_evidence, crashloop_alert)

    assert report.category_mismatch is not None
    assert report.score == 0.75


def test_a_healthy_verdict_on_a_firing_critical_alert_is_a_conflict(
    oom_diagnosis: Diagnosis, pod_evidence: list[EvidenceItem], crashloop_alert: NormalizedAlert
) -> None:
    diagnosis = oom_diagnosis.model_copy(update={"category": "healthy"})

    report = verify_diagnosis(diagnosis, pod_evidence, crashloop_alert)

    assert report.healthy_conflict == "a healthy verdict contradicts a firing critical alert"
    assert report.passed is False


def test_a_conclusion_without_claims_scores_zero(
    oom_diagnosis: Diagnosis, pod_evidence: list[EvidenceItem], crashloop_alert: NormalizedAlert
) -> None:
    report = verify_diagnosis(
        oom_diagnosis.model_copy(update={"claims": []}), pod_evidence, crashloop_alert
    )

    assert report.score == 0.0


def test_a_resolved_informational_alert_with_evidence_is_clearly_healthy(
    crashloop_alert: NormalizedAlert,
) -> None:
    healthy = crashloop_alert.model_copy(
        update={"status": AlertStatus.RESOLVED, "severity": Severity.INFO}
    )

    assert is_clearly_healthy(healthy, evidence_count=2) is True
    assert is_clearly_healthy(healthy, evidence_count=0) is False


def test_an_alert_with_an_error_or_a_high_severity_is_never_clearly_healthy(
    crashloop_alert: NormalizedAlert,
) -> None:
    resolved = crashloop_alert.model_copy(update={"status": AlertStatus.RESOLVED})
    with_error = resolved.model_copy(
        update={"severity": Severity.INFO, "annotations": {"log_excerpt": "Traceback ..."}}
    )

    assert is_clearly_healthy(resolved, evidence_count=2) is False
    assert is_clearly_healthy(with_error, evidence_count=2) is False


def test_signal_words_match_whole_words_only(pod_evidence: list[EvidenceItem]) -> None:
    claim = Claim(
        statement="The login catalog prevented checkout from serving requests.",
        evidence_ids=["ev_pods"],
    )

    assert check_claim(claim, _by_id(pod_evidence)).status is ClaimStatus.SUPPORTED
```

- [ ] **Step 2: Verificar que fallan**

Run: `uv run pytest tests/domain/test_diagnosis.py tests/domain/test_verification.py -v`

Expected: `ImportError while loading conftest` en `tests/domain/conftest.py`, con `ModuleNotFoundError: No module named 'sre_graph.domain.diagnosis'`. Mientras falte la implementación, el conftest impide colectar cualquier test bajo `tests/domain/`.

- [ ] **Step 3: Implementación**

`src/sre_graph/domain/diagnosis.py`:

```python
"""The diagnosis in incident-command form: triage, status, hypotheses, verdict and cited claims."""

from __future__ import annotations

from collections.abc import Collection
from typing import Any, Literal, cast

from pydantic import BaseModel, Field, create_model

from sre_graph.domain.alerts.model import NormalizedAlert
from sre_graph.domain.investigation import Verdict
from sre_graph.domain.taxonomy import CATEGORY_NAMES, UNKNOWN, render_taxonomy


class IncidentStatus(BaseModel):
    """The incident-command status line."""

    confirmed: str = Field(description="What is known for certain.")
    open: str = Field(description="What is still unknown.")
    next: str = Field(description="The next step.")
    owner: str = Field(description="Who should act: a team, a role or a person.")


class HypothesisOutcome(BaseModel):
    """How one hypothesis ended up."""

    hypothesis_id: str
    statement: str
    verdict: Verdict
    reason: str


class Claim(BaseModel):
    """A factual statement in the diagnosis and the evidence ids that back it."""

    statement: str
    evidence_ids: list[str] = Field(
        default_factory=list, description="Ids of the evidence items that support it."
    )


class Diagnosis(BaseModel):
    """The synthesizer's conclusion. ``category`` must name a taxonomy category."""

    triage_summary: str = Field(description="One line: what is affected and how badly.")
    status: IncidentStatus
    hypotheses: list[HypothesisOutcome] = Field(default_factory=list)
    verification: list[str] = Field(
        default_factory=list, description="Which checks tested which hypothesis, and the result."
    )
    follow_up_questions: list[str] = Field(
        default_factory=list, description="Direct questions for the responders."
    )
    remediation_tradeoffs: str = Field(
        default="", description='One line per fix path, or "N/A — single clear fix path".'
    )
    root_cause: str = Field(description="The root cause in two or three sentences.")
    category: str
    causal_chain: list[str] = Field(
        default_factory=list, description="The steps from cause to symptom, in order."
    )
    claims: list[Claim] = Field(default_factory=list)
    remediation_steps: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)


def diagnosis_schema(categories: Collection[str] | None = None) -> type[Diagnosis]:
    """``Diagnosis`` with ``category`` as a closed enum, for structured model output.

    The JSON schema lists the allowed names (plus ``unknown``) and the field
    description carries the rendered taxonomy, so the model sees both.
    """
    allowed = (CATEGORY_NAMES if categories is None else frozenset(categories) & CATEGORY_NAMES) | {
        UNKNOWN
    }
    names = tuple(sorted(allowed))
    category_type = cast(Any, Literal.__getitem__(names))
    return create_model(
        "Diagnosis",
        __base__=Diagnosis,
        category=(
            category_type,
            Field(
                description="Exactly one category from this taxonomy:\n" + render_taxonomy(names)
            ),
        ),
    )


def unknown_diagnosis(alert: NormalizedAlert, reason: str) -> Diagnosis:
    """The diagnosis to report when the investigation could not reach a conclusion."""
    return Diagnosis(
        triage_summary=f"{alert.title}: the investigation could not reach a conclusion.",
        status=IncidentStatus(
            confirmed="The alert fired.",
            open="The root cause.",
            next="Investigate manually.",
            owner="On-call responder",
        ),
        root_cause=f"Unable to determine the root cause: {reason}",
        category=UNKNOWN,
        confidence=0.0,
    )
```

`src/sre_graph/domain/verification.py`:

```python
"""Deterministic checks on a diagnosis before anyone acts on it."""

from __future__ import annotations

import re
from collections.abc import Iterable
from enum import StrEnum

from pydantic import BaseModel

from sre_graph.domain.alerts.model import AlertStatus, NormalizedAlert, Severity
from sre_graph.domain.diagnosis import Claim, Diagnosis
from sre_graph.domain.evidence import EvidenceItem
from sre_graph.domain.taxonomy import HEALTHY, UNKNOWN, category_text_mismatch
from sre_graph.domain.tools import EvidenceType

#: Score a diagnosis needs, with every section filled and no unsupported claim, to pass.
PASS_SCORE = 0.5
MISMATCH_PENALTY = 0.15
#: Annotations whose presence means the alert itself reports an error.
_ERROR_ANNOTATIONS = ("error", "error_message", "log_excerpt", "failed_steps")
#: Words that tie a claim to a kind of evidence; a claim using them should cite that kind.
_SIGNALS: dict[EvidenceType, re.Pattern[str]] = {
    EvidenceType.LOGS: re.compile(r"\b(?:logs?|logged|logging|stack traces?|tracebacks?)\b"),
    EvidenceType.METRICS: re.compile(
        r"\b(?:cpu|memory|latency|throughput|utili[sz]ation|saturat\w*|p9[59])\b"
    ),
    EvidenceType.TRACES: re.compile(r"\b(?:traces?|tracing|spans?)\b"),
    EvidenceType.EVENTS: re.compile(r"\b(?:events?|oomkill\w*|backoff|evicted|restart\w*)\b"),
    EvidenceType.TOPOLOGY: re.compile(r"\b(?:pods?|containers?|replicas?|nodes?|deployments?)\b"),
    EvidenceType.CHANGES: re.compile(
        r"\b(?:deploy\w*|rollouts?|releases?|commits?|revisions?|rollbacks?)\b"
    ),
    EvidenceType.ERRORS: re.compile(r"\b(?:exceptions?|error rates?|sentry issues?)\b"),
}


class ClaimStatus(StrEnum):
    """How well the cited evidence backs a claim."""

    SUPPORTED = "supported"
    WEAK = "weak"
    UNSUPPORTED = "unsupported"


class ClaimCheck(BaseModel):
    """The verdict on one claim and why."""

    statement: str
    status: ClaimStatus
    reason: str


class VerificationReport(BaseModel):
    """What the deterministic checks found and the resulting score."""

    claims: list[ClaimCheck]
    missing_sections: list[str]
    category_mismatch: str | None = None
    healthy_conflict: str | None = None
    score: float
    passed: bool


def check_claim(claim: Claim, evidence: dict[str, EvidenceItem]) -> ClaimCheck:
    """Whether ``claim`` cites evidence that exists and is of the kind it talks about."""
    if not claim.evidence_ids:
        return ClaimCheck(
            statement=claim.statement, status=ClaimStatus.UNSUPPORTED, reason="cites no evidence"
        )
    cited = [evidence[item] for item in claim.evidence_ids if item in evidence]
    if not cited:
        missing = ", ".join(claim.evidence_ids)
        return ClaimCheck(
            statement=claim.statement,
            status=ClaimStatus.UNSUPPORTED,
            reason=f"cites evidence that was never collected: {missing}",
        )
    text = claim.statement.lower()
    mentioned = {kind for kind, pattern in _SIGNALS.items() if pattern.search(text)}
    cited_kinds = {item.evidence_type for item in cited}
    if mentioned and not mentioned & cited_kinds:
        wanted = ", ".join(sorted(kind.value for kind in mentioned))
        return ClaimCheck(
            statement=claim.statement,
            status=ClaimStatus.WEAK,
            reason=f"talks about {wanted} but cites none of that kind",
        )
    return ClaimCheck(statement=claim.statement, status=ClaimStatus.SUPPORTED, reason="ok")


def missing_sections(diagnosis: Diagnosis) -> list[str]:
    """Incident-command sections the diagnosis left empty."""
    concluded = diagnosis.category not in (HEALTHY, UNKNOWN)
    status = diagnosis.status
    required = {
        "triage_summary": bool(diagnosis.triage_summary.strip()),
        "status": all(
            part.strip() for part in (status.confirmed, status.open, status.next, status.owner)
        ),
        "hypotheses": bool(diagnosis.hypotheses),
        "verification": bool(diagnosis.verification),
        "follow_up_questions": bool(diagnosis.follow_up_questions),
        "remediation_tradeoffs": bool(diagnosis.remediation_tradeoffs.strip()),
        "root_cause": bool(diagnosis.root_cause.strip()),
        "causal_chain": bool(diagnosis.causal_chain) or not concluded,
        "remediation_steps": bool(diagnosis.remediation_steps) or not concluded,
    }
    return [name for name, present in required.items() if not present]


def is_clearly_healthy(alert: NormalizedAlert, evidence_count: int) -> bool:
    """True only when the alert is resolved, informational, error-free, and evidence exists.

    A resolved alert that was critical, or one carrying an error, is never
    "clearly healthy": a false healthy verdict hides a real incident.
    """
    return (
        alert.status is AlertStatus.RESOLVED
        and alert.severity is Severity.INFO
        and not alert.error
        and not any(alert.annotations.get(key) for key in _ERROR_ANNOTATIONS)
        and evidence_count > 0
    )


def verify_diagnosis(
    diagnosis: Diagnosis, evidence: Iterable[EvidenceItem], alert: NormalizedAlert
) -> VerificationReport:
    """Check citations, completeness, category/text agreement and healthy verdicts.

    The score is the diagnosis' confidence scaled by how well its claims are
    backed (a weak claim counts half), minus a penalty when the category does
    not match the root-cause text. With no claims, only ``healthy`` and
    ``unknown`` keep their confidence.
    """
    by_id = {item.id: item for item in evidence}
    checks = [check_claim(claim, by_id) for claim in diagnosis.claims]
    supported = sum(check.status is ClaimStatus.SUPPORTED for check in checks)
    weak = sum(check.status is ClaimStatus.WEAK for check in checks)
    if checks:
        backing = (supported + 0.5 * weak) / len(checks)
    else:
        backing = 1.0 if diagnosis.category in (HEALTHY, UNKNOWN) else 0.0
    mismatch = category_text_mismatch(diagnosis.root_cause, diagnosis.category)
    conflict = None
    if (
        diagnosis.category == HEALTHY
        and alert.status is AlertStatus.FIRING
        and alert.severity in (Severity.CRITICAL, Severity.HIGH)
    ):
        conflict = f"a healthy verdict contradicts a firing {alert.severity.value} alert"
    score = diagnosis.confidence * backing - (MISMATCH_PENALTY if mismatch else 0.0)
    score = round(min(max(score, 0.0), 1.0), 2)
    missing = missing_sections(diagnosis)
    passed = (
        not missing
        and not conflict
        and all(check.status is not ClaimStatus.UNSUPPORTED for check in checks)
        and score >= PASS_SCORE
    )
    return VerificationReport(
        claims=checks,
        missing_sections=missing,
        category_mismatch=mismatch,
        healthy_conflict=conflict,
        score=score,
        passed=passed,
    )
```

- [ ] **Step 4: Verificar que pasan**

Run: `uv run pytest tests/domain/test_diagnosis.py tests/domain/test_verification.py -v`

Expected: `18 passed`.

- [ ] **Step 5: Gate**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run lint-imports && SRE_GRAPH_TEST_DATABASE_URL="postgresql://sre_graph:sre_graph@localhost:5433/sre_graph?connect_timeout=5" SRE_GRAPH_REQUIRE_POSTGRES=1 uv run pytest
```

Expected: todo en verde y `384 passed`.

- [ ] **Step 6: Commit**

```bash
git add src/sre_graph/domain/diagnosis.py src/sre_graph/domain/verification.py tests/domain/conftest.py tests/domain/test_diagnosis.py tests/domain/test_verification.py
git commit -m "feat(domain): incident-command diagnosis and deterministic verification" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Acciones de remediación

`RemediationAction` describe una corrección propuesta:
- la tool que la ejecuta (o ninguna, si es manual) y su nivel de efecto;
- el riesgo, si es reversible, el rollback y el radio de impacto.

`order_actions` pone primero la más chica, reversible y de menor riesgo. `template_steps` da los pasos por defecto de cada categoría, sacados de las 69 plantillas de opensre, y omite los que dependen de una integración ausente.

**Files:**
- Create: `src/sre_graph/domain/remediation_templates.py`, `src/sre_graph/domain/remediation.py`
- Test: `tests/domain/test_remediation.py`

**Interfaces:**
- Consumes: `SideEffectLevel` (Fase 1a); `CATEGORY_NAMES` y `UNKNOWN` (Tarea 5).
- Produces:
  - `TEMPLATES: dict[str, tuple[tuple[str, str | None], ...]]`: cada paso con la integración que necesita, si necesita alguna.
  - Enumeraciones:
    - `Risk(StrEnum)`: `low`, `medium`, `high`;
    - `BlastRadius(StrEnum)`: `pod`, `workload`, `service`, `cluster`, `account`.
  - `RemediationAction`, con `title`, `rationale`, `tool`, `args`, `side_effect_level=MUTATING`, `risk=MEDIUM`, `reversible=True`, `rollback` y `blast_radius=SERVICE`.
  - `order_actions(actions) -> list[RemediationAction]`.
  - `template_steps(category, integrations) -> list[str]`.

- [ ] **Step 1: Test que falla**

Crea `tests/domain/test_remediation.py`:

```python
from __future__ import annotations

from sre_graph.domain.remediation import (
    BlastRadius,
    RemediationAction,
    Risk,
    order_actions,
    template_steps,
)
from sre_graph.domain.remediation_templates import TEMPLATES
from sre_graph.domain.taxonomy import CATEGORY_NAMES


def test_the_smallest_reversible_lowest_risk_action_comes_first() -> None:
    restart_cluster = RemediationAction(title="drain nodes", blast_radius=BlastRadius.CLUSTER)
    delete_pod = RemediationAction(
        title="delete pod", blast_radius=BlastRadius.POD, risk=Risk.LOW, reversible=False
    )
    undo = RemediationAction(
        title="rollout undo", blast_radius=BlastRadius.WORKLOAD, risk=Risk.HIGH
    )
    restart = RemediationAction(
        title="rollout restart", blast_radius=BlastRadius.WORKLOAD, risk=Risk.LOW
    )

    ordered = order_actions([restart_cluster, undo, delete_pod, restart])

    assert [action.title for action in ordered] == [
        "delete pod",
        "rollout restart",
        "rollout undo",
        "drain nodes",
    ]


def test_every_template_names_a_real_category() -> None:
    assert set(TEMPLATES) <= CATEGORY_NAMES
    assert "unknown" in TEMPLATES


def test_steps_for_absent_integrations_are_skipped() -> None:
    without = template_steps("connection_exhaustion", integrations=set())
    with_grafana = template_steps("connection_exhaustion", integrations={"grafana"})

    assert not any("Grafana" in step for step in without)
    assert any("Grafana" in step for step in with_grafana)
    assert not any("Datadog" in step for step in with_grafana)


def test_a_category_without_a_template_uses_the_unknown_one() -> None:
    assert template_steps("readiness_probe_misconfigured", set()) == template_steps(
        "unknown", set()
    )
```

- [ ] **Step 2: Verificar que falla**

Run: `uv run pytest tests/domain/test_remediation.py -v`

Expected: `1 error` durante la colección, con `ModuleNotFoundError: No module named 'sre_graph.domain.remediation'`.

- [ ] **Step 3: Implementación**

`src/sre_graph/domain/remediation_templates.py` reproduce `_TEMPLATES` de `57a5cbe0c^:app/nodes/root_cause_diagnosis/remediation_templates.py` de opensre. Las etiquetas se traducen así: `grafana → grafana`, `datadog → datadog`, `eks → kubernetes`.

```python
"""Default remediation steps per root-cause category, ported from OpenSRE.

Each step names the integration it relies on, or None when it needs none.
Categories without an entry use the ``unknown`` template.
"""

from __future__ import annotations

TEMPLATES: dict[str, tuple[tuple[str, str | None], ...]] = {
    "connection_exhaustion": (
        ("Identify clients consuming max_connections via pg_stat_activity", None),
        ("Terminate idle-in-transaction sessions to recover headroom", None),
        ("Enforce server-side and pool-side connection caps with idle timeouts", None),
        ("Review Grafana DatabaseConnections trend leading to exhaustion", "grafana"),
        ("Check Datadog monitors for connection saturation breaches", "datadog"),
    ),
    "connection_pool_leak": (
        ("Locate the application code path that fails to release pool connections", None),
        ("Add a leaked-connection logger and a finite pool acquire timeout", None),
        ("Ship a hotfix that wraps the leaking acquire in a context manager / defer", None),
        ("Review Grafana pool checkout vs return rate during the incident window", "grafana"),
    ),
    "idle_in_transaction_session_leak": (
        ("Cancel sessions stuck `idle in transaction` longer than the SLA threshold", None),
        ("Set `idle_in_transaction_session_timeout` on the database server", None),
        ("Audit application code for missing COMMIT/ROLLBACK on early-return paths", None),
    ),
    "cpu_saturation_bad_query": (
        ("Capture the dominant SQL pattern from Performance Insights and EXPLAIN it", None),
        ("Mitigate immediately by killing the offending session(s)", None),
        ("Add the missing index or rewrite the query to avoid sequential scans", None),
        ("Review Grafana CPU and ReadIOPS correlated with the query's db_load", "grafana"),
    ),
    "cpu_saturation_workload_burst": (
        ("Confirm aggregate workload increase rather than a single hot query", None),
        ("Throttle bursty callers and shift batch jobs out of peak windows", None),
        ("Right-size database compute or add a read replica for read traffic", None),
    ),
    "missing_index": (
        ("EXPLAIN the slow query and confirm Seq Scan on a high-cardinality column", None),
        ("Create the index CONCURRENTLY in production to avoid lock storms", None),
        ("Backfill statistics with ANALYZE and verify the planner picks the index", None),
    ),
    "query_plan_regression": (
        ("Compare current and previous plans via auto_explain or pg_stat_statements", None),
        ("Force the prior plan with planner hints / settings while root-cause is fixed", None),
        ("Refresh statistics and verify the new plan after stats update", None),
    ),
    "lock_contention": (
        ("Identify blocking session(s) via pg_locks / pg_stat_activity", None),
        ("Cancel long-running blockers; add lock_timeout to risky transactions", None),
        ("Restructure transactions to acquire locks in a consistent order", None),
    ),
    "deadlock_storm": (
        ("Inspect server logs for deadlock victims and the involved relations", None),
        ("Serialize conflicting writers or split hot rows to reduce contention", None),
        ("Add retries with exponential backoff on the application layer", None),
    ),
    "storage_exhaustion": (
        ("Confirm disk full from FreeStorageSpace or RDS 'ran out of storage' events", None),
        ("Scale storage or enable autoscaling before write traffic resumes", None),
        ("Throttle / segment bulk write jobs that consume storage rapidly", None),
        ("Review Grafana FreeStorageSpace and WriteLatency around the incident", "grafana"),
    ),
    "storage_iops_throttling": (
        ("Identify the volume / instance class limit that is being hit", None),
        ("Move to a higher-IOPS volume type or provisioned-IOPS configuration", None),
        ("Reduce IOPS demand: cache hot reads, batch writes, vacuum bloated tables", None),
    ),
    "storage_burst_balance_depleted": (
        ("Confirm BurstBalance metric is at zero and baseline IOPS too low", None),
        ("Move from gp2 to gp3 with provisioned baseline IOPS / throughput", None),
        ("Smooth bursty workload to fit within the steady-state baseline", None),
    ),
    "checkpoint_io_storm": (
        ("Confirm dominant LWLock:BufferMapping wait events and WriteIOPS spike", None),
        ("Tune checkpoint_timeout / max_wal_size to spread flushes more evenly", None),
        ("Provision higher write IOPS while the storm is being stabilized", None),
        ("Review Grafana WriteIOPS and DiskQueueDepth spikes around the storm", "grafana"),
    ),
    "vacuum_freeze_storm": (
        ("Confirm autovacuum FREEZE on a large table is the dominant load source", None),
        ("Schedule manual VACUUM FREEZE during off-peak windows", None),
        ("Tune autovacuum_freeze_max_age and per-table autovacuum settings", None),
    ),
    "autovacuum_blocked": (
        ("Identify long-running transactions blocking autovacuum cleanup", None),
        ("Cancel offending transactions and tune statement_timeout", None),
        ("Tune autovacuum_naptime and per-table autovacuum thresholds", None),
    ),
    "transaction_id_wraparound_pressure": (
        ("Run an emergency VACUUM FREEZE on the highest-age tables", None),
        ("Cancel long-running transactions preventing freeze cleanup", None),
        ("Add monitoring on MaximumUsedTransactionIDs with paging thresholds", None),
    ),
    "table_bloat": (
        ("Quantify bloat with pgstattuple / pg_class on the suspect tables", None),
        ("Run pg_repack to reclaim space without long exclusive locks", None),
        ("Tune autovacuum aggressiveness for high-churn tables", None),
    ),
    "replication_lag_wal_volume": (
        ("Quantify WAL generation versus replica replay capacity in the window", None),
        ("Throttle or schedule write-heavy jobs to reduce replay backlog", None),
        ("Increase replica capacity or optimize replication path for sustained writes", None),
        ("Review Grafana ReplicaLag and TransactionLogsGeneration trends", "grafana"),
    ),
    "replication_lag_long_query_on_replica": (
        ("Cancel the long-running replica query holding hot_standby_feedback", None),
        ("Move heavy reads to a dedicated read replica without query timeouts", None),
        ("Tune max_standby_streaming_delay and statement timeouts for analytics", None),
    ),
    "replication_lag_replica_undersized": (
        ("Right-size the replica instance class for steady-state write rate", None),
        ("Add additional replicas to spread analytical reads", None),
    ),
    "wal_archiving_failure": (
        ("Verify archive destination availability (S3 prefix / archive command)", None),
        ("Drain WAL backlog and confirm primary disk does not fill", None),
        ("Add monitoring for archive_status delays", None),
    ),
    "failover_event": (
        ("Confirm Multi-AZ / replica failover from RDS event timeline", None),
        ("Verify application-side connection retry behavior recovered cleanly", None),
        ("Document the failover RCA from the provider (health check failure, etc.)", None),
    ),
    "dual_resource_exhaustion": (
        ("Separate each independent bottleneck with its own evidence chain", None),
        ("Mitigate both bottlenecks in parallel to avoid recurrence", None),
        ("Add independent alert thresholds for each constrained resource", None),
        ("Review Grafana dashboards for both resources in the same window", "grafana"),
        ("Check Datadog monitors for concurrent multi-resource breaches", "datadog"),
    ),
    "pod_oomkilled": (
        ("Confirm OOMKill events via kubectl describe / kubelet logs", "kubernetes"),
        ("Raise memory request/limit or fix the leak in the offending pod", None),
        ("Add memory headroom alerts at 80% of the limit", None),
    ),
    "pod_cpu_throttled": (
        ("Inspect container_cpu_cfs_throttled_seconds_total trend", None),
        ("Raise CPU limits or remove the limit if request-based scheduling suffices", None),
        ("Profile the workload and reduce CPU hotspots where feasible", None),
    ),
    "pod_evicted_node_pressure": (
        ("Identify which node pressure (memory/disk/PIDs) drove the eviction", None),
        ("Scale the node group or balance pod placement away from the hot node", None),
        ("Configure pod priorityClass to protect critical workloads", None),
    ),
    "pod_crashloop_backoff": (
        ("Pull the most recent crash log from the failing container", "kubernetes"),
        ("Roll back the latest deploy if the crash started at a deploy timestamp", None),
        ("Add a startup probe for slow-init workloads to avoid spurious restarts", None),
    ),
    "pod_imagepull_backoff": (
        ("Confirm image tag exists and the pull secret is valid", None),
        ("Re-tag and re-deploy or restore the missing tag from the registry", None),
        ("Add registry availability monitoring", None),
    ),
    "pod_pending_insufficient_resources": (
        ("Inspect scheduler events for the insufficient-resource reason", None),
        ("Scale up the node group or right-size the pod requests", None),
    ),
    "pod_pending_unschedulable": (
        ("Inspect taints, affinity, and topology spread constraints", None),
        ("Adjust node taints/labels or relax pod placement rules", None),
    ),
    "node_not_ready": (
        ("Check kubelet, container runtime, and network plugin health on the node", None),
        ("Cordon and drain the node; replace if hardware/AMI level issue", None),
    ),
    "ingress_misconfiguration": (
        ("Validate ingress rules end-to-end (host, path, backend service/port)", None),
        ("Roll back the most recent ingress / certificate change", None),
    ),
    "dns_resolution_failure": (
        ("Confirm resolution failures from CoreDNS / VPC Resolver logs", None),
        ("Roll back recent DNS changes; add NXDOMAIN/SERVFAIL alerts", None),
        ("Pin critical lookups to known-good resolvers temporarily", None),
    ),
    "tls_certificate_expired": (
        ("Identify the expired/expiring certificate from the failing handshake", None),
        ("Rotate the certificate and verify chain end-to-end", None),
        ("Add expiry monitoring at 30/14/7 days for all production certs", None),
    ),
    "load_balancer_unhealthy_targets": (
        ("Inspect target group health checks and the most recent failures", None),
        ("Fix backend health (rollback / scale) or relax health-check thresholds", None),
    ),
    "nat_gateway_throttling": (
        ("Confirm NAT gateway port allocation / throughput limit was hit", None),
        ("Add additional NAT gateways across AZs and rebalance routes", None),
    ),
    "s3_object_missing": (
        ("Trace the upstream producer to confirm the object was never written", None),
        ("Re-run the producer step or adjust the consumer to handle late arrival", None),
    ),
    "s3_access_denied": (
        ("Diff the IAM policy / bucket policy against the last known-good revision", None),
        ("Restore the missing permission and re-run the failing job", None),
    ),
    "upstream_service_outage": (
        ("Confirm the upstream incident from its status page or oncall channel", None),
        ("Enable circuit breaker / fallback path until upstream recovers", None),
        ("Add explicit alerting on upstream SLO breaches", None),
    ),
    "upstream_schema_change": (
        ("Diff the upstream contract against the consumer's expected schema", None),
        ("Roll forward a consumer fix or roll back the upstream change", None),
        ("Add contract tests at the integration boundary", None),
    ),
    "upstream_rate_limit": (
        ("Confirm 429 responses and the rate-limit window in upstream logs", None),
        ("Add jittered exponential backoff and request quota negotiation", None),
    ),
    "upstream_authentication_failure": (
        ("Verify token / cred has not rotated, expired, or been revoked", None),
        ("Rotate credentials, redeploy, and confirm 200s resume", None),
    ),
    "bad_deploy": (
        ("Correlate the failure window with the most recent deploy timestamp", None),
        ("Roll back the offending deploy immediately", None),
        ("Add a regression test covering the failing path before re-deploying", None),
    ),
    "feature_flag_misconfiguration": (
        ("Identify the flag and the cohort it was enabled for", None),
        ("Disable the flag globally and confirm recovery", None),
        ("Add staged rollout guards for the flag going forward", None),
    ),
    "env_var_missing": (
        ("Identify which env var is missing from the failing service config", None),
        ("Restore the env var via the config store and redeploy", None),
    ),
    "env_var_misconfiguration": (
        ("Diff env var values against the last known-good config", None),
        ("Restore the correct value and redeploy", None),
    ),
    "secret_rotation_failure": (
        ("Identify the failing rotation step from the secret manager logs", None),
        ("Restore the previous secret version and re-attempt rotation", None),
    ),
    "code_defect_concurrency_bug": (
        ("Reproduce the race / lost-update under controlled concurrency", None),
        ("Add the necessary lock / idempotency guard and a regression test", None),
    ),
    "code_defect_resource_leak": (
        ("Pinpoint the leaking resource (FD, connection, memory) via heap/leak profile", None),
        ("Patch the leak path; add a finite resource cap to fail fast next time", None),
    ),
    "data_schema_drift": (
        ("Diff producer and consumer schemas; identify the changed/added field", None),
        ("Quarantine drifted records and roll the consumer forward", None),
        ("Enforce schema validation at the ingestion boundary", None),
    ),
    "data_late_arrival": (
        ("Confirm the missing partition / batch is genuinely late vs lost", None),
        ("Re-run the upstream producer or extend the consumer's wait window", None),
    ),
    "lambda_concurrent_executions_exceeded": (
        ("Confirm the throttling reason from Lambda metrics and account quotas", None),
        ("Raise the concurrency reservation or partition workload across functions", None),
    ),
    "application_tier_load_spike": (
        ("Verify upstream application surge driving downstream pressure", None),
        ("Rate-limit bursty traffic and protect downstream concurrency limits", None),
        ("Right-size autoscaling and backpressure controls", None),
        (
            "Review Grafana service-to-database traffic correlation in the incident window",
            "grafana",
        ),
        ("Check Datadog service-level monitors for traffic surge and error propagation", "datadog"),
    ),
    "traffic_burst_unprotected": (
        ("Confirm the spike size relative to autoscale headroom", None),
        ("Add edge rate-limits and queue-based load leveling", None),
    ),
    "ddos_event": (
        ("Engage WAF / shield rules to absorb or block the abusive pattern", None),
        ("Coordinate with the cloud provider's DDoS response team", None),
    ),
    "cascading_failure": (
        ("Identify the first service to degrade and break the propagation path", None),
        ("Add bulkheads and circuit breakers on the cross-service hops", None),
    ),
    "az_outage": (
        ("Confirm AZ scope from CloudWatch / status page and shift traffic away", None),
        ("Verify Multi-AZ failover behaved as designed", None),
    ),
    "iam_policy_misconfiguration": (
        ("Diff the IAM policy against the last known-good revision", None),
        ("Restore the missing permission and re-run the failing action", None),
    ),
    "service_quota_exceeded": (
        ("Confirm the breached quota from the cloud quota dashboard", None),
        ("Request a quota increase and add monitoring for headroom", None),
    ),
    "resource_exhaustion": (
        (
            (
                "Identify the saturated resource (memory, CPU, connections, storage) "
                "from the evidence"
            ),
            None,
        ),
        ("Scale up or right-size the affected workload or database", None),
        ("Set resource limits and alerts at 80% to catch saturation early", None),
        ("Review Grafana dashboards for resource trend leading up to the incident", "grafana"),
        ("Check Datadog monitors for threshold breaches on the affected resource", "datadog"),
        ("List EKS pods and confirm OOMKill events with kubectl describe", "kubernetes"),
    ),
    "cpu_saturation": (
        ("Identify top CPU-consuming query patterns in Performance Insights", None),
        ("Mitigate hot queries with indexing, query rewrite, or workload throttling", None),
        ("Scale compute only after query-level causes are addressed", None),
        ("Review Grafana CPU and query throughput metrics leading to saturation", "grafana"),
        ("Check Datadog monitors for sustained CPU saturation alerts", "datadog"),
    ),
    "replication_lag": (
        ("Quantify WAL generation versus replica replay capacity during incident window", None),
        ("Throttle or schedule write-heavy jobs to reduce replica replay backlog", None),
        (
            ("Increase replica capacity or optimize replication path for sustained write bursts"),
            None,
        ),
        ("Review Grafana ReplicaLag and TransactionLogsGeneration trends", "grafana"),
        ("Check Datadog monitors for replica lag threshold breaches", "datadog"),
    ),
    "dependency_failure": (
        ("Identify the failing upstream service or dependency from error logs", None),
        ("Check upstream service health page and recent deployments", None),
        ("Enable circuit breaker or retry with exponential backoff if not active", None),
        ("Review Grafana logs for connection errors or timeouts to the dependency", "grafana"),
        ("Check Datadog monitors for upstream SLO breach", "datadog"),
    ),
    "configuration_error": (
        (
            (
                "Diff the configuration deployed before the incident against the last "
                "known-good config"
            ),
            None,
        ),
        ("Roll back the configuration change that introduced the mismatch", None),
        ("Add validation checks to CI/CD pipeline for configuration values", None),
    ),
    "code_defect": (
        (
            (
                "Identify the commit introducing the defect using git history or recent "
                "deploy timestamps"
            ),
            None,
        ),
        ("Roll back or hot-fix the affected service", None),
        ("Add a regression test covering the failing code path before re-deploying", None),
    ),
    "data_quality": (
        ("Quarantine or skip the malformed records to unblock the pipeline", None),
        ("Add schema validation at the ingestion boundary", None),
        ("Trace the upstream source of the bad data and notify the owner", None),
    ),
    "infrastructure": (
        ("Check cloud provider status page and recent AWS service events for the region", None),
        ("Verify IAM roles, VPC security groups, and networking rules are unchanged", None),
        ("Trigger failover to standby if the primary zone is degraded", None),
    ),
    "unknown": (
        ("Enable debug logging and re-run the failing workload to gather more signal", None),
        (
            ("Escalate to the owning team with the investigation trace and causal chain attached"),
            None,
        ),
    ),
    "healthy": (),
}
```

`src/sre_graph/domain/remediation.py`:

```python
"""Proposed remediation actions, ordered so the safest, smallest fix comes first."""

from __future__ import annotations

from collections.abc import Collection, Iterable
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from sre_graph.domain.remediation_templates import TEMPLATES
from sre_graph.domain.taxonomy import UNKNOWN
from sre_graph.domain.tools import SideEffectLevel


class Risk(StrEnum):
    """How likely the action is to make things worse."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class BlastRadius(StrEnum):
    """How much the action touches, smallest first."""

    POD = "pod"
    WORKLOAD = "workload"
    SERVICE = "service"
    CLUSTER = "cluster"
    ACCOUNT = "account"


class RemediationAction(BaseModel):
    """One proposed fix. ``tool`` is the integration tool that performs it; None = manual."""

    title: str
    rationale: str = ""
    tool: str | None = None
    args: dict[str, Any] = Field(default_factory=dict)
    side_effect_level: SideEffectLevel = SideEffectLevel.MUTATING
    risk: Risk = Risk.MEDIUM
    reversible: bool = True
    rollback: str = ""
    blast_radius: BlastRadius = BlastRadius.SERVICE


_RISK_ORDER = {Risk.LOW: 0, Risk.MEDIUM: 1, Risk.HIGH: 2}
_RADIUS_ORDER = {radius: rank for rank, radius in enumerate(BlastRadius)}


def order_actions(actions: Iterable[RemediationAction]) -> list[RemediationAction]:
    """Smallest blast radius first, then reversible before not, then lower risk; stable."""
    return sorted(
        actions,
        key=lambda action: (
            _RADIUS_ORDER[action.blast_radius],
            not action.reversible,
            _RISK_ORDER[action.risk],
        ),
    )


def template_steps(category: str, integrations: Collection[str]) -> list[str]:
    """Default remediation steps for ``category``, skipping steps for absent integrations.

    Categories without a template use the ``unknown`` one, so a new category
    never leaves the report without next steps.
    """
    entries = TEMPLATES.get(category, TEMPLATES[UNKNOWN])
    return [step for step, needs in entries if needs is None or needs in integrations]
```

- [ ] **Step 4: Verificar que pasa**

Run: `uv run pytest tests/domain/test_remediation.py -v`

Expected: `4 passed`.

- [ ] **Step 5: Gate**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run lint-imports && SRE_GRAPH_TEST_DATABASE_URL="postgresql://sre_graph:sre_graph@localhost:5433/sre_graph?connect_timeout=5" SRE_GRAPH_REQUIRE_POSTGRES=1 uv run pytest
```

Expected: todo en verde y `388 passed`.

- [ ] **Step 6: Commit**

```bash
git add src/sre_graph/domain/remediation_templates.py src/sre_graph/domain/remediation.py tests/domain/test_remediation.py
git commit -m "feat(domain): remediation actions, ordering and default steps per category" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Reporte y renderers

`build_report` reúne alerta, diagnóstico, verificación y acciones (ordenadas), junto con la evidencia citada, renumerada E1, E2… por orden de primera cita. Las citas a evidencia inexistente se descartan. Los cuatro renderers son funciones puras:
- **Markdown** (CLI, UI, reportes guardados): todas las secciones.
- **Slack:** bloques de Block Kit dentro de sus límites, con escape de `&`, `<` y `>`.
- **Telegram:** HTML escapado; si el mensaje no cabe en 4096 caracteres, se recortan secciones enteras.
- **PagerDuty:** nota corta en texto plano.

**Files:**
- Create: `src/sre_graph/domain/report.py`, y bajo `src/sre_graph/domain/rendering/`: `__init__.py`, `common.py`, `markdown.py`, `slack.py`, `telegram.py`, `pagerduty.py`
- Test: `tests/domain/test_report.py`, `tests/domain/test_rendering.py`

**Interfaces:**
- Consumes:
  - `NormalizedAlert` (Tarea 1);
  - `Diagnosis` y `Claim`; `ClaimStatus` y `VerificationReport` (Tarea 7);
  - `EvidenceItem` (Tarea 6);
  - `RemediationAction`, `BlastRadius`, `Risk` y `order_actions` (Tarea 8);
  - las fixtures de `tests/domain/conftest.py`.
- Produces:
  - En `report.py`:
    - `ReportClaim(statement, citations, status, reason)`;
    - `Report(alert, diagnosis, verification, claims, actions, evidence, labels, duration_seconds)`, con la propiedad `title`;
    - `build_report(alert, diagnosis, evidence, *, verification=None, actions=(), duration_seconds=None) -> Report`.
  - Renderers:
    - `render_markdown(report) -> str`;
    - `render_slack(report) -> dict[str, Any]`, con `HEADER_LIMIT = 150`, `SECTION_LIMIT = 3000` y `escape(text)`;
    - `render_telegram(report) -> str`, con `MESSAGE_LIMIT = 4096`;
    - `render_pagerduty_note(report) -> str`, con `NOTE_LIMIT = 4000`.

- [ ] **Step 1: Tests que fallan**

Crea `tests/domain/test_report.py`:

```python
from __future__ import annotations

from sre_graph.domain.alerts.model import NormalizedAlert
from sre_graph.domain.diagnosis import Claim, Diagnosis
from sre_graph.domain.evidence import EvidenceItem
from sre_graph.domain.remediation import BlastRadius, RemediationAction
from sre_graph.domain.report import build_report
from sre_graph.domain.verification import ClaimStatus, verify_diagnosis


def test_cited_evidence_is_labelled_in_order_of_first_citation(
    oom_diagnosis: Diagnosis, pod_evidence: list[EvidenceItem], crashloop_alert: NormalizedAlert
) -> None:
    diagnosis = oom_diagnosis.model_copy(
        update={
            "claims": [
                Claim(statement="logs", evidence_ids=["ev_logs"]),
                Claim(statement="pods and logs", evidence_ids=["ev_pods", "ev_logs"]),
            ]
        }
    )

    report = build_report(crashloop_alert, diagnosis, pod_evidence)

    assert report.labels == {"ev_logs": "E1", "ev_pods": "E2"}
    assert [item.id for item in report.evidence] == ["ev_logs", "ev_pods"]
    assert report.claims[1].citations == ["E2", "E1"]


def test_uncited_and_missing_evidence_is_left_out(
    oom_diagnosis: Diagnosis, pod_evidence: list[EvidenceItem], crashloop_alert: NormalizedAlert
) -> None:
    diagnosis = oom_diagnosis.model_copy(
        update={"claims": [Claim(statement="ghost", evidence_ids=["ev_missing", "ev_pods"])]}
    )

    report = build_report(crashloop_alert, diagnosis, pod_evidence)

    assert report.labels == {"ev_pods": "E1"}
    assert report.claims[0].citations == ["E1"]


def test_verification_results_are_attached_to_each_claim(
    oom_diagnosis: Diagnosis, pod_evidence: list[EvidenceItem], crashloop_alert: NormalizedAlert
) -> None:
    diagnosis = oom_diagnosis.model_copy(
        update={"claims": [*oom_diagnosis.claims, Claim(statement="A deploy broke it.")]}
    )
    verification = verify_diagnosis(diagnosis, pod_evidence, crashloop_alert)

    report = build_report(crashloop_alert, diagnosis, pod_evidence, verification=verification)

    assert report.claims[-1].status is ClaimStatus.UNSUPPORTED
    assert report.claims[-1].reason == "cites no evidence"


def test_actions_are_ordered_and_the_title_names_the_workload(
    oom_diagnosis: Diagnosis, pod_evidence: list[EvidenceItem], crashloop_alert: NormalizedAlert
) -> None:
    actions = [
        RemediationAction(title="scale up nodes", blast_radius=BlastRadius.CLUSTER),
        RemediationAction(title="raise memory limit", blast_radius=BlastRadius.WORKLOAD),
    ]

    report = build_report(crashloop_alert, oom_diagnosis, pod_evidence, actions=actions)

    assert [action.title for action in report.actions] == ["raise memory limit", "scale up nodes"]
    assert report.title == "KubePodCrashLooping — checkout"
```

Crea `tests/domain/test_rendering.py`:

```python
from __future__ import annotations

import pytest

from sre_graph.domain.alerts.model import NormalizedAlert
from sre_graph.domain.diagnosis import Claim, Diagnosis
from sre_graph.domain.evidence import EvidenceItem
from sre_graph.domain.remediation import BlastRadius, RemediationAction, Risk
from sre_graph.domain.rendering.markdown import render_markdown
from sre_graph.domain.rendering.pagerduty import NOTE_LIMIT, render_pagerduty_note
from sre_graph.domain.rendering.slack import HEADER_LIMIT, SECTION_LIMIT, render_slack
from sre_graph.domain.rendering.telegram import MESSAGE_LIMIT, render_telegram
from sre_graph.domain.report import Report, ReportClaim, build_report
from sre_graph.domain.verification import verify_diagnosis


@pytest.fixture
def report(
    oom_diagnosis: Diagnosis, pod_evidence: list[EvidenceItem], crashloop_alert: NormalizedAlert
) -> Report:
    diagnosis = oom_diagnosis.model_copy(
        update={"claims": [*oom_diagnosis.claims, Claim(statement="A deploy broke it.")]}
    )
    action = RemediationAction(
        title="Raise the checkout memory limit to 512Mi",
        tool="k8s_set_resources",
        risk=Risk.LOW,
        blast_radius=BlastRadius.WORKLOAD,
        rollback="set the limit back to 256Mi",
    )
    return build_report(
        crashloop_alert,
        diagnosis,
        pod_evidence,
        verification=verify_diagnosis(diagnosis, pod_evidence, crashloop_alert),
        actions=[action],
        duration_seconds=42.4,
    )


def test_markdown_has_every_section_with_citations_and_flags(report: Report) -> None:
    text = render_markdown(report)

    assert text.startswith("# KubePodCrashLooping — checkout\n")
    assert "**CRITICAL · firing · alertmanager · started 2026-10-07 10:00 UTC**" in text
    assert "Category: pod_oomkilled · Confidence: 90%" in text
    assert "Status — confirmed: checkout-7d9f8b6c5-x2x9z is OOMKilled" in text
    assert "- H1 (supports): The container exceeds its memory limit." in text
    assert "1. Memory grows past 256Mi" in text
    assert "- The pod restarted 7 times with exit code 137. [E1]" in text
    assert "- A deploy broke it. (unverified: cites no evidence)" in text
    assert "1. Raise the checkout memory limit to 512Mi — low risk, workload, reversible" in text
    assert "- E1 · kubernetes/k8s_list_pods: 1 of 3 pods restarting" in text
    assert text.rstrip().endswith("Generated by sre-graph in 42s")


def test_markdown_falls_back_to_the_diagnosis_steps_without_actions(
    oom_diagnosis: Diagnosis, pod_evidence: list[EvidenceItem], crashloop_alert: NormalizedAlert
) -> None:
    text = render_markdown(build_report(crashloop_alert, oom_diagnosis, pod_evidence))

    assert "1. Raise the memory limit to 512Mi" in text


def test_slack_blocks_respect_limits_and_escape_markup(report: Report) -> None:
    hostile = report.model_copy(
        update={
            "diagnosis": report.diagnosis.model_copy(
                update={"root_cause": "<!channel> & " + "x" * 5000}
            )
        }
    )

    message = render_slack(hostile)

    header = message["blocks"][0]
    assert header["type"] == "header"
    assert len(header["text"]["text"]) <= HEADER_LIMIT
    sections = [block for block in message["blocks"] if block["type"] == "section"]
    assert all(len(block["text"]["text"]) <= SECTION_LIMIT for block in sections)
    assert "&lt;!channel&gt; &amp;" in sections[0]["text"]["text"]
    assert "<!channel>" not in str(message["blocks"])
    assert message["text"].startswith("CRITICAL KubePodCrashLooping — checkout:")


def test_telegram_html_is_escaped_and_bold(report: Report) -> None:
    hostile = report.model_copy(
        update={"diagnosis": report.diagnosis.model_copy(update={"root_cause": "if a < b & c > d"})}
    )

    text = render_telegram(hostile)

    assert text.startswith("<b>KubePodCrashLooping — checkout</b>")
    assert "if a &lt; b &amp; c &gt; d" in text
    assert "<b>Claims</b>" in text


def test_a_telegram_message_over_the_limit_drops_whole_sections(report: Report) -> None:
    long_claims = [ReportClaim(statement="x" * 500, citations=["E1"]) for _ in range(20)]

    text = render_telegram(report.model_copy(update={"claims": long_claims}))

    assert len(text) <= MESSAGE_LIMIT
    assert text.endswith("… (truncated)")
    assert "<b>Root cause</b>" in text
    assert "<b>Claims</b>" not in text


def test_the_pagerduty_note_is_short_plain_text(report: Report) -> None:
    note = render_pagerduty_note(report)

    assert note.startswith("sre-graph RCA: KubePodCrashLooping — checkout")
    assert "Key evidence:" in note
    assert note.count("\n- ") <= 6
    assert "<" not in note
    assert len(note) <= NOTE_LIMIT
```

- [ ] **Step 2: Verificar que fallan**

Run: `uv run pytest tests/domain/test_report.py tests/domain/test_rendering.py -v`

Expected: `2 errors` durante la colección, con `No module named 'sre_graph.domain.report'` y `No module named 'sre_graph.domain.rendering'`.

- [ ] **Step 3: Implementación**

`src/sre_graph/domain/report.py`:

```python
"""The RCA report: everything a renderer needs, with evidence labelled E1, E2… as cited."""

from __future__ import annotations

from collections.abc import Iterable

from pydantic import BaseModel, Field

from sre_graph.domain.alerts.model import NormalizedAlert
from sre_graph.domain.diagnosis import Diagnosis
from sre_graph.domain.evidence import EvidenceItem
from sre_graph.domain.remediation import RemediationAction, order_actions
from sre_graph.domain.verification import ClaimStatus, VerificationReport


class ReportClaim(BaseModel):
    """A claim as the report shows it: its citations as labels, and any doubt about it."""

    statement: str
    citations: list[str] = Field(default_factory=list)
    status: ClaimStatus | None = None
    reason: str = ""


class Report(BaseModel):
    """One investigation's outcome, ready to render for any channel."""

    alert: NormalizedAlert
    diagnosis: Diagnosis
    verification: VerificationReport | None = None
    claims: list[ReportClaim] = Field(default_factory=list)
    actions: list[RemediationAction] = Field(default_factory=list)
    evidence: list[EvidenceItem] = Field(default_factory=list)
    labels: dict[str, str] = Field(default_factory=dict)
    duration_seconds: float | None = None

    @property
    def title(self) -> str:
        """The alert title, with the affected service when the title does not name it."""
        target = self.alert.workload or self.alert.service
        if target and target not in self.alert.title:
            return f"{self.alert.title} — {target}"
        return self.alert.title


def build_report(
    alert: NormalizedAlert,
    diagnosis: Diagnosis,
    evidence: Iterable[EvidenceItem],
    *,
    verification: VerificationReport | None = None,
    actions: Iterable[RemediationAction] = (),
    duration_seconds: float | None = None,
) -> Report:
    """Assemble a report: cited evidence only, labelled in order of first citation.

    Citations of evidence that does not exist are dropped from the labels
    (verification already flags those claims as unsupported).
    """
    by_id = {item.id: item for item in evidence}
    labels: dict[str, str] = {}
    for claim in diagnosis.claims:
        for item in claim.evidence_ids:
            if item in by_id and item not in labels:
                labels[item] = f"E{len(labels) + 1}"
    checks = verification.claims if verification is not None else []
    claims = [
        ReportClaim(
            statement=claim.statement,
            citations=[labels[item] for item in claim.evidence_ids if item in labels],
            status=checks[index].status if index < len(checks) else None,
            reason=checks[index].reason if index < len(checks) else "",
        )
        for index, claim in enumerate(diagnosis.claims)
    ]
    return Report(
        alert=alert,
        diagnosis=diagnosis,
        verification=verification,
        claims=claims,
        actions=order_actions(actions),
        evidence=[by_id[item] for item in labels],
        labels=labels,
        duration_seconds=duration_seconds,
    )
```

`src/sre_graph/domain/rendering/__init__.py`:

```python
"""Render a Report for each channel: Markdown, Slack Block Kit, Telegram HTML, PagerDuty."""
```

`src/sre_graph/domain/rendering/common.py`:

```python
"""Pieces every renderer shares: headline facts, claim lines, action lines."""

from __future__ import annotations

from sre_graph.domain.remediation import RemediationAction
from sre_graph.domain.report import Report, ReportClaim
from sre_graph.domain.verification import ClaimStatus


def headline(report: Report) -> str:
    """``CRITICAL · firing · alertmanager · started 2026-10-07 10:00 UTC``."""
    alert = report.alert
    parts = [alert.severity.value.upper(), alert.status.value, alert.source.value]
    if alert.started_at is not None:
        parts.append(f"started {alert.started_at:%Y-%m-%d %H:%M} UTC")
    return " · ".join(parts)


def verdict_line(report: Report) -> str:
    """``Category: pod_oomkilled · Confidence: 85% · Verification: 0.80``."""
    parts = [
        f"Category: {report.diagnosis.category}",
        f"Confidence: {report.diagnosis.confidence:.0%}",
    ]
    if report.verification is not None:
        parts.append(f"Verification: {report.verification.score:.2f}")
    return " · ".join(parts)


def status_line(report: Report) -> str:
    """The incident-command status block on one line."""
    status = report.diagnosis.status
    return (
        f"Status — confirmed: {status.confirmed} | open: {status.open} | "
        f"next: {status.next} | owner: {status.owner}"
    )


def claim_text(claim: ReportClaim) -> str:
    """A claim with its ``[E1, E2]`` citations and, when in doubt, why."""
    text = claim.statement
    if claim.citations:
        text += f" [{', '.join(claim.citations)}]"
    if claim.status in (ClaimStatus.WEAK, ClaimStatus.UNSUPPORTED):
        text += f" (unverified: {claim.reason})"
    return text


def action_text(action: RemediationAction) -> str:
    """``Roll back checkout — low risk, workload, reversible; runs k8s_rollout_undo``."""
    facts = [f"{action.risk.value} risk", action.blast_radius.value]
    facts.append("reversible" if action.reversible else "not reversible")
    text = f"{action.title} — {', '.join(facts)}"
    text += f"; runs {action.tool}" if action.tool else "; manual"
    if action.rollback:
        text += f"; rollback: {action.rollback}"
    return text


def remediation_lines(report: Report) -> list[str]:
    """Ordered actions, or the diagnosis' remediation steps when no action was proposed."""
    if report.actions:
        return [action_text(action) for action in report.actions]
    return list(report.diagnosis.remediation_steps)


def evidence_lines(report: Report) -> list[str]:
    """``E1 · kubernetes/k8s_list_pods: 3 pods, 1 in CrashLoopBackOff``."""
    return [
        f"{report.labels[item.id]} · {item.integration}/{item.tool}: {item.summary}"
        for item in report.evidence
    ]


def footer(report: Report) -> str:
    """Who generated the report and how long the investigation took."""
    if report.duration_seconds is None:
        return "Generated by sre-graph"
    return f"Generated by sre-graph in {report.duration_seconds:.0f}s"
```

`src/sre_graph/domain/rendering/markdown.py`:

```python
"""The full report as Markdown, for the CLI, the web UI and stored reports."""

from __future__ import annotations

from sre_graph.domain.rendering.common import (
    claim_text,
    evidence_lines,
    footer,
    headline,
    remediation_lines,
    status_line,
    verdict_line,
)
from sre_graph.domain.report import Report


def render_markdown(report: Report) -> str:
    """Every section of the report; empty sections are left out."""
    diagnosis = report.diagnosis
    lines = [f"# {report.title}", "", f"**{headline(report)}**", "", "## Root cause", ""]
    lines += [diagnosis.root_cause, "", verdict_line(report), "", "## Incident command", ""]
    lines += [f"Triage complete: {diagnosis.triage_summary}", "", status_line(report)]
    if diagnosis.hypotheses:
        lines += ["", "**Hypotheses**", ""]
        lines += [
            f"- {item.hypothesis_id} ({item.verdict.value}): {item.statement} — {item.reason}"
            for item in diagnosis.hypotheses
        ]
    for heading, items in (
        ("Verification", diagnosis.verification),
        ("Follow-up questions", diagnosis.follow_up_questions),
    ):
        if items:
            lines += ["", f"**{heading}**", ""] + [f"- {item}" for item in items]
    if diagnosis.remediation_tradeoffs:
        lines += ["", "**Remediation trade-offs**", "", diagnosis.remediation_tradeoffs]
    if diagnosis.causal_chain:
        lines += ["", "## Causal chain", ""]
        lines += [f"{number}. {step}" for number, step in enumerate(diagnosis.causal_chain, 1)]
    if report.claims:
        lines += ["", "## Claims", ""] + [f"- {claim_text(claim)}" for claim in report.claims]
    remediation = remediation_lines(report)
    if remediation:
        lines += ["", "## Remediation", ""]
        lines += [f"{number}. {item}" for number, item in enumerate(remediation, 1)]
    evidence = evidence_lines(report)
    if evidence:
        lines += ["", "## Evidence", ""] + [f"- {item}" for item in evidence]
    lines += ["", "---", footer(report)]
    return "\n".join(lines) + "\n"
```

`src/sre_graph/domain/rendering/slack.py`:

```python
"""The report as a Slack message: Block Kit blocks plus a plain-text fallback."""

from __future__ import annotations

from typing import Any

from sre_graph.domain.rendering.common import (
    claim_text,
    evidence_lines,
    footer,
    headline,
    remediation_lines,
    status_line,
    verdict_line,
)
from sre_graph.domain.report import Report

#: Slack limits: header text 150 characters, section text 3000.
HEADER_LIMIT = 150
SECTION_LIMIT = 3000


def escape(text: str) -> str:
    """Escape the three characters Slack's mrkdwn reserves."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def render_slack(report: Report) -> dict[str, Any]:
    """``{"text": fallback, "blocks": [...]}`` ready for ``chat.postMessage``."""
    diagnosis = report.diagnosis
    blocks: list[dict[str, Any]] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": _cut(report.title, HEADER_LIMIT)},
        },
        _context(f"{headline(report)} · {verdict_line(report)}"),
        _section(f"*Root cause*\n{escape(diagnosis.root_cause)}"),
        _section(
            f"*Triage complete:* {escape(diagnosis.triage_summary)}\n{escape(status_line(report))}"
        ),
    ]
    if diagnosis.hypotheses:
        items = [
            f"{item.hypothesis_id} ({item.verdict.value}): {item.statement}"
            for item in diagnosis.hypotheses
        ]
        blocks.append(_bullets("Hypotheses", items))
    if report.claims:
        blocks.append(_bullets("Claims", [claim_text(claim) for claim in report.claims]))
    remediation = remediation_lines(report)
    if remediation:
        blocks.append(_bullets("Remediation", remediation))
    evidence = evidence_lines(report)
    if evidence:
        blocks.append(_bullets("Evidence", evidence))
    blocks.append(_context(footer(report)))
    fallback = f"{report.alert.severity.value.upper()} {report.title}: {diagnosis.root_cause}"
    return {"text": fallback, "blocks": blocks}


def _section(text: str) -> dict[str, Any]:
    return {"type": "section", "text": {"type": "mrkdwn", "text": _cut(text, SECTION_LIMIT)}}


def _bullets(heading: str, items: list[str]) -> dict[str, Any]:
    return _section(f"*{heading}*\n" + "\n".join(f"• {escape(item)}" for item in items))


def _context(text: str) -> dict[str, Any]:
    return {"type": "context", "elements": [{"type": "mrkdwn", "text": escape(text)}]}


def _cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"
```

`src/sre_graph/domain/rendering/telegram.py`:

```python
"""The report as one Telegram message in HTML parse mode."""

from __future__ import annotations

from html import escape

from sre_graph.domain.rendering.common import (
    claim_text,
    footer,
    headline,
    remediation_lines,
    status_line,
    verdict_line,
)
from sre_graph.domain.report import Report

#: Telegram's limit for one message.
MESSAGE_LIMIT = 4096
_TRUNCATED = "\n… (truncated)"


def render_telegram(report: Report) -> str:
    """Whole sections only, in priority order, until the message limit is reached.

    Text is HTML-escaped, so a ``<`` in an error message cannot break the
    markup; a section that does not fit is dropped and the cut is marked.
    """
    diagnosis = report.diagnosis
    sections = [
        f"<b>{escape(report.title)}</b>\n{escape(headline(report))}",
        f"<b>Root cause</b>\n{escape(diagnosis.root_cause)}\n{escape(verdict_line(report))}",
        f"<b>Triage complete:</b> {escape(diagnosis.triage_summary)}\n"
        f"{escape(status_line(report))}",
    ]
    if report.claims:
        sections.append(_bullets("Claims", [claim_text(claim) for claim in report.claims]))
    remediation = remediation_lines(report)
    if remediation:
        sections.append(_bullets("Remediation", remediation))
    sections.append(f"<i>{escape(footer(report))}</i>")
    message = ""
    for section in sections:
        candidate = f"{message}\n\n{section}" if message else section
        if len(candidate) > MESSAGE_LIMIT - len(_TRUNCATED):
            return message + _TRUNCATED
        message = candidate
    return message


def _bullets(heading: str, items: list[str]) -> str:
    return f"<b>{heading}</b>\n" + "\n".join(f"• {escape(item)}" for item in items)
```

`src/sre_graph/domain/rendering/pagerduty.py`:

```python
"""The report as a short plain-text PagerDuty incident note."""

from __future__ import annotations

from sre_graph.domain.rendering.common import claim_text, remediation_lines, verdict_line
from sre_graph.domain.report import Report

NOTE_LIMIT = 4000
_TOP = 3


def render_pagerduty_note(report: Report) -> str:
    """Root cause, verdict, the top three claims and the top three actions, in plain text."""
    lines = [
        f"sre-graph RCA: {report.title}",
        "",
        report.diagnosis.root_cause,
        verdict_line(report),
    ]
    if report.claims:
        lines += ["", "Key evidence:"] + [f"- {claim_text(c)}" for c in report.claims[:_TOP]]
    remediation = remediation_lines(report)
    if remediation:
        lines += ["", "Next steps:"] + [f"- {item}" for item in remediation[:_TOP]]
    note = "\n".join(lines)
    return note if len(note) <= NOTE_LIMIT else note[: NOTE_LIMIT - 1] + "…"
```

- [ ] **Step 4: Verificar que pasan**

Run: `uv run pytest tests/domain/test_report.py tests/domain/test_rendering.py -v`

Expected: `10 passed`.

- [ ] **Step 5: Gate**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run lint-imports && SRE_GRAPH_TEST_DATABASE_URL="postgresql://sre_graph:sre_graph@localhost:5433/sre_graph?connect_timeout=5" SRE_GRAPH_REQUIRE_POSTGRES=1 uv run pytest
```

Expected: todo en verde y `398 passed`.

- [ ] **Step 6: Commit**

```bash
git add src/sre_graph/domain/report.py src/sre_graph/domain/rendering tests/domain/test_report.py tests/domain/test_rendering.py
git commit -m "feat(domain): RCA report with Markdown, Slack, Telegram and PagerDuty renderers" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## Verificación final

1. Corre el gate completo:

   ```bash
   uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run lint-imports && SRE_GRAPH_TEST_DATABASE_URL="postgresql://sre_graph:sre_graph@localhost:5433/sre_graph?connect_timeout=5" SRE_GRAPH_REQUIRE_POSTGRES=1 uv run pytest
   ```

   Expected: `398 passed`. Sin la URL de Postgres: `396 passed, 2 skipped`.

2. Run: `git log --oneline main..fase-1b`

   Expected: 10 commits: el del plan y uno por tarea.

3. Revisa a mano los cinco puntos de **Review Focus** contra los tests que los fijan.

4. La rama queda local. La integración a `main` se decide al cerrar (finishing-a-development-branch), sin remoto ni push.
