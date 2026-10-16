"""Fake OpenDART and SEC replies for the financials scenario set (smoke_flow).

Made-up round amounts in the real response shapes (plans/2026-10-16_past-financials.md):
no live OpenDART or SEC call and no key. A report exists once its filing date has
passed, so the newest quarter lags the calendar as it does live. SK하이닉스 has no
capex row, for the scenario that asks for a missing account.
"""

from __future__ import annotations

import io
import json
import zipfile
from datetime import UTC, date, datetime, timedelta

import httpx

# corp_code, stock_code, name, quarterly revenue base (trillion KRW), margin
_KR = {
    "00126380": ("005930", "삼성전자", 75, 0.12),
    "00164779": ("000660", "SK하이닉스", 20, 0.35),
}
_TRILLION = 1_000_000_000_000
_REPORTS = {"11013": 1, "11012": 2, "11014": 3, "11011": 4}
# Filing month/day by report: quarter reports in the same year, the annual in the next.
_FILED = {1: (5, 14), 2: (8, 14), 3: (11, 14), 4: (3, 10)}
_IDS = {
    "revenue": ("ifrs-full_Revenue", "매출액", "IS"),
    "cost": ("ifrs-full_CostOfSales", "매출원가", "IS"),
    "gross": ("ifrs-full_GrossProfit", "매출총이익", "IS"),
    "operating": ("dart_OperatingIncomeLoss", "영업이익", "IS"),
    "net": (
        "ifrs-full_ProfitLossAttributableToOwnersOfParent",
        "지배기업 소유주지분",
        "IS",
    ),
    "eps": ("ifrs-full_BasicEarningsLossPerShare", "기본주당이익", "IS"),
    "eps_diluted": ("ifrs-full_DilutedEarningsLossPerShare", "희석주당이익", "IS"),
    "assets": ("ifrs-full_Assets", "자산총계", "BS"),
    "liabilities": ("ifrs-full_Liabilities", "부채총계", "BS"),
    "equity": ("ifrs-full_Equity", "자본총계", "BS"),
    "cash": ("ifrs-full_CashAndCashEquivalents", "현금및현금성자산", "BS"),
    "ocf": (
        "ifrs-full_CashFlowsFromUsedInOperatingActivities",
        "영업활동현금흐름",
        "CF",
    ),
    "icf": (
        "ifrs-full_CashFlowsFromUsedInInvestingActivities",
        "투자활동현금흐름",
        "CF",
    ),
    "fcf": (
        "ifrs-full_CashFlowsFromUsedInFinancingActivities",
        "재무활동현금흐름",
        "CF",
    ),
    "capex": (
        "ifrs-full_PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities",
        "유형자산의 취득",
        "CF",
    ),
}


def _filed(year: int, quarter: int) -> date:
    month, day = _FILED[quarter]
    return date(year + 1 if quarter == 4 else year, month, day)


def _quarter(corp: str, metric: str, year: int, quarter: int) -> int:
    """One quarter's flow, or the balance at the quarter's end."""
    _, _, base, margin = _KR[corp]
    revenue = (base + (year - 2021) * 3 + quarter) * _TRILLION
    values = {
        "revenue": revenue,
        "cost": int(revenue * 0.6),
        "gross": revenue - int(revenue * 0.6),
        "operating": int(revenue * margin),
        "net": int(revenue * margin * 0.8),
        "ocf": int(revenue * margin * 1.3),
        "icf": -int(revenue * 0.15),
        "fcf": -int(revenue * 0.05),
        "capex": -int(revenue * 0.12),
        "assets": revenue * 6,
        "liabilities": revenue * 2,
        "equity": revenue * 4,
        "cash": revenue // 2,
    }
    return values[metric]


def _ytd(corp: str, metric: str, year: int, quarter: int) -> int:
    return sum(_quarter(corp, metric, year, q) for q in range(1, quarter + 1))


def _eps(corp: str, year: int, quarter: int, cumulative: bool) -> str:
    net = (
        _ytd(corp, "net", year, quarter)
        if cumulative
        else _quarter(corp, "net", year, quarter)
    )
    return str(round(net / 6_000_000_000))


def _dart_report(corp: str, year: int, quarter: int) -> dict:
    receipt = _filed(year, quarter).strftime("%Y%m%d") + f"{80_0000 + quarter:06d}"[-6:]
    rows = []
    for metric, (account_id, name, table) in _IDS.items():
        if metric == "capex" and corp == "00164779":
            continue  # the missing-account scenario
        row = {
            "rcept_no": receipt,
            "reprt_code": next(code for code, q in _REPORTS.items() if q == quarter),
            "bsns_year": str(year),
            "corp_code": corp,
            "sj_div": table,
            "account_id": account_id,
            "account_nm": name,
            "currency": "KRW",
            "thstrm_nm": f"제 {year - 1968} 기"
            + ("" if quarter == 4 else f" {quarter}분기"),
            "frmtrm_nm": f"제 {year - 1969} 기",
        }
        if table == "BS":
            row["thstrm_amount"] = str(_quarter(corp, metric, year, quarter))
            row["frmtrm_amount"] = str(_quarter(corp, metric, year - 1, 4))
            if quarter == 4:
                row["bfefrmtrm_amount"] = str(_quarter(corp, metric, year - 2, 4))
        elif metric.startswith("eps"):
            row["thstrm_amount"] = _eps(corp, year, quarter, quarter == 4)
            if quarter == 4:
                row["frmtrm_amount"] = _eps(corp, year - 1, 4, True)
                row["bfefrmtrm_amount"] = _eps(corp, year - 2, 4, True)
            else:
                row["thstrm_add_amount"] = _eps(corp, year, quarter, True)
                row["frmtrm_q_amount"] = _eps(corp, year - 1, quarter, False)
                row["frmtrm_add_amount"] = _eps(corp, year - 1, quarter, True)
        elif table == "CF":  # cumulative amounts sit in thstrm_amount
            row["thstrm_amount"] = str(_ytd(corp, metric, year, quarter))
            row["frmtrm_amount"] = str(_ytd(corp, metric, year - 1, quarter))
            if quarter == 4:
                row["bfefrmtrm_amount"] = str(_ytd(corp, metric, year - 2, 4))
        elif quarter == 4:
            row["thstrm_amount"] = str(_ytd(corp, metric, year, 4))
            row["frmtrm_amount"] = str(_ytd(corp, metric, year - 1, 4))
            row["bfefrmtrm_amount"] = str(_ytd(corp, metric, year - 2, 4))
        else:
            row["thstrm_amount"] = str(_quarter(corp, metric, year, quarter))
            row["thstrm_add_amount"] = str(_ytd(corp, metric, year, quarter))
            row["frmtrm_q_amount"] = str(_quarter(corp, metric, year - 1, quarter))
            row["frmtrm_add_amount"] = str(_ytd(corp, metric, year - 1, quarter))
        rows.append(row)
    return {"status": "000", "message": "정상", "list": rows}


def _corp_zip() -> bytes:
    items = "".join(
        f"<list><corp_code>{corp}</corp_code><corp_name>{name}</corp_name>"
        f"<stock_code>{code}</stock_code></list>"
        for corp, (code, name, _, _) in _KR.items()
    )
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr("CORPCODE.xml", f"<result>{items}</result>".encode())
    return out.getvalue()


# --- SEC: NVIDIA's 52/53-week fiscal year ends in late January; Apple's in late September.
_US = {
    "0001045810": ("NVDA", "NVIDIA CORP", date(2025, 1, 27), 40.0),
    "0000320193": ("AAPL", "Apple Inc.", date(2024, 9, 29), 95.0),
}
_BILLION = 1_000_000_000


def _sec_periods(start: date) -> list[tuple[int, date, date]]:
    """(quarter, start, end) for two fiscal years from start, 13 weeks each."""
    periods = []
    for year in range(2):
        first = start + timedelta(weeks=52 * year)
        for quarter in range(1, 5):
            begin = first + timedelta(weeks=13 * (quarter - 1))
            periods.append((quarter, begin, begin + timedelta(weeks=13, days=-1)))
    return periods


_FLOWS = {  # share of revenue; cash flow tags are reported year to date only
    "Revenues": 1.0,
    "CostOfRevenue": 0.35,
    "GrossProfit": 0.65,
    "OperatingIncomeLoss": 0.5,
    "NetIncomeLoss": 0.45,
    "NetCashProvidedByUsedInOperatingActivities": 0.5,
    "NetCashProvidedByUsedInInvestingActivities": -0.2,
    "NetCashProvidedByUsedInFinancingActivities": -0.25,
    "PaymentsToAcquirePropertyPlantAndEquipment": 0.05,
}
_BALANCES = {
    "Assets": 12.0,
    "Liabilities": 4.0,
    "StockholdersEquity": 8.0,
    "CashAndCashEquivalentsAtCarryingValue": 1.2,
}
_EPS = ("EarningsPerShareBasic", "EarningsPerShareDiluted")


def _sec_fact(
    end: date, accn: str, quarter: int, filed: date, start: date | None, value: float
) -> dict:
    row = {
        "end": end.isoformat(),
        "val": value,
        "accn": accn,
        # The filing's own year and period, as SEC labels every fact it carries.
        "fy": filed.year,
        "fp": "FY" if quarter == 4 else f"Q{quarter}",
        "form": "10-K" if quarter == 4 else "10-Q",
        "filed": filed.isoformat(),
    }
    if start is not None:
        row["start"] = start.isoformat()
    return row


def _per_share(amount: float) -> float:
    return round(amount * 0.45 / (24 * _BILLION), 2)


def _sec_facts(cik: str, today: date) -> dict:
    _, name, first_day, base = _US[cik]
    facts: dict[str, list[dict]] = {tag: [] for tag in (*_FLOWS, *_BALANCES, *_EPS)}
    periods = _sec_periods(first_day)
    for index, (quarter, begin, end) in enumerate(periods):
        filed = end + timedelta(days=60 if quarter == 4 else 35)
        if filed > today:
            continue
        accn = f"{int(cik):010d}-{filed.year % 100:02d}-{index + 1:06d}"
        year_start = periods[index - quarter + 1][1]
        revenue = (base + 5 * index) * _BILLION
        ytd = sum(
            (base + 5 * i) * _BILLION for i in range(index - quarter + 1, index + 1)
        )

        filing = (end, accn, quarter, filed)

        # 10-Q: the quarter and the year so far (the same in Q1); 10-K: the year only.
        for tag, ratio in _FLOWS.items():
            year_to_date_only = tag.startswith(("NetCash", "Payments"))
            if quarter < 4 and not year_to_date_only:
                facts[tag].append(_sec_fact(*filing, begin, round(revenue * ratio)))
            if quarter > 1 or year_to_date_only:
                facts[tag].append(_sec_fact(*filing, year_start, round(ytd * ratio)))
        for tag, ratio in _BALANCES.items():
            facts[tag].append(_sec_fact(*filing, None, round(revenue * ratio)))
        for tag in _EPS:
            if quarter < 4:
                facts[tag].append(_sec_fact(*filing, begin, _per_share(revenue)))
            if quarter > 1:
                facts[tag].append(_sec_fact(*filing, year_start, _per_share(ytd)))
    us_gaap = {
        tag: {"units": {"USD/shares" if tag in _EPS else "USD": rows}}
        for tag, rows in facts.items()
    }
    return {"cik": int(cik), "entityName": name, "facts": {"us-gaap": us_gaap}}


def client() -> httpx.AsyncClient:
    """An httpx client whose transport answers as OpenDART and SEC would."""

    def handle(request: httpx.Request) -> httpx.Response:
        today = datetime.now(UTC).date()
        path = request.url.path
        if path.endswith("corpCode.xml"):
            return httpx.Response(200, content=_corp_zip())
        if path.endswith("fnlttSinglAcntAll.json"):
            params = request.url.params
            corp, year = params["corp_code"], int(params["bsns_year"])
            quarter = _REPORTS[params["reprt_code"]]
            if (
                corp not in _KR
                or params["fs_div"] != "CFS"
                or year < 2016
                or _filed(year, quarter) > today
            ):
                return httpx.Response(
                    200, json={"status": "013", "message": "조회된 데이타가 없습니다."}
                )
            return httpx.Response(200, json=_dart_report(corp, year, quarter))
        if path.endswith("company_tickers.json"):
            rows = {
                str(i): {"cik_str": int(cik), "ticker": ticker, "title": name}
                for i, (cik, (ticker, name, _, _)) in enumerate(_US.items())
            }
            return httpx.Response(200, json=rows)
        if "/companyfacts/CIK" in path:
            cik = path.rsplit("CIK", 1)[1].removesuffix(".json")
            return httpx.Response(200, content=json.dumps(_sec_facts(cik, today)))
        return httpx.Response(404)

    return httpx.AsyncClient(transport=httpx.MockTransport(handle))
