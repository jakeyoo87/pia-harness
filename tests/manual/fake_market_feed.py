"""A fake market feed for the market_feed scenario set (smoke_flow).

Made-up messages in the real DB shape (pia-collector docs/research-db-spec.md,
schema_version 3, times in KST without an offset), dated from the run time: today,
yesterday, last week and the past month. No channel, broker or person names. It holds
one story twice in different words (대한전선), a message with "MUSK" in the body for
the short-ticker check (MU), price targets, this week's events and more than 50 US
stock messages. Nothing about Japanese export controls or 삼성전자 preliminary results.
"""

from __future__ import annotations

import sqlite3
import tempfile
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

_KST = timezone(timedelta(hours=9))
_SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE messages (id TEXT PRIMARY KEY, ts TEXT NOT NULL, type TEXT NOT NULL,
    body TEXT NOT NULL, summary TEXT NOT NULL, keywords TEXT NOT NULL, sentiment TEXT,
    stored_at TEXT NOT NULL);
CREATE TABLE message_stocks (id TEXT NOT NULL, stock TEXT NOT NULL, PRIMARY KEY (id, stock));
CREATE TABLE message_sectors (id TEXT NOT NULL, sector TEXT NOT NULL, PRIMARY KEY (id, sector));
CREATE TABLE message_links (id TEXT NOT NULL, url TEXT NOT NULL, PRIMARY KEY (id, url));
CREATE TABLE targets (id TEXT NOT NULL, seq INTEGER NOT NULL, stock TEXT NOT NULL,
    target_price REAL, previous_target_price REAL, currency TEXT, rating TEXT,
    previous_rating TEXT, PRIMARY KEY (id, seq));
CREATE TABLE events (id TEXT NOT NULL, event_date TEXT NOT NULL, what TEXT NOT NULL,
    stock TEXT);
INSERT INTO meta VALUES ('schema_version', '3');
"""

# (hours before now, type, summary, body, keywords, stocks, sectors, extra)
_MESSAGES: list[
    tuple[float, str, str, str, str, list[str], list[str], dict[str, Any]]
] = [
    (
        0.1,  # minutes ago, so it is "today" whenever the set runs
        "news",
        "마이크론 대만 공장 파업으로 D램·HBM 공급 차질 우려가 제기됐다.",
        (
            "마이크론의 대만 공장에서 파업이 시작돼 D램과 HBM 공급 차질 우려가 커졌다. 회사는 "
            "생산 영향이 제한적이라고 밝혔다."
        ),
        "마이크론, MU, 파업, D램, HBM, 공급 차질",
        ["MU"],
        ["반도체·IT"],
        {"link": "https://news.example.com/micron-strike"},
    ),
    (
        3,
        "community",
        "테슬라가 머스크 발언 뒤 장중 6% 올랐다는 의견.",
        "MUSK 발언 하나에 테슬라가 장중 6% 상승. 단기 과열 같아 보인다는 의견.",
        "테슬라, TSLA, 머스크, 단기 과열",
        ["TSLA"],
        ["2차전지·전기차"],
        {},
    ),
    (
        4,
        "broker",
        "SK하이닉스 목표주가 300,000원에서 330,000원으로 상향(매수 유지). HBM 수요 강세.",
        (
            "SK하이닉스 목표주가를 330,000원으로 올린다. HBM3E 판매 비중 확대와 서버 D램 가격 "
            "상승으로 4분기 영업이익이 컨센서스를 웃돌 것으로 본다. 투자의견 매수 유지."
        ),
        "SK하이닉스, 000660, 하이닉스, HBM, 목표주가 상향, 메모리",
        ["SK하이닉스"],
        ["반도체·IT"],
        {"target": ("SK하이닉스", 330000, 300000, "KRW", "buy", "buy")},
    ),
    (
        5,
        "broker",
        "삼성전자 메모리 업황 개선으로 4분기 실적 개선 전망(매수 의견).",
        (
            "삼성전자는 범용 D램 가격 반등과 HBM 공급 확대로 4분기 영업이익 개선이 예상된다. "
            "매수 의견을 유지하며 목표주가 110,000원을 유지한다."
        ),
        "삼성전자, 005930, 메모리 업황, D램 가격, 실적 개선",
        ["삼성전자"],
        ["반도체·IT"],
        {
            "sentiment": "positive",
            "target": ("삼성전자", 110000, 110000, "KRW", "buy", "buy"),
        },
    ),
    (
        6,
        "alert",
        "52주 신고가: SK하이닉스, 한미반도체.",
        "52주 신고가 종목: SK하이닉스, 한미반도체",
        "52주 신고가, SK하이닉스, 한미반도체",
        ["SK하이닉스", "한미반도체"],
        ["반도체·IT"],
        {},
    ),
    (
        26,
        "news",
        "필라델피아 반도체지수가 3% 반등했다.",
        "필라델피아 반도체지수가 엔비디아와 브로드컴 강세에 3% 반등했다.",
        "필라델피아 반도체지수, SOX, 반도체 반등, 엔비디아, 브로드컴",
        ["NVDA", "AVGO"],
        ["반도체·IT"],
        {"link": "https://news.example.com/sox-rebound"},
    ),
    (
        27,
        "community",
        "삼성전자 HBM4 품질 검증 통과 기대감이 커지고 있다는 의견.",
        "삼성전자 HBM4가 연내 품질 검증을 통과할 수 있다는 기대가 커지는 중. 확인된 건 아님.",
        "삼성전자, HBM4, 퀄 테스트, 품질 검증",
        ["삼성전자"],
        ["반도체·IT"],
        {"sentiment": "positive"},
    ),
    (
        30,
        "broker",
        "대한전선 목표주가를 39,000원에서 36,000원으로 하향(매수 유지). 환율 하락과 선적 지연.",
        (
            "대한전선 목표주가 36,000원으로 하향. 환율 하락과 선적 지연으로 3분기 영업이익이 "
            "컨센서스를 하회할 전망."
        ),
        "대한전선, 001440, 목표주가 하향, 전선, 선적 지연",
        ["대한전선"],
        ["에너지·방산·산업재"],
        {"target": ("대한전선", 36000, 39000, "KRW", "buy", "buy")},
    ),
    (
        31,
        "broker",
        "대한전선 목표주가를 36,000원으로 낮췄다. 3분기 이익이 예상을 밑돌고 내년 추정도 낮췄다.",
        (
            "대한전선: 목표가 39,000 → 36,000원. 선적 지연과 원화 강세로 3분기 실적 부진, "
            "2027년 이익 추정 하향. 매수 유지."
        ),
        "대한전선, 목표가 하향, 실적 부진, 원화 강세",
        ["대한전선"],
        ["에너지·방산·산업재"],
        {"target": ("대한전선", 36000, 39000, "KRW", "buy", "buy")},
    ),
    (
        8 * 24,
        "broker",
        "반도체 업종: 메모리 가격 상승이 이어져 4분기 업황 개선이 예상된다.",
        (
            "반도체 업종 의견: 9월 이후 범용 D램 계약가격 상승이 이어지고 있어 4분기 메모리 "
            "업황 개선을 예상한다. 최선호주 SK하이닉스."
        ),
        "반도체 업종, 메모리 가격, D램 계약가격, 업황",
        ["SK하이닉스", "삼성전자"],
        ["반도체·IT"],
        {},
    ),
    (
        9 * 24,
        "news",
        "미국 국채 10년물 금리가 4.6%로 올랐다.",
        "미국 국채 10년물 금리가 물가 지표 발표 뒤 4.6%로 상승했다.",
        "미국 국채, 10년물, 금리, 물가",
        [],
        ["매크로"],
        {"link": "https://news.example.com/ust-10y"},
    ),
]
_US = [
    ("NVDA", "엔비디아"),
    ("AAPL", "애플"),
    ("MSFT", "마이크로소프트"),
    ("GOOGL", "알파벳"),
    ("AMZN", "아마존"),
    ("META", "메타"),
    ("AMD", "AMD"),
    ("AVGO", "브로드컴"),
    ("ORCL", "오라클"),
    ("NFLX", "넷플릭스"),
]
_TOPICS = [
    "AI 서버 수요",
    "신제품 발표",
    "규제 이슈",
    "주가 급등",
    "실적 기대",
    "자사주 매입",
]
# (days ahead, what, stock)
_EVENTS = [
    (2, "미국 9월 CPI 발표", None),
    (3, "SK하이닉스 3분기 실적 발표", "SK하이닉스"),
    (5, "삼성전자 3분기 실적 발표 컨퍼런스콜", "삼성전자"),
]


def build(now: datetime | None = None) -> bytes:
    """The fake feed file, dated from now."""
    now = (now or datetime.now(UTC)).astimezone(_KST)
    path = Path(tempfile.mkdtemp()) / "feed.db"
    conn = sqlite3.connect(path)
    conn.executescript(_SCHEMA)
    rows = list(_MESSAGES)
    # Over 50 US stock messages across the month, for the narrowing scenario.
    for n in range(60):
        ticker, name = _US[n % len(_US)]
        topic = _TOPICS[n % len(_TOPICS)]
        rows.append(
            (
                12 + n * 11.5,
                "news",
                f"{name} {topic} 관련 소식 {n + 1}.",
                f"{name}({ticker}) {topic} 관련 보도. 자세한 내용은 기사 참고.",
                f"{name}, {ticker}, {topic}, 미국 주식",
                [ticker],
                ["반도체·IT" if n % 2 else "소비재·플랫폼"],
                {"link": f"https://news.example.com/us-{n + 1}"},
            )
        )
    for index, (
        hours,
        type_,
        summary,
        body,
        keywords,
        stocks,
        sectors,
        extra,
    ) in enumerate(rows):
        id_ = f"{index:016x}"
        ts = (now - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%S")
        conn.execute(
            "INSERT INTO messages VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (id_, ts, type_, body, summary, keywords, extra.get("sentiment"), ts),
        )
        conn.executemany(
            "INSERT INTO message_stocks VALUES (?, ?)", [(id_, s) for s in stocks]
        )
        conn.executemany(
            "INSERT INTO message_sectors VALUES (?, ?)", [(id_, s) for s in sectors]
        )
        if "link" in extra:
            conn.execute(
                "INSERT INTO message_links VALUES (?, ?)", (id_, extra["link"])
            )
        if "target" in extra:
            conn.execute(
                "INSERT INTO targets VALUES (?, 1, ?, ?, ?, ?, ?, ?)",
                (id_, *extra["target"]),
            )
    for days, what, stock in _EVENTS:
        conn.execute(
            "INSERT INTO events VALUES (?, ?, ?, ?)",
            # Events hang off a message, as the collector stores them.
            (
                f"{2:016x}",
                (now + timedelta(days=days)).strftime("%Y-%m-%d"),
                what,
                stock,
            ),
        )
    conn.commit()
    conn.close()
    return path.read_bytes()


def fetch(
    state: dict[str, Any],
) -> Callable[[str | None], Awaitable[tuple[str, bytes] | None]]:
    """The host's fetch: one fixed copy; fails while state["feed_down"] is set."""
    content = build()

    async def fake_fetch(version: str | None) -> tuple[str, bytes] | None:
        if state.get("feed_down"):
            raise RuntimeError("feed unavailable (scenario)")
        return None if version == "fake-1" else ("fake-1", content)

    return fake_fetch
