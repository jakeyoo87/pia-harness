# 시장 소식 검색 시나리오 테스트 (2026-10-17)

`market_feed_search`를 넣었을 때 모델이 소식·의견·목표주가·일정을 이 DB에서 먼저 찾고, 다른 질문에는 맞는 도구(broker, 뉴스, 웹)를 고르는지 확인한 수동 테스트다. `python -m tests.manual.smoke_flow --set market_feed`, 시나리오 `scenarios/market_feed.json`, 계획 `plans/2026-10-17_market-feed-search.md`.

## 구성

| 항목 | 값 |
|---|---|
| Harness | `claude/market-feed-search` 1차 `7e47119`, 재실행 `1ea467d` |
| 모델 | OpenRouter `openai/gpt-6-luna` (PIA 운영과 같음) |
| 도구 | `market_feed_search`(맨 앞) + 뉴스·웹 검색(실제 키)·본문 추출 + broker 조회·주문(가짜) + `past_financials`(가짜 공시), System에 PIA의 broker·주문 문장과 `market_feed_search` 한 줄 사본 |
| 시장 소식 DB | **가짜**(`fake_market_feed.py`: 지어낸 71건, 실행 시각 기준 KST, 같은 소식 한 쌍, MU 함정, 목표주가·일정, 미국 종목 60건). 실제 S3 호출 없음 |
| 키 | Secrets Manager에서 프로세스 안으로만 읽음. 로그에 키 값 없음 확인 |

## 1차 결과: 15개 중 12 PASS, 3 CHECK

| # | 질문 | 모델 호출 | 결과 |
|---|---|---|---|
| m15 | 요즘 반도체 소식 어때? (DB 받기 실패, 사본 없음) | market_feed_search → news_search → web_extract | PASS. 쓸 수 없다는 결과를 보고 뉴스로 넘어감 |
| m1 | 요즘 반도체 분위기 어때? | market_feed_search ×2 | PASS. 증권사 의견·뉴스 이름표, 기사 링크 인용 |
| m2 | 삼성전자 최근 증권사 의견은? | market_feed_search ×2 | PASS. "증권사 의견", 증권사 이름 없음 |
| m3 | SK하이닉스 목표주가 바뀐 거 있어? | market_feed_search ×2 (targets) | PASS |
| m4 | 이번 주 실적 발표 일정 알려줘 | web_search 15번·web_extract 3번(62초) | **CHECK**. DB를 건너뜀 |
| m5 | 오늘 마이크론 소식 있어? | market_feed_search ×2 (message_stocks의 MU) | PASS. MUSK 함정에 걸리지 않음 |
| m6 | 그 소식 자세히 알려줘 | web_extract(기사 링크) → news_search | CHECK. 뉴스는 원문을 읽는 게 맞는 행동(가짜 링크라 실패) — 시험을 바꿈 |
| m7 | KODEX 200 구성 종목 비중 알려줘 | web_search → web_extract | PASS. DB 없이 바로 웹 |
| m8 | 작년 3월에 삼성전자 무슨 일 있었어? | news_search → web | PASS. DB 없이 |
| m9 | 일본 반도체 장비 수출 규제 관련 소식 있어? | market_feed_search → news_search → web | PASS |
| m10 | 엔비디아 지금 주가 얼마야? | broker | PASS |
| m11 | 내 보유 종목 관련 소식 있어? | broker → market_feed_search ×2 | PASS |
| m12 | 삼성전자 잠정실적 나왔어? | news_search → web_extract | **CHECK**. DB를 건너뜀 |
| m13 | 최근 한 달 미국 주식 소식 다 정리해줘 | market_feed_search ×3 → 뉴스·웹 | PASS(관찰: 가짜 요약이 단조로워 뉴스까지 감, 110초) |
| m14 | 대한전선 목표가 어떻게 됐어? (같은 소식 두 건) | market_feed_search ×2 | PASS. 한 건으로 묶어 답함 |

## 원인과 수정 (`1ea467d`)

- m4: 도구 설명과 System 한 줄이 "소식과 의견"만 말해서 일정은 DB에 없다고 판단 → 범위를 "소식·의견·목표주가·일정(실적 발표일, 경제 지표 발표)"으로.
- m12: `past_financials` 설명이 "잠정실적은 news_search·web_search로"라고 도구 이름을 적음 → "최근 소식을 검색하라"로 이름을 빼서 검색 순서 규칙을 따르게.
- m6: 링크 없는 증권사 의견의 근거를 묻는 질문으로 바꿈("아까 SK하이닉스 목표가 올린 근거 자세히 알려줘").

## 재실행 (m3·m4·m6·m12): 모두 기대대로

- m4: market_feed_search(events) → web_search·web_extract로 날짜 확인. DB 먼저.
- m12: market_feed_search → news_search → web. DB 먼저.
- m3: 두 번째 검색에서 본문까지 읽음 → m6은 이미 읽은 본문으로 도구 없이 근거를 정확히 답함(HBM3E 비중, 서버 D램 가격, 4분기 영업이익). 판정상 CHECK였으나 맞는 행동이라 m6을 관찰로 바꿈.

## 판단

- 소식·의견·목표주가·일정은 DB를 먼저 쓰고, 계좌·시세는 broker, 웹에만 있는 자료와 30일보다 오래된 일은 DB를 거치지 않았다. 주문 0건.
- 출처는 "증권사 의견"·"뉴스"(기사 링크)로 표시했고 채널·증권사·사람 이름은 없었다.
- 남은 확인: 실제 DB 사본으로 m1~m4(출시 전, 승인 뒤).
