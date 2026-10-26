"""Run editable code in a disposable source copy with a process deadline."""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from better_harness.core import Variant

from fin_graph.harness.configuration import HarnessConfig
from fin_graph.harness.profile import resolve_profile
from fin_graph.harness.surfaces import FILE_SURFACES, SURFACES, validate_values
from fin_graph.safety.secrets import redact_secrets


def execute_variant(
    config: HarnessConfig, variant: Variant, scenarios: list[str], output: Path
) -> dict[str, Any]:
    validate_values(variant.values, complete=True)
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    request = output / "worker-request.json"
    request.write_text(
        json.dumps(
            {
                "model": config.experiment.model,
                "repeat": config.options.repeat,
                "case_timeout": config.options.case_timeout,
                "judge_model": config.options.judge_model
                if config.experiment.model != "scripted"
                else None,
                "scenarios": scenarios,
                "output": str(output),
                "prompts": {name: variant.values[name] for name in SURFACES},
                "runtime_limits": asdict(resolve_profile(verify_variant_pin=False).limits),
            }
        ),
        encoding="utf-8",
    )
    with TemporaryDirectory(prefix="sre-harness-") as temporary:
        root = Path(temporary)
        shutil.copytree(
            config.experiment.workspace_root / "src/fin_graph",
            root / "src/fin_graph",
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "static"),
        )
        for name, relative in FILE_SURFACES.items():
            (root / relative).write_text(variant.values[name], encoding="utf-8")
        env = os.environ.copy()
        env.update(
            PYTHONPATH=str(root / "src"),
            PYTHONUTF8="1",
            PYTHONDONTWRITEBYTECODE="1",
            LANGSMITH_TRACING="false",
        )
        env.pop("BETTER_HARNESS_VARIANT_FILE", None)
        command = [sys.executable, "-m", "fin_graph.harness.worker", str(request)]
        with subprocess.Popen(
            command,
            cwd=root,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=os.name != "nt",
        ) as process:
            try:
                stdout, _ = process.communicate(timeout=config.options.split_timeout)
            except subprocess.TimeoutExpired as exc:
                if sys.platform == "win32":
                    subprocess.run(
                        ["taskkill", "/F", "/T", "/PID", str(process.pid)],
                        capture_output=True,
                        check=False,
                    )
                else:
                    os.killpg(process.pid, signal.SIGKILL)
                process.kill()
                stdout, _ = process.communicate()
                (output / "worker.log").write_text(
                    redact_secrets(stdout.decode("utf-8", errors="replace")), encoding="utf-8"
                )
                raise TimeoutError("evaluation worker exceeded split_timeout") from exc
        (output / "worker.log").write_text(
            redact_secrets(stdout.decode("utf-8", errors="replace")), encoding="utf-8"
        )
        if process.returncode:
            raise RuntimeError("evaluation worker failed; see worker.log")
    result = json.loads((output / "results.json").read_text(encoding="utf-8"))
    expected = {
        (scenario, attempt)
        for scenario in scenarios
        for attempt in range(1, config.options.repeat + 1)
    }
    records = result.get("runs", [])
    observed = {(record["scenario"], record["attempt"]) for record in records}
    if observed != expected or len(records) != len(expected):
        raise RuntimeError("worker returned incomplete or duplicate cases")
    return dict(result)
