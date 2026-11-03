"""Run equity scenarios through the production analysis graph, offline, and write the results.

``model="scripted"`` uses no model at all: the analysts' compute tools run and every model step
takes its deterministic fallback. It is the baseline that shows what the quant pipeline alone
concludes. ``provider:model`` (``ollama:llama3.2:3b``) runs the same graph with that model in
the mode ``FIN_ANALYST_MODE`` picks for the provider.
"""

from __future__ import annotations

import asyncio
import json
import math
import time
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from datetime import time as day_time
from pathlib import Path
from typing import Any
from uuid import uuid4

from langchain_core.callbacks import UsageMetadataCallbackHandler
from langchain_core.tools import BaseTool

from fin_graph.config.settings import Settings
from fin_graph.config.tracing import disable_external_tracing
from fin_graph.evals.market.scenarios import EquityScenario, dataset_fingerprint, load_equity_suite
from fin_graph.evals.market.scoring import score_equity_scenario
from fin_graph.evals.market.trajectory import contract_for, grade_trajectory
from fin_graph.integrations.analytics.engine import AnalyticsEngine
from fin_graph.integrations.analytics.tools import ANALYTICS_TOOLS
from fin_graph.integrations.context import RunContext
from fin_graph.llm.models import ModelRole, build_chat_model, missing_credentials, parse_model_spec
from fin_graph.runtime.analysis import analyst_mode, make_analysis_graph
from fin_graph.runtime.trace import ExecutionTrace
from fin_graph.safety.secrets import redact_secrets, redact_value
from fin_graph.workflows.analysis.graph import AnalysisLimits, is_fallback_error
from fin_graph.workflows.analysis.services import (
    AnalysisServices,
    LangChainAnalysisServices,
    UnavailableAnalysisServices,
)


class _FixtureTools:
    """The analytics tools, which read the scenario's market fixtures."""

    def tools(self, context: RunContext) -> list[BaseTool]:
        return list(ANALYTICS_TOOLS)


def services_for(model: str) -> AnalysisServices:
    """Deterministic services for ``scripted``; model-backed ones for ``provider:model``."""
    if model == "scripted":
        return UnavailableAnalysisServices()
    spec = parse_model_spec(model)
    missing = missing_credentials(spec.provider)
    if missing:
        raise RuntimeError(f"{missing} is not configured")
    settings = Settings(_env_file=None, llm_provider=spec.provider)
    chat = build_chat_model(spec, settings, temperature=0)
    return LangChainAnalysisServices(dict.fromkeys(ModelRole, chat), mode=analyst_mode(settings))


def run_context(scenario: EquityScenario) -> RunContext:
    return RunContext(
        clients={"analytics": AnalyticsEngine("fixtures"), "market_fixtures": scenario.market()}
    )


async def run_scenario(
    scenario: EquityScenario,
    services: AnalysisServices,
    *,
    case_timeout: float = 900.0,
    limits: AnalysisLimits | None = None,
    trace: ExecutionTrace | None = None,
) -> tuple[dict[str, Any], float, int | None]:
    """One analysis of ``scenario``: the final state, seconds taken and tokens used."""
    reference = datetime.combine(scenario.as_of + timedelta(days=1), day_time(16), tzinfo=UTC)
    started = time.perf_counter()

    def clock() -> datetime:
        # Replay on the scenario's date while keeping real deadline progression.
        return reference + timedelta(seconds=time.perf_counter() - started)

    graph = make_analysis_graph(
        services,
        _FixtureTools(),
        clock=clock,
        limits=limits,
        trace=trace,
        checkpointer=False,
        # Evals measure candidate variants, which by definition differ from the pinned one.
        verify_variant_pin=False,
    )
    callback = UsageMetadataCallbackHandler()
    run_id = str(uuid4())
    async with asyncio.timeout(case_timeout):
        state: dict[str, Any] = await graph.ainvoke(
            {
                "raw_request": scenario.request(),
                "source_hint": "json",
                "user_requested": True,
                "thread_id": run_id,
                "run_id": run_id,
            },
            context=run_context(scenario),
            config={"recursion_limit": 64, "callbacks": [callback]},
        )
    usage = callback.usage_metadata
    tokens = sum(item.get("total_tokens", 0) for item in usage.values()) if usage else None
    return state, time.perf_counter() - started, tokens


async def run_equity_suite(
    suite: str,
    model: str,
    repeat: int,
    output_dir: Path,
    *,
    scenarios: Sequence[EquityScenario] | None = None,
    case_timeout: float = 900.0,
    limits: AnalysisLimits | None = None,
) -> dict[str, Any]:
    """Run every scenario ``repeat`` times; failures are recorded, never raised."""
    if not 1 <= repeat <= 20:
        raise ValueError("repeat must be between 1 and 20")
    if not math.isfinite(case_timeout) or case_timeout <= 0:
        raise ValueError("case_timeout must be positive and finite")
    disable_external_tracing()
    cases = list(load_equity_suite(suite) if scenarios is None else scenarios)
    if not cases:
        raise ValueError("scenarios must be nonempty")
    runs: list[dict[str, Any]] = []
    for scenario in cases:
        for attempt in range(1, repeat + 1):
            record: dict[str, Any] = {"scenario": scenario.key, "attempt": attempt}
            trace = ExecutionTrace()
            try:
                services = services_for(model)
                state, elapsed, tokens = await run_scenario(
                    scenario, services, case_timeout=case_timeout, limits=limits, trace=trace
                )
                score = score_equity_scenario(scenario, state, elapsed=elapsed, tokens=tokens)
                errors = [str(item) for item in state.get("errors", [])]
                record.update(
                    status="completed",
                    passed=score.passed,
                    score=score.model_dump(mode="json"),
                    theses=redact_value(state.get("theses", [])),
                    decisions=state.get("decisions", {}),
                    degraded=state.get("degraded", []),
                    # A model step that fell back is degraded, not a broken execution.
                    graph_errors=[e for e in errors if not is_fallback_error(e)],
                    fallbacks=[e for e in errors if is_fallback_error(e)],
                    deliveries=state.get("deliveries", []),
                    harness=state.get("harness", {}),
                )
            except Exception as exc:
                blocked = isinstance(exc, ValueError) or "not configured" in str(exc)
                record.update(
                    status="blocked" if blocked else "failed",
                    passed=False,
                    score=None,
                    error=redact_secrets(f"{type(exc).__name__}: {exc}"),
                )
            record["trace"] = trace.events
            record["trajectory"] = grade_trajectory(contract_for(scenario), trace.events)
            trace_path = (
                output_dir / "traces" / f"{scenario.key.replace('/', '--')}--{attempt}.json"
            )
            await asyncio.to_thread(_write_trace, trace_path, trace.events)
            record["trace_artifact"] = str(trace_path)
            runs.append(record)
    result = {
        "dataset_fingerprint": dataset_fingerprint(cases),
        "suite": suite,
        "model": model,
        "repeat": repeat,
        "summary": summarize(runs),
        "runs": runs,
    }
    await asyncio.to_thread(_write, output_dir, result)
    return result


def summarize(runs: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Pass rate and mean accuracies over the completed runs."""
    done = [run for run in runs if run.get("status") == "completed"]
    scores = [run["score"] for run in done]

    def mean(field: str) -> float | None:
        return round(sum(item[field] for item in scores) / len(scores), 3) if scores else None

    return {
        "runs": len(runs),
        "completed": len(done),
        "passed": sum(bool(run.get("passed")) for run in runs),
        "signal_accuracy": mean("signal_accuracy"),
        "category_accuracy": mean("category_accuracy"),
        "verified_share": mean("verified_share"),
        "mean_latency_seconds": mean("latency_seconds"),
    }


def _write_trace(path: Path, events: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(events, indent=2, default=str), encoding="utf-8")


def _write(output_dir: Path, result: dict[str, Any]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "results.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8"
    )
    summary = result["summary"]
    lines = [
        f"# Evaluación de acciones: {result['suite']}",
        "",
        f"Modelo: `{result['model']}`; repeticiones: {result['repeat']}.",
        "",
        "`scripted` no usa modelo: mide solo los analistas cuantitativos y los respaldos."
        if result["model"] == "scripted"
        else "Con modelo, la calidad se reporta tal cual; no es criterio de aceptación.",
        "",
        f"Aprobados {summary['passed']}/{summary['runs']}; señal {summary['signal_accuracy']}; "
        f"categoría {summary['category_accuracy']}; verificadas {summary['verified_share']}.",
        "",
        "| Escenario | Intento | Estado | Señal | Categoría | Verificada | Citas | Tools | Seg. |",
        "|---|---:|---|---|---|---|---:|---:|---:|",
    ]
    for run in result["runs"]:
        score = run.get("score") or {}
        tickers = score.get("tickers") or []
        lines.append(
            f"| {run['scenario']} | {run['attempt']} | {run['status']} | "
            + ", ".join(f"{t['ticker']} {t['signal']}" for t in tickers)
            + " | "
            + ", ".join(f"{t['category']}{'' if t['category_match'] else ' ✗'}" for t in tickers)
            + f" | {score.get('verified_share', '—')} | "
            f"{score.get('citations_valid', '—')}/{score.get('citations_total', '—')} | "
            f"{score.get('trajectory_covered', '—')}/{score.get('trajectory_total', '—')} | "
            f"{score.get('latency_seconds', '—')} |"
        )
    (output_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
