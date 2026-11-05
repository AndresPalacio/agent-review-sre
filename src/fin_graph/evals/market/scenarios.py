"""Equity scenarios: market fixtures per ticker, the request, and the expected answer.

``data/equities/NNN-name/`` holds ``scenario.yml`` (tickers, as_of, horizon, question),
``answer.yml`` (expected signal and thesis category per ticker, the tools the analysis should
use) and one folder per ticker in the market-fixtures layout read by
:func:`load_market_fixtures`. Synthetic scenarios come from ``scripts/build_equity_scenarios.py``;
the ``*-2024-03`` seeds are ai-hedge-fund's recorded API fixtures (MIT).
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from fin_graph.domain.quant.common import Signal
from fin_graph.integrations.market_data import StaticMarketData, load_market_fixtures

DATA_ROOT = Path(__file__).resolve().parents[1] / "data" / "equities"
SMOKE = ("001-deep-value", "003-overvalued", "013-aapl-2024-03")
_SAFE_ID = re.compile(r"[0-9]{3}-[a-z0-9-]+\Z")


class TickerAnswer(BaseModel):
    """What a sound analysis concludes for one ticker."""

    expected_signal: Signal
    thesis_category: str
    equivalent_categories: list[str] = Field(default_factory=list)
    forbidden_categories: list[str] = Field(default_factory=list)
    #: Each keyword may list alternatives with ``|`` (English or Spanish wording).
    required_keywords: list[str] = Field(default_factory=list)
    expected_action: str | None = None


class EquityAnswer(BaseModel):
    tickers: dict[str, TickerAnswer]
    optimal_trajectory: list[str] = Field(default_factory=list)
    required_queries: list[str] = Field(default_factory=list)
    max_waves: int = 2


@dataclass(frozen=True)
class EquityScenario:
    scenario_id: str
    metadata: dict[str, Any]
    answer: EquityAnswer
    directory: Path

    @property
    def key(self) -> str:
        return f"equities/{self.scenario_id}"

    @property
    def tickers(self) -> list[str]:
        return [str(item) for item in self.metadata["tickers"]]

    @property
    def as_of(self) -> date:
        return date.fromisoformat(str(self.metadata["as_of"]))

    def request(self) -> dict[str, Any]:
        """The raw request the graph receives."""
        payload: dict[str, Any] = {
            "tickers": self.tickers,
            "as_of": self.as_of.isoformat(),
            "horizon": self.metadata.get("horizon", "medium"),
        }
        if self.metadata.get("question"):
            payload["question"] = self.metadata["question"]
        return payload

    def market(self) -> StaticMarketData:
        return load_market_fixtures(self.directory)

    def fingerprint(self) -> str:
        """Hash of every file of the scenario, so results bind to the exact inputs."""
        digest = hashlib.sha256()
        for path in sorted(self.directory.rglob("*")):
            if path.is_file():
                digest.update(path.relative_to(self.directory).as_posix().encode())
                content = path.read_bytes()
                if path.suffix.lower() in {".json", ".yaml", ".yml"}:
                    content = content.replace(b"\r\n", b"\n")
                digest.update(content)
        return digest.hexdigest()


def _yaml(path: Path) -> dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected a YAML object: {path.name}")
    return value


def load_equity_scenario(scenario_id: str, root: Path = DATA_ROOT) -> EquityScenario:
    if not _SAFE_ID.fullmatch(scenario_id):
        raise ValueError(f"invalid scenario id: {scenario_id}")
    directory = root / scenario_id
    metadata = _yaml(directory / "scenario.yml")
    answer = EquityAnswer.model_validate(_yaml(directory / "answer.yml"))
    tickers = {str(item) for item in metadata.get("tickers", [])}
    if not tickers or set(answer.tickers) != tickers:
        raise ValueError(f"{scenario_id}: answer tickers must match the scenario tickers")
    return EquityScenario(scenario_id, metadata, answer, directory)


def load_equity_case(key: str, root: Path = DATA_ROOT) -> EquityScenario:
    """The scenario a result or split manifest names by its key, ``equities/<id>``."""
    group, _, scenario_id = key.partition("/")
    if group != "equities":
        raise ValueError(f"not an equity scenario key: {key}")
    return load_equity_scenario(scenario_id, root)


def equity_scenario_ids(root: Path = DATA_ROOT) -> list[str]:
    return sorted(path.name for path in root.iterdir() if (path / "scenario.yml").is_file())


def load_equity_suite(suite: str, root: Path = DATA_ROOT) -> list[EquityScenario]:
    """``smoke`` (three cases), ``all`` or ``equities`` (every case), or one scenario id."""
    if suite == "smoke":
        ids = list(SMOKE)
    elif suite in ("all", "equities"):
        ids = equity_scenario_ids(root)
    elif _SAFE_ID.fullmatch(suite) and (root / suite).is_dir():
        ids = [suite]
    else:
        raise ValueError(f"unknown equity suite: {suite}")
    return [load_equity_scenario(item, root) for item in ids]


def dataset_fingerprint(scenarios: list[EquityScenario]) -> str:
    payload = json.dumps(
        {scenario.key: scenario.fingerprint() for scenario in scenarios}, sort_keys=True
    )
    return hashlib.sha256(payload.encode()).hexdigest()
