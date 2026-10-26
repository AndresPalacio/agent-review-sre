"""A scripted LLM drives production services to test contracts, without measuring RCA quality."""

from __future__ import annotations

from fin_graph.agents.testing.scripted_model import ScriptedChatModel, tool_call_message
from fin_graph.domain.evidence import evidence_id
from fin_graph.evals.scenarios import load_scenario
from fin_graph.graphs.investigation.services import LangChainServices
from fin_graph.llm.models import ModelRole


async def test_real_agent_tools_middleware_skill_and_grader_work_together(tmp_path, monkeypatch):
    from langchain.agents.middleware import AgentMiddleware

    import fin_graph.evals.runner as runner
    from fin_graph.graphs.investigation.extensions import investigation_skill
    from fin_graph.graphs.investigation.strategy import registration
    from fin_graph.harness.quality import assess_record

    calls = []

    class ObservedMiddleware(AgentMiddleware):
        async def awrap_model_call(self, request, handler):
            calls.append("middleware")
            return await handler(request)

    monkeypatch.setattr(registration, "build_middleware", lambda task: [ObservedMiddleware()])
    scenario = load_scenario("eks", "002-image-pull-backoff")
    args = {"namespace": scenario.metadata["namespace"]}
    ids = [evidence_id(name, args) for name in ("k8s_list_pods", "k8s_list_events")]
    model = ScriptedChatModel(
        responses=[
            tool_call_message(
                "InvestigationPlan",
                {
                    "hypotheses": [
                        {
                            "id": "H1",
                            "statement": "Bad image",
                            "confirm_if": "ImagePullBackOff",
                            "rule_out_if": "Image pulled",
                        }
                    ],
                    "tasks": [
                        {
                            "domain": "kubernetes",
                            "objective": "Read pods and events",
                            "hypothesis_ids": ["H1"],
                        }
                    ],
                },
                "plan",
            ),
            tool_call_message("k8s_list_pods", args, "pods"),
            tool_call_message("k8s_list_events", args, "events"),
            tool_call_message(
                "SpecialistFinding",
                {
                    "domain": "kubernetes",
                    "summary": "ImagePullBackOff",
                    "confidence": 0.95,
                    "evidence_ids": ids,
                },
                "finding",
            ),
            tool_call_message(
                "Diagnosis",
                {
                    "triage_summary": "Image pull failure",
                    "status": {
                        "confirmed": "Image pull failed",
                        "open": "none",
                        "next": "Fix image",
                        "owner": "SRE",
                    },
                    "root_cause": (
                        "ImagePullBackOff for payments:v2.1.0-bad: manifest unknown; "
                        "not an OOM. The old ReplicaSet is healthy."
                    ),
                    "category": "configuration_error",
                    "confidence": 0.95,
                    "claims": [{"statement": "The image cannot be pulled", "evidence_ids": ids}],
                    "hypotheses": [
                        {
                            "hypothesis_id": "H1",
                            "statement": "Bad image",
                            "verdict": "supports",
                            "reason": "Pod events",
                        }
                    ],
                    "verification": ["Pods and events confirm image pull failure"],
                    "follow_up_questions": ["Which valid tag should be deployed?"],
                    "remediation_tradeoffs": "Rollback restores the previous working image",
                    "causal_chain": ["Invalid image", "Pull fails", "Pod stays pending"],
                    "remediation_steps": ["Review and deploy a valid image tag"],
                },
                "diagnosis",
            ),
            tool_call_message(
                "ClaimJudgment", {"supported": True, "reason": "Recorded pods and events"}, "judge"
            ),
        ]
    )
    monkeypatch.setattr(
        runner,
        "_real_services",
        lambda name: LangChainServices({role: model for role in ModelRole}),
    )
    result = await runner.run_suite(
        "contract-test", "offline:scripted-llm", 1, tmp_path, scenarios=[scenario]
    )
    record = result["runs"][0]
    assert record["status"] == "completed", record
    assert assess_record(record) == [], record
    assert calls == ["middleware"] * 3
    assert investigation_skill() in str(model.calls[1][0].content)
    agents = {e["name"] for e in record["trace"] if e["kind"] == "agent_start"}
    assert agents == {"planner", "specialist_kubernetes", "synthesizer", "judge"}
