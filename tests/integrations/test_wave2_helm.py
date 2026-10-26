from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from fin_graph.integrations.base import IntegrationError
from fin_graph.integrations.wave2.helm import HelmClient
from fin_graph.integrations.wave2.settings import HelmSettings


class Reader:
    def __init__(self, value: bytes) -> None:
        self.value = value

    async def read(self, size: int = -1) -> bytes:
        value, self.value = self.value[:size], self.value[size:]
        return value


class Process:
    def __init__(self, stdout: str, code: int = 0) -> None:
        self.stdout = Reader(stdout.encode())
        self.stderr = Reader(b"private-error")
        self.returncode: int | None = None
        self.code = code
        self.killed = False

    async def wait(self) -> int:
        self.returncode = self.code
        return self.code

    def kill(self) -> None:
        self.killed = True


@pytest.mark.parametrize(
    ("operation", "args", "expected", "output"),
    [
        (
            "releases",
            {"limit": 1},
            ("list", "--all", "--max", "1", "--output", "json"),
            '[{"name":"app"}]',
        ),
        ("status", {"identifier": "app"}, ("status", "app", "--output", "json"), '{"name":"app"}'),
        (
            "history",
            {"identifier": "app", "limit": 2},
            ("history", "app", "--max", "2", "--output", "json"),
            '[{"revision":1}]',
        ),
        (
            "values",
            {"identifier": "app", "revision": 2},
            ("get", "values", "app", "--revision", "2", "--output", "json"),
            '{"password":"do-not-export","replicas":2}',
        ),
        (
            "manifest",
            {"identifier": "app"},
            ("get", "manifest", "app"),
            "apiVersion: v1\nkind: Secret\ndata:\n  arbitrary: ZG8tbm90LWV4cG9ydA==\n"
            "---\nkind: ConfigMap\ndata:\n  normal: ok\n",
        ),
    ],
)
async def test_fixed_helm_commands_and_secret_sanitation(
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
    args: dict[str, Any],
    expected: tuple[str, ...],
    output: str,
) -> None:
    seen: list[tuple[str, ...]] = []

    async def spawn(*argv: str, **kwargs: Any) -> Process:
        seen.append(argv)
        assert "shell" not in kwargs
        assert kwargs["stdout"] == asyncio.subprocess.PIPE
        return Process(output)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    client = HelmClient(HelmSettings(_env_file=None, enabled=True, namespace="prod"))
    result = await client.call(operation, **args)
    assert seen == [("helm", *expected, "--namespace", "prod")]
    assert result["items"]
    assert "do-not-export" not in json.dumps(result)
    assert "ZG8tbm90LWV4cG9ydA==" not in json.dumps(result)


@pytest.mark.parametrize("bad", ["--help", "../app", "app; whoami", "$(whoami)", "app/name"])
async def test_invalid_release_rejected_before_spawn(
    monkeypatch: pytest.MonkeyPatch, bad: str
) -> None:
    async def spawn(*args: Any, **kwargs: Any) -> None:
        pytest.fail("unsafe process spawned")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    client = HelmClient(HelmSettings(_env_file=None, enabled=True))
    with pytest.raises(IntegrationError):
        await client.call("status", identifier=bad)


async def test_helm_oversized_output_kills_process(monkeypatch: pytest.MonkeyPatch) -> None:
    process = Process("x" * (2 * 1024 * 1024 + 1))

    async def spawn(*args: Any, **kwargs: Any) -> Process:
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    with pytest.raises(IntegrationError):
        await HelmClient(HelmSettings(_env_file=None)).call("releases")
    assert process.killed


async def test_helm_no_user_values_is_a_valid_empty_read(monkeypatch: pytest.MonkeyPatch) -> None:
    async def spawn(*args: Any, **kwargs: Any) -> Process:
        return Process("null")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    result = await HelmClient(HelmSettings(_env_file=None)).call("values", identifier="app")
    assert result["items"] == [{}]


async def test_helm_yaml_aliases_are_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    async def spawn(*args: Any, **kwargs: Any) -> Process:
        return Process("kind: ConfigMap\ndata:\n  first: &shared [x, x]\n  second: *shared\n")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    with pytest.raises(IntegrationError, match="alias"):
        await HelmClient(HelmSettings(_env_file=None)).call("manifest", identifier="app")
