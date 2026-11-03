"""Build the bundled web application before creating a wheel."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class CustomBuildHook(BuildHookInterface):
    """Materialize the ignored Vite output required by the Python wheel."""

    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        npm = shutil.which("npm")
        if npm is None:
            raise RuntimeError("Node.js/npm is required to build the fin-graph wheel")

        web_root = Path(self.root) / "web"
        subprocess.run([npm, "ci"], cwd=web_root, check=True)
        subprocess.run([npm, "run", "build"], cwd=web_root, check=True)
