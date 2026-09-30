"""Read-only broker tool: connection status, quotes, holdings and buyable amounts.

It calls pia-broker's internal routes with the signed client PIA also gives the
order tool; Harness knows no AWS. Every failure becomes a sentence for the model
rather than an exception, so one broker problem does not end the Turn. Figures
come from the broker as they are; nothing is computed here.
"""

from __future__ import annotations

import json
import re
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
_ACTIONS = ("status", "quote", "account", "buyable")
_NEEDS_NAME = frozenset({"quote", "buyable"})

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
            "calculates it.",
        },
        "name": {
            "type": ["string", "null"],
            "description": "For quote and buyable: the official listed stock or ETF "
            "name. Convert nicknames and abbreviations (삼전 -> 삼성전자, 하닉 -> "
            "SK하이닉스). Use the 6-digit code only if the user gave a code. Leave it "
            "out for status and account.",
        },
    },
    # Only action: the Orchestrator skips a read call whose required argument is null.
    "required": ["action"],
    "additionalProperties": False,
}
BROKER_DESCRIPTION = (
    "Look up the user's own brokerage data through their connected account: the "
    "connection status, a stock's live price and market figures, the user's holdings "
    "and cash, or how much of a stock they can buy. Read-only; orders go through the "
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
        try:
            if action == "status":
                return ReadToolResult(await self._status(user_key))
            if action == "account":
                return ReadToolResult(await self._account(user_key))
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
                f"Quote for {label} at {_kst(quote['observed_at'])}: "
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
                + "."
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

    async def _get(self, path: str, params: dict[str, str] | None = None) -> dict[str, Any]:
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


def _hundred_million(value: object) -> str | None:
    # KIS hts_avls; its unit (100 million KRW, 억 원) is to be confirmed live.
    number = _number(value)
    return None if number is None else f"{_format(number)} hundred million KRW (억 원)"


def _change(quote: dict[str, Any]) -> str | None:
    amount = _krw(quote.get("change"), signed=True)
    rate = _percent(quote.get("change_rate"))
    if amount and rate:
        return f"{amount} ({rate})"
    return amount or rate


def _join(*pairs: tuple[str, str | None]) -> str:
    return "; ".join(f"{label} {value}" for label, value in pairs if value is not None)


def _kst(value: str) -> str:
    moment = datetime.fromisoformat(value)
    return moment.astimezone(_KST).strftime("%Y-%m-%d %H:%M KST")
