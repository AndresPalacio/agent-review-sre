"""Environment aliases and the local reference-application trust profile."""

from __future__ import annotations

import ipaddress
from collections.abc import Mapping, Sequence

from pydantic import BaseModel, ConfigDict, field_validator

from fin_graph.config.aliases import resolve_alias


def _environment_bool(
    env: Mapping[str, str],
    modern: str,
    legacy: Sequence[str],
    default: bool,
) -> bool:
    value = resolve_alias(env, modern, legacy)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{modern} must be a boolean")


def _ip(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    candidate = value.strip()
    if candidate.startswith("[") and candidate.endswith("]"):
        candidate = candidate[1:-1]
    return ipaddress.ip_address(candidate)


def _is_loopback(host: str) -> bool:
    if host.strip().lower() == "localhost":
        return True
    try:
        return _ip(host).is_loopback
    except ValueError:
        return False


class ReferenceAppProfile(BaseModel):
    """Security boundary for the unauthenticated local reference API."""

    model_config = ConfigDict(frozen=True)

    local: bool = True
    enabled: bool = True
    allow_insecure_remote: bool = False
    trusted_proxies: tuple[str, ...] = ()

    @field_validator("trusted_proxies")
    @classmethod
    def validate_trusted_proxies(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        for value in values:
            ipaddress.ip_network(value, strict=False)
        return values

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> ReferenceAppProfile:
        proxies = resolve_alias(
            env,
            "FIN_GRAPH_TRUSTED_PROXIES",
            ("TRUSTED_PROXIES",),
        )
        return cls(
            local=_environment_bool(env, "FIN_GRAPH_LOCAL", ("LOCAL",), True),
            enabled=_environment_bool(
                env,
                "FIN_GRAPH_LEGACY_API",
                ("LEGACY_API",),
                True,
            ),
            allow_insecure_remote=_environment_bool(
                env,
                "FIN_GRAPH_ALLOW_INSECURE_LEGACY_API",
                ("ALLOW_INSECURE_LEGACY_API",),
                False,
            ),
            trusted_proxies=tuple(
                part.strip() for part in (proxies or "").split(",") if part.strip()
            ),
        )

    @property
    def default_bind_host(self) -> str:
        return "127.0.0.1" if self.local else "0.0.0.0"

    def validate_bind(self, host: str) -> None:
        if self.enabled and not self.allow_insecure_remote and not _is_loopback(host):
            raise ValueError(
                "reference API may bind only to loopback; set "
                "ALLOW_INSECURE_LEGACY_API=true to opt in explicitly"
            )

    def resolve_client_host(self, peer_host: str, forwarded_for: str | None) -> str:
        if not forwarded_for or not self._is_trusted_proxy(peer_host):
            return peer_host
        forwarded_host = forwarded_for.split(",", maxsplit=1)[0].strip()
        try:
            _ip(forwarded_host)
        except ValueError:
            return peer_host
        return forwarded_host

    def _is_trusted_proxy(self, peer_host: str) -> bool:
        try:
            peer = _ip(peer_host)
        except ValueError:
            return False
        return any(
            peer in ipaddress.ip_network(network, strict=False) for network in self.trusted_proxies
        )
