"""The wheel is self-contained and imports from an isolated extraction."""

from __future__ import annotations

import os
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).parents[2]


def _build(tmp_path: Path) -> Path:
    destination = tmp_path / "dist"
    completed = subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(destination)],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    return next(destination.glob("*.whl"))


def test_wheel_contains_app_static_and_every_compatibility_namespace(tmp_path: Path) -> None:
    wheel = _build(tmp_path)
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        assert "fin_graph/server/static/index.html" in names
        assert any(name.startswith("fin_graph/server/static/assets/") for name in names)
        for namespace in ("fin_graph/", "src/", "v2/", "app/"):
            assert any(name.startswith(namespace) for name in names)
        lowered_names = [name.lower() for name in names]
        assert not any("ai-hedge-fund" in name for name in lowered_names)
        assert not any(name.endswith("optimization/fin.toml") for name in lowered_names)
        assert not any(name.endswith((".sqlite", ".db", ".env", ".key")) for name in names)
        packaged_eval_data = [
            name for name in lowered_names if name.startswith("fin_graph/evals/data/")
        ]
        assert packaged_eval_data
        assert all(
            name.startswith("fin_graph/evals/data/equities/") for name in packaged_eval_data
        )


def test_extracted_wheel_imports_without_repository_on_pythonpath(tmp_path: Path) -> None:
    wheel = _build(tmp_path)
    extracted = tmp_path / "site"
    with zipfile.ZipFile(wheel) as archive:
        archive.extractall(extracted)
    code = """
import builtins, pathlib, sys
root = pathlib.Path(sys.argv[1]).resolve()
repository = pathlib.Path.cwd().resolve()
outside_repository = [
    item for item in sys.path
    if item and pathlib.Path(item).resolve() != repository
]
sys.path[:] = [str(root), *outside_repository]
original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name == 'better_harness' or name.startswith('better_harness.'):
        raise AssertionError(f'production import attempted optional dependency: {name}')
    return original_import(name, *args, **kwargs)
builtins.__import__ = guarded_import
import fin_graph
from fin_graph.cli.main import app
from fin_graph.evals.market import runner as market_runner
from fin_graph.server.factory import RuntimeProfile
from src.main import run_hedge_fund
from app.backend.main import app as legacy_app
assert pathlib.Path(fin_graph.__file__).resolve().is_relative_to(root)
assert callable(run_hedge_fund)
assert legacy_app.title
assert RuntimeProfile.local(data_dir=root / 'data').backend == 'sqlite'
assert app.info.help
assert market_runner.run_equity_suite
"""
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    completed = subprocess.run(
        [sys.executable, "-c", code, str(extracted)],
        cwd=tmp_path,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
