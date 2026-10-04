# Broker 조회 도구: 기간 시세 `prices` (종목·지수)

날짜: 2026-10-04 (Asia/Seoul)
브랜치: pia-harness `claude/broker-prices`(계획서와 harness 구현), pia-broker `claude/broker-prices`(Broker 구현)
담당: Claude 계획·구현(harness, Broker), Codex 계획·구현 검토, 릴리스·PIA 연동·IAM·배포는 Codex

## 목표

PIA가 종목과 시장 지수의 기간별 시세를 증권사 값으로 답한다. 차트(PIA 별도 계획)의 데이터 출처이기도 하다. 예:

- "삼성전자 지난 한 달 어땠어?", "SK하이닉스 1년 주가 흐름"
- "코스피 3개월 추이", "코스닥 요즘 어때?"
- "삼성전자 3개월 차트 그려줘"(차트는 PIA가 그린다. 이 도구는 숫자만 준다)

지금은 `quote`가 한 시점의 값(현재가, 52주 최고·최저)만 줘서 흐름을 물으면 웹 검색으로 답한다.

## 결정 (사용자, 2026-10-04)

- 새 도구가 아니라 기존 `broker` 도구의 action 하나: `prices`. `quote`와 결과 모양이 전혀 다르므로 `quote`에 기간을 붙이지 않고 나눈다.
- 종목과 지수 둘 다. 종목은 `name`, 지수는 종목 없이 `market`(`kospi`·`kosdaq`; `all`은 지수가 없으므로 안 됨). 시장 전체 `investors`와 같은 규칙이다.
- 기간은 모델이 고르고 단위는 기간이 정한다. 어느 기간이든 수십 줄 안팎이 되게 해 모델이 읽고 차트에 옮겨 쓰는 숫자와 Context 크기를 작게 둔다(일 단위 5년은 약 1,225줄이라 택하지 않음).

  | `period` | 단위 | 줄 수(대략) |
  |---|---|---|
  | `1m` | 일 | 21 |
  | `3m`(기본) | 일 | 62 |
  | `6m` | 주 | 26 |
  | `1y` | 주 | 52 |
  | `3y` | 월 | 36 |
  | `5y` | 월 | 60 |

- 단위는 모델과 사용자에게 알린다: 도구 설명에 기간별 단위를 적고, 결과 첫 줄에 단위를 밝히며 "사용자가 일 단위를 원했는데 아니면 그렇다고 말하라"를 넣는다. 6개월 일 단위는 첫 버전에서 열지 않고 써 보며 판단한다.
- KIS 한 번 호출로 기간을 다 못 받으면 Broker가 이어 부른다(아래). 모델은 이어 부르기를 모른다.
- 시가총액 추이처럼 도구에 없는 데이터를 위한 규칙은 넣지 않는다. 실제 문제가 보이면 그때 다룬다.

## KIS API (공식 저장소 `examples_llm/domestic_stock`, 실전 Postman 샘플 v2.6에서 확인)

| | 종목 | 지수 |
|---|---|---|
| 경로 | `/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice` | `/uapi/domestic-stock/v1/quotations/inquire-daily-indexchartprice` |
| TR ID | `FHKST03010100` | `FHKUP03500100` |
| 시장 코드 | `FID_COND_MRKT_DIV_CODE=J`(KRX) | `U`(업종) |
| 대상 | `FID_INPUT_ISCD`=종목코드 | `0001` 코스피 종합, `1001` 코스닥 종합(시장 `investors`와 같은 코드) |
| 기간 | `FID_INPUT_DATE_1` 시작, `FID_INPUT_DATE_2` 종료(YYYYMMDD), `FID_PERIOD_DIV_CODE` `D`/`W`/`M`/`Y` | 같음 |
| 수정주가 | `FID_ORG_ADJ_PRC=0`(수정주가: 액면분할에도 추이가 끊기지 않음) | 없음 |
| 한 번 응답 | **최대 100줄** | **최대 50줄** |
| 다음 묶음 | 받은 것 중 가장 오래된 날짜의 전날을 `FID_INPUT_DATE_2`에 넣어 다시 호출(공식 안내) | 같음 |
| 줄(`output2`) | `stck_bsop_date`, `stck_oprc`, `stck_hgpr`, `stck_lwpr`, `stck_clpr`, `acml_vol`(주) | `stck_bsop_date`, `bstp_nmix_oprc`, `bstp_nmix_hgpr`, `bstp_nmix_lwpr`, `bstp_nmix_prpr`(종가) |

지수 50줄 한도 때문에 지수 `3m`(약 62줄), `1y`(약 52줄), `5y`(60줄)는 두 번 호출이 된다. 종목은 모든 기간이 한 번이다.

## 설계

### Broker (pia-broker)

- `models.py`:
  - `PricePeriod`(`1m 3m 6m 1y 3y 5y`)와 기간별 단위(`D`/`W`/`M`). 시작일은 조회일(KST)에서 개월·년을 뺀 같은 날(말일은 그 달 말일로)이다.
  - `PriceBar(date, open, high, low, close, volume)`: 날짜 `YYYYMMDD`, 가격은 KIS 값 그대로(종목 원, 지수 포인트), `volume`은 종목만(지수는 None).
  - `PriceHistory(code | None, market | None, period, unit, bars, observed_at)`: `bars`는 KIS 순서(최근 먼저).
- `connectors/kis.py`: `get_stock_prices(instrument, period)`, `get_index_prices(market, period)`. 둘 다 같은 이어 부르기 함수 하나를 쓴다:
  1. 종료일 = 조회일, 시작일 = 기간 시작일로 부른다.
  2. 날짜가 비어 있는 줄은 버린다(KIS가 범위 밖을 빈 칸으로 채우는 경우 대비). 남은 줄이 없으면 멈춘다.
  3. 날짜는 앞 묶음까지 포함해 엄격하게 줄어들어야 한다(겹침·순서 어긋남은 schema error).
  4. 가장 오래된 날짜가 시작일 이하이면 멈춘다. 아니면 그 전날을 종료일로 다시 부른다.
  5. 호출 상한 3번. 지금 기간표에서 필요한 최대는 2번이다. 상한에 닿으면 schema error(일부만 돌려주지 않음).
  6. 시작일보다 앞선 줄은 버린다.
  - 중간 호출이 실패하면 조회 전체가 실패한다. 반쪽 추이를 돌려주지 않는다.
  - 상장한 지 얼마 안 된 종목은 다음 호출이 빈 응답이라 멈추고, 있는 만큼만 준다(결과에 줄 수가 나온다).
- `orders.py` `TradingService.prices(member_id, code | None, market, period)`: 종목이면 종목 시세, 아니면 지수. 지수에 `all`은 400.
- 내부 경로 `GET /internal/members/{member_id}/prices?code=…&period=…` 또는 `?market=kospi|kosdaq&period=…`. 기간 생략 시 `3m`. JSON: `code`, `market`, `period`, `unit`(`day`/`week`/`month`), `observed_at`, `rows`(최근 먼저, `date`/`open`/`high`/`low`/`close`/`volume`).
- 인프라: Broker 템플릿에 새 경로(GET, IAM 인증, `investors`와 같은 방식), PIA bootstrap의 Bot 역할 허용 ARN 패턴과 정책에 `prices` 추가. AWS 변경이라 Codex가 사용자 승인 후 적용한다.

### harness (`broker_read.py`)

- `_ACTIONS`에 `prices`. 도구 설명(`BROKER_DESCRIPTION`)에 "기간별 시세(종목·코스피·코스닥 지수)"를 더한다.
- 인자 설명:
  - `action` 설명: `prices: price history over a period, for one stock (with name) or the KOSPI/KOSDAQ index (without name, with market). 1m and 3m give daily rows, 6m and 1y weekly rows, 3y and 5y monthly rows.`
  - `name`: prices에도 쓴다고 적는다.
  - `market`: "For prices without a name: kospi or kosdaq (the index); all is not allowed."
  - `period`: 값 목록을 공매도 값과 합친다(`1d 2d 3d 4d 1w 2w 3w 1m 2m 3m 6m 1y 3y 5y`). 설명에 action별 허용 값을 적는다. 검사는 지금처럼 action이 쓰는 인자만 본다(`_argument_problem`): prices는 `1m 3m 6m 1y 3y 5y`만, 공매도는 지금 값만.
- 결과 글(예):

  ```
  Prices of 삼성전자(005930) over the last 3 months as daily rows (one per trading day), oldest first, 62 rows, split-adjusted, looked up at 2026-10-04 14:05 KST. The newest row may be a session still in progress; its close is then the price at the lookup time. If the user asked for daily rows and these are not daily, say so.
  - 2026-07-06: open 71,200 KRW; high 72,000 KRW; low 70,900 KRW; close 71,800 KRW; volume 12,345,678 shares
  …
  ```

  - 지수는 `points`, 거래량 없음.
  - 줄이 날짜를 가지므로 `_NO_SESSION_DATE` 문구는 쓰지 않는다. 진행 중인 장의 줄만 따로 밝힌다.
  - 순서: Broker는 KIS 순서(최근 먼저), harness는 오래된 것부터 적는다(추이·차트는 왼쪽에서 오른쪽으로 읽는다).
- 계산하지 않는다(등락률·평균 없음). 모델이 필요하면 줄에서 읽는다.
- 결과 크기: 줄당 약 100자, 최대 약 65줄 → 약 7,000자.

### PIA (별도 계획에서)

- harness 핀 갱신, `BROKER_READ_PROMPT`에 기간 시세를 더한다. 차트 계획(`pia` 저장소)과 함께 다룬다.

## 실호출 확인 (사용자 승인 후, 본인 회원)

부를 것(조회만, KIS 요청 최대 9번):

1. 종목 `005930` `1m`(일, 1번)
2. 종목 `005930` `6m`(주, 1번): 주 단위 줄의 날짜가 주의 어느 날인지
3. 종목 `005930` `5y`(월, 1번): 월 단위 날짜, 수정주가
4. 지수 `kospi` `3m`(일, 2번): 이어 부르기 경계에 겹침·빠짐이 없는지
5. 지수 `kosdaq` `1y`(주, 2번)
6. 지수 `kospi` `5y`(월, 2번)

확인할 것: 단위 날짜 표기, 지수 값 단위(포인트, 소수), 빈 줄 동작, 장중이면 오늘 줄이 진행 중 값인지. 결과에 따라 결과 글의 단위 설명을 고친다.

## 검증 (오프라인)

- Broker: 기간별 단위·시작일(말일 포함), 이어 부르기(한 번, 두 번, 상한, 빈 응답, 빈 날짜 줄, 겹침·역순 거부, 중간 실패 시 전체 실패), 시작일 이전 줄 버림, 지수 `all` 400, 경로·JSON, 회원·연결 검사(기존 경로와 같음).
- harness: 인자 검사(prices 기간 값, 지수 market 필수·`all` 거부, 공매도 기간과 섞이지 않음), 결과 글(단위 문장, 오래된 순, 지수 포인트·거래량 없음, 줄 수), 실패 문구.

## 순서

1. 계획 → Codex 계획 검토 → 반영
2. Broker 구현 → harness 구현 → Codex 구현 검토
3. Broker 병합·배포·IAM(Codex, 사용자 승인) → 실호출 확인(사용자 승인) → 결과 반영
4. harness 릴리스(버전은 그때 사용자에게 묻는다) → PIA 연동은 차트 계획과 함께

## Codex 검토에서 특히 볼 것

1. 새 경로 `prices`(IAM 추가) vs 기존 경로 재사용. 계획은 새 경로다(`account`+`code` → `buyable`처럼 한 경로에 다른 결과를 싣는 방식은 결과 모양이 너무 달라 택하지 않음).
2. 이어 부르기의 멈춤 조건과 상한이 KIS 동작에 맞는지.
3. 기간 시작일 계산(조회일 기준 같은 날, 말일 처리)과 KIS 주·월 단위 날짜 의미.
