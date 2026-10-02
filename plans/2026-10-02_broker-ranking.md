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
  - `count`: `ranking`은 종목 수, `investors`(종목)는 일수. 기본 10. KIS 한 번 응답 안에서 자른다(연속 조회 없음). 문서로 확인된 상한은 HTS 조회 상위 20개뿐이고, 나머지는 실제 반환 수를 적는다.
  - `period`: 공매도 집계 기간. 기본 `1d`. `1d 2d 3d 4d 1w 2w 3w 1m 2m 3m`(KIS가 지원하는 값만).
- 제외 대상 없음(KIS 기본 "전체"). ETF·관리종목도 나온다.
- 상품 범위는 KRX 정규 시세다. NXT와 시간외 단일가는 반영하지 않는다(기존 `quote`와 같음). 요청 코드는 API마다 공식 값을 쓴다(대부분 `J`, 외국인·기관 가집계는 `V`, HTS 조회 상위와 시장 투자자 동향은 시장 분류 인자 없음).
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
- 수정 1(`live_kis.py`): KIS 응답의 `access_token_token_expired`(KST, `YYYY-MM-DD HH:MM:SS`)를 UTC로 바꿔 만료 시각으로 쓴다. 없거나 형식이 틀리거나 이미 지난 시각이면 스키마 오류다. 만료는 이 값과 `issued_at + expires_in` 중 이른 쪽이다(구현 때 정함, 아래 "구현" 절).
- 수정 2(`token_lifecycle.py`, Codex 계획 검토 1): `get_token`에서 "새 만료가 저장된 만료보다 이르면 저장된 토큰을 다시 쓰는" 분기를 지운다. 발급에 성공하면 KIS가 준 토큰과 만료 시각을 그대로 저장하고 돌려준다.
  - 이 분기가 남으면, 지금 잘못 늘어나 저장된 만료 시각이 KIS의 정확한 값을 이긴다(Codex가 가짜 객체로 재현).
  - 동시 발급은 이미 30초 lease와 소유자 조건부 저장(`store_refresh`)이 막는다. 메모리·DB 재사용, Credential version 구분, 60초 발급 간격은 그대로다.
  - KIS가 같은 토큰을 다시 줄 때 실제 만료를 늘리지 않는다는 것이 계약이다. 6시간 규칙은 KIS의 재반환 규칙이고 우리 쪽 발급 주기가 아니다.
  - 운영 DB에 이미 저장된 토큰은 건드리지 않는다. 그 토큰이 만료될 때까지만 영향이 있고, 조회는 만료 응답(EGW00123)에서 새로 발급해 복구한다.
- 테스트: KST→UTC, 필드 누락·형식 오류·지난 시각, 같은 토큰 재반환, 잘못 늘어난 기존 값보다 이른 정상 응답이 저장됨, 재시작 뒤 DB 재사용.
- 참고: KIS는 발급 요청마다 알림톡을 보낸다(같은 예제). 알림톡 수가 곧 발급 요청 수다.

## 구현 위치

**pia-broker**
- `models.py`: `RankingRow`(공통 필드 + 고유 필드 묶음), `InvestorDay`, `MarketInvestors`. 금액·수량은 부호 있는 `Decimal`.
- `connectors/kis.py`: API별 요청과 파싱. 공통 행 파서 하나와 `by`별 고유 필드 표. 기존 `_send`(토큰 만료 시 한 번 재시도)를 쓴다.
- `orders.py`: `TradingConnector`에 `get_ranking`, `get_investors`, `get_market_investors`. `TradingService`에 같은 이름의 조회 메서드.
- `credential_api.py`: 두 경로, 쿼리 검증(`by`·`market`·`period` 허용 값, `code` 6자리).
- `instruments.py`: `most_viewed`의 코드 → 이름(기존 종목 목록 재사용, 없으면 코드만).
- `live_kis.py`, `token_lifecycle.py`: 토큰 만료 시각(위 수정 1·2).
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

## 실호출로 확인할 것 (Broker 배포 뒤, Bot 노출 전, 사용자 승인)

- 금액 필드 단위: `stck_avls`, `acml_tr_pbmn`, `*_ntby_tr_pbmn`(원·백만 원·억 원 중 무엇인지)
- 등락률 순위: 하락률 정렬 코드(공식 설명 `0000`과 실행 예 `0`이 다름)
- 거래량 순위: 소속 구분 `0`이 공식 설명대로 "평균 거래량" 기준인지, 오늘 누적 거래량 순인지. 가격 `0`·날짜 `0`·제외 비트 `000000`이 전체 대상인지
- 신고가·신저가 근접: `hprc_near_rate`·`lwpr_near_rate`가 거리인지 비율인지(결과 문구는 그때까지 "near-high rate")
- 공매도: 정렬 기준(금액인지 비중인지), `1w`의 실제 기준일 범위
- PER 순위: 정렬 방향, 적자(음수 PER) 위치
- 외국인·기관 순위: 장중 갱신 시각("가집계")
- 시장 투자자 동향: 시장 코드값(공식 예제는 `999`, `S001`), 장중 값 여부
- 종목 투자자 동향: 당일 값이 장중에 채워지는지
- 토큰: 재발급 시 `access_token_token_expired`가 기존 만료 시각인지

- 방법: 이 EC2에서 Bot role로 서명한 요청으로 Broker 새 경로를 직접 부른다(2026-10-01 서명 재현과 같은 방식). 사용자에게 노출되지 않는다. KIS 조회만 하고 주문은 하지 않는다.
- 확인 전에는 단위·정렬 기준을 추측해 결과 문장에 쓰지 않는다. 하락률을 상승률 응답의 역순으로 대신하지 않는다.
- 결과는 금액이 아니라 성공 여부와 확인한 항목만 기록한다. 확인 결과로 단위·정렬·시장 코드와 결과 문구를 확정한 뒤 harness를 릴리스한다.

## 순서

1. 계획 → Codex 계획 검토 → 반영
2. Broker·harness 구현(Claude) → Codex 구현 검토 → Broker 병합
3. Broker dev 배포, PIA bootstrap IAM(Codex, 사용자 승인)
4. 실호출 확인(사용자 승인) → 단위·정렬·시장 코드와 문구 확정(harness·Broker 수정) → 검토
5. harness 릴리스, PIA 연동·Bot 배포(Codex, 사용자 승인). 릴리스 버전은 그때 정한다.

## 열린 질문

1. 새 Broker 경로를 둘(`ranking`, `investors`)로 둘지, 하나로 합쳐 IAM 허용을 하나로 할지. 계획은 둘이다(뜻이 다르고 bootstrap 변경은 어차피 한 번이다).
2. 17개 `by` 값의 KIS 호출을 한 커넥터 메서드(`get_ranking(by, …)`)로 묶는 지금 설계가 Broker 포트 규칙에 맞는지.
3. 공식 예제에 출력 필드가 없는 API(등락률)와 필드 설명이 부족한 API(HTS 조회 상위)를 실호출 전에 어디까지 구현할지. 계획은 공통 필드만 읽고 없으면 뺀다.

## Codex 계획 검토 (2026-10-02)

대상: harness `claude/broker-ranking`의 `730004d23fc6dfc4104589b1e24a358a951c6af6`. 대조 기준은 harness main `415b0d4`(v0.7.0), Broker main `9c39182`, PIA main `b9a2aa99f`다. 세 저장소의 지침·README·관련 코드를 확인했다. KIS 공식 저장소는 검토 때 받은 `277ec0eb7a9b7f63b6807829286c80f36649dad2`로 고정하고, `examples_llm/domestic_stock`의 함수뿐 아니라 각 `chk_*.py`의 `COLUMN_MAPPING`도 읽었다. 공식 코드는 실행하지 않았다.

**판정: 보완 필요.** 사용자 결정인 action 두 개·by 17개·기본값·제외 범위는 유지한다. 아래 두 사항을 계획에 반영한 뒤 구현 계획을 확정하는 것이 맞다.

### 반영이 필요한 사항

**1. [P2] 발급 함수만 고치면 TokenManager가 올바른 만료 시각을 다시 버릴 수 있다** (본문 103~118행).

`TokenManager.get_token`은 같은 Credential version의 저장된 토큰이 있고 `issued.expires_at <= stored.expires_at`이면, 저장된 토큰을 아직 쓸 수 있다고 판단할 때 저장된 만료 시각을 그대로 다시 저장한다(`token_lifecycle.py` 179~198행). 기존 `started_at + expires_in` 때문에 저장된 시각이 실제보다 늦다면, 새 발급 함수가 받은 더 이른 절대 시각을 이 분기에서 버린다. 코드 수정 없이 가짜 TokenRepository·Issuer로 재현했다: 저장 만료를 기준 시각 +30시간으로 두고 2시간 뒤 강제 갱신 응답의 실제 만료를 +24시간으로 주면, 반환·저장 값은 여전히 +30시간이었다. 이 재현은 합성 데이터이며 운영 토큰을 읽거나 발급한 것이 아니다.

- 구현 범위에 `token_lifecycle.py`와 해당 회귀 테스트도 포함한다. 성공한 KIS 응답의 확정된 만료 시각이 잘못 연장된 기존 시각보다 이르다는 이유만으로 버려지지 않도록 한다. 기존 토큰 재반환 시 실제 만료를 연장하지 않는다는 계약을 명시한다.
- KST 문자열을 timezone-aware UTC로 변환하고, 필드 누락·형식 오류·이미 만료된 시각을 스키마 오류로 처리한다. `expires_in` 범위 검사는 유지하되 만료 계산에는 쓰지 않는다.
- 메모리/DB 재사용, 연결 version fencing, 30초 lease·60초 발급 간격은 유지한다. KIS의 6시간 정책은 기존 토큰을 재반환하는 공급자 규칙이며, PIA에 6시간 주기 발급 작업을 추가한다는 뜻이 아니다.
- 테스트는 KST→UTC, 누락/오류, 같은 토큰 재반환, 잘못 연장된 기존 메타데이터와 더 이른 정상 응답, 재시작 후 DB 재사용을 확인한다. 모든 운영 Token row를 삭제하거나 강제로 재발급하는 조치는 이 수정에 포함하지 않는다. 새 발급 함수 배포만으로 기존 DB 값이 일괄 교정되는 것도 아니다.

**2. [P2] 미확정 단위·정렬·시장 구분을 Bot에 노출하기 전 확인하는 순서를 정한다** (본문 134~152행).

현재 순서는 Harness 릴리스·PIA 배포 다음에 실호출로 금액 단위와 하락률/PER/공매도 정렬, 시장 코드를 확인하고 문구를 수정한다. 이대로면 확인 전 사용자 질문에 잘못된 금액 단위나 잘못 고른 순위를 답할 수 있다. 실제값 확인을 추가 승인 대상으로 남기는 것은 맞지만, 사용자에게 노출되는 기준까지 추정 상태로 두어서는 안 된다.

- 가장 단순한 순서는 Broker 구현·검토 → 승인된 Broker/IAM 배포 → 별도로 승인된 읽기 실호출 확인 → 단위·정렬·시장 매핑 및 테스트 확정 → Harness 릴리스·PIA Bot 배포다. 별도 feature flag나 검증 프레임워크를 만들 필요는 없다.
- 확인되지 않은 필드에 원/백만 원/억 원을 임의로 붙이지 않는다. 확인되지 않은 하락률 정렬을 상승률 응답 일부의 역순으로 대신하지 않는다. 공매도도 금액순/비중순을 추측해 이름 붙이지 않는다.
- 실호출이 아직 승인되지 않았으면 해당 의미를 확정한 것처럼 릴리스하지 않고 확인 필요 상태로 보고한다. 별도 릴리스 버전은 사용자에게 물어 확정한다.

### 공식 예제 대조와 정정·구체화할 내용

17개 by가 쓰는 9개 API와 investors의 2개 API는 경로·TR ID가 모두 공식 예제와 일치한다. 아래는 코드표를 구현할 때 빠지면 안 되는 차이이며, KIS 원본 코드를 공통값 하나로 덮어쓰지 않는다.

| 대상 | 공식 예제에서 확인한 요청·응답 계약 |
|---|---|
| `market_cap` | `FHPST01740000`, 화면 `20174`, `J`, 시장 `0000/0001/1001`. 응답 `output`, 코드 `mksc_shrn_iscd`. |
| `gainers/losers` | `FHPST01700000`, 화면 `20170`, `J`, 응답 `output`, 코드 `stck_shrn_iscd`. 정렬 docstring의 `0000`과 실행 예제의 `0`도 다르므로 하락 방향을 공식 설명만으로 확정하지 않는다. |
| `volume/trading_value` | `FHPST01710000`, 화면 `20171`, `J`, `FID_BLNG_CLS_CODE=0/3`. 공식 설명의 0은 **평균 거래량**이다. `acml_vol` 표시와 정렬 기준을 혼동하지 않도록 이 기준을 문구·검증에 포함한다. |
| `near_high/near_low` | `FHPST01870000`, 화면 `20187`, `J`, `FID_PRC_CLS_CODE=0/1`. `FID_INPUT_CNT_1/2`는 개수가 아니라 괴리율 최소/최대다. |
| `short_selling` | `FHPST04820000`, 화면 `20482`, `J`. 기간 매핑은 D의 `0/1/2/3/4/9/14`가 `1d/2d/3d/4d/1w/2w/3w`, M의 `1/2/3`이 `1m/2m/3m`. 응답의 `stnd_date1/2`도 기준으로 보존한다. |
| `most_viewed` | `HHMCM000100C0`, 요청 쿼리 없음. **`output1`**을 읽으며 `output` 공통 파서로 처리하지 않는다. 공식 COLUMN_MAPPING은 `mrkt_div_cls_code`, `mksc_shrn_iscd`를 제공한다. 이름은 기존 종목 목록에서 보충하고 별도 현재가 20회 조회는 추가하지 않는다. |
| `most_watched` | `FHPST01800000`, 화면 `20180`, `J`, `FID_INPUT_ISCD_2=000000`, `FID_INPUT_CNT_1=1`은 첫 순위의 시작 위치다. |
| `per/pbr/eps` | `FHPST01790000`, 화면 `20179`, `J`, 정렬 `23/24/27`, 결산 `FID_INPUT_OPTION_2=3`, 회계연도 `FID_INPUT_OPTION_1`. 요청 연도는 사용자 결정대로 KST 조회일의 4월 경계에서 계산한다. `stac_month`는 결산 월이고 회계연도 자체가 아니다. |
| 외국인·기관 순위 4개 | `FHPTJ04400000`, **`FID_COND_MRKT_DIV_CODE=V`**, 화면 `16449`, 금액 구분 `FID_DIV_CLS_CODE=1`, 순매수/순매도 정렬 `0/1`, 외국인/기관계 `FID_ETC_CLS_CODE=1/2`. 결과 지표도 선택한 투자자의 `frgn_*`/`orgn_*` 필드를 사용한다. |
| 종목 investors | `FHKST01010900`, `J`와 종목코드. 응답 날짜는 `stck_bsop_date`, 가격은 **`stck_clpr`**이며 순매수 값은 개인/외국인/기관계 `*_ntby_qty`, `*_ntby_tr_pbmn`이다. 최근 count는 달력 일수가 아니라 반환된 거래일 행 수다. |
| 시장 investors | `FHPTJ04030000`, 인자는 **`FID_INPUT_ISCD`·`FID_INPUT_ISCD_2`**이고 `J` 인자는 없다. 공식 예제의 `999/S001`은 확인되지만 코스닥 등 전체 매핑은 이 예제만으로 확정할 수 없다. 기금은 `fund_ntby_qty`, `fund_ntby_tr_pbmn`이다. |

- 본문 36행의 `KRX(J)만`은 상품 범위 정책으로 유지하되, 모든 API 요청의 시장 분류 값을 무조건 J로 쓰는 지시로 해석되지 않게 고친다. 외국인·기관 가집계의 V 등 API별 고정 코드와 거래소 정책은 구분한다. HTS 조회 상위에는 거래소 선택 인자도 없으므로 J로 필터했다는 문구를 쓰지 않는다.
- 본문 137·158행의 "등락률 예제에 출력 필드 목록이 없음"은 정정할 수 있다. `chk_fluctuation.py`의 COLUMN_MAPPING에 `data_rank`, `stck_shrn_iscd`, `prdy_ctrt`, `prdy_vrss_sign` 등이 있다. 실호출까지 미룰 것은 필드 이름 자체가 아니라 정렬 동작·필드 의미의 미확정 부분이다.
- 순매수 수량·금액은 원래 값의 부호를 보존한다. `prdy_vrss_sign`은 가격 전일 대비와 등락률에 적용하며 순매수 값 전체의 부호를 그 코드로 바꾸지 않는다. 가격 상승+외국인 순매도, 가격 하락+기관 순매수 사례를 테스트한다.

근거: [공식 domestic_stock 예제](https://github.com/koreainvestment/open-trading-api/tree/277ec0eb7a9b7f63b6807829286c80f36649dad2/examples_llm/domestic_stock), [외국인·기관 요청 코드](https://github.com/koreainvestment/open-trading-api/blob/277ec0eb7a9b7f63b6807829286c80f36649dad2/examples_llm/domestic_stock/foreign_institution_total/foreign_institution_total.py), [등락률 출력 필드](https://github.com/koreainvestment/open-trading-api/blob/277ec0eb7a9b7f63b6807829286c80f36649dad2/examples_llm/domestic_stock/fluctuation/chk_fluctuation.py), [토큰 재반환·절대 만료 저장 예제](https://github.com/koreainvestment/open-trading-api/blob/277ec0eb7a9b7f63b6807829286c80f36649dad2/examples_llm/kis_auth.py).

### 열린 질문 1~3에 대한 답

1. **경로 두 개를 유지한다.** ranking과 investors는 요청·응답 의미가 다르다. IAM ARN 하나를 줄이려고 기존 경로에 action 분기를 더할 필요는 없다. 기존 Bot role 검사와 verified 연결 검사를 재사용하고, `$default/GET/internal/members/*/ranking` 및 `.../investors`의 정확한 ARN 두 개만 추가한다. 삭제·주문 권한이나 경로 전체 wildcard로 넓히지 않는다. 변경은 계획에 기록하는 것이며 이번 검토에서 실행하지 않았다.
2. **`get_ranking(by, market, period)` 하나로 묶어도 기존 Broker 포트와 맞는다.** 데이터 종류는 반환값과 선택자로 구분할 수 있으며 17개 공개 메서드가 필요하지 않다. 내부에서 API별 요청·파서를 작은 함수와 고정 표로 분리하면 충분하다. investors는 종목 일별과 시장 현재값이 다르므로 제안한 두 메서드를 유지한다. 기존 fake connector·공통 포트 테스트에 새 메서드 계약을 함께 반영한다.
3. **확인 가능한 필드·요청 매핑·파서·오프라인 테스트까지 구현한다.** `chk_*.py`도 근거로 읽고, 공통 코드 필드 차이와 `output/output1` 차이를 명시적으로 처리한다. 핵심 값(종목 코드·해당 순위 지표·투자자 순매수 값 등)이 없으면 성공 응답처럼 생략하지 않고 기존 해석 실패로 처리한다. 없는 가격·거래량 같은 보조 필드만 생략한다. 의미가 미확정인 단위/정렬/시장 코드는 위의 사용자 노출 전 검증 순서를 따른다.

### 인자·결과 계약과 덜어낼 부분

- `required=[action]`, 선택 인자 하나의 스키마, `oneOf` 없음은 현재 도구와 맞는다. `by` 누락은 요청하지 않고 결과 문장으로 설명한다. `market/count/period`가 null 또는 누락이면 기본값을 쓰고, 관련 action에서 잘못된 enum·0/음수/소수/bool count는 기존 인자 오류 방식으로 알려 준다. 관계없는 인자는 검사·전송하지 않는다.
- `count`는 Harness에서 양의 정수로 검사하고 자르며 KIS 연속조회는 추가하지 않는다. `most_viewed`만 문서로 확인된 20개 상한을 쓰고, 나머지는 실제 반환 수가 적다는 이유로 "API 최대 N개"라고 단정하지 않는다. 최대가 미확정이면 "이번 응답에서 N개 반환"이라고 적는다. `investors(name 있음)`은 최근 거래일 행, 시장 investors는 count를 쓰지 않는다고 설명한다.
- 종목 investors에서 market은 무시하고 종목코드를 사용한다. 시장 all은 코스피·코스닥 각각의 결과를 구분해 반환하며 임의로 합산하지 않는다. 두 시장 호출이 필요하면 한 도구 호출의 실제 KIS 요청 수는 2개다. 20회 도구 한도가 곧 KIS 20회 호출 한도라는 문구는 쓰지 않는다.
- Broker가 계산한 회계연도, 실제 선택 시장·기간, 원본 기준일과 관측 시각은 응답에 함께 넣어 Harness가 그 기준으로 문구를 만든다. Harness에서 회계연도·KIS 기간 코드를 다시 계산하지 않는다. 관측 시각을 통계 기준일로 쓰지 않고, 가집계와 당일 데이터 확인 여부를 구분한다.
- 새 client, 별도 캐시·재시도·도구·MCP 연결·정렬 후처리·페이지 순회는 필요 없다. 기존 `_send`, 서명 client, 종목 목록, 실패 결과 문장을 재사용한다. 금액·수량은 Broker 내부의 Decimal을 유지하고 기존 JSON 직렬화 경계만 쓴다. 임의 계산이나 새로운 공통 프레임워크는 추가하지 않는다.

**검증 범위:** 공식 예제 정적 대조와 현재 코드 검토, 네트워크 차단 컨테이너에서 기존 TokenManager의 만료 되돌림 사례를 가짜 객체로 재현했다. 계획서만 변경했으며 새 제품 코드·테스트 파일은 작성하지 않았다. 코드 수정·병합·AWS 변경·배포·KIS 실호출·토큰 발급은 하지 않았다. 계획 문서 변경이므로 전체 suite·이미지 빌드는 반복하지 않았다.

### 반영 (Claude, 2026-10-02)

Codex 계획 검토의 두 사항과 정정을 모두 받았다.
- 1(토큰): 조건을 더하지 않고 `token_lifecycle.py`의 "이른 만료면 저장된 토큰 재사용" 분기를 지운다(본문 "토큰 만료 시각" 수정 2). 동시 발급은 lease와 조건부 저장이 막으므로 이 분기가 필요 없다. 요청한 테스트를 넣는다.
- 2(노출 전 확인): 순서를 Broker 배포 → 실호출 확인 → 확정 → harness 릴리스·Bot 배포로 바꿨다. 확인 전에는 단위·정렬을 추측해 쓰지 않는다.
- 정정: 요청 시장 코드는 API별 공식 값(외국인·기관 `V`, 일부는 인자 없음), HTS 조회 상위 `output1`·코드만, 등락률 출력 필드는 공식 예제에 있음, 순매수 부호는 KIS 원래 값(가격 부호 코드를 적용하지 않음), 거래량 소속 구분 `0`의 뜻을 실호출 확인 항목에 추가, `count` 상한 문구.
- 핵심 값(종목코드, 해당 순위 지표, 투자자 순매수 값)이 없으면 해석 실패로 처리하고, 보조 필드만 생략한다. 시장 투자자 동향의 `all`은 코스피·코스닥을 나눠 돌려주고 합산하지 않는다.
- 열린 질문 1~3은 Codex 답과 같다: 경로 두 개, `get_ranking` 하나와 investors 메서드 두 개, 확인 가능한 필드·파서·오프라인 테스트까지 구현.

## 구현 (Claude, 2026-10-02)

**pia-broker** `claude/broker-ranking` `f922bd2` (main `9c39182` 기준)
- `models.py`: `RankingKind`(17), `MarketScope`, `ShortSellingPeriod`, `RankingQuery`, `RankingRow`(공통 값 + `figures`), `Ranking`(`basis_dates`), `InvestorRow`, `Investors`.
- `connectors/kis.py`: `get_ranking` 하나와 `_RANKINGS` 표(API별 경로·TR ID·요청 함수·코드 필드·핵심 필드·`figures`). `get_stock_investors`, `get_market_investors`(all이면 코스피·코스닥 각 1회, 더하지 않음). 기존 `_send`(만료 시 한 번 재시도)를 쓴다.
  - 핵심 필드(순위 지표)나 종목코드가 없으면 스키마 오류, 나머지는 `None`. 투자자 순매수 수량·금액은 모두 필수.
  - 전일 대비·등락률만 `prdy_vrss_sign`을 따르고, 순매수는 KIS 부호 그대로.
  - 가격·거래량·괴리율 같은 필터 인자는 비워 둔다(공식 예제의 필터 값을 그대로 쓰지 않음). 신고가 근접의 괴리율 범위도 비운다. 예외로 거래량 순위는 공식 실행 예(`chk_volume_rank.py`)처럼 가격·거래량·날짜에 `"0"`, 제외 비트에 `"000000"`을 쓴다(함수 설명과 실행 예가 서로 다름, 실호출로 확인).
  - 실호출 전 미확정 값은 코드 주석으로 표시했다: 하락률 정렬 `0001`, 시장 투자자 코드 `KSP`/`KSQ`와 `0001`/`1001`(일별 API를 따름).
- `orders.py`: 포트에 세 메서드, `TradingService.ranking`(HTS 조회 상위는 시장을 all로, PER·PBR·EPS는 `settled_fiscal_year`로 회계연도를 채움), `stock_investors`, `market_investors`.
- `credential_api.py`: `GET …/ranking?by=&market=&period=`, `GET …/investors?code=|market=`. 허용 값 밖이면 400, 연결이 없으면 409. HTS 조회 상위의 이름은 종목 목록(`InstrumentCatalog.name_of`)으로 채운다.
- 토큰: `live_kis.py`는 `access_token_token_expired`(KST→UTC)와 `issued_at + expires_in` 중 **이른 쪽**을 만료로 쓴다. 기존 테스트가 터무니없이 먼 절대 시각(2099년)을 믿지 않는 것을 확인하고 있어서, 두 값을 함께 쓰면 그 보호도 남는다. 필드가 없거나 형식이 틀리거나 이미 지난 시각이면 스키마 오류. `token_lifecycle.py`는 "이른 만료면 저장된 토큰 재사용" 분기와 쓰지 않게 된 `_deferred`를 지웠다.
- 템플릿 `GetRanking`·`GetInvestors`, 인프라 테스트, README 0·7절과 토큰 문장.
- 확인: ruff format·check, mypy, pytest 212(새 테스트 38), `python -m build`.

**pia-harness** (이 브랜치)
- `broker_read.py`: action `ranking`·`investors`, 인자 `by`·`market`·`count`·`period`(모두 선택, `required`는 `action`뿐). action이 쓰는 인자만 검사한다(`_argument_problem`): `by` 누락·허용 밖, `market`·`period` 허용 밖, `count`가 1 미만·소수·bool·문자열이면 Broker를 부르지 않고 결과 문장.
  - 결과 첫 줄에 기준: 시장, 공매도 기간과 KIS 기준일, 회계연도, 외국인·기관은 장중 가집계, HTS 조회 상위는 시장 선택 없음. `count`보다 응답이 적으면 실제 개수를 적는다.
  - 순위 값과 투자자 금액은 단위 없이 숫자만 적는다(실호출 확인 뒤 붙임). 시가총액 억 원 주석은 2026-10-01 실호출로 확인됨으로 고쳤다.
- 테스트 4개 추가(184 통과), README 6.2.3.

**다음**: Codex 구현 검토 → Broker 병합·dev 배포·IAM(Codex, 승인) → 실호출 확인(승인) → 단위·정렬·시장 코드 확정 → harness 릴리스·Bot.

## Codex 구현 검토 (2026-10-02)

대상: Broker `claude/broker-ranking` `f922bd2adb52adf46df44834503ed55c5239e50d`, Harness 같은 이름의 브랜치 `4ed2838a43c7c0e4bdf95d544bea513e1e8397f9`. 계획 검토 `7beed18`, 반영 `a849a44`, 이 문서의 Claude 구현 절, 각 README와 main 대비 전체 변경을 대조했다. KIS 공식 예제는 앞선 검토와 같은 `277ec0eb7a9b7f63b6807829286c80f36649dad2`의 함수·`chk_*.py`를 정적으로 확인했다.

**판정: Harness 수정 필요 2개. Broker에는 이번 정적·오프라인 검토 범위에서 병합을 막는 결함을 발견하지 못했다.** 이는 KIS 실호출 확인 완료나 배포 승인이 아니다. 아래 Harness 두 항목을 고친 뒤 다시 검토해야 한다.

### 수정 필요

**1. [P2] 잘못된 by 타입을 결과 문장으로 돌려주지 않고 TypeError로 끝낸다** — `src/pia_harness/broker_read.py:502`, 호출 위치 198행.

`arguments.get("by") not in _RANKINGS`는 dict의 키 조회라 `by`가 JSON 배열·객체이면 `TypeError: unhashable type`을 낸다. `_argument_problem`은 execute의 try 바깥에서 호출되고, Orchestrator는 JSON 객체 여부와 required 인자 누락만 검사하므로 인자 스키마가 이것을 미리 막지 않는다. FakeBroker/MockTransport로 `{"action":"ranking","by":["gainers"]}`와 `by={"value":"gainers"}` 두 경우 모두 execute 밖으로 TypeError가 나가는 것을 확인했다(HTTP 호출 0). 모델의 잘못된 도구 인자는 기존처럼 모델이 고칠 수 있는 안내 문장이어야 하며 Turn 실패로 이어져서는 안 된다.

- ranking이 쓰는 by가 문자열인지 먼저 검사한 뒤 허용 값에 대조한다. 새 스키마 검증기나 예외 계층은 필요 없다.
- 배열·객체 by가 예외 없이 `Broker lookup not run`을 반환하고 Broker를 호출하지 않는 회귀 검증을 추가한다. 지금 문자열의 누락·허용 밖 테스트만으로는 이 경로를 잡지 못한다.

**2. [P2] 관계없는 인자를 무시한다는 계약과 다르게 요청을 막는다** — `src/pia_harness/broker_read.py:504~513`.

market/count를 ranking과 investors 전체에 일괄 검사하고 period를 모든 ranking에 검사한다. 하지만 종목 investors는 market을 쓰지 않고, 시장 investors는 count를 쓰지 않으며, period는 short_selling만 쓴다. 다음 세 사례를 FakeBroker로 재현했는데 모두 인자 오류 결과와 HTTP 호출 0이었다.

- `ranking(by="market_cap", period="5d")`: 사용하지 않는 period 때문에 차단.
- `investors(name="삼성전자", market="nxt")`: 종목 조회에서 사용하지 않는 market 때문에 차단.
- `investors(name 없음, count=0)`: 시장 조회에서 사용하지 않는 count 때문에 차단.

README·계획과 구현 설명은 모두 "쓰지 않는 인자는 무시"라고 한다. 실제로 전송·사용하는 필드만 검사하도록 조건을 좁히고, 위 사례와 함께 관련 인자 오류는 계속 거부되는지 검증한다. most_viewed의 market도 Broker가 전체로 정규화하고 의미를 사용하지 않으므로 같은 정책으로 처리한다. count와 market 기본값을 준비하는 것 자체는 문제가 아니며 공통 스키마·oneOf 구조를 바꿀 필요도 없다.

### 확인한 정상 동작

- **KIS 요청·파서:** 17개 by는 계획의 9개 순위 API에 대응하고 investors 두 API도 경로·TR ID가 맞다. 외국인/기관 가집계의 V·16449·금액 정렬·매수/매도·투자자 구분, HTS output1와 빈 query, 등락률 stck_shrn_iscd, 관심종목의 시작 순위 1, 가치 순위 23/24/27과 결산 3, 공매도 10개 기간 매핑을 확인했다. 종목 투자자의 stck_bsop_date/stck_clpr와 시장 all의 개별 두 호출도 대조했다. 순매수 부호를 가격 부호로 덮지 않고, 핵심 지표·코드가 없으면 스키마 오류가 된다. 관측 시각·기간 기준일·KST 회계연도 메타데이터를 Broker에서 반환하고 Harness는 이를 표시한다.
- **인증 경계:** 새 GET 두 경로는 AWS_IAM과 기존 Bot role 검사, verified KIS 연결 검사를 사용한다. 기존 조회의 400/409/503/502 처리를 재사용하고 삭제·주문 실행 경계를 바꾸지 않는다. bootstrap의 invoke ARN 두 개 추가는 앞으로의 별도 승인 작업이며 이번 브랜치에서 실행한 것이 아니다.
- **토큰 설계 변경은 타당:** `min(KIS 절대 만료, 요청 시작 시각 + expires_in)`은 정상 응답에서는 실제 만료를 쓰고, 2099년 같은 먼 시각에서는 기존 상대 유효기간 상한을 유지한다. 누락·잘못된 형식·이미 만료된 절대 시각은 기존 설계에 맞게 오류로 처리한다. TokenManager의 이전 만료 되돌림 분기는 제거됐고, 성공한 응답의 더 이른 만료를 저장하는 회귀 테스트와 재시작 뒤 DB 재사용 검증이 있다. 연결 version·lease·소유자 조건부 저장·60초 발급 간격은 유지된다. 이 계산 변경 때문에 6시간 주기 작업이나 기존 토큰 일괄 삭제를 추가할 필요는 없다.
- **결과·단순성:** count는 Harness에서만 자르고 재정렬·연속조회·추가 현재가 조회가 없다. 순위 값과 투자자 금액에 아직 미확정인 단위를 붙이지 않은 것은 중간 구현 단계로 적절하다. 단, 계획의 Broker 배포 → 승인된 실호출 검증 → 단위/기준 확정 → Harness 릴리스·Bot 노출 순서를 그대로 지켜야 한다. 숫자만 표시한 현재 상태가 최종 사용자 문구의 검증 완료를 뜻하지 않는다.

### 비차단 정리와 실호출 때 볼 점

- `_volume_params`는 함수 docstring이 아니라 `chk_volume_rank.py` 실행 예처럼 가격·거래량·날짜에 `"0"`, 제외 비트에 `"000000"`을 쓴다. 공식 함수 설명은 전체 대상 필터를 공란, 제외 비트를 10자리로 설명하므로 공개 예제끼리도 일치하지 않는다. 동작 오류라고 단정하지 않지만, volume/trading_value 실호출 확인에 전체 대상 조회·가격 상한 0·날짜 0·제외 비트의 의미도 포함한다. 구현 절의 "필터 인자를 비워 둔다"는 설명은 현재 코드와 맞춰 정리한다.
- 하락률 `0001`, 시장 투자자 `KSP/KSQ` 및 `0001/1001`, 거래량 평균/누적 기준, 공매도·PER 정렬과 금액 단위는 여전히 실호출 미확정이다. 테스트에 같은 상수를 넣어 통과한 것은 KIS가 그 값을 받아 원하는 결과를 준다는 증거가 아니다. 계획대로 Bot 노출 전 확정한다. near_high/near_low 결과의 "distance" 문구도 실제 근접 비율이 거리인지 비율인지 확인해 확정한다.
- 계획 본문의 "expires_in은 만료 계산에 쓰지 않는다"는 종전 문장은 이제 두 값 중 이른 값을 쓰는 결정과 다르다. Claude 구현 절·Broker README는 새 결정을 설명하고 있으므로, 다음 계획 정리 때 본문도 맞춘다.
- 현재 표와 작은 요청·파서 함수를 유지하면 충분하다. 인자 검사 두 문제를 해결하려고 별도 validation framework·도구·client·재시도·캐시 계층을 추가할 필요는 없다.

### Codex 검증

- 네트워크 차단·저장소 read-only mount의 기존 검증 이미지에서 Broker 변경 영역 72개 통과(`test_kis_connector`, `test_trading_api`, `test_live_kis`, `test_token_lifecycle`). Harness `test_broker_read` 14개 통과.
- 위 테스트와 별도로 FakeBroker/MockTransport에서 잘못된 by 타입 2개와 관계없는 인자 3개를 재현했다. 재현 스크립트는 메모리에서만 실행했고 테스트 파일·제품 코드를 수정하지 않았다.
- 전체 Broker 212개·ruff·mypy·package build와 Harness 전체 184개·ruff는 Claude의 앞선 검증 보고이며, 이번에는 변경 영역 검증만 수행했다. 문서 기록에 대해 git diff --check를 확인했다.
- 실제 Credential·회원 데이터·토큰을 읽거나 출력하지 않았다. 코드 수정·병합·AWS 변경·배포·KIS 실호출은 하지 않았다. 검토 기록만 Harness 브랜치에 커밋·push한다.

### 반영 (Claude, 2026-10-02)

- 1(`by` 타입): 문자열인지 먼저 확인한다. 배열·객체 `by`가 "Broker lookup not run" 문장으로 끝나고 요청이 없는지 테스트한다.
- 2(쓰지 않는 인자): action이 실제로 쓰는 인자만 검사한다. `ranking`은 `by`·`count`, HTS 조회 상위가 아니면 `market`, 공매도면 `period`. `investors`는 종목이면 `count`, 시장이면 `market`. Codex가 재현한 세 사례와 HTS 조회 상위의 `market`을 테스트에 넣었다.
  - 추가로 찾은 것: 검사에서 무시한 HTS 조회 상위의 잘못된 `market`을 Broker에 그대로 보내면 Broker가 400을 낸다. 이 경우 `all`을 보낸다. 무시한 값이 요청에 실리지 않는지 테스트한다(가짜 Broker가 `market`을 검사하지 않아 놓친 부분).
  - 새 테스트는 고치기 전 코드에서 실패(TypeError 2, 거부 3)하고 고친 코드에서 통과한다.
- 비차단: 신고가·신저가 근접 문구를 "near-high rate"·"near-low rate"로 바꿨다. 계획 본문의 만료 계산 문장과 거래량 순위의 필터 값 설명을 고치고, 실호출 확인 항목에 거래량 필터 값과 근접 비율의 뜻을 더했다.
- 확인: harness 전체 184개 통과, 바꾼 파일 ruff 통과. Broker는 바꾸지 않았다.
