import pytest

from fin_graph.harness.dataset import load_manifest


@pytest.mark.parametrize("scenario", [c.scenario for c in load_manifest().select("scorecard")])
async def test_rca(scenario, evaluate_rca):
    await evaluate_rca(scenario)
