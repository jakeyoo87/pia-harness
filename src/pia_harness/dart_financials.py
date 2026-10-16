"""OpenDART reported accounts. No account-name inference, filings search, or live setup."""

from __future__ import annotations

import io
import logging
import re
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime
from typing import Any

import httpx

from .past_financials import (
    BALANCE_METRICS,
    INCOME_METRICS,
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

BASE = "https://opendart.fss.or.kr/api/"
REPORT_CODES = {1: "11013", 2: "11012", 3: "11014", 4: "11011"}
REPORT_NAMES = {1: "1분기보고서", 2: "반기보고서", 3: "3분기보고서", 4: "사업보고서"}
ACCOUNTS = {
    "ifrs-full_Revenue": "revenue",
    "ifrs-full_CostOfSales": "cost_of_sales",
    "ifrs-full_GrossProfit": "gross_profit",
    "dart_OperatingIncomeLoss": "operating_income",
    "ifrs-full_ProfitLossAttributableToOwnersOfParent": "net_income_parent",
    "ifrs-full_BasicEarningsLossPerShare": "eps_basic",
    "ifrs-full_DilutedEarningsLossPerShare": "eps_diluted",
    "ifrs-full_Assets": "assets",
    "ifrs-full_Liabilities": "liabilities",
    "ifrs-full_Equity": "equity",
    "ifrs-full_CashAndCashEquivalents": "cash",
    "ifrs-full_CashFlowsFromUsedInOperatingActivities": "operating_cashflow",
    "ifrs-full_CashFlowsFromUsedInInvestingActivities": "investing_cashflow",
    "ifrs-full_CashFlowsFromUsedInFinancingActivities": "financing_cashflow",
    "ifrs-full_PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities": "capex",
}
_KEY = re.compile(r"crtfc_key=[^&\s]+", re.IGNORECASE)


class _DartKeyFilter(logging.Filter):
    """httpx logs query URLs at INFO. DART requires its key in that query."""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        if "crtfc_key=" in message.lower():
            record.msg, record.args = _KEY.sub("crtfc_key=[REDACTED]", message), ()
        return True


class DartFinancials:
    def __init__(self, client: httpx.AsyncClient, key: str) -> None:
        self._client, self._key = client, key
        self._companies: list[tuple[str, str, str]] | None = None
        logger = logging.getLogger("httpx")
        if not any(isinstance(item, _DartKeyFilter) for item in logger.filters):
            logger.addFilter(_DartKeyFilter())

    async def _company(self, name: str) -> tuple[str, str]:
        if self._companies is None:
            response = await get(
                self._client, BASE + "corpCode.xml", params={"crtfc_key": self._key}
            )
            try:
                with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
                    root = ET.fromstring(archive.read("CORPCODE.xml"))
                self._companies = [
                    (
                        row.findtext("corp_code", ""),
                        row.findtext("stock_code", "").strip(),
                        row.findtext("corp_name", ""),
                    )
                    for row in root.findall("list")
                    if row.findtext("stock_code", "").strip()
                ]
                if not self._companies or any(
                    not re.fullmatch(r"\d{8}", row[0]) for row in self._companies
                ):
                    raise ValueError("invalid identifiers")
            except (zipfile.BadZipFile, KeyError, ET.ParseError, ValueError):
                self._companies = None
                raise FinancialsError(
                    "OpenDART company list response was invalid."
                ) from None
        return company_match(self._companies, name)

    async def _report(
        self, corp: str, year: int, quarter: int
    ) -> tuple[str, list[dict[str, Any]]] | None:
        for basis in ("CFS", "OFS"):
            response = await get(
                self._client,
                BASE + "fnlttSinglAcntAll.json",
                params={
                    "crtfc_key": self._key,
                    "corp_code": corp,
                    "bsns_year": str(year),
                    "reprt_code": REPORT_CODES[quarter],
                    "fs_div": basis,
                },
            )
            payload = json_object(response)
            status = payload.get("status")
            if status == "013":
                continue
            if status != "000":
                code = (
                    status
                    if isinstance(status, str) and re.fullmatch(r"\d{3}", status)
                    else "unknown"
                )
                raise FinancialsError(f"OpenDART request failed (code {code}).")
            rows = payload.get("list")
            if not isinstance(rows, list) or not all(
                isinstance(row, dict) for row in rows
            ):
                raise FinancialsError("OpenDART financial response was invalid.")
            return basis, rows
        return None

    async def read(
        self, name: str, frequency: str, end_year: int | None, count: int, now: datetime
    ) -> FinancialData:
        corp, company = await self._company(name)
        data = FinancialData(company, corp, [], [])
        # A later report may restate its comparison columns. Inspect intervening
        # reports, not jumps of three years, even if an older period is already filled.
        year = min(now.year, end_year + 2) if end_year is not None else now.year
        # The API's business year need not end in December. Ask for the filed
        # annual report first, rather than infer the latest quarter from today's month.
        quarter = 4
        target = (end_year * 4 + 3) if end_year is not None else None
        lower = (
            target - (count - 1) * (4 if frequency == "annual" else 1)
            if target is not None
            else None
        )
        while year >= 2015:
            if lower is not None:
                # An oldest requested Q4 still needs its Q3 cumulative operand.
                first_needed = lower - int(frequency == "quarterly" and lower % 4 == 3)
                if year * 4 + quarter - 1 < first_needed:
                    break
            try:
                current = await self._report(corp, year, quarter)
                if current:
                    found = _facts(
                        current, year, quarter, annual_output=frequency == "annual"
                    )
                    if target is None and found:
                        target = year * 4 + quarter - 1
                        lower = target - (count - 1) * (
                            4 if frequency == "annual" else 1
                        )
                    data.facts.extend(found)
            except FinancialsError as error:
                data.notes.append(f"{year} {REPORT_NAMES[quarter]}: {error}")
                break
            if frequency == "annual":
                year -= 1
            else:
                quarter -= 1
                if quarter == 0:
                    year, quarter = year - 1, 4
        facts = latest(data.facts)
        if frequency == "quarterly":
            cumulative = {
                (f.metric, f.period[:4], f.unit, f.basis): f
                for f in facts
                if f.period.endswith("Q3") and f.kind == "ytd"
            }
            for annual in tuple(facts):
                if annual.kind != "annual":
                    continue
                prior = cumulative.get(
                    (annual.metric, annual.period[:4], annual.unit, annual.basis)
                )
                if prior is not None:
                    computed = q4(annual, prior, annual.period, annual.period + " (Q4)")
                    if computed is not None:
                        facts.append(computed)
        if target is not None and lower is not None:
            facts = [f for f in facts if lower <= _period_index(f.period) <= target]
        data.facts = facts
        data.notes.append(
            "Korean periods use the reported business year/report and original period labels; "
            "exact calendar dates not provided by the API are not inferred. "
            "CFS=연결, OFS=별도; cash flow marked 누적 is year-to-date, not a single quarter. "
            "Comparison columns are selected from the latest inspected regular reports."
        )
        return data


def _period_index(period: str) -> int:
    return int(period[:4]) * 4 + (int(period[-1]) - 1 if "Q" in period else 3)


def _facts(
    report: tuple[str, list[dict[str, Any]]],
    year: int,
    quarter: int,
    *,
    annual_output: bool = False,
) -> list[Fact]:
    basis, rows = report
    facts: list[Fact] = []
    columns = (
        (("thstrm", 0), ("frmtrm", 1), ("bfefrmtrm", 2))
        if quarter == 4
        else (("thstrm", 0), ("frmtrm", 1))
    )
    for row in rows:
        account_id = row.get("account_id")
        if not isinstance(account_id, str):
            continue
        metric = ACCOUNTS.get(account_id)
        if metric is None:
            continue
        table = row.get("sj_div")
        allowed = (
            ("IS", "CIS")
            if metric in INCOME_METRICS
            else ("BS",)
            if metric in BALANCE_METRICS
            else ("CF",)
        )
        if table not in allowed:
            continue
        receipt = row.get("rcept_no", "")
        if not isinstance(receipt, str) or re.fullmatch(r"\d{14}", receipt) is None:
            raise FinancialsError("OpenDART filing reference was invalid.")
        filed = datetime.strptime(receipt[:8], "%Y%m%d").date().isoformat()
        source = Source(
            receipt,
            filed,
            REPORT_NAMES[quarter],
            "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=" + receipt,
        )
        currency = row.get("currency")
        if not isinstance(currency, str) or not currency:
            continue
        unit = currency + "/share" if metric.startswith("eps_") else currency
        for prefix, offset in columns:
            period_year = year - offset
            output_quarter = (
                4 if prefix == "frmtrm" and metric in BALANCE_METRICS else quarter
            )
            period = (
                str(period_year)
                if annual_output
                else f"{period_year}-Q{output_quarter}"
            )
            field = prefix + "_amount"
            label_field = prefix + "_nm"
            if quarter < 4 and prefix == "frmtrm" and metric in INCOME_METRICS:
                field, label_field = "frmtrm_q_amount", "frmtrm_q_nm"
            span = str(
                row.get(label_field)
                or f"사업연도 {period_year} {REPORT_NAMES[output_quarter]}"
            )
            kind = (
                "instant"
                if metric in BALANCE_METRICS
                else "annual"
                if quarter == 4
                else "quarter"
                if metric in INCOME_METRICS
                else "ytd"
            )
            samples = [
                (row.get(field), span + (" 누적" if kind == "ytd" else ""), kind)
            ]
            if quarter < 4 and metric in INCOME_METRICS:
                samples.append(
                    (
                        row.get(prefix + "_add_amount"),
                        f"사업연도 {period_year} {REPORT_NAMES[quarter]} 누적",
                        "ytd",
                    )
                )
            for raw, sample_span, sample_kind in samples:
                if raw not in (None, "", "-"):
                    facts.append(
                        Fact(
                            metric,
                            period,
                            number(raw),
                            unit,
                            basis,
                            account_id,
                            source,
                            sample_span,
                            sample_kind,
                            str(period_year),
                        )
                    )
    return facts
