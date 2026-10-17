"""SEC Company Facts: regular filings, value periods, and period-specific tag choices."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

import httpx

from .past_financials import (
    BALANCE_METRICS,
    Fact,
    FinancialData,
    FinancialsError,
    Source,
    company_match,
    get,
    json_object,
    latest,
    number,
    q4,
)

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{}.json"
FORMS = ("10-K", "10-Q", "10-K/A", "10-Q/A")
# Ordered only to break a same-filing tie; newest filing wins across all candidates.
ACCOUNTS = {
    "revenue": (
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
    ),
    "cost_of_sales": ("CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold"),
    "gross_profit": ("GrossProfit",),
    "operating_income": ("OperatingIncomeLoss",),
    "net_income_parent": ("NetIncomeLoss",),
    "eps_basic": ("EarningsPerShareBasic",),
    "eps_diluted": ("EarningsPerShareDiluted",),
    "assets": ("Assets",),
    "liabilities": ("Liabilities",),
    "equity": (
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
        "StockholdersEquity",
    ),
    "cash": ("CashAndCashEquivalentsAtCarryingValue",),
    "operating_cashflow": ("NetCashProvidedByUsedInOperatingActivities",),
    "investing_cashflow": ("NetCashProvidedByUsedInInvestingActivities",),
    "financing_cashflow": ("NetCashProvidedByUsedInFinancingActivities",),
    # Some filers (NVIDIA since 2020) report capex as ProductiveAssets, which also
    # covers intangible assets; the result labels it 유형·무형자산 취득.
    "capex": (
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "PaymentsToAcquireProductiveAssets",
    ),
}


class SecFinancials:
    def __init__(self, client: httpx.AsyncClient, user_agent: str) -> None:
        self._client = client
        self._headers = {"User-Agent": user_agent.strip()}
        self._companies: list[tuple[str, str, str]] | None = None

    async def _company(self, name: str) -> tuple[str, str]:
        if self._companies is None:
            payload = json_object(
                await get(self._client, TICKERS_URL, headers=self._headers)
            )
            try:
                self._companies = [
                    (
                        str(int(row["cik_str"])).zfill(10),
                        str(row["ticker"]),
                        str(row["title"]),
                    )
                    for row in payload.values()
                ]
                if not self._companies or any(
                    not row[0].isdigit() or len(row[0]) != 10 for row in self._companies
                ):
                    raise ValueError("invalid identifiers")
            except (KeyError, TypeError, ValueError):
                self._companies = None
                raise FinancialsError(
                    "SEC company list response was invalid."
                ) from None
        return company_match(self._companies, name)

    async def read(
        self, name: str, frequency: str, end_year: int | None, count: int, now: datetime
    ) -> FinancialData:
        del count
        cik, company = await self._company(name)
        payload = json_object(
            await get(self._client, FACTS_URL.format(cik), headers=self._headers)
        )
        if str(payload.get("cik", "")).zfill(10) != cik:
            raise FinancialsError(
                "SEC company response did not match the selected company."
            )
        namespaces = payload.get("facts")
        if not isinstance(namespaces, dict):
            raise FinancialsError("SEC financial response was invalid.")
        raw_facts = namespaces.get("us-gaap")
        if not isinstance(raw_facts, dict):
            raise FinancialsError(
                "SEC standard financial accounts were not available (10-K/10-Q only)."
            )
        upper = min(date(end_year, 12, 31), now.date()) if end_year else now.date()
        facts = latest(_facts(raw_facts, cik, upper))
        annual_ends = {f.period for f in facts if f.kind == "annual"}
        if frequency == "quarterly":
            for annual in tuple(facts):
                if annual.kind != "annual":
                    continue
                candidates = [
                    f
                    for f in facts
                    if f.metric == annual.metric
                    and f.kind == "ytd"
                    and f.fiscal_start == annual.fiscal_start
                    and f.unit == annual.unit
                    and f.basis == annual.basis
                    and 230 <= _days(f.span) <= 300
                    and 60
                    <= (
                        date.fromisoformat(annual.period) - date.fromisoformat(f.period)
                    ).days
                    <= 120
                ]
                # A reported standalone quarter takes precedence over a subtraction.
                reported = any(
                    f.metric == annual.metric
                    and f.period == annual.period
                    and f.kind == "quarter"
                    and f.unit == annual.unit
                    and f.basis == annual.basis
                    for f in facts
                )
                if not candidates or reported:
                    continue
                nine = max(candidates, key=lambda f: f.period)
                start = date.fromisoformat(nine.period) + timedelta(days=1)
                computed = q4(
                    annual, nine, annual.period, f"{start.isoformat()}..{annual.period}"
                )
                if computed is not None:
                    facts.append(computed)
        else:
            facts = [
                f
                for f in facts
                if f.kind == "annual"
                or (f.kind == "instant" and f.period in annual_ends)
            ]
        return FinancialData(
            str(payload.get("entityName") or company),
            cik,
            facts,
            [
                (
                    "SEC 10-K/10-Q and /A only; dates are the values' start/end, not filing fy/fp. "
                    "SEC entity-wide facts; the API does not label CFS/OFS. "
                    "Cash flow may be cumulative or annual, not a standalone quarter."
                )
            ],
        )


def _days(span: str) -> int:
    start, end = span.split("..")
    return (date.fromisoformat(end) - date.fromisoformat(start)).days + 1


def _facts(accounts: dict[str, Any], cik: str, upper: date) -> list[Fact]:
    facts: list[Fact] = []
    for metric, tags in ACCOUNTS.items():
        for tag in tags:
            account = accounts.get(tag, {})
            for unit, values in account.get("units", {}).items():
                currency = unit.removesuffix("/shares")
                if len(currency) != 3 or not currency.isalpha():
                    continue
                if metric.startswith("eps_") != unit.endswith("/shares"):
                    continue
                for value in values:
                    if value.get("form") not in FORMS:
                        continue
                    end = date.fromisoformat(value["end"])
                    if end > upper:
                        continue
                    if metric in BALANCE_METRICS:
                        start, span, kind = None, end.isoformat(), "instant"
                    else:
                        start = date.fromisoformat(value["start"])
                        days = (end - start).days + 1
                        if not 0 < days <= 400:
                            continue
                        span = f"{start.isoformat()}..{end.isoformat()}"
                        kind = (
                            "annual"
                            if days >= 330
                            else "quarter"
                            if days <= 120
                            else "ytd"
                        )
                    accession = value["accn"]
                    if (
                        not isinstance(accession, str)
                        or len(accession) != 20
                        or not accession.replace("-", "").isdigit()
                    ):
                        raise FinancialsError("SEC filing reference was invalid.")
                    filed = date.fromisoformat(value["filed"]).isoformat()
                    source = Source(
                        accession,
                        filed,
                        value["form"],
                        f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession.replace('-', '')}/{accession}-index.html",
                    )
                    facts.append(
                        Fact(
                            metric,
                            end.isoformat(),
                            number(value["val"]),
                            unit,
                            "SEC entity",
                            "us-gaap:" + tag,
                            source,
                            span,
                            kind,
                            start.isoformat() if start else "",
                        )
                    )
    return facts
