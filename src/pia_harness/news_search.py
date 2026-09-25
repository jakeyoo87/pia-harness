"""NAVER API HUB news search registered as the ``search`` read tool."""

from __future__ import annotations

import html
import json
import re
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlsplit

import httpx

from .orchestrator import (
    ConversationInput,
    ConversationProgress,
    ReadToolDefinition,
    ReadToolResult,
    ToolCall,
    ToolLink,
)

NAVER_NEWS_SEARCH_URL = "https://naverapihub.apigw.ntruss.com/search/v1/news"
NEWS_RESULT_COUNT = 5
_TAG = re.compile(r"<[^>]+>")

NEWS_SEARCH_DESCRIPTION = (
    "Find up to five news article candidates with title, short description, link, "
    "and date. Candidate bodies are not included."
)

NEWS_SEARCH_ARGUMENTS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": "Two to five key words in the user's language, mainly "
            "the subject's name. Replace pronouns with the actual subject. Do not add "
            "generic words such as news, latest, today, or major. Take a different "
            "angle from searches already made in this Turn.",
        },
    },
    "required": ["query"],
    "additionalProperties": False,
}


class NaverNewsSearch:
    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        timeout_seconds: float = 10.0,
        async_client: httpx.AsyncClient | None = None,
    ) -> None:
        if not isinstance(client_id, str) or not client_id.strip():
            raise ValueError("NAVER client ID is required")
        if not isinstance(client_secret, str) or not client_secret.strip():
            raise ValueError("NAVER client secret is required")
        self._headers = {
            "X-NCP-APIGW-API-KEY-ID": client_id.strip(),
            "X-NCP-APIGW-API-KEY": client_secret.strip(),
        }
        self._owns_client = async_client is None
        self._client = async_client or httpx.AsyncClient(
            timeout=httpx.Timeout(float(timeout_seconds))
        )

    def tool(self) -> ReadToolDefinition:
        return ReadToolDefinition(
            name="search",
            description=NEWS_SEARCH_DESCRIPTION,
            execute=self.execute,
            arguments_schema=NEWS_SEARCH_ARGUMENTS_SCHEMA,
            progress=ConversationProgress.WEB_SEARCH_STARTED,
        )

    async def execute(
        self,
        user_key: str,
        call: ToolCall,
        inputs: tuple[ConversationInput, ...],
    ) -> ReadToolResult:
        del user_key, inputs
        # The Orchestrator already checked that arguments_json is a JSON object.
        arguments = json.loads(call.arguments_json)
        query = arguments.get("query")
        if not isinstance(query, str) or not query.strip():
            return ReadToolResult("News search was not run: the query was invalid.")
        query = query.strip()
        heading = f"News search: query={json.dumps(query, ensure_ascii=False)}"
        try:
            response = await self._client.get(
                NAVER_NEWS_SEARCH_URL,
                params={
                    "query": query,
                    "display": NEWS_RESULT_COUNT,
                    "start": 1,
                    # Always by relevance; candidate dates let Jev judge recency.
                    "sort": "sim",
                },
                headers=self._headers,
            )
        except httpx.HTTPError:
            return ReadToolResult(f"{heading}. The search request failed.")
        if response.status_code != 200:
            return ReadToolResult(
                f"{heading}. The search request failed with status "
                f"{response.status_code}."
            )
        try:
            payload = response.json()
            items = payload["items"]
            if not isinstance(items, list):
                raise TypeError("items")
        except (KeyError, TypeError, ValueError):
            return ReadToolResult(f"{heading}. The search response was invalid.")
        links = tuple(
            link
            for link in (_link(item) for item in items[:NEWS_RESULT_COUNT])
            if link is not None
        )
        if not links:
            return ReadToolResult(f"{heading}. No articles were found.")
        return ReadToolResult(f"{heading}. Found {len(links)} candidates.", links)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


def _link(item: Any) -> ToolLink | None:
    if not isinstance(item, dict):
        return None
    title = _clean(item.get("title"))
    hosts = {
        url: host
        for url in (item.get("link"), item.get("originallink"))
        if (host := _host(url)) is not None
    }
    naver = [
        url
        for url, host in hosts.items()
        if host == "news.naver.com" or host.endswith(".news.naver.com")
    ]
    url = naver[0] if naver else next(iter(hosts), None)
    if not title or url is None:
        return None
    return ToolLink(
        title=title,
        url=url,
        published=_date(item.get("pubDate")),
        summary=_clean(item.get("description")) or None,
    )


def _host(url: Any) -> str | None:
    """Return the host of a usable http(s) link, or None for a malformed one."""
    if not isinstance(url, str) or not url.startswith(("http://", "https://")):
        return None
    try:
        return urlsplit(url).hostname
    except ValueError:
        return None


def _clean(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    text = _TAG.sub("", html.unescape(_TAG.sub("", value)))
    return " ".join(text.split())


def _date(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        return parsedate_to_datetime(value).date().isoformat()
    except (TypeError, ValueError, IndexError):
        return None
