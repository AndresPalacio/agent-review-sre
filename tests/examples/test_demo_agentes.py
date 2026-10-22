import runpy
from pathlib import Path

import httpx


async def test_demo_runs_real_graph_and_explains_retry_without_network() -> None:
    module = runpy.run_path(str(Path(__file__).parents[2] / "examples/demo_agentes.py"))
    app = module["create_demo_app"]()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://demo"
    ) as client:
        response = await client.post("/v1/investigations")
        assert response.status_code == 200
        thread = response.json()["thread_id"]
        result = (await client.get(f"/v1/investigations/{thread}")).json()
        explanation = (await client.get(f"/v1/investigations/{thread}/explain")).json()
        stages = [step["node"] for step in explanation["steps"]]
        assert stages.count("plan") == 2
        assert "refine" in stages
        assert result["verification"]["passed"] is True
        assert result["evidence"]
        assert "OOMKilled" in result["diagnosis"]["root_cause"]
        assert not result["errors"]
        assert (await client.get("/v1/investigations/missing")).status_code == 404


async def test_debug_checkpoints_expose_committed_state_and_resume_without_restarting() -> None:
    """A fresh input on resume would repeat intake instead of completing the second wave."""
    module = runpy.run_path(str(Path(__file__).parents[2] / "examples/demo_agentes.py"))
    checkpoints = []

    def inspect_checkpoint(node, state, next_nodes):
        # Put a breakpoint here to debug this test in your IDE.
        checkpoints.append((node, state, next_nodes))

    result = await module["run_demo"](on_checkpoint=inspect_checkpoint)

    assert [node for node, _, _ in checkpoints].count("intake") == 1
    plans = [state for node, state, _ in checkpoints if node == "plan"]
    assert [state["wave"] for state in plans] == [1, 2]
    assert not plans[0]["evidence"]
    assert plans[1]["evidence"]
    assert plans[0]["harness"]["mode"] == "incident"
    verifications = [
        (state, next_nodes) for node, state, next_nodes in checkpoints if node == "verify"
    ]
    assert [state["verification"]["passed"] for state, _ in verifications] == [False, True]
    assert verifications[0][1] == ("refine",)
    assert verifications[1][1] == ("propose_remediation",)
    assert checkpoints[-1][2] == ()
    assert result["verification"]["passed"] is True
    assert not result["errors"]
    tool_calls = [event for event in result["harness_trace"] if event["kind"] == "tool_start"]
    assert len(tool_calls) == 2
    assert {event["name"] for event in tool_calls} == {"k8s_list_pods"}
    assert len({event["call_id"] for event in tool_calls}) == 2
    assert result["harness"]["variant"] == plans[0]["harness"]["variant"]
