from __future__ import annotations

import numpy as np
import pytest

from fin_graph.quant.risk import (
    RiskSimulationRequest,
    black_scholes_price,
    monte_carlo_option_price,
    simulate_gbm_paths,
    simulate_risk,
)


def test_gbm_option_and_var_are_seeded_and_finite() -> None:
    first = simulate_gbm_paths(100, 0.001, 0.02, n_steps=5, n_sims=100, seed=7)
    second = simulate_gbm_paths(100, 0.001, 0.02, n_steps=5, n_sims=100, seed=7)
    assert np.array_equal(first, second)
    assert first.shape == (100, 6)

    option = monte_carlo_option_price(100, 100, 0.03, 0.2, 1, n_sims=100_000, seed=7)
    analytic = black_scholes_price(100, 100, 0.03, 0.2, 1)
    assert option.price == pytest.approx(analytic, abs=0.12)

    rng = np.random.default_rng(4)
    returns = rng.normal([0.0005, 0.0002], [0.01, 0.015], size=(252, 2))
    request = RiskSimulationRequest(
        returns=returns.tolist(),
        weights=[0.6, 0.4],
        portfolio_value=100_000,
        confidence=0.95,
        horizon_days=5,
        simulations=20_000,
        seed=9,
    )
    risk = simulate_risk(request)
    assert risk == simulate_risk(request)
    assert risk.var > 0 and risk.cvar >= risk.var
    assert risk.seed == 9


@pytest.mark.parametrize(
    "change",
    [
        {"weights": [1.0]},
        {"weights": [0.8, 0.8]},
        {"returns": [[0.1, float("nan")], [0.2, 0.1]]},
        {"portfolio_value": 0},
    ],
)
def test_risk_rejects_invalid_shapes_weights_and_values(change) -> None:
    base = {
        "returns": [[0.01, 0.02], [-0.01, 0.01], [0.02, -0.02]],
        "weights": [0.5, 0.5],
        "portfolio_value": 1_000,
    }
    with pytest.raises(ValueError):
        simulate_risk(RiskSimulationRequest.model_construct(**{**base, **change}))
