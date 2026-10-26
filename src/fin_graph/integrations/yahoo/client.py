"""Yahoo Finance as a point-in-time :class:`MarketDataClient`.

Ported from ai-hedge-fund ``src/data/providers/yahoo_provider.py`` (MIT) to async httpx:

- chart v8 for daily prices (no crumb);
- quoteSummary v10 for the TTM ratios and insider transactions (cookie and crumb);
- fundamentals-timeseries for annual statements, which fills what quoteSummary no longer returns;
- the RSS headline feed for news.

Two point-in-time rules the original lacked. The TTM snapshot describes today, so it is used only
when ``as_of`` is recent. An annual statement counts as known ``report_lag_days`` after its fiscal
year ends. Yahoo keeps only about five annual periods, about a year of insider filings and the
current news feed, so older ranges come back short or empty: a data gap, never an error.
"""

from __future__ import annotations

import time
import xml.etree.ElementTree as ElementTree
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any

from fin_graph.domain.market import CompanyNews, FinancialMetrics, InsiderTrade, LineItem, Price
from fin_graph.integrations.base import IntegrationError
from fin_graph.integrations.market_data import MarketDataClient, RunMemo
from fin_graph.integrations.yahoo.session import YahooSession

CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
SUMMARY_URL = "https://query1.finance.yahoo.com/v10/finance/quoteSummary/{ticker}"
TIMESERIES_URL = (
    "https://query1.finance.yahoo.com/ws/fundamentals-timeseries/v1/finance/timeseries/{ticker}"
)
RSS_URL = "https://feeds.finance.yahoo.com/rss/2.0/headline"
SUMMARY_MODULES = ",".join(
    [
        "price",
        "summaryDetail",
        "defaultKeyStatistics",
        "financialData",
        "incomeStatementHistory",
        "balanceSheetHistory",
        "cashflowStatementHistory",
        "insiderTransactions",
    ]
)
#: Canonical fact name → annual fundamentals-timeseries type.
TIMESERIES_TYPES = {
    "revenue": "annualTotalRevenue",
    "net_income": "annualNetIncome",
    "gross_profit": "annualGrossProfit",
    "operating_income": "annualOperatingIncome",
    "interest_expense": "annualInterestExpense",
    "ebit": "annualEBIT",
    "ebitda": "annualEBITDA",
    "depreciation_and_amortization": "annualDepreciationAndAmortization",
    "operating_cash_flow": "annualOperatingCashFlow",
    "capital_expenditure": "annualCapitalExpenditure",
    "free_cash_flow": "annualFreeCashFlow",
    "dividends_paid": "annualCashDividendsPaid",
    "repurchase_of_stock": "annualRepurchaseOfCapitalStock",
    "issuance_of_stock": "annualCommonStockIssuance",
    "total_assets": "annualTotalAssets",
    "total_liabilities": "annualTotalLiabilitiesNetMinorityInterest",
    "current_assets": "annualCurrentAssets",
    "current_liabilities": "annualCurrentLiabilities",
    "working_capital": "annualWorkingCapital",
    "shareholders_equity": "annualStockholdersEquity",
    "total_debt": "annualTotalDebt",
    "cash_and_equivalents": "annualCashAndCashEquivalents",
    "outstanding_shares": "annualOrdinarySharesNumber",
}
_TYPE_TO_NAME = {value: key for key, value in TIMESERIES_TYPES.items()}

type Facts = dict[str, float | None]


def _raw(node: Any, *keys: str) -> float | None:
    """Walk nested dicts and unwrap Yahoo's ``{"raw": x, "fmt": "..."}`` numbers."""
    current = node
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    if isinstance(current, dict):
        current = current.get("raw")
    if isinstance(current, bool) or not isinstance(current, int | float):
        return None
    return float(current)


def _fmt_date(node: Any, *keys: str) -> date | None:
    current = node
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    text = current.get("fmt") if isinstance(current, dict) else None
    try:
        return date.fromisoformat(text) if isinstance(text, str) else None
    except ValueError:
        return None


def _div(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None or denominator == 0:
        return None
    return numerator / denominator


def _sum(*values: float | None) -> float | None:
    present = [value for value in values if value is not None]
    return sum(present) if present else None


def _first(*values: float | None) -> float | None:
    return next((value for value in values if value is not None), None)


class YahooClient(MarketDataClient):
    """One run's Yahoo access; quoteSummary and timeseries are fetched once per ticker."""

    provider = "yahoo"

    def __init__(
        self,
        session: YahooSession | None = None,
        *,
        report_lag_days: int = 45,
        snapshot_max_age_days: int = 7,
        today: Callable[[], date] | None = None,
    ) -> None:
        self.session = session or YahooSession()
        self.report_lag = timedelta(days=report_lag_days)
        self.snapshot_max_age = timedelta(days=snapshot_max_age_days)
        self._today = today or (lambda: datetime.now(UTC).date())
        self._memo = RunMemo()

    # -- raw endpoints, memoized per run ---------------------------------------------------

    async def prices(self, ticker: str, start: date, end: date) -> list[Price]:
        return await self._memo.get(
            ("prices", ticker, start, end), lambda: self._prices(ticker, start, end)
        )

    async def _prices(self, ticker: str, start: date, end: date) -> list[Price]:
        if end < start:
            return []
        period1 = int(datetime(start.year, start.month, start.day, tzinfo=UTC).timestamp())
        stop = end + timedelta(days=1)
        period2 = int(datetime(stop.year, stop.month, stop.day, tzinfo=UTC).timestamp())
        payload = await self.session.get_json(
            CHART_URL.format(ticker=ticker),
            {"period1": period1, "period2": period2, "interval": "1d"},
        )
        results = ((payload or {}).get("chart") or {}).get("result") or []
        if not results:
            return []
        chart = results[0]
        stamps = chart.get("timestamp") or []
        quote = ((chart.get("indicators") or {}).get("quote") or [{}])[0]
        columns = [quote.get(name) or [] for name in ("open", "high", "low", "close", "volume")]
        bars = []
        for index, stamp in enumerate(stamps):
            row = [column[index] if index < len(column) else None for column in columns]
            opened, high, low, close, volume = row
            if opened is None or high is None or low is None or close is None:
                continue  # holidays and partial rows
            moment = datetime.fromtimestamp(stamp, UTC)
            if not start <= moment.date() <= end:
                continue
            bars.append(
                Price(
                    time=datetime(moment.year, moment.month, moment.day, tzinfo=UTC),
                    open=float(opened),
                    high=float(high),
                    low=float(low),
                    close=float(close),
                    volume=int(volume or 0),
                )
            )
        return bars

    async def _summary(self, ticker: str) -> dict[str, Any] | None:
        async def load() -> dict[str, Any] | None:
            payload = await self.session.get_json(
                SUMMARY_URL.format(ticker=ticker), {"modules": SUMMARY_MODULES}, needs_crumb=True
            )
            results = ((payload or {}).get("quoteSummary") or {}).get("result") or []
            first = results[0] if results else None
            return first if isinstance(first, dict) else None

        return await self._memo.get(("summary", ticker), load)

    async def _timeseries(self, ticker: str) -> dict[date, Facts]:
        async def load() -> dict[date, Facts]:
            now = int(time.time())
            payload = await self.session.get_json(
                TIMESERIES_URL.format(ticker=ticker),
                {
                    "type": ",".join(TIMESERIES_TYPES.values()),
                    "period1": now - 6 * 365 * 24 * 3600,
                    "period2": now,
                    "merge": "false",
                    "padTimeSeries": "false",
                },
            )
            facts: dict[date, Facts] = {}
            for series in ((payload or {}).get("timeseries") or {}).get("result") or []:
                kind = ((series.get("meta") or {}).get("type") or [None])[0]
                name = _TYPE_TO_NAME.get(kind)
                if not name or not isinstance(series.get(kind), list):
                    continue
                for point in series[kind]:
                    value = _raw(point, "reportedValue")
                    try:
                        day = date.fromisoformat(str((point or {}).get("asOfDate")))
                    except ValueError:
                        continue
                    if value is not None:
                        facts.setdefault(day, {})[name] = value
            return facts

        return await self._memo.get(("timeseries", ticker), load)

    # -- derived views ---------------------------------------------------------------------

    def _snapshot_is_current(self, as_of: date) -> bool:
        return self._today() - as_of <= self.snapshot_max_age

    @staticmethod
    def _statements(summary: dict[str, Any] | None, module: str, key: str) -> dict[date, Any]:
        out = {}
        for statement in ((summary or {}).get(module) or {}).get(key) or []:
            day = _fmt_date(statement, "endDate")
            if day:
                out[day] = statement
        return out

    async def annual_facts(self, ticker: str, as_of: date) -> list[tuple[date, Facts]]:
        """Fiscal years known on ``as_of``, newest first; quoteSummary wins over timeseries."""
        summary = await self._summary(ticker)
        try:
            series_by_day = await self._timeseries(ticker)
        except IntegrationError:
            series_by_day = {}  # quoteSummary alone still gives a few periods
        income = self._statements(summary, "incomeStatementHistory", "incomeStatementHistory")
        balance = self._statements(summary, "balanceSheetHistory", "balanceSheetStatements")
        cash = self._statements(summary, "cashflowStatementHistory", "cashflowStatements")
        current_shares = _raw((summary or {}).get("defaultKeyStatistics"), "sharesOutstanding")
        days = sorted({*income, *balance, *cash, *series_by_day}, reverse=True)
        known = []
        for day in days:
            if day + self.report_lag > as_of:
                continue
            known.append((day, self._facts(income.get(day, {}), balance.get(day, {}),
                                           cash.get(day, {}), series_by_day.get(day, {}),
                                           current_shares)))  # fmt: skip
        return known

    @staticmethod
    def _facts(
        income: Any, balance: Any, cash: Any, series: Facts, shares_now: float | None
    ) -> Facts:
        revenue = _first(_raw(income, "totalRevenue"), series.get("revenue"))
        net_income = _first(_raw(income, "netIncome"), series.get("net_income"))
        gross_profit = _first(_raw(income, "grossProfit"), series.get("gross_profit"))
        operating_income = _first(_raw(income, "operatingIncome"), series.get("operating_income"))
        ebit = _first(_raw(income, "ebit"), series.get("ebit"), operating_income)
        depreciation = _first(
            _raw(cash, "depreciation"), series.get("depreciation_and_amortization")
        )
        operating_cash = _first(
            _raw(cash, "totalCashFromOperatingActivities"), series.get("operating_cash_flow")
        )
        capex = _first(_raw(cash, "capitalExpenditures"), series.get("capital_expenditure"))
        equity = _first(_raw(balance, "totalStockholderEquity"), series.get("shareholders_equity"))
        debt = _first(
            _sum(_raw(balance, "shortLongTermDebt"), _raw(balance, "longTermDebt")),
            series.get("total_debt"),
        )
        current_assets = _first(_raw(balance, "totalCurrentAssets"), series.get("current_assets"))
        current_liabilities = _first(
            _raw(balance, "totalCurrentLiabilities"), series.get("current_liabilities")
        )
        interest = _first(_raw(income, "interestExpense"), series.get("interest_expense"))
        shares = _first(series.get("outstanding_shares"), shares_now)
        working_capital = _first(
            series.get("working_capital"),
            current_assets - current_liabilities
            if current_assets is not None and current_liabilities is not None
            else None,
        )
        return {
            "revenue": revenue,
            "net_income": net_income,
            "gross_profit": gross_profit,
            "gross_margin": _div(gross_profit, revenue),
            "operating_income": operating_income,
            "operating_margin": _div(operating_income, revenue),
            "interest_expense": abs(interest) if interest is not None else None,
            "ebit": ebit,
            "ebitda": _first(
                series.get("ebitda"), _sum(ebit, depreciation) if ebit is not None else None
            ),
            "depreciation_and_amortization": depreciation,
            "free_cash_flow": _first(
                series.get("free_cash_flow"),
                _sum(operating_cash, capex) if operating_cash is not None else None,
            ),
            "capital_expenditure": capex,
            "dividends_and_other_cash_distributions": _first(
                _raw(cash, "dividendsPaid"), series.get("dividends_paid")
            ),
            "issuance_or_purchase_of_equity_shares": _first(
                _sum(_raw(cash, "repurchaseOfStock"), _raw(cash, "issuanceOfStock")),
                _sum(series.get("repurchase_of_stock"), series.get("issuance_of_stock")),
            ),
            "cash_and_equivalents": _first(
                _raw(balance, "cash"), series.get("cash_and_equivalents")
            ),
            "total_assets": _first(_raw(balance, "totalAssets"), series.get("total_assets")),
            "total_liabilities": _first(
                _raw(balance, "totalLiab"), series.get("total_liabilities")
            ),
            "current_assets": current_assets,
            "current_liabilities": current_liabilities,
            "working_capital": working_capital,
            "shareholders_equity": equity,
            "total_debt": debt,
            "debt_to_equity": _div(debt, equity),
            "outstanding_shares": shares,
            "earnings_per_share": _div(net_income, shares),
            "book_value_per_share": _div(equity, shares),
            "return_on_invested_capital": _div(net_income, _sum(equity, debt)),
        }

    @staticmethod
    def _currency(summary: dict[str, Any] | None) -> str:
        summary = summary or {}
        currency = (summary.get("price") or {}).get("currency") or (
            summary.get("financialData") or {}
        ).get("financialCurrency")
        return currency if isinstance(currency, str) else "USD"

    # -- MarketDataClient --------------------------------------------------------------------

    async def financial_metrics(
        self, ticker: str, as_of: date, *, period: str = "ttm", limit: int = 10
    ) -> list[FinancialMetrics]:
        summary = await self._summary(ticker)
        annual = await self.annual_facts(ticker, as_of)
        currency = self._currency(summary)
        metrics: list[FinancialMetrics] = []
        if summary and self._snapshot_is_current(as_of):
            metrics.append(self._snapshot(ticker, as_of, period, currency, summary))
        for day, facts in annual[: max(0, limit - len(metrics))]:
            metrics.append(
                FinancialMetrics(
                    ticker=ticker,
                    report_period=day,
                    period="annual",
                    currency=currency,
                    gross_margin=facts["gross_margin"],
                    operating_margin=facts["operating_margin"],
                    net_margin=_div(facts["net_income"], facts["revenue"]),
                    return_on_equity=_div(facts["net_income"], facts["shareholders_equity"]),
                    return_on_assets=_div(facts["net_income"], facts["total_assets"]),
                    return_on_invested_capital=facts["return_on_invested_capital"],
                    current_ratio=_div(facts["current_assets"], facts["current_liabilities"]),
                    debt_to_equity=facts["debt_to_equity"],
                    earnings_per_share=facts["earnings_per_share"],
                    book_value_per_share=facts["book_value_per_share"],
                )
            )
        return metrics[:limit]

    @staticmethod
    def _snapshot(
        ticker: str, as_of: date, period: str, currency: str, summary: dict[str, Any]
    ) -> FinancialMetrics:
        financial = summary.get("financialData") or {}
        stats = summary.get("defaultKeyStatistics") or {}
        detail = summary.get("summaryDetail") or {}
        market_cap = _raw(summary.get("price"), "marketCap") or _raw(detail, "marketCap")
        free_cash_flow = _raw(financial, "freeCashflow")
        debt_to_equity_pct = _raw(financial, "debtToEquity")
        return FinancialMetrics(
            ticker=ticker,
            report_period=as_of,
            period=period,
            currency=currency,
            market_cap=market_cap,
            enterprise_value=_raw(stats, "enterpriseValue"),
            price_to_earnings_ratio=_raw(detail, "trailingPE"),
            price_to_book_ratio=_raw(stats, "priceToBook"),
            price_to_sales_ratio=_raw(detail, "priceToSalesTrailing12Months"),
            enterprise_value_to_ebitda_ratio=_raw(stats, "enterpriseToEbitda"),
            enterprise_value_to_revenue_ratio=_raw(stats, "enterpriseToRevenue"),
            free_cash_flow_yield=_div(free_cash_flow, market_cap),
            peg_ratio=_raw(stats, "pegRatio"),
            gross_margin=_raw(financial, "grossMargins"),
            operating_margin=_raw(financial, "operatingMargins"),
            net_margin=_raw(financial, "profitMargins"),
            return_on_equity=_raw(financial, "returnOnEquity"),
            return_on_assets=_raw(financial, "returnOnAssets"),
            current_ratio=_raw(financial, "currentRatio"),
            quick_ratio=_raw(financial, "quickRatio"),
            # Yahoo reports debt-to-equity as a percentage (154.3 means 1.543).
            debt_to_equity=debt_to_equity_pct / 100 if debt_to_equity_pct is not None else None,
            revenue_growth=_raw(financial, "revenueGrowth"),
            earnings_growth=_raw(financial, "earningsGrowth"),
            payout_ratio=_raw(detail, "payoutRatio"),
            earnings_per_share=_raw(stats, "trailingEps"),
            book_value_per_share=_raw(stats, "bookValue"),
            free_cash_flow_per_share=_div(free_cash_flow, _raw(stats, "sharesOutstanding")),
        )

    async def line_items(
        self,
        ticker: str,
        names: Sequence[str],
        as_of: date,
        *,
        period: str = "annual",
        limit: int = 10,
    ) -> list[LineItem]:
        summary = await self._summary(ticker)
        currency = self._currency(summary)
        return [
            LineItem(
                ticker=ticker,
                report_period=day,
                period="annual",
                currency=currency,
                **{name: facts.get(name) for name in names},
            )
            for day, facts in (await self.annual_facts(ticker, as_of))[:limit]
        ]

    async def insider_trades(
        self, ticker: str, as_of: date, *, start: date | None = None, limit: int = 1000
    ) -> list[InsiderTrade]:
        summary = await self._summary(ticker)
        trades: list[InsiderTrade] = []
        for item in ((summary or {}).get("insiderTransactions") or {}).get("transactions") or []:
            day = _fmt_date(item, "startDate")
            if day is None or day > as_of or (start is not None and day < start):
                continue
            shares = _raw(item, "shares")
            value = _raw(item, "value")
            if shares is not None and "sale" in str(item.get("transactionText") or "").lower():
                shares = -abs(shares)
            trades.append(
                InsiderTrade(
                    ticker=ticker,
                    filing_date=day,
                    transaction_date=day,
                    name=item.get("filerName"),
                    title=item.get("filerRelation"),
                    transaction_shares=shares,
                    transaction_value=value,
                    transaction_price_per_share=_div(value, abs(shares) if shares else None),
                )
            )
            if len(trades) >= limit:
                break
        return trades

    async def company_news(
        self, ticker: str, as_of: date, *, start: date | None = None, limit: int = 100
    ) -> list[CompanyNews]:
        async def load() -> bytes:
            response = await self.session.get(
                RSS_URL, {"s": ticker, "region": "US", "lang": "en-US"}
            )
            return response.content

        content = await self._memo.get(("news", ticker), load)
        try:
            root = ElementTree.fromstring(content)
        except ElementTree.ParseError as exc:
            raise IntegrationError("Yahoo news feed is not valid RSS") from exc
        news: list[CompanyNews] = []
        for item in root.iter("item"):
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            try:
                published = parsedate_to_datetime((item.findtext("pubDate") or "").strip())
            except (TypeError, ValueError):
                continue
            if not title or not link:
                continue
            day = published.astimezone(UTC).date()
            if day > as_of or (start is not None and day < start):
                continue  # the feed is current only: historical ranges come back empty
            news.append(
                CompanyNews(
                    ticker=ticker, title=title, source="Yahoo Finance", date=published, url=link
                )
            )
            if len(news) >= limit:
                break
        return news

    async def market_cap(self, ticker: str, as_of: date) -> float | None:
        """Live market cap for a recent ``as_of``; else its close times the current shares."""
        summary = await self._summary(ticker)
        if not summary:
            return None
        live = _raw(summary.get("price"), "marketCap") or _raw(
            summary.get("summaryDetail"), "marketCap"
        )
        if self._snapshot_is_current(as_of) and live:
            return live
        shares = _raw(summary.get("defaultKeyStatistics"), "sharesOutstanding")
        if shares:
            bars = await self.prices(ticker, as_of - timedelta(days=7), as_of)
            if bars:
                return bars[-1].close * shares
        return live if self._snapshot_is_current(as_of) else None
