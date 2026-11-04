from __future__ import annotations

import pytest

from fin_graph.integrations.base import IntegrationNotConfiguredError
from fin_graph.integrations.context import RunContext


class MarketDataClient:
    pass


class CloseableClient:
    def __init__(self) -> None:
        self.closes = 0

    async def aclose(self) -> None:
        self.closes += 1


def test_a_registered_client_is_returned_with_its_type() -> None:
    market = MarketDataClient()

    assert RunContext(clients={"market": market}).client("market", MarketDataClient) is market


def test_a_missing_client_explains_it_is_not_configured() -> None:
    with pytest.raises(
        IntegrationNotConfiguredError, match=r"market is unavailable.*not configured"
    ):
        RunContext().client("market", MarketDataClient)


def test_a_client_that_failed_to_build_explains_why() -> None:
    context = RunContext(unavailable={"market": "ConfigException: no market API key"})

    with pytest.raises(IntegrationNotConfiguredError, match="no market API key"):
        context.client("market", MarketDataClient)


def test_a_client_of_the_wrong_type_is_a_programming_error() -> None:
    with pytest.raises(TypeError, match="expected MarketDataClient"):
        RunContext(clients={"market": object()}).client("market", MarketDataClient)


async def test_context_closes_each_owned_client_once_and_leaves_borrowed_clients_open() -> None:
    owned = CloseableClient()
    borrowed = CloseableClient()
    context = RunContext(
        clients={"first": owned, "alias": owned, "borrowed": borrowed},
        owned_clients=(owned, owned),
    )

    await context.aclose()
    await context.aclose()

    assert owned.closes == 1
    assert borrowed.closes == 0
