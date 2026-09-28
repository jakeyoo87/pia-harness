"""Read one page through Jina Reader and keep only verified passages for a goal."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable

import httpx

from .orchestrator import PageExcerpt

JINA_READER_URL = "https://r.jina.ai/"
EXTRACT_SOURCE_MAX_CHARS = 30_000
_WHITESPACE = re.compile(r"\s+")


class PageReadError(RuntimeError):
    """The page could not be read; the orchestrator reports it as unread."""


class JinaPageExtractor:
    """Pages are fetched by Jina, so this client only ever connects to Jina."""

    def __init__(
        self,
        extract_passages: Callable[[str, str], Awaitable[tuple[str, ...]]],
        *,
        api_key: str | None = None,
        timeout_seconds: float = 30.0,
        max_source_chars: int = EXTRACT_SOURCE_MAX_CHARS,
        async_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._extract_passages = extract_passages
        self._max_source_chars = max_source_chars
        # DNT asks Jina not to cache or log the request.
        self._headers = {"Accept": "text/plain", "DNT": "1"}
        if api_key is not None and api_key.strip():
            self._headers["Authorization"] = f"Bearer {api_key.strip()}"
        self._owns_client = async_client is None
        self._client = async_client or httpx.AsyncClient(
            timeout=httpx.Timeout(float(timeout_seconds)),
            follow_redirects=False,
            trust_env=False,
        )

    async def extract(self, url: str, goal: str) -> PageExcerpt:
        page = await self._read(url)
        truncated = len(page) > self._max_source_chars
        source = page[: self._max_source_chars]
        passages = await self._extract_passages(goal, source)
        if not passages:
            if truncated:
                return PageExcerpt(
                    f"nothing about the goal in the first {self._max_source_chars:,} "
                    "characters; the rest was not checked"
                )
            return PageExcerpt("nothing about the goal on the page")
        # A passage must be on the page as is; otherwise a changed figure or a
        # made-up link would reach the answer and the readable-link list.
        normalized = _normalize(source)
        if any(_normalize(passage) not in normalized for passage in passages):
            return PageExcerpt("passages did not match the page; nothing was used")
        return PageExcerpt("passages copied from the page", passages)

    async def _read(self, url: str) -> str:
        try:
            response = await self._client.post(
                JINA_READER_URL, json={"url": url}, headers=self._headers
            )
        except httpx.HTTPError:
            raise PageReadError("request failed") from None
        if response.status_code != 200:
            raise PageReadError(f"status {response.status_code}")
        page = response.text
        if not page.strip():
            raise PageReadError("empty page")
        return page

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


def _normalize(text: str) -> str:
    return _WHITESPACE.sub(" ", text).strip()
