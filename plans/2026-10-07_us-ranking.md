# 미국 주식 순위

날짜: 2026-10-07 (Asia/Seoul)
브랜치: pia-harness `claude/us-ranking`(계획, harness 구현), pia-broker 같은 이름(Broker 구현)
담당: Claude 계획·구현·실호출 확인, Codex 계획·구현 검토와 병합·릴리스·배포
기준: harness main `612640f`(v0.7.5), Broker main `93161dd`, PIA main `23d239758`. 앞 작업: `plans/2026-10-06_us-stocks.md`

## 목표

"미국 시가총액 Top10", "오늘 미국 상승률 상위", "미국 거래대금 상위"를 증권사 값으로 답한다. 지금은 `ranking`이 국내만이라 웹 검색으로 가고(Telegram 2026-10-07: 검색 5번·본문 4번, 약 2분), 값의 기준 시각도 섞인다.

## 결정 (사용자, 2026-10-07)

| # | 항목 | 결정 |
|---|---|---|
| 1 | 범위 | 국내 `ranking`의 `prices` 기준(시가총액·상승률·하락률·거래량·거래대금)을 미국에도. 투자자별·공매도·관심 종목은 KIS 미국 자료가 없어 그대로 국내만 |
| 2 | 미국 시장 | **미국 전체 하나**(`market=us`). KIS 해외 순위는 거래소별(나스닥·뉴욕·아멕스)이라 세 거래소를 받아 합친다. 거래소별 선택은 두지 않는다 |
| 3 | 기간 | **당일만**(국내와 같음). 미국 API의 N일 옵션은 쓰지 않는다 |

## 1. 도구 (harness)

- `market` 설명과 받는 값: ranking `prices`에만 `us`를 더한다. `investors`·`short_selling`·`attention`은 지금처럼 all·kospi·kosdaq. data마다 받는 market 목록을 표 하나로 둔다(quote·history의 `_TARGETS`와 같은 방식).
- 결과 문장: 미국 행은 `엔비디아(NAS:NVDA)`, 가격 USD(KIS 소수 그대로), 시가총액 조 달러·거래대금 억 달러(국내 규칙과 같게, 2026-10-07 사용자 결정), 거래량 주.
- 설명의 "Korean market rankings"를 "Korean or US (us = Nasdaq, NYSE and AMEX together, today only) market rankings"로.

## 2. Broker

- `MarketScope`에 `US`. `/ranking?by=…&market=us`.
- KIS(공식 `examples_llm/overseas_stock`): `GET /uapi/overseas-stock/v1/ranking/…`, `EXCD`=NAS·NYS·AMS, `AUTH`·`KEYB` 빈 값, `VOL_RANG`=0(전체).

| 기준 | API | tr_id | 추가 인자 | 정렬 값 |
|---|---|---|---|---|
| market_cap | `market-cap` | `HHDFS76350100` | | `tomv` 큰 순 |
| gainers / losers | `updown-rate` | `HHDFS76290000` | `NDAY=0`(당일), `GUBN` 1 상승·0 하락 | `rate` 큰 순 / 작은 순 |
| volume | `trade-vol` | `HHDFS76310010` | `NDAY=0` | `tvol` 큰 순 |
| trading_value | `trade-pbmn` | `HHDFS76320010` | `NDAY=0` | `tamt` 큰 순 |

- **합치기(규칙 하나):** 세 거래소를 한 번씩 받아(KIS 3번, 요청 사이 쉬지 않음) 행을 합치고, 그 순위의 기준 값으로 정렬해 요청 개수만큼 자른다. 새 숫자를 만들지 않고 KIS 값으로 정렬만 한다. 지금 "KIS 순서 그대로" 원칙의 예외라 `get_ranking`에 이유를 적는다.
- 한 거래소라도 실패하면 순위 전체를 실패로 한다(일부만 보여 주지 않음).
- 행: `code` = `{excd}:{symb}`(검색·시세와 같은 모양), `name`(KIS 종목명, 없으면 `ename`), `price`=`last`, `change`=`diff`(부호 `sign`), `change_rate`=`rate`, `volume`=`tvol`, `figures`는 그 순위의 값(시가총액 `tomv`, 거래대금 `tamt` 등).
- 응답에 `currency`(`KRW`·`USD`)를 붙여 harness가 단위를 고른다.
- 한 번에 오는 줄 수는 실호출로 본다. 요청 개수(기본 10)보다 적으면 그때 `KEYB` 이어 받기를 더한다(지금은 넣지 않음).

## 3. 실호출 확인 (Broker 배포 후, 승인, 요청 사이 1.5초)

| 조회 | 볼 것 |
|---|---|
| market_cap us 10 | 세 거래소 줄 수, ETF 포함 여부, `tomv` 단위(USD인지), 이름 열 |
| gainers us 10, losers us 10 | 부호, 정렬 |
| volume us 10, trading_value us 10 | `tamt` 단위 |

KIS 요청 15번(5개×3). 실제 주문 없음.

## 4. 테스트와 시나리오

- 오프라인: 세 거래소 합치기·정렬·자르기(기준별 방향), 한 거래소 실패 시 전체 실패, `market=us`는 prices 기준에서만, 미국 행 표기·단위 문장.
- 시나리오(`broker` 세트, 가짜 Broker): "미국 시가총액 Top10" → `ranking` market_cap us, "오늘 미국 상승률 상위 5개" → gainers us 5, "미국 외국인 순매수 상위"(관찰: 국내만이라고 답하는지).

## 5. 순서

계획 → Codex 계획 검토 → Broker·harness 구현 → Codex 구현 검토 → 유료 시나리오 → Broker 배포 → 실호출 → harness 0.7.6 → PIA 핀(프롬프트의 "market rankings"는 그대로 맞음) → Telegram.

## 6. Codex 검토에서 특히 볼 것

1. 세 거래소 합치기를 순위 규칙의 예외 하나로 두는 것보다 단순한 길이 있는지.
2. data마다 받는 market 표(§1)로 국내 순위 동작이 바뀌는 곳이 있는지.
3. KIS 해외 순위 API 인자·필드가 공식 예제와 맞는지.
