from __future__ import annotations

import tomllib
from pathlib import Path


def test_wheel_includes_every_compatibility_package() -> None:
    root = Path(__file__).parents[2]
    config = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))

    assert config["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"] == [
        "src/fin_graph",
        "src/src",
        "src/v2",
        "src/app",
    ]
