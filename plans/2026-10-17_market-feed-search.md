# 시장 소식 검색 도구 `market_feed_search`

- 작성: 2026-10-17, Claude (EC2). 구현: Claude / 검토: Local Codex(계획 → 구현). 출시·배포: Local Codex(사용자 승인).
- 저장소: pia-harness(도구), 그다음 pia-agent(S3 받기·등록). 기준 harness main `398c1f5`(v0.7.16).
- DB 약속: pia-collector `docs/research-db-spec.md`(main `e3a8404`, schema_version 3).

## 배경

- 사용자 Mac의 수집기가 투자 Telegram 채널 18개를 2시간마다 모아 SQLite DB를 만든다(유형: 뉴스 공유, 증권사 코멘트, 커뮤니티 의견, 시장 알림).
  - 이 DB는 S3 객체 하나(`telegram/research.db.gz`)로 덮어쓴다. 지금 약 0.23MB, 30일 보관이라 수 MB까지 커진다.
- 사용자 목표: 검색이 필요한 질문은 **이 DB → 뉴스(`news_search`) → 웹(`web_search`)** 순서로 찾는다. 웹만 답할 수 있는 질문은 바로 웹으로 간다.
- DB에는 채널·증권사·사람 이름이 없다(수집기가 지움). Bot에는 S3 객체 읽기 권한이 이미 있다(pia `infra/collector-research.template.json`).

## 사용자 결정 (2026-10-17, 한 단계씩)

1. 새 읽기 도구, 이름 `market_feed_search`. "telegram"·"research"는 넣지 않는다(답에 출처를 이름표로만 쓰기 위해, 그리고 내용이 드러나게).
2. **LLM이 검색문(SQL)을 직접 쓴다.** 도구 설명에 DB 구조를 알려 주고, 인자는 SQL 하나다. 조건 칸을 하나하나 만들지 않는다. 목표주가·일정 표도 같은 방식으로 쓴다.
3. 찾는 곳은 OR로 넓게: 본문 + 키워드, 특정 종목은 종목 표(짧은 티커 MU·BE 같은 잘못된 일치를 피함). AND는 질문이 좁힐 때만(기간, 유형).
4. 요약을 먼저 보고, 필요한 글만 본문을 읽는다(10건 정도).
5. 한도: 결과 최대 **50행**, 전체 **20,000자**. 넘으면 잘렸다고 알려 AI가 좁혀 다시 찾는다. 글 하나의 본문은 자르지 않는다(가장 긴 본문 약 2,500자).
6. S3 받기는 **도구 안에서**, 다만 S3 접근 자체는 host(PIA)가 넣어 주는 함수가 한다. harness는 AWS·boto3를 모른다(다른 도구와 같은 규칙).
7. **검색할 때마다** 바뀌었는지 확인하고, 바뀌었을 때만 받는다.
8. 출처 규칙은 **결과 맨 위 안내문**에 둔다(도구 없이 부르는 마지막 답변에서도 대화에 남은 결과로 보이게. 과거 실적 도구와 같음). 답에는 `뉴스`(기사 링크가 있으면 평소처럼 인용) / `증권사 의견` / `커뮤니티 의견` / `시장 알림`만 쓰고, 채널·증권사·사람 이름은 쓰지 않는다.
9. 검색 순서는 두 곳에 나눠 적는다(0.7.15 규칙): 자세한 기준은 도구 설명에, 고르기 한 줄은 PIA System 지침에(도구를 등록할 때만).
10. 같은 소식을 다른 문장으로 쓴 중복은 지금 묶지 않는다(AI가 답에서 묶는다).
11. 시나리오 테스트는 Claude가 주도한다.

## harness 변경

### 새 파일 `src/pia_harness/market_feed.py`

```python
class MarketFeedSearchTool:
    def __init__(self, *, fetch: Callable[[str | None], Awaitable[tuple[str, bytes] | None]],
                 clock: Callable[[], datetime] | None = None) -> None: ...
    def tool(self) -> ReadToolDefinition: ...   # name market_feed_search, schema {sql: string}
    async def aclose(self) -> None: ...         # 임시 폴더 정리
```

- **`fetch(version)`**(host가 줌): 마지막으로 받은 버전 표시(S3 ETag)를 받아서, 바뀌었으면 `(새 버전, SQLite 파일 내용)`을, 같으면 `None`을 돌려준다. 실패하면 예외. 압축 풀기도 host 쪽 일이다(gzip은 S3 운반 방식).
- **받아 두기:** 도구가 만든 임시 폴더에 파일 하나를 둔다. 새 내용은 임시 파일에 쓴 뒤 `os.replace`로 바꿔 끼운다(이미 열린 검색은 옛 파일을 끝까지 읽음). 확인·교체는 `asyncio.Lock` 하나로 묶는다. 읽기는 동시에 3개까지 돌 수 있어서, 같은 파일을 두 번 받지 않게 하기 위해서다. Bot을 다시 시작하면 첫 검색에서 다시 받는다(설정할 경로 없음).
- **확인 실패:** 받아 둔 파일이 있으면 그것으로 검색하고 결과 맨 위에 "최신 확인 실패, 마지막으로 받은 자료"라고 적는다. 받아 둔 파일이 없으면 검색하지 않고 "지금 쓸 수 없음, news_search·web_search로"를 결과로 준다.
- **실행:** `sqlite3`(표준 라이브러리, 새 의존성 없음)로 `file:...?mode=ro` 읽기 전용 연결을 검색마다 연다. 이벤트 루프를 막지 않게 `asyncio.to_thread`로 돌린다.
  - 한 번에 한 문장: `sqlite3`의 `execute`가 여러 문장을 거절한다.
  - **조회만 허용(규칙 하나):** `set_authorizer`로 조회 동작(`SQLITE_SELECT`, `SQLITE_READ`, `SQLITE_FUNCTION`, `SQLITE_RECURSIVE`)만 허용하고 나머지는 모두 거절한다. 읽기 전용 연결만으로는 `VACUUM INTO`·`ATTACH`가 새 파일을 만들 수 있다(Codex 재현). SQL 글자를 분석하지 않는다.
  - **값 크기 한도:** `setlimit(SQLITE_LIMIT_LENGTH, 1_000_000)`. 결과 한도는 계산 중 큰 값(예: `randomblob(8000000)`)을 막지 못한다. 가장 긴 본문이 약 2,500자라 정상 검색에는 영향이 없다.
  - 시간: `set_progress_handler`로 2초가 지나면 멈춘다. 대략적인 한도이며 엄격한 시간·메모리 격리는 아니다(별도 프로세스는 두지 않는다).
  - 행은 `fetchmany(51)`로 받아 50행을 넘으면 "50행에서 잘림, 좁혀서 다시"라고 적는다. 행을 차례로 적다가 20,000자를 넘으면 거기서 멈추고 같은 안내를 붙인다.
  - SQL 오류는 SQLite 메시지를 그대로 결과로 준다(예상 가능한 실패, 모델이 고쳐 다시 씀). DB 내용은 결과로만 나가고 지침이 아니다.
- **이름 한 번 더 지우기(2026-10-10 결정):** 결과 글에서 `t.me` 링크와 `@아이디`를 코드로 지운다(수집기가 이미 지우지만 같은 규칙을 한 번 더).
- **결과 모양:**
  ```
  market_feed_search (시장 소식): 투자 채널의 최근 30일 소식·의견. 검색 시각 2026-10-17T22:40 KST. 행 12개.
  [출처 규칙 한 줄: 네 가지 이름표만, 뉴스는 기사 링크 인용 가능, 채널·증권사·사람 이름 금지]
  1. id=…; ts=2026-10-17T21:50:46; type=broker; summary=…
  2. …
  ```

### 도구 설명 (모델이 보는 사양, 영어로 작성)

- **무엇:** 최근 30일 투자 채널의 짧은 소식과 의견 DB(뉴스 공유, 증권사 코멘트, 커뮤니티 의견, 시장 알림). 계좌·시세·공시 실적은 없다.
- **언제:** 시장·섹터·종목의 소식과 의견을 찾을 때 news_search·web_search보다 **먼저**. 부족하면 news_search, 그다음 web_search.
  - 답이 특정 문서나 페이지에 있는 자료라면 바로 web_search: ETF 구성 종목·비중, 회사 공식 페이지·공시 원문, 제품 설명, 표로 된 통계, 30일보다 오래된 일, 사용자가 준 링크.
- **구조:** 표 6개와 칸, 값.
  - `messages(id, ts, type, body, summary, keywords, sentiment, stored_at)`, `type` ∈ news/broker/community/alert. `keywords`는 JSON 배열이 아니라 ", "로 이은 TEXT(`LIKE`로 찾음)
  - `message_stocks(id, stock)`: 국내 공식 종목명, 미국 티커
  - `message_sectors(id, sector)`: 고정 8개 값 그대로 — 반도체·IT, 2차전지·전기차, 바이오·헬스케어, 금융, 소비재·플랫폼, 에너지·방산·산업재, 매크로, 기타
  - `message_links(id, url)`: 뉴스 기사 링크
  - `targets(id, seq, stock, target_price, previous_target_price, currency, rating, previous_rating)`
  - `events(id, event_date, what, stock)`
- **시각:** 모든 시각은 오프셋 없는 KST 문자열(`YYYY-MM-DDTHH:MM:SS`, `event_date`는 `YYYY-MM-DD`). 오늘은 `date('now', '+9 hours')`. 기간은 문자열 비교로(`ts >= '2026-10-15'`).
- **찾는 법:**
  - 찾는 곳은 OR로 넓게: `body LIKE`·`keywords LIKE`, 종목은 `message_stocks`에서 정확히. 짧은 티커는 본문 LIKE를 쓰지 않는다.
  - 최신순 `ORDER BY ts DESC`.
  - 먼저 `id, ts, type, summary`만 뽑고, 필요한 글만 `WHERE id IN (...)`으로 `body`를 읽는다(10건 정도).
  - 결과는 50행·20,000자에서 잘린다.
- **주의:** 같은 소식이 여러 채널에 다른 문장으로 있을 수 있다(답에서 묶는다). 미국 외 해외 종목명은 제각각이라 키워드·본문으로 찾는다.

### 그 밖

- `__init__.py`에 `MarketFeedSearchTool` 내보내기.
- README 6.0 표의 읽기 도구 목록, 새 절 6.2.5(위 내용 요약, 이 계획 링크).
- 버전 0.7.17.

## 테스트

**단위(`tests/test_market_feed.py`, 가짜 fetch, 작은 SQLite를 만들어 사용)**
- 처음 검색에서 받고, 같은 버전이면 다시 받지 않음, 바뀌면 교체
- 동시에 3개 검색: 변경 확인(fetch)은 3번, 같은 파일 받기·교체는 1번
- 확인 실패: 받아 둔 파일로 검색하고 안내 / 파일이 없으면 쓸 수 없다는 결과
- 50행·20,000자에서 잘리고 안내
- SQL 오류 메시지가 결과로, 여러 문장은 거절
- 조회만 허용: 일반 SELECT·CTE는 통과, `VACUUM INTO`·`ATTACH`·PRAGMA·쓰기는 거절되고 파일이 생기지 않음
- 값 크기 한도(`randomblob` 큰 값 거절), 2초 한도
- `t.me`·`@아이디` 제거
- 결과 맨 위의 출처 규칙
- 설명에 시각·OR·순서 규칙

**시나리오(`tests/manual`, 유료, 사용자 승인 뒤)**
- `fake_market_feed.py`: 지어낸 글로 가짜 DB를 만든다. 실행 시각 기준 오늘·어제·지난주. 중복 소식 한 쌍, MU, 목표주가·일정 포함.
- `scenarios/market_feed.json`, `smoke_flow --set market_feed`(가짜 broker와 PIA의 System 한 줄 사본 포함).

| # | 질문 | 확인 |
|---|---|---|
| m1 | 요즘 반도체 분위기 어때? | market_feed_search 먼저, 요약으로 답 |
| m2 | 삼성전자 최근 증권사 의견은? | 종목 표 + type=broker, "증권사 의견" 표시, 이름 없음 |
| m3 | SK하이닉스 목표주가 바뀐 거 있어? | targets 사용 |
| m4 | 이번 주 실적 발표 일정 알려줘 | events, KST 날짜 |
| m5 | 오늘 마이크론 소식 있어? | message_stocks의 MU, 오늘 |
| m6 | 그 소식 자세히 알려줘 (m5 다음) | body 읽기 |
| m7 | KODEX 200 구성 종목 비중 알려줘 | market_feed_search 없이 web_search |
| m8 | 작년 3월에 삼성전자 무슨 일 있었어? | 30일보다 오래된 일: market_feed_search 없이 web_search(또는 news_search) |
| m9 | 일본 반도체 장비 수출 규제 소식 있어? (DB에 없음) | DB → news_search → (필요하면) web_search |
| m10 | 엔비디아 지금 주가 얼마야? | broker, DB 아님 |
| m11 | 내 보유 종목 관련 소식 있어? | broker 계좌 → 그 종목들로 DB |
| m12 | 삼성전자 잠정실적 나왔어? | past_financials가 아니라 DB → news |
| m13 | 최근 한 달 미국 주식 소식 다 정리해줘 | 50행 넘으면 좁혀 다시 |
| m14 | 대한전선 목표가 어떻게 됐어? (중복 한 쌍) | 하나로 묶어 답 |
| m15 | 요즘 반도체 소식 (fetch 실패, 받아 둔 파일 없음) | news_search로 넘어감 |

- 판정: 도구 순서·쓴 표·출처 표시·이름 없음은 자동 PASS/FAIL. SQL 차단·처음 받기는 단위 테스트에서 본다.
- 마지막에 실제 DB(S3 사본)로 m1~m4를 한 번 더 확인(승인 뒤).

## PIA 변경 (harness 출시 뒤, 같은 방향으로 따로 구현·검토)

- `fetch` 함수: boto3 `get_object(Bucket, Key, IfNoneMatch=버전)`. 처음(`fetch(None)`)에는 `IfNoneMatch` 인자를 아예 넣지 않는다(None을 넣으면 SDK가 거절)
  - 304(바뀌지 않음)는 SDK 오류 응답으로 오므로 잡아서 `None`
  - 아니면 gzip을 풀어 `(ETag, 내용)`
  - `asyncio.to_thread`로 감싼다.
  - 권한은 이미 있는 GetObject 하나다.
- 설정: 버킷 이름(필수 설정 하나, 다른 설정과 같은 방식), 키는 고정 `telegram/research.db.gz`.
- 등록: 읽기 도구 목록에서 `news_search` 앞. 종료 때 `aclose()`.
- System 한 줄(도구를 등록할 때만, BROKER_READ_PROMPT와 같은 방식): "For recent news and opinions on markets, sectors or stocks, use market_feed_search first when it applies, before news_search and web_search."
- Telegram 진행 표시: `"market_feed_search": "시장 소식 검색"`.
- harness 고정 0.7.17, README, 테스트, bot-only 배포.

## 순서

1. 계획 검토(Local Codex)
2. harness 구현·단위 테스트
3. 시나리오(유료, 승인)
4. 구현 검토
5. v0.7.17 출시
6. PIA 구현
7. 검토
8. bot-only 배포(승인)
9. 사용자 Telegram 확인

DB의 KST 시각은 Mac 적용이 끝났고 S3에는 다음 업로드(2026-10-18 00:00)부터 반영된다. 시나리오는 가짜 DB라 영향이 없고, 출시 전에 실제 사본이 KST인지 확인한다.

## 범위 밖

- 같은 소식 묶기, 의미 검색·전문 검색 색인(쓰면서 부족할 때)
- 아침 브리핑, 투자 계획 점검에 쓰는 것
- DB 구조 버전 검사(구조가 바뀌면 이 계획과 도구 설명을 함께 고친다)

## Local Codex 계획 검토 반영 (2026-10-17, 기준 a5ae537)

- [차단] 읽기 전용 연결에서도 `VACUUM INTO`·`ATTACH`가 파일을 만든다 → `set_authorizer`로 조회 동작만 허용(규칙 하나).
- [차단] 결과 한도는 계산 중 메모리를 막지 못한다 → `SQLITE_LIMIT_LENGTH` 1MB. 2초 progress handler는 대략적 한도로만 설명.
- 동시 검색 테스트는 확인 3번·받기 1번으로 나눠 본다.
- PIA `fetch(None)`은 `IfNoneMatch`를 넣지 않고, 304는 None.
- System 한 줄에 "when it applies"(30일 이전 자료는 바로 웹이라는 설명과 부딪히지 않게).
- 도구 설명에 섹터 8개 값과 `keywords` 형식(TEXT).
- m8을 "30일보다 오래된 일 → 바로 웹" 검사로 바꿈.
