from __future__ import annotations

import unittest

import httpx

from pia_harness import UrlReader, UrlReadError

PUBLIC = "93.184.216.34"


def resolver(addresses: dict[str, list[str]]):
    async def resolve(host: str, port: int) -> list[str]:
        return addresses.get(host, [PUBLIC])

    return resolve


def html_response(body: str, **headers: str) -> httpx.Response:
    return httpx.Response(
        200,
        content=body.encode(headers.pop("encoding", "utf-8")),
        headers={"content-type": "text/html; charset=utf-8", **headers},
    )


class UrlReaderTest(unittest.IsolatedAsyncioTestCase):
    def reader(self, handler, *, addresses=None, **options) -> UrlReader:
        self.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        return UrlReader(
            resolve=resolver(addresses or {}), async_client=self.client, **options
        )

    async def asyncTearDown(self) -> None:
        if hasattr(self, "client"):
            await self.client.aclose()

    async def test_article_text_is_preferred_and_scripts_are_dropped(self) -> None:
        article = "외국인 순매도가 이어졌다. " * 20
        page = (
            "<html><head><title>메뉴</title><script>var x=1;</script></head><body>"
            "<nav>홈 뉴스 증권</nav>"
            f"<article><h1>삼성전자</h1><p>{article}</p><script>track()</script></article>"
            "<footer>저작권</footer></body></html>"
        )
        text = await self.reader(lambda request: html_response(page)).read(
            "https://n.news.naver.com/a"
        )

        self.assertIn("삼성전자", text)
        self.assertIn("외국인 순매도가 이어졌다.", text)
        self.assertNotIn("홈 뉴스 증권", text)
        self.assertNotIn("track()", text)
        self.assertNotIn("저작권", text)

    async def test_body_is_used_without_an_article_and_is_truncated(self) -> None:
        page = "<html><body><div>" + "가" * 50 + "</div></body></html>"
        text = await self.reader(
            lambda request: html_response(page), max_chars=10
        ).read("https://example.com/")

        self.assertEqual("가" * 10 + " [truncated]", text)

    async def test_meta_charset_decodes_korean_pages(self) -> None:
        page = '<html><head><meta charset="euc-kr"></head><body><p>코스피</p></body></html>'
        text = await self.reader(
            lambda request: httpx.Response(
                200,
                content=page.encode("euc-kr"),
                headers={"content-type": "text/html"},
            )
        ).read("https://example.com/")

        self.assertEqual("코스피", text)

    async def test_non_public_targets_are_refused_before_any_request(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError("refused URL must not be requested")

        reader = self.reader(
            handler,
            addresses={
                "internal.example": ["10.0.0.5"],
                "metadata.example": ["169.254.169.254"],
                "local.example": ["127.0.0.1"],
                "v6.example": ["::1"],
            },
        )
        for url in (
            "file:///etc/passwd",
            "ftp://example.com/a",
            "https://user:pass@example.com/",
            "http://internal.example/",
            "http://metadata.example/latest/meta-data/",
            "http://local.example:8080/",
            "http://v6.example/",
        ):
            with self.subTest(url=url), self.assertRaises(UrlReadError):
                await reader.read(url)

    async def test_redirects_are_rechecked(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "start.example":
                return httpx.Response(
                    302, headers={"location": "http://internal.example/"}
                )
            raise AssertionError("private redirect target must not be requested")

        reader = self.reader(handler, addresses={"internal.example": ["192.168.0.2"]})
        with self.assertRaises(UrlReadError):
            await reader.read("https://start.example/")

        def public_redirect(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/old":
                return httpx.Response(301, headers={"location": "/new"})
            return html_response("<body><p>moved body</p></body>")

        text = await self.reader(public_redirect).read("https://example.com/old")
        self.assertEqual("moved body", text)

    async def test_unreadable_responses_raise_safe_errors(self) -> None:
        cases = {
            "status": lambda request: httpx.Response(404, text="missing"),
            "type": lambda request: httpx.Response(
                200, content=b"%PDF", headers={"content-type": "application/pdf"}
            ),
            "empty": lambda request: html_response("<html><body></body></html>"),
            "loop": lambda request: httpx.Response(302, headers={"location": "/again"}),
        }
        for name, handler in cases.items():
            with self.subTest(case=name):
                with self.assertRaises(UrlReadError) as caught:
                    await self.reader(handler).read("https://example.com/a")
                self.assertNotIn("missing", str(caught.exception))
                await self.client.aclose()

    async def test_default_client_ignores_environment_proxies(self) -> None:
        reader = UrlReader()
        try:
            self.assertFalse(reader._client.trust_env)
        finally:
            await reader.aclose()

    async def test_download_stops_at_the_byte_limit(self) -> None:
        page = "<body><p>" + "a" * 5000 + "</p></body>"
        text = await self.reader(
            lambda request: html_response(page), max_bytes=100, max_chars=10_000
        ).read("https://example.com/")

        self.assertLess(len(text), 100)


if __name__ == "__main__":
    unittest.main()
