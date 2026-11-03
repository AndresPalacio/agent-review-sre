"""Encrypted provider-key CRUD with reference response shapes and no plaintext exposure."""

from __future__ import annotations

import re
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request, Response

from fin_graph.compat.ai_hedge_fund.schemas import (
    ApiKeyBulkUpdateRequest,
    ApiKeyCreateRequest,
    ApiKeyResponse,
    ApiKeySummaryResponse,
    ApiKeyUpdateRequest,
)
from fin_graph.compat.storage import CompatibilityStore, ProviderKeyRecord

router = APIRouter(prefix="/api-keys")
_PROVIDER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_. -]{0,99}$")


def _store(request: Request) -> CompatibilityStore:
    store = getattr(request.app.state, "compatibility_store", None)
    if not isinstance(store, CompatibilityStore):
        raise HTTPException(503, "compatibility store unavailable")
    return store


def _provider(value: str) -> str:
    if not _PROVIDER.fullmatch(value) or ".." in value:
        raise HTTPException(400, "Invalid provider")
    return value.strip()


def _public_id(record: ProviderKeyRecord) -> int:
    try:
        return int(record.id[:15], 16)
    except ValueError:
        return int.from_bytes(record.id.encode("utf-8")[:7], "big")


def _display_provider(record: ProviderKeyRecord) -> str:
    return record.provider.upper() if record.provider.endswith("_api_key") else record.provider


def _response(record: ProviderKeyRecord) -> ApiKeyResponse:
    return ApiKeyResponse(
        id=_public_id(record),
        provider=_display_provider(record),
        key_value=record.key_hint,
        is_active=record.is_active,
        description=record.description,
        created_at=record.created_at,
        updated_at=record.updated_at,
        last_used=record.last_used,
    )


def _summary(record: ProviderKeyRecord) -> ApiKeySummaryResponse:
    return ApiKeySummaryResponse(
        id=_public_id(record),
        provider=_display_provider(record),
        is_active=record.is_active,
        description=record.description,
        created_at=record.created_at,
        updated_at=record.updated_at,
        last_used=record.last_used,
        has_key=True,
    )


async def _record(store: CompatibilityStore, provider: str) -> ProviderKeyRecord:
    normalized = provider.casefold()
    try:
        return next(
            item for item in await store.list_provider_keys() if item.provider == normalized
        )
    except StopIteration as exc:
        raise HTTPException(404, "API key not found") from exc


@router.post("/", response_model=ApiKeyResponse)
async def put_key(body: ApiKeyCreateRequest, request: Request) -> ApiKeyResponse:
    provider = _provider(body.provider)
    record = await _store(request).put_provider_key(
        provider,
        body.key_value,
        description=body.description,
        is_active=body.is_active,
    )
    return _response(record)


@router.get("/", response_model=list[ApiKeySummaryResponse])
async def list_keys(
    request: Request, include_inactive: bool = False
) -> list[ApiKeySummaryResponse]:
    records = await _store(request).list_provider_keys()
    return [_summary(item) for item in records if include_inactive or item.is_active]


@router.post("/bulk", response_model=list[ApiKeyResponse])
async def bulk_keys(body: ApiKeyBulkUpdateRequest, request: Request) -> list[ApiKeyResponse]:
    store = _store(request)
    values: list[ApiKeyResponse] = []
    for item in body.api_keys:
        provider = _provider(item.provider)
        record = await store.put_provider_key(
            provider,
            item.key_value,
            description=item.description,
            is_active=item.is_active,
        )
        values.append(_response(record))
    return values


@router.get("/{provider}", response_model=ApiKeyResponse)
async def get_key(provider: str, request: Request) -> ApiKeyResponse:
    provider = _provider(provider)
    return _response(await _record(_store(request), provider))


@router.put("/{provider}", response_model=ApiKeyResponse)
async def update_key(
    provider: str,
    body: ApiKeyUpdateRequest,
    request: Request,
) -> ApiKeyResponse:
    provider = _provider(provider)
    store = _store(request)
    current = await _record(store, provider)
    fields = body.model_fields_set
    updated = await store.update_provider_key(
        provider,
        expected_version=current.version,
        secret=body.key_value if "key_value" in fields else None,
        description=body.description if "description" in fields else current.description,
        is_active=body.is_active
        if "is_active" in fields and body.is_active is not None
        else current.is_active,
        last_used=current.last_used,
    )
    if updated is None:
        raise HTTPException(409, "API key changed concurrently")
    return _response(updated)


@router.patch("/{provider}/deactivate", response_model=ApiKeySummaryResponse)
async def deactivate_key(provider: str, request: Request) -> ApiKeySummaryResponse:
    provider = _provider(provider)
    store = _store(request)
    current = await _record(store, provider)
    updated = await store.update_provider_key(
        provider,
        expected_version=current.version,
        secret=None,
        description=current.description,
        is_active=False,
        last_used=current.last_used,
    )
    if updated is None:
        raise HTTPException(409, "API key changed concurrently")
    return _summary(updated)


@router.patch("/{provider}/last-used")
async def touch_key(provider: str, request: Request) -> dict[str, str]:
    provider = _provider(provider)
    store = _store(request)
    current = await _record(store, provider)
    updated = await store.update_provider_key(
        provider,
        expected_version=current.version,
        secret=None,
        description=current.description,
        is_active=current.is_active,
        last_used=datetime.now(UTC),
    )
    if updated is None:
        raise HTTPException(409, "API key changed concurrently")
    return {"message": "Last used timestamp updated"}


@router.delete("/{provider}", status_code=204)
async def delete_key(provider: str, request: Request) -> Response:
    provider = _provider(provider)
    store = _store(request)
    current = await _record(store, provider)
    if not await store.delete_provider_key(provider, expected_version=current.version):
        raise HTTPException(409, "API key changed concurrently")
    return Response(status_code=204)
