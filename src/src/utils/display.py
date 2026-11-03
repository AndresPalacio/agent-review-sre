"""Pipe-safe legacy result display."""

from __future__ import annotations

import json


def print_trading_output(result: dict[str, object]) -> None:
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


__all__ = ["print_trading_output"]
