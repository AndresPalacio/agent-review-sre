"""Legacy Financial Datasets client alias."""

from fin_graph.integrations.financial_datasets.client import FinancialDatasetsClient as FDClient

FDClientError = RuntimeError

__all__ = ["FDClient", "FDClientError"]
