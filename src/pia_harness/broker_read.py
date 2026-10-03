"""Read-only broker tool: connection status, quotes, holdings, buyable amounts,
market rankings and investor net buying.

It calls pia-broker's internal routes with the signed client PIA also gives the
order tool; Harness knows no AWS. Every failure becomes a sentence for the model
rather than an exception, so one broker problem does not end the Turn. Figures
come from the broker as they are; nothing is computed here.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
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
_ACTIONS = ("status", "quote", "account", "buyable", "ranking", "investors")
_NEEDS_NAME = frozenset({"quote", "buyable"})
_RANKINGS = {
    "market_cap": "market cap",
    "gainers": "top gainers by change rate",
    "losers": "top losers by change rate",
    "volume": "trading volume",
    "trading_value": "trading value",
    "short_selling": "short selling",
    "most_viewed": "most viewed on the KIS trading app",
    "foreign_buying": "foreign net buying by amount",
    "foreign_selling": "foreign net selling by amount",
    "institution_buying": "institutional net buying by amount",
    "institution_selling": "institutional net selling by amount",
}
_MARKETS = ("all", "kospi", "kosdaq")
_PERIODS = ("1d", "2d", "3d", "4d", "1w", "2w", "3w", "1m", "2m", "3m")
_DEFAULT_COUNT = 10
# For figures whose reply carries no trading day: outside trading hours or on a
# holiday they are the last session's, and only KIS knows which day that was.
_NO_SESSION_DATE = (
    "These are the latest session's figures as of the lookup; the reply does not "
    "say which trading day. Do not call them today's figures or name a date unless "
    "the user or another result gives it."
)
_GROUPS = (
    ("individual", "individuals"),
    ("foreign", "foreigners"),
    ("institution", "institutions"),
    ("pension", "pension funds"),
)

BROKER_ARGUMENTS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": list(_ACTIONS),
            "description": "status: whether the user's brokerage account is connected. "
            "quote: one stock's current price, change, volume and market figures. "
            "account: the user's holdings (with sellable quantities) and cash. "
            "buyable: how much of one stock the user can buy now, as the broker "
            "calculates it. ranking: a market-wide stock ranking (see by). "
            "investors: net buying by individuals, foreigners and institutions, for "
            "one stock by day (with name) or for the whole market in the latest "
            "session (without).",
        },
        "name": {
            "type": ["string", "null"],
            "description": "For quote and buyable, and investors for one stock: the "
            "official listed stock or ETF name. Convert nicknames and abbreviations "
            "(삼전 -> 삼성전자, 하닉 -> SK하이닉스). Use the 6-character code only if the "
            "user gave a code. Leave it out for status, account, ranking and market-wide "
            "investors.",
        },
        "by": {
            "type": ["string", "null"],
            "enum": [*_RANKINGS, None],
            "description": "For ranking: "
            + "; ".join(f"{key} = {label}" for key, label in _RANKINGS.items())
            + ". There is no dividend, PER, PBR, watchlist or new-high ranking.",
        },
        "market": {
            "type": ["string", "null"],
            "enum": [*_MARKETS, None],
            "description": "For ranking and market-wide investors: kospi, kosdaq, or "
            "all (the default).",
        },
        "count": {
            "type": ["integer", "null"],
            "description": "For ranking: how many stocks; for investors of one stock: "
            "how many recent trading days. Default 10; set it when the user asks for a "
            "number.",
        },
        "period": {
            "type": ["string", "null"],
            "enum": [*_PERIODS, None],
            "description": "For the short_selling ranking only: the period it covers "
            "(d = days, w = weeks, m = months). Default 1d.",
        },
    },
    # Only action: the Orchestrator skips a read call whose required argument is null.
    "required": ["action"],
    "additionalProperties": False,
}
BROKER_DESCRIPTION = (
    "Look up the user's own brokerage data through their connected account: the "
    "connection status, a stock's live price and market figures, the user's holdings "
    "and cash, how much of a stock they can buy, market rankings, or investor net "
    "buying. Read-only; orders go through the "
    "order tool. Figures are live at the time shown, so call again for a later "
    "question instead of reusing an earlier result. Never work out a buyable "
    "quantity from cash and price; use action buyable."
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
        arguments = json.loads(call.arguments_json)
        action = arguments.get("action")
        name = arguments.get("name")
        if action not in _ACTIONS:
            return ReadToolResult(
                f"Broker lookup not run: action must be one of {', '.join(_ACTIONS)}."
            )
        if action in _NEEDS_NAME and (not isinstance(name, str) or not name.strip()):
            return ReadToolResult(
                f"Broker lookup not run: {action} needs the stock name. Ask the user "
                "which stock if it is not clear."
            )
        problem = _argument_problem(action, arguments)
        if problem is not None:
            return ReadToolResult(f"Broker lookup not run: {problem}")
        count = arguments.get("count") or _DEFAULT_COUNT
        market = arguments.get("market") or "all"
        try:
            if action == "status":
                return ReadToolResult(await self._status(user_key))
            if action == "account":
                return ReadToolResult(await self._account(user_key))
            if action == "ranking":
                by = arguments["by"]
                period = arguments.get("period") or "1d"
                return ReadToolResult(
                    await self._ranking(user_key, by, market, period, count)
                )
            if action == "investors":
                stock = name.strip() if isinstance(name, str) and name.strip() else None
                return ReadToolResult(
                    await self._investors(user_key, stock, market, count)
                )
            return ReadToolResult(await self._stock(user_key, action, name.strip()))
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
        reply = await self._get(f"/internal/members/{user_key}/account")
        totals = _join(
            ("cash (deposit)", _krw(reply.get("cash"))),
            ("total valuation", _krw(reply.get("total_valuation"))),
            ("total profit", _krw(reply.get("total_profit"), signed=True)),
        )
        lines = [
            f"Account at {_kst(reply['observed_at'])}, as reported by the broker.",
            f"Totals: {totals or 'not reported'}.",
            "Cash is not what can be spent on one stock; use action buyable for that.",
        ]
        positions = reply["positions"]
        if not positions:
            lines.append("Holdings: none.")
        else:
            lines.append(f"Holdings ({len(positions)}):")
            for item in positions:
                label = f"{item.get('name') or item['code']}({item['code']})"
                lines.append(
                    f"- {label}: "
                    + _join(
                        ("quantity", _shares(item.get("quantity"))),
                        ("sellable now", _shares(item.get("sellable_quantity"))),
                        ("average price", _krw(item.get("average_price"))),
                        ("current price", _krw(item.get("current_price"))),
                        ("valuation", _krw(item.get("valuation"))),
                        ("profit", _krw(item.get("profit"), signed=True)),
                        ("return", _percent(item.get("profit_rate"))),
                    )
                )
        return "\n".join(lines)

    async def _stock(self, user_key: str, action: str, name: str) -> str:
        found = await find_instrument(self._client, name)
        if found.code is None:
            raise _BrokerFailure(str(found.problem))
        label = f"{found.name}({found.code})"
        if action == "quote":
            quote = await self._get(
                f"/internal/members/{user_key}/quote", {"code": found.code}
            )
            return (
                f"Quote for {label}, looked up at {_kst(quote['observed_at'])}: "
                + _join(
                    ("price", _krw(quote["price"])),
                    ("change from the previous close", _change(quote)),
                    ("volume", _shares(quote.get("volume"))),
                    ("trading value", _krw(quote.get("trading_value"))),
                    ("market cap", _hundred_million(quote.get("market_cap"))),
                    ("PER", _plain(quote.get("per"))),
                    ("PBR", _plain(quote.get("pbr"))),
                    ("52-week high", _krw(quote.get("high_52w"))),
                    ("52-week low", _krw(quote.get("low_52w"))),
                )
                + ". "
                + _NO_SESSION_DATE
            )
        reply = await self._get(
            f"/internal/members/{user_key}/account", {"code": found.code}
        )
        buyable = reply["buyable"]
        unit = _krw(buyable.get("unit_price"))
        basis = f", unit price used by the broker {unit}" if unit else ""
        return (
            f"Buyable for {label} at {_kst(buyable['observed_at'])}, as calculated by "
            f"the broker without margin on a market-order basis{basis}: up to "
            f"{_shares(buyable['quantity'])}, amount {_krw(buyable['amount'])}."
        )

    async def _ranking(
        self, user_key: str, by: str, market: str, period: str, count: int
    ) -> str:
        # The broker answers most_viewed for the whole market; send only what is used.
        params = {"by": by, "market": "all" if by == "most_viewed" else market}
        if by == "short_selling":
            params["period"] = period
        reply = await self._get(f"/internal/members/{user_key}/ranking", params)
        basis = [f"market {reply['market']}"]
        if by == "most_viewed":
            basis[0] = "whole market (KIS offers no market choice for this ranking)"
        if by == "short_selling":
            dates = "–".join(_date(value) for value in reply.get("basis_dates") or ())
            basis.append(
                f"period {reply['period']}"
                + (f", KIS dates {dates}" if dates else "")
                + ", volume over the period"
            )
        if by.startswith(("foreign_", "institution_")):
            basis.append(
                "KIS's provisional tally, which can differ from the final per-stock "
                "figures that action investors gives"
            )
        lines = [
            (
                f"Ranking: {_RANKINGS[by]}, {'; '.join(basis)}, looked up at "
                f"{_kst(reply['observed_at'])}, in the broker's order."
            )
        ]
        if by != "short_selling":  # short selling carries its KIS dates
            lines.append(_NO_SESSION_DATE)
        rows = reply["rows"]
        if not rows:
            lines.append("The broker returned no stocks.")
        for number, row in enumerate(rows[:count], 1):
            label = f"{row.get('name') or row['code']}({row['code']})"
            figures = row.get("figures") or {}
            details = _join(
                ("price", _krw(row.get("price"))),
                ("change", _change(row)),
                ("volume", _shares(row.get("volume"))),
                *(_figure(key, value) for key, value in figures.items()),
            )
            lines.append(f"{number}. {label}" + (f": {details}" if details else ""))
        if count > len(rows):
            lines.append(f"The broker returned {len(rows)} stocks in this reply.")
        return "\n".join(lines)

    async def _investors(
        self, user_key: str, name: str | None, market: str, count: int
    ) -> str:
        if name is None:
            reply = await self._get(
                f"/internal/members/{user_key}/investors", {"market": market}
            )
            lines = [
                (
                    "Net buying by investor group, latest session as of the lookup at "
                    f"{_kst(reply['observed_at'])} (positive = net buying, negative = "
                    "net selling). " + _NO_SESSION_DATE
                )
            ]
            lines.extend(
                f"- {row['label']}: {_flows(row['flows'], 'thousand shares')}"
                for row in reply["rows"]
            )
            return "\n".join(lines)
        found = await find_instrument(self._client, name)
        if found.code is None:
            raise _BrokerFailure(str(found.problem))
        reply = await self._get(
            f"/internal/members/{user_key}/investors", {"code": found.code}
        )
        lines = [
            (
                f"Net buying in {found.name}({found.code}) by investor group, by trading "
                f"day, newest first, looked up at {_kst(reply['observed_at'])} (positive = net "
                "buying, negative = net selling):"
            )
        ]
        rows = reply["rows"]
        for row in rows[:count]:
            day = _join(
                ("close", _krw(row.get("close"))),
                ("change", _krw(row.get("change"), signed=True)),
            )
            lines.append(
                f"- {_date(row['label'])}: {day}; {_flows(row['flows'], 'shares')}"
            )
        if count > len(rows):
            lines.append(f"The broker returned {len(rows)} trading days.")
        return "\n".join(lines)

    async def _get(
        self, path: str, params: dict[str, str] | None = None
    ) -> dict[str, Any]:
        response = await self._client.get(path, params=params)
        if response.status_code == 409:
            raise _BrokerFailure(_NOT_CONNECTED)
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
    text = _format(abs(number)) if signed else _format(number)
    if signed:
        text = ("-" if number < 0 else "+" if number > 0 else "") + text
    return f"{text} KRW"


def _percent(value: object) -> str | None:
    number = _number(value)
    if number is None:
        return None
    return f"{number:+.2f}%"


def _shares(value: object) -> str | None:
    number = _number(value)
    return None if number is None else f"{_format(number)} shares"


def _plain(value: object) -> str | None:
    number = _number(value)
    return None if number is None else _format(number)


def _million(value: object) -> str | None:
    number = _number(value)
    return None if number is None else f"{_format(number)} million KRW (백만 원)"


def _share(value: object) -> str | None:
    number = _number(value)
    return None if number is None else f"{_format(number)}%"


def _hundred_million(value: object) -> str | None:
    # KIS hts_avls is in 100 million KRW (checked live on 2026-10-01).
    number = _number(value)
    return None if number is None else f"{_format(number)} hundred million KRW (억 원)"


def _change(quote: dict[str, Any]) -> str | None:
    amount = _krw(quote.get("change"), signed=True)
    rate = _percent(quote.get("change_rate"))
    if amount and rate:
        return f"{amount} ({rate})"
    return amount or rate


def _argument_problem(action: str, arguments: dict[str, Any]) -> str | None:
    """Check only the arguments this action uses; the others are ignored."""

    if action == "ranking":
        by = arguments.get("by")
        if not isinstance(by, str) or by not in _RANKINGS:
            return f"ranking needs by, one of {', '.join(_RANKINGS)}."
        uses = {"count"}
        if by != "most_viewed":  # KIS has no market choice there
            uses.add("market")
        if by == "short_selling":
            uses.add("period")
    elif action == "investors":
        name = arguments.get("name")
        uses = {"count"} if isinstance(name, str) and name.strip() else {"market"}
    else:
        return None
    if "market" in uses and arguments.get("market") not in (*_MARKETS, None):
        return f"market must be one of {', '.join(_MARKETS)}."
    count = arguments.get("count")
    if (
        "count" in uses
        and count is not None
        and (isinstance(count, bool) or not isinstance(count, int) or count < 1)
    ):
        return "count must be a whole number of 1 or more."
    if "period" in uses and arguments.get("period") not in (*_PERIODS, None):
        return f"period must be one of {', '.join(_PERIODS)}."
    return None


def _figure(key: str, value: object) -> tuple[str, str | None]:
    label, unit = _FIGURES.get(key, (key, _plain))
    return label, unit(value)


def _flows(flows: dict[str, Any], volume_unit: str) -> str:
    parts = []
    for key, label in _GROUPS:
        volume = _signed(flows.get(f"{key}_net_volume"))
        value = _signed(flows.get(f"{key}_net_value"))
        figures = _join(
            ("volume", None if volume is None else f"{volume} {volume_unit}"),
            ("value", None if value is None else f"{value} million KRW"),
        )
        if figures:
            parts.append(f"{label} {figures.replace('; ', ', ')}")
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


# Figure names from the broker and how to read them out; units as checked live
# (plans/2026-10-02_broker-ranking.md "실호출 확인").
_FIGURES: dict[str, tuple[str, Callable[[object], str | None]]] = {
    "market_cap": ("market cap", _hundred_million),
    "market_cap_share": ("share of the market's total cap", _share),
    "trading_value": ("trading value", _krw),
    "short_volume": ("short-sold volume", _shares),
    "short_volume_share": ("short share of volume", _share),
    "short_value": ("short-sold value", _krw),
    "short_value_share": ("short share of value", _share),
    "net_buy_value": ("net buying value", _million),
    "net_buy_volume": ("net buying volume", _shares),
}
