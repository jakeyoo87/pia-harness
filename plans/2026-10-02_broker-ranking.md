# Broker 조회 도구 2단계: `ranking`, `investors`

날짜: 2026-10-02 (Asia/Seoul)
브랜치: pia-harness `claude/broker-ranking`(계획서와 harness 구현), pia-broker `claude/broker-ranking`(Broker 구현)
담당: Claude 계획·구현(harness, Broker), Codex 계획·구현 검토, 릴리스·PIA 연동·IAM·배포는 Codex

## 목표

PIA가 시장 전체의 순위와 투자자별 매매 동향을 증권사 값으로 답한다. 예:

- "오늘 코스닥 상승률 상위 알려줘", "거래대금 상위 20개", "시가총액 순위"
- "요즘 신고가 근처인 종목", "공매도 많은 종목 일주일 기준", "PER 순위"
- "외국인이 오늘 많이 판 종목", "삼성전자 외국인이 요즘 사고 있어?", "오늘 시장에서 기관은 샀어?"

지금은 이런 질문을 웹 검색으로 답한다. 결과가 늦거나 기준 시점이 불분명하다.

## 원칙: action은 KIS 데이터 하나

- action이나 `by` 값은 사용자 질문 유형이 아니라 KIS가 따로 주는 데이터 하나에 대응한다.
- 기존 action으로 얻을 수 있는 데이터를 조합하면 답할 수 있는 질문에는 새 action을 만들지 않는다. 예: "내 종목 중 수익률 1위"는 `account`, "두 종목 비교"는 `quote` 두 번이면 된다.
- 같은 KIS API의 정렬·구분 옵션은 `by` 값으로 나눌 수 있다(예: 상승률·하락률).
- 참고 자료는 KIS 공식 저장소 `koreainvestment/open-trading-api`의 `examples_llm/domestic_stock`과 `MCP/Kis Trading MCP`다. 공식 MCP 서버를 직접 연결하지는 않는다(2026-10-01 사용자 결정: 회원별 자격 증명 구조, 실행 중 코드 다운로드, 주문 확인 흐름 우회 문제).

## 결정 (사용자, 2026-10-02)

- 새 action 두 개: `ranking`(순위), `investors`(투자자별 매매 동향).
- `ranking`의 `by` 값 17개(아래 표). 배당률 순위와 재무비율 순위는 뺀다.
  - 배당률(`dividend_rate`): 배당 한 건이 한 줄이라 회사별 연간 배당률이 아니다. 분기·반기 구분도 알 수 없다. 사용자가 물으면 웹 검색으로 답한다.
  - 재무비율(`finance_ratio`): "수익성·안정성" 같은 묶음 단위 정렬이라 기준을 설명하기 어렵다.
  - 포워드 PER: KIS에 순위 API가 없다. 종목별 추정실적 API(`estimate_perform`)는 필드 의미가 문서에 없어 이번에 쓰지 않는다.
- 인자(모두 선택, `action`만 필수):
  - `market`: 전체(기본)·코스피·코스닥. HTS 조회 상위는 시장 선택이 없어 항상 전체다.
  - `count`: `ranking`은 종목 수, `investors`(종목)는 일수. 기본 10, 최대는 KIS 한 번 응답 크기(보통 30, HTS 조회 상위 20).
  - `period`: 공매도 집계 기간. 기본 `1d`. `1d 2d 3d 4d 1w 2w 3w 1m 2m 3m`(KIS가 지원하는 값만).
- 제외 대상 없음(KIS 기본 "전체"). ETF·관리종목도 나온다.
- 거래소는 KRX(`J`)만. NXT와 시간외 단일가는 반영하지 않는다(기존 `quote`와 같음).
- PER·PBR·EPS 순위는 연간 결산 기준. 회계연도는 조회일로 계산한다: 4월 이후는 작년, 1~3월은 재작년.
- 투자자 구분: 개인·외국인·기관계, 시장 조회에는 연기금(기금)도. 종목별 API에는 연기금이 없다.
- 외국인·기관 순위는 금액 기준 정렬, 순매수·순매도 모두.
- 같은 작업에 KIS 토큰 만료 시각 수정을 넣는다(아래 "토큰 만료 시각").

## 설계

### 도구 인터페이스 (harness)

```json
{
  "action": "status | quote | account | buyable | ranking | investors",
  "name": "종목명 또는 null",
  "by": "ranking 종류 또는 null",
  "market": "all | kospi | kosdaq 또는 null",
  "count": "정수 또는 null",
  "period": "1d … 3m 또는 null"
}
```

- 인자 목록은 하나이고 action마다 쓰는 인자가 다르다(Anthropic computer use 도구와 같은 방식). `oneOf`는 모델·제공자 호환성 때문에 쓰지 않는다.
  - `ranking`: `by`(필요), `market`, `count`, `period`
  - `investors`: `name`(없으면 시장 전체), `market`, `count`
- `required`는 지금처럼 `action`뿐이다. 필요한 인자가 없으면 결과 문장으로 알린다(기존 `name` 처리와 같음). 관계없는 인자는 무시한다.
- `count`는 harness가 결과를 자를 때 쓴다. Broker는 KIS 한 번 응답을 그대로 돌려준다. 요청이 최대보다 크면 결과에 "최대 N개까지 조회된다"고 적는다.
- 결과 문장에는 조회 시각(KST)과 기준을 적는다: 시장, 집계 기간(공매도), 회계연도(PER·PBR·EPS), "장중 추정치"(외국인·기관 순위), "전체 시장 기준"(HTS 조회 상위).
- 음수는 음수로 둔다(하락률, 순매도). 부호는 KIS `prdy_vrss_sign`을 따른다(기존 `quote`와 같음).

### `ranking`의 `by` 값과 KIS API

| `by` | 뜻 | KIS API (TR ID) | 고유 출력 |
|---|---|---|---|
| `market_cap` | 시가총액 | `ranking/market-cap` (`FHPST01740000`) | `stck_avls`, `mrkt_whol_avls_rlim` |
| `gainers` / `losers` | 상승률 / 하락률 | `ranking/fluctuation` (`FHPST01700000`) | 등락률 |
| `volume` / `trading_value` | 거래량 / 거래대금 | `quotations/volume-rank` (`FHPST01710000`, 소속 구분 0 / 3) | `acml_vol`, `acml_tr_pbmn` |
| `near_high` / `near_low` | 신고가 / 신저가 근접 | `ranking/near-new-highlow` (`FHPST01870000`, 가격 구분 0 / 1) | `new_hgpr`·`hprc_near_rate` / `new_lwpr`·`lwpr_near_rate` |
| `short_selling` | 공매도 상위 | `ranking/short-sale` (`FHPST04820000`) | 공매도 수량·금액·비중, 기준일 1·2 |
| `most_viewed` | HTS 조회 상위 20 | `ranking/hts-top-view` (`HHMCM000100C0`) | 종목코드만(이름은 Broker 종목 목록으로 찾음) |
| `most_watched` | 관심종목 등록 상위 | `ranking/top-interest-stock` (`FHPST01800000`) | `inter_issu_reg_csnu` |
| `per` / `pbr` / `eps` | PER / PBR / EPS 순위 | `ranking/market-value` (`FHPST01790000`, 정렬 23 / 24 / 27, 결산 `3`) | `per`, `pbr`, `eps`, `stac_month` |
| `foreign_buying` / `foreign_selling` / `institution_buying` / `institution_selling` | 외국인·기관 순매수·순매도 상위 | `quotations/foreign-institution-total` (`FHPTJ04400000`, 금액 정렬) | 순매수 수량·금액 |

공통 출력: 순위, 종목명, 코드, 현재가, 전일 대비(부호 적용), 등락률, 거래량. 응답에 없는 값은 빼고 적는다.

### `investors`

| 호출 | KIS API (TR ID) | 결과 |
|---|---|---|
| `name` 있음 | `quotations/inquire-investor` (`FHKST01010900`) | 날짜별(최근 `count`일): 종가, 전일 대비, 개인·외국인·기관계 순매수 수량·금액 |
| `name` 없음 | `quotations/inquire-investor-time-by-market` (`FHPTJ04030000`) | 오늘, 시장별(`market` 없으면 코스피·코스닥 둘 다): 개인·외국인·기관계·연기금 순매수 수량·금액 |

- 매수·매도 총량, 기관 세부(증권·투신·사모·은행·보험)는 결과에 넣지 않는다.
- 시장 일별 API(`inquire-investor-daily-by-market`)는 한 번에 하루만 줘서 이번에 쓰지 않는다.

### Broker 경로

새 경로 두 개, 모두 GET, Bot role만 허용(기존 내부 경로와 같은 IAM 검사):

- `GET /internal/members/{member_id}/ranking?by=&market=&period=`
- `GET /internal/members/{member_id}/investors?code=&market=`

- KIS 토큰이 회원의 앱 키로 발급되므로 순위도 회원 경로 아래에 둔다. 확인된 KIS 연결이 없으면 기존처럼 409다.
- 값 해석(KIS 코드표, 회계연도 계산, 기간 코드)은 Broker가 한다. harness는 `by`·`market`·`period` 이름만 안다.
- 응답 숫자는 기존처럼 정수면 int, 아니면 float. 음수 보존. 응답 본문은 로그에 남기지 않는다.
- IAM: 새 invoke ARN 두 개. PIA bootstrap에 파라미터 두 개를 더한다(Codex). 한 경로로 합쳐 허용을 하나로 줄일 수도 있다(열린 질문 1).

### 토큰 만료 시각

- 지금 `KisTokenIssuer.issue`(`live_kis.py`)는 만료를 "요청 시각 + `expires_in`"으로 저장한다.
- KIS 공식 예제(`kis_auth.py`)에 따르면 6시간 안에 다시 요청하면 기존 토큰을 그대로 돌려준다. 이때 `expires_in`이 전체 기간이면 실제보다 늦게 만료를 잡는다.
- 수정: KIS 응답의 `access_token_token_expired`(KST, `YYYY-MM-DD HH:MM:SS`)를 만료 시각으로 쓴다. 이 값이 없거나 읽을 수 없으면 지금처럼 스키마 오류다. `expires_in` 범위 검사는 그대로 둔다.
- 참고: KIS는 발급 요청마다 알림톡을 보낸다(같은 예제). 알림톡 수가 곧 발급 요청 수다.

## 구현 위치

**pia-broker**
- `models.py`: `RankingRow`(공통 필드 + 고유 필드 묶음), `InvestorDay`, `MarketInvestors`. 금액·수량은 부호 있는 `Decimal`.
- `connectors/kis.py`: API별 요청과 파싱. 공통 행 파서 하나와 `by`별 고유 필드 표. 기존 `_send`(토큰 만료 시 한 번 재시도)를 쓴다.
- `orders.py`: `TradingConnector`에 `get_ranking`, `get_investors`, `get_market_investors`. `TradingService`에 같은 이름의 조회 메서드.
- `credential_api.py`: 두 경로, 쿼리 검증(`by`·`market`·`period` 허용 값, `code` 6자리).
- `instruments.py`: `most_viewed`의 코드 → 이름(기존 종목 목록 재사용, 없으면 코드만).
- `live_kis.py`: 토큰 만료 시각.
- `infra/broker-credential.template.json`: `GetRanking`, `GetInvestors` 경로. 인프라 테스트.
- 픽스처: API별 KIS 응답(하락·순매도 음수 포함), 테스트, README.

**pia-harness**
- `broker_read.py`: action 두 개, 인자 스키마·설명, 결과 문장, `count` 자르기.
- 테스트(action별 결과 문장, 인자 누락, `count` 상한), README 6.2.3.

**PIA(Codex)**: harness 버전 고정, bootstrap IAM 파라미터 두 개, 계약 테스트, 운영 문서. 프롬프트(`BROKER_READ_PROMPT`)에 순위·투자자 동향을 한 구절 더한다.

## 개인정보·보안

- 순위와 시장 투자자 동향은 공개 시장 데이터다. 회원 계좌 정보는 들어가지 않는다.
- 회원 KIS 앱 키로 호출하므로 KIS 호출 한도는 회원 몫을 쓴다. 한 Turn의 읽기 도구 호출 한도(20회)가 상한이다.
- 응답 본문은 로그에 남기지 않는다(기존 규칙).

## 실호출로 확인할 것 (배포 뒤, 사용자 승인)

- 금액 필드 단위: `stck_avls`, `acml_tr_pbmn`, `*_ntby_tr_pbmn`(원·백만 원·억 원 중 무엇인지)
- 등락률 순위: 하락률 정렬 코드, 응답 필드(공식 예제에 출력 필드 목록이 없음)
- 공매도: 정렬 기준(금액인지 비중인지), `1w`의 실제 기준일 범위
- PER 순위: 정렬 방향, 적자(음수 PER) 위치
- 외국인·기관 순위: 장중 갱신 시각("가집계")
- 시장 투자자 동향: 시장 코드값(공식 예제는 `999`, `S001`), 장중 값 여부
- 종목 투자자 동향: 당일 값이 장중에 채워지는지
- 토큰: 재발급 시 `access_token_token_expired`가 기존 만료 시각인지

결과는 금액이 아니라 성공 여부와 확인한 항목만 기록한다. 확인 결과에 따라 결과 문장의 단위·기준 문구를 고친다.

## 순서

1. 계획 → Codex 계획 검토 → 반영
2. Broker·harness 구현(Claude) → Codex 구현 검토 → 병합
3. harness 릴리스, PIA 연동, Broker 배포, IAM(Codex) → 검토 → 배포(사용자 승인)
4. 실호출 확인(사용자 승인) → 단위·문구 수정

## 열린 질문

1. 새 Broker 경로를 둘(`ranking`, `investors`)로 둘지, 하나로 합쳐 IAM 허용을 하나로 할지. 계획은 둘이다(뜻이 다르고 bootstrap 변경은 어차피 한 번이다).
2. 17개 `by` 값의 KIS 호출을 한 커넥터 메서드(`get_ranking(by, …)`)로 묶는 지금 설계가 Broker 포트 규칙에 맞는지.
3. 공식 예제에 출력 필드가 없는 API(등락률)와 필드 설명이 부족한 API(HTS 조회 상위)를 실호출 전에 어디까지 구현할지. 계획은 공통 필드만 읽고 없으면 뺀다.
