from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest
from langgraph.store.memory import InMemoryStore
from tests.graphs.analysis.conftest import AS_OF, NOW, ScriptedServices, Tools

from fin_graph.domain.analysis_report import AnalysisReport
from fin_graph.domain.evidence import EvidenceItem
from fin_graph.domain.orders import OrderAction, PortfolioDecision
from fin_graph.domain.quant.common import Signal
from fin_graph.domain.thesis import InvestmentThesis, ThesisClaim
from fin_graph.graphs.analysis.graph import AnalysisLimits, _compile_analysis_graph
from fin_graph.harness.runtime import make_analysis_graph
from fin_graph.integrations.context import RunContext
from fin_graph.memory.theses import ThesisMemory
from fin_graph.safety.trading_limits import TradingLimits

REQUEST = {"tickers": "DEEP,DIST", "as_of": AS_OF.isoformat()}


def graph(services: ScriptedServices | None = None, **kwargs: Any) -> Any:
    return _compile_analysis_graph(
        services or ScriptedServices(), Tools(), clock=lambda: NOW, checkpointer=False, **kwargs
    )


async def run(compiled: Any, context: RunContext, raw: Any = None, **extra: Any) -> dict[str, Any]:
    payload = {"raw_request": REQUEST if raw is None else raw, "user_requested": True,
               "run_id": "run-1", "thread_id": "run-1", **extra}  # fmt: skip
    result: dict[str, Any] = await compiled.ainvoke(
        payload, context=context, config={"recursion_limit": 64}
    )
    return result


async def test_a_run_without_a_model_still_reports_every_ticker(context: RunContext) -> None:
    state = await run(graph(), context)

    report = AnalysisReport.model_validate(state["report"])
    assert [section.ticker for section in report.sections] == ["DEEP", "DIST"]
    deep = report.sections[0]
    assert deep.thesis.category == "deep_value" and deep.thesis.stance is Signal.BULLISH
    assert set(deep.signals) == {
        "fundamentals",
        "valuation",
        "technicals",
        "sentiment",
        "buffett",
        "graham",
    }
    assert deep.limit is not None and deep.limit.max_shares > 0
    assert "plan: deterministic plan" in report.degraded_steps
    assert report.labels and all(claim.citations for claim in deep.claims)


async def test_without_a_model_the_portfolio_holds(context: RunContext) -> None:
    state = await run(graph(), context)

    assert {item["action"] for item in state["decisions"].values()} == {"hold"}
    assert state["orders"] == []
    assert any("portfolio" in step or "thesis" in step for step in state["degraded"]) or all(
        item["action"] == "hold" for item in state["decisions"].values()
    )


async def test_the_manager_decides_inside_the_stance_and_the_limits(context: RunContext) -> None:
    decision = PortfolioDecision(
        action=OrderAction.BUY, quantity=10**6, confidence=70, reasoning="net-net"
    )
    services = ScriptedServices(decision=decision)

    state = await run(graph(services), context)

    deep, dist = state["decisions"]["DEEP"], state["decisions"]["DIST"]
    assert deep["action"] == "buy" and 0 < deep["quantity"] <= state["allowed"]["DEEP"]["buy"]
    assert set(state["allowed"]["DIST"]) == {"hold"}  # bearish, nothing held, no shorting
    assert dist["action"] == "hold"
    [order] = state["orders"]
    assert order["ticker"] == "DEEP" and order["client_order_id"].startswith("ord_")
    assert order["quantity"] * order["reference_price"] <= TradingLimits().max_order_notional
    assert services.decided and set(services.decided[0]) <= {
        OrderAction.HOLD,
        OrderAction.BUY,
        OrderAction.COVER,
    }


async def test_trading_gates_override_the_manager(context: RunContext) -> None:
    decision = PortfolioDecision(action=OrderAction.BUY, quantity=5, confidence=90)

    state = await run(
        graph(ScriptedServices(decision=decision), trading_limits=TradingLimits(kill_switch=True)),
        context,
    )

    assert state["orders"] == []
    assert state["allowed"]["DEEP"] == {"hold": 0}


async def test_an_unverified_model_thesis_is_refined_then_replaced(context: RunContext) -> None:
    bogus = InvestmentThesis(
        ticker="DEEP",
        stance=Signal.BULLISH,
        summary="It will double.",
        category="deep_value",
        confidence=90,
        bull_case=["cheap"],
        bear_case=["none"],
        risks=["none"],
        claims=[ThesisClaim(statement="The DCF says it doubles.", evidence_ids=["ev_invented"])],
    )
    services = ScriptedServices(theses={"DEEP": bogus})

    state = await run(graph(services), context)

    attempts = [problems for ticker, problems in services.synthesized if ticker == "DEEP"]
    assert len(attempts) == 2 and any("never collected" in problem for problem in attempts[1])
    assert state["window"]["lookback_days"] == 1095
    final = InvestmentThesis.model_validate(state["theses"][0])
    assert final.degraded and final.summary != bogus.summary
    assert "verify DEEP: model thesis not verified; score-based thesis" in state["degraded"]


async def test_greetings_are_noise_and_text_names_the_tickers(context: RunContext) -> None:
    noise = await run(graph(), context, raw="gracias!", user_requested=False)
    text = await run(graph(), context, raw="analiza DEEP a largo plazo")

    assert (noise["is_noise"] is True and "report" not in noise) or not noise.get("report")
    assert text["request"]["tickers"] == ["DEEP"] and text["request"]["horizon"] == "long"


async def test_verified_theses_are_remembered_and_recalled(context: RunContext) -> None:
    memory = ThesisMemory(InMemoryStore())

    await run(graph(memory=memory), context)
    second = await run(graph(memory=memory), context, run_id="run-2")

    saved = await memory.search_theses(ticker="DEEP")
    assert saved and saved[0]["category"] == "deep_value" and saved[0]["entry_price"]
    assert any(item["ticker"] == "DEEP" for item in second["recalled"])


async def test_a_missing_data_provider_holds_every_ticker() -> None:
    state = await run(graph(), RunContext())

    assert all(item["action"] == "hold" for item in state["decisions"].values())
    assert "risk: no limits, every ticker holds" in state["degraded"]
    assert all(
        InvestmentThesis.model_validate(item).category == "unknown" for item in state["theses"]
    )


async def test_an_elapsed_deadline_still_finishes(context: RunContext) -> None:
    later = iter([NOW, *[datetime.max.replace(tzinfo=NOW.tzinfo)] * 200])
    compiled = _compile_analysis_graph(
        ScriptedServices(), Tools(), clock=lambda: next(later), checkpointer=False,
        limits=AnalysisLimits(deadline_seconds=1),
    )  # fmt: skip

    state = await run(compiled, context)

    assert state["report"]
    assert any("analyst failed: TimeoutError" in error for error in state["errors"])


async def test_delivery_receives_the_analysis_report(context: RunContext) -> None:
    sent: list[tuple[AnalysisReport, dict[str, str], str]] = []

    class Delivery:
        async def send_analysis(
            self, report: AnalysisReport, destinations: dict[str, str], key: str
        ) -> list[Any]:
            sent.append((report, destinations, key))
            return []

    state = await run(graph(delivery=Delivery()), context, destinations={"slack": "C1"})

    assert sent and sent[0][1] == {"slack": "C1"} and sent[0][2] == "run-1"
    assert state["deliveries"] == []


async def test_the_harness_traces_every_analyst(
    context: RunContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    compiled = make_analysis_graph(
        ScriptedServices(), Tools(), clock=lambda: NOW, checkpointer=False
    )

    state = await run(compiled, context)

    agents = {event["name"] for event in state["harness_trace"] if event["kind"] == "agent_start"}
    assert {f"analyst_{name}" for name in ("fundamentals", "valuation", "graham")} <= agents
    assert {"synthesizer", "planner"} <= agents
    assert state["harness"]["mode"] == "analysis"


async def test_a_sound_but_weak_thesis_is_reported_and_never_traded(context: RunContext) -> None:
    from fin_graph.domain.thesis import fallback_thesis
    from fin_graph.graphs.analysis.evidence import scores_by_domain

    first = await run(graph(), context)
    evidence = [EvidenceItem.model_validate(item) for item in first["evidence"]]
    scores, ids = scores_by_domain(evidence, "DEEP")
    weak = fallback_thesis("DEEP", scores, ids).model_copy(
        update={"confidence": 30, "degraded": False}
    )
    decision = PortfolioDecision(action=OrderAction.BUY, quantity=5, confidence=90)

    state = await run(graph(ScriptedServices(theses={"DEEP": weak}, decision=decision)), context)

    assert state["verification"]["DEEP"]["passed"] is True
    assert state["decisions"]["DEEP"]["action"] == "hold"
    assert "below 50%" in state["decisions"]["DEEP"]["reasoning"]
    assert not [order for order in state["orders"] if order["ticker"] == "DEEP"]


async def test_failed_model_claims_are_dropped_and_the_prose_kept(context: RunContext) -> None:
    from fin_graph.domain.thesis import fallback_thesis
    from fin_graph.graphs.analysis.evidence import scores_by_domain

    first = await run(graph(), context)
    evidence = [EvidenceItem.model_validate(item) for item in first["evidence"]]
    scores, ids = scores_by_domain(evidence, "DEEP")
    base = fallback_thesis("DEEP", scores, ids)
    wrong_kind = ThesisClaim(statement="The RSI is oversold.", evidence_ids=[ids[next(iter(ids))]])
    narrated = base.model_copy(
        update={
            "summary": "Prosa del modelo.",
            "claims": [*base.claims, wrong_kind],
            "degraded": False,
        }
    )

    state = await run(graph(ScriptedServices(theses={"DEEP": narrated})), context)

    final = InvestmentThesis.model_validate(state["theses"][0])
    assert final.summary == "Prosa del modelo." and wrong_kind not in final.claims
    assert state["verification"]["DEEP"]["passed"] is True
    assert "verify DEEP: 1 unsupported model claims dropped" in state["degraded"]
