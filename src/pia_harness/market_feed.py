"""Market feed search: the model's own read-only SQL over the investment-channel feed.

The feed is one SQLite file the host fetches (PIA: an S3 object the Mac collector
overwrites). The tool asks the host's fetch for a newer copy on every search and swaps
the file only when it changed. Plan: plans/2026-10-17_market-feed-search.md; the DB
contract is pia-collector docs/research-db-spec.md (schema_version 3).
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import sqlite3
import tempfile
import time
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .orchestrator import (
    ConversationInput,
    ReadToolDefinition,
    ReadToolResult,
    ToolCall,
)

# The host's fetch: given the version it last returned (None at first), the new
# (version, SQLite file content) when the feed changed, or None when it did not.
Fetch = Callable[[str | None], Awaitable[tuple[str, bytes] | None]]

MAX_ROWS = 50
MAX_CHARS = 20_000
# One value at most this many bytes, even inside the query; the longest body is
# about 2,500 characters.
MAX_VALUE_BYTES = 1_000_000
# A rough limit checked between SQLite steps, not a strict time or memory bound.
QUERY_SECONDS = 2.0
_KST = timezone(timedelta(hours=9))
# Reading only: a read-only connection still lets VACUUM INTO and ATTACH write files.
_ALLOWED = frozenset(
    {
        sqlite3.SQLITE_SELECT,
        sqlite3.SQLITE_READ,
        sqlite3.SQLITE_FUNCTION,
        sqlite3.SQLITE_RECURSIVE,
    }
)
_TELEGRAM_LINK = re.compile(r"(?:https?://)?(?:t\.me|telegram\.me)/\S+", re.IGNORECASE)
_HANDLE = re.compile(r"(?<![\w.])@[A-Za-z0-9_]{3,}")
_SPACE = re.compile(r"\s+")

DESCRIPTION = (
    "Recent market news and opinions: a database of the last 30 days of short "
    "messages from investment channels (shared news articles, broker comments, "
    "community opinions, market alerts), searched with one read-only SQLite query you "
    "write. It has no account, live quote or filed financial-statement data. For news "
    "and opinions on markets, sectors or stocks, use this before news_search and "
    "web_search; if it lacks what the question needs, use news_search, then "
    "web_search. When the answer lives in a specific document or page, go straight "
    "to web_search: ETF holdings and weights, official company pages or filings, "
    "product details, statistics tables, events older than 30 days, or a link the "
    "user gave. "
    "Tables: messages(id, ts, type, body, summary, keywords, sentiment, stored_at), "
    "type one of news, broker, community, alert; keywords is text joined with ', ' "
    "(search it with LIKE), sentiment positive, neutral, negative or null. "
    "message_stocks(id, stock): Korean official stock names (삼성전자, SK하이닉스) and "
    "US tickers (NVDA, MU). message_sectors(id, sector), sector exactly one of "
    "반도체·IT, 2차전지·전기차, 바이오·헬스케어, 금융, 소비재·플랫폼, "
    "에너지·방산·산업재, 매크로, 기타. message_links(id, url): news article links. "
    "targets(id, seq, stock, target_price, previous_target_price, currency, rating, "
    "previous_rating): price targets in their own currency (KRW, USD, ...), rating "
    "buy, hold or sell. events(id, event_date, what, stock): dated events. All join "
    "on id. Times are Korean time without an offset: ts and stored_at "
    "'YYYY-MM-DDTHH:MM:SS', event_date 'YYYY-MM-DD'; today is date('now', '+9 "
    "hours'); compare periods as text, such as ts >= '2026-10-15'. "
    "How to search: match places with OR to start wide (body LIKE, keywords LIKE, "
    "and a stock in message_stocks by exact name); look up a short ticker such as MU "
    "only in message_stocks, never with body LIKE. Add AND conditions (period, type) "
    "only when the question asks for them. Order by ts DESC. First select id, ts, "
    "type and summary; then read body only for the messages you need (about ten) "
    "with WHERE id IN (...). A result stops at 50 rows or 20,000 characters; narrow "
    "the query when it does. The same story can appear from several channels in "
    "different words: treat them as one. Stocks outside Korea and the US have no "
    "fixed name form: find them through keywords and body."
)
SOURCE_RULE = (
    "Source rule: cite these only by type, as 뉴스 (news; cite its article link from "
    "message_links like any other link when you have it), 증권사 의견 (broker), "
    "커뮤니티 의견 (community) or 시장 알림 (alert). Never name the channels, brokers, "
    "analysts or people behind them. These are the channels' messages, not "
    "verified facts."
)
ARGUMENTS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "sql": {
            "type": "string",
            "description": "One read-only SQLite query (SELECT or WITH ... SELECT).",
        }
    },
    "required": ["sql"],
    "additionalProperties": False,
}


class MarketFeedSearchTool:
    def __init__(
        self, *, fetch: Fetch, clock: Callable[[], datetime] | None = None
    ) -> None:
        self._fetch = fetch
        self._clock = clock or (lambda: datetime.now(UTC))
        self._dir = Path(tempfile.mkdtemp(prefix="pia-market-feed-"))
        self._path = self._dir / "feed.db"
        self._version: str | None = None
        # Reads run up to three at a time: one check and swap at a time keeps the
        # same new copy from being downloaded twice.
        self._lock = asyncio.Lock()

    def tool(self) -> ReadToolDefinition:
        return ReadToolDefinition(
            "market_feed_search", DESCRIPTION, self.execute, ARGUMENTS
        )

    async def execute(
        self, user_key: str, call: ToolCall, inputs: tuple[ConversationInput, ...]
    ) -> ReadToolResult:
        del user_key, inputs
        sql = json.loads(call.arguments_json).get("sql")
        if not isinstance(sql, str) or not sql.strip():
            return ReadToolResult("market_feed_search not run: sql is required.")
        check_failed = await self._refresh()
        if not self._path.exists():
            return ReadToolResult(
                "market_feed_search: the feed is not available right now; "
                "use news_search or web_search."
            )
        try:
            columns, rows, more = await asyncio.to_thread(_query, self._path, sql)
        except sqlite3.Error as error:
            reason = (
                f"stopped after about {QUERY_SECONDS:g} seconds"
                if "interrupted" in str(error)
                else str(error)
            )
            return ReadToolResult(
                f"market_feed_search: SQL error: {reason}. Only one read-only query "
                "is allowed; fix or narrow it and try again."
            )
        return ReadToolResult(_render(columns, rows, more, self._clock(), check_failed))

    async def _refresh(self) -> bool:
        """Swaps in a newer copy if there is one; True when the check failed."""
        async with self._lock:
            try:
                fresh = await self._fetch(self._version)
            except Exception:  # noqa: BLE001 - any fetch failure keeps the last copy
                return True
            if fresh is not None:
                version, content = fresh
                temp = self._dir / "feed.tmp"
                await asyncio.to_thread(temp.write_bytes, content)
                # A search still reading the old file keeps it until it closes.
                os.replace(temp, self._path)
                self._version = version
            return False

    async def aclose(self) -> None:
        shutil.rmtree(self._dir, ignore_errors=True)


def _authorize(action: int, *_: Any) -> int:
    return sqlite3.SQLITE_OK if action in _ALLOWED else sqlite3.SQLITE_DENY


def _query(path: Path, sql: str) -> tuple[list[str], list[tuple[Any, ...]], bool]:
    conn = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    try:
        conn.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, MAX_VALUE_BYTES)
        conn.set_authorizer(_authorize)
        deadline = time.monotonic() + QUERY_SECONDS
        conn.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
        cursor = conn.execute(sql)
        columns = [column[0] for column in cursor.description or ()]
        rows = cursor.fetchmany(MAX_ROWS + 1)
    finally:
        conn.close()
    return columns, rows[:MAX_ROWS], len(rows) > MAX_ROWS


def _text(value: Any) -> str:
    if value is None:
        return "null"
    text = str(value)
    text = _TELEGRAM_LINK.sub("", text)
    text = _HANDLE.sub("", text)
    return _SPACE.sub(" ", text).strip()


def _render(
    columns: Sequence[str],
    rows: Sequence[tuple[Any, ...]],
    more: bool,
    now: datetime,
    check_failed: bool,
) -> str:
    lines = [SOURCE_RULE]
    if check_failed:
        lines.append(
            "Note: checking for a newer copy failed; these rows come from the last "
            "copy downloaded."
        )
    if not rows:
        lines.append("No rows matched.")
    used = sum(len(line) + 1 for line in lines)
    shown = 0
    for number, row in enumerate(rows, 1):
        line = f"{number}. " + "; ".join(
            f"{column}={_text(value)}" for column, value in zip(columns, row)
        )
        if used + len(line) + 1 > MAX_CHARS:
            more = True
            break
        lines.append(line)
        used += len(line) + 1
        shown += 1
    if more:
        lines.append(
            f"Stopped at {shown} rows (limit {MAX_ROWS} rows or {MAX_CHARS:,} "
            "characters): narrow the query (period, stock, type) or select fewer "
            "columns."
        )
    header = (
        "market_feed_search (시장 소식): investment-channel news and opinions, last 30 "
        f"days; searched at {now.astimezone(_KST):%Y-%m-%dT%H:%M} KST; rows {shown}"
        f"{' (more not shown)' if more else ''}."
    )
    return "\n".join([header, *lines])
