"""Import target for Uvicorn and Docker."""

from fin_graph.server.app import create_app

app = create_app()
