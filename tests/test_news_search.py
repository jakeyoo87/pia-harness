from __future__ import annotations

import json
import unittest
from datetime import UTC, datetime

import httpx

from pia_harness import (
    ConversationInput,
    ConversationProgress,
    NaverNewsSearch,
    ToolCall,
    ToolLink,
)
from pia_harness.news_search import NAVER_NEWS_SEARCH_URL

FAKE_ID = "TEST-ONLY-client-id"
FAKE_SECRET = "TEST-ONLY-client-secret"


def inputs() -> tuple[ConversationInput, ...]:
    now = datetime(2026, 9, 25, 9, 0, tzinfo=UTC)
    return (ConversationInput("user", "삼성전자 왜 떨어져?", now, "turn"),)


def item(index: int, **overrides: str) -> dict[str, str]:
    value = {
        "title": f"<b>삼성전자</b> 기사 {index} &quot;속보&quot;",
        "originallink": f"https://press.example.com/{index}",
        "link": f"https://n.news.naver.com/mnews/article/001/{index}",
        "description": f"<b>삼성전자</b> 요약 {index}",
        "pubDate": "Thu, 24 Sep 2026 18:34:00 +0900",
    }
    value.update(overrides)
    return value


class NaverNewsSearchTest(unittest.IsolatedAsyncioTestCase):
    def search(self, handler) -> NaverNewsSearch:
        self.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        return NaverNewsSearch(
            client_id=FAKE_ID, client_secret=FAKE_SECRET, async_client=self.client
        )

    async def asyncTearDown(self) -> None:
        if hasattr(self, "client"):
            await self.client.aclose()

    async def test_tool_definition_declares_query_and_progress(self) -> None:
        tool = self.search(
            lambda request: httpx.Response(200, json={"items": []})
        ).tool()

        self.assertEqual("search", tool.name)
        self.assertEqual(["query", "sort"], tool.arguments_schema["required"])
        self.assertEqual(ConversationProgress.WEB_SEARCH_STARTED, tool.progress)

    async def test_one_request_returns_five_clean_candidates(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(
                200,
                json={"total": 120, "items": [item(index) for index in range(1, 8)]},
            )

        result = await self.search(handler).execute(
            "user",
            ToolCall(
                "search", json.dumps({"query": "삼성전자 주가 하락", "sort": "date"})
            ),
            inputs(),
        )

        self.assertEqual(1, len(requests))
        request = requests[0]
        self.assertEqual(NAVER_NEWS_SEARCH_URL, str(request.url.copy_with(query=None)))
        self.assertEqual("삼성전자 주가 하락", request.url.params["query"])
        self.assertEqual("5", request.url.params["display"])
        self.assertEqual("date", request.url.params["sort"])
        self.assertEqual(FAKE_ID, request.headers["X-NCP-APIGW-API-KEY-ID"])
        self.assertEqual(FAKE_SECRET, request.headers["X-NCP-APIGW-API-KEY"])
        self.assertEqual(5, len(result.links))
        self.assertEqual(
            ToolLink(
                '삼성전자 기사 1 "속보"',
                "https://n.news.naver.com/mnews/article/001/1",
                "2026-09-24",
                "삼성전자 요약 1",
            ),
            result.links[0],
        )
        self.assertNotIn(FAKE_SECRET, result.observation_text)

    async def test_malformed_links_skip_only_that_candidate(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "items": [
                        item(1, link="http://[bad", originallink="http://[bad"),
                        item(2, link="http://[bad"),
                        item(3),
                    ]
                },
            )

        result = await self.search(handler).execute(
            "user", ToolCall("search", '{"query":"삼성전자","sort":"sim"}'), inputs()
        )

        self.assertEqual(
            [
                "https://press.example.com/2",
                "https://n.news.naver.com/mnews/article/001/3",
            ],
            [link.url for link in result.links],
        )

    async def test_original_link_is_used_without_a_naver_news_link(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={"items": [item(1, link="https://press.example.com/1")]},
            )

        result = await self.search(handler).execute(
            "user", ToolCall("search", '{"query":"삼성전자","sort":"sim"}'), inputs()
        )
        self.assertEqual("https://press.example.com/1", result.links[0].url)

    async def test_failures_are_observations_without_candidates(self) -> None:
        cases = {
            "status": lambda request: httpx.Response(500, json={}),
            "invalid": lambda request: httpx.Response(200, json={"items": "wrong"}),
            "empty": lambda request: httpx.Response(200, json={"items": []}),
        }
        for name, handler in cases.items():
            with self.subTest(case=name):
                result = await self.search(handler).execute(
                    "user",
                    ToolCall("search", '{"query":"삼성전자","sort":"sim"}'),
                    inputs(),
                )
                self.assertEqual((), result.links)
                self.assertTrue(result.observation_text.strip())
                await self.client.aclose()

        def raising(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("offline", request=request)

        result = await self.search(raising).execute(
            "user", ToolCall("search", '{"query":"삼성전자","sort":"sim"}'), inputs()
        )
        self.assertIn("failed", result.observation_text)

    async def test_invalid_query_does_not_call_the_api(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError("invalid query must not be sent")

        search = self.search(handler)
        for arguments in ('{"query":"  ","sort":"sim"}', '{"sort":"sim"}', "[]"):
            with self.subTest(arguments=arguments):
                result = await search.execute(
                    "user", ToolCall("search", arguments), inputs()
                )
                self.assertIn("not run", result.observation_text)

    def test_constructor_requires_credentials(self) -> None:
        with self.assertRaises(ValueError):
            NaverNewsSearch(client_id="", client_secret=FAKE_SECRET)
        with self.assertRaises(ValueError):
            NaverNewsSearch(client_id=FAKE_ID, client_secret=" ")


if __name__ == "__main__":
    unittest.main()
