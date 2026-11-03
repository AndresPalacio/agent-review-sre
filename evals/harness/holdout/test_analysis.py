import pytest

from fin_graph.optimization.harness.dataset import load_manifest


@pytest.mark.parametrize("scenario", [c.scenario for c in load_manifest().select("holdout")])
async def test_analysis(scenario, evaluate_analysis):
    await evaluate_analysis(scenario)
