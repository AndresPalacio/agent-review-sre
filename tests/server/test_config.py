from __future__ import annotations

import pytest
from pydantic import SecretStr

from sre_graph.server.config import ServerConfig


def test_server_requires_postgres_and_explicit_cors() -> None:
    with pytest.raises(ValueError, match="DATABASE_URL"):
        ServerConfig(_env_file=None).require_database_url()
    with pytest.raises(ValueError, match="Postgres"):
        ServerConfig(_env_file=None, database_url=SecretStr("sqlite:///db")).require_database_url()
    config = ServerConfig(_env_file=None, cors_origins="https://a.example, https://b.example")
    assert config.cors_origins == ("https://a.example", "https://b.example")


def test_alert_routes_are_loaded_from_json_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALERT_DELIVERY_ROUTES", '{"default":{"slack":"C1"}}')
    assert ServerConfig(_env_file=None).alert_delivery_routes == {"default": {"slack": "C1"}}


@pytest.mark.parametrize(
    "routes",
    [
        {"urgent": {"slack": "C1"}},
        {"default": {"email": "ops@example.com"}},
        {"critical": {"slack": " "}},
        {"default": {"telegram": "1" * 129}},
    ],
)
def test_alert_routes_reject_invalid_severity_channels_and_destinations(routes) -> None:
    with pytest.raises(ValueError):
        ServerConfig(_env_file=None, alert_delivery_routes=routes)
