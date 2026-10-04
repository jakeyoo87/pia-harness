# broker 도구 구조 통일 (설계 기록·계획)

날짜: 2026-10-04 (Asia/Seoul)
브랜치: pia-harness `claude/broker-unify` (main `6dffbc4`, v0.7.2에서)
담당: Claude 계획·구현(harness, Broker), Codex 계획·구현 검토, 릴리스·PIA 연동·IAM·배포는 Codex
상태: 구조 합의(아래 "합의한 구조")와 그에 따른 **구현 계획**(아래 "계획"). Codex 계획 검토 대기.

## 왜

`broker` 읽기 도구가 액션 6개(`status`, `quote`, `account`, `buyable`, `ranking`, `investors`)로 흩어져 한눈에 파악하기 어렵다(사용자, 2026-10-04).

- 경로 하나가 두 모양을 낸다: `investors`는 종목이면 날짜별, 아니면 시장 오늘 하루. `/account`는 `code`가 있으면 매수 가능.
- 같은 주제가 흩어져 있다: 외국인 매매가 `ranking`과 `investors`에, 공매도는 순위만.
- 같은 인자가 액션마다 다른 뜻이다: `count`는 순위 개수이거나 일수, `market`·`period`도 쓰임이 제각각.
- 금액 단위가 섞여 있다(원, 억 원, 백만 원). Telegram 확인(2026-10-04)에서 모델이 백만 원 순매수를 "억원"으로 적어 100배 틀린 차트를 그렸다.
- `buyable` 같은 전용 액션이 데이터가 생길 때마다 늘어날 구조다(`sellable`, `orderable` …).

## 합의한 구조 (사용자와 합의, 2026-10-04)

### 원칙

- 액션은 **질문의 대상과 모양**으로만 나눈다: 내 계좌 / 지금 / 기간별 / 순위. 새 데이터는 이 넷 중 하나의 `data` 값이나 결과 항목으로 들어가고, **새 액션을 만들지 않는다.**
- **인자마다 뜻은 하나.** 액션·데이터마다 달라도 되는 것은 받는 값의 목록뿐이다.
- 숫자는 증권사가 준 값·계산한 값을 쓴다. 모델이 계산하게 하지 않는다(매수 가능 수량, 시가총액 등).

### 액션 4개

```
broker
├─ account ······ 내 계좌                          (선택) name
│     계좌 전체: 연결 상태 · 예수금 · D+2 예수금 · 보유 종목 · 합계
│     name=종목: 그 종목 기준 내 계좌(보유 수량 · 매도 가능 · 평균가 · 매수 가능 수량·금액, KIS 계산값)
│
├─ quote ········ 지금                             name
│   └─ data=prices         현재가·등락·거래량·거래대금·시총·PER·PBR·52주
│
├─ history ······ 기간별                           name 또는 market, period(1m~5y)
│   ├─ data=prices         시가·고가·저가·종가·거래량·거래대금 (종목, 코스피·코스닥 지수)
│   ├─ data=investors      개인·외국인·기관 순매수 (종목, 시장 전체)
│   └─ (나중) short_selling, credit
│
└─ ranking ······ 순위                             market(all·kospi·kosdaq), count
    ├─ data=prices         by: market_cap·gainers·losers·volume·trading_value
    ├─ data=investors      by: foreign_buying·foreign_selling·institution_buying·institution_selling
    ├─ data=short_selling  by: short_volume, period(집계 기간 1d~3m)
    └─ data=attention      by: most_viewed
```

- `status`와 `buyable`은 `account`로 들어간다. 연결이 안 됐거나 확인 전이면 `account`는 KIS를 부르지 않고 그 상태와 회원 웹 안내만 준다. 연결 확인 질문에 잔고 조회가 한 번 더 일어나는 것은 받아들인다(드묾). Telegram `/status` 명령은 이 도구와 무관하다.
- `account` + `name`은 다른 종류의 데이터가 아니라 같은 "내 계좌"를 그 종목으로 좁혀 보는 것이다(옛 `investors`처럼 데이터 종류가 바뀌는 구조와 다름). 앞으로 매도 가능 수량·미체결 주문·주문 가능 금액은 `account` 결과 항목으로 더한다. 미체결 주문은 별도 KIS API라 "주문·체결 내역 + 정정·취소" 작업 때 넣는다.
- `history`라는 이름: 6개월 이상은 주·월 단위라 "daily"가 아니다.
- `ranking` 이름 유지: 시가총액·등락·투자자·공매도·조회 상위를 모두 정확히 설명한다. `screen`은 나중에 KIS 조건검색(조건으로 거르기)을 붙일 때 쓴다.
- 검토한 대안: `account`/`market` 두 액션 아래 `view × data`(단계가 하나 더 깊고 빈칸 조합을 따로 거절해야 함), 대상 기준 세 액션(시장 전체 투자자 동향의 자리가 애매함). 둘 다 택하지 않았다.

### 인자 6개 (뜻 하나, 값 목록만 다름)

| 인자 | 하나의 뜻 | 쓰는 곳 | 받는 값 |
|---|---|---|---|
| `data` | 무슨 데이터인가 | quote, history, ranking | quote: `prices` / history: `prices`·`investors` / ranking: `prices`·`investors`·`short_selling`·`attention` |
| `name` | 질문 대상인 종목 하나 | account, quote, history | 종목명. account에서는 계좌를 그 종목으로 좁힘 |
| `market` | 질문 대상인 시장 | history, ranking (나중 quote: 지수 현재값) | history: `kospi`·`kosdaq`(그 시장 자체: 지수, 시장 전체 투자자 동향) / ranking: `all`·`kospi`·`kosdaq`(그 시장의 종목 순위) |
| `period` | 오늘부터 거슬러 올라가 데이터가 덮는 기간 | history, ranking(short_selling) | history: `1m`·`3m`·`6m`·`1y`·`3y`·`5y` / 공매도 순위: `1d`~`3m`(KIS 값) |
| `by` | 순위를 매기는 기준 | ranking | data마다 목록, 생략하면 목록의 첫 값. 기준이 하나뿐인 데이터도 목록에 그 하나를 둔다 |
| `count` | 순위에서 몇 개 | ranking | 1 이상, 기본 10. 날짜 범위에는 쓰지 않는다(옛 `investors` 일수는 `period`로) |

- `market`은 두 액션에서 같은 뜻("질문이 어느 시장에 관한 것인가")이다. 그 시장 자체를 보느냐(history) 그 시장의 종목 순위를 보느냐(ranking)는 액션이 정한다. `name`이 "질문 대상 종목"인 것과 같은 방식. history에 `index` 같은 다른 이름을 쓰는 대안은 "코스피 외국인 순매수"를 `index`로 부르는 어색함 때문에 택하지 않았다.

### 함께 바꾸기로 한 것

- **금액 단위 통일:** 결과의 금액은 모두 억 원(가격은 원). 모델이 단위를 바꿔 적을 이유를 없앤다. 수량 단위(주·천 주) 표기도 함께 정리한다.
- **기간 시세:** `history` + `data=prices`. KIS 내용(종목 100줄·지수 50줄, 날짜로 이어 부르기와 짧은 묶음으로 끝 판정, 상한 3번, 지수 소수 보존, 기간별 단위 1m·3m 일·6m·1y 주·3y·5y 월, 실호출 가격 요청 9번 예산)은 `claude/broker-prices`의 `plans/2026-10-04_broker-prices.md`와 Codex 검토(`87ddba2`)·반영(`2defac9`)을 그대로 쓴다. 그 계획의 독립 `prices` 액션 설계는 이 구조로 대체한다.
- **시장 전체 투자자 동향:** 지금의 오늘 하루 API(`FHPTJ04030000`)에서 KIS 시장별 일별 API(`inquire_investor_daily_by_market`)로 바꿔 `history`에 넣는다. 단위·필드는 실호출로 확인.
- **D+2 예수금:** `account` 결과에 추가(잔고 API에 이미 있음, 필드는 구현 때 확인).

## 다음에 정할 것 (계획 단계)

1. 결과 형식: 액션·데이터별 한 줄 형식, 단위 표기, 조회 시점·장중 표기(기존 `_NO_SESSION_DATE` 문구와 기간별 결과의 진행 중 줄 안내).
2. Broker 경로: 지금 경로(`/quote`, `/account`, `/ranking`, `/investors`, `/broker-status`)를 액션 구조에 맞출지, 경로는 두고 harness만 바꿀지. IAM 변경 범위와 함께.
3. 인자 검사: 액션별 받는 `data`·`by`·`period` 목록 하나로(예외 분기 없이).
4. KIS 실호출 확인 목록(새 API: 기간 시세 종목·지수, 시장 일별 투자자)과 예산.
5. harness 버전: 도구 인자 구조가 바뀌므로 중간 자리(0.8.0)를 제안할 예정, 릴리스 때 사용자 확인.
6. PIA 연동: harness 핀, `BROKER_READ_PROMPT` 설명.

## 순서

1. (지금) PIA 정리(`pia` `claude/pia-cleanup`) Codex 검토·구현·병합을 먼저 마친다.
2. 이 문서를 계획으로 이어 쓴다 → Codex 계획 검토 → 구현(Broker → harness) → Codex 구현 검토 → Broker 배포·IAM(승인) → 실호출 확인(승인) → harness 릴리스(버전 확인) → PIA 연동·배포.
3. 그 뒤 harness·Broker 전체 정리(PIA 정리와 같은 방식).

---

# 계획 (Claude, 2026-10-04)

위 "다음에 정할 것" 여섯 가지와 시나리오 테스트를 정한다. 대조한 코드: harness main v0.7.2 `src/pia_harness/broker_read.py`, Broker main `9b2cb69`(`credential_api.py`, `orders.py`, `models.py`, `connectors/kis.py`, `infra/broker-credential.template.json`), PIA main(`app/core.py`, `infra/dev-deployment-bootstrap.template.json`), harness `tests/manual/smoke_flow.py`.

표시: **(권고)** 는 사용자가 정하지 않고 Claude가 권고한 것이다. Codex 검토에서 다른 의견이면 그대로 바꿀 수 있다.

## 0. 바뀌지 않는 것 (제약)

- **Telegram `/status` 명령:** 모델을 거치지 않고 PIA가 Broker `GET /internal/members/{id}/broker-status`를 직접 부른다(`app/broker_status.py`). 이 경로·응답은 그대로 둔다.
- **주문 도구:** 확인 문구의 현재가를 Broker `GET /quote`로 받는다(`broker_order.py`). `/quote` 응답은 그대로 둔다(필드 추가만 가능).
- **탈퇴 작업자:** `DELETE /broker-data`. 그대로.
- **이미 배포된 harness v0.7.2와의 호환:** Broker를 먼저 배포하므로, PIA가 새 harness로 바뀌기 전까지 지금 경로의 응답 의미를 바꾸지 않는다. Broker 변경은 **필드 추가와 새 경로**만 한다. 옛 경로 제거는 PIA 전환 뒤 마지막 단계(§8)에서 한다.
- 숫자는 증권사 값 그대로(계산 없음). 단위 변환만 harness가 한다(§3).

## 1. Broker 경로 (권고)

도구 액션과 Broker 경로를 1:1로 맞춘다. 새 경로는 `history` 하나다.

| 도구 | Broker 경로 | 바뀌는 것 |
|---|---|---|
| `account` | `GET /account` | 응답에 `cash_d2` 추가 |
| `account` + `name` | `GET /account?code=` | 응답에 `position` 추가(그 종목 보유 행 또는 `null`) |
| `quote` | `GET /quote?code=` | 없음 |
| `history` | **새** `GET /history?data=prices\|investors&(code=\|market=)&period=` | 새 경로 |
| `ranking` | `GET /ranking?by=&market=&period=` | 없음(`data`+`by` → Broker `by`는 harness가 바꿈, §2) |
| (연결 상태, `account`가 409를 받았을 때만) | `GET /broker-status` | 없음 |
| (없어짐) | `GET /investors` | PIA 전환 뒤 제거(§8) |

- 이유: 기간 시세와 기간 투자자 동향은 둘 다 "기간·날짜별 줄"이라 한 경로·한 응답 틀(`rows`)에 맞고, 도구 `history`와 같아서 한눈에 보인다. `/investors`의 의미(종목 30일·시장 오늘 하루)를 기간으로 바꾸면 배포된 v0.7.2가 깨지므로 고치지 않고 새 경로로 옮긴다.
- 대안: `/prices` 새 경로 + `/investors`에 `period` 분기(옛 계획 `claude/broker-prices`). `/investors`에 "period가 있으면 다른 API" 분기가 생기고 옛 동작을 계속 끌고 가서 택하지 않았다. `claude/broker-prices`의 `/prices` 경로 설계는 이 `/history`로 대체한다(KIS 내용은 그대로 쓴다).
- IAM: Broker 템플릿에 `/history` route(GET, IAM 인증, 기존 route와 같은 방식) 하나. PIA bootstrap에 `BrokerHistoryApiArn`(`GET /internal/members/*/history`) 하나, Bot 역할 정책에 그 ARN. 옛 `BrokerInvestorsApiArn`은 §8에서 뺀다. AWS 변경이라 Codex가 사용자 승인 후 적용한다.
- `/account?code=` 하나에 매수 가능과 그 종목 보유를 함께 싣는다(새 경로·IAM 없음). 매도 가능 수량은 잔고 행의 `ord_psbl_qty`(지금 `account`가 쓰는 값)를 쓰고 따로 `inquire-psbl-sell`을 부르지 않는다.

## 2. harness 도구 정의

### 2.1 표 하나

액션·데이터·기준·기간 허용 값을 한 표(`_ACTIONS`)로 두고, schema enum·설명·검사가 모두 이 표에서 나온다. 액션별 `if` 분기는 결과를 만드는 함수 선택에만 쓴다.

| action | data(첫 값이 기본) | by(첫 값이 기본) | period(첫 값이 기본) | name | market | count |
|---|---|---|---|---|---|---|
| `account` | — | — | — | 선택 | — | — |
| `quote` | `prices` | — | — | 필수 | — | — |
| `history` | `prices` | — | `1m` `3m` `6m` `1y` `3y` `5y` | name 또는 market | `kospi` `kosdaq` | — |
| `history` | `investors` | — | `1m` | name 또는 market | `kospi` `kosdaq` | — |
| `ranking` | `prices` | `market_cap` `gainers` `losers` `volume` `trading_value` | — | — | `all`(기본) `kospi` `kosdaq` | 기본 10 |
| `ranking` | `investors` | `foreign_buying` `foreign_selling` `institution_buying` `institution_selling` | — | — | 같음 | 같음 |
| `ranking` | `short_selling` | `short_volume` | `1d` `2d` `3d` `4d` `1w` `2w` `3w` `1m` `2m` `3m` | — | 같음 | 같음 |
| `ranking` | `attention` | `most_viewed` | — | — | 같음(KIS는 전체만, 결과에 밝힘) | 같음 |

- **기본값 규칙 하나 (권고):** `data`·`by`·`period`는 생략하면 그 칸 목록의 첫 값. 그래서 history 기본 기간은 `1m`이다(옛 계획 `3m`에서 바꿈: 규칙 하나로 두기 위해. 모델은 사용자가 말한 기간을 넣는다). `market`은 ranking에서만 기본 `all`이 있다.
- **history 대상:** `name`이 있으면 그 종목, 없으면 `market`(`kospi`·`kosdaq`, 그 시장 자체). 둘 다 없거나 `market=all`이면 실행하지 않고 "종목이나 kospi·kosdaq를 정하라"고 돌려준다. 둘 다 있으면 `name`을 쓴다(name이 질문 대상이라는 뜻 그대로).
- **investors 기간 (사용자, 2026-10-04): `1m`만.** 투자자 동향은 KIS에 일 단위만 있다. 기간이 길면 줄이 그대로 늘고(3개월 약 62줄, 1년 약 250줄), 주·월로 묶으려면 Broker가 더하기(계산)를 해야 하므로 하지 않는다. 더 긴 기간을 물으면 모델이 1개월까지만 된다고 말한다.
- **ranking `by` → Broker:** `short_volume` → `short_selling`, 나머지는 같은 이름. Broker `/ranking`은 바꾸지 않는다.
- **검사는 그 액션·데이터가 쓰는 인자만** 본다(지금과 같음). 안 쓰는 인자는 무시한다. 틀리면 "Broker lookup not run: …"과 허용 값 목록을 돌려준다(지금 문구 방식).
- `count`는 ranking만, 1 이상 정수. 옛 investors 일수는 `period`가 맡는다.

### 2.2 schema와 설명 (영문 초안)

- `action` enum `account` `quote` `history` `ranking`, 필수(지금처럼 `required`는 `action`만).
  - "account: the user's own account: connection, cash (deposit and D+2 deposit), holdings and totals. With name, that one stock in the account: quantity held, sellable now, average price, and how much can be bought now as the broker calculates it. quote: one stock's live price and market figures. history: figures over a period for one stock (name) or for the KOSPI or KOSDAQ market itself (market). ranking: a market-wide ranking of stocks."
- `data` enum `prices` `investors` `short_selling` `attention`: "What figures. quote: prices. history: prices (stock price or index level) or investors (net buying by individuals, foreigners and institutions). ranking: prices, investors, short_selling or attention. Left out, the first one listed."
- `name`: 지금 설명(정식 종목명, 별칭 변환, 코드는 사용자가 준 경우만) + "The one stock the question is about. account: narrows the account to that stock. quote: required. history: used instead of market."
- `market` enum `all` `kospi` `kosdaq`: "The market the question is about. history without name: kospi or kosdaq (the index, or net buying in the whole market). ranking: all (default), kospi or kosdaq (stocks in that market)."
- `period` enum(합집합): "How far back from today. history prices: 1m (default) and 3m give daily rows, 6m and 1y weekly rows, 3y and 5y monthly rows. history investors: 1m only, daily rows (there is no longer investor history). ranking short_selling: 1d (default) to 3m, the period the ranking covers."
- `by` enum(합집합): "What a ranking is ordered by. prices: market_cap (default), gainers, losers, volume, trading_value. investors: foreign_buying (default), foreign_selling, institution_buying, institution_selling (by amount). short_selling: short_volume. attention: most_viewed (on the KIS trading app). There is no dividend, PER, PBR, watchlist or new-high ranking."
- `count`: "ranking: how many stocks. Default 10; set it when the user asks for a number."
- `BROKER_DESCRIPTION`: "Look up through the user's connected brokerage account: their account (cash, holdings, what one stock they can buy or sell), a stock's live price, price or investor history for a stock or the KOSPI/KOSDAQ market, and market rankings. Read-only; orders go through the order tool. Figures are live at the time shown, so call again for a later question instead of reusing an earlier result. Never work out a buyable quantity from cash and price; use account with name."

## 3. 단위 (사용자 확인: 계좌 금액은 원, 시장 금액은 억 원)

| 값 | 결과 단위 | 이유 |
|---|---|---|
| 가격(주가·평균가·단가·52주) | 원 `KRW` | 주가는 원으로 말한다 |
| 내 계좌 금액(예수금·D+2·평가·손익·매수 가능 금액) | 원 `KRW` | 3,000,000원을 0.03억 원으로 쓰면 읽기 어렵다 |
| 시장 금액(거래대금·시가총액·순매수 금액·공매도 금액) | **억 원**, 소수 둘째 자리까지 | 2026-10-04 Telegram의 백만 원 → "억원" 오류가 난 곳. 모두 한 단위 |
| 수량(거래량·순매수 수량·보유 수량) | 주 `shares` | 천 주는 ×1000으로 바꾼다 |
| 지수 | `points`, KIS 소수 그대로 | |
| 비율 | `%` | |

- 처음 합의한 문장은 "금액은 모두 억 원(가격은 원)"이었다. 계좌 금액까지 억 원이면 소액이 0.0x억 원이 되어 원으로 둔다. 시장 금액은 한 단위(억 원)라 합의 목적(모델이 단위를 바꿔 적지 않게)은 그대로다. **사용자 확인(2026-10-04): 계좌 금액은 원, 시장 금액은 억 원.** 써 본 뒤 다시 본다(사용자).
- 변환은 harness 한 곳의 표(필드 → KIS 단위)로 한다. Broker는 지금처럼 KIS 값을 그대로 준다(배포된 v0.7.2 호환, §0). Decimal로 바꾸고 둘째 자리 반올림(ROUND_HALF_UP), 끝 0은 지운다.
- KIS 단위(실호출로 확인된 것): 시가총액 `hts_avls`·`stck_avls` 억 원, 거래대금·공매도 금액 원, 순위 순매수 금액 백만 원, 시장 투자자(오늘) 수량 천 주(2026-10-01·02 확인). 새 API(기간 시세 거래대금, 일별 투자자 두 개)의 단위는 §6에서 확인하고 표를 채운다. **확인 전에는 harness를 릴리스하지 않는다.**
- 결과 첫 줄에 그 결과의 단위를 적는다(예: "amounts in 억 원 (KRW 100 million), volumes in shares").

## 4. Broker 구현

### 4.1 account

- `AccountSummary.cash_d2`: 잔고 `output2`의 D+2 예수금. 필드는 `prvs_rcdl_excc_amt`(가수도정산금액, 흔히 D+2 예수금)로 보고 §6에서 확인. 없거나 읽을 수 없으면 `null`(다른 합계 값과 같은 규칙).
- `GET /account?code=`: `TradingService.buyable` → `stock_account(member_id, code)`로 바꾸고 현재가 → 매수 가능 → 잔고(모든 page)를 부른다. 응답 `{"buyable": {...지금과 같음}, "position": {...positions[]의 한 행과 같은 필드} | null}`. 보유 행은 잔고 결과에서 코드가 같은 행(수량 0 행은 이미 빠짐). KIS 호출 2 → 3 + 잔고 page 수. 배포된 v0.7.2는 `position`을 무시한다.

### 4.2 history

- `models.py`:
  - `HistoryPeriod`(`1m 3m 6m 1y 3y 5y`)와 `prices`의 기간별 단위(D·D·W·W·M·M). 시작일은 KST 조회일을 한 번 고정하고 개월·년을 뺀 같은 날(없으면 그 달 말일), 양끝 포함(옛 계획 반영 그대로).
  - `PriceBar(date, open, high, low, close, volume, trading_value)`: 종목 가격은 정수 원, 지수는 유한 Decimal(소수 보존). `volume`·`trading_value`는 있으면(종목 `acml_vol`·`acml_tr_pbmn`, 지수는 KIS가 주면), 없으면 None.
  - `InvestorDay(date, close, change, flows)`: `flows`는 `individual`·`foreign`·`institution`의 `net_volume`·`net_value`, 모두 필수(지금 `_flows` 규칙). 기금(`pension`)은 넣지 않는다(권고: 종목·시장 같은 세 집단으로 하나. 기관계에 포함된 값).
  - `History(data, code | None, market | None, period, unit, rows, observed_at)`.
- `connectors/kis.py` — 네 API, 이어 부르기 함수 하나:

  | | 종목 | 시장(지수) |
  |---|---|---|
  | prices | `inquire-daily-itemchartprice` `FHKST03010100`, J, 수정주가 0 | `inquire-daily-indexchartprice` `FHKUP03500100`, U, `0001`·`1001` |
  | investors | `investor-trade-by-stock-daily` `FHPTJ04160001`, J, `FID_INPUT_DATE_1`=끝 날짜, 수정주가·기타 빈칸, 줄은 `output2` | `inquire-investor-daily-by-market` `FHPTJ04040000`, U, `0001`/`KSP`/`0001`(코스피)·`1001`/`KSQ`/`1001`(코스닥), `FID_INPUT_DATE_1`·`_2` |

  - 근거: prices는 옛 계획(공식 예제·Postman v2.6 커밋 `277ec0e`). investors 둘은 공식 예제 `examples_llm/domestic_stock/investor_trade_by_stock_daily`, `inquire_investor_daily_by_market`(2026-10-04 확인). 종목 일별 투자자 예제는 `tr_cont`로 이어 부르고, 시장 일별은 이어 부르기 설명이 없고 날짜 둘을 "같은 날"로 넣는다. **두 API 모두 한 번에 몇 줄인지, 끝 날짜를 앞으로 옮겨 이어 받을 수 있는지가 문서에 없다** → §6 실호출에서 정한다.
  - 지금 종목 투자자(`FHKST01010900`, 날짜 입력 없이 최근 30일)는 기간을 줄 수 없어 `history`에 쓰지 않는다(옛 `/investors`와 함께 §8에서 정리).
- **이어 부르기 규칙 하나 (권고, 옛 계획의 "짧은 묶음" 규칙을 바꿈):**
  1. 끝 날짜 = 조회일로 부른다.
  2. 모든 칸이 빈 줄만 버린다. 날짜·필수 값이 비었거나 형식이 틀린 줄은 schema error.
  3. 날짜는 묶음 안과 묶음 사이 모두 엄격히 줄어야 한다(겹침·역순·같은 날 반복은 schema error, 재정렬하지 않음).
  4. **멈춤:** 묶음에 줄이 없거나, 가장 오래된 날짜가 시작일 이하.
  5. 아니면 가장 오래된 날짜의 전날을 끝 날짜로 다시 부른다. 최초 포함 **총 3번**. 3번 안에 멈추지 못하면 전체 실패(부분 결과 없음).
  6. 멈춘 뒤 시작일보다 앞선 줄을 버린다.
  - 바꾼 이유: 네 API에 규칙 하나. "한 번 한도보다 짧으면 끝"은 한도를 알아야 하는데 투자자 API 둘은 한도를 모른다. 대가는 시작일이 휴장일이거나 주·월 기준일이 시작일 뒤일 때 한 번 더 부르는 것(빈 묶음 또는 시작일 이전 줄로 멈춤)이고, 지금 기간표에서 모두 3번 안이다: 종목 prices 1~2번, 지수 prices 3m·1y·5y 2~3번, 나머지 1~2번.
  - 묶음 2~3번 × transport 8초 + 토큰 갱신: Lambda 30초, harness 35초 안. 실호출에서 지연을 잰다. 시간 설정은 바꾸지 않는다.
- `orders.py` `TradingService.history(member_id, data, code | None, market | None, period)`: `code`가 있으면 종목, 없으면 `market`(`kospi`·`kosdaq`). `all`·둘 다 없음·investors에 `1m` 아닌 기간은 400(`ValueError`, 지금 경로의 입력 오류와 같음).
- `credential_api.py`: `/history` route. JSON `{data, code, market, period, unit(day|week|month), observed_at, rows[]}`, `rows`는 KIS 순서(최근 먼저). prices 줄 `{date, open, high, low, close, volume, trading_value}`, investors 줄 `{date, close, change, flows{...}}`. 오류 계약은 지금 경로와 같다(409·503·502, 입력 400).

## 5. harness 결과 글

모두 첫 줄에 대상·기간·시점·단위, 줄 순서를 적는다. 예는 모양만 보이는 가짜 값이다.

- **account:**
  ```
  Account at 2026-10-04 14:05 KST, as reported by the broker (a verified KIS connection). Amounts in KRW.
  Cash: deposit 3,000,000 KRW; D+2 deposit 2,500,000 KRW. Cash is not what can be spent on one stock; use account with name for that.
  Totals: total valuation 12,345,000 KRW; total profit +345,000 KRW.
  Holdings (2):
  - 삼성전자(005930): quantity 10 shares; sellable now 10 shares; average price 70,000 KRW; current price 71,200 KRW; valuation 712,000 KRW; profit +12,000 KRW; return +1.71%
  ```
  - 409(확인된 KIS 연결 없음)이면 `/broker-status`를 불러 지금 `_status`의 연결 상태 문장(없음 / 다른 증권사·미확인 상태와 회원 웹 안내)을 돌려준다. 연결 확인 질문도 이 액션으로 답한다.
- **account + name:**
  ```
  삼성전자(005930) in the user's account at 2026-10-04 14:05 KST, as reported by the broker. Amounts in KRW.
  Held: quantity 10 shares; sellable now 10 shares; average price 70,000 KRW; current price 71,200 KRW; valuation 712,000 KRW; profit +12,000 KRW; return +1.71%.   (보유가 없으면 "Held: none.")
  Buyable now, as calculated by the broker without margin on a market-order basis (unit price used by the broker 71,200 KRW): up to 140 shares, amount 9,968,000 KRW.
  ```
- **quote:** 지금 글과 같고 거래대금만 억 원으로("trading value 1,234.56억 원"). `_NO_SESSION_DATE` 유지.
- **history prices:** 옛 계획 반영 절 그대로(일·주봉·월봉 KIS 기준일, 오래된 것부터, 줄 수, 수정주가, 진행 중 줄 안내, 사용자가 일 단위를 원했는데 아니면 말하라). 거래대금은 억 원, 지수는 points.
- **history investors:**
  ```
  Net buying in 삼성전자(005930) by investor group over the last 1 month, daily rows, oldest first, 21 rows, looked up at 2026-10-04 14:05 KST. Positive = net buying, negative = net selling; amounts in 억 원 (KRW 100 million), volumes in shares. The newest row may be a session still in progress.
  - 2026-09-04: close 71,800 KRW (+600 KRW); individuals -1,234,567 shares, -88.12억 원; foreigners +1,000,000 shares, +71.80억 원; institutions +234,567 shares, +16.32억 원
  ```
  - 시장은 "in the KOSPI market", 종가 대신 "index 2,612.34 points".
  - 옛 investors는 최근 먼저였다. history는 prices와 같이 **오래된 것부터**로 하나(추이·차트 순서).
- **ranking:** 지금 글과 같고 금액만 억 원으로(시가총액·거래대금·순매수 금액·공매도 금액). 첫 줄의 순위 이름은 `data`+`by`로. investors 순위의 "KIS 잠정 집계라 history investors의 확정 값과 다를 수 있다" 문장은 액션 이름만 바꿔 유지.
- 실패 문구(`Broker {action} lookup failed: …`)·409 안내·503·응답 형식 오류 처리는 지금과 같다.

## 6. 실호출 확인 (Broker 배포·IAM 뒤, 사용자 승인 후, 본인 회원, 조회만)

| # | 조회 | 예상 KIS 조회 요청 | 확인할 것 |
|---|---|---|---|
| 1 | `account` | 잔고 page 수(보통 1, 최대 2로 셈) | `cash_d2` 필드와 값이 KIS 앱의 D+2 예수금과 같은지 |
| 2 | `account` + `005930` | 3 + 잔고 page | `position`, 매수 가능 그대로 |
| 3 | history prices `005930` `1m` | 1~2 | 일봉, 거래대금 단위 |
| 4 | history prices `005930` `6m` | 1~2 | 주봉 기준일, 첫·마지막 부분 주 |
| 5 | history prices `kospi` `3m` | 2~3 | 지수 50줄 묶음, 이어 부르기 경계 겹침·빠짐, 소수 |
| 6 | history prices `kosdaq` `5y` | 2~3 | 월봉 기준일, 지수 거래량·거래대금 유무 |
| 7 | history investors `005930` `1m` | 1~3 | 한 번 줄 수, 끝 날짜 이어 부르기 동작, 수량·금액 단위 |
| 8 | history investors `kospi` `1m` | 1~3 | 날짜 둘의 의미, 한 번 줄 수, 단위 |
| 9 | history investors `kosdaq` `1m` | 1~3 | 코스닥 코드(`KSQ`·`1001`) |

- **승인 예산: KIS 조회 요청 최대 26번**(위 최댓값 합). 세면서 진행하고 26번에 닿으면 남은 조회를 멈춘다. 토큰 발급은 따로 센다(유효 토큰이면 0). 추가 호출은 새 승인.
- 결과에 따라: 단위 표(§3) 채우기, 시장 일별 API 날짜 인자 확정. 이어 부르기가 문서와 달리 동작하면(끝 날짜를 무시하고 같은 줄을 다시 줌 등) 규칙 3이 schema error로 막으므로 반쪽 결과는 나가지 않는다. 그때는 그 data·기간을 열지 않거나 계획을 고쳐 Codex 검토를 다시 받는다.
- 결과는 이 계획서에 기록한다(값이 아니라 형식·단위·줄 수·지연만. 계좌 금액·보유는 적지 않는다).

## 7. 시나리오 테스트 (`broker` 세트)

### 7.1 왜 새 세트인가

- `read` 세트는 broker 도구 없이 웹 검색 흐름을 본다(12·24번 주가 질문의 기대값이 `web_search`). broker 도구를 넣으면 그 흐름이 사라진다.
- `execution` 세트는 주문 도구만 둔다. 지금 어느 세트도 broker 조회 도구를 검증하지 않는다.

### 7.2 `smoke_flow.py` 변경

- `SCENARIO_SETS`에 `broker`. 이 세트는 검색 도구·본문 추출 + `BrokerReadTool` + `BrokerOrderTool`을 함께 등록한다(실제 PIA와 같은 조합).
- 시스템 프롬프트에 PIA `BROKER_READ_PROMPT`·`ORDER_PROMPT`와 같은 뜻의 문장을 더한다(PIA §9의 새 문구를 복사, 출처 주석).
- 가짜 Broker(`_fake_broker`)에 `/account`(code 유무), `/history`, `/ranking`, `/broker-status`를 더한다. 고정 값이고 실제 KIS·AWS는 부르지 않는다. 기존 `/instruments`·`/quote`·`/orders`는 그대로.
- `Record`에 첫 단계의 도구 호출 인자(`first_calls: [(name, arguments)]`)를 담는다.
- 시나리오에 `expect_args`(선택): 첫 단계의 `expect` 도구 호출 중 **하나라도** 이 키·값을 모두 포함하면 통과(나머지 인자는 자유). 예 `{"action": "account", "name": "삼성전자"}`.
- 요약 표는 그대로, CHECK는 사람이 로그로 판단한다(지금과 같음).

### 7.3 `scenarios/broker.json` (한 대화, 순서대로)

| id | 질문 | expect | expect_args | 보는 것 |
|---|---|---|---|---|
| b1 | 증권사 연결돼 있어? | broker | `{"action":"account"}` | 연결 확인이 account로 |
| b2 | 내 계좌 보여줘 | broker | `{"action":"account"}` | |
| b3 | 예수금이랑 D+2 얼마야? | broker | `{"action":"account"}` | D+2 |
| b4 | 삼성전자 얼마나 살 수 있어? | broker | `{"action":"account","name":"삼성전자"}` | 주문으로 가지 않음, 계산하지 않음 |
| b5 | 삼전 몇 주 있고 지금 팔 수 있는 건 몇 주야? | broker | `{"action":"account","name":"삼성전자"}` | 별칭, 매도 가능 |
| b6 | SK하이닉스 지금 주가 얼마야? | broker | `{"action":"quote","name":"SK하이닉스"}` | 웹 검색이 아니라 quote |
| b7 | 삼성전자 3개월 주가 흐름 알려줘 | broker | `{"action":"history","data":"prices","name":"삼성전자","period":"3m"}` | quote가 아니라 history |
| b8 | 코스피 1년 추이 차트로 보여줘 | broker | `{"action":"history","data":"prices","market":"kospi","period":"1y"}` | 지수, 주봉 안내, 차트 블록 |
| b9 | 삼성전자 최근 한 달 외국인 순매수 추이 | broker | `{"action":"history","data":"investors","name":"삼성전자","period":"1m"}` | |
| b10 | 요즘 코스닥에서 외국인이 사고 있어? | broker | `{"action":"history","data":"investors","market":"kosdaq"}` | 시장 전체 |
| b10-1 | 삼성전자 1년 외국인 순매수 추이도 보여줘 | (관찰) | | 투자자 동향은 1개월까지라고 말하고 지어내지 않는지 |
| b11 | 오늘 시가총액 상위 5개 | broker | `{"action":"ranking","data":"prices","by":"market_cap","count":5}` | |
| b12 | 코스피 외국인 순매수 상위 종목 | broker | `{"action":"ranking","data":"investors","by":"foreign_buying","market":"kospi"}` | |
| b13 | 일주일 기준 공매도 많은 종목 | broker | `{"action":"ranking","data":"short_selling","period":"1w"}` | |
| b14 | 요즘 사람들이 많이 보는 종목은? | broker | `{"action":"ranking","data":"attention"}` | |
| b15 | 삼성전자 시가총액 1년 추이 보여줘 | (관찰) | | 도구에 없는 데이터: 지어내지 않고 없다고 말하는지 |
| b16 | 삼전 10주 사줘 | order | | 조회와 주문 구분 |
| b16-1 | 아니 하지 마 | answer | | 주문 보내지 않음 |

- 기대값의 단위 확인: 가짜 Broker 값으로 결과 글이 억 원·원으로 나오고, 답변이 그 단위 그대로인지 로그에서 본다(b3·b11·b12).
- 실행은 유료 모델·검색 호출이다. 구현과 Codex 구현 검토 뒤, PIA 연동 전에 사용자 승인을 받고 돌리고 `records/{날짜}_broker.md`에 기록한다. 모델 판단이 흔들린 CHECK는 도구 설명을 고칠지 사람이 판단한다.

### 7.4 오프라인 테스트 (pytest, 비용 없음)

- `broker.json`의 모든 `expect_args`가 새 schema와 §2.1 검사를 통과한다(시나리오가 없는 인자 값을 기대하지 않게).
- 가짜 Broker가 모든 액션·데이터에 대해 `BrokerReadTool`의 정상 결과를 낸다("lookup failed"가 없다). 스크립트가 경로 변경으로 조용히 깨지지 않게 하는 최소 검사다.

## 8. 옛 경로 제거 (PIA 전환 확인 뒤)

- Broker: `/investors` route·코드(`get_stock_investors`·`get_market_investors`·`Investors` JSON)·템플릿 route 제거. PIA bootstrap: `BrokerInvestorsApiArn`과 정책 항목, `test_dev_deployment.py`·`ops/dev_deployment_rollout.md`의 해당 줄 제거. 둘 다 AWS 변경, 사용자 승인.
- `TradingService.buyable` 이름 등 남는 옛 이름은 이번에 바꾼다(§4.1). 그 밖의 정리는 다음 "harness·Broker 정리" 작업으로.

## 9. PIA 연동

- harness 핀 0.8.0(권고: 도구 인자 구조가 바뀌므로 중간 자리. 릴리스 때 사용자 확인).
- `BROKER_READ_PROMPT`(`app/core.py:40`): "PIA can use the broker tool to look up the user's account (cash, holdings, and what one stock they can buy or sell), live quotes, price and investor history for a stock or the KOSPI/KOSDAQ market, and market rankings." `tests/test_core.py`·`test_main.py`는 상수를 import해서 비교하므로 문구만 바뀐다.
- PIA bootstrap IAM(§1)은 PIA 저장소 변경이다. Broker 배포 뒤, 실호출 확인 전에 적용해야 Bot role로 `/history`를 부를 수 있다.
- README: harness(도구 표·결과·단위), Broker(§6 계좌·매수 가능, §7 순위·투자자 → history), PIA(broker 도구 한 줄).

## 10. 검증 (오프라인)

- **Broker:** `cash_d2`; `/account?code=`의 `position`(있음·없음·수량 0 행 제외); history 기간·단위·시작일(말일); 이어 부르기(1번 멈춤, 2·3번, 시작일 이하 멈춤, 빈 묶음 멈춤, 상한 초과 실패, 빈 줄 버림, 필수 값 빈 줄 오류, 겹침·역순·같은 날 오류, 중간 실패 시 전체 실패, 시작일 이전 줄 버림); 지수 소수 보존; investors 세 집단 필수; 입력 400(`all`, 대상 없음, investors `6m`); route JSON; 연결 검사 409(기존 경로와 같음); 기존 `/quote`·`/ranking`·`/broker-status` 응답 불변.
- **harness:** 표에서 나온 schema enum과 설명; 기본값(data·by·period 첫 값, ranking market all, count 10); 검사(액션별 허용 값, history 대상 규칙, investors 기간, 안 쓰는 인자 무시); `by` → Broker 이름(`short_volume`); 단위 변환(원→억, 백만→억, 천 주→주, 반올림·끝 0, 지수 소수); 결과 글(액션별 첫 줄 단위, 오래된 것부터, 줄 수, 진행 중 안내, 보유 없음); 409 → `/broker-status` 문장; 실패 문구.
- **PIA:** 지금 전체 suite(DynamoDB Local, 건너뜀 0) + 후보 이미지 build.

## 11. 순서

1. 이 계획 → Codex 계획 검토 → 반영.
2. Broker 구현(pia-broker `claude/broker-unify`) → harness 구현(이 브랜치, 시나리오 스크립트·세트 포함) → Codex 구현 검토.
3. Broker 병합·배포(Codex, 사용자 승인) → PIA bootstrap IAM `/history` 추가(Codex, 사용자 승인) → 실호출 확인(§6, 사용자 승인, 26번 예산) → 결과로 단위 표 확정, 필요하면 수정·재검토.
4. `broker` 시나리오 실행(유료, 사용자 승인) → 기록 → 필요하면 도구 설명 수정.
5. harness 릴리스 0.8.0(버전 사용자 확인) → PIA 핀·프롬프트·README(pia `claude/broker-unify`) → PIA 검토·병합·배포(승인) → Telegram 확인.
6. 옛 `/investors` 제거(§8, Broker·IAM, 승인).
7. `claude/broker-prices` 브랜치(두 저장소)는 이 계획으로 대체되므로 병합하지 않고 닫는다(Codex).

## 12. Codex 검토에서 특히 볼 것

1. §1 경로: `/history` 새 경로 + `/investors` 나중 제거 vs 다른 방식. 배포된 v0.7.2 호환을 지키는지.
2. §4.2 이어 부르기 규칙 하나로 바꾼 것(옛 "짧은 묶음" 규칙 대체)의 타당성, 상한 3번이 네 API에 맞는지.
3. §3 단위 변환을 harness 한 곳(필드별 KIS 단위 표)에 둔 것. 단위 자체(계좌 원, 시장 억 원)는 사용자가 정했다.
4. §2.1 기본값 규칙(목록의 첫 값)과 history 기본 `1m`.
5. §6 실호출 목록·예산(26번)이 확인할 것을 빠짐없이 덮는지, 줄일 것이 있는지.
6. §7 시나리오 세트가 헷갈리기 쉬운 경우(account+name vs order, quote vs history, history vs ranking, 시장 vs 종목)를 덮는지.
