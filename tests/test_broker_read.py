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


def ranking_row(code: str, change: int, **figures: object) -> dict:
    return {
        "code": code,
        "name": None if code == "000000" else f"종목{code}",
        "price": 70000,
        "change": change,
        "change_rate": -2.1 if change < 0 else 2.1,
        "volume": 1000,
        "figures": figures,
    }


RANKING = {
    "by": "losers",
    "market": "kosdaq",
    "observed_at": AT,
    "rows": [ranking_row(f"{n:06d}", -1500) for n in range(1, 13)],
}
STOCK_INVESTORS = {
    "code": "005930",
    "observed_at": AT,
    "rows": [
        {
            "label": f"202610{day:02d}",
            "close": 68500,
            "change": -1500,
            "flows": {
                "individual_net_volume": 250000,
                "individual_net_value": 17125,
                "foreign_net_volume": -300000,
                "foreign_net_value": -20550,
                "institution_net_volume": 50000,
                "institution_net_value": 3425,
            },
        }
        for day in (2, 1)
    ],
}
MARKET_INVESTORS = {
    "code": None,
    "observed_at": AT,
    "rows": [
        {
            "label": market,
            "close": None,
            "change": None,
            "flows": {"pension_net_value": 30},
        }
        for market in ("kospi", "kosdaq")
    ],
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
        self.ranking: dict = RANKING
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
        if path == f"/internal/members/{MEMBER}/ranking":
            return httpx.Response(200, json=self.ranking)
        if path == f"/internal/members/{MEMBER}/investors":
            if request.url.params.get("code"):
                return httpx.Response(200, json=STOCK_INVESTORS)
            return httpx.Response(200, json=MARKET_INVESTORS)
        if path == f"/internal/members/{MEMBER}/account":
            if request.url.params.get("code"):
                return httpx.Response(200, json=BUYABLE)
            return httpx.Response(200, json=self.account)
        return httpx.Response(404)


def call(action: object, name: object = None, **arguments: object) -> ToolCall:
    payload = {"action": action, "name": name, **arguments}
    return ToolCall("broker", json.dumps(payload, ensure_ascii=False))


class BrokerReadToolTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.broker = FakeBroker()
        client = httpx.AsyncClient(
            base_url="https://broker.example",
            transport=httpx.MockTransport(self.broker.handle),
        )
        self.tool = BrokerReadTool(base_url="https://broker.example", client=client)

    async def lookup(
        self, action: object, name: object = None, **arguments: object
    ) -> str:
        result = await self.tool.execute(MEMBER, call(action, name, **arguments), ())
        self.assertEqual((), result.links)
        return result.observation_text

    async def test_definition(self) -> None:
        definition = self.tool.tool()
        self.assertEqual("broker", definition.name)
        self.assertEqual(
            ["status", "quote", "account", "buyable", "ranking", "investors"],
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
            "Totals: cash (deposit) 1,500,000 KRW; total valuation "
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
        self.assertEqual(
            "Buyable for 삼성전자(005930) at 2026-09-28 09:31 KST, as calculated by the "
            "broker without margin on a market-order basis, unit price used by the broker "
            "91,000 KRW: up to 12 shares, amount 1,180,000 KRW.",
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
        for action, name in (
            ("quote", "삼성전자"),
            ("account", None),
            ("buyable", "삼성전자"),
        ):
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
        for action, name in (
            ("status", None),
            ("account", None),
            ("buyable", "삼성전자"),
        ):
            text = await self.lookup(action, name)
            self.assertNotRegex(text, r"\d{8}-?\d{2}")

    async def test_ranking_shows_the_basis_and_the_asked_number_of_rows(self) -> None:
        text = await self.lookup("ranking", by="losers", market="kosdaq")
        lines = text.splitlines()
        self.assertEqual(
            "Ranking: top losers by change rate, market kosdaq, at 2026-09-28 09:31 KST, "
            "in the broker's order.",
            lines[0],
        )
        self.assertEqual(
            "1. 종목000001(000001): price 70,000 KRW; change -1,500 KRW (-2.10%); "
            "volume 1,000 shares",
            lines[1],
        )
        self.assertEqual(11, len(lines))  # the default 10 rows
        request = self.broker.requests[-1]
        self.assertEqual({"by": "losers", "market": "kosdaq"}, dict(request.url.params))
        text = await self.lookup("ranking", by="losers", count=20)
        self.assertIn("12. 종목000012(000012)", text)
        self.assertIn("The broker returned 12 stocks in this reply.", text)
        self.assertEqual("all", self.broker.requests[-1].url.params["market"])

    async def test_ranking_basis_by_kind(self) -> None:
        self.broker.ranking = {
            "by": "short_selling",
            "market": "all",
            "period": "1w",
            "basis_dates": ["20260924", "20261001"],
            "observed_at": AT,
            "rows": [
                ranking_row(
                    "005930", 1500, short_value=8400000000, short_value_share=None
                )
            ],
        }
        text = await self.lookup("ranking", by="short_selling", period="1w")
        self.assertIn("period 1w, KIS dates 2026-09-24–2026-10-01", text)
        self.assertIn("short-sold value 8,400,000,000", text)
        self.assertNotIn("short share of value", text)
        self.assertEqual("1w", self.broker.requests[-1].url.params["period"])
        self.broker.ranking = {
            "by": "per",
            "market": "all",
            "fiscal_year": 2025,
            "observed_at": AT,
            "rows": [ranking_row("005930", 0, per=13.2)],
        }
        text = await self.lookup("ranking", by="per", period="1w")
        self.assertIn("annual results for fiscal year 2025", text)
        self.assertNotIn("period", self.broker.requests[-1].url.params)
        self.broker.ranking = {
            **RANKING,
            "market": "all",
            "rows": [ranking_row("000000", 0)],
        }
        text = await self.lookup("ranking", by="most_viewed", market="kospi")
        self.assertIn("whole market (KIS offers no market choice", text)
        self.assertIn("1. 000000(000000)", text)
        self.broker.ranking = {
            **RANKING,
            "rows": [
                ranking_row(
                    "005930", 1500, net_buy_value=-10500, net_buy_volume=-150000
                )
            ],
        }
        text = await self.lookup("ranking", by="foreign_selling")
        self.assertIn("provisional intraday tally", text)
        self.assertIn("net buying value -10,500; net buying volume -150,000", text)

    async def test_investors_for_one_stock_or_the_market(self) -> None:
        text = await self.lookup("investors", "삼성전자", count=1)
        self.assertIn(
            "Net buying in 삼성전자(005930) by investor group, by trading day", text
        )
        self.assertIn(
            "- 2026-10-02: close 68,500 KRW; change -1,500 KRW; individuals volume +250,000, "
            "value +17,125; foreigners volume -300,000, value -20,550; institutions volume "
            "+50,000, value +3,425",
            text,
        )
        self.assertNotIn("2026-10-01", text)
        self.assertEqual("005930", self.broker.requests[-1].url.params["code"])
        text = await self.lookup("investors", market="kosdaq")
        self.assertIn("- kospi: pension funds value +30", text)
        self.assertEqual(
            {"market": "kosdaq"}, dict(self.broker.requests[-1].url.params)
        )

    async def test_ranking_and_investor_arguments(self) -> None:
        self.assertIn("ranking needs by", await self.lookup("ranking"))
        self.assertIn("ranking needs by", await self.lookup("ranking", by="dividend"))
        for by in (["gainers"], {"value": "gainers"}):
            with self.subTest(by=by):
                self.assertIn("ranking needs by", await self.lookup("ranking", by=by))
        self.assertIn("market must be", await self.lookup("investors", market="nxt"))
        for count in (0, 1.5, True, "10"):
            with self.subTest(count=count):
                text = await self.lookup("ranking", by="gainers", count=count)
                self.assertIn("count must be", text)
        self.assertIn(
            "period must be",
            await self.lookup("ranking", by="short_selling", period="5d"),
        )
        self.assertIn(
            "market must be", await self.lookup("ranking", by="gainers", market="nxt")
        )
        self.assertIn(
            "count must be", await self.lookup("investors", "삼성전자", count=0)
        )
        self.assertEqual(0, len(self.broker.requests))
        # Arguments an action does not use are ignored.
        text = await self.lookup("quote", "삼성전자", by="dividend", count=0)
        self.assertIn("Quote for 삼성전자(005930)", text)
        unused = (
            ("ranking", None, {"by": "market_cap", "period": "5d"}),
            ("ranking", None, {"by": "most_viewed", "market": "nxt"}),
            ("investors", "삼성전자", {"market": "nxt"}),
            ("investors", None, {"count": 0}),
        )
        for action, name, arguments in unused:
            with self.subTest(action=action, arguments=arguments):
                text = await self.lookup(action, name, **arguments)
                self.assertNotIn("Broker lookup not run", text)
                params = self.broker.requests[-1].url.params
                self.assertNotIn("nxt", params.values())
                self.assertNotIn("5d", params.values())
        self.broker.reply_status = 409
        self.assertIn(
            "no verified KIS account connection",
            await self.lookup("ranking", by="gainers"),
        )


if __name__ == "__main__":
    unittest.main()
