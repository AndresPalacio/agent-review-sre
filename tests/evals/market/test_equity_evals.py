from __future__ import annotations

import importlib.util
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from fin_graph.cli.main import app
from fin_graph.domain.quant.common import Signal
from fin_graph.evals.market.recording import record_scenario
from fin_graph.evals.market.runner import run_equity_suite, services_for
from fin_graph.evals.market.scenarios import (
    EquityAnswer,
    TickerAnswer,
    equity_scenario_ids,
    load_equity_scenario,
    load_equity_suite,
)
from fin_graph.evals.market.scoring import score_equity_scenario
from fin_graph.integrations.market_data import load_market_fixtures

ROOT = Path(__file__).resolve().parents[3]


def test_packaged_eval_data_contains_only_equities() -> None:
    data_root = ROOT / "src" / "fin_graph" / "evals" / "data"

    assert {path.name for path in data_root.iterdir()} == {"equities"}


def test_the_suites_cover_every_archetype_and_the_seeds() -> None:
    ids = equity_scenario_ids()

    assert len(ids) == 16 and len(load_equity_suite("all")) == 16
    assert [item.scenario_id for item in load_equity_suite("smoke")] == [
        "001-deep-value",
        "003-overvalued",
        "013-aapl-2024-03",
    ]
    categories = {
        answer.thesis_category
        for scenario in load_equity_suite("all")
        for answer in scenario.answer.tickers.values()
    }
    assert {"deep_value", "quality_compounder", "garp", "momentum", "overvalued",
            "deteriorating_fundamentals", "balance_sheet_risk", "negative_sentiment",
            "no_edge"} <= categories  # fmt: skip
    with pytest.raises(ValueError):
        load_equity_suite("nope")


def test_every_scenario_loads_its_market_and_names_its_tickers() -> None:
    for scenario in load_equity_suite("all"):
        market = scenario.market()
        assert set(scenario.tickers) <= set(market.tickers), scenario.key
        assert scenario.request()["tickers"] == scenario.tickers
        assert len(scenario.fingerprint()) == 64


def test_the_synthetic_scenarios_match_their_generator() -> None:
    spec = importlib.util.spec_from_file_location(
        "build_equity_scenarios", ROOT / "scripts" / "build_equity_scenarios.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve their module while executing
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(spec.name, None)

    assert module.build(check=True) == []


async def test_the_deterministic_pipeline_passes_every_scenario(tmp_path: Path) -> None:
    result = await run_equity_suite("all", "scripted", 1, tmp_path)

    failing = [run["scenario"] for run in result["runs"] if not run["passed"]]
    assert failing == []
    assert result["summary"]["signal_accuracy"] == 1.0
    assert (tmp_path / "report.md").read_text(encoding="utf-8").startswith("# Evaluación")
    assert json.loads((tmp_path / "results.json").read_text(encoding="utf-8"))["suite"] == "all"


def _state(
    category: str, stance: str, *, claim_ids: list[str], error: bool = False
) -> dict[str, Any]:
    return {
        "theses": [
            {
                "ticker": "DEEP",
                "stance": stance,
                "category": category,
                "summary": "Cotiza con margen de seguridad frente al Graham Number.",
                "claims": [{"statement": "x", "evidence_ids": claim_ids}],
            }
        ],
        "verification": {"DEEP": {"passed": True}},
        "decisions": {"DEEP": {"action": "buy"}},
        "evidence": [{"id": "ev_a", "tool": "compute_graham", "error": "boom" if error else None}],
        "findings": [{"wave": 1}],
    }


def test_scoring_counts_matches_alternatives_and_failed_citations() -> None:
    scenario = load_equity_scenario("001-deep-value")
    answer = scenario.answer.model_copy(
        update={
            "tickers": {
                "DEEP": TickerAnswer(
                    expected_signal=Signal.BULLISH,
                    thesis_category="deep_value",
                    forbidden_categories=["overvalued"],
                    required_keywords=["margin of safety|margen de seguridad", "net-net"],
                    expected_action="buy",
                )
            }
        }
    )
    case = type(scenario)(scenario.scenario_id, scenario.metadata, answer, scenario.directory)

    good = score_equity_scenario(
        case, _state("deep_value", "bullish", claim_ids=["ev_a"]), elapsed=1
    )
    bad = score_equity_scenario(
        case, _state("overvalued", "bearish", claim_ids=["ev_a", "ev_x"], error=True), elapsed=1
    )

    [ticker] = good.tickers
    assert good.passed and ticker.keywords_found == 1 and ticker.action_match is True
    assert good.citations_valid == 1 and good.trajectory_covered == 1
    assert not bad.passed and bad.tickers[0].forbidden_category
    assert bad.citations_valid == 0 and bad.citations_total == 2


async def test_recorded_scenarios_round_trip(tmp_path: Path) -> None:
    source = load_equity_scenario("001-deep-value").market()

    written = await record_scenario(source, ["DEEP"], date(2024, 6, 28), tmp_path / "016-copy")
    copy = load_market_fixtures(tmp_path / "016-copy")

    assert (tmp_path / "016-copy" / "scenario.yml") in written
    original = await source.prices("DEEP", date(2023, 1, 1), date(2024, 6, 28))
    assert await copy.prices("DEEP", date(2023, 1, 1), date(2024, 6, 28)) == original
    assert await copy.market_cap("DEEP", date(2024, 6, 28)) == await source.market_cap(
        "DEEP", date(2024, 6, 28)
    )


def test_models_need_credentials_and_scripted_needs_none() -> None:
    assert services_for("scripted") is not None
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        services_for("anthropic:claude-sonnet-5-5")
    with pytest.raises(ValueError):
        services_for("llama")


def test_answers_must_name_the_scenario_tickers(tmp_path: Path) -> None:
    folder = tmp_path / "001-bad"
    folder.mkdir()
    (folder / "scenario.yml").write_text("tickers: [AAA]\nas_of: '2024-01-02'\n", encoding="utf-8")
    (folder / "answer.yml").write_text(
        EquityAnswer(
            tickers={"BBB": TickerAnswer(expected_signal=Signal.NEUTRAL, thesis_category="no_edge")}
        ).model_dump_json(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="must match"):
        load_equity_scenario("001-bad", tmp_path)


def test_the_eval_cli_runs_the_equity_smoke_suite(tmp_path: Path) -> None:
    command = CliRunner().invoke(
        app,
        ["eval", "run", "--suite", "smoke", "--model", "scripted", "--output-dir", str(tmp_path)],
    )

    assert command.exit_code == 0, command.output
    assert "aprobados 3/3" in command.output
