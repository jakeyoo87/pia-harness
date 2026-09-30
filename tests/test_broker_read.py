from __future__ import annotations

import json
import unittest

import httpx

from pia_harness import BrokerReadTool, ToolCall

MEMBER = "member-1"
SAMSUNG = {"status": "FOUND", "code": "005930", "name": "삼성전자", "market": "KOSPI"}
AT = "2026-09-28T00:31:05Z"
QUOTE = {
    "code": "005930",
    "price": 68500,
    "observed_at": AT,
    "change": -1500,
    "change_rate": -2.14,
    "volume": 9876543,
    "trading_value": 676543210000,
    "market_cap": 4089300,
    "per": 12.92,
    "pbr": None,
    "high_52w": 88800,
    "low_52w": 49900,
}
ACCOUNT = {
    "positions": [
        {
            "code": "005930",
            "name": "삼성전자",
            "quantity": 10,
            "sellable_quantity": 7,
            "average_price": 58966.6667,
            "current_price": 70000,
            "valuation": 700000,
            "profit": 110333,
            "profit_rate": 18.71,
        },
        {
            "code": "000660",
            "name": "SK하이닉스",
            "quantity": 3,
            "sellable_quantity": 3,
            "average_price": 120000,
            "current_price": 115000,
            "valuation": 345000,
            "profit": -15000,
            "profit_rate": -4.17,
        },
    ],
    "cash": 1500000,
    "cash_d2": 1200000,
    "total_valuation": 2245000,
    "total_profit": 95333,
    "observed_at": AT,
}
BUYABLE = {
    "buyable": {
        "code": "005930",
        "amount": 1180000,
        "quantity": 12,
        "unit_price": 91000,
        "observed_at": AT,
    }
}


class FakeBroker:
    def __init__(self) -> None:
        self.search: dict = SAMSUNG
        self.status: dict = {
            "connection": {
                "broker": "KIS",
                "lifecycle_status": "PENDING",
                "verification_status": "VERIFIED",
                "verification_reason": None,
            }
        }
        self.account: dict = ACCOUNT
        self.reply_status = 200
        self.requests: list[httpx.Request] = []
        self.fail: Exception | None = None

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.fail is not None:
            raise self.fail
        path = request.url.path
        if path == "/internal/instruments":
            return httpx.Response(200, json=self.search)
        if self.reply_status != 200:
            return httpx.Response(self.reply_status, json={"error": {"code": "X"}})
        if path == f"/internal/members/{MEMBER}/broker-status":
            return httpx.Response(200, json=self.status)
        if path == f"/internal/members/{MEMBER}/quote":
            return httpx.Response(200, json=QUOTE)
        if path == f"/internal/members/{MEMBER}/account":
            if request.url.params.get("code"):
                return httpx.Response(200, json=BUYABLE)
            return httpx.Response(200, json=self.account)
        return httpx.Response(404)


def call(action: object, name: object = None) -> ToolCall:
    return ToolCall("broker", json.dumps({"action": action, "name": name}, ensure_ascii=False))


class BrokerReadToolTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.broker = FakeBroker()
        client = httpx.AsyncClient(
            base_url="https://broker.example",
            transport=httpx.MockTransport(self.broker.handle),
        )
        self.tool = BrokerReadTool(base_url="https://broker.example", client=client)

    async def lookup(self, action: object, name: object = None) -> str:
        result = await self.tool.execute(MEMBER, call(action, name), ())
        self.assertEqual((), result.links)
        return result.observation_text

    async def test_definition(self) -> None:
        definition = self.tool.tool()
        self.assertEqual("broker", definition.name)
        self.assertEqual(
            ["status", "quote", "account", "buyable"],
            definition.arguments_schema["properties"]["action"]["enum"],
        )
        self.assertIn("Never work out a buyable quantity", definition.description)

    async def test_quote_keeps_a_fall_negative(self) -> None:
        text = await self.lookup("quote", "삼성전자")
        self.assertEqual(
            "Quote for 삼성전자(005930) at 2026-09-28 09:31 KST: price 68,500 KRW; "
            "change from the previous close -1,500 KRW (-2.14%); volume 9,876,543 shares; "
            "trading value 676,543,210,000 KRW; market cap 4,089,300 hundred million KRW "
            "(억 원); PER 12.92; 52-week high 88,800 KRW; 52-week low 49,900 KRW.",
            text,
        )
        quote = self.broker.requests[-1]
        self.assertEqual("005930", quote.url.params["code"])

    async def test_account_lists_holdings_and_totals(self) -> None:
        text = await self.lookup("account")
        self.assertIn("Account at 2026-09-28 09:31 KST", text)
        self.assertIn(
            "Totals: cash (deposit) 1,500,000 KRW; D+2 cash 1,200,000 KRW; total valuation "
            "2,245,000 KRW; total profit +95,333 KRW.",
            text,
        )
        self.assertIn(
            "- 삼성전자(005930): quantity 10 shares; sellable now 7 shares; average price "
            "58,966.67 KRW; current price 70,000 KRW; valuation 700,000 KRW; profit "
            "+110,333 KRW; return +18.71%",
            text,
        )
        self.assertIn("profit -15,000 KRW; return -4.17%", text)
        self.assertIn("use action buyable", text)
        # One request, no stock lookup.
        self.assertEqual(1, len(self.broker.requests))

    async def test_empty_account(self) -> None:
        self.broker.account = {**ACCOUNT, "positions": [], "total_profit": None}
        text = await self.lookup("account")
        self.assertIn("Holdings: none.", text)
        self.assertNotIn("total profit", text)

    async def test_buyable_reports_the_brokers_figures(self) -> None:
        text = await self.lookup("buyable", "삼성전자")
        self.assertIn(
            "Buyable for 삼성전자(005930) at 2026-09-28 09:31 KST, as calculated by the "
            "broker without margin on a market-order basis (unit price used 91,000 KRW): "
            "up to 12 shares, amount 1,180,000 KRW.",
            text,
        )
        request = self.broker.requests[-1]
        self.assertEqual(f"/internal/members/{MEMBER}/account", request.url.path)
        self.assertEqual("005930", request.url.params["code"])

    async def test_status(self) -> None:
        self.assertIn("connected and verified", await self.lookup("status"))
        self.broker.status = {"connection": None}
        self.assertIn("Brokerage connection: none", await self.lookup("status"))
        self.broker.status = {
            "connection": {
                "broker": "KIS",
                "lifecycle_status": "PENDING",
                "verification_status": "FAILED",
                "verification_reason": "CREDENTIAL_INVALID",
            }
        }
        text = await self.lookup("status")
        self.assertIn("verification FAILED, reason CREDENTIAL_INVALID", text)
        self.assertIn("only through a verified KIS connection", text)

    async def test_not_connected_does_not_claim_no_connection(self) -> None:
        self.broker.reply_status = 409
        for action, name in (("quote", "삼성전자"), ("account", None), ("buyable", "삼성전자")):
            with self.subTest(action=action):
                text = await self.lookup(action, name)
                self.assertIn("no verified KIS account connection", text)
                self.assertIn("another broker does not count", text)

    async def test_failures_become_sentences(self) -> None:
        self.broker.reply_status = 503
        self.assertIn("temporarily unavailable", await self.lookup("account"))
        self.broker.reply_status = 502
        self.assertIn("status 502", await self.lookup("quote", "삼성전자"))
        self.broker.reply_status = 200
        self.broker.fail = httpx.ConnectError("down")
        self.assertIn("no response", await self.lookup("status"))

    async def test_invalid_arguments_and_stock_names(self) -> None:
        self.assertIn("action must be one of", await self.lookup("orders"))
        self.assertIn("needs the stock name", await self.lookup("quote"))
        self.assertIn("needs the stock name", await self.lookup("buyable", " "))
        self.broker.search = {
            "status": "AMBIGUOUS",
            "candidates": [
                {"code": "005930", "name": "삼성전자", "market": "KOSPI"},
                {"code": "005935", "name": "삼성전자우", "market": "KOSPI"},
            ],
        }
        self.assertIn("삼성전자우(005935)", await self.lookup("quote", "삼성"))
        self.broker.search = {"status": "NOT_FOUND"}
        self.assertIn("official listed name", await self.lookup("buyable", "없는회사"))

    async def test_no_account_number_or_credentials_in_results(self) -> None:
        for action, name in (("status", None), ("account", None), ("buyable", "삼성전자")):
            text = await self.lookup(action, name)
            self.assertNotRegex(text, r"\d{8}-?\d{2}")


if __name__ == "__main__":
    unittest.main()
