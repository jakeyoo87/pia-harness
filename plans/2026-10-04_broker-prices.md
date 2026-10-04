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


## Codex 계획 검토

검토일: 2026-10-04 (Asia/Seoul). 대상 `4640530`, 대조 harness main v0.7.2, Broker main `9b2cb69`, 현재 코드·README와 KIS 공개 공식 예제. 실제 KIS는 호출하지 않았다.

### 판정

`prices` action·종목/지수 구분·기간별 고정 단위·새 경로 방향은 적절하다. 아래 이어 부르기 근거·종료 판정과 실제 확인 호출 예산을 보완한 뒤 구현한다. 문서 검토이며 구현 완료·AWS 승인 판정은 아니다.

### API·경로

- [종목 공식 예제](https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/domestic_stock/inquire_daily_itemchartprice/inquire_daily_itemchartprice.py)의 경로·TR ID, J, D/W/M, 수정주가 0 및 최대 100건은 계획과 맞는다. 수정주가는 액면분할 등 가격 조정이며 배당 재투자 수익률이라는 뜻은 아니다.
- [지수 공식 예제](https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/domestic_stock/inquire_daily_indexchartprice/inquire_daily_indexchartprice.py)의 경로·TR ID·U·시작/종료일 인자는 맞다. 그러나 현재 예제는 `tr_cont` M/F → N 방식으로 이어 부른다. 따라서 두 API 모두 '전날로 다시 조회가 공식 안내'라고 단정하지 않는다. 50건 한도·전날 방식의 정확한 공식 근거(문서 절 또는 고정 커밋)를 계획에 연결하고, 승인된 실제 확인에서 그 방식의 진행·종료를 검증한다. 최신 예제를 그대로 복제하거나 날짜 방식과 tr_cont를 동시에 붙일 필요는 없다.
- 새 `/internal/members/{member_id}/prices` 권고. 기존 quote/account의 응답 모양과 입력 의미를 늘리는 것보다 분명하며, IAM은 정확한 GET prices ARN 한 개만 추가하면 된다. Broker는 기존 VERIFIED 연결 검사·409/502/503 계약을 쓴다. 사용자 승인 전 AWS 정책·배포는 바꾸지 않는다.

### 이어 부르기 규칙에서 보완할 점

- **완료를 나타내는 응답 조건을 먼저 확정한다.** 현재 '최저 날짜가 시작일 이하'만으로는 시작일이 휴장일이거나 주·월 기준일이 맞지 않을 때 이미 전 범위를 받아도 다음 빈 조회가 필요하다. 이 경우 지수 두 번이 아닌 세 번, 종목 한 번이 아닌 두 번일 수 있다. 3회째에 충분히 받았으나 아직 종료를 증명하지 못한 경우를 성공으로 숨기지 않는 현재 원칙은 맞다.
- 선택한 API 방식의 종료 표식/빈 응답 또는 확인된 최대 응답 수 대비 짧은 page 중 무엇을 완료 근거로 쓸지 공식 근거·실제 확인으로 정한다. '빈 응답이면 끝'은 `rt_cd` 정상·유효한 output2 목록인 경우에만 적용하고, output 누락·오류를 빈 성공으로 바꾸지 않는다. 동일 날짜 반복/앞으로 이동은 진행 불가로 실패시키면 충분하며 추정한 page 예외를 계속 추가하지 않는다.
- 상한은 **최초 포함 총 3회**로 명시한다. 끝 판정을 한 뒤에 상한 초과 여부를 판단한다. 중간 오류·진행 불가·끝을 확인하기 전 예산 소진은 전체 실패라는 공통 규칙을 유지한다. 부분 시세를 완전한 기간처럼 돌려주지 않는다.
- 가장 오래된 날짜와 다음 종료일 계산은 전체 유효 page에서 하고, 시작일 이전 행의 제거는 완료 판정 뒤 적용한다. 날짜 없는 빈 padding만 제외하며 잘못된 날짜가 있는 행을 조용히 버리지 않는다. 페이지 순서·날짜를 재정렬해서 깨진 진행을 숨기지 않는다.
- Lambda 30초, 현재 transport 한 요청 8초, harness 기본 35초다. 3회면 가격 요청만 최대 24초이고 Token/인증 갱신이 더해질 수 있다. 실제 확인에서 전체 지연을 측정하고 같은 전체 시간 예산 안에서 실패시키는 계약을 정한다. 이번 작업을 위해 timeout·retry·Lambda 시간을 일괄 확대하지 않는다.

### 기간·주·월 기준·가격 타입

- KST 조회일을 한 번 고정하고 달/년을 빼며 해당 달 말일로 clamp하는 시작일 계산은 적절하다. 하루씩 30/365일을 빼는 계산으로 바꾸지 않는다. 양끝 포함 범위와 기본 3m를 명시한다.
- 주·월 행 날짜는 KIS가 준 기준일을 그대로 쓴다. 확인 전 주 시작/끝 또는 월말로 바꾸거나 하루의 OHLC라고 말하지 않는다. 첫 구간·마지막 구간이 부분 주/월인지도 실제 확인 대상으로 넣고, 결과 첫 줄은 '주봉/월봉 기준일'로 밝힌다. 최근 행의 close가 진행 중 값이라는 알림은 일뿐 아니라 미완료 주·월에도 적용한다.
- 지수 포인트는 소수점이 있으므로 유한 Decimal로 보존한다. 기존 정수 KRW parser를 지수에 재사용하거나 정수로 반올림하지 않는다. 종목 가격과 지수 포인트는 단위를 구분하고 OHLC·날짜 필수 값 검사 후 반환한다. 지수 volume=None은 계획대로 충분하다.
- 종목 name이 있으면 종목, 없으면 명시된 kospi/kosdaq 지수라는 규칙을 도구 설명·검사·Broker가 같이 지킨다. 종목 action에서 안 쓰는 market/count/by는 검사를 추가하지 않는다. prices period와 공매도 period의 허용값은 action별로 검사한다.

### 실제 확인 예산·덜어낼 부분

- **계획의 'KIS 요청 최대 9번'은 현재 종료 규칙에서 보장되지 않는다.** 9는 표의 예상 page 수 합계이고, 빈 종료 확인까지 총 3회가 가능하면 6개 조회의 가격 요청은 최대 18회다. 인증 재조회·토큰 발급도 별도다. 이후 승인문에서 가격 요청/토큰 요청 범위를 분명히 하고, 9회 승인이라면 전체 가격 호출 예산을 세어 도달 시 멈춘다. 추가 호출은 새 승인을 받는다. 이번 검토는 어떠한 실제 호출 승인도 아니다.
- 상장 초기·휴장일 시작·주·월 경계·마지막 page가 꽉 찬 경우의 종료와 상한을 가짜 응답으로 검증한다. 기간별 예상 줄 수(약 65)는 설명이며 잘라내는 규칙으로 쓰지 않는다. OHLC 원문 결과에 계산 지표·차트 JSON·새 연속조회 도구를 추가할 필요는 없다.
- 날짜/이어 부르기 함수 하나와 기존 connector/service 경계를 유지하는 정도면 충분하다. 새 도구·범용 pagination 프레임워크·응답 schema 다형성은 덜어낸다. 릴리스 버전은 계획대로 나중에 사용자에게 묻는다.
- 변경은 이 계획서의 검토 기록뿐이며 `git diff --check`로 확인한다. 코드 수정·병합·실제 KIS·AWS 변경·배포는 하지 않는다.

## Codex 계획 검토 반영 (Claude, 2026-10-04)

검토 `87ddba2`의 지적을 아래 규칙으로 정리한다. 본문과 다르면 이 절이 우선한다.

### 이어 부르기: 근거와 끝 판정

- **근거:** 날짜 방식(가장 오래된 날짜의 전날을 `FID_INPUT_DATE_2`로)은 KIS 공식 저장소 `koreainvestment/open-trading-api`의 `legacy/postman/실전계좌_POSTMAN_샘플코드_v2.6.json`(커밋 `277ec0e`, 2026-09-28 확인) 두 요청의 `fid_input_date_1` 설명에 있다: 종목 "한 번의 호출에 최대 100건 … 가장 과거 일자의 1일 전 날짜를 FID_INPUT_DATE_2에 넣어 재호출", 지수 "최대 50건 …" 같은 문장. 최신 지수 예제의 `tr_cont` 방식은 쓰지 않는다(두 방식을 섞지 않는다). 실호출에서 진행·끝을 확인한다.
- **끝 판정 규칙 하나:** 정상 응답(`rt_cd` 0, `output2`가 목록)에서 날짜 있는 줄이 **API 한 번 한도(종목 100, 지수 50)보다 적으면** 더 받을 것이 없다. 한도만큼 꽉 찼고 가장 오래된 날짜가 시작일보다 뒤면 그 전날로 다시 부른다. 꽉 찼고 가장 오래된 날짜가 시작일 이하이면 끝이다.
  - 시작일이 휴장일이거나 주·월 기준일과 어긋나도 짧은 묶음이 끝을 알려 주므로 빈 확인 호출이 필요 없다. 상장 초기 종목도 짧은 묶음으로 끝난다.
  - 지금 기간표에서 종목은 모든 기간 1번(최대 약 62줄 < 100), 지수는 3m·1y·5y가 2번, 나머지 1번이다.
  - 한도 값은 공식 문서 값이며 실호출에서 확인한다. 실제 한도가 문서보다 작으면 이 규칙은 짧게 끊으므로, 실호출에서 꽉 찬 묶음 크기를 반드시 본다.
- **상한:** 최초 호출 포함 총 3번. 끝을 판정한 뒤에도 더 불러야 하는데 3번을 다 썼으면 전체 실패. 중간 오류, 진행 불가(다음 묶음의 날짜가 앞 묶음 가장 오래된 날짜보다 뒤이거나 같음), `output2` 누락은 모두 전체 실패. 부분 시세를 돌려주지 않는다.
- **줄 검사:** 모든 칸이 빈 문자열인 줄(빈 채움)만 버린다. 날짜·시가·고가·저가·종가 중 하나라도 비었거나 형식이 틀린 줄은 schema error다. 순서를 다시 정렬하지 않는다(묶음 안과 묶음 사이 모두 날짜가 엄격히 줄어야 함). 시작일 이전 줄은 끝 판정 뒤 버린다.
- **시간:** 묶음 2번 × transport 8초 = 최대 16초 + 토큰 갱신. Lambda 30초, harness 35초 안이다. 실호출에서 전체 지연을 재고, 시간 설정은 바꾸지 않는다.

### 기간·단위·값

- 시작일: KST 조회일을 한 번 고정하고 개월·년을 뺀 같은 날(없는 날이면 그 달 말일). 범위는 시작일·조회일 양끝 포함. 기간 생략 시 `3m`.
- 주·월 줄의 날짜는 KIS가 준 기준일 그대로다. 주 시작·끝이나 월말로 바꾸지 않는다. 결과 첫 줄은 "주봉(월봉), KIS 기준일"로 밝히고, 첫 줄과 마지막 줄이 부분 주·월일 수 있다고 적는다. 진행 중 알림은 일·주·월 모두에 적용한다: "The newest row may cover a session, week or month still in progress."
- 지수 포인트는 유한 Decimal로 보존한다(소수). 종목 가격 parser(정수 원)를 쓰지 않는다. JSON에서도 소수 그대로. 결과 글은 종목 `KRW`, 지수 `points`.

### 실호출 예산

- 위 끝 판정에서 6개 조회의 가격 요청은 문서 한도대로면 정확히 9번(1+1+1+2+2+2)이다. 승인은 **가격 요청 9번**으로 받고, 세면서 진행해 9번에 닿으면 남은 조회를 멈춘다. 토큰 발급·재조회는 가격 요청과 따로 센다(유효 토큰이면 0번). 추가 호출은 새 승인.
- 확인 항목에 더한다: 꽉 찬 묶음 크기(100/50), 짧은 묶음으로 끝나는지, 주·월 첫·마지막 부분 구간, 지수 소수 자릿수, 전체 지연.

### 덜어내는 것

- 범용 이어 부르기 틀, 새 도구, 계산 지표는 만들지 않는다. 날짜 계산·이어 부르기 함수 하나를 connector 안에 둔다.
