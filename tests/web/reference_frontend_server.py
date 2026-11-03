"""Build and serve the frozen AI Hedge Fund frontend without modifying its source."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


def resolve_reference_frontend() -> Path:
    configured = os.getenv("AI_HEDGE_FUND_REFERENCE")
    repository = Path(__file__).resolve().parents[2]
    candidates = [
        Path(configured).expanduser() if configured else None,
        Path.home() / "Desktop" / "ai-hedge-fund" / "app" / "frontend",
        repository.parent / "ai-hedge-fund" / "app" / "frontend",
    ]
    for candidate in candidates:
        if candidate is not None and (candidate / "package.json").is_file():
            return candidate.resolve()
    raise FileNotFoundError(
        "AI Hedge Fund frontend not found; set AI_HEDGE_FUND_REFERENCE to app/frontend"
    )


def build_reference_frontend(frontend: Path) -> Path:
    environment = {
        **os.environ,
        "VITE_API_URL": os.getenv("REFERENCE_API_URL", "http://localhost:8000"),
    }
    npm = shutil.which("npm.cmd" if os.name == "nt" else "npm")
    if npm is None:
        raise FileNotFoundError("npm executable not found")
    if not (frontend / "node_modules").is_dir():
        subprocess.run([npm, "ci"], cwd=frontend, env=environment, check=True)
    subprocess.run([npm, "run", "build"], cwd=frontend, env=environment, check=True)
    output = frontend / "dist"
    if not (output / "index.html").is_file():
        raise RuntimeError("reference frontend build produced no index.html")
    return output


def main() -> None:
    frontend = resolve_reference_frontend()
    port = int(os.getenv("REFERENCE_FRONTEND_PORT", "4174"))
    npm = shutil.which("npm.cmd" if os.name == "nt" else "npm")
    if npm is None:
        raise FileNotFoundError("npm executable not found")
    if not (frontend / "node_modules").is_dir():
        subprocess.run([npm, "ci"], cwd=frontend, check=True)
    environment = {
        **os.environ,
        "VITE_API_URL": os.getenv("REFERENCE_API_URL", "http://localhost:8000"),
    }
    subprocess.run(
        [npm, "run", "dev", "--", "--host", "127.0.0.1", "--port", str(port), "--strictPort"],
        cwd=frontend,
        env=environment,
        check=True,
    )


if __name__ == "__main__":
    main()
