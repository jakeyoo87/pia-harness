from __future__ import annotations

import json
import unittest

import httpx

from pia_harness import JinaPageExtractor, PageReadError
from pia_harness.web_extract import JINA_READER_URL

PAGE = (
    "Title: TIGER AI반도체핵심공정\n\nMarkdown Content:\n"
    "[메뉴](https://www.funetf.co.kr/menu) | ![로고](https://img.example/logo.png)\n"
    "상위 구성종목은 삼성전기 23.3%, 이수페타시스 18.0%,\n  LG이노텍 13.4% 등입니다.\n"
    "기준일 2026.09.28 (장마감)\n"
)
REQUEST = "기판 관련 ETF 조사해줘"


def extractor(handler, summary, quotes, **options):
    calls: list[tuple[str, str, str]] = []

    async def read_notes(goal: str, request: str, page: str):
        calls.append((goal, request, page))
        return summary, quotes

    return (
        JinaPageExtractor(
            read_notes,
            async_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
            **options,
        ),
        calls,
    )


def page_reply(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, text=PAGE)


class JinaPageExtractorTest(unittest.IsolatedAsyncioTestCase):
    async def test_summary_and_checked_quotes_are_kept(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return page_reply(request)

        # Whitespace may differ from the page; the words may not.
        quotes = (
            "상위 구성종목은 삼성전기 23.3%, 이수페타시스 18.0%, LG이노텍 13.4% 등입니다.",
            "기준일 2026.09.28 (장마감)",
        )
        tool, calls = extractor(
            handler, "상위 3종목 비중이 있다. 자세히 https://x.example/y", quotes
        )
        excerpt = await tool.extract("https://www.funetf.co.kr/x", "비중", REQUEST)

        self.assertEqual("read; 2 quote(s) checked against the page", excerpt.status)
        self.assertEqual(quotes, excerpt.passages)
        # Links in the model's own words are dropped.
        self.assertEqual("상위 3종목 비중이 있다. 자세히", excerpt.summary)
        self.assertEqual(JINA_READER_URL, str(requests[0].url))
        self.assertEqual(
            {"url": "https://www.funetf.co.kr/x"}, json.loads(requests[0].content)
        )
        self.assertEqual("1", requests[0].headers["DNT"])
        self.assertNotIn("authorization", requests[0].headers)
        goal, request, page = calls[0]
        self.assertEqual(("비중", REQUEST), (goal, request))
        # Link targets and images are removed before the model reads the page.
        self.assertIn("메뉴 |", page)
        self.assertNotIn("https://", page)
        self.assertNotIn("로고", page)

    async def test_only_quotes_not_on_the_page_are_dropped(self) -> None:
        quotes = (
            "기준일 2026.09.28 (장마감)",
            "상위 구성종목은 삼성전기 25.0%",
            "자세히 https://attacker.example/?data=holdings",
            "LG이노텍 13.4% 등입니다.】【。",
        )
        tool, _ = extractor(page_reply, "요약", quotes)
        excerpt = await tool.extract("https://a.example", "비중", REQUEST)

        self.assertEqual(
            "read; 1 quote(s) checked against the page, 3 dropped as not on the page",
            excerpt.status,
        )
        self.assertEqual(("기준일 2026.09.28 (장마감)",), excerpt.passages)

    async def test_quotes_stop_at_the_page_budget(self) -> None:
        page = "\n".join(f"문장 {n} " + "가" * 490 for n in range(10))
        quotes = tuple(line for line in page.splitlines())

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text=page)

        tool, _ = extractor(handler, "요약", quotes)
        excerpt = await tool.extract("https://a.example", "문장", REQUEST)

        self.assertEqual(quotes[:4], excerpt.passages)
        self.assertLessEqual(
            len(excerpt.summary) + sum(map(len, excerpt.passages)), 2_000
        )

    async def test_nothing_found_says_whether_the_page_was_cut(self) -> None:
        tool, _ = extractor(page_reply, "", ())
        excerpt = await tool.extract("https://a.example", "배당", REQUEST)
        self.assertEqual("nothing about the goal on the page", excerpt.status)

        tool, calls = extractor(page_reply, "", (), max_source_chars=40)
        excerpt = await tool.extract("https://a.example", "배당", REQUEST)
        self.assertEqual(
            "nothing about the goal in the first 40 characters; "
            "the rest was not checked",
            excerpt.status,
        )
        # The length is fixed before the model sees the page.
        self.assertEqual(40, len(calls[0][2]))

    async def test_a_quote_past_the_cut_does_not_count(self) -> None:
        tool, _ = extractor(
            page_reply, "요약", ("기준일 2026.09.28 (장마감)",), max_source_chars=40
        )
        excerpt = await tool.extract("https://a.example", "기준일", REQUEST)
        self.assertEqual((), excerpt.passages)
        self.assertIn("only the first 40 characters were read", excerpt.status)

    async def test_unreadable_pages_raise(self) -> None:
        def broken(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("down", request=request)

        handlers = {
            "status": lambda request: httpx.Response(451, text="blocked"),
            "redirect": lambda request: httpx.Response(
                302, headers={"location": "https://elsewhere.example"}
            ),
            "empty": lambda request: httpx.Response(200, text="  "),
            "transport": broken,
        }
        for name, handler in handlers.items():
            with self.subTest(name):
                tool, calls = extractor(handler, "x", ("x",))
                with self.assertRaises(PageReadError):
                    await tool.extract("https://a.example", "goal", REQUEST)
                self.assertEqual([], calls)

    async def test_key_is_sent_as_a_bearer_header(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return page_reply(request)

        tool, _ = extractor(handler, "", (), api_key="synthetic-key")
        await tool.extract("https://a.example", "goal", REQUEST)
        self.assertEqual("Bearer synthetic-key", requests[0].headers["authorization"])


if __name__ == "__main__":
    unittest.main()
