"""Public tool contract; every request goes through MockTransport, never live APIs."""

from __future__ import annotations

import io
import json
import logging
import unittest
import zipfile
from datetime import UTC, datetime

import httpx

from pia_harness import PastFinancialsTool, ToolCall

DART_KEY = "FAKE-DART-KEY"
NOW = datetime(2026, 10, 16, tzinfo=UTC)


def companies_zip() -> bytes:
    xml = b"""<result><list><corp_code>00126380</corp_code><corp_name>Samsung</corp_name>
    <stock_code>005930</stock_code></list>
    <list><corp_code>00999998</corp_code><corp_name>Samsung</corp_name>
    <stock_code> </stock_code></list>
    <list><corp_code>00999999</corp_code><corp_name>Private Company</corp_name>
    <stock_code></stock_code></list></result>"""
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as zipped:
        zipped.writestr("CORPCODE.xml", xml)
    return out.getvalue()


def dart_row(account: str, table: str, amount: str, *, year: int = 2025, **extra):
    return {
        "rcept_no": f"{year + 1}0317000001",
        "account_id": account,
        "account_nm": account,
        "sj_div": table,
        "currency": "KRW",
        "thstrm_nm": f"FY {year}",
        "thstrm_amount": amount,
        **extra,
    }


def dart_report(year: int, quarter: int = 4):
    amount = "1000" if quarter == 4 else "300"
    return {
        "status": "000",
        "list": [
            dart_row(
                "ifrs-full_Revenue",
                "IS",
                amount,
                year=year,
                thstrm_add_amount="700",
                frmtrm_amount="900",
                bfefrmtrm_amount="800",
            ),
            dart_row(
                "ifrs-full_ProfitLossAttributableToOwnersOfParent",
                "IS",
                "100" if quarter == 4 else "30",
                year=year,
                thstrm_add_amount="70",
            ),
            dart_row("ifrs-full_Equity", "SCE", "999999", year=year),
            dart_row("ifrs-full_Equity", "BS", "500", year=year),
            dart_row("ifrs-full_ProfitLoss", "IS", "888888", year=year),
            dart_row(
                "ifrs-full_ComprehensiveIncomeAttributableToOwnersOfParent",
                "CIS",
                "777777",
                year=year,
            ),
            dart_row(
                "ifrs-full_CashFlowsFromUsedInOperatingActivities",
                "CF",
                "54",
                year=year,
                thstrm_add_amount="",
            ),
            dart_row("ifrs-full_BasicEarningsLossPerShare", "IS", "1.25", year=year),
            dart_row("ifrs-full_ShortTermBorrowings", "BS", "666666", year=year),
        ],
    }


def fact(
    start,
    end,
    value,
    *,
    filed="2025-03-01",
    form="10-K",
    accn="0001045810-25-000001",
    **extra,
):
    return {
        "start": start,
        "end": end,
        "val": value,
        "filed": filed,
        "form": form,
        "accn": accn,
        "fy": 2026,
        "fp": "FY",
        **extra,
    }


def sec_facts():
    annual = ("2024-01-29", "2025-01-26")
    nine = ("2024-01-29", "2024-10-27")
    third = ("2024-07-29", "2024-10-27")
    return {
        "cik": 1045810,
        "entityName": "NVIDIA",
        "facts": {
            "us-gaap": {
                "RevenueFromContractWithCustomerExcludingAssessedTax": {
                    "units": {
                        "USD": [
                            fact(*third, 999, filed="2024-11-01", form="10-Q"),
                            fact("2023-01-30", "2024-01-28", 850),
                        ]
                    }
                },
                "Revenues": {
                    "units": {
                        "USD": [
                            fact(*annual, 1000),
                            fact(
                                *annual,
                                1100,
                                filed="2026-03-01",
                                form="10-K/A",
                                accn="0001045810-26-000001",
                            ),
                            fact(*nine, 700, filed="2024-11-01", form="10-Q"),
                            fact(
                                *third,
                                300,
                                filed="2026-03-01",
                                accn="0001045810-26-000001",
                            ),
                        ]
                    }
                },
                "NetIncomeLoss": {
                    "units": {
                        "USD": [
                            fact(*annual, 100),
                            fact(*nine, 70, filed="2024-11-01", form="10-Q"),
                            fact(*annual, 999999, filed="2026-04-01", form="DEF 14A"),
                        ]
                    }
                },
                "NetCashProvidedByUsedInOperatingActivities": {
                    "units": {
                        "USD": [
                            fact(*annual, 80),
                            fact(*nine, 50, filed="2024-11-01", form="10-Q"),
                        ]
                    }
                },
                "Assets": {"units": {"USD": [fact(None, annual[1], 400)]}},
                # NVIDIA-style capex tag (the PropertyPlantAndEquipment one is absent).
                "PaymentsToAcquireProductiveAssets": {
                    "units": {"USD": [fact(*annual, 33)]}
                },
                "EarningsPerShareBasic": {
                    "units": {"USD/shares": [fact(*annual, 4.25)]}
                },
            }
        },
    }


class PastFinancialsTest(unittest.IsolatedAsyncioTestCase):
    def make_tool(self, handler):
        self.requests = []

        def wrapped(request):
            self.requests.append(request)
            return handler(request)

        self.client = httpx.AsyncClient(transport=httpx.MockTransport(wrapped))
        self.tool = PastFinancialsTool(
            dart_api_key=DART_KEY,
            sec_user_agent="PIA test test@example.invalid",
            async_client=self.client,
            clock=lambda: NOW,
        )
        return self.tool

    async def asyncTearDown(self):
        if hasattr(self, "client"):
            await self.client.aclose()

    async def read(self, **args):
        return await self.tool.execute(
            "unused", ToolCall("past_financials", json.dumps(args)), ()
        )

    def kr_handler(self, request):
        if request.url.path.endswith("corpCode.xml"):
            return httpx.Response(200, content=companies_zip())
        self.assertTrue(request.url.path.endswith("fnlttSinglAcntAll.json"))
        year = int(request.url.params["bsns_year"])
        if year > 2025:
            return httpx.Response(200, json={"status": "013"})
        quarter = {"11011": 4, "11014": 3, "11012": 2, "11013": 1}[
            request.url.params["reprt_code"]
        ]
        payload = dart_report(year, quarter)
        receipt = (
            f"{year + 1}0317000001"
            if quarter == 4
            else f"{year}{ {1: 5, 2: 8, 3: 11}[quarter]:02d}14000001"
        )
        for row in payload["list"]:
            row["rcept_no"] = receipt
        return httpx.Response(200, json=payload)

    def us_handler(self, request):
        self.assertEqual(request.headers["User-Agent"], "PIA test test@example.invalid")
        if request.url.path.endswith("company_tickers.json"):
            return httpx.Response(
                200,
                json={"0": {"cik_str": 1045810, "ticker": "NVDA", "title": "NVIDIA"}},
            )
        self.assertTrue(request.url.path.endswith("CIK0001045810.json"))
        return httpx.Response(200, json=sec_facts())

    async def test_schema_and_invalid_arguments_make_no_requests(self):
        tool = self.make_tool(self.kr_handler).tool()
        self.assertEqual("past_financials", tool.name)
        self.assertNotIn("statement", tool.arguments_schema["properties"])
        for overrides in (
            {"country": []},
            {"name": ""},
            {"frequency": "forecast"},
            {"count": True},
            {"count": 11},
            {"frequency": "quarterly", "count": 9},
            {"frequency": ""},
            {"count": 0},
            {"count": "5"},
            {"end_year": False},
        ):
            args = {"name": "005930", "country": "kr", **overrides}
            result = await self.read(**args)
            self.assertIn("not run", result.observation_text)
        self.assertEqual([], self.requests)

    async def test_optional_null_arguments_are_the_same_as_omitted(self):
        self.make_tool(self.us_handler)
        properties = self.tool.tool().arguments_schema["properties"]
        for key, value_type in (
            ("frequency", "string"),
            ("count", "integer"),
            ("end_year", "integer"),
        ):
            self.assertEqual([value_type, "null"], properties[key]["type"])
        self.assertIn(None, properties["frequency"]["enum"])
        for frequency in ("annual", "quarterly"):
            base = {"name": "NVDA", "country": "us", "frequency": frequency}
            expected = (await self.read(**base)).observation_text
            cases = [
                {"count": None},
                {"end_year": None},
                {"count": None, "end_year": None},
            ]
            if frequency == "annual":
                cases.extend(
                    [
                        {"frequency": None},
                        {"frequency": None, "count": None, "end_year": None},
                    ]
                )
            for overrides in cases:
                with self.subTest(frequency=frequency, overrides=overrides):
                    actual = (await self.read(**{**base, **overrides})).observation_text
                    self.assertEqual(expected, actual)

    async def test_unlisted_dart_companies_do_not_match(self):
        self.make_tool(self.kr_handler)
        result = await self.read(name="Private Company", country="kr")
        self.assertIn("Company not found", result.observation_text)
        self.assertEqual(1, len(self.requests))

    async def test_kr_all_statements_ids_and_latest_comparison_columns(self):
        self.make_tool(self.kr_handler)
        result = await self.read(name="005930", country="kr", end_year=2025, count=5)
        text = result.observation_text
        self.assertEqual(
            8, len(self.requests)
        )  # metadata + absent current report CFS/OFS + five annual filings
        for year in (2025, 2024, 2023, 2022, 2021):
            self.assertIn(str(year), text)
        for value in ("revenue", "equity", "operating_cashflow", "net_income_parent"):
            self.assertIn(value, text)
        for value in ("999999", "888888", "777777", "666666", DART_KEY):
            self.assertNotIn(value, text)
        self.assertEqual(5, text.count("https://dart.fss.or.kr/"))
        self.assertEqual((), result.links)

    async def test_kr_q4_computed_from_annual_and_nine_months_and_cf_kept_cumulative(
        self,
    ):
        self.make_tool(self.kr_handler)
        result = await self.read(
            name="Samsung", country="kr", frequency="quarterly", end_year=2025, count=2
        )
        text = result.observation_text
        self.assertIn("계산값(연간 − 3분기 누적)", text)
        self.assertIn("revenue (매출): 300", text)
        self.assertIn("net_income_parent (지배주주 순이익): 30", text)
        revenue, calculation = text.split("Period 2025-Q4", 1)[1].splitlines()[1:3]
        self.assertIn("quarter (계산값: 연간 − 3분기 누적)", revenue)
        self.assertNotIn("[", revenue)
        self.assertIn("[1]", calculation)
        self.assertIn("[2]", calculation)
        self.assertIn("operating_cashflow (영업활동 현금흐름): 54", text)
        self.assertIn("누적", text)
        self.assertIn("eps_basic (기본 EPS): 미제공", text)
        self.assertEqual(
            11, len(self.requests)
        )  # inspect subsequent comparative filings, then annual and Q3

    async def test_kr_ofs_once_only_for_013(self):
        def handler(request):
            if request.url.path.endswith("corpCode.xml"):
                return self.kr_handler(request)
            if (
                int(request.url.params["bsns_year"]) > 2025
                or request.url.params["fs_div"] == "CFS"
            ):
                return httpx.Response(200, json={"status": "013"})
            return httpx.Response(200, json=dart_report(2025))

        self.make_tool(handler)
        text = (
            await self.read(name="005930", country="kr", end_year=2025, count=1)
        ).observation_text
        self.assertIn("OFS", text)
        self.assertEqual(5, len(self.requests))

    async def test_kr_auth_or_limit_failure_is_not_missing_data_or_ofs_retry(self):
        def handler(request):
            if request.url.path.endswith("corpCode.xml"):
                return self.kr_handler(request)
            return httpx.Response(
                200, json={"status": "020", "message": "do not echo raw responses"}
            )

        self.make_tool(handler)
        text = (await self.read(name="005930", country="kr", count=1)).observation_text
        self.assertIn("020", text)
        self.assertIn("failed", text)
        self.assertNotIn("do not echo", text)
        self.assertEqual(2, len(self.requests))

    async def test_us_latest_regular_filing_periods_and_changed_tags(self):
        self.make_tool(self.us_handler)
        text = (
            await self.read(name="NVDA", country="us", end_year=2025, count=2)
        ).observation_text
        self.assertIn("2025-01-26", text)
        self.assertIn("2024-01-28", text)
        self.assertIn("revenue (매출): 1100", text)
        self.assertIn("revenue (매출): 850", text)
        self.assertIn("net_income_parent (지배주주 순이익): 100", text)
        self.assertNotIn("999999", text)
        self.assertEqual(2, len(self.requests))

    async def test_us_q4_and_fy_fp_not_used_as_the_value_period(self):
        self.make_tool(self.us_handler)
        text = (
            await self.read(
                name="NVIDIA",
                country="us",
                frequency="quarterly",
                end_year=2025,
                count=2,
            )
        ).observation_text
        self.assertIn("2024-10-27", text)
        self.assertIn("revenue (매출): 300", text)
        self.assertIn("revenue (매출): 400", text)
        q4_period = text.split("Period 2024-Q4 (ends 2025-01-26)", 1)[1]
        revenue, calculation = q4_period.splitlines()[1:3]
        self.assertIn("quarter (계산값: 연간 − 3분기 누적)", revenue)
        self.assertNotIn("[", revenue)
        self.assertIn("[1]", calculation)
        self.assertIn("[2]", calculation)
        self.assertNotIn("revenue (매출): 999", text)
        self.assertIn("계산값(연간 − 3분기 누적)", text)
        self.assertIn("2024-10-28", text)
        self.assertIn("eps_basic (기본 EPS): 미제공", text)
        self.assertEqual(2, len(self.requests))

    async def test_us_actual_q4_wins_and_incompatible_operands_are_not_subtracted(self):
        payload = sec_facts()
        revenue = payload["facts"]["us-gaap"]["Revenues"]["units"]["USD"]
        revenue.append(fact("2024-10-28", "2025-01-26", 401))
        payload["facts"]["us-gaap"]["NetIncomeLoss"]["units"]["USD"] = [
            fact("2024-02-01", "2024-10-27", 70)
        ]

        def handler(request):
            if request.url.path.endswith("company_tickers.json"):
                return self.us_handler(request)
            return httpx.Response(200, json=payload)

        self.make_tool(handler)
        text = (
            await self.read(
                name="NVDA", country="us", frequency="quarterly", end_year=2025, count=1
            )
        ).observation_text
        self.assertIn("revenue (매출): 401", text)
        self.assertNotIn("revenue (매출): 400", text)
        self.assertIn("net_income_parent (지배주주 순이익): 미제공", text)

    async def test_http_failure_is_fixed_text_and_not_redirected(self):
        self.make_tool(
            lambda request: httpx.Response(
                302, headers={"location": "https://elsewhere.invalid/?secret=hidden"}
            )
        )
        text = (await self.read(name="NVDA", country="us")).observation_text
        self.assertIn("failed", text)
        self.assertNotIn("elsewhere", text)
        self.assertEqual(1, len(self.requests))

    async def test_metadata_is_cached_and_unknown_target_does_not_fetch_financials(
        self,
    ):
        self.make_tool(self.us_handler)
        await self.read(name="missing company", country="us")
        await self.read(name="missing company", country="us")
        self.assertEqual(1, len(self.requests))

    async def test_partial_kr_failure_preserves_available_periods_and_discloses_failure(
        self,
    ):
        def handler(request):
            if request.url.path.endswith("corpCode.xml"):
                return self.kr_handler(request)
            if request.url.params["bsns_year"] == "2024":
                return httpx.Response(503)
            return self.kr_handler(request)

        self.make_tool(handler)
        text = (
            await self.read(name="005930", country="kr", end_year=2025, count=5)
        ).observation_text
        self.assertIn("3/5", text)
        self.assertIn("failed", text)
        self.assertIn("revenue (매출): 1000", text)

    async def test_dart_key_is_not_in_httpx_logs(self):
        self.make_tool(self.kr_handler)
        with self.assertLogs("httpx", logging.INFO) as captured:
            await self.read(name="005930", country="kr", end_year=2025, count=1)
        self.assertNotIn(DART_KEY, "\n".join(captured.output))

    async def test_kr_latest_comparison_is_not_replaced_by_an_older_original(self):
        def handler(request):
            response = self.kr_handler(request)
            if request.url.path.endswith("fnlttSinglAcntAll.json"):
                payload = response.json()
                if payload.get("status") == "000":
                    revenue = payload["list"][0]
                    if request.url.params["bsns_year"] == "2024":
                        revenue["bfefrmtrm_amount"] = "2224"
                    if request.url.params["bsns_year"] == "2022":
                        revenue["thstrm_amount"] = "909090"
                    return httpx.Response(200, json=payload)
            return response

        self.make_tool(handler)
        text = (
            await self.read(name="005930", country="kr", end_year=2025, count=5)
        ).observation_text
        self.assertIn("revenue (매출): 2224", text)
        self.assertNotIn("909090", text)
        self.assertIn("2024", {r.url.params.get("bsns_year") for r in self.requests})

    async def test_kr_quarter_comparisons_have_their_own_period_and_feed_q4(self):
        def handler(request):
            if (
                request.url.path.endswith("fnlttSinglAcntAll.json")
                and request.url.params["bsns_year"] == "2026"
                and request.url.params["reprt_code"] == "11014"
            ):
                payload = dart_report(2026, 3)
                for row in payload["list"]:
                    row["rcept_no"] = "20261015000001"
                    if (
                        row["account_id"]
                        == "ifrs-full_ProfitLossAttributableToOwnersOfParent"
                    ):
                        row["frmtrm_q_amount"] = "25"
                        row["frmtrm_add_amount"] = "65"
                    if (
                        row["account_id"] == "ifrs-full_Equity"
                        and row["sj_div"] == "BS"
                    ):
                        row["frmtrm_amount"] = "550"
                return httpx.Response(200, json=payload)
            return self.kr_handler(request)

        self.make_tool(handler)
        text = (
            await self.read(
                name="005930",
                country="kr",
                end_year=2025,
                frequency="quarterly",
                count=2,
            )
        ).observation_text
        third, fourth = text.split("Period 2025-Q3", 1)[1].split("Period 2025-Q4", 1)
        self.assertIn("net_income_parent (지배주주 순이익): 25", third)
        self.assertIn("equity (자본총계): 500", third)
        self.assertIn("net_income_parent (지배주주 순이익): 35", fourth)
        self.assertIn("equity (자본총계): 550", fourth)

    async def test_kr_q4_never_subtracts_different_connection_bases(self):
        def handler(request):
            if (
                request.url.path.endswith("fnlttSinglAcntAll.json")
                and request.url.params["bsns_year"] == "2025"
                and request.url.params["reprt_code"] == "11014"
                and request.url.params["fs_div"] == "CFS"
            ):
                return httpx.Response(200, json={"status": "013"})
            return self.kr_handler(request)

        self.make_tool(handler)
        text = (
            await self.read(
                name="005930",
                country="kr",
                end_year=2025,
                frequency="quarterly",
                count=1,
            )
        ).observation_text
        self.assertIn("revenue (매출): 미제공", text)
        self.assertNotIn("계산값(연간 − 3분기 누적):", text)

    async def test_sec_preserves_zero_and_small_decimal_values(self):
        payload = sec_facts()
        payload["facts"]["us-gaap"]["NetIncomeLoss"]["units"]["USD"] = [
            fact("2024-01-29", "2025-01-26", 0)
        ]
        payload["facts"]["us-gaap"]["EarningsPerShareBasic"]["units"]["USD/shares"][0][
            "val"
        ] = 0.00001

        def handler(request):
            return (
                self.us_handler(request)
                if request.url.path.endswith("company_tickers.json")
                else httpx.Response(200, json=payload)
            )

        self.make_tool(handler)
        text = (
            await self.read(name="NVDA", country="us", end_year=2025, count=1)
        ).observation_text
        self.assertIn("net_income_parent (지배주주 순이익): 0", text)
        self.assertIn("eps_basic (기본 EPS): 0.00001", text)

    async def test_bad_response_does_not_look_like_missing_financials(self):
        def handler(request):
            return (
                self.us_handler(request)
                if request.url.path.endswith("company_tickers.json")
                else httpx.Response(200, json={"cik": 1045810, "facts": []})
            )

        self.make_tool(handler)
        text = (await self.read(name="NVDA", country="us")).observation_text
        self.assertIn("invalid", text)
        self.assertNotIn("periods 0/", text)

    async def test_dart_currency_mismatch_leaves_q4_income_unavailable(self):
        def handler(request):
            response = self.kr_handler(request)
            if (
                request.url.path.endswith("fnlttSinglAcntAll.json")
                and request.url.params["bsns_year"] == "2025"
                and request.url.params["reprt_code"] == "11014"
            ):
                payload = response.json()
                for row in payload.get("list", []):
                    row["currency"] = "USD"
                return httpx.Response(200, json=payload)
            return response

        self.make_tool(handler)
        text = (
            await self.read(
                name="005930",
                country="kr",
                end_year=2025,
                frequency="quarterly",
                count=1,
            )
        ).observation_text
        self.assertIn("revenue (매출): 미제공", text)
        self.assertNotIn("계산값(연간 − 3분기 누적):", text)

    async def test_single_q4_still_fetches_the_nine_month_operand(self):
        self.make_tool(self.kr_handler)
        text = (
            await self.read(
                name="005930",
                country="kr",
                end_year=2025,
                frequency="quarterly",
                count=1,
            )
        ).observation_text
        self.assertIn("revenue (매출): 300", text)
        self.assertIn("net_income_parent (지배주주 순이익): 30", text)
        self.assertIn("계산값(연간 − 3분기 누적)", text)
        self.assertNotIn("Period 2025-Q3", text)

    async def test_default_quarter_does_not_assume_a_calendar_fiscal_year(self):
        def handler(request):
            if (
                request.url.path.endswith("fnlttSinglAcntAll.json")
                and request.url.params["bsns_year"] == "2026"
                and request.url.params["reprt_code"] == "11011"
            ):
                payload = dart_report(2026)
                for row in payload["list"]:
                    row["rcept_no"] = "20260515000001"
                    row["thstrm_nm"] = "사업연도 2026 (기말 2026-03)"
                return httpx.Response(200, json=payload)
            return self.kr_handler(request)

        self.make_tool(handler)
        text = (
            await self.read(name="005930", country="kr", frequency="quarterly", count=1)
        ).observation_text
        self.assertIn("latest filed period: 2026-Q4", text)
        self.assertIn("기말 2026-03", text)

    async def test_capex_falls_back_to_productive_assets(self):
        self.make_tool(self.us_handler)
        text = (await self.read(name="NVDA", country="us")).observation_text
        capex = [line for line in text.splitlines() if line.strip().startswith("capex")]
        self.assertTrue(any("33 USD" in line for line in capex), capex)
        self.assertTrue(
            any("capex (유형·무형자산 취득): 33 USD" in line for line in capex)
        )
        self.assertTrue(
            any("us-gaap:PaymentsToAcquireProductiveAssets" in line for line in capex)
        )

    async def test_answers_cite_two_sources_and_do_not_recheck_on_the_web(self):
        tool = self.make_tool(self.us_handler).tool()
        for text in (
            tool.description,
            (await self.read(name="NVDA", country="us")).observation_text,
        ):
            self.assertIn("do not re-check", text)
            self.assertIn("at most two", text)

    async def test_us_periods_are_named_by_their_middle(self):
        self.make_tool(self.us_handler)
        quarterly = (
            await self.read(name="NVDA", country="us", frequency="quarterly", count=2)
        ).observation_text
        self.assertIn("latest filed period: 2024-Q4 (ends 2025-01-26)", quarterly)
        self.assertIn("Period 2024-Q4 (ends 2025-01-26)", quarterly)
        self.assertIn("Period 2024-Q3 (ends 2024-10-27)", quarterly)
        annual = (await self.read(name="NVDA", country="us", count=2)).observation_text
        self.assertIn("Period 2024 (ends 2025-01-26)", annual)
        self.assertIn("Period 2023 (ends 2024-01-28)", annual)

    async def test_us_end_year_uses_the_period_name(self):
        payload = sec_facts()
        revenue = payload["facts"]["us-gaap"]["Revenues"]["units"]["USD"]
        # July-June years split evenly; ties go to the end year (as SEC frames).
        revenue.append(fact("2024-07-01", "2025-06-30", 2000, filed="2025-08-01"))
        revenue.append(fact("2025-07-01", "2026-06-30", 3000, filed="2026-08-01"))
        # A next-year quarter must not take a slot from the requested year.
        revenue.append(
            fact("2025-01-27", "2025-04-27", 500, filed="2025-05-30", form="10-Q")
        )

        def handler(request):
            if request.url.path.endswith("company_tickers.json"):
                return self.us_handler(request)
            return httpx.Response(200, json=payload)

        self.make_tool(handler)
        annual = (
            await self.read(name="NVDA", country="us", end_year=2025, count=1)
        ).observation_text
        self.assertIn("Period 2025 (ends 2025-06-30)", annual)
        self.assertIn("revenue (매출): 2000", annual)
        self.assertNotIn("revenue (매출): 3000", annual)
        quarterly = (
            await self.read(
                name="NVDA", country="us", frequency="quarterly", end_year=2024, count=2
            )
        ).observation_text
        self.assertIn("periods 2/2", quarterly)
        self.assertIn("Period 2024-Q4 (ends 2025-01-26)", quarterly)
        self.assertIn("Period 2024-Q3 (ends 2024-10-27)", quarterly)
        self.assertNotIn("2025-Q1", quarterly)

    async def test_tool_first_and_no_fiscal_name_lookup(self):
        description = self.make_tool(self.us_handler).tool().description
        self.assertIn("call this tool first", description)
        self.assertIn("do not look up fiscal-year or fiscal-quarter names", description)
