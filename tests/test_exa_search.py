from __future__ import annotations

import json
import unittest
from pathlib import Path

import httpx

from pia_harness import ExaWebSearch, ToolCall
from pia_harness.exa_search import EXA_SEARCH_URL

FIXTURE = Path(__file__).parent / "fixtures" / "exa_search.json"


def call(query: str = "ETF holdings official report") -> ToolCall:
    return ToolCall("web_search", json.dumps({"query": query}, ensure_ascii=False))


async def execute(handler, query: str = "ETF holdings official report"):
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        tool = ExaWebSearch(api_key="synthetic-key", async_client=client)
        return await tool.execute("user", call(query), ())


class ExaWebSearchTest(unittest.IsolatedAsyncioTestCase):
    async def test_lite_request_and_five_candidates(self) -> None:
        requests = []

        def handler(request):
            requests.append(request)
            return httpx.Response(200, content=FIXTURE.read_bytes())

        result = await execute(handler)
        self.assertEqual(1, len(requests))
        request = requests[0]
        self.assertEqual(EXA_SEARCH_URL, str(request.url))
        self.assertEqual("synthetic-key", request.headers["x-api-key"])
        self.assertEqual("application/json", request.headers["accept"])
        self.assertEqual(
            {
                "query": "ETF holdings official report",
                "type": "lite",
                "numResults": 5,
                "contents": {"highlights": True},
            },
            json.loads(request.content),
        )
        self.assertIn("Found 5 candidates", result.observation_text)
        self.assertEqual(5, len(result.links))
        first = result.links[0]
        self.assertEqual("기업 자료 1", first.title)
        self.assertEqual("https://source1.example/report", first.url)
        self.assertIsNone(first.published)
        self.assertEqual(400, len(first.summary or ""))
        self.assertEqual("2026-10-20", result.links[1].published)
        self.assertEqual("발췌 2 두 번째 줄", result.links[1].summary)

    async def test_missing_fields_bad_urls_and_result_limit(self) -> None:
        rows = [
            None,
            {"url": "ftp://bad.example"},
            {"title": "URL missing"},
            {"url": "https://a.example", "title": None, "publishedDate": "N/A"},
        ]
        rows += [{"url": f"https://example.com/{i}"} for i in range(6)]
        result = await execute(
            lambda request: httpx.Response(200, json={"results": rows})
        )
        self.assertEqual(5, len(result.links))
        self.assertEqual("https://a.example", result.links[0].title)
        self.assertIsNone(result.links[0].published)
        self.assertIsNone(result.links[0].summary)
        self.assertNotIn("https://example.com/5", [link.url for link in result.links])

    async def test_notices_are_shown_even_with_no_results_but_warnings_are_not(
        self,
    ) -> None:
        for rows in ([], [{"url": "https://example.com"}]):
            with self.subTest(rows=rows):
                result = await execute(
                    lambda request, rows=rows: httpx.Response(
                        200,
                        json={
                            "results": rows,
                            "notices": [
                                {
                                    "code": "CONSTRAINT_RELAXED",
                                    "path": "/query",
                                    "message": "No page matched site:; searched without it.",
                                }
                            ],
                            "warnings": [
                                {
                                    "code": "IGNORED_FIELD",
                                    "path": "/startCrawlDate",
                                    "message": "internal diagnostic",
                                }
                            ],
                        },
                    )
                )
                self.assertIn(
                    "Note: No page matched site:; searched without it.",
                    result.observation_text,
                )
                self.assertNotIn("internal diagnostic", result.observation_text)
                self.assertNotIn("IGNORED_FIELD", result.observation_text)
                if not rows:
                    self.assertIn("No pages were found", result.observation_text)

    async def test_failures_return_safe_results_without_retry(self) -> None:
        cases = [
            (httpx.Response(429, text="synthetic-key"), "failed with status 429"),
            (httpx.Response(503, text="synthetic-key"), "failed with status 503"),
            (
                httpx.Response(302, headers={"location": "https://other.example"}),
                "failed with status 302",
            ),
            (httpx.Response(200, text="synthetic-key"), "response was invalid"),
            (
                httpx.Response(200, json={"error": {"message": "synthetic-key"}}),
                "response was invalid",
            ),
            (httpx.Response(200, json={"results": {}}), "response was invalid"),
            (httpx.Response(200, json={"results": []}), "No pages were found"),
            (
                httpx.Response(200, json={"results": [{"url": "ftp://bad.example"}]}),
                "could not be read",
            ),
        ]
        for reply, expected in cases:
            requests = []

            def handler(request, requests=requests, reply=reply):
                requests.append(request)
                return reply

            with self.subTest(expected=expected):
                result = await execute(handler)
                self.assertIn(expected, result.observation_text)
                self.assertNotIn("synthetic-key", result.observation_text)
                self.assertEqual((), result.links)
                self.assertEqual(1, len(requests))

        def broken(request):
            raise httpx.ConnectError("synthetic-key", request=request)

        result = await execute(broken)
        self.assertIn("The search request failed.", result.observation_text)
        self.assertNotIn("synthetic-key", result.observation_text)

    async def test_blank_query_is_not_sent(self) -> None:
        def handler(request):
            raise AssertionError("not sent")

        result = await execute(handler, "  ")
        self.assertIn("query was invalid", result.observation_text)

    def test_a_key_is_required(self) -> None:
        for key in (None, "", "  "):
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "api_key"):
                ExaWebSearch(api_key=key)

    async def test_tool_definition_is_still_query_only(self) -> None:
        tool = ExaWebSearch(api_key="synthetic-key")
        try:
            definition = tool.tool()
            self.assertEqual("web_search", definition.name)
            self.assertEqual(["query"], list(definition.arguments_schema["properties"]))
            self.assertIn(
                "site:",
                definition.arguments_schema["properties"]["query"]["description"],
            )
            self.assertIn("cached index", definition.description)
        finally:
            await tool.aclose()


if __name__ == "__main__":
    unittest.main()
