"""Legacy Yahoo client alias."""

from fin_graph.integrations.yahoo.client import YahooClient

YahooClientError = RuntimeError

__all__ = ["YahooClient", "YahooClientError"]
