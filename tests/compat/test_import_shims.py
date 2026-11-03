from __future__ import annotations

import ast
import importlib
import inspect
from pathlib import Path

from fastapi import FastAPI


def test_documented_legacy_imports_resolve_to_native_objects() -> None:
    from app.backend.main import app
    from src.backtester import run_backtest
    from src.main import create_workflow, parse_hedge_fund_response, run_hedge_fund
    from v2.backtesting import BacktestEngine
    from v2.event_study import EventStudyResult, compute_car
    from v2.models import Signal
    from v2.risk import simulate_gbm_paths
    from v2.signals import ALPHA_MODEL_REGISTRY, PEADModel
    from v2.strategies import ALGORITHM_REGISTRY

    assert isinstance(app, FastAPI)
    assert callable(run_hedge_fund)
    assert callable(create_workflow)
    assert callable(parse_hedge_fund_response)
    assert callable(run_backtest)
    assert BacktestEngine is not None
    assert EventStudyResult is not None
    assert Signal is not None
    assert callable(compute_car)
    assert callable(simulate_gbm_paths)
    assert ALPHA_MODEL_REGISTRY["pead"] is PEADModel
    assert set(ALGORITHM_REGISTRY) == {
        "momentum",
        "rsi_mean_reversion",
        "sma_crossover",
    }


def test_run_hedge_fund_keeps_the_reference_signature() -> None:
    from src.main import run_hedge_fund

    assert list(inspect.signature(run_hedge_fund).parameters) == [
        "tickers",
        "start_date",
        "end_date",
        "portfolio",
        "show_reasoning",
        "selected_analysts",
        "model_name",
        "model_provider",
    ]


def test_every_public_v2_compatibility_module_imports() -> None:
    modules = (
        "v2.models",
        "v2.data",
        "v2.data.models",
        "v2.data.protocol",
        "v2.features",
        "v2.features.snapshot",
        "v2.portfolio",
        "v2.portfolio.consensus",
        "v2.portfolio.optimizer",
        "v2.pipeline.execution",
        "v2.risk",
        "v2.risk.montecarlo",
        "v2.signals",
        "v2.signals.base",
        "v2.signals.pead",
        "v2.backtesting",
        "v2.event_study",
        "v2.strategies",
        "v2.strategies.catalog",
        "v2.strategies.library",
        "v2.strategy_backtesting",
    )

    for name in modules:
        assert importlib.import_module(name).__name__ == name


def test_shims_do_not_reimplement_financial_formulas() -> None:
    root = Path(__file__).parents[2] / "src"
    shim_roots = (root / "src", root / "v2", root / "app")
    forbidden = {"numpy", "pandas", "scipy", "sklearn", "statsmodels"}
    for shim_root in shim_roots:
        for path in shim_root.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            imported = {
                node.names[0].name.split(".")[0]
                for node in ast.walk(tree)
                if isinstance(node, ast.Import) and node.names
            }
            imported.update(
                node.module.split(".")[0]
                for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom) and node.module
            )
            assert imported.isdisjoint(forbidden), path
