from __future__ import annotations

import httpx
import pytest

from sre_graph.integrations.base import IntegrationError
from sre_graph.integrations.http import HttpJsonClient, prometheus_data, require_window


async def test_json_request_encodes_params_and_headers() -> None:
    seen: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"ok": True})

    client = HttpJsonClient(
        "https://metrics.example/prefix",
        headers={"Authorization": "Bearer secret"},
        transport=httpx.MockTransport(respond),
    )

    assert await client.request("GET", "/api/v1/query", params={"query": 'up{job="api"}'}) == {
        "ok": True
    }
    assert seen[0].url.path == "/prefix/api/v1/query"
    assert seen[0].url.params["query"] == 'up{job="api"}'
    assert seen[0].headers["Authorization"] == "Bearer secret"


async def test_mutation_uses_explicit_method_and_still_rejects_redirects() -> None:
    seen: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"ok": True})

    client = HttpJsonClient("https://vendor.example", transport=httpx.MockTransport(respond))

    with pytest.raises(ValueError, match="only read"):
        await client.request("PUT", "/api/item", json_body={"status": "resolved"})
    assert await client.write_request("PUT", "/api/item", json_body={"status": "resolved"}) == {
        "ok": True
    }
    assert seen[0].method == "PUT"
    assert seen[0].url.path == "/api/item"


async def test_request_with_headers_returns_only_pagination_link() -> None:
    client = HttpJsonClient(
        "https://vendor.example",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json=[{"id": 1}],
                headers={"Link": '</next?cursor=abc>; rel="next"', "Set-Cookie": "private=value"},
            )
        ),
    )

    payload, headers = await client.request_with_headers("GET", "/items")

    assert payload == [{"id": 1}]
    assert headers == {"link": '</next?cursor=abc>; rel="next"'}


@pytest.mark.parametrize("status", [301, 401, 429, 503])
async def test_http_errors_do_not_echo_credentials(status: int) -> None:
    token = "secret-app-key"
    client = HttpJsonClient(
        "https://vendor.example",
        headers={"Authorization": token},
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                status, text=f"denied {token}", headers={"Location": "https://evil.example"}
            )
        ),
    )

    with pytest.raises(IntegrationError) as caught:
        await client.request("GET", "/read")
    assert str(status) in str(caught.value)
    assert token not in str(caught.value)
    assert "evil.example" not in str(caught.value)


async def test_http_redirect_is_not_followed() -> None:
    calls = 0

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(302, headers={"Location": "https://evil.example/secret"})

    client = HttpJsonClient("https://vendor.example", transport=httpx.MockTransport(respond))
    with pytest.raises(IntegrationError, match="302"):
        await client.request("GET", "/read")
    assert calls == 1


async def test_oversized_response_is_rejected() -> None:
    client = HttpJsonClient(
        "https://vendor.example",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=b"x" * (2 * 1024 * 1024 + 1))
        ),
    )
    with pytest.raises(IntegrationError, match="too large"):
        await client.request("GET", "/read")


async def test_invalid_json_is_an_integration_error() -> None:
    client = HttpJsonClient(
        "https://vendor.example",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text="not json")),
    )
    with pytest.raises(IntegrationError, match="invalid JSON"):
        await client.request("GET", "/read")


async def test_non_ascii_secret_header_is_a_safe_integration_error() -> None:
    secret = "contraseña-secreta"
    client = HttpJsonClient(
        "https://vendor.example",
        headers={"Authorization": f"Bearer {secret}"},
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={})),
    )

    with pytest.raises(IntegrationError) as caught:
        await client.request("GET", "/read")
    assert secret not in str(caught.value)


def test_prometheus_rejects_error_envelope() -> None:
    with pytest.raises(IntegrationError, match="Prometheus"):
        prometheus_data({"status": "error", "error": "secret"}, "Prometheus")


@pytest.mark.parametrize("start,end", [(10, 10), (20, 10), (1, 604803), (-1, 10)])
def test_query_range_rejects_invalid_window(start: int, end: int) -> None:
    with pytest.raises(IntegrationError, match="window"):
        require_window(start, end)


@pytest.mark.parametrize(
    "base_url", ["", "example.com", "ftp://example.com", "https://user:secret@example.com"]
)
def test_bad_base_url_is_rejected(base_url: str) -> None:
    with pytest.raises(ValueError, match="base_url"):
        HttpJsonClient(base_url)
