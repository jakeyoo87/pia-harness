from __future__ import annotations

import json
import unittest

import httpx

from pia_harness import BrokerOrderTool, PreparedAction, ToolCall

MEMBER = "member-1"
SAMSUNG = {"status": "FOUND", "code": "005930", "name": "삼성전자", "market": "KOSPI"}
QUOTE = {
    "code": "005930",
    "price": 285500,
    "currency": "KRW",
    "observed_at": "2026-09-28T00:31:05Z",
}
NVDA = {"status": "FOUND", "code": "NAS:NVDA", "name": "엔비디아", "market": "NAS"}
US_QUOTE = {
    "code": "NAS:NVDA",
    "price": 233.95,
    "currency": "USD",
    "observed_at": "2026-09-28T00:31:05Z",
}


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
            us = request.url.params.get("code") == "NAS:NVDA"
            return httpx.Response(200, json=US_QUOTE if us else QUOTE)
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
        self.assertIn("Do not say the order was placed", result.observation_text)

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
        # The model may send any JSON type; a wrong one is reported, not raised.
        for wrong in ({"side": []}, {"order_type": ["LIMIT"]}):
            with self.subTest(wrong=wrong):
                wrong_type = await self.tool.prepare(MEMBER, call(**wrong))
                self.assertIsNone(wrong_type.action)
                self.assertIn("not valid", wrong_type.observation_text)

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

    async def test_an_accepted_order_goes_to_the_host_to_watch(self) -> None:
        watched: list[tuple[str, dict]] = []

        async def on_accepted(user_key, order):
            watched.append((user_key, dict(order)))

        client = httpx.AsyncClient(
            base_url="https://broker.example",
            transport=httpx.MockTransport(self.broker.handle),
        )
        tool = BrokerOrderTool(
            base_url="https://broker.example", client=client, on_accepted=on_accepted
        )
        prepared = await tool.prepare(MEMBER, call())
        assert prepared.action is not None
        text = await tool.execute(MEMBER, prepared.action)
        self.assertTrue(text.endswith("체결되면 알려 드릴게요."), text)
        stored = json.loads(prepared.action.arguments_json)
        self.assertEqual(
            [
                (
                    MEMBER,
                    {
                        "request_id": stored["request_id"],
                        "code": "005930",
                        "name": "삼성전자",
                        "side": "BUY",
                        "quantity": 10,
                        "broker_order_no": "0000117057",
                        "ordered_at": "2026-09-28T00:31:06Z",
                    },
                )
            ],
            watched,
        )

        # The order is placed either way; a failed hook only changes the last sentence.
        async def failing(user_key, order):
            raise RuntimeError("store down")

        tool = BrokerOrderTool(
            base_url="https://broker.example", client=client, on_accepted=failing
        )
        prepared = await tool.prepare(MEMBER, call())
        assert prepared.action is not None
        with self.assertLogs("pia_harness", "WARNING") as logs:
            text = await tool.execute(MEMBER, prepared.action)
        self.assertIn("주문이 접수되었습니다", text)
        self.assertTrue(
            text.endswith(
                "체결 알림을 등록하지 못했어요. 체결 여부는 증권사 앱에서 확인해 주세요."
            ),
            text,
        )
        self.assertEqual(
            ["WARNING:pia_harness:harness.order_tracking_failed error_type=RuntimeError"],
            logs.output,
        )
        # Without a hook: the sentence as before.
        prepared = await self.tool.prepare(MEMBER, call())
        assert prepared.action is not None
        text = await self.tool.execute(MEMBER, prepared.action)
        self.assertTrue(text.endswith("체결 여부는 증권사 앱에서 확인해 주세요."), text)

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


class UsOrderTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.broker = FakeBroker()
        self.broker.search = NVDA
        client = httpx.AsyncClient(
            base_url="https://broker.example",
            transport=httpx.MockTransport(self.broker.handle),
        )
        self.tool = BrokerOrderTool(base_url="https://broker.example", client=client)

    async def confirm(self, **arguments: object) -> PreparedAction:
        result = await self.tool.prepare(
            MEMBER, call(name="엔비디아", quantity=2, **arguments)
        )
        assert result.action is not None, result.observation_text
        # The prepared action goes through storage as JSON before it runs.
        stored = PreparedAction(
            result.action.summary,
            result.action.confirmation,
            json.loads(json.dumps(result.action.arguments_json)),
        )
        await self.tool.execute(MEMBER, stored)
        return result.action

    async def test_limit_at_the_current_price_keeps_cents_end_to_end(self) -> None:
        action = await self.confirm()
        self.assertEqual(
            "엔비디아(NAS:NVDA) 2주를 233.95달러 지정가로 매수할까요? "
            "(09:31 기준 현재가 233.95달러)",
            action.confirmation,
        )
        (sent,) = self.broker.orders
        self.assertEqual(
            {"code": "NAS:NVDA", "quantity": 2, "order_type": "LIMIT", "price": 233.95},
            {key: sent[key] for key in ("code", "quantity", "order_type", "price")},
        )

    async def test_a_price_the_user_gave_is_sent_as_given(self) -> None:
        action = await self.confirm(price=230.5)
        self.assertIn("230.5달러 지정가", action.confirmation)
        self.assertEqual(230.5, self.broker.orders[0]["price"])

    async def test_a_market_order_is_a_limit_order_at_the_current_price(self) -> None:
        action = await self.confirm(order_type="MARKET")
        self.assertEqual(
            "미국 주식은 시장가 주문이 안 돼서 현재가 지정가로 준비했어요. "
            "엔비디아(NAS:NVDA) 2주를 233.95달러 지정가로 매수할까요? "
            "(09:31 기준 현재가 233.95달러)",
            action.confirmation,
        )
        (sent,) = self.broker.orders
        self.assertEqual(
            {"order_type": "LIMIT", "price": 233.95},
            {key: sent[key] for key in ("order_type", "price")},
        )

    async def test_a_korean_price_with_decimals_is_refused(self) -> None:
        self.broker.search = SAMSUNG
        result = await self.tool.prepare(MEMBER, call(price=285500.5))
        self.assertIsNone(result.action)
        self.assertIn("whole won", result.observation_text)


MMF = "488770"


class FundingBroker(FakeBroker):
    """A Korean account keeping its cash in the money-market ETF (PIA's operation)."""

    def __init__(self) -> None:
        super().__init__()
        self.buyable = 200000
        self.sellable = 8
        self.filled = 3
        self.listing: httpx.Response | None = None
        self.placed_no = iter(["0000000001", "0000000002"])

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        code = request.url.params.get("code")
        if path == f"/internal/members/{MEMBER}/account":
            if code == MMF:
                position = {"code": MMF, "name": "KODEX 머니마켓액티브", "sellable_quantity": self.sellable}
                return httpx.Response(200, json={"buyable": {}, "position": position})
            return httpx.Response(200, json={"buyable": {"amount": self.buyable}, "position": None})
        if path == f"/internal/members/{MEMBER}/quote" and code == MMF:
            return httpx.Response(200, json={"asks": [{"price": 105030}], "bids": [{"price": 105020}, {"price": 105015}]})
        if path == f"/internal/members/{MEMBER}/orders" and request.method == "GET":
            if self.listing is not None:
                return self.listing
            return httpx.Response(200, json={"orders": [{"order_no": "0000000001", "filled": self.filled}]})
        if path == f"/internal/members/{MEMBER}/orders":
            self.orders.append(json.loads(request.content))
            return httpx.Response(
                200, json={"status": "ACCEPTED", "broker_order_no": next(self.placed_no), "ordered_at": None}
            )
        return super().handle(request)


class FundingTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.broker = FundingBroker()
        self.watched: list[dict] = []
        self.pauses: list[float] = []

        async def on_accepted(user_key, order):
            self.watched.append(dict(order))

        async def sleep(seconds):
            self.pauses.append(seconds)

        client = httpx.AsyncClient(
            base_url="https://broker.example", transport=httpx.MockTransport(self.broker.handle)
        )
        self.tool = BrokerOrderTool(
            base_url="https://broker.example", client=client, on_accepted=on_accepted,
            funding_code=MMF, sleep=sleep,
        )

    async def test_a_buy_that_fits_is_one_order(self) -> None:
        self.broker.buyable = 300000
        prepared = await self.tool.prepare(MEMBER, call(quantity=1))
        assert prepared.action is not None
        self.assertNotIn("funding", json.loads(prepared.action.arguments_json))
        self.assertTrue(prepared.action.confirmation.startswith("삼성전자(005930) 1주를"))

    async def test_a_short_buy_sells_the_cash_first_in_one_confirmation(self) -> None:
        # 285,500 − 200,000 = 85,500 short: one share at the best bid 105,020.
        prepared = await self.tool.prepare(MEMBER, call(quantity=1))
        assert prepared.action is not None
        self.assertTrue(
            prepared.action.confirmation.startswith(
                "KODEX 머니마켓액티브(488770) 1주를 105,020원에 팔고, 삼성전자(005930) 1주를 285,500원 지정가로 매수할까요?"
            ),
            prepared.action.confirmation,
        )
        text = await self.tool.execute(MEMBER, prepared.action)
        sell, buy = self.broker.orders
        self.assertEqual((MMF, "SELL", 1, 105020), (sell["code"], sell["side"], sell["quantity"], sell["price"]))
        self.assertEqual(("005930", "BUY", 1, 285500), (buy["code"], buy["side"], buy["quantity"], buy["price"]))
        self.assertEqual(["0000000001", "0000000002"], [o["broker_order_no"] for o in self.watched])
        self.assertIn("\n주문이 접수되었습니다: 삼성전자(005930) 1주 285,500원 지정가 매수", text)
        # Market buys and too little to sell: one order as before.
        prepared = await self.tool.prepare(MEMBER, call(quantity=1, order_type="MARKET"))
        assert prepared.action is not None
        self.assertNotIn("funding", json.loads(prepared.action.arguments_json))
        self.broker.sellable = 0
        prepared = await self.tool.prepare(MEMBER, call(quantity=1))
        assert prepared.action is not None
        self.assertNotIn("funding", json.loads(prepared.action.arguments_json))

    async def test_no_buy_until_the_sell_has_filled(self) -> None:
        self.broker.filled = 0
        prepared = await self.tool.prepare(MEMBER, call(quantity=1))
        assert prepared.action is not None
        text = await self.tool.execute(MEMBER, prepared.action)
        (sell,) = self.broker.orders
        self.assertEqual("SELL", sell["side"])
        self.assertEqual([1.0] * 9, self.pauses)
        self.assertIn("체결이 확인되지 않아 삼성전자(005930) 1주 285,500원 지정가 매수 주문은 넣지 않았어요.", text)
        # The sale was told to the host before the checks (they may fail).
        self.assertEqual(["0000000001"], [o["broker_order_no"] for o in self.watched])
