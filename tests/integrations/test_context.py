from __future__ import annotations

import pytest

from sre_graph.integrations.base import IntegrationNotConfiguredError
from sre_graph.integrations.context import RunContext


class PagerClient:
    pass


def test_a_registered_client_is_returned_with_its_type() -> None:
    pager = PagerClient()

    assert RunContext(clients={"pager": pager}).client("pager", PagerClient) is pager


def test_a_missing_client_explains_it_is_not_configured() -> None:
    with pytest.raises(
        IntegrationNotConfiguredError, match=r"pager is unavailable.*not configured"
    ):
        RunContext().client("pager", PagerClient)


def test_a_client_that_failed_to_build_explains_why() -> None:
    context = RunContext(unavailable={"pager": "ConfigException: no kubeconfig"})

    with pytest.raises(IntegrationNotConfiguredError, match="no kubeconfig"):
        context.client("pager", PagerClient)


def test_a_client_of_the_wrong_type_is_a_programming_error() -> None:
    with pytest.raises(TypeError, match="expected PagerClient"):
        RunContext(clients={"pager": object()}).client("pager", PagerClient)
