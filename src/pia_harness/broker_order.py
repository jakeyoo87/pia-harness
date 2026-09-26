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
from typing import Any

import httpx

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
            "description": "Official listed stock name. Convert nicknames and "
            "abbreviations (삼전 -> 삼성전자, 하닉 -> SK하이닉스). Use the 6-digit code "
            "only if the user gave a code. Null only if no stock was named.",
        },
        "side": {"type": ["string", "null"], "enum": ["BUY", "SELL", None]},
        "quantity": {"type": ["integer", "null"], "description": "Number of shares."},
        "order_type": {
            "type": ["string", "null"],
            "enum": ["LIMIT", "MARKET", None],
            "description": "LIMIT unless the user explicitly asks for a market order.",
        },
        "price": {
            "type": ["integer", "null"],
            "description": "Limit price in KRW only if the user gave one.",
        },
    },
    "required": ["name", "side", "quantity", "order_type", "price"],
    "additionalProperties": False,
}
ORDER_DESCRIPTION = (
    "Prepare a Korean stock buy or sell order for the user's confirmation; nothing "
    "is executed yet. Choose when the user asks to buy or sell a stock, or answers a "
    "question about an order being drafted. Fill each field from the whole "
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

    def definition(self) -> ExecutionToolDefinition:
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
            or side not in _SIDES
            or not _positive_int(quantity)
            or order_type not in _ORDER_TYPES
            or (price is not None and not _positive_int(price))
        ):
            return PreparationResult(
                "Order not prepared. The side, quantity, or price is not valid "
                "(quantity and price must be positive whole numbers). Ask the user."
            )

        found = await self._get_json("/internal/instruments", {"query": name})
        status = found.get("status")
        if status == "AMBIGUOUS":
            candidates = ", ".join(
                f"{item['name']}({item['code']})" for item in found["candidates"]
            )
            return PreparationResult(
                f"Order not prepared. '{name}' matches several stocks: {candidates}. "
                "Ask the user which one, listing these names in the answer."
            )
        if status != "FOUND":
            return PreparationResult(
                f"Order not prepared. No listed stock matches '{name}'. Ask the "
                "user for the official listed name."
            )
        code, official_name = str(found["code"]), str(found["name"])

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
        current = int(quote["price"])
        at = _kst_clock(str(quote["observed_at"]))

        label = f"{official_name}({code}) {quantity:,}주"
        action_word = _SIDES[side]
        if order_type == "LIMIT":
            limit = price or current
            summary = f"{label} {limit:,}원 지정가 {action_word}"
            confirmation = (
                f"{label}를 {limit:,}원 지정가로 {action_word}할까요? "
                f"({at} 기준 현재가 {current:,}원)"
            )
        else:
            limit = None
            summary = f"{label} 시장가 {action_word}"
            confirmation = (
                f"{label}를 시장가로 {action_word}할까요? "
                f"(예상 금액 약 {current * quantity:,}원, {at} 기준 현재가 {current:,}원)"
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

    async def _get_json(self, path: str, params: dict[str, str]) -> dict[str, Any]:
        response = await self._client.get(path, params=params)
        response.raise_for_status()
        value = response.json()
        if not isinstance(value, dict):
            raise TypeError("Broker response must be an object")
        return value

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


def _positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


def _kst_clock(value: str) -> str:
    moment = datetime.fromisoformat(value)
    return moment.astimezone(_KST).strftime("%H:%M")
