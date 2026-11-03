"""Seeded GBM, option-pricing, and portfolio VaR/CVaR services."""

from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

_COVARIANCE_JITTER = 1e-12


class OptionPriceResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    price: float
    std_error: float
    option_type: Literal["call", "put"]
    simulations: int
    steps: int
    seed: int | None


class RiskSimulationRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    returns: list[list[float]]
    weights: list[float]
    portfolio_value: float = Field(gt=0)
    confidence: float = Field(default=0.95, gt=0, lt=1)
    horizon_days: int = Field(default=1, ge=1)
    simulations: int = Field(default=100_000, ge=1)
    seed: int | None = None


class RiskSimulation(BaseModel):
    model_config = ConfigDict(frozen=True)

    var: float
    cvar: float
    confidence: float
    horizon_days: int
    simulations: int
    portfolio_value: float
    expected_pnl: float
    portfolio_sigma: float
    var_pct: float
    seed: int | None
    method: str = "multivariate-normal-monte-carlo"


def simulate_gbm_paths(
    initial_price: float,
    drift: float,
    volatility: float,
    *,
    n_steps: int,
    n_sims: int,
    dt: float = 1,
    seed: int | None = None,
) -> Any:
    import numpy as np

    if initial_price <= 0 or volatility < 0 or n_steps < 1 or n_sims < 1 or dt <= 0:
        raise ValueError("invalid GBM parameters")
    generator = np.random.default_rng(seed)
    shocks = generator.standard_normal((n_sims, n_steps))
    drift_term = (drift - 0.5 * volatility**2) * dt
    log_increments = drift_term + volatility * math.sqrt(dt) * shocks
    paths = initial_price * np.exp(np.cumsum(log_increments, axis=1))
    return np.hstack([np.full((n_sims, 1), initial_price), paths])


def monte_carlo_option_price(
    initial_price: float,
    strike: float,
    rate: float,
    volatility: float,
    years: float,
    *,
    option_type: Literal["call", "put"] = "call",
    n_sims: int = 100_000,
    n_steps: int = 1,
    seed: int | None = None,
) -> OptionPriceResult:
    import numpy as np

    if strike <= 0 or years <= 0 or option_type not in {"call", "put"}:
        raise ValueError("invalid option parameters")
    paths = simulate_gbm_paths(
        initial_price,
        rate,
        volatility,
        n_steps=n_steps,
        n_sims=n_sims,
        dt=years / n_steps,
        seed=seed,
    )
    terminal = paths[:, -1]
    payoff = (
        np.maximum(terminal - strike, 0)
        if option_type == "call"
        else np.maximum(strike - terminal, 0)
    )
    discount = math.exp(-rate * years)
    deviation = payoff.std(ddof=1) if n_sims > 1 else 0
    return OptionPriceResult(
        price=float(discount * payoff.mean()),
        std_error=float(discount * deviation / math.sqrt(n_sims)),
        option_type=option_type,
        simulations=n_sims,
        steps=n_steps,
        seed=seed,
    )


def black_scholes_price(
    initial_price: float,
    strike: float,
    rate: float,
    volatility: float,
    years: float,
    *,
    option_type: Literal["call", "put"] = "call",
) -> float:
    from scipy.stats import norm  # type: ignore[import-untyped]

    if initial_price <= 0 or strike <= 0 or volatility <= 0 or years <= 0:
        raise ValueError("Black-Scholes inputs must be positive")
    root = math.sqrt(years)
    first = (math.log(initial_price / strike) + (rate + 0.5 * volatility**2) * years) / (
        volatility * root
    )
    second = first - volatility * root
    if option_type == "call":
        return float(
            initial_price * norm.cdf(first) - strike * math.exp(-rate * years) * norm.cdf(second)
        )
    if option_type == "put":
        return float(
            strike * math.exp(-rate * years) * norm.cdf(-second) - initial_price * norm.cdf(-first)
        )
    raise ValueError("option_type must be call or put")


def simulate_risk(request: RiskSimulationRequest) -> RiskSimulation:
    import numpy as np

    returns = np.asarray(request.returns, dtype=float)
    weights = np.asarray(request.weights, dtype=float)
    if returns.ndim != 2 or len(returns) < 2:
        raise ValueError("returns must be a 2-D matrix with at least two observations")
    if not np.isfinite(returns).all() or not np.isfinite(weights).all():
        raise ValueError("returns and weights must be finite")
    assets = returns.shape[1]
    if weights.shape != (assets,):
        raise ValueError("weights length must match return columns")
    if abs(float(weights.sum()) - 1) > 1e-6:
        raise ValueError("weights must sum to one")
    if request.portfolio_value <= 0 or not math.isfinite(request.portfolio_value):
        raise ValueError("portfolio value must be finite and positive")
    if not 0 < request.confidence < 1 or request.horizon_days < 1:
        raise ValueError("invalid risk horizon or confidence")
    mean = returns.mean(axis=0)
    covariance = np.atleast_2d(np.cov(returns, rowvar=False))
    if not np.isfinite(covariance).all():
        raise ValueError("return covariance is non-finite")
    factor = np.linalg.cholesky(covariance + _COVARIANCE_JITTER * np.eye(assets))
    generator = np.random.default_rng(request.seed)
    shocks = generator.standard_normal((request.simulations, assets))
    scenarios = mean * request.horizon_days + (shocks @ factor.T) * math.sqrt(request.horizon_days)
    pnl = request.portfolio_value * (scenarios @ weights)
    value_at_risk = float(-np.quantile(pnl, 1 - request.confidence))
    tail = pnl[pnl <= -value_at_risk]
    cvar = float(-tail.mean()) if len(tail) else value_at_risk
    sigma = float(np.sqrt(weights @ covariance @ weights))
    return RiskSimulation(
        var=value_at_risk,
        cvar=cvar,
        confidence=request.confidence,
        horizon_days=request.horizon_days,
        simulations=request.simulations,
        portfolio_value=request.portfolio_value,
        expected_pnl=float(request.portfolio_value * (mean @ weights) * request.horizon_days),
        portfolio_sigma=sigma,
        var_pct=value_at_risk / request.portfolio_value,
        seed=request.seed,
    )
