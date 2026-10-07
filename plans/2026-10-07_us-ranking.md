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

## Codex 계획 검토 (2026-10-07)

대상 `ba78872`. 기준 저장소의 현재 코드·README, KIS 공식 `examples_llm/overseas_stock`의 네 API와 각 `chk` 파일을 대조했다. 공식 예제만 GitHub에서 읽었으며 KIS·AWS 실호출, 구현, 배포, 유료 시나리오 실행은 하지 않았다.

**판정: 세 거래소를 받아 기준값으로 합치는 구조는 적절하다. 아래 P2 두 건의 계약을 먼저 정리한다.** 사용자 결정인 미국 전체 하나·prices 다섯 기준·당일만은 유지한다. 더 작은 요청으로 미국 전체를 얻는 EXCD 값은 이 공식 예제들에 없으며, 거래소별 선택을 모델에 추가하거나 국내 순위까지 재정렬할 이유는 없다.

### 구현 전에 보완할 것

1. **[P2] 요청 개수로 Broker에서 자른다는 계획에는 count 전달 계약이 빠졌다 — §2, 28·38행.**
   현재 `broker_read.py:641`의 요청은 `by`·`market`과 공매도 때 `period`뿐이고, `count`는 harness가 응답을 표시할 때만 쓴다. Broker의 `RankingQuery`와 `/ranking` 파서에도 count가 없다. 따라서 Broker가 기본 10개로 자르면 사용자가 20개를 요청해도 10개만 나온다.
   - **첫 페이지 세 개를 합치는 단계의 최소 변경**은 Broker가 합쳐 정렬한 행을 모두 반환하고, 기존처럼 harness가 count만큼 보여 주는 것이다. 이렇게 하면 새 query 인자나 국내 자르기 변경이 필요 없다.
   - Broker가 요청 개수에 맞춰 후보를 더 받거나 자르려면 **count를 Broker에 전달하는 계약**을 계획에 추가해야 한다. 정확한 미국 Top N을 계속 지원하면서 필요한 만큼만 이어 받으려면 이쪽이 자연스럽다. count가 어느 층에서 쓰이는지 먼저 정하고, 국내는 기존 한 페이지·KIS 순서·harness 자르기를 유지한다.

2. **[P2] 합친 줄 수가 N보다 적을 때만 이어 받는 조건으로는 미국 전체 Top N을 보장하지 못한다 — §2, 38·42행.**
   가장 단순한 보장 조건은 **각 거래소에서 상위 N개를 확보했거나, 그 거래소의 결과가 끝났음을 확인했다**는 것이다. 합친 결과의 길이만으로는 알 수 없다. 오프라인 반례: Top 12를 요청하고 NAS 상위 10개·NYS 20개·AMS 20개를 받으면 합계 50개라 계획의 조건은 통과한다. 하지만 NAS 11·12위가 다른 거래소보다 높으면 실제 미국 Top 12에서 두 종목이 빠진다. 현재 count는 양의 정수이며 상한이 없어 이 문제를 기본 10개만의 검증으로 끝낼 수 없다.
   - 실호출에서는 다섯 기준 **각 거래소별** 첫 페이지 길이와 다음 페이지 유무를 함께 확인한다. 한 페이지가 짧다는 사실만으로 끝났다고 판단하지 않는다.
   - 각 거래소가 N개를 주거나 결과가 끝났으면 세 번만 부르면 충분하다. 그렇지 않으면 필요한 거래소만 이어 받거나, 지원 개수/조회 범위를 명시해야 한다. 조회된 후보를 정렬한 결과를 확인 없이 "미국 전체 Top N"으로 내지 않는다. count 상한을 새로 두는 것은 사용자와 정할 지원 범위이지, 지금 검토가 임의로 정할 값은 아니다.
   - KEYB 이어 받기는 실호출 결과가 필요할 때 구현해도 된다. 다만 추가 조건은 전체 행 수가 아니라 위 거래소별 조건이다. 모든 종목을 끝까지 받아서 정렬하거나, heap·병렬 스트림·순위 병합 엔진을 새로 만들 필요는 없다.

### 공식 API 대조와 구현 범위

| 기준 | 공식 요청·필드 | 판단 |
|---|---|---|
| market_cap | `/ranking/market-cap`, `HHDFS76350100`, EXCD·VOL_RANG·KEYB·AUTH, `tomv` | 계획과 일치 |
| gainers / losers | `/ranking/updown-rate`, `HHDFS76290000`, NDAY=`0`, GUBN=`1` 상승·`0` 하락, VOL_RANG=`0` | 계획과 일치. 정렬·표시의 rate는 아래 부호 규칙을 같이 적용 |
| volume | `/ranking/trade-vol`, `HHDFS76310010`, NDAY=`0`, VOL_RANG=`0`, **PRC1·PRC2 빈 값** | 계획에 빈 가격 필터 두 개 추가 |
| trading_value | `/ranking/trade-pbmn`, `HHDFS76320010`, NDAY=`0`, VOL_RANG=`0`, **PRC1·PRC2 빈 값** | 계획에 빈 가격 필터 두 개 추가 |

- 네 API 모두 **행은 output2**, output1은 `crec`·`trec`·`nrec` 등 조회 정보다. 국내 순위의 기본 output 키를 그대로 쓰지 않는다. 공식 `chk`의 `excd`·`symb`·`name`·`ename`·`last`·`sign`·`diff`·`rate`·`tvol`, 시가총액 `tomv`, 거래대금 `tamt`는 계획과 맞는다.
- **정렬 기준값은 필수**다: `tomv` / 부호가 적용된 `rate` / `tvol` / `tamt`가 읽히지 않으면 현재 국내 순위의 핵심 필드 규칙처럼 전체 실패로 한다. None을 0으로 바꾸거나 그 행만 빼면 순위가 달라진다. 정렬은 원래 Decimal 값으로 하고 화면의 조·억 단위 반올림 값은 쓰지 않는다.
- `diff`뿐 아니라 **rate에도 기존 `_with_sign` 규칙을 재사용**한다. 음수 자체가 오는 값도 유지하고, sign이 하락이면 하락률은 음수로 정렬·표시한다. 새 부호 예외 표를 만들지 않는다. NDAY=`0`에서 `rate`와 `n_rate` 및 KIS 원래 순서가 어떤 관계인지 실호출의 상승·하락 확인에 포함한다.
- 한 거래소 실패 시 전체 실패, 세 거래소의 순차 호출 세 번, 별도 재시도·대기 장치를 만들지 않는 방향은 적절하다. 조회 시각은 합치기를 마친 시각으로 두고, 동시 스냅샷이라고 표현하지 않는다.
- 공식 예제의 연속 조회 코드를 그대로 복사하지 않는다. market_cap·updown_rate·trade_vol 예제는 다음 호출에 keyb를 갱신하지 않고 재사용하고, trade_pbmn만 응답의 keyb를 읽는다. 실제 다음 키의 위치와 종료 표시를 확인한 뒤 필요한 부분만 구현한다.

### 국내 동작과 출력

- data별 market 표를 두는 것은 적절하다. 각 목록의 첫 값은 **all**로 유지하고, 기존 `_MARKETS=(all,kospi,kosdaq)`를 통째로 US 포함 목록으로 바꾸지 않는다. prices만 기존 목록 뒤에 us를 더하고, schema의 enum은 허용 목록의 합집합에서 만든다. quote·history의 대상 목록과 기본값은 그대로 둔다.
- Broker도 `market=us`에 다섯 prices 기준만 허용해야 한다. 현재 Broker 요청에는 data가 없으므로 **by와 market의 조합**을 KIS 호출 전에 검사한다. harness가 거절한다는 이유로 Broker에서 국내 API로 보내거나 all로 바꾸지 않는다.
- HTS 조회 상위는 지금처럼 허용된 국내 market을 받아도 Broker에 **all**로 보내고 결과도 전체 시장으로 적는다. 국내 investors·short_selling·attention의 허용값·period 검사, prices의 사용하지 않는 period 무시, ranking의 name 무시도 유지한다.
- 미국 응답에 currency를 붙이는 방향은 기존 quote·account와 같다. 현재 `_FIGURES`의 market_cap은 **국내 억 원 입력**을 전제로 하므로 가격 라벨만 USD로 바꾸면 부족하다. ranking의 가격·대비·시가총액·거래대금·첫 줄 단위를 모두 통화에 맞춰 고르고 기존 `_money`·`_market_amount`를 재사용한다. 국내 시가총액은 입력 억 원 → 조 원, 미국이 raw USD로 확인되면 USD → 조 달러다. 새 금액 클래스나 별도 환산 계층은 필요 없다.
- "당일만"은 KIS NDAY=`0`의 최신 미국 거래 세션이라는 뜻으로 적는다. 한국의 오늘 날짜나 실시간이라는 보장은 아니다. 기존 `_NO_SESSION_DATE`를 유지하고 날짜 없는 응답에서 미국 거래일을 만들지 않는다. PIA 프롬프트의 market rankings는 그대로 쓸 수 있다.

### 실호출·테스트와 남은 결정

- 첫 확인 예산 **15회(5종류 × 3거래소)**는 적절하다. 페이지가 더 필요하면 추가 호출은 별도 확인 예산에 포함한다. 각 API·각 거래소의 행 수·다음 페이지 표시·정렬 기준 필드·ETF 포함 여부를 함께 읽으면 이 예산으로 필요한 단서를 얻을 수 있다.
- 순위 API의 `tomv`·`tamt` 단위는 기존 quote API와 같다고 가정하지 않고 별도로 확인한다. 결과가 비정상적인 API·거래소를 조용히 빼고 전체 시장이라고 부르지 않는다.
- 남은 지원 범위 결정은 **요청 count가 첫 페이지 범위를 넘을 때의 처리**다(P2 두 건). ETF 범위는 기존 미국 종목과 국내 순위처럼 KIS가 제공하는 종목 단위로 확인하면 된다. 기업별로 합치거나 주식 클래스·ADR을 다른 종목과 합산하는 계산은 이번 범위에 넣지 않는다.
- 오프라인 테스트: 다섯 기준 방향, 가격과 기준값이 다른 행, 하락 sign, 동일 기준값의 안정된 순서, 한 거래소 실패, 거래소별 후보 부족 반례. 국내 default all·세 시장·HTS all·사용하지 않는 인자 유지, US 잘못된 data 거절도 확인한다.
- 시나리오의 expect_args에는 **action·data·by·market·count**를 적어 잘못된 국내 조회가 PASS가 되지 않게 한다. fake 미국 응답에도 currency와 실제 요청 수량에 맞는 후보를 준다. 기존처럼 작은 fake 결과 때문에 모델이 나머지를 웹에서 채우는 현상을 새 기능의 성공으로 혼동하지 않는다. 미국 투자자 순위는 관찰 대상으로 두고 국내 결과를 미국으로 답하지 않는지 본다.

근거: [market_cap](https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/overseas_stock/market_cap/market_cap.py), [updown_rate](https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/overseas_stock/updown_rate/updown_rate.py), [trade_vol](https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/overseas_stock/trade_vol/trade_vol.py), [trade_pbmn](https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/overseas_stock/trade_pbmn/trade_pbmn.py)와 각 디렉터리의 `chk` 필드 표. 검증은 위 count 전달 코드 확인과 합친 50행에도 Top 12를 놓치는 오프라인 반례뿐이며, 문서만 변경하므로 전체 코드 테스트·빌드는 실행하지 않았다.

## Codex 계획 검토 반영 (Claude, 2026-10-07)

| 지적 | 반영 |
|---|---|
| P2-1 count 전달 계약 없음 | Broker는 합쳐 정렬한 행을 모두 돌려주고 harness가 지금처럼 count만큼 보여 준다. 새 인자 없음, 국내 그대로 |
| P2-2 합친 길이로는 Top N 보장 안 됨 | 규칙 하나: Broker가 합친 목록을 **확실히 맞는 길이까지만** 남긴다 = 다음 페이지가 남은 거래소(`tr_cont` M·F) 중 가장 적게 받은 줄 수. 끝난 거래소는 제한에 넣지 않는다. 이어 받기(KEYB)는 넣지 않고, 실호출에서 첫 페이지가 짧으면 다시 정한다. count가 그보다 크면 국내처럼 받은 개수만 보여 주고 그 사실을 적는다 |
| 거래량·거래대금 PRC1·PRC2, 행은 output2 | 그대로 |
| 정렬 기준값 필수, Decimal로 정렬 | 그대로(없으면 순위 전체 실패) |
| rate에도 `_with_sign` | 그대로 |
| Broker도 by×market 조합 검사 | 그대로(us는 prices 다섯 기준만, 아니면 400) |
| data별 market 표, 첫 값 all 유지 | 그대로 |
| 결과 문장 단위를 통화로 | 그대로(`_money`·`_market_amount` 재사용, 순위 API의 `tomv`·`tamt` 단위는 실호출로 확인) |
| "당일" = KIS NDAY=0 최신 미국 세션 | 그대로, 날짜를 만들지 않음 |
| 시나리오 expect_args에 action·data·by·market·count | 그대로 |
