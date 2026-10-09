"""Copy the historical OpenSRE scenario data without importing its code."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path

SOURCE_REV = "ce77b4a0b^"
SUITES = ("eks", "rds_postgres", "rds_upstream")
SCENARIO_FILE = re.compile(r"^[0-9]{3}-[a-z0-9-]+/[^/]+\.(?:json|yml)$")


def git(repo: Path, *args: str) -> bytes:
    return subprocess.check_output(["git", "-C", str(repo), *args])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-repo", type=Path, default=Path(__file__).resolve().parents[2] / "opensre"
    )
    args = parser.parse_args()
    source = args.source_repo.resolve()
    target = Path(__file__).resolve().parents[1] / "src" / "sre_graph" / "evals" / "data"
    commit = git(source, "rev-parse", SOURCE_REV).decode().strip()
    manifest: dict[str, object] = {
        "source": "https://github.com/Tracer-Cloud/opensre",
        "commit": commit,
        "files": {},
    }
    files: dict[str, str] = {}
    for suite in SUITES:
        root = f"tests/synthetic/{suite}"
        names = git(source, "ls-tree", "-r", "--name-only", f"{SOURCE_REV}:{root}")
        for relative in names.decode().splitlines():
            if not SCENARIO_FILE.fullmatch(relative):
                continue
            payload = git(source, "show", f"{SOURCE_REV}:{root}/{relative}")
            payload = payload.rstrip(b"\r\n") + b"\n"
            destination = target / suite / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(payload)
            files[f"{suite}/{relative}"] = hashlib.sha256(payload).hexdigest()
    manifest["files"] = files
    target.mkdir(parents=True, exist_ok=True)
    (target / "manifest.json").write_bytes(
        (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    )
    print(f"Ported {len(files)} JSON/YAML files from {commit}")


if __name__ == "__main__":
    main()
