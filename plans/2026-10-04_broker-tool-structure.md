# broker 도구 구조 통일 (설계 기록)

날짜: 2026-10-04 (Asia/Seoul)
브랜치: pia-harness `claude/broker-unify` (main `6dffbc4`, v0.7.2에서)
담당: Claude 계획·구현(harness, Broker), Codex 계획·구현 검토, 릴리스·PIA 연동·IAM·배포는 Codex
상태: **구조 합의만 기록.** 계획(인자 검사, 결과 형식, Broker 경로, KIS, 실호출, 버전, PIA 연동)은 PIA 정리 작업을 마친 뒤 이어서 쓴다.

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
