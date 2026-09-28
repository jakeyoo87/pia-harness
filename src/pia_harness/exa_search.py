"""Web search through Exa's hosted MCP server (`web_search_exa`)."""

from __future__ import annotations

import json
import re
from typing import Any

import httpx

from .orchestrator import (
    ConversationInput,
    ReadToolDefinition,
    ReadToolResult,
    ToolCall,
    ToolLink,
)

EXA_MCP_URL = "https://mcp.exa.ai/mcp"
WEB_SEARCH_RESULT_COUNT = 5
WEB_SEARCH_SUMMARY_MAX_CHARS = 400
WEB_SEARCH_DESCRIPTION = (
    "Search the web, news included, for pages about the request: recent news, "
    "company and fund pages, ETF holdings, filings and reports. Returns up to five "
    "candidates with title, link, date and a short excerpt, not full pages. Choose "
    "when the answer needs facts that are not already in the conversation."
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
_LABELS = ("Title:", "URL:", "Published:", "Author:", "Highlights:")
_SSE_LINE = re.compile(r"\r\n|\r|\n")


class ExaWebSearch:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        timeout_seconds: float = 20.0,
        async_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        # The same endpoint takes a key as a header; it is never put in the URL.
        if api_key is not None and api_key.strip():
            self._headers["x-api-key"] = api_key.strip()
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
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "web_search_exa",
                "arguments": {"query": query, "numResults": WEB_SEARCH_RESULT_COUNT},
            },
        }
        try:
            response = await self._client.post(
                EXA_MCP_URL, json=payload, headers=self._headers
            )
        except httpx.HTTPError:
            return ReadToolResult(f"{heading}. The search request failed.")
        if response.status_code != 200:
            return ReadToolResult(
                f"{heading}. The search request failed with status "
                f"{response.status_code}."
            )
        text = _tool_text(response.content.decode("utf-8", "replace"))
        if text is None:
            return ReadToolResult(f"{heading}. The search response was invalid.")
        if not text.strip():
            return ReadToolResult(f"{heading}. No pages were found.")
        links = parse_results(text)[:WEB_SEARCH_RESULT_COUNT]
        if not links:
            return ReadToolResult(f"{heading}. The search results could not be read.")
        return ReadToolResult(f"{heading}. Found {len(links)} candidates.", links)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


def _tool_text(body: str) -> str | None:
    """Text of a JSON-RPC tools/call reply, sent as JSON or as SSE data lines.

    None for an error reply or an unknown shape. SSE lines are split on CR/LF only:
    str.splitlines would also split on characters that occur inside Korean text.
    """
    stripped = body.strip()
    candidates = [stripped] if stripped.startswith("{") else []
    candidates += [
        line[len("data:") :].strip()
        for line in _SSE_LINE.split(body)
        if line.startswith("data:")
    ]
    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except ValueError:
            continue
        if not isinstance(data, dict) or data.get("error"):
            return None
        result = data.get("result")
        if not isinstance(result, dict) or result.get("isError"):
            return None
        content = result.get("content")
        if not isinstance(content, list):
            return None
        return "".join(
            item["text"]
            for item in content
            if isinstance(item, dict) and isinstance(item.get("text"), str)
        )
    return None


def parse_results(text: str) -> tuple[ToolLink, ...]:
    """Exa's text blocks, split by '---': Title/URL/Published/Author/Highlights."""
    links: list[ToolLink] = []
    for block in text.split("\n---\n"):
        fields: dict[str, str] = {}
        highlights: list[str] = []
        in_highlights = False
        for line in (item.strip() for item in block.splitlines()):
            label = next((name for name in _LABELS if line.startswith(name)), None)
            if label is not None:
                in_highlights = label == "Highlights:"
                value = line[len(label) :].strip()
                if in_highlights:
                    if value:
                        highlights.append(value)
                else:
                    fields[label] = value
            elif in_highlights and line:
                highlights.append(line)
        url = fields.get("URL:", "")
        if not url.startswith(("http://", "https://")):
            continue
        published = fields.get("Published:", "")
        summary = " ".join(highlights)[:WEB_SEARCH_SUMMARY_MAX_CHARS]
        links.append(
            ToolLink(
                title=_value(fields.get("Title:")) or url,
                url=url,
                published=_value(published[:10]),
                summary=summary or None,
            )
        )
    return tuple(links)


def _value(text: str | None) -> str | None:
    return None if text is None or text in ("", "N/A") else text
