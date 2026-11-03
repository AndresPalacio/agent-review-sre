"""ASGI import target retained for existing Uvicorn and Docker commands."""

from fin_graph.server.asgi import app

__all__ = ["app"]
