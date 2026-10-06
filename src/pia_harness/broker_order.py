"""Buy/sell order execution tool backed by pia-broker's internal routes.

The tool prepares an order (instrument search, quote, confirmation text) and runs
it only after the user confirms it. Harness knows no AWS: PIA passes the Broker
origin and an httpx auth that signs each request.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import httpx

from .broker_instruments import find_instrument
from .orchestrator import (
    ExecutionToolDefinition,
    PreparationResult,
    PreparedAction,
    ToolCall,
)

ORDER_TOOL_NAME = "order"
_MEMBER_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_KST = timezone(timedelta(hours=9))
_SIDES = {"BUY": "매수", "SELL": "매도"}
_ORDER_TYPES = frozenset({"LIMIT", "MARKET"})

ORDER_ARGUMENTS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {
            "type": ["string", "null"],
            "description": "Official name of a Korean or US stock or ETF (Korean or "
            "English), or a US ticker. Convert nicknames and abbreviations (삼전 -> "
            "삼성전자, 하닉 -> SK하이닉스). Use a code (005930, NAS:NVDA) only if the "
            "user gave one or a candidate list showed it. Null only if no stock was "
            "named.",
        },
        "side": {"type": ["string", "null"], "enum": ["BUY", "SELL", None]},
        "quantity": {"type": ["integer", "null"], "description": "Number of shares."},
        "order_type": {
            "type": ["string", "null"],
            "enum": ["LIMIT", "MARKET", None],
            "description": "LIMIT unless the user explicitly asks for a market order. "
            "US stocks take limit orders only.",
        },
        "price": {
            "type": ["number", "null"],
            "description": "Limit price in the stock's currency (KRW, or USD for a US "
            "stock) only if the user gave one.",
        },
    },
    "required": ["name", "side", "quantity", "order_type", "price"],
    "additionalProperties": False,
}
ORDER_DESCRIPTION = (
    "Prepare a Korean or US stock buy or sell order for the user's confirmation; nothing "
    "is executed yet. Use when the user asks to buy or sell a stock, or answers a "
    "question about an order being drafted; call it once per order. Fill each field from the whole "
    "conversation (a short reply like '3주' continues the previous order request). "
    "Never invent the side, quantity or price; leave them null if not said."
)


class BrokerOrderTool:
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

    def tool(self) -> ExecutionToolDefinition:
        return ExecutionToolDefinition(
            name=ORDER_TOOL_NAME,
            description=ORDER_DESCRIPTION,
            arguments_schema=ORDER_ARGUMENTS_SCHEMA,
            prepare=self.prepare,
            execute=self.execute,
        )

    async def prepare(self, user_key: str, call: ToolCall) -> PreparationResult:
        if _MEMBER_ID.fullmatch(user_key) is None:
            raise ValueError("user_key is not a Broker member id")
        arguments = json.loads(call.arguments_json)
        missing = [
            key
            for key in ("name", "side", "quantity")
            if arguments.get(key) in (None, "")
        ]
        if missing:
            return PreparationResult(
                f"Order not prepared. Missing: {', '.join(missing)}. "
                "Ask the user for them; do not guess."
            )
        name = arguments["name"]
        side = arguments["side"]
        quantity = arguments["quantity"]
        order_type = arguments.get("order_type") or "LIMIT"
        price = arguments.get("price") if order_type == "LIMIT" else None
        if (
            not isinstance(name, str)
            or not isinstance(side, str)
            or side not in _SIDES
            or not _positive_int(quantity)
            or not isinstance(order_type, str)
            or order_type not in _ORDER_TYPES
            or (price is not None and not _positive_number(price))
        ):
            return PreparationResult(
                "Order not prepared. The side, quantity, or price is not valid "
                "(quantity must be a positive whole number, price a positive number). "
                "Ask the user."
            )

        found = await find_instrument(self._client, name)
        if found.code is None or found.name is None:
            return PreparationResult(f"Order not prepared. {found.problem}")
        code, official_name = found.code, found.name

        response = await self._client.get(
            f"/internal/members/{user_key}/quote", params={"code": code}
        )
        if response.status_code == 409:
            return PreparationResult(
                "Order not prepared. The user's broker account is not connected or not "
                "verified; the user must connect and verify it in Member Web first."
            )
        response.raise_for_status()
        quote = response.json()
        # The price stays the JSON number the broker sent; a float's repr is its
        # shortest form, so 233.95 reaches the confirmation and the order unchanged.
        current = quote["price"]
        currency = quote["currency"]
        at = _kst_clock(str(quote["observed_at"]))
        if currency == "USD" and order_type == "MARKET":
            return PreparationResult(
                "Order not prepared. US stocks take limit orders only. Ask the user "
                f"whether to place a limit order at the current price "
                f"{_amount(current, currency)} instead."
            )
        if currency == "KRW" and price is not None and price != int(price):
            return PreparationResult(
                "Order not prepared. A Korean stock's price is whole won. Ask the user."
            )
        if currency == "KRW" and price is not None:
            price = int(price)

        label = f"{official_name}({code}) {quantity:,}주"
        action_word = _SIDES[side]
        now = _amount(current, currency)
        if order_type == "LIMIT":
            limit = price or current
            summary = f"{label} {_amount(limit, currency)} 지정가 {action_word}"
            confirmation = (
                f"{label}를 {_amount(limit, currency)} 지정가로 {action_word}할까요? "
                f"({at} 기준 현재가 {now})"
            )
        else:
            limit = None
            summary = f"{label} 시장가 {action_word}"
            confirmation = (
                f"{label}를 시장가로 {action_word}할까요? "
                f"(예상 금액 약 {current * quantity:,}원, {at} 기준 현재가 {now})"
            )
        stored = {
            "request_id": uuid.uuid4().hex,
            "code": code,
            "side": side,
            "quantity": quantity,
            "order_type": order_type,
            "price": limit,
            "summary": summary,
        }
        return PreparationResult(
            f"Order prepared and waiting for the user's confirmation: {summary}. The "
            "confirmation question is appended to the answer automatically; do not "
            "repeat it and do not say the order was placed.",
            PreparedAction(
                summary, confirmation, json.dumps(stored, ensure_ascii=False)
            ),
        )

    async def execute(self, user_key: str, action: PreparedAction) -> str:
        stored = json.loads(action.arguments_json)
        summary = stored["summary"]
        body = {
            key: stored[key]
            for key in ("request_id", "code", "side", "quantity", "order_type")
        }
        if stored["price"] is not None:
            body["price"] = stored["price"]
        result: Any = None
        try:
            response = await self._client.post(
                f"/internal/members/{user_key}/orders", json=body
            )
            if response.status_code == 200:
                result = response.json()
        except (httpx.HTTPError, ValueError):
            result = None
        status = result.get("status") if isinstance(result, dict) else None
        if status == "ACCEPTED" and result.get("broker_order_no"):
            return (
                f"주문이 접수되었습니다: {summary} (주문번호 {result.get('broker_order_no')}). "
                "체결 여부는 증권사 앱에서 확인해 주세요."
            )
        if status == "REJECTED":
            reason = result.get("reason") or result.get("reason_code")
            return f"주문이 거부되었습니다: {summary}. 사유: {reason}"
        # Anything else may still have reached the broker: never resend.
        return (
            f"주문 결과를 확인하지 못했습니다: {summary}. 주문이 들어갔을 수 있으니 "
            "다시 주문하기 전에 증권사 앱에서 꼭 확인해 주세요."
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


def _positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


def _positive_number(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and value > 0


def _amount(value: float, currency: str) -> str:
    """285,500원 or 233.95달러: every decimal kept, never rounded here."""

    text = f"{Decimal(repr(value)).normalize():,f}"
    return f"{text}원" if currency == "KRW" else f"{text}달러"


def _kst_clock(value: str) -> str:
    moment = datetime.fromisoformat(value)
    return moment.astimezone(_KST).strftime("%H:%M")
