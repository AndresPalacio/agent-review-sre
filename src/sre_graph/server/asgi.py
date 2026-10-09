"""Import target for Uvicorn and Docker."""

from sre_graph.server.app import create_app

app = create_app()
