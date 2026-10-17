"""market_feed_search: fetch and swap, the read-only boundary, limits and the result."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from pia_harness import MarketFeedSearchTool, ToolCall, market_feed

NOW = datetime(2026, 10, 17, 13, 0, tzinfo=UTC)  # 22:00 KST

SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE messages (id TEXT PRIMARY KEY, ts TEXT NOT NULL, type TEXT NOT NULL,
    body TEXT NOT NULL, summary TEXT NOT NULL, keywords TEXT NOT NULL, sentiment TEXT,
    stored_at TEXT NOT NULL);
CREATE TABLE message_stocks (id TEXT NOT NULL, stock TEXT NOT NULL);
CREATE TABLE message_sectors (id TEXT NOT NULL, sector TEXT NOT NULL);
CREATE TABLE message_links (id TEXT NOT NULL, url TEXT NOT NULL);
CREATE TABLE targets (id TEXT NOT NULL, seq INTEGER NOT NULL, stock TEXT NOT NULL,
    target_price REAL, previous_target_price REAL, currency TEXT, rating TEXT,
    previous_rating TEXT);
CREATE TABLE events (id TEXT NOT NULL, event_date TEXT NOT NULL, what TEXT NOT NULL,
    stock TEXT);
INSERT INTO meta VALUES ('schema_version', '3');
"""


def feed(messages: list[tuple[str, str, str]], links: tuple[str, ...] = ()) -> bytes:
    """A feed file with (id, type, summary) messages; the body repeats the summary.
    links are article links of the first message."""
    path = Path(tempfile.mkdtemp()) / "feed.db"
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    conn.executemany(
        "INSERT INTO messages VALUES (?, '2026-10-17T21:00:00', ?, ?, ?, 'k', NULL, "
        "'2026-10-17T22:00:00')",
        [(id_, type_, summary, summary) for id_, type_, summary in messages],
    )
    conn.executemany(
        "INSERT INTO message_links VALUES (?, ?)",
        [(messages[0][0], url) for url in links],
    )
    conn.commit()
    conn.close()
    return path.read_bytes()


class Fetches:
    """The host's fetch: versions in order; raises when told to fail."""

    def __init__(self, *copies: bytes) -> None:
        self.copies = list(copies)
        self.calls: list[str | None] = []
        self.downloads = 0
        self.fail = False

    async def __call__(self, version: str | None) -> tuple[str, bytes] | None:
        self.calls.append(version)
        await asyncio.sleep(0)
        if self.fail:
            raise RuntimeError("network down")
        latest = f"v{len(self.copies)}"
        if not self.copies or version == latest:
            return None
        self.downloads += 1
        return latest, self.copies[-1]


class MarketFeedTest(unittest.IsolatedAsyncioTestCase):
    def make(self, fetch: Fetches) -> MarketFeedSearchTool:
        tool = MarketFeedSearchTool(fetch=fetch, clock=lambda: NOW)
        self.addAsyncCleanup(tool.aclose)
        return tool

    async def search(self, tool: MarketFeedSearchTool, sql: str) -> str:
        call = ToolCall("market_feed_search", json.dumps({"sql": sql}))
        return (await tool.execute("user", call, ())).observation_text

    async def test_a_copy_is_downloaded_once_and_swapped_when_it_changes(self) -> None:
        fetch = Fetches(feed([("a", "news", "첫 소식")]))
        tool = self.make(fetch)
        self.assertIn(
            "summary=첫 소식", await self.search(tool, "SELECT summary FROM messages")
        )
        self.assertIn(
            "summary=첫 소식", await self.search(tool, "SELECT summary FROM messages")
        )
        self.assertEqual(([None, "v1"], 1), (fetch.calls, fetch.downloads))
        fetch.copies.append(feed([("b", "broker", "새 소식")]))
        self.assertIn(
            "summary=새 소식", await self.search(tool, "SELECT summary FROM messages")
        )
        self.assertEqual(2, fetch.downloads)

    async def test_three_searches_at_once_check_three_times_and_download_once(
        self,
    ) -> None:
        fetch = Fetches(feed([("a", "news", "소식")]))
        tool = self.make(fetch)
        results = await asyncio.gather(
            *(self.search(tool, "SELECT summary FROM messages") for _ in range(3))
        )
        self.assertTrue(all("summary=소식" in text for text in results))
        self.assertEqual((3, 1), (len(fetch.calls), fetch.downloads))

    async def test_a_failed_check_uses_the_last_copy_or_says_unavailable(self) -> None:
        fetch = Fetches(feed([("a", "news", "소식")]))
        fetch.fail = True
        tool = self.make(fetch)
        text = await self.search(tool, "SELECT summary FROM messages")
        self.assertIn("not available", text)
        self.assertIn("news_search", text)
        fetch.fail = False
        await self.search(tool, "SELECT summary FROM messages")
        fetch.fail = True
        text = await self.search(tool, "SELECT summary FROM messages")
        self.assertIn("checking for a newer copy failed", text)
        self.assertIn("summary=소식", text)

    async def test_rows_stop_at_fifty_or_twenty_thousand_characters(self) -> None:
        tool = self.make(
            Fetches(feed([(str(n), "news", f"소식 {n}") for n in range(60)]))
        )
        text = await self.search(tool, "SELECT summary FROM messages")
        self.assertIn("rows 50 (more not shown)", text)
        self.assertIn("Stopped at 50 rows", text)
        text = await self.search(tool, "SELECT printf('%.900c', 'x') FROM messages")
        self.assertIn("(more not shown)", text)
        self.assertLessEqual(len(text), market_feed.MAX_CHARS)

    async def test_only_one_reading_query_runs(self) -> None:
        tool = self.make(Fetches(feed([("a", "news", "소식")])))
        for sql in (
            "WITH x AS (SELECT summary FROM messages) SELECT * FROM x",
            (
                "WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n WHERE i < 3) "
                "SELECT count(*) FROM n"
            ),
            "SELECT date('now', '+9 hours')",
        ):
            self.assertNotIn("SQL error", await self.search(tool, sql), sql)
        folder = Path(tempfile.mkdtemp())
        for sql in (
            f"VACUUM INTO '{folder / 'copy.db'}'",
            f"ATTACH DATABASE '{folder / 'other.db'}' AS other",
            "PRAGMA journal_mode",
            "INSERT INTO messages (id) VALUES ('x')",
            "SELECT 1; SELECT 2",
            "SELECT nope FROM messages",
        ):
            self.assertIn("SQL error", await self.search(tool, sql), sql)
        self.assertEqual([], list(folder.iterdir()))

    async def test_a_huge_value_and_a_long_query_are_stopped(self) -> None:
        tool = self.make(Fetches(feed([("a", "news", "소식")])))
        text = await self.search(tool, "SELECT length(randomblob(8000000))")
        self.assertIn("SQL error", text)
        original = market_feed.QUERY_SECONDS
        market_feed.QUERY_SECONDS = 0.05
        self.addCleanup(setattr, market_feed, "QUERY_SECONDS", original)
        text = await self.search(
            tool,
            "WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n) "
            "SELECT count(*) FROM n",
        )
        self.assertIn("stopped after about", text)

    async def test_telegram_links_and_handles_are_removed(self) -> None:
        tool = self.make(
            Fetches(
                feed(
                    [
                        (
                            "a",
                            "news",
                            "원문 t.me/somechannel/12 @someone 참고 https://news.example/1",
                        )
                    ],
                    links=("https://news.example/1",),
                )
            )
        )
        text = await self.search(tool, "SELECT summary FROM messages")
        self.assertNotIn("t.me", text)
        self.assertNotIn("@someone", text)
        self.assertIn("https://news.example/1", text)
        # Column names go through the same cleaning.
        text = await self.search(tool, 'SELECT 1 AS "t.me/chan @someone"')
        self.assertNotIn("t.me", text)
        self.assertNotIn("@someone", text)

    async def test_a_url_the_feed_does_not_hold_never_reaches_the_result(self) -> None:
        # The Harness lets web_extract read any URL a result shows: a query must not
        # be able to make one up, in a value, a column name or an error.
        made_up = "https://not-in-db.example/collect?user=1"
        tool = self.make(
            Fetches(feed([("a", "news", "소식")], links=("https://news.example/1",)))
        )
        for sql in (
            f"SELECT '{made_up}' AS url, url AS kept FROM message_links",
            f'SELECT 1 AS "{made_up}"',
            f'SELECT * FROM "{made_up}"',
        ):
            text = await self.search(tool, sql)
            self.assertNotIn("not-in-db.example", text, sql)
        self.assertIn(
            "https://news.example/1",
            await self.search(tool, "SELECT url FROM message_links"),
        )

    async def test_the_result_and_description_carry_the_rules(self) -> None:
        tool = self.make(Fetches(feed([("a", "broker", "소식")])))
        text = await self.search(tool, "SELECT type, summary FROM messages")
        self.assertIn("2026-10-17T22:00 KST", text)
        for label in ("뉴스", "증권사 의견", "커뮤니티 의견", "시장 알림"):
            self.assertIn(label, text)
        self.assertIn("Never name the channels", text)
        description = tool.tool().description
        for rule in (
            "before news_search and web_search",
            "go straight to web_search",
            "date('now', '+9 hours')",
            "반도체·IT, 2차전지·전기차, 바이오·헬스케어, 금융, 소비재·플랫폼",
            "only in message_stocks",
            "50 rows or 20,000 characters",
        ):
            self.assertIn(rule, description)

    async def test_an_empty_query_is_not_run(self) -> None:
        fetch = Fetches(feed([("a", "news", "소식")]))
        tool = self.make(fetch)
        self.assertIn("sql is required", await self.search(tool, " "))
        self.assertEqual([], fetch.calls)


if __name__ == "__main__":
    unittest.main()
