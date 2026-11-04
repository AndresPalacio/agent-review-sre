from fin_graph.agents.common.middleware.deepagents import restricted_deepagents_middleware


def test_restricted_middleware_replaces_additive_deepagents_tool_slots() -> None:
    middleware = restricted_deepagents_middleware()

    assert [item.name for item in middleware] == [
        "FilesystemMiddleware",
        "SubAgentMiddleware",
    ]
    assert all(item.tools == () for item in middleware)
