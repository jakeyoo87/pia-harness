"""Past filed financials; providers return facts, the existing read loop explains them."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from .orchestrator import (
    ConversationInput,
    ReadToolDefinition,
    ReadToolResult,
    ToolCall,
)

METRICS = {
    "revenue": "매출",
    "cost_of_sales": "매출원가",
    "gross_profit": "매출총이익",
    "operating_income": "영업이익",
    "net_income_parent": "지배주주 순이익",
    "eps_basic": "기본 EPS",
    "eps_diluted": "희석 EPS",
    "assets": "자산총계",
    "liabilities": "부채총계",
    "equity": "자본총계",
    "cash": "현금및현금성자산",
    "operating_cashflow": "영업활동 현금흐름",
    "investing_cashflow": "투자활동 현금흐름",
    "financing_cashflow": "재무활동 현금흐름",
    "capex": "유형자산 취득",
}
# An account whose meaning is wider than its metric's name says so in the result.
ACCOUNT_LABELS = {
    "us-gaap:PaymentsToAcquireProductiveAssets": "유형·무형자산 취득",
}
FREQUENCIES = {"annual": (5, 10), "quarterly": (4, 8)}
Q4_METRICS = tuple(METRICS)[:5]
INCOME_METRICS = tuple(METRICS)[:7]
BALANCE_METRICS = tuple(METRICS)[7:11]
DESCRIPTION = (
    "Past results: financial statements filed in periodic reports (Korean annual, "
    "quarterly and half-year reports; US 10-K/10-Q and amendments), not forecasts. "
    "The latest quarter appears only after its report is filed; for just-announced "
    "or preliminary earnings use news_search or web_search. For a company's past revenue, "
    "profit, balance sheet or cash flow, call this tool first, even when earlier turns "
    "answered such questions with web search. Returns 15 representative "
    "income, balance-sheet and cash-flow accounts together, without a brokerage connection. "
    "Q4 income amounts may be marked computed (annual minus nine-month cumulative); "
    "EPS, balance sheets and cash flows are never computed that way. No estimates, "
    "consensus, guidance, price targets, recommendations, quotes, historical P/E or "
    "P/B, or dividend-history service. Use broker for quotes/accounts and web search "
    "for forecasts or guidance. Missing is not zero. Preserve each value's period, "
    "basis, unit and reported/computed label. US periods come labeled with the calendar "
    "year/quarter of their middle (e.g. 2026-Q3); use these labels with the dates and do "
    "not look up fiscal-year or fiscal-quarter names. Never call older results the quarter "
    "the user just asked about. These are the filed figures: do not re-check them "
    "with web search; search the web only for what this result lacks. Source numbers "
    "are local to this tool result: in the answer cite at most two of their URLs "
    "(the newest report, and the annual report behind a computed Q4) using the normal "
    "citation rules; the rest stay in this result."
)
ARGUMENTS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {
            "type": "string",
            "description": "Korean stock code or exact company name; US ticker or exact English company name.",
        },
        "country": {"type": "string", "enum": ["kr", "us"]},
        "frequency": {
            "type": ["string", "null"],
            "enum": [*FREQUENCIES, None],
            "description": "Default annual. Cash flow may be cumulative, not a standalone quarter.",
        },
        "end_year": {
            "type": ["integer", "null"],
            "description": "Upper bound: Korean DART business year, or US calendar year of the period's middle. Not an as-known-at date. Omit for the latest filed periods.",
        },
        "count": {
            "type": ["integer", "null"],
            "minimum": 1,
            "maximum": 10,
            "description": "Annual: default 5, max 10. Quarterly: default 4, max 8.",
        },
    },
    "required": ["name", "country"],
    "additionalProperties": False,
}


class FinancialsError(ValueError):
    """Expected provider/input failure, with a fixed safe explanation."""


@dataclass(frozen=True)
class Source:
    accession: str
    filed: str
    form: str
    url: str


@dataclass(frozen=True)
class Fact:
    metric: str
    period: str
    value: Decimal
    unit: str
    basis: str
    account: str
    source: Source
    span: str
    kind: str
    fiscal_start: str = ""
    inputs: tuple[Fact, ...] = ()


@dataclass
class FinancialData:
    company: str
    identifier: str
    facts: list[Fact]
    notes: list[str]


def number(value: Any) -> Decimal:
    if isinstance(value, bool) or value is None or value == "":
        raise ValueError("not a number")
    result = Decimal(str(value).replace(",", ""))
    if not result.is_finite():
        raise ValueError("not a finite number")
    return result


def us_label(period: str, frequency: str) -> str:
    """US period name: the calendar quarter/year holding the period's middle.

    180 days, not half a year: a July-June year is split evenly, and ties go to the
    end year (Microsoft 2025-07..2026-06 is 2026), matching SEC frames in the sampled
    companies."""
    quarterly = frequency == "quarterly"
    middle = date.fromisoformat(period) - timedelta(days=45 if quarterly else 180)
    return (
        f"{middle.year}-Q{(middle.month + 2) // 3}" if quarterly else str(middle.year)
    )


def latest(facts: list[Fact]) -> list[Fact]:
    """Same concept/period/basis/unit: newest regular filing, across candidate tags."""
    selected: dict[tuple[str, ...], Fact] = {}
    for fact in facts:
        key = (
            fact.metric,
            fact.period,
            fact.fiscal_start,
            fact.kind,
            fact.basis,
            fact.unit,
        )
        old = selected.get(key)
        if old is None or (fact.source.filed, fact.source.accession) > (
            old.source.filed,
            old.source.accession,
        ):
            selected[key] = fact
    return list(selected.values())


def q4(annual: Fact, nine: Fact, period: str, span: str) -> Fact | None:
    if (
        annual.metric not in Q4_METRICS
        or annual.metric != nine.metric
        or (annual.unit, annual.basis, annual.fiscal_start)
        != (nine.unit, nine.basis, nine.fiscal_start)
    ):
        return None
    return replace(
        annual,
        period=period,
        span=span,
        kind="quarter",
        value=annual.value - nine.value,
        inputs=(annual, nine),
    )


def company_match(companies: list[tuple[str, str, str]], query: str) -> tuple[str, str]:
    wanted = query.strip().casefold()
    found = {
        (identifier, name)
        for identifier, code, name in companies
        if wanted in (code.casefold(), name.casefold())
    }
    if not found:
        raise FinancialsError(
            "Company not found; confirm the official name or stock code/ticker."
        )
    if len(found) != 1:
        names = ", ".join(
            f"{name} ({identifier})" for identifier, name in sorted(found)
        )
        raise FinancialsError(f"Several companies match: {names}. Ask which company.")
    return next(iter(found))


async def get(client: httpx.AsyncClient, url: str, **kwargs: Any) -> httpx.Response:
    try:
        response = await client.get(url, follow_redirects=False, **kwargs)
    except httpx.HTTPError:
        raise FinancialsError(
            "The financial data request failed (connection/timeout)."
        ) from None
    if response.status_code != 200:
        raise FinancialsError(
            f"The financial data request failed (HTTP {response.status_code})."
        )
    return response


def json_object(response: httpx.Response) -> dict[str, Any]:
    try:
        value = response.json(parse_float=Decimal)
        if not isinstance(value, dict):
            raise TypeError("object required")
        return value
    except (ValueError, TypeError):
        raise FinancialsError("The financial data response was invalid.") from None


def render(
    data: FinancialData, frequency: str, count: int, now: datetime, country: str
) -> ReadToolResult:
    facts = latest(data.facts)
    # Providers identify the report periods. Choose one duration per income/CF account;
    # keep different units/bases explicit rather than converting or mixing them.
    display: dict[tuple[str, ...], Fact] = {}
    for fact in facts:
        if frequency == "annual":
            eligible = fact.kind in ("annual", "instant")
        else:
            eligible = fact.kind == "quarter" if fact.metric in INCOME_METRICS else True
        if not eligible:
            continue
        key = (fact.period, fact.metric, fact.unit, fact.basis)
        old = display.get(key)
        priority = {"quarter": 3, "instant": 3, "ytd": 2, "annual": 1}
        if old is None or priority[fact.kind] > priority[old.kind]:
            display[key] = fact
    periods = sorted({fact.period for fact in display.values()}, reverse=True)[:count]
    basis = (
        "DART 사업연도"
        if country == "kr"
        else "calendar year/quarter of each period's middle"
    )

    def name(period: str) -> str:
        if country != "us":
            return period
        return f"{us_label(period, frequency)} (ends {period})"

    lines = [
        (
            f"past_financials (과거 실적): {data.company} ({data.identifier}); {country}; {frequency}; "
            f"year basis: {basis}; "
            f"periods {len(periods)}/{count}; latest filed period: {name(periods[0]) if periods else 'not available'}; "
            f"looked up at {now.astimezone(UTC).isoformat()}."
        ),
        (
            "Filed periodic reports, not forecasts or just-announced earnings. Missing is not zero. "
            "Do not treat cumulative cash flows as standalone quarters. These are the filed figures; "
            "do not re-check them with web search. Local source numbers below map to URLs; "
            "cite at most two of those URLs in your answer, not bare local numbers."
        ),
    ]
    sources: dict[str, tuple[int, Source]] = {}

    def source_number(source: Source) -> int:
        if source.url not in sources:
            sources[source.url] = (len(sources) + 1, source)
        return sources[source.url][0]

    for period in reversed(periods):
        group = [f for f in display.values() if f.period == period]
        common = Counter(f.source for f in group).most_common(1)[0][0]
        default = source_number(common)
        lines.append(f"\nPeriod {name(period)} — default source [{default}]")
        for metric, label in METRICS.items():
            values = [f for f in group if f.metric == metric]
            if not values:
                lines.append(f"  {metric} ({label}): 미제공 (not available)")
            for fact in values:
                ref = source_number(fact.source)
                suffix = f" [{ref}]" if ref != default and not fact.inputs else ""
                kind = (
                    f"{fact.kind} (계산값: 연간 − 3분기 누적)"
                    if fact.inputs
                    else fact.kind
                )
                lines.append(
                    f"  {metric} ({ACCOUNT_LABELS.get(fact.account, label)}): "
                    f"{fact.value:f} {fact.unit}; "
                    f"{fact.basis}; {kind}; {fact.span}; {fact.account}{suffix}"
                )
                if fact.inputs:
                    annual, nine = fact.inputs
                    lines.append(
                        "    계산값(연간 − 3분기 누적): "
                        f"{annual.value:f} ({annual.span}; {annual.account}) [{source_number(annual.source)}] − "
                        f"{nine.value:f} ({nine.span}; {nine.account}) [{source_number(nine.source)}]. "
                        "Keep the computed label in answers and charts."
                    )
    if not periods:
        lines.append(
            "No financial periods were available in the inspected range; this does not mean zero."
        )
    lines.extend(f"Note: {note}" for note in data.notes)
    if sources:
        lines.append("\nSources (listed once):")
        for ref, source in sources.values():
            lines.append(
                f"[{ref}] {source.form}; {source.accession}; filed {source.filed}; <{source.url}>"
            )
    return ReadToolResult("\n".join(lines))


class PastFinancialsTool:
    def __init__(
        self,
        *,
        dart_api_key: str | None = None,
        sec_user_agent: str | None = None,
        timeout_seconds: float = 10.0,
        async_client: httpx.AsyncClient | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        # Providers use the shared fact types above; no provider registry or extra layer.
        from .dart_financials import DartFinancials
        from .sec_financials import SecFinancials

        self._owns_client = async_client is None
        self._client = async_client or httpx.AsyncClient(
            timeout=timeout_seconds, follow_redirects=False, trust_env=False
        )
        self._clock = clock or (lambda: datetime.now(UTC))
        self._dart = (
            DartFinancials(self._client, dart_api_key) if dart_api_key else None
        )
        self._sec = (
            SecFinancials(self._client, sec_user_agent) if sec_user_agent else None
        )

    def tool(self) -> ReadToolDefinition:
        return ReadToolDefinition(
            "past_financials", DESCRIPTION, self.execute, ARGUMENTS
        )

    async def execute(
        self, user_key: str, call: ToolCall, inputs: tuple[ConversationInput, ...]
    ) -> ReadToolResult:
        del user_key, inputs
        args = json.loads(call.arguments_json)
        for optional in ("frequency", "count", "end_year"):
            if args.get(optional) is None:
                args.pop(optional, None)
        name, country = args.get("name"), args.get("country")
        frequency = args.get("frequency", "annual")
        default_count, maximum = (
            FREQUENCIES.get(frequency, (0, 0)) if isinstance(frequency, str) else (0, 0)
        )
        count = args.get("count", default_count)
        now = self._clock()
        end_year = args.get("end_year")
        if (
            not isinstance(name, str)
            or not name.strip()
            or not isinstance(country, str)
            or country not in ("kr", "us")
            or not isinstance(frequency, str)
            or frequency not in FREQUENCIES
            or type(count) is not int
            or not 1 <= count <= maximum
            or (
                "end_year" in args
                and (type(end_year) is not int or not 1900 <= end_year <= now.year)
            )
            or set(args) - set(ARGUMENTS["properties"])
        ):
            return ReadToolResult(
                "past_financials not run: invalid name/country/frequency/end_year/count. "
                "Annual count 1..10, quarterly 1..8; no estimates or statement selector."
            )
        provider = self._dart if country == "kr" else self._sec
        if provider is None:
            return ReadToolResult(
                f"past_financials not run: {country} provider is not configured."
            )
        try:
            data = await provider.read(name.strip(), frequency, end_year, count, now)
        except FinancialsError as error:
            return ReadToolResult(f"past_financials: {error}")
        except (KeyError, TypeError, ValueError, InvalidOperation):
            return ReadToolResult(
                "past_financials: The financial data response was invalid; not missing or zero."
            )
        return render(data, frequency, count, now, country)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()
