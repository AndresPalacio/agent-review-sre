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
