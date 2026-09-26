from __future__ import annotations

import json
import unittest

import httpx

from pia_harness import BrokerOrderTool, PreparedAction, ToolCall

MEMBER = "member-1"
SAMSUNG = {"status": "FOUND", "code": "005930", "name": "삼성전자", "market": "KOSPI"}
QUOTE = {"code": "005930", "price": 285500, "observed_at": "2026-09-28T00:31:05Z"}


class FakeBroker:
    def __init__(self) -> None:
        self.search = SAMSUNG
        self.quote_status = 200
        self.order_reply: httpx.Response | Exception = httpx.Response(
            200,
            json={
                "status": "ACCEPTED",
                "order_id": "x",
                "broker_order_no": "0000117057",
                "ordered_at": "2026-09-28T00:31:06Z",
            },
        )
        self.orders: list[dict] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/internal/instruments":
            return httpx.Response(200, json=self.search)
        if path == f"/internal/members/{MEMBER}/quote":
            if self.quote_status != 200:
                return httpx.Response(
                    self.quote_status, json={"error": {"code": "BROKER_NOT_CONNECTED"}}
                )
            return httpx.Response(200, json=QUOTE)
        if path == f"/internal/members/{MEMBER}/orders":
            self.orders.append(json.loads(request.content))
            if isinstance(self.order_reply, Exception):
                raise self.order_reply
            return self.order_reply
        return httpx.Response(404)


def call(**arguments: object) -> ToolCall:
    values = {
        "name": "삼성전자",
        "side": "BUY",
        "quantity": 10,
        "order_type": "LIMIT",
        "price": None,
    }
    values.update(arguments)
    return ToolCall("order", json.dumps(values, ensure_ascii=False))


class BrokerOrderToolTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.broker = FakeBroker()
        client = httpx.AsyncClient(
            base_url="https://broker.example",
            transport=httpx.MockTransport(self.broker.handle),
        )
        self.tool = BrokerOrderTool(base_url="https://broker.example", client=client)

    async def test_limit_order_defaults_to_the_current_price(self) -> None:
        result = await self.tool.prepare(MEMBER, call())
        assert result.action is not None
        self.assertEqual(
            "삼성전자(005930) 10주를 285,500원 지정가로 매수할까요? "
            "(09:31 기준 현재가 285,500원)",
            result.action.confirmation,
        )
        self.assertIn("do not say the order was placed", result.observation_text)

    async def test_market_order_shows_the_estimated_amount(self) -> None:
        result = await self.tool.prepare(MEMBER, call(order_type="MARKET", side="SELL"))
        assert result.action is not None
        self.assertIn("시장가로 매도할까요?", result.action.confirmation)
        self.assertIn("예상 금액 약 2,855,000원", result.action.confirmation)

    async def test_missing_invalid_ambiguous_and_unknown_prepare_nothing(self) -> None:
        missing = await self.tool.prepare(MEMBER, call(quantity=None))
        self.assertIsNone(missing.action)
        self.assertIn("Missing: quantity", missing.observation_text)
        invalid = await self.tool.prepare(MEMBER, call(quantity=0))
        self.assertIsNone(invalid.action)

        self.broker.search = {
            "status": "AMBIGUOUS",
            "candidates": [
                {"code": "005930", "name": "삼성전자", "market": "KOSPI"},
                {"code": "005935", "name": "삼성전자우", "market": "KOSPI"},
            ],
        }
        ambiguous = await self.tool.prepare(MEMBER, call(name="삼성"))
        self.assertIsNone(ambiguous.action)
        self.assertIn("삼성전자우(005935)", ambiguous.observation_text)

        self.broker.search = {"status": "NOT_FOUND"}
        unknown = await self.tool.prepare(MEMBER, call(name="없는회사"))
        self.assertIsNone(unknown.action)
        self.assertIn("official listed name", unknown.observation_text)

    async def test_unconnected_account_is_explained(self) -> None:
        self.broker.quote_status = 409
        result = await self.tool.prepare(MEMBER, call())
        self.assertIsNone(result.action)
        self.assertIn("Member Web", result.observation_text)

    async def test_execute_sends_the_stored_order_once(self) -> None:
        prepared = await self.tool.prepare(MEMBER, call())
        assert prepared.action is not None
        text = await self.tool.execute(MEMBER, prepared.action)
        self.assertIn("주문이 접수되었습니다", text)
        self.assertIn("0000117057", text)
        (sent,) = self.broker.orders
        stored = json.loads(prepared.action.arguments_json)
        self.assertEqual(
            {
                "request_id": stored["request_id"],
                "code": "005930",
                "side": "BUY",
                "quantity": 10,
                "order_type": "LIMIT",
                "price": 285500,
            },
            sent,
        )

    async def test_market_order_sends_no_price(self) -> None:
        prepared = await self.tool.prepare(MEMBER, call(order_type="MARKET", price=1))
        assert prepared.action is not None
        await self.tool.execute(MEMBER, prepared.action)
        self.assertNotIn("price", self.broker.orders[0])

    async def test_rejection_and_unknown_results(self) -> None:
        action = PreparedAction(
            "삼성전자(005930) 10주 285,500원 지정가 매수",
            "q",
            json.dumps(
                {
                    "request_id": "r1",
                    "code": "005930",
                    "side": "BUY",
                    "quantity": 10,
                    "order_type": "LIMIT",
                    "price": 285500,
                    "summary": "삼성전자(005930) 10주 285,500원 지정가 매수",
                },
                ensure_ascii=False,
            ),
        )
        self.broker.order_reply = httpx.Response(
            200,
            json={
                "status": "REJECTED",
                "order_id": "r1",
                "reason_code": "APBK0919",
                "reason": "잔고 부족",
            },
        )
        self.assertIn("사유: 잔고 부족", await self.tool.execute(MEMBER, action))
        for reply in (
            httpx.Response(200, json={"status": "UNKNOWN", "order_id": "r1"}),
            httpx.Response(200, json={"status": "ACCEPTED", "order_id": "r1"}),
            httpx.Response(502),
            httpx.ReadTimeout("timeout"),
        ):
            self.broker.order_reply = reply
            text = await self.tool.execute(MEMBER, action)
            self.assertIn("증권사 앱에서 꼭 확인", text)


if __name__ == "__main__":
    unittest.main()
