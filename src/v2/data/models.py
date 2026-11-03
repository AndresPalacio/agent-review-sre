"""Legacy data-model aliases."""

from v2.data import CompanyNews, EarningsData, EarningsRecord, FinancialMetrics, InsiderTrade, Price

Earnings = EarningsData

__all__ = [
    "CompanyNews",
    "Earnings",
    "EarningsData",
    "EarningsRecord",
    "FinancialMetrics",
    "InsiderTrade",
    "Price",
]
