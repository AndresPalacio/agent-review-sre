"""Trust boundary for seamless loopback legacy routes and authenticated remote use."""

from __future__ import annotations

import inspect
import ipaddress
from collections.abc import Awaitable, Callable

from fastapi import Header, HTTPException, Request

from fin_graph.adapters.reference_app.settings import ReferenceAppProfile

type KeyRoleResolver = Callable[[str], Awaitable[str | None] | str | None]


def _loopback(host: str) -> bool:
    if host.strip().casefold() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host.strip().strip("[]")).is_loopback
    except ValueError:
        return False


async def reference_app_guard(
    request: Request,
    x_api_key: str | None = Header(default=None),
) -> None:
    profile = getattr(request.app.state, "reference_app_profile", ReferenceAppProfile())
    if not isinstance(profile, ReferenceAppProfile) or not profile.enabled:
        raise HTTPException(404, "not found")
    peer = request.client.host if request.client is not None else ""
    client = profile.resolve_client_host(peer, request.headers.get("x-forwarded-for"))
    if _loopback(client) or profile.allow_insecure_remote:
        return
    if not x_api_key:
        raise HTTPException(401, "API key required")
    resolver: KeyRoleResolver | None = getattr(request.app.state, "reference_app_key_role", None)
    if resolver is None:
        manager = getattr(request.app.state, "manager", None)
        storage = getattr(manager, "storage", None)
        resolver = getattr(storage, "key_role", None)
    if resolver is None:
        raise HTTPException(401, "invalid API key")
    role = resolver(x_api_key)
    if inspect.isawaitable(role):
        role = await role
    if role not in {"viewer", "operator", "approver", "admin"}:
        raise HTTPException(401, "invalid API key")
