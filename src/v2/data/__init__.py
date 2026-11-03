"""Point-in-time market-data compatibility exports."""

from fin_graph.domain.market import CompanyNews, FinancialMetrics, InsiderTrade, Price
from fin_graph.integrations.financial_datasets.client import FinancialDatasetsClient as FDClient
from fin_graph.integrations.yahoo.client import YahooClient
from fin_graph.quant.cache import CachedMarketData as CachedDataClient
from fin_graph.quant.data import MarketDataClient as DataClient
from fin_graph.quant.signals.pead import EarningsData, EarningsRecord

FDClientError = RuntimeError
YahooClientError = RuntimeError

__all__ = [
    "CachedDataClient",
    "CompanyNews",
    "DataClient",
    "EarningsData",
    "EarningsRecord",
    "FDClient",
    "FDClientError",
    "FinancialMetrics",
    "InsiderTrade",
    "Price",
    "YahooClient",
    "YahooClientError",
]
