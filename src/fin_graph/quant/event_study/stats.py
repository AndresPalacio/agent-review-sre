"""Pure statistical functions for event studies."""

from __future__ import annotations

from typing import Any

from fin_graph.quant.event_study.models import BootstrapCI, MarketModelFit


def fit_market_model(stock_returns: Any, market_returns: Any) -> MarketModelFit:
    import numpy as np

    stock = np.asarray(stock_returns, dtype=float)
    market = np.asarray(market_returns, dtype=float)
    if stock.shape != market.shape or stock.ndim != 1 or len(stock) < 2:
        raise ValueError("market-model series must be aligned one-dimensional arrays")
    design = np.column_stack([np.ones(len(stock)), market])
    coefficients, _, _, _ = np.linalg.lstsq(design, stock, rcond=None)
    alpha, beta = float(coefficients[0]), float(coefficients[1])
    predicted = alpha + beta * market
    residual = float(np.sum((stock - predicted) ** 2))
    total = float(np.sum((stock - stock.mean()) ** 2))
    return MarketModelFit(
        alpha=alpha,
        beta=beta,
        r_squared=1 - residual / total if total > 0 else 0,
        n_obs=len(stock),
    )


def compute_abnormal_returns(
    stock_returns: Any, market_returns: Any, alpha: float, beta: float
) -> Any:
    return stock_returns - (alpha + beta * market_returns)


def sum_car(values: Any, start: int, end: int) -> float:
    import numpy as np

    return float(np.sum(values[start : end + 1]))


def ttest_cars(values: Any) -> tuple[float, float]:
    from scipy import stats  # type: ignore[import-untyped]

    if len(values) < 2:
        return 0, 1
    statistic, probability = stats.ttest_1samp(values, popmean=0)
    return float(statistic), float(probability)


def bootstrap_ci(
    values: Any,
    *,
    n_bootstrap: int,
    confidence: float = 0.95,
    seed: int | None,
) -> BootstrapCI:
    import numpy as np

    values = np.asarray(values, dtype=float)
    generator = np.random.default_rng(seed)
    samples = generator.choice(values, size=(n_bootstrap, len(values)), replace=True)
    means = samples.mean(axis=1)
    tail = (1 - confidence) / 2 * 100
    lower, upper = np.percentile(means, [tail, 100 - tail])
    return BootstrapCI(
        lower=float(lower),
        upper=float(upper),
        confidence=confidence,
        n_bootstrap=n_bootstrap,
    )
