import pytest

from fin_graph.harness.dataset import load_manifest


@pytest.mark.parametrize("scenario", [c.scenario for c in load_manifest().select("train")])
async def test_analysis(scenario, evaluate_analysis):
    await evaluate_analysis(scenario)
