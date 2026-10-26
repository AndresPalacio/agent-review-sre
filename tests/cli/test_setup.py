import pytest
from typer.testing import CliRunner

from fin_graph.cli.main import app
from fin_graph.integrations.setup import update_env


def test_atomic_setup_preserves_and_hides(tmp_path):
    path = tmp_path / "custom.env"
    path.write_text("# keep\nOTHER=value\nPROMETHEUS_ENABLED=false\n")
    summary = update_env(
        "prometheus",
        {"enabled": True, "url": "https://example.com", "token": "private-value"},
        path,
        dry_run=True,
    )
    assert "private-value" not in str(summary)
    assert "PROMETHEUS_TOKEN" not in path.read_text()
    update_env(
        "prometheus",
        {"enabled": True, "url": "https://example.com", "token": "private-value"},
        path,
    )
    assert "# keep\nOTHER=value\n" in path.read_text()
    assert "PROMETHEUS_ENABLED=true" in path.read_text()


def test_invalid_proposal_not_masked_by_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("PROMETHEUS_REQUEST_TIMEOUT_SECONDS", "20")
    path = tmp_path / "env"
    path.write_text("OTHER=value\n")
    with pytest.raises(ValueError):
        update_env(
            "prometheus",
            {"enabled": True, "url": "https://example.com", "request_timeout_seconds": -1},
            path,
        )
    assert path.read_text() == "OTHER=value\n"


def test_setup_cli_dryrun(tmp_path):
    config = tmp_path / "input.json"
    config.write_text('{"enabled":true,"url":"https://example.com"}')
    result = CliRunner().invoke(
        app,
        [
            "integrations",
            "setup",
            "prometheus",
            "--env-file",
            str(tmp_path / "env"),
            "--input-file",
            str(config),
            "--dry-run",
        ],
    )
    assert result.exit_code == 0, result.output
    assert not (tmp_path / "env").exists()


def test_list_cli():
    result = CliRunner().invoke(app, ["integrations", "list", "--env-file", "nonexistent.env"])
    assert result.exit_code == 0, result.output
    assert "prometheus" in result.output


def test_missing_proposed_value_cannot_inherit_process_env(tmp_path, monkeypatch):
    monkeypatch.setenv("PROMETHEUS_URL", "https://ambient.example")
    monkeypatch.setenv("PROMETHEUS_ENABLED", "true")
    with pytest.raises(ValueError):
        update_env("prometheus", {"enabled": True}, tmp_path / "env")


def test_verify_failure_preserves_original(tmp_path):
    path = tmp_path / "env"
    path.write_text("# preserved\n")
    with pytest.raises(ValueError):
        update_env(
            "prometheus",
            {"enabled": True, "url": "https://example.com"},
            path,
            verify=lambda _: False,
        )
    assert path.read_text() == "# preserved\n"


def test_cli_refuses_literal_secret(tmp_path):
    config = tmp_path / "input.json"
    config.write_text('{"enabled":true,"url":"https://example.com","token":"do-not-print"}')
    result = CliRunner().invoke(
        app,
        [
            "integrations",
            "setup",
            "prometheus",
            "--env-file",
            str(tmp_path / "env"),
            "--input-file",
            str(config),
        ],
    )
    assert result.exit_code == 2
    assert "do-not-print" not in result.output
    assert not (tmp_path / "env").exists()


def test_cli_env_reference_secret(tmp_path, monkeypatch):
    monkeypatch.setenv("SETUP_TOKEN", "do-not-print")
    config = tmp_path / "input.json"
    config.write_text('{"enabled":true,"url":"https://example.com","token":{"env":"SETUP_TOKEN"}}')
    result = CliRunner().invoke(
        app,
        [
            "integrations",
            "setup",
            "prometheus",
            "--env-file",
            str(tmp_path / "env"),
            "--input-file",
            str(config),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "do-not-print" not in result.output


def test_wave2_setup_fields(tmp_path):
    from fin_graph.integrations.setup import settings_models

    assert "gitlab" in settings_models()
    update_env(
        "gitlab",
        {
            "enabled": True,
            "url": "https://gitlab.example",
            "token": "secret",
            "project": "group/project",
        },
        tmp_path / "env",
    )
    assert "GITLAB_PROJECT" in (tmp_path / "env").read_text()


def test_verification_cannot_overwrite_external_edit(tmp_path):
    path = tmp_path / "env"
    path.write_text("OTHER=original\n")

    def verify(_):
        path.write_text("OTHER=external\n")
        return True

    with pytest.raises(ValueError):
        update_env(
            "prometheus", {"enabled": True, "url": "https://example.com"}, path, verify=verify
        )
    assert path.read_text() == "OTHER=external\n"


def test_multiline_secret_roundtrip(tmp_path):
    from dotenv import dotenv_values

    path = tmp_path / "env"
    value = "first\nsecond'last\\suffix"
    update_env("prometheus", {"enabled": True, "url": "https://example.com", "token": value}, path)
    assert dotenv_values(path)["PROMETHEUS_TOKEN"] == value


def test_cli_setup_verify_failure_leaves_old_file(tmp_path, monkeypatch):
    import fin_graph.cli.integrations as commands

    async def failed_verify(*_):
        return False

    monkeypatch.setattr(commands, "verify_proposed", failed_verify)
    path = tmp_path / "env"
    path.write_text("# unchanged\n")
    config = tmp_path / "input.json"
    config.write_text('{"enabled":true,"url":"https://example.com"}')
    result = CliRunner().invoke(
        app,
        [
            "integrations",
            "setup",
            "prometheus",
            "--env-file",
            str(path),
            "--input-file",
            str(config),
            "--verify",
        ],
    )
    assert result.exit_code == 2
    assert path.read_text() == "# unchanged\n"


def test_replacing_multiline_binding_does_not_activate_continuations(tmp_path):
    from dotenv import dotenv_values

    path = tmp_path / "env"
    path.write_text(
        '# keep\nPROMETHEUS_ENABLED=true\nPROMETHEUS_URL=https://example.com\nPROMETHEUS_TOKEN="old\nOTHER=must-not-activate"\nUNRELATED=preserve\n'
    )
    update_env("prometheus", {"token": "new"}, path)
    values = dotenv_values(path)
    assert values["PROMETHEUS_TOKEN"] == "new"
    assert "OTHER" not in values
    assert values["UNRELATED"] == "preserve"
    assert "# keep" in path.read_text()


def test_market_data_providers_can_be_set_up(tmp_path):
    env = tmp_path / "env"

    update_env("yahoo", {"enabled": False, "report_lag_days": 60}, env)
    update_env("financial_datasets", {"enabled": True, "api_key": "fd-key"}, env)

    text = env.read_text(encoding="utf-8")
    assert "YAHOO_ENABLED=false" in text and "YAHOO_REPORT_LAG_DAYS=60" in text
    assert 'FINANCIAL_DATASETS_API_KEY="fd-key"' in text
    with pytest.raises(ValueError):
        update_env("financial_datasets", {"enabled": True, "api_key": ""}, tmp_path / "other")
