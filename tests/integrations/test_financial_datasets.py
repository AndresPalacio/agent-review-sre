from __future__ import annotations

import json
from datetime import date

import httpx
import pytest
from pydantic import ValidationError
from tests.domain.quant.synthetic import DATA

from fin_graph.integrations.base import HttpStatusError, IntegrationError
from fin_graph.integrations.financial_datasets.client import FinancialDatasetsClient
from fin_graph.integrations.financial_datasets.integration import FinancialDatasetsIntegration
from fin_graph.integrations.financial_datasets.settings import FinancialDatasetsSettings
from fin_graph.safety.secrets import redact_secrets

AS_OF = date(2024, 3, 8)


def _api(kind: str) -> dict[str, object]:
    return json.loads((DATA / "api" / kind / "AAPL.json").read_text(encoding="utf-8"))


def _client(seen: list[httpx.Request], respond: object = None) -> FinancialDatasetsClient:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if callable(respond):
            return respond(request)  # type: ignore[no-any-return]
        routes = {
            "/prices/": "prices",
            "/financial-metrics/": "financial_metrics",
            "/insider-trades/": "insider_trades",
            "/news/": "news",
        }
        if request.url.path == "/financials/search/line-items":
            body = json.loads(request.content)
            return httpx.Response(
                200,
                json={
                    "search_results": [
                        {
                            "ticker": "AAPL",
                            "report_period": "2023-09-30",
                            "period": body["period"],
                            "currency": "USD",
                            "revenue": 383e9,
                        }
                    ]
                },
            )
        return httpx.Response(200, json=_api(routes[request.url.path]))

    return FinancialDatasetsClient(
        "fd-secret-key", transport=httpx.MockTransport(handler), backoff=(0,)
    )


async def test_calls_send_the_key_and_point_in_time_filters() -> None:
    seen: list[httpx.Request] = []
    client = _client(seen)

    metrics = await client.financial_metrics("AAPL", AS_OF, limit=3)
    bars = await client.prices("AAPL", date(2024, 3, 1), AS_OF)
    items = await client.line_items("AAPL", ["revenue"], AS_OF, period="ttm")

    assert seen[0].headers["X-API-KEY"] == "fd-secret-key"
    assert seen[0].url.params["report_period_lte"] == "2024-03-08"
    assert len(metrics) == 3 and metrics[0].report_period <= AS_OF
    assert bars and all(bar.day <= AS_OF for bar in bars)
    assert json.loads(seen[2].content)["end_date"] == "2024-03-08"
    assert items[0].value("revenue") == 383e9


async def test_repeated_calls_are_cached_for_the_run() -> None:
    seen: list[httpx.Request] = []
    client = _client(seen)

    await client.financial_metrics("AAPL", AS_OF)
    await client.market_cap("AAPL", AS_OF)

    assert len(seen) == 1


async def test_rate_limit_backs_off_and_a_bad_key_is_readable() -> None:
    answers = iter([httpx.Response(429), httpx.Response(200, json=_api("news"))])
    seen: list[httpx.Request] = []
    news = await _client(seen, lambda request: next(answers)).company_news("AAPL", AS_OF)
    assert news and len(seen) == 2

    with pytest.raises(IntegrationError, match="rejected the API key") as error:
        await _client([], lambda request: httpx.Response(401)).insider_trades("AAPL", AS_OF)
    assert "fd-secret-key" not in str(error.value)
    with pytest.raises(HttpStatusError):
        await _client([], lambda request: httpx.Response(429)).prices("AAPL", AS_OF, AS_OF)


async def test_malformed_payloads_raise_a_readable_error() -> None:
    bad = lambda request: httpx.Response(200, json={"financial_metrics": [{"ticker": "AAPL"}]})  # noqa: E731

    with pytest.raises(IntegrationError, match="malformed financial_metrics"):
        await _client([], bad).financial_metrics("AAPL", AS_OF)


def test_settings_need_a_key_once_enabled_and_never_print_it() -> None:
    with pytest.raises(ValidationError):
        FinancialDatasetsSettings(_env_file=None, enabled=True)
    settings = FinancialDatasetsSettings(_env_file=None, enabled=True, api_key="fd-secret-key")

    assert "fd-secret-key" not in repr(settings)
    assert FinancialDatasetsIntegration().load_settings(None) is None
    assert "fd-secret-key" not in redact_secrets("X-API-KEY: fd-secret-key")
