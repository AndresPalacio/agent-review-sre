"""Historical risk names delegated to native seeded simulations."""

from fin_graph.quant.risk import (
    OptionPriceResult,
    RiskSimulationRequest,
    black_scholes_price,
    monte_carlo_option_price,
    simulate_gbm_paths,
    simulate_risk,
)
from fin_graph.quant.risk import (
    RiskSimulation as VaRResult,
)

monte_carlo_var = simulate_risk
portfolio_var = simulate_risk

__all__ = [
    "OptionPriceResult",
    "RiskSimulationRequest",
    "VaRResult",
    "black_scholes_price",
    "monte_carlo_option_price",
    "monte_carlo_var",
    "portfolio_var",
    "simulate_gbm_paths",
    "simulate_risk",
]
