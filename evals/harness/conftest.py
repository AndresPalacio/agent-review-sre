"""Provider-enabled evals live outside the ordinary credential-isolated test suite."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from fin_graph.evals.runner import run_suite
from fin_graph.evals.scenarios import load_scenario
from fin_graph.harness.configuration import load_config
from fin_graph.harness.quality import assess_record


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item, call):
    report = yield
    # Upstream matches JUnit file attributes to POSIX pytest nodeids on all platforms.
    filename, lineno, name = report.location
    report.location = (filename.replace("\\", "/"), lineno, name)
    return report


@pytest.fixture
def evaluate_rca(request, tmp_path):
    async def evaluate(key: str):
        model = request.config.getoption("--harness-model")
        options = load_config(
            Path(request.config.getoption("--harness-config")),
            model=model,
            repeat=request.config.getoption("--harness-repeat"),
        ).options
        repeat = 1 if model == "scripted" else options.repeat
        result = await run_suite(
            "harness-case",
            model,
            repeat,
            tmp_path,
            scenarios=[load_scenario(*key.split("/", 1))],
            case_timeout=options.case_timeout,
            judge_model=options.judge_model if model != "scripted" else None,
        )
        destination = request.config.getoption("--evals-report-file")
        if destination:
            path = Path(destination)
            await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)
            await asyncio.to_thread(path.write_text, json.dumps(result, indent=2), encoding="utf-8")
        assert all(
            r["status"] == "completed" and not r.get("graph_errors") for r in result["runs"]
        ), result
        if model != "scripted":
            failures = [
                assess_record(
                    record,
                    max_latency=options.max_case_latency,
                    max_tokens=options.max_case_tokens,
                    min_judge_score=options.min_judge_score if options.judge_model else None,
                )
                for record in result["runs"]
            ]
            assert not any(failures), failures

    return evaluate
