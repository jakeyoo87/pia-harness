from __future__ import annotations

import json
import unittest

import httpx

from pia_harness import BrokerReadTool, ToolCall
from pia_harness.broker_read import _EOK, _MILLION, _WON, _market_amount

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
STOCK_ACCOUNT = {
    "buyable": {
        "code": "005930",
        "amount": 1180000,
        "quantity": 12,
        "unit_price": 91000,
        "observed_at": AT,
    },
    "position": ACCOUNT["positions"][0],
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
FLOWS = {
    "individual_net_volume": 250000,
    "individual_net_value": 17125,
    "foreign_net_volume": -300000,
    "foreign_net_value": -20550,
    "institution_net_volume": 50000,
    "institution_net_value": 3425,
}


SERIES = {
    ("macro", "usdkrw"): ("USD/KRW", "KRW per USD", 1384.5512),
    ("macro", "us10y"): ("US 10-year Treasury yield", "%", 4.1234),
    ("commodity", "gold"): ("Gold (COMEX)", "USD per troy ounce", 2650.4),
    ("market", "spx"): ("S&P 500", "points", 6012.25),
}


def series_of(params: httpx.QueryParams) -> tuple[str, str] | None:
    for kind in ("market", "macro", "commodity"):
        key = params.get(kind)
        if key is not None and (kind, key) in SERIES:
            return kind, key
    return None


def series_quote(kind: str, key: str) -> dict:
    name, unit, price = SERIES[(kind, key)]
    return {
        kind: key,
        "name": name,
        "unit": unit,
        "price": price,
        "change": -0.031,
        "change_rate": -0.75,
        "observed_at": AT,
    }


def history(request: httpx.Request) -> dict:
    params = request.url.params
    stock = "code" in params
    series = series_of(params)
    if series is not None:
        name, unit, price = SERIES[series]
        return {
            "data": "prices",
            "code": None,
            "market": None,
            series[0]: series[1],
            "name": name,
            "price_unit": unit,
            "period": params["period"],
            "unit": "day",
            "observed_at": AT,
            "rows": [
                {
                    "date": day,
                    "open": price,
                    "high": price + 1.25,
                    "low": price - 0.5,
                    "close": price,
                    "volume": None,
                    "trading_value": None,
                }
                for day in ("2026-09-25", "2026-09-24")
            ],
        }
    if params["data"] == "prices":
        rows = [
            {
                "date": day,
                "open": 70000 if stock else 2612.5,
                "high": 71000 if stock else 2630.1,
                "low": 69000 if stock else 2600.25,
                "close": 70500 if stock else 2621.07,
                # A market counts thousands of shares and millions of KRW.
                "volume": 1234567 if stock else 412345,
                "trading_value": 87037037000 if stock else 15234567,
            }
            for day in ("2026-09-25", "2026-09-24")
        ]
        unit = "week" if params["period"] in ("6m", "1y") else "day"
    else:
        rows = [
            {
                "date": "2026-09-25",
                "close": 68500 if stock else 812.34,
                "change": -1500 if stock else 3.21,
                "flows": FLOWS,
            }
        ]
        unit = "day"
    return {
        "data": params["data"],
        "code": params.get("code"),
        "market": params.get("market"),
        "period": params["period"],
        "unit": unit,
        "observed_at": AT,
        "rows": rows,
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
        self.stock_account: dict = STOCK_ACCOUNT
        self.ranking: dict = RANKING
        self.reply_status = 200
        self.not_connected = False
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
        if self.not_connected:
            return httpx.Response(409, json={"error": {"code": "BROKER_NOT_CONNECTED"}})
        if path == f"/internal/members/{MEMBER}/quote":
            series = series_of(request.url.params)
            if series is not None:
                return httpx.Response(200, json=series_quote(*series))
            return httpx.Response(200, json=QUOTE)
        if path == f"/internal/members/{MEMBER}/ranking":
            return httpx.Response(200, json=self.ranking)
        if path == f"/internal/members/{MEMBER}/history":
            return httpx.Response(200, json=history(request))
        if path == f"/internal/members/{MEMBER}/account":
            if request.url.params.get("code"):
                return httpx.Response(200, json=self.stock_account)
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

    def params(self) -> dict[str, str]:
        return dict(self.broker.requests[-1].url.params)

    async def test_definition(self) -> None:
        definition = self.tool.tool()
        self.assertEqual("broker", definition.name)
        properties = definition.arguments_schema["properties"]
        self.assertEqual(
            ["account", "quote", "history", "ranking"], properties["action"]["enum"]
        )
        self.assertEqual(
            ["prices", "investors", "short_selling", "attention", None],
            properties["data"]["enum"],
        )
        self.assertIn("use account with name", definition.description)

    async def test_quote_gives_market_amounts_in_market_amount(self) -> None:
        text = await self.lookup("quote", "삼성전자")
        self.assertEqual(
            "Quote for 삼성전자(005930), looked up at 2026-09-28 09:31 KST: price "
            "68,500 KRW; change from the previous close -1,500 KRW (-2.14%); volume "
            "9,876,543 shares; trading value 6,765.43억 원; market cap 408.93조 원; "
            "PER 12.92; 52-week high 88,800 KRW; 52-week low 49,900 KRW. These are the "
            "latest session's figures as of the lookup time; the reply does not say "
            "which trading day. Say they are as of the lookup time. Do not call them "
            "today's figures or name a trading day, even if the question says today; "
            "take a date only from another result that carries one.",
            text,
        )
        self.assertEqual({"code": "005930"}, self.params())

    async def test_account_lists_cash_holdings_and_totals(self) -> None:
        text = await self.lookup("account")
        lines = text.splitlines()
        self.assertEqual(
            "Account at 2026-09-28 09:31 KST, as reported by the broker (a verified KIS "
            "connection). Amounts in KRW.",
            lines[0],
        )
        self.assertEqual(
            "Cash: deposit 1,500,000 KRW; D+2 deposit 1,200,000 KRW. Cash is not what "
            "can be spent on one stock; use account with name for that.",
            lines[1],
        )
        self.assertEqual(
            "Totals: total valuation 2,245,000 KRW; total profit +95,333 KRW.", lines[2]
        )
        self.assertEqual(
            "- 삼성전자(005930): quantity 10 shares; sellable now 7 shares; average price "
            "58,966.67 KRW; current price 70,000 KRW; valuation 700,000 KRW; profit "
            "+110,333 KRW; return +18.71%",
            lines[4],
        )
        self.assertIn("profit -15,000 KRW; return -4.17%", lines[5])
        self.assertEqual(1, len(self.broker.requests))
        self.broker.account = {**ACCOUNT, "positions": [], "cash_d2": None}
        text = await self.lookup("account")
        self.assertIn("Cash: deposit 1,500,000 KRW. ", text)
        self.assertIn("Holdings: none.", text)

    async def test_account_with_a_name_is_that_stock_in_the_account(self) -> None:
        text = await self.lookup("account", "삼성전자")
        self.assertEqual(
            "삼성전자(005930) in the user's account at 2026-09-28 09:31 KST, as reported "
            "by the broker. Amounts in KRW.\n"
            "Held: quantity 10 shares; sellable now 7 shares; average price 58,966.67 "
            "KRW; current price 70,000 KRW; valuation 700,000 KRW; profit +110,333 KRW; "
            "return +18.71%.\n"
            "Buyable now, as calculated by the broker without margin for a limit order "
            "at the current price (unit price used by the broker 91,000 KRW): up to 12 "
            "shares, amount "
            "1,180,000 KRW.",
            text,
        )
        self.assertEqual({"code": "005930"}, self.params())
        self.broker.stock_account = {**STOCK_ACCOUNT, "position": None}
        self.assertIn("Held: none.", await self.lookup("account", "삼성전자"))

    async def test_account_without_a_verified_connection_says_which_state(self) -> None:
        self.broker.not_connected = True
        cases = (
            ({"connection": None}, "Brokerage connection: none"),
            (
                {
                    "connection": {
                        "broker": "NH",
                        "lifecycle_status": "PENDING",
                        "verification_status": "VERIFIED",
                        "verification_reason": None,
                    }
                },
                "a NH account connection exists",
            ),
            (
                {
                    "connection": {
                        "broker": "KIS",
                        "lifecycle_status": "PENDING",
                        "verification_status": "FAILED",
                        "verification_reason": "CREDENTIAL_INVALID",
                    }
                },
                "verification FAILED, reason CREDENTIAL_INVALID",
            ),
        )
        for status, expected in cases:
            self.broker.status = status
            for name in (None, "삼성전자"):
                with self.subTest(expected=expected, name=name):
                    self.assertIn(expected, await self.lookup("account", name))
        text = await self.lookup("quote", "삼성전자")
        self.assertIn("no verified KIS account connection", text)
        self.assertIn("another broker does not count", text)

    async def test_failures_become_sentences(self) -> None:
        self.broker.reply_status = 503
        self.assertIn("temporarily unavailable", await self.lookup("account"))
        self.broker.reply_status = 502
        self.assertIn("status 502", await self.lookup("quote", "삼성전자"))
        self.broker.reply_status = 200
        self.broker.fail = httpx.ConnectError("down")
        self.assertIn("no response", await self.lookup("ranking"))

    async def test_stock_names(self) -> None:
        self.broker.search = {
            "status": "AMBIGUOUS",
            "candidates": [
                {"code": "005930", "name": "삼성전자", "market": "KOSPI"},
                {"code": "005935", "name": "삼성전자우", "market": "KOSPI"},
            ],
        }
        self.assertIn("삼성전자우(005935)", await self.lookup("quote", "삼성"))
        self.broker.search = {"status": "NOT_FOUND"}
        self.assertIn("official listed name", await self.lookup("history", "없는회사"))

    async def test_no_account_number_or_credentials_in_results(self) -> None:
        for name in (None, "삼성전자"):
            text = await self.lookup("account", name)
            self.assertNotRegex(text, r"\d{8}-?\d{2}")

    async def test_history_of_stock_prices_oldest_first(self) -> None:
        text = await self.lookup("history", "삼성전자", period="6m", market="all")
        lines = text.splitlines()
        self.assertEqual(
            "Prices of 삼성전자(005930) over the last 6 months, weekly bars dated as KIS "
            "dates them; the first and last may cover part of a week, oldest first, 2 "
            "rows, looked up at 2026-09-28 09:31 KST. Split-adjusted prices in KRW; "
            "volume in shares; trading value in 억 원. The newest row may cover a "
            "session, week or month still in progress. If the user asked for daily rows "
            "and these are not daily, say so.",
            lines[0],
        )
        self.assertEqual(
            "- 2026-09-24: open 70,000; high 71,000; low 69,000; close 70,500; volume "
            "1,234,567 shares; trading value 870.37억 원",
            lines[1],
        )
        # The name decides; the market the question did not need is not sent.
        self.assertEqual(
            {"data": "prices", "period": "6m", "code": "005930"}, self.params()
        )

    async def test_history_of_a_market_index(self) -> None:
        text = await self.lookup("history", market="kospi")
        self.assertIn(
            "Prices of the KOSPI market over the last 1 month, daily rows", text
        )
        self.assertIn("Index levels in points", text)
        self.assertIn(
            "- 2026-09-25: open 2,612.50; high 2,630.10; low 2,600.25; close 2,621.07; "
            "volume 412,345,000 shares; trading value 152,345.67억 원",
            text,
        )
        self.assertEqual(
            {"data": "prices", "period": "1m", "market": "kospi"}, self.params()
        )

    async def test_history_of_investors(self) -> None:
        text = await self.lookup("history", "삼성전자", data="investors")
        self.assertIn(
            "Net buying by investor group in 삼성전자(005930) over the last 1 month",
            text,
        )
        self.assertIn(
            "- 2026-09-25: close 68,500 (-1,500); individuals +250,000 shares, "
            "+171.25억 원; foreigners -300,000 shares, -205.5억 원; institutions "
            "+50,000 shares, +34.25억 원",
            text,
        )
        text = await self.lookup("history", data="investors", market="kosdaq")
        self.assertIn("in the KOSDAQ market", text)
        self.assertIn("close is the index level in points", text)
        # The market figures count volume in thousands of shares.
        self.assertIn("close 812.34 (+3.21); individuals +250,000,000 shares", text)

    async def test_ranking(self) -> None:
        text = await self.lookup("ranking", data="prices", by="losers", market="kosdaq")
        lines = text.splitlines()
        self.assertEqual(
            "Ranking: top losers by change rate, market kosdaq, looked up at 2026-09-28 "
            "09:31 KST, in the broker's order. Market caps in 조 원, other amounts in 억 원.",
            lines[0],
        )
        self.assertIn("does not say which trading day", lines[1])
        self.assertEqual(
            "1. 종목000001(000001): price 70,000 KRW; change -1,500 KRW (-2.10%); "
            "volume 1,000 shares",
            lines[2],
        )
        self.assertEqual(12, len(lines))  # the default 10 rows
        self.assertEqual({"by": "losers", "market": "kosdaq"}, self.params())
        text = await self.lookup("ranking", by="losers", count=20)
        self.assertIn("The broker returned 12 stocks in this reply.", text)

    async def test_ranking_defaults_and_bases(self) -> None:
        await self.lookup("ranking")
        self.assertEqual({"by": "market_cap", "market": "all"}, self.params())
        self.broker.ranking = {
            "by": "short_selling",
            "market": "all",
            "period": "1d",
            "basis_dates": ["20260924", "20261001"],
            "observed_at": AT,
            "rows": [ranking_row("005930", 1500, short_value=8400000000)],
        }
        text = await self.lookup("ranking", data="short_selling")
        self.assertEqual(
            {"by": "short_selling", "market": "all", "period": "1d"}, self.params()
        )
        self.assertIn("period 1d, KIS dates 2026-09-24–2026-10-01", text)
        self.assertIn("short-sold value 84억 원", text)
        self.assertNotIn("does not say which trading day", text)
        self.broker.ranking = {
            **RANKING,
            "rows": [
                ranking_row(
                    "005930",
                    0,
                    market_cap=16135729,
                    trading_value=676543210000,
                    net_buy_value=-10500,
                    net_buy_volume=-150000,
                )
            ],
        }
        text = await self.lookup("ranking", data="investors")
        self.assertEqual({"by": "foreign_buying", "market": "all"}, self.params())
        self.assertIn("provisional tally; history investors gives daily figures", text)
        self.assertIn(
            "market cap 1,613.57조 원; trading value 6,765.43억 원; net buying value "
            "-105억 원; net buying volume -150,000 shares",
            text,
        )
        self.broker.ranking = {
            **RANKING,
            "market": "all",
            "rows": [ranking_row("000000", 0)],
        }
        text = await self.lookup("ranking", data="attention", market="kospi")
        self.assertEqual({"by": "most_viewed", "market": "all"}, self.params())
        self.assertIn("whole market (KIS offers no market choice", text)

    async def test_arguments_are_checked_against_what_the_action_offers(self) -> None:
        problems = (
            (
                {"action": "orders"},
                "action must be one of account, quote, history, ranking",
            ),
            ({"action": "quote"}, "quote prices needs one of name, market, macro"),
            ({"action": ["account"]}, "action must be one of"),
            ({"action": "quote", "name": " "}, "name must be the stock name"),
            ({"action": "account", "name": ""}, "name must be the stock name"),
            ({"action": "history", "name": " ", "market": "kospi"}, "name must be"),
            (
                {"action": "history", "name": ["삼성전자"]},
                "name must be the stock name",
            ),
            (
                {"action": "quote", "name": "삼성전자", "data": "investors"},
                "data for quote must be one of prices",
            ),
            (
                {"action": "history"},
                "history prices needs one of name, market, macro, commodity",
            ),
            (
                {"action": "history", "market": "all"},
                "market for history prices must be",
            ),
            (
                {"action": "history", "data": "investors", "market": "spx"},
                "market for history investors must be one of kospi, kosdaq",
            ),
            ({"action": "quote", "macro": "bitcoin"}, "macro for quote prices must be"),
            (
                {"action": "quote", "commodity": ["gold"]},
                "commodity for quote prices must be",
            ),
            (
                {
                    "action": "history",
                    "name": "삼성전자",
                    "data": "investors",
                    "period": "3m",
                },
                "period for history investors must be one of 1m",
            ),
            (
                {"action": "history", "name": "삼성전자", "period": "1d"},
                "period for history prices must be one of",
            ),
            (
                {"action": "ranking", "by": "foreign_buying"},
                "by for ranking prices must be one of",
            ),
            (
                {"action": "ranking", "data": "short_selling", "period": "5d"},
                "period for ranking short_selling",
            ),
            (
                {"action": "ranking", "market": "nxt"},
                "market for ranking must be one of",
            ),
            ({"action": "ranking", "by": ["gainers"]}, "by for ranking prices"),
        )
        for arguments, expected in problems:
            with self.subTest(arguments=arguments):
                action = arguments.pop("action")
                text = await self.lookup(action, **arguments)
                self.assertTrue(text.startswith("Broker lookup not run: "), text)
                self.assertIn(expected, text)
        for count in (0, 1.5, True, "10"):
            with self.subTest(count=count):
                text = await self.lookup("ranking", count=count)
                self.assertIn("count must be a whole number", text)
        self.assertEqual(0, len(self.broker.requests))
        # Arguments an action does not use are ignored.
        unused = (
            ("quote", "삼성전자", {"by": "dividend", "count": 0, "period": "5d"}),
            ("account", None, {"data": "attention", "market": "nxt"}),
            ("ranking", "삼성전자", {"period": "5d"}),
            ("history", "삼성전자", {"market": "nxt", "count": 0}),
        )
        for action, name, arguments in unused:
            with self.subTest(action=action, arguments=arguments):
                text = await self.lookup(action, name, **arguments)
                self.assertNotIn("Broker lookup not run", text)
                params = self.broker.requests[-1].url.params
                self.assertNotIn("nxt", params.values())
                self.assertNotIn("5d", params.values())

    async def test_quote_of_a_macro_figure_keeps_its_decimals(self) -> None:
        text = await self.lookup("quote", macro="us10y")
        self.assertTrue(
            text.startswith(
                "US 10-year Treasury yield, looked up at 2026-09-28 09:31 KST: 4.1234%; "
                "change from the previous close -0.031 percentage points (-0.75%). This "
                "is the latest value KIS gives as of the lookup time, not necessarily a "
                "live one."
            ),
            text,
        )
        self.assertEqual({"macro": "us10y"}, self.params())
        text = await self.lookup("quote", commodity="gold")
        self.assertIn("Gold (COMEX), looked up at", text)
        self.assertIn(
            ": 2,650.4 USD per troy ounce; change from the previous close", text
        )
        self.assertIn("-0.031 USD per troy ounce", text)

    async def test_history_of_a_series_has_no_volume(self) -> None:
        text = await self.lookup("history", macro="usdkrw", period="3m")
        lines = text.splitlines()
        self.assertTrue(
            lines[0].startswith(
                "Prices of USD/KRW over the last 3 months, daily rows (one per trading "
                "day), oldest first, 2 rows, looked up at 2026-09-28 09:31 KST. Values in "
                "KRW per USD, as KIS reports them."
            ),
            lines[0],
        )
        self.assertEqual(
            "- 2026-09-24: open 1,384.5512; high 1,385.8012; low 1,384.0512; close "
            "1,384.5512",
            lines[1],
        )
        self.assertEqual(
            {"data": "prices", "period": "3m", "macro": "usdkrw"}, self.params()
        )

    async def test_the_target_is_the_first_given(self) -> None:
        # A name comes first; otherwise market, then macro, then commodity.
        await self.lookup("quote", "삼성전자", macro="us10y", commodity="gold")
        self.assertEqual({"code": "005930"}, self.params())
        await self.lookup("quote", market="spx", commodity="gold")
        self.assertEqual({"market": "spx"}, self.params())
        await self.lookup("history", macro="us10y", commodity="nope")
        self.assertEqual(
            {"data": "prices", "period": "1m", "macro": "us10y"}, self.params()
        )


class UnitTest(unittest.TestCase):
    def test_market_amounts_reach_market_amount_from_any_kis_unit(self) -> None:
        for value, divisor in (
            (123_456_789_012, _WON),
            (123_456.789, _MILLION),
            (1234.57, _EOK),
        ):
            with self.subTest(divisor=divisor):
                self.assertEqual("1,234.57억 원", _market_amount(value, divisor))
        self.assertEqual("-0.5억 원", _market_amount(-50_000_000, _WON))
        self.assertEqual("+12억 원", _market_amount(1200, _MILLION, signed=True))
        self.assertEqual("0억 원", _market_amount(-1, _WON))
        # The unit belongs to the figure, not its size: market caps in 조 원,
        # every other amount in 억 원 however large.
        self.assertEqual("1,613.57조 원", _market_amount(16135729, _EOK, in_jo=True))
        self.assertEqual("0.05조 원", _market_amount(500, _EOK, in_jo=True))
        self.assertEqual(
            "-15,462.13억 원", _market_amount(-1_546_213, _MILLION, signed=True)
        )
        self.assertIsNone(_market_amount(None, _WON))


if __name__ == "__main__":
    unittest.main()
