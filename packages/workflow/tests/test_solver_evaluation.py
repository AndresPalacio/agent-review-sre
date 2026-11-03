"""Evaluation grades resulting environments, not the scripted model's trajectory."""

import pytest

pytest.importorskip("deepagents")


def test_independent_grader_rejects_forged_evidence_and_accepts_actual_repair():
    from examples.solver_benchmark import LocalEnvironment, cases, grade_environment

    case = next(case for case in cases() if case.id == "config-direct")
    environment = LocalEnvironment(case)
    forged = {"resolved": True, "evidence_refs": ["config/service.json#verified"]}
    assert not grade_environment(case, environment, forged).passed
    environment.documents["config/service.json"] = "[]"
    assert not grade_environment(case, environment, forged).passed
    environment.documents["config/service.json"] = '{"port":8443,"retries":3,"log_level":"INFO"}'
    assert grade_environment(case, environment, {"resolved": False}).passed


@pytest.mark.parametrize("port,retries", [(8443.0, 3), (8443, 3.0), (True, 3), (8443, True)])
def test_config_acceptance_rejects_noninteger_json_values(port, retries):
    import json

    from examples.solver_benchmark import LocalEnvironment, cases, grade_environment

    case = next(case for case in cases() if case.id == "config-direct")
    environment = LocalEnvironment(case)
    environment.documents[case.source] = json.dumps(
        {"port": port, "retries": retries, "log_level": "INFO"}
    )
    assert environment.verify(case.problem(), None, None).verdict == "failed"
    assert not grade_environment(case, environment).passed


@pytest.mark.asyncio
async def test_paired_offline_direct_cases_measure_real_changes_in_all_four_families():
    from examples.solver_benchmark import cases, run_benchmark

    direct = tuple(
        case for case in cases() if case.scenario == "direct" and case.split == "contract"
    )
    report = await run_benchmark(direct)
    assert {row.family for row in report.rows} == {
        "configuration",
        "transformation",
        "artifact",
        "investigation",
    }
    assert len(report.rows) == 8
    for row in report.rows:
        assert row.environment_passed, row
        assert row.claimed_success, row
        assert not row.false_success, row
        assert not row.over_budget, row
        assert row.duplicate_effects == 0, row
        assert row.duration_seconds >= 0
        assert row.model_calls > 0
        assert row.tool_calls > 0
    assert report.summary()["solver"]["verified_resolution_rate"] == 1.0
    assert report.summary()["baseline"]["environment_resolution_rate"] == 1.0


@pytest.mark.asyncio
async def test_contract_suite_separates_false_success_recovery_blocking_and_budget_exhaustion():
    from examples.solver_benchmark import cases, run_benchmark

    report = await run_benchmark(cases())
    rows = {(row.case_id, row.arm): row for row in report.rows}
    assert rows["config-forged-evidence", "baseline"].false_success
    assert not rows["config-forged-evidence", "solver"].claimed_success
    assert rows["config-wrong-diagnosis", "solver"].recovered
    assert rows["transform-tool-failure", "solver"].recovered
    for case_id in ("transform-missing-data", "artifact-missing-tool"):
        assert rows[case_id, "solver"].blocked
        assert not rows[case_id, "solver"].environment_passed
    for row in report.rows:
        assert not row.over_budget, row
    assert not rows["config-depleted-budget", "solver"].claimed_success
    assert report.summary()["solver"]["false_successes"] == 0
    assert report.summary()["solver"]["over_budget_runs"] == 0
    assert any(row.split == "holdout" for row in report.rows)
    metrics = report.summary()["solver"]
    assert (
        metrics["model_calls_per_solved_case"] == metrics["model_calls"] / metrics["solved_cases"]
    )


@pytest.mark.asyncio
async def test_each_arm_receives_a_fresh_environment_and_failed_writes_are_not_effects():
    from examples.solver_benchmark import cases, run_benchmark

    case = next(case for case in cases() if case.id == "transform-tool-failure")
    report = await run_benchmark((case,))
    assert all(row.environment_passed for row in report.rows)
    assert all(row.tool_failures == 1 for row in report.rows)
    assert all(row.duplicate_effects == 0 for row in report.rows)
    for arm in ("baseline", "solver"):
        metrics = report.summary()[arm]
        assert metrics["solved_cases"] == 1
        assert metrics["model_calls_per_solved_case"] > 0
        assert metrics["tokens_per_solved_case"] is None


@pytest.mark.asyncio
async def test_native_team_delegates_actions_and_read_only_inspection_before_host_verification():
    from examples.solver_team import run_team

    result, environment, delegations = await run_team()
    assert result.verified
    assert environment.documents["config/service.json"] == (
        '{"port": 8443, "retries": 3, "log_level": "INFO"}'
    )
    assert set(delegations) == {"general-purpose", "verifier"}
    assert environment.duplicate_effects == 0


@pytest.mark.asyncio
async def test_provider_path_only_counts_recovery_after_an_observed_failure():
    from examples.solver_benchmark import ScriptedModel, cases, run_benchmark

    wrong_case = next(case for case in cases() if case.id == "config-wrong-diagnosis")
    direct_case = next(case for case in cases() if case.id == "config-direct")
    report = await run_benchmark(
        (wrong_case,), model_factory=lambda: ScriptedModel(case=direct_case)
    )
    assert report.mode == "provider"
    assert all(row.environment_passed for row in report.rows)
    assert not any(row.recovered for row in report.rows)


@pytest.mark.parametrize(
    "usage_sequence,expected", [([10, None], None), ([None, 10], None), ([10, 10], 20)]
)
def test_token_usage_is_complete_only_when_every_model_call_reports_tokens(
    usage_sequence, expected
):
    from langchain_core.messages import AIMessage
    from langchain_core.outputs import ChatGeneration, LLMResult

    from examples.solver_benchmark import UsageMeter

    meter = UsageMeter()
    for tokens in usage_sequence:
        meter.on_chat_model_start({}, [[]])
        usage = (
            None if tokens is None else {"input_tokens": 6, "output_tokens": 4, "total_tokens": 10}
        )
        message = AIMessage(content="done", usage_metadata=usage)
        meter.on_llm_end(LLMResult(generations=[[ChatGeneration(message=message)]]))
    assert meter.total_tokens == expected
    assert meter.observed_tokens == sum(tokens or 0 for tokens in usage_sequence)


@pytest.mark.asyncio
async def test_recovery_counts_incorrect_writes_and_self_correction_in_both_arms():
    import json

    from langchain_core.messages import AIMessage
    from langchain_core.outputs import ChatGeneration, ChatResult

    from examples.solver_benchmark import ScriptedModel, cases, run_benchmark

    class SelfRepairModel(ScriptedModel):
        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            step = self._turn
            self._turn += 1
            if step in (0, 2):
                name, args = "read_document", {"path": self.case.source}
            elif step in (1, 3):
                content = json.dumps(
                    {"port": 443 if step == 1 else 8443, "retries": 3, "log_level": "INFO"}
                )
                name, args = "write_document", {"path": self.case.destination, "content": content}
            else:
                name, args = "Solution", {"resolved": True, "summary": "Repaired incorrect port."}
            return ChatResult(
                generations=[
                    ChatGeneration(
                        message=AIMessage(
                            content="",
                            tool_calls=[{"name": name, "args": args, "id": f"step-{step}"}],
                        )
                    )
                ]
            )

    case = next(case for case in cases() if case.id == "config-wrong-diagnosis")
    report = await run_benchmark((case,), model_factory=lambda: SelfRepairModel(case=case))
    assert all(row.environment_passed for row in report.rows)
    assert all(row.recovered for row in report.rows)
    assert all(row.incorrect_effects == 1 for row in report.rows)


@pytest.mark.asyncio
async def test_resolution_metrics_use_the_same_independent_check_for_both_arms():
    from examples.solver_benchmark import cases, run_benchmark

    selected = tuple(case for case in cases() if case.id in {"config-direct", "artifact-holdout"})
    report = await run_benchmark(selected)
    for arm in ("baseline", "solver"):
        assert report.summary()[arm]["verified_resolution_rate"] == 1.0
        assert report.summary()[arm]["total_model_calls"] == 6
    assert report.summary()["baseline"]["host_verified_resolution_rate"] == 0.0
    assert report.summary()["solver"]["host_verified_resolution_rate"] == 1.0
    split = report.as_dict()["by_split"]
    assert split["contract"]["baseline"]["cases"] == 1
    assert split["holdout"]["solver"]["cases"] == 1


@pytest.mark.asyncio
async def test_provider_rows_do_not_present_scripted_only_perturbations_as_injected():
    from examples.solver_benchmark import ScriptedModel, cases, run_benchmark

    case = next(case for case in cases() if case.id == "config-forged-evidence")
    direct = next(case for case in cases() if case.id == "config-direct")
    report = await run_benchmark((case,), model_factory=lambda: ScriptedModel(case=direct))
    assert all(row.perturbation_scope == "scripted_only_not_injected" for row in report.rows)


@pytest.mark.asyncio
async def test_provider_factory_cannot_reuse_one_stateful_model_across_arms():
    from examples.solver_benchmark import ScriptedModel, cases, run_benchmark

    case = next(case for case in cases() if case.id == "config-direct")
    shared_model = ScriptedModel(case=case)
    with pytest.raises(ValueError, match="fresh"):
        await run_benchmark((case,), model_factory=lambda: shared_model)


@pytest.mark.asyncio
async def test_information_gathering_and_holdout_resolve_from_multiple_sources():
    from examples.solver_benchmark import cases, run_benchmark

    selected = tuple(case for case in cases() if case.family == "investigation")
    assert {case.id for case in selected} == {
        "investigate-direct",
        "investigate-holdout",
        "investigate-missing-data",
    }
    report = await run_benchmark(selected)
    for row in report.rows:
        assert row.error is None
        assert not row.over_budget
        if row.scenario == "missing_data":
            assert row.blocked
            assert not row.environment_passed
        else:
            assert row.environment_passed
            assert row.tool_calls >= 5  # manifest, three discovered sources, and actual artifact
            assert not row.recovered
    assert report.as_dict()["by_split"]["holdout"]["baseline"]["verified_resolution_rate"] == 1.0


def test_information_grader_rejects_wrong_selection_and_changed_sources():
    import json

    from examples.solver_benchmark import LocalEnvironment, cases, grade_environment

    case = next((case for case in cases() if case.id == "investigate-holdout"), None)
    assert case is not None, "changed-data information-gathering holdout is required"
    environment = LocalEnvironment(case)
    good = {
        "pages": [
            {"service": "auth", "owner": "identity"},
            {"service": "search", "owner": "discovery"},
        ]
    }
    environment.documents[case.destination] = json.dumps(good)
    assert grade_environment(case, environment).passed
    assert environment.verify(case.problem(), None, None).verdict == "verified"
    environment.documents[case.destination] = json.dumps(
        {"pages": [{"service": "checkout", "owner": "payments"}]}
    )
    assert not grade_environment(case, environment).passed
    assert environment.verify(case.problem(), None, None).verdict == "failed"
    environment.documents[case.destination] = json.dumps(good)
    environment.documents[case.source] = "{}"
    assert not grade_environment(case, environment).passed
    assert environment.verify(case.problem(), None, None).verdict != "verified"


def test_investigation_policy_uses_observed_documents_without_hidden_fixture_answers():
    import json

    from langchain_core.messages import HumanMessage, ToolMessage

    from examples.solver_benchmark import Case, ScriptedModel, cases

    class HiddenCase(Case):
        @property
        def initial_source(self):
            raise AssertionError("model must not access hidden source data")

        @property
        def initial_documents(self):
            raise AssertionError("model must not access hidden fixture data")

    case = HiddenCase("observations-only", "investigation")
    assert case.family in {item.family for item in cases()}, "investigation family is required"
    model = ScriptedModel(case=case)
    documents = {
        case.source: {
            "inventory": "custom/registry",
            "observations": "custom/signals",
            "policy": "custom/rule",
        },
        "custom/registry": {
            "services": [
                {"id": "delta", "owner": "new-owner", "active": True},
                {"id": "alpha", "owner": "ignored", "active": False},
            ]
        },
        "custom/signals": {"error_rates": {"delta": 14, "alpha": 99}},
        "custom/rule": {"page_above": 12},
    }
    messages = [HumanMessage(case.problem().model_dump_json())]
    for _ in range(8):
        response = model.invoke(messages)
        messages.append(response)
        call = response.tool_calls[0]
        if call["name"] == "write_document":
            assert json.loads(call["args"]["content"]) == {
                "pages": [{"service": "delta", "owner": "new-owner"}]
            }
            return
        assert call["name"] == "read_document"
        observed = json.dumps({"ok": True, "content": json.dumps(documents[call["args"]["path"]])})
        messages.append(
            ToolMessage(content=observed, name="read_document", tool_call_id=call["id"])
        )
    pytest.fail("policy never produced the requested artifact")


@pytest.mark.asyncio
async def test_failed_model_call_keeps_cost_unknown_instead_of_reporting_a_subtotal():
    from examples.solver_benchmark import ScriptedModel, cases, run_benchmark

    class FailingModel(ScriptedModel):
        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            if self._turn:
                raise RuntimeError("controlled local failure: private-provider-detail")
            result = super()._generate(messages, stop, run_manager, **kwargs)
            result.generations[0].message.usage_metadata = {
                "input_tokens": 6,
                "output_tokens": 4,
                "total_tokens": 10,
            }
            return result

    case = next(case for case in cases() if case.id == "config-direct")
    report = await run_benchmark((case,), model_factory=lambda: FailingModel(case=case))
    for row in report.rows:
        assert "RuntimeError" in row.error
        assert "private-provider-detail" not in row.error
        assert not row.claimed_success
        assert row.total_tokens is None
        assert row.observed_tokens == 10
        assert row.total_model_calls == 2


@pytest.mark.asyncio
async def test_summary_usage_survives_a_graph_failure_after_the_summary_completed():
    from langchain_core.messages import AIMessage
    from langchain_core.outputs import ChatGeneration, ChatResult

    from examples.solver_benchmark import ScriptedModel, cases, run_benchmark

    class SummaryModel(ScriptedModel):
        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            if str(messages[0].content).startswith("Summarize the following conversation"):
                result = ChatResult(
                    generations=[ChatGeneration(message=AIMessage(content="Config task remains."))]
                )
            else:
                result = super()._generate(messages, stop, run_manager, **kwargs)
            result.generations[0].message.usage_metadata = {
                "input_tokens": 6,
                "output_tokens": 4,
                "total_tokens": 10,
            }
            return result

    case = next(case for case in cases() if case.id == "config-direct")
    # The native prompt exceeds this model's local context profile, so compaction
    # executes and then fails safely. This makes no provider/network calls.
    report = await run_benchmark(
        (case,), model_factory=lambda: SummaryModel(case=case, profile={"max_input_tokens": 1024})
    )
    solver = next(row for row in report.rows if row.arm == "solver")
    assert "ContextOverflowError" in solver.error
    assert solver.summary_calls == 1
    assert solver.model_calls == 0
    assert solver.total_model_calls == 1
    assert solver.total_tokens == 10
    assert not solver.claimed_success
