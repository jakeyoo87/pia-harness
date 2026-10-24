"""Web search through Exa Lite, with cached highlights and no page fetch."""

from __future__ import annotations

import json
from typing import Any

import httpx

from .orchestrator import (
    ConversationInput,
    ReadToolDefinition,
    ReadToolResult,
    ToolCall,
    ToolLink,
)

EXA_SEARCH_URL = "https://api.exa.ai/search"
WEB_SEARCH_RESULT_COUNT = 5
WEB_SEARCH_SUMMARY_MAX_CHARS = 400
WEB_SEARCH_DESCRIPTION = (
    "Search the web, news included, for pages about the request: recent news, "
    "company and fund pages, ETF holdings, filings and reports. Returns up to five "
    "candidates with title, link, date and a short excerpt, not full pages. Exa Lite "
    "uses its cached index; pages are not fetched live during search. Choose "
    "when the answer needs facts that are not already in the conversation. For "
    "Korean news articles, news_search is usually better."
)
WEB_SEARCH_ARGUMENTS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "minLength": 1,
            "maxLength": 200,
            "description": "Search query in the language of the sources wanted. "
            "When earlier results left something unconfirmed, search for that. "
            'Operators such as site:example.com and "exact phrase" work.',
        }
    },
    "required": ["query"],
    "additionalProperties": False,
}


class ExaWebSearch:
    def __init__(
        self,
        *,
        api_key: str,
        timeout_seconds: float = 20.0,
        async_client: httpx.AsyncClient | None = None,
    ) -> None:
        # PIA provides its existing Exa key; never put it in a query or URL.
        if not isinstance(api_key, str) or not api_key.strip():
            raise ValueError("api_key is required")
        # The key goes in a header, never in the URL.
        self._headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "x-api-key": api_key.strip(),
        }
        self._owns_client = async_client is None
        self._client = async_client or httpx.AsyncClient(
            timeout=httpx.Timeout(float(timeout_seconds)),
            follow_redirects=False,
            trust_env=False,
        )

    def tool(self) -> ReadToolDefinition:
        return ReadToolDefinition(
            name="web_search",
            description=WEB_SEARCH_DESCRIPTION,
            execute=self.execute,
            arguments_schema=WEB_SEARCH_ARGUMENTS_SCHEMA,
        )

    async def execute(
        self,
        user_key: str,
        call: ToolCall,
        inputs: tuple[ConversationInput, ...],
    ) -> ReadToolResult:
        del user_key, inputs
        # The Orchestrator already checked that arguments_json is a JSON object.
        query = json.loads(call.arguments_json).get("query")
        if not isinstance(query, str) or not query.strip():
            return ReadToolResult("Web search was not run: the query was invalid.")
        query = query.strip()
        heading = f"Web search: query={json.dumps(query, ensure_ascii=False)}"
        payload = {
            "query": query,
            "type": "lite",
            "numResults": WEB_SEARCH_RESULT_COUNT,
            "contents": {"highlights": True},
        }
        try:
            response = await self._client.post(
                EXA_SEARCH_URL, json=payload, headers=self._headers
            )
        except httpx.HTTPError:
            return ReadToolResult(f"{heading}. The search request failed.")
        if response.status_code != 200:
            return ReadToolResult(
                f"{heading}. The search request failed with status "
                f"{response.status_code}."
            )
        try:
            data = response.json()
            rows = data["results"]
            if not isinstance(rows, list):
                raise TypeError("results")
            links = tuple(link for row in rows if (link := _link(row)) is not None)[
                :WEB_SEARCH_RESULT_COUNT
            ]
            notices = data.get("notices", [])
            notes = [
                f"Note: {notice['message']}"
                for notice in notices
                if isinstance(notice, dict) and isinstance(notice.get("message"), str)
            ]
        except (KeyError, TypeError, ValueError):
            # API errors can echo request data: report only the response size.
            return ReadToolResult(
                f"{heading}. The search response was invalid ({len(response.content)} bytes)."
            )
        if links:
            status = f"Found {len(links)} candidates."
        elif rows:
            status = "The search results could not be read (no usable URLs)."
        else:
            status = "No pages were found."
        observation = "\n".join([f"{heading}. {status}", *notes])
        return ReadToolResult(observation, links)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


def _link(row: Any) -> ToolLink | None:
    if not isinstance(row, dict):
        return None
    url = row.get("url")
    if not isinstance(url, str) or not url.startswith(("http://", "https://")):
        return None
    published = _value(row.get("publishedDate"))
    highlights = row.get("highlights") or []
    if not isinstance(highlights, list):
        raise TypeError("highlights")
    return ToolLink(
        title=_value(row.get("title")) or url,
        url=url,
        published=published[:10] if published else None,
        summary=" ".join(" ".join(highlights).split())[:WEB_SEARCH_SUMMARY_MAX_CHARS]
        or None,
    )


def _value(text: Any) -> str | None:
    return text if isinstance(text, str) and text else None
