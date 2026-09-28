from __future__ import annotations

import json
import unittest
from pathlib import Path

import httpx

from pia_harness import ExaWebSearch, ToolCall
from pia_harness.exa_search import EXA_MCP_URL, parse_results

FIXTURE = Path(__file__).parent / "fixtures" / "exa_web_search.sse"


def search(handler) -> ExaWebSearch:
    return ExaWebSearch(
        async_client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )


def call(query: str = "TIGER AI반도체핵심공정 471760 구성종목") -> ToolCall:
    return ToolCall("web_search", json.dumps({"query": query}, ensure_ascii=False))


def mcp_reply(text: str) -> httpx.Response:
    body = {"result": {"content": [{"type": "text", "text": text}]}}
    return httpx.Response(
        200,
        text="event: message\ndata: " + json.dumps(body, ensure_ascii=False) + "\n",
        headers={"content-type": "text/event-stream"},
    )


class ExaWebSearchTest(unittest.IsolatedAsyncioTestCase):
    async def test_recorded_reply_becomes_five_candidates(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(
                200,
                content=FIXTURE.read_bytes(),
                headers={"content-type": "text/event-stream"},
            )

        result = await search(handler).execute("user", call(), ())

        self.assertEqual(EXA_MCP_URL, str(requests[0].url))
        sent = json.loads(requests[0].content)
        self.assertEqual("web_search_exa", sent["params"]["name"])
        self.assertEqual(5, sent["params"]["arguments"]["numResults"])
        self.assertNotIn("x-api-key", requests[0].headers)
        self.assertIn("Found 5 candidates", result.observation_text)
        self.assertEqual(
            [
                "https://etfshopping.com/etf/471760",
                "https://www.funetf.co.kr/product/etf/view/KR7471760009",
                "https://finance.naver.com/item/main.naver?code=471760",
                "https://kr.tradingview.com/symbols/KRX-471760/holdings/",
                "https://etfexplorer.io/etf/471760",
            ],
            [link.url for link in result.links],
        )
        first = result.links[0]
        self.assertTrue(first.title.startswith("TIGER AI반도체핵심공정"))
        # "N/A" is not a date.
        self.assertIsNone(first.published)
        self.assertIsNotNone(first.summary)
        self.assertLessEqual(len(first.summary or ""), 400)

    def test_fields_and_highlights_are_read_per_block(self) -> None:
        text = (
            "Title: 기사 A\nURL: https://a.example/1\nPublished: 2026-09-28T04:55:00Z\n"
            "Author: 기자\nHighlights:\n첫 줄\n둘째 줄\n\n---\n\n"
            "Title: N/A\nURL: https://b.example/2\nPublished: N/A\nHighlights:\n\n---\n\n"
            "Title: 링크 없음\nURL: ftp://c.example/3\n"
        )
        links = parse_results(text)

        self.assertEqual(2, len(links))
        self.assertEqual("기사 A", links[0].title)
        self.assertEqual("2026-09-28", links[0].published)
        self.assertEqual("첫 줄 둘째 줄", links[0].summary)
        # A missing title falls back to the link.
        self.assertEqual("https://b.example/2", links[1].title)
        self.assertIsNone(links[1].summary)

    async def test_failures_come_back_as_results(self) -> None:
        cases = {
            "status": lambda request: httpx.Response(429, text="rate limited"),
            "error reply": lambda request: httpx.Response(
                200, json={"error": {"message": "limit"}}
            ),
            "tool error": lambda request: httpx.Response(
                200, json={"result": {"isError": True, "content": []}}
            ),
            "unreadable text": lambda request: mcp_reply("no blocks here"),
            "empty": lambda request: mcp_reply(""),
        }
        expected = {
            "status": "failed with status 429",
            "error reply": "response was an error or invalid",
            "tool error": "response was an error or invalid",
            "unreadable text": "could not be read (14 characters, no result blocks)",
            "empty": "No pages were found",
        }
        for name, handler in cases.items():
            with self.subTest(name):
                result = await search(handler).execute("user", call(), ())
                self.assertIn(expected[name], result.observation_text)
                self.assertEqual((), result.links)

        def broken(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("down", request=request)

        result = await search(broken).execute("user", call(), ())
        self.assertIn("The search request failed.", result.observation_text)

    async def test_blank_query_is_not_sent(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError("not sent")

        result = await search(handler).execute("user", call("  "), ())
        self.assertIn("query was invalid", result.observation_text)

    async def test_key_goes_in_a_header(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return mcp_reply("Title: A\nURL: https://a.example/1\n")

        tool = ExaWebSearch(
            api_key="synthetic-key",
            async_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )
        await tool.execute("user", call(), ())

        self.assertEqual("synthetic-key", requests[0].headers["x-api-key"])
        self.assertEqual(EXA_MCP_URL, str(requests[0].url))

    def test_tool_definition(self) -> None:
        tool = ExaWebSearch().tool()
        self.assertEqual("web_search", tool.name)
        self.assertEqual(["query"], tool.arguments_schema["required"])
        self.assertIn(
            "site:", tool.arguments_schema["properties"]["query"]["description"]
        )


if __name__ == "__main__":
    unittest.main()
