"""Static container/Compose guarantees that do not require a running Docker daemon."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).parents[2]


def test_image_builds_ui_installs_complete_profile_and_runs_non_root() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "npm ci" in dockerfile and "npm run build" in dockerfile
    assert "--extra app" in dockerfile
    assert "USER fingraph" in dockerfile
    assert "install -d -o fingraph -g fingraph /data" in dockerfile
    assert "HEALTHCHECK" in dockerfile
    assert "ai-hedge-fund" not in dockerfile.lower()


def test_compose_has_loopback_local_and_distributed_postgres_profiles() -> None:
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    services = compose["services"]
    assert services["app"]["ports"] == ["127.0.0.1:8000:8000"]
    assert services["app"]["healthcheck"]["test"]
    assert services["server"]["profiles"] == ["distributed"]
    assert services["postgres"]["profiles"] == ["distributed"]
    assert services["server"]["depends_on"]["postgres"]["condition"] == "service_healthy"
    assert services["server"]["healthcheck"]["test"]


def test_docker_context_excludes_private_and_reference_state() -> None:
    ignored = (ROOT / ".dockerignore").read_text(encoding="utf-8")
    for entry in (".env", ".git", ".venv", "dist", "web/node_modules", "*.sqlite", "*.db"):
        assert entry in ignored.splitlines()
