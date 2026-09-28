from __future__ import annotations

import json
import unittest

import httpx

from pia_harness import JinaPageExtractor, PageReadError
from pia_harness.web_extract import JINA_READER_URL

PAGE = (
    "Title: TIGER AI반도체핵심공정\n\nMarkdown Content:\n메뉴 | 검색 | 로그인\n"
    "상위 구성종목은 삼성전기 23.3%, 이수페타시스 18.0%,\n  LG이노텍 13.4% 등입니다.\n"
    "기준일 2026.09.28 (장마감)\n자세히 보기 https://www.funetf.co.kr/product/etf\n"
)


def extractor(handler, passages, **options) -> tuple[JinaPageExtractor, list]:
    calls: list[tuple[str, str]] = []

    async def extract_passages(goal: str, page: str) -> tuple[str, ...]:
        calls.append((goal, page))
        return passages

    return (
        JinaPageExtractor(
            extract_passages,
            async_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
            **options,
        ),
        calls,
    )


def page_reply(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, text=PAGE)


class JinaPageExtractorTest(unittest.IsolatedAsyncioTestCase):
    async def test_verified_passages_are_kept(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return page_reply(request)

        # Whitespace may differ from the page; the words may not.
        passages = (
            "상위 구성종목은 삼성전기 23.3%, 이수페타시스 18.0%, LG이노텍 13.4% 등입니다.",
            "기준일 2026.09.28 (장마감)",
        )
        tool, calls = extractor(handler, passages)
        excerpt = await tool.extract("https://www.funetf.co.kr/x", "471760 비중")

        self.assertEqual("passages copied from the page", excerpt.status)
        self.assertEqual(passages, excerpt.passages)
        self.assertEqual(JINA_READER_URL, str(requests[0].url))
        self.assertEqual(
            {"url": "https://www.funetf.co.kr/x"}, json.loads(requests[0].content)
        )
        self.assertEqual("1", requests[0].headers["DNT"])
        self.assertNotIn("authorization", requests[0].headers)
        self.assertEqual([("471760 비중", PAGE)], calls)

    async def test_a_changed_figure_or_new_link_fails_the_page(self) -> None:
        cases = {
            "changed weight": ("상위 구성종목은 삼성전기 25.0%",),
            "made-up link": ("자세히 보기 https://attacker.example/?data=holdings",),
            "one bad of two": ("기준일 2026.09.28 (장마감)", "삼성전기 30%"),
        }
        for name, passages in cases.items():
            with self.subTest(name):
                tool, _ = extractor(page_reply, passages)
                excerpt = await tool.extract("https://a.example", "비중")
                self.assertEqual(
                    "passages did not match the page; nothing was used", excerpt.status
                )
                self.assertEqual((), excerpt.passages)

    async def test_no_passage_says_whether_the_page_was_cut(self) -> None:
        tool, calls = extractor(page_reply, ())
        excerpt = await tool.extract("https://a.example", "배당")
        self.assertEqual("nothing about the goal on the page", excerpt.status)

        tool, calls = extractor(page_reply, (), max_source_chars=40)
        excerpt = await tool.extract("https://a.example", "배당")
        self.assertEqual(
            "nothing about the goal in the first 40 characters; the rest was not checked",
            excerpt.status,
        )
        # The length is fixed before the model sees the page.
        self.assertEqual(PAGE[:40], calls[0][1])

    async def test_a_passage_past_the_cut_does_not_count(self) -> None:
        tool, _ = extractor(
            page_reply, ("기준일 2026.09.28 (장마감)",), max_source_chars=40
        )
        excerpt = await tool.extract("https://a.example", "기준일")
        self.assertEqual(
            "passages did not match the page; nothing was used", excerpt.status
        )

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
                tool, calls = extractor(handler, ("x",))
                with self.assertRaises(PageReadError):
                    await tool.extract("https://a.example", "goal")
                self.assertEqual([], calls)

    async def test_key_is_sent_as_a_bearer_header(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return page_reply(request)

        tool, _ = extractor(handler, (), api_key="synthetic-key")
        await tool.extract("https://a.example", "goal")
        self.assertEqual("Bearer synthetic-key", requests[0].headers["authorization"])


if __name__ == "__main__":
    unittest.main()
