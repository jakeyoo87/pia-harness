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

**우선순위:** 이 "계획" 절은 앞부분(합의 기록)의 옛 문장(짧은 묶음 종료 규칙, prices 기본 3m·9회 예산, "금액은 모두 억 원")을 대체한다. 앞부분은 이력이다. 끝의 "Codex 계획 검토 반영" 절이 이 절보다 우선한다.

## 0. 바뀌지 않는 것 (제약)

- **Telegram `/status` 명령:** 모델을 거치지 않고 PIA가 Broker `GET /internal/members/{id}/broker-status`를 직접 부른다(`app/broker_status.py`). 이 경로·응답은 그대로 둔다.
- **주문 도구:** 확인 문구의 현재가를 Broker `GET /quote`로 받는다(`broker_order.py`). `/quote` 응답은 그대로 둔다(필드 추가만 가능).
- **탈퇴 작업자:** `DELETE /broker-data`. 그대로.
- **호환성은 고려하지 않는다(사용자, 2026-10-04: 테스트 운영).** 옛 `/investors` 경로와 그 IAM은 이번에 지우고, Broker·PIA IAM·harness·PIA를 한 번에 배포한다. 그 사이 잠깐 맞지 않는 것은 받아들인다.
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
| (없어짐) | `GET /investors` | 이번에 제거 |

- 이유: 기간 시세와 기간 투자자 동향은 둘 다 "기간·날짜별 줄"이라 한 경로·한 응답 틀(`rows`)에 맞고, 도구 `history`와 같아서 한눈에 보인다. `/investors`(종목 30일·시장 오늘 하루)는 기간 기준과 맞지 않아 `/history`로 옮기고 지운다.
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
- **history 대상:** `name`이 있으면 그 종목, 없으면 `market`(`kospi`·`kosdaq`, 그 시장 자체). `name`이 있으면 `market`은 쓰지 않는다(검사도, Broker 전달도 안 함). `name`이 없을 때만 `market`을 보고, 없거나 `all`이면 실행하지 않고 "종목이나 kospi·kosdaq를 정하라"고 돌려준다. history의 `market`에는 기본값이 없다.
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
- 변환은 harness 한 곳의 표(필드 → KIS 단위)로 한다. Broker는 KIS 값을 그대로 준다. 시장 금액만 둘째 자리로 반올림하고 끝 0은 지운다.
- KIS 단위(실호출로 확인된 것): 시가총액 `hts_avls`·`stck_avls` 억 원, 거래대금·공매도 금액 원, 순위 순매수 금액 백만 원, 시장 투자자(오늘) 수량 천 주(2026-10-01·02 확인). 새 API(기간 시세 거래대금, 일별 투자자 두 개)의 단위는 §6에서 확인하고 표를 채운다. **확인 전에는 harness를 릴리스하지 않는다.**
- 결과 첫 줄에 그 결과의 단위를 적는다(예: "amounts in 억 원 (KRW 100 million), volumes in shares").

## 4. Broker 구현

### 4.1 account

- `AccountSummary.cash_d2`: 잔고 `output2`의 D+2 예수금. 필드는 `prvs_rcdl_excc_amt`(가수도정산금액, 흔히 D+2 예수금)로 보고 §6에서 확인. 없거나 읽을 수 없으면 `null`(다른 합계 값과 같은 규칙).
- `GET /account?code=`: `TradingService.buyable` → `stock_account(member_id, code)`로 바꾸고 현재가 → 매수 가능 → 잔고(모든 page)를 부른다. 응답 `{"buyable": {...지금과 같음}, "position": {...positions[]의 한 행과 같은 필드} | null}`. 보유 행은 잔고 결과에서 코드가 같은 행(수량 0 행은 이미 빠짐). KIS 호출 2 → 2 + 잔고 page 수. 잔고가 실패하면 조회 전체가 실패한다(부분 결과 없음).

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
  - 가격은 문서상 3번 안에 끝난다. 투자자 두 API는 이어 받기가 문서에 없어 실호출로 확인한다. 안 맞으면 그 data를 열지 않는다(옛 30일 API로 대체하지 않음).
  - 지연은 실호출에서 잰다. 시간 설정은 바꾸지 않는다.
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
| 2 | `account` + `005930` | 2 + 잔고 page | `position`, 매수 가능 그대로 |
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
| b8 | 코스피 1년 추이 보여줘 | broker | `{"action":"history","data":"prices","market":"kospi","period":"1y"}` | 지수, 주봉 안내 |
| b9 | 삼성전자 최근 한 달 외국인 순매수 추이 | broker | `{"action":"history","data":"investors","name":"삼성전자","period":"1m"}` | |
| b10 | 요즘 코스닥에서 외국인이 사고 있어? | broker | `{"action":"history","data":"investors","market":"kosdaq"}` | 시장 전체 |
| b10-1 | 삼성전자 1년 외국인 순매수 추이도 보여줘 | (관찰) | | 투자자 동향은 1개월까지라고 말하고 지어내지 않는지 |
| b11 | 오늘 시가총액 상위 5개 | broker | `{"action":"ranking","data":"prices","by":"market_cap","count":5}` | |
| b12 | 코스피 외국인 순매수 상위 종목 | broker | `{"action":"ranking","data":"investors","by":"foreign_buying","market":"kospi"}` | |
| b13 | 일주일 기준 공매도 많은 종목 | broker | `{"action":"ranking","data":"short_selling","period":"1w"}` | |
| b14 | 요즘 사람들이 많이 보는 종목은? | broker | `{"action":"ranking","data":"attention"}` | |
| b15 | 삼성전자 시가총액 1년 추이 보여줘 | (관찰) | | 도구에 없는 데이터: 지어내지 않고 없다고 말하는지 |

- 기대값의 단위 확인: 가짜 Broker 값으로 결과 글이 억 원·원으로 나오고, 답변이 그 단위 그대로인지 로그에서 본다(b3·b11·b12).
- 실행은 유료 모델·검색 호출이다. 구현과 Codex 구현 검토 뒤, PIA 연동 전에 사용자 승인을 받고 돌리고 `records/{날짜}_broker.md`에 기록한다. 모델 판단이 흔들린 CHECK는 도구 설명을 고칠지 사람이 판단한다.

## 8. 옛 경로 제거

- 옛 `/investors` route·코드와 PIA의 `BrokerInvestorsApiArn`은 이번에 지운다(호환성 고려 안 함).

## 9. PIA 연동

- harness 핀 **0.7.3**(사용자, 2026-10-05: 릴리스는 아직 0.7대에서).
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
5. harness 릴리스 0.7.3(사용자 결정) → PIA 핀·프롬프트·README(pia `claude/broker-unify`) → PIA 검토·병합·배포(승인) → Telegram 확인.
6. `claude/broker-prices` 브랜치(두 저장소)는 이 계획으로 대체되므로 병합하지 않고 닫는다(Codex).

## 12. Codex 검토에서 특히 볼 것

1. §1 경로: `/history` 새 경로 + 옛 `/investors` 제거 vs 다른 방식.
2. §4.2 이어 부르기 규칙 하나로 바꾼 것(옛 "짧은 묶음" 규칙 대체)의 타당성, 상한 3번이 네 API에 맞는지.
3. §3 단위 변환을 harness 한 곳(필드별 KIS 단위 표)에 둔 것. 단위 자체(계좌 원, 시장 억 원)는 사용자가 정했다.
4. §2.1 기본값 규칙(목록의 첫 값)과 history 기본 `1m`.
5. §6 실호출 목록·예산(26번)이 확인할 것을 빠짐없이 덮는지, 줄일 것이 있는지.
6. §7 시나리오 세트가 헷갈리기 쉬운 경우(account+name vs order, quote vs history, history vs ranking, 시장 vs 종목)를 덮는지.


## Codex 계획 검토

검토일: 2026-10-04 (Asia/Seoul). 대상 `2bcfe04336e3977e18e72da22c5d7176c5ebc3c7`, 대조 harness main v0.7.2 `6dffbc4`, Broker main `9b2cb69`, PIA main `7e37ee3ec`, 가격 계획의 `87ddba2`/`2defac9` 반영과 공개 KIS 자료. 실제 KIS·AWS·유료 모델/검색을 호출하지 않았다.

### 판정

합의된 액션 4개·인자 6개와 **계좌 금액/가격 원, 시장 금액 억 원, 수량 주**, **investors history 1m만**은 그대로 따른다. 계획의 새 history 경로·단계적 전환·harness 단위 변환 방향은 타당하다. 아래 호환 종료 조건·대상 검사 모순·미확정 API 동작과 검증 범위를 보완한 뒤 구현한다. 지금은 계획 검토이며 구현 완료·릴리스·실호출 승인 판정이 아니다.

### 1. 경로·구버전 호환·옛 경로 제거 (§1·§8)

- `/history` 새 경로 권고. 기존 `/investors`의 API/응답 의미를 바꾸는 것보다 구버전과 신규 기간 결과를 분리하기 쉽다. 기존 quote/status/orders/broker-data는 유지하고, history의 정확한 GET invoke ARN 하나만 추가한다. account의 cash_d2/position 필드 추가는 v0.7.2 parser가 읽는 기존 필드를 그대로 두면 구조적으로 호환된다. account?code의 buyable 객체 필드·단위·오류 계약은 유지하며 호출 지연 변화는 검증한다.
- **[P2] 옛 `/investors` 제거 조건에 롤백 호환을 넣는다.** PIA가 새 harness로 정상 응답했다는 확인만으로는 충분하지 않다. `ops/deploy_dev_bot.py`는 현재/previous_image를 보존하고 건강 검사 실패 시 이전 이미지로 되돌린다. 이전 이미지가 v0.7.2이면 옛 경로와 IAM ARN을 다시 필요로 한다.
- 제거 전 자동 롤백 대상으로 유지하는 이미지가 모두 history를 쓰는 버전인지 확인한다. 구버전 롤백을 유지하는 동안에는 옛 route·응답·IAM을 같이 남긴다. 옛 롤백을 포기한다면 별도 사용자 결정으로 명시한다. 강제로 이미지나 release record를 정리해서 조건을 맞추지 않는다. 역할·route를 바꾸는 AWS 변경은 별도 승인 대상이다.
- Broker 선배포 호환 테스트에 v0.7.2의 **account 전체·buyable(code)·investors(종목/시장) 파서 실행**도 포함한다. §10의 quote/ranking/status 불변만으로는 새 코드가 건드리는 account와 아직 남겨야 할 investors를 충분히 확인하지 못한다. 필드를 추가했지만 잔고 실패·다중 page 때문에 기존 buyable 전체가 실패하는 경로도 계획한 계약으로 검증한다.

### 2. 네 API의 이어 조회와 총 3회 (§4.2)

- 빈 정상 묶음 또는 시작일에 닿으면 완료, 그 밖에는 날짜를 뒤로 옮기며 총 3회, 중간 오류/진행 불가/예산 소진은 전체 실패라는 **공통 수집 규칙은 적절하다**. 짧은 묶음의 행 수로 완료를 추측하지 않아 새 API 한도를 몰라도 부분 기간을 성공으로 내지 않는다. 요청 생성·output 경로/행 해석은 API별로 두되 수집 loop 하나면 충분하다. 새로운 pagination class/여러 fallback 방식은 필요 없다.
- [Postman v2.6 고정 커밋](https://github.com/koreainvestment/open-trading-api/blob/277ec0e/legacy/postman/실전계좌_POSTMAN_샘플코드_v2.6.json)의 종목/지수 가격 요청은 각 100/50건 및 최저 날짜 전날 재조회 안내가 있다. 검토에서 해당 공개 파일을 직접 읽어 확인했다. 가격은 이 날짜 방식 하나를 선택하고 최신 지수 예제의 tr_cont를 동시에 붙이지 않는다.
- **네 API에 모두 3회면 된다는 보장은 아직 없다.** [종목 일별 투자자 예제](https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/domestic_stock/investor_trade_by_stock_daily/investor_trade_by_stock_daily.py)는 J·날짜 하나·빈 조정/기타 값·FHPTJ04160001/output2와 tr_cont 흐름을 보여 준다. [시장 일별 투자자 예제](https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/domestic_stock/inquire_investor_daily_by_market/inquire_investor_daily_by_market.py)는 U·날짜 둘을 같은 날짜·KSP/KSQ·FHPTJ04040000/output로 읽는다. 단일 응답 크기나 날짜 전날 방식의 완료는 이 예제로 확정되지 않는다.
- 따라서 §4.2의 '지금 기간표에서 모두 3번 안'은 **가격의 문서상 예상과 투자자의 검증 대기**로 분리한다. 두 투자자 API에서 같은 날짜/날짜 전진·종료 동작이 안 맞으면 부분 결과를 내지 않고 해당 기능을 노출하지 않는 §6 gate는 맞다. 그때 사용자가 정한 기간을 조용히 줄이거나 30일 구 API를 자동 fallback하지 않는다. API가 다른 진행 방식을 요구하면 그 사실로 계획을 다시 검토한다.
- 종료 검사는 3회째 받은 정상 묶음을 처리한 뒤 한다. 유효한 빈 목록만 완료로 보고 missing output/실패 rt_cd는 오류다. 날짜의 유효성·엄격한 감소·요청 끝날짜 범위, 필수 값, 전부 빈 padding과 실제 손상 행 구분을 한 규칙으로 검사한다. 시작일 이전 행은 종료 판정 뒤 거른다.
- 3×8초는 가격/투자자 GET만의 한도다. 최초 Token 준비·인증 실패 후 재조회·Secrets/Dynamo 읽기까지 합하면 Lambda 30초를 보장하지 않는다. §4.2의 '30/35초 안'은 보장 표현을 빼고 전체 지연 검증 대상으로 적는다. 기존 시간 제한을 확대하거나 새 재시도 계층을 넣지 않는다. 인증 재조회도 실제 GET 횟수/시간 예산에 들어간다.

### 3. 단위 변환 표 (§3)

- harness 한 곳에서 변환하고 Broker의 기존 원시 값·응답 단위를 유지하는 설계에 찬성한다. **단위 표의 key는 단순 정규화 필드명만이 아니라 출처/결과 문맥 + 필드**여야 한다. 예를 들어 같은 net_volume 필드도 기존 종목은 주, 기존 시장 오늘 결과는 천 주이고, 새 시장 일별/지수 volume은 아직 단위가 미확정이다. close도 종목 가격과 시장 지수 points의 문맥이 다르다.
- API별 실제 raw 단위를 한 표에 기록하고 action/data/종목·시장 문맥으로 고른다. 새 결과 타입/공통 계층을 만들거나 모델에게 TR ID를 노출할 필요는 없다. 새 API는 확인 전 출시하지 않는 계획을 유지한다. 숫자 크기로 단위를 추정하지 않는다.
- 시장 금액만 Decimal의 ROUND_HALF_UP 두 자리 표기로 정리한다. 원→억 / 백만→억 / 이미 억은 변환 없음, 천 주→주를 정확한 scale로 검증한다. 가격·계좌 값·지수 points·수량에 시장 금액의 소수 두 자리 규칙을 일괄 적용하지 않는다. null은 unknown으로, 음수 순매도/손실 부호와 0은 그대로 남긴다. float에 먼저 산술한 뒤 Decimal로 바꾸지 않는다.
- 같은 경제적 값의 서로 다른 raw 단위가 같은 결과가 되는 입력, 양/음의 반올림 경계, 이미 억인 시총의 이중 변환 방지를 오프라인 검사한다. 이는 사용자가 정한 단위 자체를 다시 검토하는 것이 아니라 100배/1000배 오류를 막는 유일한 변환 계약이다.

### 4. 기본값·인자 검사 (§2)

- data/by/period의 첫 값 규칙과 history 기본 1m는 적절하다. schema에 목록만 있다고 실행 때 기본값이 주입되지는 않으므로 설명·검사·실행이 같은 표를 쓰고 생략 시 해석 결과를 검사한다. period가 안 쓰이는 ranking prices의 잘못된 period 등은 검사/전송하지 않는다. 기본값은 '생략'이지 모든 falsy 값은 아니다. count=0/False와 active enum의 빈 값·배열·객체를 기본값으로 바꾸지 않는다.
- **[P2] history 대상 우선순위의 검사 문장을 일치시킨다.** §2.1의 'name 있으면 종목, 둘 다 있으면 name' 및 '쓰는 인자만 검사'에 따르면 name이 있는 요청에서는 market을 쓰지 않는다. 따라서 같은 절의 'market=all이면 실행하지 않음'은 **name 없는 지수/시장 조회에만** 적용한다. 종목 name+market=all 요청을 잘못 거절하거나 all을 Broker에 전달하지 않는다. §4.2 서버도 code가 있으면 종목이고 쓰지 않는 market은 결과에 의미를 주지 않는다.
- history의 market은 목록 첫 kospi를 자동 기본값으로 삼지 않는다(대상을 묻는 계약). name 없는 경우 명시된 kospi/kosdaq만 허용한다. ranking의 market 기본 all은 계획대로 별도의 대상 기본값이다. enum 검사 전에 문자열 여부를 확인하고 사용 여부→타입/값→기본값/파생 by 처리라는 표 기반 공통 흐름을 쓴다.
- short_volume→기존 Broker short_selling 매핑은 harness 안에 한 번이면 된다. most_viewed는 실제 전체 시장 기준임을 밝혀 선택 시장으로 필터됐다고 말하지 않는다. user 구조를 바꾸는 새 action·index 인자는 필요 없다.

### 5. 실호출 목록과 26회 상한 (§6)

- **계좌 호출 수를 정정한다.** 현재 구현의 현재가+매수 가능은 2회다. 잔고 p page를 더하면 **2+p**(한 page일 때 총 3회)이며 §4.1/§6의 '3+잔고 page'는 한 번 과다 계산했다. 표에서 p≤2로 가정하면 기존 목록 합은 26이 아니라 25다. 하지만 현재 잔고는 max_pages=20이고 p≤2는 보장되지 않으므로 어느 쪽도 실제 worst-case의 증명은 아니다.
- 26은 **실행 전체의 단일 hard budget**으로 유지하고, 이를 '모든 확인이 반드시 26회 안에 완료'라는 뜻으로 쓰지 않는다. KIS GET 발행 시점에서 세어 page/인증 재조회도 포함한다. Broker HTTP 요청 1번을 KIS 1번으로 세면 안 된다. Token 발급 POST는 별도로 세며, 추가 GET은 새 승인이다. 현재는 실호출을 승인받지 않은 계획이다.
- 빠진 중요한 조합은 **종목 월봉(수정주가·거래대금/거래량)**과 **지수 주봉(50행 경계·주 기준일)**이다. 기존 목록에는 stock D/W, index D/M만 있다. 1m/3m, 6m/1y, 3y/5y는 코드가 같은 D/W/M로 묶이므로 모든 period를 따로 호출할 필요는 없지만 API×단위 조합은 확인 목록에 넣는다. 기간 시작일 계산 자체는 가짜 날짜로 검증한다.
- 목록을 먼저 탐색 조회(투자자 요청 날짜/행 수/단위)와 경계 조회(이어 받기·주·월 날짜)로 정렬하고 26회 안에서 우선순위를 정한다. 한 응답에서 행 수·raw 단위·날짜·진행 중 여부를 같이 확인해 단위별 중복 호출을 줄인다. 예산이 부족하면 미확인 항목을 명시하고 멈춘다. 표에 검증 조합을 더한다는 이유로 자동으로 승인 예산을 늘리지 않는다.
- D+2 후보 필드를 예수금/매수 가능과 혼동하지 않고 앱의 같은 기준 값과 비교하는 것은 적절하다. position 없음/수량 0·미확인 연결·input 400·중간 page 오류는 가짜 응답으로 확인해 실제 계좌/주문/인증 실패를 만들지 않는다. 기록은 형식·단위·page 수·지연만이며 실제 금액/보유/credential은 남기지 않는다.

### 6. broker 시나리오 (§7)

- 기존 read/execution 세트를 유지하고 broker 전용 세트를 두는 것은 타당하다. 계좌·주문 가능·quote/history·시장/종목·ranking의 혼동과 b10-1/b15의 지원하지 않는 요청을 포함한다. 기본값을 첫 실제 tool call의 raw JSON 인자에서 확인하는 것과 실행된 기본값에서 확인하는 것은 구분한다. by/period를 생략해도 valid인 b13/b14가 명시적 기본값을 꼭 출력해야만 통과하게 만들지 않는다.
- **추가로 필요한 최소 검증:** 가짜 account 409→broker-status의 없음/NH/미확인 상태(오프라인이면 충분), history name+market 불필요 값, 모호한 종목/찾지 못함, 읽기 질문에서 order/confirm로 잘못 가지 않는지, b16-1 이후 가짜 `/orders` 발행 수가 0인지. '첫 call 중 하나에 기대 인자 포함'만으로는 broker와 잘못된 order를 같이 고른 경우를 PASS로 만들 수 있다. 같은 Record의 tool 목록·가짜 주문 목록을 사용해 확인하며 새 scenario framework는 만들지 않는다.
- 현재 smoke_flow.py의 SYSTEM_PROMPT는 짧은 일반 지침이고 MEMORY_INSTRUCTION은 holdings를 기억 대상으로 둔다. 실제 PIA는 보유/계좌/주문을 장기 Memory에서 제외하고 chart JSON 형식을 SYSTEM_PROMPT로 알려 준다. Broker/ORDER 문장만 복사하면 **b8 차트 블록 확인과 금융 Memory 동작이 실제 PIA와 다르다**. 해당 지시도 PIA와 같은 뜻으로 맞추거나 차트 검증은 PIA 전송 테스트로 분리한다. 두 저장소를 잇는 새 prompt package는 만들지 않는다.
- 단위의 실제 변환·409 fallback·기본값·요청 경로는 비용 없는 deterministic 테스트가 맡고, 유료 세트는 모델이 질문의 대상을/데이터를 고르는지와 unsupported 설명을 관찰한다. 지금은 세트를 실행하지 않는다. 합의한 investors 1m 지원을 늘리거나 월별 합계를 계산해 b10-1을 맞추지 않는다.
- 신규 history를 날짜가 있다고 무조건 '확정 값'으로 말하지 않는다. §5의 ranking과 history의 차이는 집계 기준 차이이고 newest row는 진행 중일 수 있다. 공식 집계 확정 시점을 확인하지 않았다면 기존 'history 확정 값' 문구를 옮기기보다 두 조회의 집계 기준이 달라질 수 있음을 밝힌다.

### 문서·릴리스·범위

- 앞부분의 '짧은 묶음 종료', 옛 prices 기본 3m/9회, 옛 모두 억 원 합의 인용은 §계획의 새 규칙/최신 사용자 결정으로 대체되는 참고 이력임을 명확히 한다. 같은 문서의 두 규칙을 구현하지 않는다. release 0.8.0은 아직 권고이며 실제 태그/핀은 사용자에게 버전을 확인한 뒤 정한다.
- `claude/broker-prices`의 계획 브랜치를 닫는 단계는 전환 작업의 마지막 정리로 유지하되 이번 계획 검토에서 삭제하지 않는다. 새 경로 IAM·Broker 배포·실호출·harness 릴리스·PIA 배포·옛 route 제거는 각각 계획한 사용자 승인 뒤 수행한다. AWS 검증 권한을 확보한 주체가 실제 change set 조건을 확인하며 EC2 권한을 자동 확대하지 않는다.
- 이번 변경은 계획서 끝의 검토 기록뿐이다. 코드·테스트·branch 구조·AWS·실제 KIS·유료 시나리오는 변경/실행하지 않았다. 현재 코드와 공개 공식 예제/고정 Postman을 대조했고 문서 공백 검사 후 같은 브랜치에 커밋·push한다.

## Codex 계획 검토 반영 (Claude, 2026-10-04, simple-first로 다시 평가)

사용자 지시(2026-10-04): 과한 것은 반영하지 않는다. simple-first, 과한 예외 방지, 범용성으로 다시 평가했다. 받은 것은 본문에 직접 고쳤다.

| 지적 | 판단 | 어떻게 |
|---|---|---|
| 1. 롤백 대비 `/investors` 제거 조건 | 받지 않음 | 호환성을 고려하지 않으므로 이번에 지운다(§8) |
| 1. v0.7.2 도구로 새 응답 확인 | 받지 않음 | 호환성을 고려하지 않는다 |
| 1. 잔고 실패 시 매수 가능 전체 실패 | 받음(한 줄) | §4.1. 가짜 응답 테스트 하나 |
| 2. 투자자 API 이어 받기 미확정 | 받음 | §4.2: 실호출로 확인, 안 맞으면 열지 않음 |
| 2. 판정 순서·요청 끝 날짜 이하 검사 등 세부 | 일부만 | 묶음 검사는 지금 규칙(필수 값, 전부 빈 줄만 버림, 날짜 엄격히 감소) 그대로. 끝 날짜 범위 검사는 넣지 않는다(감소 검사와 상한 3번으로 충분) |
| 2. 30·35초 "안" 보장 표현 | 받음 | 문구만 뺌 |
| 3. 단위 표를 출처+필드로 | 받음(문서) | 코드는 결과마다 자기 출처를 알므로 변환 함수만 고르면 된다. 새 표 구조·계층 없음 |
| 3. Decimal·ROUND_HALF_UP·반올림 경계 테스트 | 받지 않음 | 표시용 둘째 자리 반올림이라 일반 숫자로 충분. 시장 금액만 둘째 자리 반올림. 출처별 변환 테스트 하나(원·백만 원·억 원·천 주) |
| 4. name 있으면 market 검사 안 함 (P2) | 받음 | §2.1 |
| 4. 생략만 기본값, 0·빈 값은 오류 | 받음(자연히) | `None`이면 기본값, 아니면 검사. 따로 테스트 늘리지 않음 |
| 4. history market 기본값 없음 | 받음 | §2.1 |
| 5. 계좌 호출 수 2+p | 받음 | §4.1·§6 정정 |
| 5. 종목 월봉·지수 주봉 추가 | 받지 않음 | D·W·M은 같은 코드의 인자 하나다. 종목 주봉·지수 월봉으로 기준일 의미를 보고, 나머지는 같은 규칙. 예산 26번 그대로 |
| 5. KIS GET마다 세기, 닿으면 멈춤 | 받음 | §6 그대로 |
| 6. PIA 프롬프트 전체 복사 | 받지 않음 | b8에서 "차트로"를 뺀다(차트는 PIA 테스트가 본다). 메모리 규칙 차이는 도구 선택에 영향 없음. 본문대로 broker·주문 문장만 |
| 6. 조회 질문에 주문 도구가 끼면 실패 | 받음 | `_check`에 조건 하나 |
| 6. `expect_orders`·b16 주문 시나리오 | 받지 않음 | 주문 흐름은 `execution` 세트가 본다. b16·b16-1 삭제 |
| 6. 409 상태·모호한 종목 등 오프라인 테스트 | 기존 것으로 | 이미 있는 테스트를 새 액션 이름으로 옮긴다. 새로 더하는 것은 name+market, 기본값, 단위 변환, 경로 |
| 6. 시나리오 파일·가짜 Broker 검사 pytest(본문 §7.4) | 삭제 | 수동 스크립트의 테스트까지는 과함. 깨지면 실행 로그에 보인다 |
| 6. ranking과 history 투자자 문구 | 받음 | "집계 기준·시점이 다를 수 있다"로. "확정 값"이라 하지 않음 |
| 문서 앞부분 옛 규칙 | 받음 | 계획 첫머리에 우선순위 문장 |

## 구현 기록 (Claude, 2026-10-04)

| 저장소·브랜치 | 커밋 | 내용 |
|---|---|---|
| pia-broker `claude/broker-unify` | `e7b8780` | `/history`(prices·investors, 종목·지수/시장), `/account?code=`에 `position`, `/account`에 `cash_d2`, 템플릿 route `GetHistory`, README §6·§8 |
| pia-harness `claude/broker-unify` | `1f43bcf` | `broker_read.py` 새 액션 4개·표 하나(`_ACTIONS`)·단위 변환, 테스트, `smoke_flow.py` `broker` 세트·`expect_args`, `scenarios/broker.json`(16개), README |
| pia `claude/broker-unify` | `240060ecf` | bootstrap IAM `BrokerHistoryApiArn`(Bot 역할에 `/history` 호출 허용), 테스트·rollout 문서 |
| pia-broker | `c5535db` | 옛 `/investors` route·코드·템플릿 route 제거 |
| pia | `762c9a00a` | `BrokerInvestorsApiArn` 제거. PIA 전체 suite 439개 통과(건너뜀 0) |

- **Broker:** 이어 받기는 `KisReadConnector._dated_rows` 하나(네 API 공통). 기간 날짜 계산은 `TradingService.history`(KST 오늘, `_months_before`). 테스트 227개 통과, ruff·mypy 통과, 패키지 build 성공.
- **harness:** 인자 해석은 `_resolve` 하나(생략만 기본값, 쓰지 않는 인자 무시, history는 name이 있으면 market 무시). 단위는 출처별 배율(`_WON`·`_MILLION`·`_EOK`)과 `_eok` 하나, 시장 금액만 둘째 자리. 일별 투자자 단위(`_INVESTOR_DAY_UNITS`: 종목 주·백만 원, 시장 천 주·백만 원)는 **가정**이며 §6 실호출로 확인한다. 테스트 185개 통과. ruff·mypy 오류 수는 base와 같다(로컬 도구 버전 차이로 base에도 있는 것).
- **시나리오:** `_check`가 broker 인자를 도구와 같은 `_resolve`로 기본값을 채운 뒤 비교하므로, 모델이 기본값을 생략해도 맞으면 PASS다. 조회 질문의 첫 응답에 order·confirm이 끼면 CHECK. 16개 `expect_args`가 모두 유효하고 가짜 Broker가 모든 액션에 정상 결과를 내는 것을 일회성으로 확인했다(유료 실행은 하지 않음).
- **남은 단계(승인 필요):** Broker 병합·배포와 PIA bootstrap IAM(`/history` 추가, `/investors` 제거) → 실호출(§6, KIS GET 26번) → 단위 가정 확인·수정 → harness 0.7.3 릴리스 → PIA 핀·`BROKER_READ_PROMPT`.

## Codex 구현 검토 (2026-10-05, Asia/Seoul)

### 대상·판정

- Broker: `e7b8780`·`c5535db`·`d01db6a`(검토 HEAD `d01db6a12d807c6375a26bfcd1583f340c97a8d3`).
- harness: 구현 `1f43bcf`, 계획 최신 `c831dc12ed6bd5e5a788accfc61ce7ad8b34475f`.
- PIA: `240060ecf`·`762c9a00a`(검토 HEAD `762c9a00a340ce2f567ccf9c160c0adff9e80c2a`).
- 사용자 결정(호환성 고려 안 함, 네 구성 요소 함께 전환, 계좌·가격 원 / 시장 금액 억 원 / 수량 주, investors history 1m)을 그대로 적용했다. 이전 검토의 호환성·Decimal·추가 실호출 조합·새 테스트 체계 권고를 다시 요구하지 않는다.
- **최종 판정: 수정할 P2 세 건.** Broker와 PIA IAM 변경 자체에는 blocker가 없지만, 아래 harness 인자 처리 두 건과 시나리오 판정 한 건을 고친 뒤 전체 최종 검토를 마친다. 실제 응답 단위와 투자자 API 이어 받기는 아직 확인하지 않았으며, 계획대로 실호출 확인 전 harness를 릴리스하지 않는다.

### 수정할 것

1. **[P2] action도 다른 활성 enum과 같은 문자열 검사 규칙을 적용한다.**
   - 파일·행: `src/pia_harness/broker_read.py:530–532`, 오류 전달 경로 `:225–228`; 수동 시나리오 `tests/manual/smoke_flow.py:382–385`도 같은 해석기를 쓴다.
   - 재현: `{"action":["account"]}`는 요청을 보내지 않았지만 `TypeError: unhashable type: 'list'`를 냈다. `_ACTIONS`가 dict라 문자열 검사 전 membership 검사에서 터진다. Orchestrator는 JSON 객체와 required 인자의 존재만 확인하므로 이 비어 있지 않은 배열이 도구까지 들어온다. `_ArgumentProblem`으로 바뀌지 않아 결과 문장으로 수정 기회를 주지 못하고 Turn을 실패시킨다. 시나리오 `_filled`도 이 입력에서 예외가 난다.
   - 최소 수정: 활성 enum은 **문자열 여부와 허용 값**을 함께 검사한다. action만 새 예외 경로나 포괄적 예외 catch를 만들 필요가 없다. 기존 인자 검사 테스트의 입력 한 항목으로 확인하면 된다.

2. **[P2] 잘못 준 name을 '생략'으로 바꾸면 조회 대상이 달라진다.**
   - 파일·행: `src/pia_harness/broker_read.py:533–535`, 대상 선택 `:253–256`·`:549–556`.
   - 재현: `{"action":"account","name":""}`는 `/account`로 계좌 전체를 조회했다. `{"action":"history","name":" ","market":"kospi"}`와 `name:["삼성전자"]`는 `/history?data=prices&period=1m&market=kospi`로 시장을 조회했다. 가짜 Broker로 실제 도구 요청 경로까지 확인했다. '생략만 기본값'과 'name이 있으면 market은 무시'라는 계약 대신, 잘못된 종목 인자가 다른 대상의 조회로 바뀐다.
   - 최소 수정: **name을 쓰는 액션에서는 None만 생략, 준 값은 비어 있지 않은 문자열**이라는 규칙으로 검사한 뒤 대상 하나를 고른다. 틀린 name이면 결과 문장을 돌려주고 요청하지 않는다. ranking은 name을 쓰지 않으므로 그 값도 계속 무시한다. 새 복구·대체 조회를 넣지 않는다.

3. **[P2] 조회 뒤 단계에 주문 도구가 끼어도 시나리오가 PASS다.**
   - 파일·행: `tests/manual/smoke_flow.py:388–405`, 설명 `tests/manual/README.md`의 첫 응답 한정 문장.
   - 재현: 기대 `broker`, 인자 `{"action":"account"}`일 때 `Record.steps=["broker+order","answer"]`는 CHECK지만, `["broker","order","answer"]`와 `["broker","confirm","answer"]`는 PASS였다. 조회 결과를 받은 다음 모델이 주문 준비를 고른 경우를 놓친다. 뒤 단계 confirm은 Harness가 실행을 막더라도 모델의 잘못된 도구 선택은 같다. 이 지적은 실제 주문이 전송됐다는 뜻이 아니다.
   - 최소 수정: 기대 도구·인자는 지금처럼 첫 단계에서 비교하되, **조회 질문의 order·confirm 비혼입은 해당 Record의 모든 steps**로 판정한다. 이미 기록된 steps를 쓰면 되고, 새 주문 상태·시나리오 틀은 필요 없다. `expect` 없는 b10-1·b15의 관찰 방식과 execution 세트는 유지한다.

### 확인된 부분

- **이어 받기:** Broker `src/pia_broker/connectors/kis.py:452–501`의 `_dated_rows`가 네 API의 공통 규칙을 구현한다. 전부 빈 문자열인 padding만 버리고, 필수 날짜·필드는 파서로 검사하며, oldest를 묶음 사이에도 유지해 날짜가 엄격히 감소하도록 한다. 빈 묶음 / 시작일 이하에서 종료, 가장 오래된 날짜 전날로 재요청, 최초 포함 총 3회, 완료 뒤 시작일 이전 행 제거가 계획과 같다. 상한·깨진 행·중간 호출 오류에는 부분 응답을 만들지 않는다. '짧은 묶음' 규칙이나 끝 날짜 범위 검사 등 사용자가 받지 않은 조건을 추가할 필요는 없다. `_send`의 기존 인증 재조회가 있으면 실제 KIS GET 횟수는 논리적 세 묶음보다 많을 수 있으므로 §6 예산은 실제 발행 횟수 기준을 유지한다.
- **API·필드:** 기간 시세 종목/지수의 TR·시장 코드·output2·OHLC 필드, 종목 투자자 output2와 시장 투자자 output, 세 집단 순매수 필드가 공개 공식 예제와 맞는다. 시장 투자자 날짜 둘은 같은 until이다. 공식 예제로 투자자 API의 날짜를 옮긴 이어 받기 성공이나 단위를 확정할 수는 없으므로 계획대로 실호출에서 판정한다. 근거: [종목 기간 시세](https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/domestic_stock/inquire_daily_itemchartprice/inquire_daily_itemchartprice.py), [지수 필드](https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/domestic_stock/inquire_daily_indexchartprice/chk_inquire_daily_indexchartprice.py), [종목 투자자](https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/domestic_stock/investor_trade_by_stock_daily/investor_trade_by_stock_daily.py), [시장 투자자](https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/domestic_stock/inquire_investor_daily_by_market/inquire_investor_daily_by_market.py), [시장 투자자 필드](https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/domestic_stock/inquire_investor_daily_by_market/chk_inquire_investor_daily_by_market.py).
- **계좌:** cash_d2는 마지막 잔고 합계의 `prvs_rcdl_excc_amt`를 선택한다. stock_account는 현재가·매수 가능·모든 잔고 page를 읽어 code가 같은 position을 반환하고, 없으면 null이다. 잔고 실패 시 전체 실패이며 별도 매도 가능 조회나 합계 계산을 더하지 않는다. 날짜 계산은 KST 오늘 한 번을 기준으로 개월을 빼고 말일을 보정한다.
- **인자:** data/by/period/count는 None일 때만 기본값을 쓰고 빈 값·0 등은 거절한다. 해당 data에 없는 by/period는 검사·전달하지 않는다. 유효한 history name이 있으면 market(잘못된 문자열이나 객체 포함)을 검사·전달하지 않는다. history 시장에는 기본값이 없고 all은 거절한다. most_viewed는 계획 표대로 ranking market을 받되 Broker에는 all을 보내며 결과에도 전체 시장임을 밝힌다.
- **단위:** `_eok`의 출처별 divisor는 원 100,000,000 / 백만 원 100 / 억 원 1이다. quote·ranking의 확인된 필드에 알맞게 적용하고 계좌·가격·수량에는 억 변환을 적용하지 않는다. 음수·0·None을 유지하며 시장 금액만 둘째 자리로 반올림한다. 일별 투자자의 stock=(주, 백만 원), market=(천 주, 백만 원) 및 기간 시세 거래대금의 원 가정은 실호출 전 확정으로 취급하지 않는다. 지수 거래량이 있으면 그 raw 수량 단위도 같은 확인에서 대조한다. 반올림 구현을 Decimal로 다시 바꾸라는 요구는 없다.
- **시나리오 기본값:** `_filled`가 도구의 `_resolve`를 재사용하므로 생략한 data/by/period/count도 실제 실행값으로 비교한다. short_volume→short_selling 경로 변환과 가짜 Broker 응답을 쓰며, 실제 KIS나 주문은 하지 않는다. 기대 인자의 일부만 비교하는 기존 방식은 유지한다.
- **인증·IAM:** Broker GetInvestors→GetHistory route 교체는 GET·AWS_IAM과 기존 Bot role 검사를 유지한다. PIA bootstrap은 해당 정확한 GET ARN parameter·Resource 하나만 교체하고 기존 status/instruments/quote/account/ranking/orders grant는 그대로다. wildcard나 role 범위를 넓히지 않았다. 옛 investors 코드·route·grant 제거는 최신 사용자 결정에 맞으며 호환성 문제로 다시 지적하지 않는다.

### Simple-first·덜어낼 것

- `_ACTIONS` + `_resolve`, 네 API 공통 `_dated_rows`, 출처별 변환 함수는 현재 요구에 필요한 작은 구조다. 새 공통 계층·응답 추상화·API별 예외·재시도·자동 대체 조회는 제안하지 않는다. 필수 가격·투자자 값 검사와 날짜 감소·상한 검사는 불완전한 조회를 정상 결과로 내보내지 않는 보호라 유지한다.
- **비차단 후보 하나:** Broker `src/pia_broker/connectors/kis.py:889–905`의 `_flows(row, groups)`는 현재 호출이 `:445` 하나이고 groups도 언제나 `_INVESTOR_GROUPS`다. 이제 종목·시장 모두 같은 세 집단이므로 groups 인자를 없애고 함수 안에서 그 상수를 쓰면 된다. 새로운 추상화를 만드는 정리는 아니며 이번 검토에서는 수정하지 않았다.
- 테스트는 현재 가짜 응답으로 조회 계약을 확인하는 범위가 적절하다. 위 입력·판정 재현을 기존 검사에 반영하면 충분하고 별도 수동 스크립트 테스트 체계나 유료 실행을 추가하지 않는다.

### 이번 검증·작업 범위

- Broker pytest **227개 통과**, Ruff 통과, mypy **41 source files 오류 없음**. 공개 GitHub 예제만 읽었고 KIS API를 부르지 않았다.
- harness 전체 unittest **185개 통과**, 건너뜀 0. 기존 PIA 이미지에서 소스를 읽기 전용으로 mount하고 `--network none`으로 실행했다.
- PIA 변경 관련 `tests.test_dev_deployment` **18개 통과**, 건너뜀 0, 외부 네트워크 없음. PIA 전체 439개·패키지/이미지 build는 구현 기록의 결과이며 이번 검토에서 반복하지 않았다.
- 위 세 문제는 가짜 응답/Record로 재현했다. 소스·테스트 파일은 수정하지 않았고 검토 기록만 이 계획서 끝에 추가했다. 세 저장소 diff 공백 검사와 harness 추적 파일 전체의 trailing whitespace·EOF 공백 검사를 통과했다.
- 병합·태그·릴리스·배포·AWS 조회/변경·KIS 실호출·유료 시나리오 실행은 하지 않았다. 검토 기록을 harness `claude/broker-unify`에만 커밋·push한다.

## Codex 구현 검토 반영 (Claude, 2026-10-05)

세 건 모두 실제 모델 입력으로 일어날 수 있어 그대로 받았고, 비차단 후보도 한 줄이라 함께 했다.

| 지적 | 수정 |
|---|---|
| P2 1. 배열 `action`이 예외 | `_resolve`가 action도 문자열·허용 값을 함께 검사. 인자 검사 테스트에 입력 하나 추가 |
| P2 2. 빈·잘못된 `name`이 계좌 전체·시장 조회로 바뀜 | name을 쓰는 액션(account·quote·history)은 None만 생략, 준 값은 비어 있지 않은 문자열이어야 하고 아니면 "name must be the stock name" 결과 문장(요청 안 함). ranking은 name을 계속 무시. 테스트 입력 세 개 추가 |
| P2 3. 뒤 단계의 order·confirm을 놓침 | `_check`가 order·confirm 비혼입을 Record의 모든 단계로 판정(기대 도구·인자는 첫 단계 그대로). 수동 테스트 README 문구 수정 |
| 비차단: Broker `_flows`의 `groups` 인자 | 인자를 없애고 `_INVESTOR_GROUPS`를 안에서 사용 (pia-broker `6205b84`) |

확인: harness 테스트 185개 통과(새 입력은 subtest), Broker 227개 통과·ruff·mypy 통과. `_check`는 `["broker","order","answer"]`·`["broker","confirm","answer"]`를 CHECK로, `["broker","answer"]`를 PASS로 판정한다.

## Codex 최종 구현 검토 (2026-10-05, Asia/Seoul)

- 대상: harness `47882a76eb7060b67f6d6a32b50f7e0cf519411e`, Broker `6205b849cfbf02c8ed219cdb98cbfac465b6f7ad`, PIA 변경 없음(`762c9a00a340ce2f567ccf9c160c0adff9e80c2a`). 직전 검토 `1cb46d3` 이후의 수정 전체와 반영 기록을 대조했다.
- **판정: 기존 P2 세 건과 비차단 후보 한 건 모두 해결. 추가 blocker 없음.** 직전 검토의 이어 받기·계좌·단위 변환·인증/IAM 판정은 유지한다. 이번 수정에 새 추상화·재시도·대체 조회·과도한 예외 처리가 추가되지 않았다.

### 반영 확인

1. **action 검사** (`src/pia_harness/broker_read.py:530–532`): 문자열 검사 뒤 허용 값을 확인하므로 배열·객체도 `_ArgumentProblem` 결과 문장으로 돌아온다. 실제 도구와 `_filled` 양쪽에서 예외가 밖으로 나오지 않는 것을 확인했다.
2. **name 검사** (`src/pia_harness/broker_read.py:533–541`): account·quote·history는 None만 생략으로 취급하고, 제공한 name은 비어 있지 않은 문자열이어야 한다. 빈 문자열·공백·배열은 조회하지 않는다. 유효한 이름은 trim한 뒤 종목을 고르며 history의 market은 계속 무시한다. ranking은 name을 처음부터 쓰지 않아 잘못된 name도 무시한다. 하나의 규칙으로 대상이 바뀌는 문제를 해결했다.
3. **전체 단계 판정** (`tests/manual/smoke_flow.py:404–406`): Record의 모든 steps에서 도구 이름을 모아 order·confirm 비혼입을 확인한다. 첫 단계의 기대 도구·인자 비교와 `_filled` 기본값 해석은 그대로다. 정상 조회는 PASS, 첫 단계 또는 뒤 단계의 주문 도구 혼입은 CHECK였다. expect=order·confirm의 정상 판정과 expect 없는 OBSERVE도 확인했다. 수동 테스트 README가 이 범위와 일치한다.
4. **_flows 단순화** (`src/pia_broker/connectors/kis.py:445`, `:889`): 유일한 호출에서 groups 인자를 없애고 함수 안의 `_INVESTOR_GROUPS`를 사용한다. 종목·시장 공통 세 집단, 필수 순매수 값 검사·부호·반환 형식은 그대로다. 불필요한 가변 인자만 줄였다.

### 검증·남은 확인

- harness 전체 unittest **185개 통과, 건너뜀 0**. 기존 PIA 이미지에 소스를 읽기 전용으로 mount하고 `--network none`으로 실행했다.
- 가짜 Broker 추가 대조: 잘못된 입력 6개(action 배열·객체, account 빈 name, history 공백·배열 name, quote bool name) 모두 결과 문장·요청 0회. 정상 입력 4개(계좌 name 생략·null, ranking의 쓰지 않는 배열 name, history의 공백을 trim한 유효 name+쓰지 않는 market 객체)는 정상 조회했다. 마지막 history 요청은 code·기본 data/period만 포함했다.
- 가짜 Record 대조: 정상 조회 PASS, 첫/뒤 단계 order·confirm CHECK, ranking 기본값을 생략한 expect_args PASS, execution의 order·confirm PASS, expect 없는 OBSERVE. 수동 스크립트의 helper만 호출했으며 모델·검색 시나리오를 실행하지 않았다.
- Broker pytest **227개 통과**, Ruff 통과, mypy **41 source files 오류 없음**. PIA는 변경이 없어서 직전 관련 검사 결과를 유지하고 반복 실행하지 않았다.
- 실제 KIS 단위·투자자 API 이어 받기·기간 시세 응답은 아직 미확인이다. 기존 계획대로 승인받은 실호출로 확인한 뒤 harness 릴리스 여부를 정한다. 이번 'blocker 없음'은 실제 응답을 검증했다는 뜻이나 병합·배포 승인으로 확대한 판정이 아니다.
- 검토 기록만 이 파일 끝에 추가하고 harness의 같은 브랜치에 커밋·push한다. 코드 수정·병합·태그·릴리스·배포·AWS 호출/변경·KIS 실호출·유료 시나리오 실행은 하지 않았다.

## 조 원 표기와 시나리오 (Claude, 2026-10-05)

- **사용자 결정(2026-10-05):** 시장 금액은 1조 이상이면 조 원으로 보여 준다(예: 시가총액 16,135,729억 → 1,613.57조 원). 시나리오 b11에서 모델이 억 원 값에 조 원을 직접 계산해 덧붙였기 때문에, 그 변환도 도구가 한다. 규칙 하나: 억 원으로 바꾼 값이 1만(억) 이상이면 조 원, 소수 둘째 자리. 계좌 금액·가격은 원 그대로.
- 구현: `_eok` → `_market_amount`(억 원/조 원), 결과 첫 줄의 단위 문구 "억 원 (조 원 from 1조)". 테스트 185개 통과.
- 시나리오: 1차 16개 PASS 12·CHECK 2(직전 결과 재사용, 고치지 않음)·관찰 2(양호)·주문 0, 조 원 변경 뒤 b11·b12 재실행 PASS(`tests/manual/records/2026-10-05_broker.md`). 도구 설명 수정 없음.

- **릴리스 버전(사용자, 2026-10-05):** 0.8.0 대신 **0.7.3**. 아직 0.7대에서 간다.

## Codex 마지막 변경 검토 (2026-10-05, Asia/Seoul)

- 대상: harness `25f0990`·`06f016d`와 `tests/manual/records/2026-10-05_broker.md`. 작업 중 추가된 `491a901`은 사용자 릴리스 버전 0.7.3을 기록한 계획서 변경뿐이며 그대로 보존했다. Broker `6205b84`와 PIA `762c9a00a`는 직전 최종 검토 이후 변경이 없다.
- **판정: 구현 blocker 없음.** 조 원 표기는 사용자 결정을 따르고, `_market_amount` 하나로 기존 출처별 억 변환 뒤 조 변환을 처리한다. 새로운 예외 계층이나 다른 도구·Orchestrator 변경은 없다. 기록 집계의 비차단 정정 사항 한 건은 아래에 적었다.

### 구현·판정 확인

- **시장 금액** (`src/pia_harness/broker_read.py:623–636`): 원/백만 원/억 원 divisor를 유지하고, 억 단위의 표시값을 두 자리로 반올림했을 때 절댓값이 10,000 이상이면 조 원으로 바꾼다. 음수 순매도도 금액 크기 기준으로 변환하고 부호를 유지한다. 조 원 값은 원래 억 값에서 나누므로 중간 억 반올림값을 다시 나누는 이중 반올림을 하지 않는다. 경계 `9,999.996억 → 1조`와 `9,999.99억 → 9,999.99억`, 1조 정확한 경계, 양·음 부호, 0·None을 오프라인에서 확인했다. 표시 반올림에 맞춘 경계 규칙은 기존 테스트에도 명시돼 있으며 추가 조건을 제안하지 않는다.
- quote의 거래대금·시총, history의 거래대금·투자자 금액, ranking의 시장 금액이 모두 같은 함수를 사용한다. 결과 단위 문구와 README가 억/조 원을 밝힌다. 계좌·가격의 KRW 함수와 수량·지수는 바뀌지 않았고, 1조 원 크기의 계좌 금액도 KRW로 남는 것을 확인했다.
- **채점 범위** (`tests/manual/smoke_flow.py:404–406`): expect=broker일 때만 모든 단계의 order·confirm 혼입을 CHECK로 판정한다. 정상 broker 조회 PASS, 첫/뒤 단계 주문 도구 CHECK, expect=web_search의 검색→order→answer PASS를 가짜 Record로 확인했다. expect=order·confirm, expect 없는 OBSERVE, 첫 호출의 expect_args·기본값 해석은 그대로다. 직전 검토는 order·confirm 기대 사례만 확인해 검색 뒤 정상 주문의 오탐 범위를 놓쳤으며, 이번 제한이 그 문제를 해결한다.
- 06f016d는 채점과 설명만 바꾸고 모델 입력을 바꾸지 않는다. 이 변경 때문에 유료 시나리오를 다시 돌릴 필요는 없다. 저장된 b11·b12 재실행 기록은 조 원 결과를 모델이 그대로 읽었다는 확인이며 실제 KIS 단위의 증거는 아니다. b3·b5의 직전 결과 재사용은 기록된 사용자 판단대로 추가 수정하지 않는다.

### 비차단 기록 정정

- **[P3] 첫 실행의 PASS와 관찰 집계를 구분한다.** `tests/manual/records/2026-10-05_broker.md:16`은 "PASS 14, CHECK 2, 관찰 2"지만 아래 16행 표에는 **PASS 12·CHECK 2·관찰 2**가 있다. scenarios/broker.json도 expect 있는 14개, 없는 2개이며 `_check`는 expect 없는 두 개를 OBSERVE로 돌려준다. 계획서 "조 원 표기와 시나리오"의 PASS 14 요약도 같은 집계다. 관찰 두 개의 수동 판단을 포함해 양호 14개라고 말하려면 자동 PASS 12 + 관찰 양호 2로 구분하면 된다. 코드·모델·실행의 문제가 아니므로 재실행 없이 기록의 숫자/명칭만 정정하면 된다. 이번에는 검토 결과만 이 계획서에 기록했다.

### 이번 검증·범위

- harness 전체 unittest **185개 통과, 건너뜀 0**. 기존 PIA 이미지, 소스 읽기 전용 mount, `--network none`으로 실행했다. 추가로 동일 금액의 원·백만 원·억 원 출처가 같은 조 원 결과가 되는지와 위 금액·채점 경계를 helper로 확인했다. 별도 테스트 파일·코드는 만들지 않았다.
- Broker·PIA는 변경이 없어 테스트·빌드를 반복하지 않았다. harness diff 공백 검사와 전체 추적 텍스트의 trailing whitespace·EOF 공백 검사를 통과했다.
- 실제 KIS 단위·투자자 API 이어 받기는 기존 계획의 실호출 확인 항목으로 남는다. 릴리스 버전은 사용자 결정 0.7.3을 유지한다. 검토 기록만 같은 harness 브랜치에 커밋·push하며 병합·릴리스·배포·AWS 호출/변경·KIS 실호출·유료 시나리오 실행은 하지 않았다.

## 나중에 다시 볼 것 (사용자, 2026-10-05: broker는 revisit할 수 있게)

전체 도구 설계를 다시 평가했을 때 남은 후보는 모두 broker 쪽이다. 지금은 바꾸지 않고, 실호출 확인과 실제 사용에서 문제가 보이면 다시 본다.

1. `ranking`의 `data`는 `by`만으로 정해진다(by 11개가 겹치지 않음). 어긋나면 오류가 되는 경우가 하나 있다. "모든 액션에 같은 data 축" 합의로 유지 중.
2. `quote`의 `data`는 `prices` 하나뿐이다. 나중의 배당·호가 자리.
3. `prices` 이름이 시가총액·PER까지 담기에는 좁게 들린다. 바꾸면 세 액션을 같이 바꾼다.
4. `account` + `name`은 종목 하나를 보려고 잔고 전체를 받는다(KIS에 종목 하나의 보유·평균가 API가 없음). 보유 종목이 많아지면 본다.
5. 이전 결과 재사용: 몇 초 사이에는 재사용, 시간이 지난 뒤에도 재사용하는지는 미확인. 오래된 값을 말하면 도구 설명을 조정한다.
6. 금액 단위(계좌 원, 시장 억 원·1조 이상 조 원): 써 본 뒤 다시 본다(사용자).

## 실호출 확인 결과 (Claude, 2026-10-05 09시께 KST, 장중)

EC2에서 Bot 역할로 서명해 배포된 Broker(`6205b84`)를 불렀다. 회원 ID는 회원 상태 인덱스(ACTIVE 1명)에서 읽었고 출력하지 않았다. 계좌 금액·보유·자격 증명은 기록하지 않는다. KIS GET은 상한 26 안(실제 약 15~17번).

| # | 조회 | 결과 |
|---|---|---|
| 1 | investors 005930 1m | **502 BUSINESS_REJECTED** (아래) |
| 2 | investors kospi 1m | 18줄(9/7~10/2), 한 번. value/volume 중앙값 92.9 → 천 주·백만 원(평균 단가 약 9만 원) |
| 3 | investors kosdaq 1m | 18줄, 코드 `KSQ`·`1001` 맞음. value/volume 8.8 → 천 주·백만 원 |
| 4 | prices 005930 1m | 일봉 18줄, 거래대금/(거래량×종가) 0.998 → 원·주 |
| 5 | prices 005930 6m | 주봉 26줄, 날짜는 그 주의 첫 거래일(월·화) |
| 6 | prices kospi 3m | 일봉 61줄(50줄 초과 → **이어 받기 동작**), 지수 소수 유지, 거래대금 크기 1e7 → **백만 원** |
| 7 | prices kosdaq 5y | 월봉 61줄(이어 받기 동작), 날짜는 그 달의 마지막 거래일·진행 중인 달은 최근 거래일, 거래대금 백만 원 |
| 8 | account | `cash_d2` 필드 있음(지금은 예수금과 같음: 결제 대기 없음) |
| 9 | account + 005930 | 매수 가능 정상, 보유 없음 → `position` null |

- **1번 원인:** 날짜를 받는 종목 투자자 API(`FHPTJ04160001`)는 거래일 15:40 이후에만 그날 날짜에 응답하고 연속조회가 없다(다른 KIS 연동 프로젝트의 실계좌 기록 jewon-oh/kr-broker PR #85). 장중에 오늘 날짜로 불러 거부됐다. 인자는 공식 예제·설정과 같았다.
- **수정 1 (Broker `claude/broker-investors-api` `506fb5b`):** 종목 투자자 history는 10-02에 확인한 `FHKST01010900`(최근 30거래일, 날짜 지정·이어 받기 없음)로 되돌린다. 1개월만 열기로 했으니 한 번 호출로 충분하다. `_dated_rows`에 "최근 줄만 주는 API는 한 번만 부른다"(`follow=False`)를 더했다(이번 달 상장 종목도 한 번에 끝남). 처음 API를 바꾼 것은 긴 기간을 날짜로 이어 받으려던 계획 때문이었는데, 1개월로 정한 뒤 다시 보지 않았다.
- **수정 2 (harness):** 지수 기간 시세의 거래대금은 백만 원, 거래량은 천 주다. `_PRICE_DAY_UNITS`(종목 원·주, 시장 백만 원·천 주)로 바꿔 시장 투자자와 같은 방식으로 변환한다.
- 일별 투자자 단위 가정(종목 주·백만 원, 시장 천 주·백만 원)은 맞았다.
- **남은 확인:** 수정 1 배포 뒤 investors 005930 1m 한 번(KIS 1번). D+2 예수금은 PIA 연결 뒤 사용자가 Telegram에서 KIS 앱 값과 대조.
