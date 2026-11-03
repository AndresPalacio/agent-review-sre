"""Historical backtester launcher."""

from __future__ import annotations

from typing import Any


def run_backtest(backtester: Any) -> Any:
    return backtester.run_backtest()


__all__ = ["run_backtest"]
