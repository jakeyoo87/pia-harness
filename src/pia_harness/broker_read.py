"""Read-only broker tool: the user's account, live quotes, history over a period
and market rankings.

It calls pia-broker's internal routes with the signed client PIA also gives the
order tool; Harness knows no AWS. Every failure becomes a sentence for the model
rather than an exception, so one broker problem does not end the Turn. Figures
come from the broker as they are; only their units are converted here, so every
market amount reaches the model in 억 원 (market caps in 조 원), a US stock's in
억 달러 and 조 달러, and every volume
in shares.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import httpx

from .broker_instruments import find_instrument
from .orchestrator import (
    ConversationInput,
    ReadToolDefinition,
    ReadToolResult,
    ToolCall,
)

BROKER_TOOL_NAME = "broker"
_MEMBER_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_KST = timezone(timedelta(hours=9))
_HISTORY_PERIODS = ("1m", "3m", "6m", "1y", "3y", "5y")
_SHORT_SELLING_PERIODS = ("1d", "2d", "3d", "4d", "1w", "2w", "3w", "1m", "2m", "3m")
_MARKETS = ("all", "kospi", "kosdaq")
# Rankings by data: price rankings also take us (Nasdaq, NYSE and AMEX together);
# the first value is the default.
_RANKING_MARKETS = {
    "prices": (*_MARKETS, "us"),
    "investors": _MARKETS,
    "short_selling": _MARKETS,
    "attention": _MARKETS,
}
_INDEX_MARKETS = ("kospi", "kosdaq")
# Figures other than Korean stocks, by the kind of target that asks for them. The
# keys are pia-broker's series table (src/pia_broker/series.py); the two lists are
# kept in step by hand.
_SERIES_MARKETS = {
    "spx": "S&P 500",
    "nasdaq": "NASDAQ Composite",
    "nasdaq100": "NASDAQ-100",
    "sox": "PHLX Semiconductor Index (a sector index)",
    "nikkei": "Nikkei 225",
    "hangseng": "Hang Seng",
    "shanghai": "Shanghai Composite",
    "dax": "DAX",
    "ftse": "FTSE 100",
    "sp500_futures": "E-Mini S&P 500 index futures",
    "nasdaq100_futures": "E-Mini NASDAQ-100 index futures",
}
_MACRO = {
    "us10y": "US 10-year Treasury yield",
    "us30y": "US 30-year Treasury yield",
    "us1y": "US 1-year T-bill yield",
    "fed_funds": "US federal funds rate (call rate, not the FOMC target decision)",
    "jp10y": "Japan 10-year government bond yield",
    "kr3y": "Korea 3-year treasury bond yield (not the Bank of Korea base rate)",
    "kr10y": "Korea 10-year treasury bond yield",
    "cd91": "Korea CD 91-day rate",
    "usdkrw": "USD/KRW exchange rate",
    "jpykrw": "JPY/KRW exchange rate",
    "eurusd": "EUR/USD exchange rate",
    "usdjpy": "USD/JPY exchange rate",
    "usdcny": "USD/CNY exchange rate",
    "vix": "VIX volatility index",
}
_COMMODITY = {
    "gold": "gold (COMEX)",
    "silver": "silver (COMEX)",
    "wti": "WTI crude oil",
    "brent": "Brent crude oil",
    "copper": "copper (LME)",
}
_PRICE_TARGETS: dict[str, tuple[str, ...]] = {
    "market": (*_INDEX_MARKETS, *_SERIES_MARKETS),
    "macro": tuple(_MACRO),
    "commodity": tuple(_COMMODITY),
}
# What a quote or history can be about besides a stock name, in the order taken.
_TARGETS: dict[tuple[str, str], dict[str, tuple[str, ...]]] = {
    ("quote", "prices"): _PRICE_TARGETS,
    ("history", "prices"): _PRICE_TARGETS,
    ("history", "investors"): {"market": _INDEX_MARKETS},
}
# What each action offers: data -> (ranking bases, periods). The first value of
# each list is the default, and the schema, description and checks all use this.
_ACTIONS: dict[str, dict[str, tuple[tuple[str, ...], tuple[str, ...]]]] = {
    "account": {},
    "quote": {"prices": ((), ())},
    "history": {
        "prices": ((), _HISTORY_PERIODS),
        # KIS has investor flows by day only; one month keeps the rows readable.
        "investors": ((), ("1m",)),
    },
    "ranking": {
        "prices": (("market_cap", "gainers", "losers", "volume", "trading_value"), ()),
        "investors": (
            (
                "foreign_buying",
                "foreign_selling",
                "institution_buying",
                "institution_selling",
            ),
            (),
        ),
        "short_selling": (("short_volume",), _SHORT_SELLING_PERIODS),
        "attention": (("most_viewed",), ()),
    },
}
_RANKINGS = {
    "market_cap": "market cap",
    "gainers": "top gainers by change rate",
    "losers": "top losers by change rate",
    "volume": "trading volume",
    "trading_value": "trading value",
    "short_volume": "short selling volume",
    "most_viewed": "most viewed on the KIS trading app",
    "foreign_buying": "foreign net buying by amount",
    "foreign_selling": "foreign net selling by amount",
    "institution_buying": "institutional net buying by amount",
    "institution_selling": "institutional net selling by amount",
}
_BROKER_RANKINGS = {"short_volume": "short_selling"}
_DEFAULT_COUNT = 10
_PERIOD_WORDS = {
    "1m": "1 month",
    "3m": "3 months",
    "6m": "6 months",
    "1y": "1 year",
    "3y": "3 years",
    "5y": "5 years",
}
_UNIT_WORDS = {
    "day": "daily rows (one per trading day)",
    "week": "weekly bars dated as KIS dates them; the first and last may cover part of a week",
    "month": "monthly bars dated as KIS dates them; the first and last may cover part of a month",
}
# For figures whose reply carries no trading day: outside trading hours or on a
# holiday they are the last session's, and only KIS knows which day that was.
_NO_SESSION_DATE = (
    "These are the latest session's figures as of the lookup time; the reply does "
    "not say which trading day. Say they are as of the lookup time. Do not call "
    "them today's figures or name a trading day, even if the question says today; "
    "take a date only from another result that carries one."
)
_GROUPS = (
    ("individual", "individuals"),
    ("foreign", "foreigners"),
    ("institution", "institutions"),
)

BROKER_ARGUMENTS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": list(_ACTIONS),
            "description": "account: the user's own account: connection, cash "
            "(deposit and D+2 deposit), holdings and totals. With name, that one "
            "stock in the account: quantity held, sellable now, average price, and "
            "how much can be bought now as the broker calculates it. quote: the "
            "latest value of one stock (with its market figures), market index, macro "
            "figure or commodity. history: values over a period for one of those "
            "(investor flows: a Korean stock or KOSPI/KOSDAQ only). ranking: a "
            "market-wide ranking of Korean stocks.",
        },
        "data": {
            "type": ["string", "null"],
            "enum": [
                *dict.fromkeys(d for data in _ACTIONS.values() for d in data),
                None,
            ],
            "description": "What figures. quote: prices. history: prices (stock price "
            "or index level) or investors (net buying by individuals, foreigners and "
            "institutions). ranking: prices, investors, short_selling or attention. "
            "Left out, the first one listed.",
        },
        "name": {
            "type": ["string", "null"],
            "description": "The one stock the question is about: a Korean or US stock "
            "or ETF by its official name in the language the user used, or its US "
            "ticker; do not translate a Korean name into English (애플, not Apple). "
            "Convert nicknames and abbreviations (삼전 -> 삼성전자, 하닉 -> SK하이닉스). Use a "
            "code (005930, NAS:NVDA) only if the user gave one or a candidate list "
            "showed it. account: narrows the account to that stock. "
            "quote and history: the stock to look up; it is used before market, macro "
            "or commodity.",
        },
        "market": {
            "type": ["string", "null"],
            "enum": [*_MARKETS, "us", *_SERIES_MARKETS, None],
            "description": "The market the question is about, as an index for a whole "
            "market or a sector. quote and history prices: kospi, kosdaq, "
            + ", ".join(f"{key} = {label}" for key, label in _SERIES_MARKETS.items())
            + ". history investors: kospi or kosdaq only (net buying in the whole "
            "market). ranking: all (default), kospi or kosdaq (Korean stocks in that "
            "market); ranking prices also us (US stocks of Nasdaq, NYSE and AMEX "
            "together, the latest US session). The Dow Jones index is not here: use "
            "web_search.",
        },
        "macro": {
            "type": ["string", "null"],
            "enum": [*_MACRO, None],
            "description": "A rate, exchange rate or volatility figure for quote or "
            "history prices: "
            + "; ".join(f"{key} = {label}" for key, label in _MACRO.items())
            + ". Economic releases (CPI, jobs, GDP), central bank decisions and "
            "crypto are not here: use web_search.",
        },
        "commodity": {
            "type": ["string", "null"],
            "enum": [*_COMMODITY, None],
            "description": "One commodity's market price for quote or history prices: "
            + "; ".join(f"{key} = {label}" for key, label in _COMMODITY.items())
            + ". Memory chip (DRAM, NAND) prices are not here: use web_search.",
        },
        "period": {
            "type": ["string", "null"],
            "enum": [
                *dict.fromkeys((*_HISTORY_PERIODS, *_SHORT_SELLING_PERIODS)),
                None,
            ],
            "description": "How far back from today. history prices: 1m (default) and "
            "3m give daily rows, 6m and 1y weekly rows, 3y and 5y monthly rows. "
            "history investors: 1m only, daily rows (there is no longer investor "
            "history). ranking short_selling: 1d (default) to 3m, the period the "
            "ranking covers.",
        },
        "by": {
            "type": ["string", "null"],
            "enum": [*_RANKINGS, None],
            "description": "What a ranking is ordered by. prices: market_cap "
            "(default), gainers, losers, volume, trading_value. investors: "
            "foreign_buying (default), foreign_selling, institution_buying, "
            "institution_selling (by amount). short_selling: short_volume. attention: "
            "most_viewed (on the KIS trading app). There is no dividend, PER, PBR, "
            "watchlist or new-high ranking.",
        },
        "count": {
            "type": ["integer", "null"],
            "description": "ranking: how many stocks. Default 10; set it when the user "
            "asks for a number.",
        },
    },
    # Only action: the Orchestrator skips a read call whose required argument is null.
    "required": ["action"],
    "additionalProperties": False,
}
BROKER_DESCRIPTION = (
    "Look up through the user's connected brokerage account: their account (cash, "
    "holdings, and what one stock they can buy or sell), the latest value and price "
    "history of a Korean or US stock, a market index (Korean, overseas, sector or index "
    "futures), a rate, exchange rate or volatility figure, or a commodity, investor "
    "flows for a Korean stock or market, and market rankings (Korean, or US price "
    "rankings). US stocks and "
    "ETFs (Nasdaq, NYSE, AMEX) work like Korean ones, in USD; other overseas stocks "
    "are not offered: use web_search for them. Read-only; orders "
    "go through the order tool. Figures are live at the "
    "time shown, so call again for a later question instead of reusing an earlier "
    "result. Never work out a buyable quantity from cash and price; use account "
    "with name."
)
_NOT_CONNECTED = (
    "There is no verified KIS account connection for this lookup (a connection to "
    "another broker does not count). Tell the user to connect or verify a KIS "
    "account in Member Web; do not just say the account is not connected."
)


class BrokerReadTool:
    def __init__(
        self,
        *,
        base_url: str,
        auth: httpx.Auth | None = None,
        timeout_seconds: float = 35.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not base_url.startswith("https://") and client is None:
            raise ValueError("Broker origin must be https")
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=base_url, auth=auth, timeout=timeout_seconds
        )

    def tool(self) -> ReadToolDefinition:
        return ReadToolDefinition(
            name=BROKER_TOOL_NAME,
            description=BROKER_DESCRIPTION,
            execute=self.execute,
            arguments_schema=BROKER_ARGUMENTS_SCHEMA,
        )

    async def execute(
        self,
        user_key: str,
        call: ToolCall,
        inputs: tuple[ConversationInput, ...],
    ) -> ReadToolResult:
        del inputs
        if _MEMBER_ID.fullmatch(user_key) is None:
            raise ValueError("user_key is not a Broker member id")
        # The Orchestrator already checked that arguments_json is a JSON object.
        try:
            ask = _resolve(json.loads(call.arguments_json))
        except _ArgumentProblem as problem:
            return ReadToolResult(f"Broker lookup not run: {problem}")
        action = ask["action"]
        try:
            return ReadToolResult(await self._lookup(user_key, ask))
        except _BrokerFailure as failure:
            return ReadToolResult(f"Broker {action} lookup failed: {failure}")
        except httpx.HTTPStatusError as error:
            return ReadToolResult(
                f"Broker {action} lookup failed: the broker service answered with "
                f"status {error.response.status_code}."
            )
        except httpx.HTTPError:
            return ReadToolResult(
                f"Broker {action} lookup failed: no response from the broker service. "
                "Say so and suggest trying again shortly."
            )
        except (KeyError, TypeError, ValueError):
            return ReadToolResult(
                f"Broker {action} lookup failed: the broker service reply was invalid."
            )

    async def _lookup(self, user_key: str, ask: dict[str, Any]) -> str:
        action, name = ask["action"], ask["name"]
        if action == "ranking":
            return await self._ranking(user_key, ask)
        if action == "account" and name is None:
            return await self._account(user_key)
        if action != "account" and ask["target"][0] != "name":
            if action == "quote":
                return await self._series_quote(user_key, *ask["target"])
            return await self._history(user_key, ask, None, None)
        found = await find_instrument(self._client, name)
        if found.code is None:
            raise _BrokerFailure(str(found.problem))
        label = f"{found.name}({found.code})"
        if action == "history" and ask["data"] == "investors" and ":" in found.code:
            raise _BrokerFailure(
                f"investor flows are offered for Korean stocks and the KOSPI/KOSDAQ "
                f"market only; {label} is a US stock."
            )
        if action == "account":
            return await self._stock_account(user_key, found.code, label)
        if action == "quote":
            return await self._quote(user_key, found.code, label)
        return await self._history(user_key, ask, found.code, label)

    async def _status(self, user_key: str) -> str:
        reply = await self._get(f"/internal/members/{user_key}/broker-status")
        connection = reply["connection"]
        if connection is None:
            return (
                "Brokerage connection: none. The user can connect a KIS account in "
                "Member Web; PIA's quotes, account lookups and orders need it."
            )
        broker = connection["broker"]
        lifecycle = connection["lifecycle_status"]
        verification = connection["verification_status"]
        if broker == "KIS" and lifecycle == "PENDING" and verification == "VERIFIED":
            return (
                "Brokerage connection: a KIS account is connected and verified. "
                "Quotes, account lookups and orders are available."
            )
        reason = connection.get("verification_reason")
        detail = f", reason {reason}" if reason else ""
        return (
            f"Brokerage connection: a {broker} account connection exists (state "
            f"{lifecycle}, verification {verification}{detail}), but PIA reads and "
            "orders only through a verified KIS connection. The user can finish this "
            "in Member Web."
        )

    async def _account(self, user_key: str) -> str:
        try:
            reply = await self._get(f"/internal/members/{user_key}/account")
        except _NotConnected:
            return await self._status(user_key)
        cash = _join(
            ("deposit", _krw(reply.get("cash"))),
            ("D+2 deposit", _krw(reply.get("cash_d2"))),
        )
        totals = _join(
            ("total valuation", _krw(reply.get("total_valuation"))),
            ("total profit", _krw(reply.get("total_profit"), signed=True)),
        )
        lines = [
            (
                f"Account at {_kst(reply['observed_at'])}, as reported by the broker "
                "(a verified KIS connection). Amounts in KRW."
            ),
            (
                f"Cash: {cash or 'not reported'}. Cash is not what can be spent on one "
                "stock; use account with name for that."
            ),
            f"Korean account totals: {totals or 'not reported'}.",
        ]
        positions = reply["positions"]
        if not positions:
            lines.append("Holdings: none.")
        else:
            lines.append(f"Holdings ({len(positions)}):")
            lines.extend(_holding(item, "KRW") for item in positions)
        us = reply.get("us")
        if us is None and reply.get("us_error"):
            lines.append(
                f"US stocks: not available this time (broker error {reply['us_error']}); "
                "the Korean account above is complete. Do not say the user has no US "
                "stocks. The account may not be set up for overseas trading, or the "
                "broker's overseas service may be down; suggest checking the KIS app."
            )
        if us is not None:
            us_cash = _join(
                ("USD deposit", _money(us.get("cash"), "USD")),
                (
                    "exchange rate KIS applies",
                    _money(us.get("exchange_rate"), "KRW per USD"),
                ),
            )
            lines.append(
                f"US stocks (amounts in USD, as KIS reports them): "
                f"{us_cash or 'cash not reported'}. The Korean account totals above "
                "leave these out."
            )
            us_positions = us["positions"]
            if not us_positions:
                lines.append("US holdings: none.")
            else:
                lines.append(f"US holdings ({len(us_positions)}):")
                lines.extend(_holding(item, "USD") for item in us_positions)
        return "\n".join(lines)

    async def _stock_account(self, user_key: str, code: str, label: str) -> str:
        try:
            reply = await self._get(
                f"/internal/members/{user_key}/account", {"code": code}
            )
        except _NotConnected:
            return await self._status(user_key)
        buyable = reply["buyable"]
        position = reply["position"]
        currency = buyable["currency"]
        unit = _money(buyable.get("unit_price"), currency)
        basis = f" (unit price used by the broker {unit})" if unit else ""
        lines = [
            (
                f"{label} in the user's account at {_kst(buyable['observed_at'])}, "
                f"as reported by the broker. Amounts in {currency}."
            ),
            f"Held: {_position(position, currency) if position else 'none'}.",
            (
                "Buyable now, as calculated by the broker without margin for a "
                f"limit order at the current price{basis}: up to "
                f"{_shares(buyable['quantity'])}, "
                f"amount {_money(buyable['amount'], currency)}."
            ),
        ]
        after = buyable.get("after_exchange_quantity")
        if after is not None:
            lines.append(
                f"After exchanging KRW (KIS 환전이후 figure): up to {_shares(after)}. "
                "PIA does not exchange currency; KIS does it when the user's account "
                "allows buying US stocks with KRW."
            )
        return "\n".join(lines)

    async def _quote(self, user_key: str, code: str, label: str) -> str:
        quote = await self._get(f"/internal/members/{user_key}/quote", {"code": code})
        currency = quote["currency"]
        # KIS gives a Korean market cap in 억 원 and a US one in dollars.
        word, cap_divisor = ("원", _EOK) if currency == "KRW" else ("달러", _WON)
        return (
            f"Quote for {label}, looked up at {_kst(quote['observed_at'])}: "
            + _join(
                ("price", _money(quote["price"], currency)),
                ("change from the previous close", _change(quote, currency)),
                ("volume", _shares(quote.get("volume"))),
                (
                    "trading value",
                    _market_amount(quote.get("trading_value"), _WON, currency=word),
                ),
                (
                    "market cap",
                    _market_amount(
                        quote.get("market_cap"), cap_divisor, in_jo=True, currency=word
                    ),
                ),
                ("PER", _plain(quote.get("per"))),
                ("PBR", _plain(quote.get("pbr"))),
                ("52-week high", _money(quote.get("high_52w"), currency)),
                ("52-week low", _money(quote.get("low_52w"), currency)),
            )
            + ". "
            + _NO_SESSION_DATE
        )

    async def _series_quote(self, user_key: str, kind: str, key: str) -> str:
        quote = await self._get(f"/internal/members/{user_key}/quote", {kind: key})
        unit = quote["unit"]
        value = f"{_exact(quote['price'])}{'' if unit == '%' else ' '}{unit}"
        change = _exact(quote.get("change"), signed=True)
        rate = _percent(quote.get("change_rate"))
        moved = ""
        if change is not None:
            change_unit = "percentage points" if unit == "%" else unit
            moved = f"; change from the previous close {change} {change_unit}"
            moved += f" ({rate})" if rate else ""
        return (
            f"{quote['name']}, looked up at {_kst(quote['observed_at'])}: {value}{moved}. "
            "This is the latest value KIS gives as of the lookup time, not necessarily "
            "a live one. " + _NO_SESSION_DATE
        )

    async def _history(
        self, user_key: str, ask: dict[str, Any], code: str | None, label: str | None
    ) -> str:
        data, period = ask["data"], ask["period"]
        kind, key = ask["target"]
        params = {"data": data, "period": period}
        if code is None:
            params[kind] = key
        else:
            params["code"] = code
        reply = await self._get(f"/internal/members/{user_key}/history", params)
        rows = list(reversed(reply["rows"]))  # the broker gives the newest first
        when = (
            f"over the last {_PERIOD_WORDS[period]}, {_UNIT_WORDS[reply['unit']]}, oldest "
            f"first, {len(rows)} rows, looked up at {_kst(reply['observed_at'])}."
        )
        if code is None and key not in _INDEX_MARKETS:
            # An overseas index, rate, exchange rate or commodity: values exactly as
            # KIS reports them, in the series' own unit, with no volume.
            lines = [
                (
                    f"Prices of {reply['name']} {when} Values in {reply['price_unit']}, "
                    "as KIS reports them. The newest row may cover a session, week or "
                    "month still in progress. If the user asked for daily rows and these "
                    "are not daily, say so."
                )
            ]
            lines.extend(
                f"- {row['date']}: "
                + _join(
                    ("open", _exact(row["open"])),
                    ("high", _exact(row["high"])),
                    ("low", _exact(row["low"])),
                    ("close", _exact(row["close"])),
                )
                for row in rows
            )
            if not rows:
                lines.append("The broker returned no rows for this period.")
            return "\n".join(lines)
        subject = f"the {key.upper()} market" if code is None else label
        source = "market" if code is None else "stock"
        if data == "prices" and reply.get("price_unit") == "USD":
            lines = [
                (
                    f"Prices of {subject} {when} Split-adjusted prices in USD, as KIS "
                    "reports them; volume in shares; trading value in 억 달러. The newest "
                    "row may cover a "
                    "session, week or month still in progress. If the user asked for "
                    "daily rows and these are not daily, say so."
                )
            ]
            lines.extend(
                f"- {row['date']}: "
                + _join(
                    ("open", _exact(row["open"])),
                    ("high", _exact(row["high"])),
                    ("low", _exact(row["low"])),
                    ("close", _exact(row["close"])),
                    ("volume", _shares(row.get("volume"))),
                    (
                        "trading value",
                        _market_amount(row.get("trading_value"), _WON, currency="달러"),
                    ),
                )
                for row in rows
            )
        elif data == "prices":
            per_unit, divisor = _PRICE_DAY_UNITS[source]
            units = (
                "Index levels in points"
                if code is None
                else "Split-adjusted prices in KRW"
            )
            lines = [
                (
                    f"Prices of {subject} {when} {units}; volume in shares; trading "
                    "value in 억 원. The newest row may cover a session, "
                    "week or month "
                    "still in progress. If the user asked for daily rows and these are "
                    "not daily, say so."
                )
            ]
            for row in rows:
                lines.append(
                    f"- {row['date']}: "
                    + _join(
                        ("open", _plain(row["open"])),
                        ("high", _plain(row["high"])),
                        ("low", _plain(row["low"])),
                        ("close", _plain(row["close"])),
                        ("volume", _shares(row.get("volume"), per_unit)),
                        (
                            "trading value",
                            _market_amount(row.get("trading_value"), divisor),
                        ),
                    )
                )
        else:
            close_unit = "the index level in points" if code is None else "in KRW"
            per_unit, divisor = _INVESTOR_DAY_UNITS[source]
            lines = [
                (
                    f"Net buying by investor group in {subject} {when} Positive = net "
                    "buying, negative = net selling; amounts in 억 원, "
                    "volumes in "
                    f"shares; close is {close_unit}. The newest row may be a session "
                    "still in progress."
                )
            ]
            for row in rows:
                close = _plain(row.get("close"))
                change = _signed(row.get("change"))
                parts = []
                if close is not None:
                    parts.append(f"close {close}" + (f" ({change})" if change else ""))
                parts.append(_flows(row["flows"], per_unit, divisor))
                lines.append(f"- {row['date']}: " + "; ".join(parts))
        if not rows:
            lines.append("The broker returned no rows for this period.")
        return "\n".join(lines)

    async def _ranking(self, user_key: str, ask: dict[str, Any]) -> str:
        by, market, count = ask["by"], ask["market"], ask["count"]
        broker_by = _BROKER_RANKINGS.get(by, by)
        # The broker answers most_viewed for the whole market; send only what is used.
        params = {"by": broker_by, "market": "all" if by == "most_viewed" else market}
        if ask["data"] == "short_selling":
            params["period"] = ask["period"]
        reply = await self._get(f"/internal/members/{user_key}/ranking", params)
        basis = [f"market {reply['market']}"]
        if by == "most_viewed":
            basis[0] = "whole market (KIS offers no market choice for this ranking)"
        if ask["data"] == "short_selling":
            dates = "–".join(_date(value) for value in reply.get("basis_dates") or ())
            basis.append(
                f"period {reply['period']}"
                + (f", KIS dates {dates}" if dates else "")
                + ", volume over the period"
            )
        if ask["data"] == "investors":
            basis.append(
                "KIS's provisional tally; history investors gives daily figures whose "
                "basis and timing can differ"
            )
        currency = reply.get("currency", "KRW")
        if currency == "KRW":
            order = (
                "in the broker's order. Market caps in 조 원, other amounts in 억 원."
            )
        else:
            order = (
                "Nasdaq, NYSE and AMEX merged by the ranked figure (KIS ranks each "
                "exchange); prices in USD, market caps in 조 달러, other amounts in 억 "
                "달러."
            )
        lines = [
            (
                f"Ranking: {_RANKINGS[by]}, {'; '.join(basis)}, looked up at "
                f"{_kst(reply['observed_at'])}, {order}"
            )
        ]
        if ask["data"] != "short_selling":  # short selling carries its KIS dates
            lines.append(_NO_SESSION_DATE)
        rows = reply["rows"]
        if not rows:
            lines.append("The broker returned no stocks.")
        for number, row in enumerate(rows[:count], 1):
            label = f"{row.get('name') or row['code']}({row['code']})"
            figures = row.get("figures") or {}
            details = _join(
                ("price", _money(row.get("price"), currency)),
                ("change", _change(row, currency)),
                ("volume", _shares(row.get("volume"))),
                *(_figure(key, value, currency) for key, value in figures.items()),
            )
            lines.append(f"{number}. {label}" + (f": {details}" if details else ""))
        if count > len(rows):
            lines.append(f"The broker returned {len(rows)} stocks in this reply.")
        return "\n".join(lines)

    async def _get(
        self, path: str, params: dict[str, str] | None = None
    ) -> dict[str, Any]:
        response = await self._client.get(path, params=params)
        if response.status_code == 409:
            raise _NotConnected(_NOT_CONNECTED)
        if response.status_code == 503:
            raise _BrokerFailure(
                "the broker is temporarily unavailable. Say so and suggest trying "
                "again shortly."
            )
        if response.status_code != 200:
            raise _BrokerFailure(
                f"the broker service answered with status {response.status_code}."
            )
        value = response.json()
        if not isinstance(value, dict):
            raise TypeError("Broker response must be an object")
        return value

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


class _BrokerFailure(Exception):
    """A broker answer the model should explain; the message is for the model."""


class _NotConnected(_BrokerFailure):
    """Broker 409: no verified KIS connection."""


class _ArgumentProblem(Exception):
    """Arguments the tool cannot run with; the message is for the model."""


def _resolve(arguments: dict[str, Any]) -> dict[str, Any]:
    """The lookup asked for, with defaults filled in. Only the arguments the action
    and data use are checked; the others are ignored. Only a left-out (null)
    argument takes its default."""

    action = arguments.get("action")
    if not isinstance(action, str) or action not in _ACTIONS:
        raise _ArgumentProblem(f"action must be one of {', '.join(_ACTIONS)}.")
    # A name given wrongly must not turn into the whole account or a market.
    name = None if action == "ranking" else arguments.get("name")
    if name is not None:
        if not isinstance(name, str) or not name.strip():
            raise _ArgumentProblem(
                "name must be the stock name; leave it out for the whole account or "
                "a market."
            )
        name = name.strip()
    ask: dict[str, Any] = {"action": action, "name": name, "data": None}
    offered = _ACTIONS[action]
    if offered:
        ask["data"] = data = _choice(arguments, "data", tuple(offered), action)
        bases, periods = offered[data]
        where = f"{action} {data}"
        ask["by"] = _choice(arguments, "by", bases, where) if bases else None
        ask["period"] = (
            _choice(arguments, "period", periods, where) if periods else None
        )
    if action in ("quote", "history"):
        ask["target"] = _target(arguments, name, action, ask["data"])
    if action == "ranking":
        ask["market"] = _choice(
            arguments,
            "market",
            _RANKING_MARKETS[ask["data"]],
            f"{action} {ask['data']}",
        )
        count = arguments.get("count")
        if count is None:
            count = _DEFAULT_COUNT
        if isinstance(count, bool) or not isinstance(count, int) or count < 1:
            raise _ArgumentProblem("count must be a whole number of 1 or more.")
        ask["count"] = count
    return ask


def _target(
    arguments: dict[str, Any], name: str | None, action: str, data: str
) -> tuple[str, str]:
    """The one thing a quote or history is about: the name, else the first of
    market, macro and commodity given. Only that one is checked."""

    if name is not None:
        return "name", name
    offered = _TARGETS[(action, data)]
    for kind, values in offered.items():
        value = arguments.get(kind)
        if value is None:
            continue
        if not isinstance(value, str) or value not in values:
            raise _ArgumentProblem(
                f"{kind} for {action} {data} must be one of {', '.join(values)}."
            )
        return kind, value
    kinds = ", ".join(("name", *offered))
    raise _ArgumentProblem(
        f"{action} {data} needs one of {kinds}. Ask the user what to look up if it is "
        "not clear."
    )


def _choice(
    arguments: dict[str, Any], key: str, values: tuple[str, ...], where: str
) -> str:
    value = arguments.get(key)
    if value is None:
        return values[0]
    if not isinstance(value, str) or value not in values:
        raise _ArgumentProblem(f"{key} for {where} must be one of {', '.join(values)}.")
    return value


# KIS units of the amounts in Broker replies, as divisors to 억 원.
_WON = 100_000_000
_MILLION = 100
_EOK = 1
# History rows of a stock or a market: (shares per volume unit, amount unit), as
# checked live (plans/2026-10-04_broker-tool-structure.md "실호출 확인 결과"). A
# market counts volume in thousands of shares and amounts in millions of KRW.
_PRICE_DAY_UNITS = {"stock": (1, _WON), "market": (1000, _MILLION)}
_INVESTOR_DAY_UNITS = {"stock": (1, _MILLION), "market": (1000, _MILLION)}


def _number(value: object) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def _format(value: float) -> str:
    if isinstance(value, int) or value == int(value):
        return f"{int(value):,}"
    return f"{value:,.2f}"


def _krw(value: object, *, signed: bool = False) -> str | None:
    number = _number(value)
    if number is None:
        return None
    text = _signed(number) if signed else _format(number)
    return f"{text} KRW"


def _market_amount(
    value: object,
    divisor: int,
    *,
    signed: bool = False,
    in_jo: bool = False,
    currency: str = "원",
) -> str | None:
    """A market amount to two decimals, in 억, or in 조 for market caps, of 원 or
    달러 (a US stock's amounts come in dollars and follow the same rule).

    The unit belongs to the figure, never to its size: one figure keeps one unit
    across rows, so a model can chart it without converting anything.
    """

    number = _number(value)
    if number is None:
        return None
    amount, unit = number / divisor, f"억 {currency}"
    if in_jo:
        amount, unit = amount / 10_000, f"조 {currency}"
    amount = round(amount, 2)
    text = f"{abs(amount) if signed else amount:,.2f}".rstrip("0").rstrip(".")
    if signed and amount:
        text = ("-" if amount < 0 else "+") + text
    return f"{'0' if text in ('-0', '') else text}{unit}"


def _exact(value: object, *, signed: bool = False) -> str | None:
    """A number with thousands separators and every decimal the broker gave, for
    figures such as exchange rates and yields where two decimals would hide moves."""

    number = _number(value)
    if number is None:
        return None
    text = f"{Decimal(repr(abs(number))).normalize():,f}"
    if number < 0:
        return "-" + text
    return ("+" if signed and number > 0 else "") + text


def _percent(value: object) -> str | None:
    number = _number(value)
    if number is None:
        return None
    return f"{number:+.2f}%"


def _shares(value: object, per_unit: int = 1, *, signed: bool = False) -> str | None:
    number = _number(value)
    if number is None:
        return None
    number = number * per_unit
    return f"{_signed(number) if signed else _format(number)} shares"


def _plain(value: object) -> str | None:
    number = _number(value)
    return None if number is None else _format(number)


def _share(value: object) -> str | None:
    number = _number(value)
    return None if number is None else f"{_format(number)}%"


def _money(value: object, currency: str, *, signed: bool = False) -> str | None:
    """KRW as today; USD with every decimal KIS gave (233.95 USD)."""

    if currency == "KRW":
        return _krw(value, signed=signed)
    text = _exact(value, signed=signed)
    return None if text is None else f"{text} {currency}"


def _change(quote: dict[str, Any], currency: str) -> str | None:
    amount = _money(quote.get("change"), currency, signed=True)
    rate = _percent(quote.get("change_rate"))
    if amount and rate:
        return f"{amount} ({rate})"
    return amount or rate


def _holding(item: dict[str, Any], currency: str) -> str:
    return f"- {item.get('name') or item['code']}({item['code']}): " + _position(
        item, currency
    )


def _position(item: dict[str, Any], currency: str) -> str:
    return _join(
        ("quantity", _shares(item.get("quantity"))),
        ("sellable now", _shares(item.get("sellable_quantity"))),
        ("average price", _money(item.get("average_price"), currency)),
        ("current price", _money(item.get("current_price"), currency)),
        ("valuation", _money(item.get("valuation"), currency)),
        ("profit", _money(item.get("profit"), currency, signed=True)),
        ("return", _percent(item.get("profit_rate"))),
    )


def _figure(key: str, value: object, currency: str = "KRW") -> tuple[str, str | None]:
    label, unit = _FIGURES.get(key, (key, _plain))
    if currency == "USD" and key in ("market_cap", "trading_value"):
        # KIS gives US amounts in dollars: the same 조 / 억 rule, in 달러.
        in_jo = key == "market_cap"
        return label, _market_amount(value, _WON, in_jo=in_jo, currency="달러")
    return label, unit(value)


def _flows(flows: dict[str, Any], per_unit: int, divisor: int) -> str:
    parts = []
    for key, label in _GROUPS:
        figures = [
            figure
            for figure in (
                _shares(flows.get(f"{key}_net_volume"), per_unit, signed=True),
                _market_amount(flows.get(f"{key}_net_value"), divisor, signed=True),
            )
            if figure is not None
        ]
        if figures:
            parts.append(f"{label} {', '.join(figures)}")
    return "; ".join(parts)


def _signed(value: object) -> str | None:
    number = _number(value)
    if number is None:
        return None
    return ("-" if number < 0 else "+" if number > 0 else "") + _format(abs(number))


def _date(value: str) -> str:
    return f"{value[:4]}-{value[4:6]}-{value[6:]}" if len(value) == 8 else value


def _join(*pairs: tuple[str, str | None]) -> str:
    return "; ".join(f"{label} {value}" for label, value in pairs if value is not None)


def _kst(value: str) -> str:
    moment = datetime.fromisoformat(value)
    return moment.astimezone(_KST).strftime("%Y-%m-%d %H:%M KST")


# Ranking figures from the broker and how to read them out; KIS units as checked
# live (plans/2026-10-02_broker-ranking.md "실호출 확인").
_FIGURES: dict[str, tuple[str, Callable[[object], str | None]]] = {
    "market_cap": ("market cap", lambda value: _market_amount(value, _EOK, in_jo=True)),
    "market_cap_share": ("share of the market's total cap", _share),
    "trading_value": ("trading value", lambda value: _market_amount(value, _WON)),
    "short_volume": ("short-sold volume", _shares),
    "short_volume_share": ("short share of volume", _share),
    "short_value": ("short-sold value", lambda value: _market_amount(value, _WON)),
    "short_value_share": ("short share of value", _share),
    "net_buy_value": (
        "net buying value",
        lambda value: _market_amount(value, _MILLION, signed=True),
    ),
    "net_buy_volume": ("net buying volume", lambda value: _shares(value, signed=True)),
}
