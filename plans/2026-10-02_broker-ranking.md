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

## Codex 구현 재검토·Simple-first 검토 (2026-10-02)

대상: Harness `claude/broker-ranking` `3d1b6abfc15f97aa8130e253a4df8c85a8dc42b0`(이전 검토 `8d3222a` 대비), Broker는 `f922bd2adb52adf46df44834503ed55c5239e50d` 그대로다. 최신 사용자 요청에 따라 기존 두 지적의 해소와 Broker/Harness의 불필요한 설계·추정한 예외를 함께 검토했다.

**판정: 기존 P2 두 건 해소, 재검토 범위에서 blocker 없음.** 아래 객체형 응답 허용은 비차단 단순화 권고다. 다른 요청 영역은 현재 불변 조건을 지키는 데 필요한 범위이며 새 추상화나 추가 예외 경로가 필요하지 않다. 실호출 미확정 사항은 계획의 Bot 노출 전 검증 조건 그대로다.

### 기존 두 지적 해소

1. `src/pia_harness/broker_read.py:503~506`: by가 문자열인지 먼저 확인하므로 배열·객체도 기존 안내 문장으로 끝난다. FakeBroker에서 두 사례 모두 예외가 없고 HTTP 요청 0인 것을 재확인했다.
2. `src/pia_harness/broker_read.py:507~527`: 실제 사용하는 market/count/period만 검사한다. 공매도 외 ranking의 잘못된 period, 종목 investors의 잘못된 market, 시장 investors의 count=0이 더는 조회를 막지 않는다. most_viewed는 **335행에서 market을 all로 정규화해 전송**하므로 무시한 nxt가 Broker 400으로 이어지지도 않는다. 네 사례의 실제 요청 쿼리까지 확인했다.
3. 근접 비율 문구가 distance에서 near-high/near-low rate로 바뀌어 미확정 계산 의미를 덧붙이지 않는다. 계획 본문의 토큰 계산·거래량 필터 설명도 구현과 맞춰 정리돼 있다.

### 덜어낼 부분과 유지할 부분

| 영역·파일·행 | 판단과 근거 |
|---|---|
| Broker `src/pia_broker/connectors/kis.py:393~398` | **[P3] 객체형 output 허용 제거를 권고한다.** 현재 테스트는 목록형 행을 사용하며 평면 객체형 output을 KIS가 반환한다는 확인된 근거가 없다. `require_list(output)` → 빈 목록이면 기존 스키마 오류 → 첫 행에 `require_mapping`으로 충분하다. 새로운 타입 판별·객체→목록 변환·형태별 fallback을 추가하지 않는다. |
| Harness `src/pia_harness/broker_read.py:500~528`, `334~337` | **유지.** by 타입 검사는 재현된 TypeError를 막고, 작은 uses 집합은 두 action의 실제 인자 범위를 드러낸다. most_viewed의 all 전송은 무시한 값이 HTTP 경계로 넘어가지 않게 하는 필수 정규화다. 새 validator/registry/예외 계층으로 바꾸거나 관계없는 인자 검사로 되돌리지 않는다. |
| Broker `src/pia_broker/connectors/kis.py:578~592`와 `_RANKINGS` 표 | **유지.** API별 경로·TR ID·인자·코드 필드·output/output1 차이를 담는 작은 정적 데이터다. 17개 공개 메서드나 거대한 if 분기로 풀면 같은 요청·파싱을 반복한다. 현재 dataclass와 작은 요청 함수면 충분하며 일반적인 dispatch framework로 확장하지 않는다. |
| Broker `src/pia_broker/connectors/kis.py:878~889`, `893~908` | **핵심 지표·코드·순매수 검사는 유지.** 순위 지표가 없는데도 가격만으로 성공한 순위처럼 답하지 않는 불변 조건이다. core를 figures 첫 항목에서 무조건 유도할 수도 없다(gainers/losers는 공통 change_rate가 핵심이고 figures는 비어 있으며 most_viewed는 코드만 있다). 같은 숫자를 한 번 더 읽는 정도를 줄이려고 별도 캐시·파싱 상태를 만들 이유는 없다. 보조 필드는 기존 optional 처리만 사용한다. |
| Broker `src/pia_broker/live_kis.py:139~157` | **min 계산과 필수 응답 검사는 유지.** 재반환 토큰의 실제 만료를 넘기지 않으면서 기존 expires_in 상한도 지킨다. 한 줄의 상한 계산이며 새 상태·재시도·별도 예외 경로가 아니다. KST 파싱 실패 시 상대값으로 조용히 대체하는 fallback은 추가하지 않는다. |
| Harness `tests/test_broker_read.py:392~429`, Broker `tests/test_live_kis.py:29~70`, `tests/test_token_lifecycle.py:183~202`, `tests/test_kis_connector.py:349~394` | **현재 회귀·계약 검증은 유지.** 실제 실패했던 인자 사례, 전송에서 무시값 제거, 정상/먼 토큰 만료의 다른 경계, DB 재사용, API별 다른 요청 코드를 확인한다. 전체 문자열 snapshot·17개 조합의 동일 파서 검증·사용하지 않는 인자의 모든 조합을 늘릴 필요는 없다. 객체형 응답 지원을 위한 테스트도 새로 만들지 않는다. |

**목록형 후보의 근거 한계:** `pd.DataFrame(output)`만으로 실제 JSON이 반드시 list라고 확정할 수는 없다. pandas는 dict와 여러 iterable도 입력으로 받는다([pandas DataFrame 문서](https://pandas.pydata.org/docs/reference/api/pandas.DataFrame.html)). 위 권고는 “DataFrame이면 무조건 list”가 아니라, 현재 공식 예제·테스트에 맞춰 필요한 목록형 계약만 구현하고 확인되지 않은 평면 객체형 지원을 미리 넣지 않자는 판단이다. 실제 KIS 형태는 승인된 실호출에서 확인한다. 현 코드가 받는 평면 객체형과 pandas가 받는 dict-of-columns도 같은 형태가 아니다.

KIS 근거: 고정 커밋 `277ec0eb7a9b7f63b6807829286c80f36649dad2`의 [시장 투자자 공식 함수](https://github.com/koreainvestment/open-trading-api/blob/277ec0eb7a9b7f63b6807829286c80f36649dad2/examples_llm/domestic_stock/inquire_investor_time_by_market/inquire_investor_time_by_market.py)(59~61행). Broker의 `test_kis_market_investors_ask_each_market_and_never_add_them_up`(486행)도 두 시장 각각 목록형 output을 전달한다. 축소 시 그 정상 계약과 빈 목록의 기존 해석 실패를 확인하면 충분하며, 근거 없이 “반드시 정확히 한 행” 같은 새 제한은 만들지 않는다.

### 검증과 이후 순서

- Harness `test_broker_read` 14개 통과. FakeBroker/MockTransport로 배열·객체 by 2개와 관계없는 인자 4개를 추가 확인했고, most_viewed는 최종 쿼리가 정확히 `by=most_viewed&market=all`임을 확인했다. 모두 네트워크 차단·read-only mount·가짜 데이터로 수행했다.
- Broker는 커밋이 변하지 않아 앞선 72개 변경 영역 검증을 반복하지 않았다. Harness 전체 184개·ruff는 Claude 보고를 근거로 하며 이번에는 관련 테스트만 실행했다.
- 기록에 git diff --check를 수행한다. 제품 코드·테스트 파일·AWS·배포·KIS 실호출은 변경하거나 실행하지 않았다.
- 이후에는 계획대로 **사용자 승인 후 Broker 병합·dev 배포·bootstrap ranking/investors ARN 두 개 적용**, **별도 승인된 실호출로 단위·정렬·시장 코드·응답 형태 확정**, 그다음 Harness 릴리스·PIA Bot 노출 순서를 지킨다. 릴리스 버전은 사용자에게 물어 확정한다. 이번 검토 결과를 그 실행 승인으로 재사용하지 않는다.

### 반영 (Claude, 2026-10-02)

- P3(객체형 output): Broker `236544a`에서 `get_market_investors`가 목록형 output만 받게 줄였다(`require_list` → 빈 목록이면 스키마 오류 → 첫 행 `require_mapping`). 새 제한이나 테스트는 더하지 않았다. 실제 응답 형태는 실호출에서 확인한다.
- 나머지는 Codex 판단대로 유지한다.
- 확인: Broker ruff format·check, mypy, pytest 212 통과.
- 현재: Broker `claude/broker-ranking` `236544a`, harness `claude/broker-ranking`(이 커밋). 병합·배포·IAM·실호출·릴리스는 사용자 승인 뒤 계획 순서대로 한다.

## Claude 진행 검토 (2026-10-02)

대조: Broker main `236544a`, PIA main `6bb9a37`, harness `claude/broker-ranking` `e4251bb`(미병합), 운영 Bot 컨테이너. 코드·AWS·KIS 호출은 하지 않았다.

**1. "그럼 해"의 승인 범위: 실호출까지로 본 것은 넓게 해석한 것이다. 멈춘 것은 맞다.**
- 직전 Codex 답 자체가 "IAM 병합·CI, 그리고 **별도 승인 후** KIS 검증"이었다. 그 답에 대한 "그럼 해"는 별도 승인 단계를 남긴 것으로 읽는 게 맞다. 실호출은 승인할 호출 목록을 보여 주고 받기로 했다(계획 "실호출로 확인할 것").
- 사전 조회(ACTIVE 회원 인덱스의 PK·SK, Broker status GET)는 KIS를 부르지 않았고 원문을 출력하지 않아 피해는 없다. 다만 실호출 준비라서 같은 승인 범위에 들어간다. 다음부터는 대상 회원도 사용자에게 확인받는다(사용자 본인 계정).

**2. 설계 과잉: 코드에는 없다. 문서 두 곳만 줄일 수 있다(비차단).**
- `ops/dev_deployment_rollout.md` 98~106행 "Broker ranking/investors IAM preparation" 문단은 이미 끝난 1회성 절차다. 같은 파일 4단계(24~42행)에 두 ARN이 이미 들어가 있으므로 지워도 된다.
- PIA `README.md` 191~193행의 "GET 권한만 준비하며 Harness 릴리스·Bot 연동은 별도 작업" 문장은 진행 상태라서 연동 때 낡는다. 연동 커밋에서 지우면 된다.
- `tests/test_dev_deployment.py`의 AllowedPattern 전체 일치 검사는 범위를 좁히는 쪽이라 유지한다.

**3. 실호출 검증의 최소 범위와 중단 기준**
- 대상: 사용자 본인 회원 하나(사용자가 확인). 방법: EC2에서 Bot role로 서명한 GET을 Broker에 순서대로 보낸다. 주문·토큰 강제 갱신 경로는 부르지 않는다.
- 호출(13회, 미확정 항목 하나에 한 번):
  1. `ranking?by=market_cap` — `stck_avls` 단위(삼성전자 시가총액을 `quote`의 억 원 값과 비교)
  2. `by=losers` — 정렬 `0001`이 하락률순인지
  3. `by=volume` — 소속 구분 `0`이 누적인지 평균인지(`acml_vol` 순서로 판단), 필터 `0`이 전체인지
  4. `by=trading_value` — `acml_tr_pbmn` 단위
  5. `by=near_high` — 근접 비율의 뜻(현재가·신고가로 판단)
  6. `by=short_selling&period=1w` — 정렬 기준, 기준일 범위
  7. `by=most_viewed` — `output1` 형태
  8. `by=most_watched` — 응답 형태
  9. `by=per` — 정렬 방향, 음수 PER 위치, 회계연도 2025 응답 여부
  10. `by=foreign_selling` — `V` 코드, 금액 단위, 부호
  11. `by=institution_buying` — 기관 구분 코드
  12. `investors?code=005930` — 당일 행 여부, 금액 단위
  13. `investors?market=all` — 시장 코드 `KSP`/`KSQ`·`0001`/`1001`, 응답 형태
- 중단: Broker가 400·403·409·5xx를 주거나, 해석 실패(502 `SCHEMA_INVALID` 포함), KIS 호출 한도 응답이 나오면 그 자리에서 멈추고 보고한다. 같은 요청을 다시 보내지 않는다.
- 기록: 성공 여부, 확인한 뜻·단위·정렬, 공개 시세 예시 숫자. 회원 식별자·계좌·토큰은 쓰지 않는다. 토큰이 만료돼 새로 발급되면 KIS 알림톡이 한 번 갈 수 있다.

**4. Bot 노출: 막혀 있다.**
- 운영 Bot은 `pia-bot:dev-b9a2aa99…`(harness 0.7.0)로, `broker` 도구에 `ranking`·`investors`가 없다. harness 브랜치는 미병합·미릴리스다. Bot role의 새 GET 권한을 쓰는 코드가 Bot에 없다.
- 단위·정렬·시장 코드를 확정하고 harness를 고친 뒤에 릴리스·PIA 연동·Bot 배포를 한다(계획 순서 4~5).

## 실호출 확인 (Claude, 2026-10-02 22:18~22:21 KST, 사용자 승인)

대상: 사용자 본인 회원(활성 회원 1명). EC2 Bot 컨테이너에서 Bot role로 서명한 Broker GET 14회(시가총액·하락률, 상승률, 나머지 11개). 회원 식별자·계좌·토큰은 출력하지 않았다. 주문·토큰 강제 갱신은 하지 않았다. 아래 숫자는 공개 시세다.

| # | 호출 | 결과 | 확인한 것 |
|---|---|---|---|
| 1 | `market_cap` | 200, 30행 | 삼성전자 276,000원, `stck_avls` 16,135,729 → **억 원**(≈1,614조). `market_cap_share` 23.84 = 시장 비중 %. 시가총액 내림차순 |
| 2 | `losers` | **502 `BUSINESS_REJECTED`** | KIS가 요청 거부 |
| 3 | `gainers` | **502 `BUSINESS_REJECTED`** | 정렬 `0000`도 거부 → 등락률 요청 자체가 틀림. 공식 실행 예는 정렬 `"0"`(설명은 `0000`) |
| 4 | `volume` | 200, 30행 | 오늘 누적 거래량 내림차순(4,145,527,462 → 505,027,412 …). `average_volume`이 거래량과 같은 값이라 쓸모없음. 거래대금 = 가격×거래량 → **원** |
| 5 | `trading_value` | 200, 30행 | SK하이닉스 1,842,000×2,032,332 ≈ 3,741,057,167,691 → **원**, 내림차순 |
| 6 | `near_high` | 200, 30행 | **쓸 수 없음**: 거래량 0·등락 0인 종목(거래정지로 보임)이 근접률 0으로 나오고 종목코드 순이다. 필터를 비운 탓. 종목코드에 영숫자(`0016X0`)도 있다 |
| 7 | `short_selling` 1w | 200, 30행 | 기준일 20260928~20261002(영업일 5일). **공매도 수량 내림차순**. 공매도 금액 ÷ 수량 ≈ 주가 → **원**, 비중은 %. 행의 거래량은 기간 누적(삼성전자 79,029,831) |
| 8 | `most_viewed` | 200, 20행 | `output1`, 코드만. 이름은 종목 목록으로 채워짐 |
| 9 | `most_watched` | 200, 30행 | **의심**: 순서가 시가총액 순위와 같고, `inter_issu_reg_csnu`가 삼성전자 16,135,729로 1번의 시가총액 값과 똑같다. 관심 등록 수로 보기 어렵다 |
| 10 | `per` (2025 결산) | 200, 30행 | **PER 높은 순**(9400, 4940, 4240 …). `per` = 100 × 가격 ÷ `eps`로 나와 값의 배율이 불분명. 이익이 아주 작은 종목이 위로 온다 |
| 11 | `foreign_selling` | 200, 30행 | 시장 코드 `V` 동작. 삼성전자 −141,864 / −514,000주 → 금액 **백만 원**(주당 276,000원). 금액 순, 순매도 음수 |
| 12 | `institution_buying` | 200, 30행 | 기관 구분 동작, 단위 11과 같음 |
| 13 | `investors` 005930 | 200, 30행 | 오늘(20261002) 행 있음(장 마감 뒤). 순매수 금액 **백만 원**, 수량 주. 부호 각각 |
| 14 | `investors` all | 200, 2행 | 시장 코드 `KSP`/`KSQ`·`0001`/`1001` 동작. 코스피 개인 −13,287 / −1,789,715 → 수량 **천 주**, 금액 **백만 원**으로 보임(주당 약 134,700원) |

**정리**
- 확인됨: 시가총액(억 원), 거래량·거래대금(주·원), 공매도(수량순, 원·%), HTS 조회 상위, 외국인·기관 순위(백만 원), 종목 투자자 동향(주·백만 원), 시장 투자자 동향(천 주·백만 원, 추정 근거는 주당 금액).
- 고쳐야 함: 등락률(요청 거부), 신고가·신저가 근접(필터 없이 무의미한 결과).
- 뺄지 정할 것: 관심종목 등록 상위(값이 시가총액과 같음), PER·PBR·EPS(높은 순만, 값 배율 불분명).
- 덜어낼 것: 거래량 순위의 `average_volume`.
- 따로 볼 것: 영숫자 종목코드(`0016X0`)는 Broker `InstrumentId`(6자리 숫자)로 `quote`할 수 없다. 이번 범위 밖.

### 실호출 뒤 결정과 수정 (2026-10-02)

사용자 결정: 관심종목 등록 상위와 PER·PBR·EPS 순위를 뺀다(`by` 17개 → 13개). 등락률과 신고가·신저가 근접은 고쳐서 다시 확인한다.

- Broker `claude/broker-ranking-fixes` `1066fc5`(main `236544a` 기준): 네 순위와 회계연도 계산(`settled_fiscal_year`, `RankingQuery.fiscal_year`)을 지웠다. 등락률 정렬을 공식 실행 예처럼 `"0"`(상승)·`"1"`(하락, 확인 필요)로, 신고가·신저가는 공식 실행 예의 거래량 100주·괴리율 0~10% 필터를 넣었다(가격 범위는 넣지 않음). 거래량 순위의 `average_volume`을 뺐다. ruff·mypy·pytest 205.
- harness(이 커밋): 같은 네 순위를 뺐다. 확인된 단위를 결과 문장에 붙였다(시가총액 억 원, 비중 %, 거래대금·공매도 금액·신고가 원, 순매수 백만 원, 수량 주/천 주). 공매도 기준에 "volume over the period"를 적는다. 근접 비율은 다시 확인할 때까지 숫자만. 테스트 184 통과.
- 다시 확인할 것(Broker 재배포 뒤, 4회): `gainers`, `losers`(정렬 `1`이 하락률순인지), `near_high`, `near_low`(거래 있는 종목이 근접 순으로 나오는지, 근접 비율의 뜻).

## Codex 실호출 후 수정 검토 (2026-10-02)

대상: Broker `claude/broker-ranking-fixes` `1066fc59a7dae37c8c2fbaf1d40a44dc9158ab88`(main `236544a` 대비), Harness `claude/broker-ranking` `f9f4bb544fe98b0d7117b7c23af7c4ddcd43c40f`(이전 `e4251bb` 대비). 본문 Claude 실호출 확인·최신 사용자 결정·수정 기록, README, 코드·테스트와 앞선 공식 예제 고정 커밋 `277ec0e`를 대조했다.

**판정: blocker 없음.** 승인된 범위대로 검토 기록 후 Broker만 main 병합·CI 확인·broker-credential dev 재배포를 진행할 수 있다. 이는 Harness 병합·릴리스, PIA/IAM 변경, Bot 배포, KIS 재확인의 승인이 아니다.

### 확인 결과

- **13개 계약으로 함께 축소:** Broker `RankingKind`, `_RANKINGS`와 Harness enum·설명·단위 표에서 most_watched/per/pbr/eps가 제거됐다. Broker의 관련 요청 함수·가치 순위 표·회계연도 필드·JSON 출력·서비스 연도 계산·전용 테스트도 함께 없어졌고 잔존 참조를 확인했다. 기존 quote의 PER/PBR는 별개 데이터이므로 정상적으로 유지한다. Harness는 네 삭제값을 안내 문장으로 거부하고 HTTP를 보내지 않는다.
- **요청 수정:** 등락률 정렬은 `0000/0001`에서 `0/1`로 좁은 값 교체다. 공식 chk_fluctuation 실행 예의 0과 일치하며 하락 1의 실제 뜻은 아직 재확인 대상이다. 근접 순위는 공식 chk_near_new_highlow 실행 예의 거래량 100·괴리율 0/10을 적용하되 가격 10,000~50,000 필터를 따라 넣지 않았다. 기존 시장 선택·상승/하락 및 신고/신저 구분은 유지된다. 재정렬·새 조회·추정 응답 fallback은 추가하지 않았다.
- **결과 단위:** 실호출 기록의 시가총액 억 원, 거래대금·공매도 금액·신고/신저가 원, 비중 %, 순매수 백만 원, 종목 수량 주·시장 수량 천 주가 해당 formatter에 대응한다. 음수 순매수 부호는 유지하고 값을 임의로 배율 계산하지 않는다. 공매도 행 거래량은 기간 누적이라는 기준이 붙었다. near_high_rate/near_low_rate는 미확정이므로 숫자만 표시한다.
- **경계 유지:** 기존 by 타입 검사, action별 관련 인자 검사, most_viewed의 market=all 전송, Bot role·verified 연결·서명 client·토큰 정책·주문 확인 경계는 바뀌지 않았다. IAM과 새 API 경로를 다시 변경할 필요가 없다.

### Simple-first와 재확인 범위

- 네 순위와 연도 계산, 중복 average_volume을 제거한 것은 최신 결정에 맞는 실제 축소다. 나머지 정적 순위 표·필수 지표/코드 검사·목록형 응답 검사는 필요한 계약이므로 유지한다. unit callback을 이름과 함께 둔 작은 `_FIGURES` 표와 공유 formatter는 단위를 반복 if문으로 처리하는 것보다 단순하다. 새 provider/validation/formatter framework가 필요하지 않다.
- 현재 표와 함수에서 추가로 없애야 할 설계 과잉은 찾지 못했다. 삭제된 기능의 전용 테스트는 제거하고 남은 요청 코드·필터·부호·단위·인자 회귀 검증은 유지하는 것이 맞다.
- `0/1`과 100주·0~10% 필터는 코드·예제 대조와 가짜 테스트로 검증했을 뿐, 새 배포에서 실제 의미까지 확인된 것은 아니다. 사용자 지시대로 재배포 뒤 Claude가 gainers/losers/near_high/near_low 네 개만 별도 승인 범위로 확인한다. 이번 Codex 작업은 이를 호출하지 않는다.
- Bot 노출 전 근접 비율의 의미·단위를 확정할 때 100주·0~10%로 제한된 대상이라는 기준도 결과 문구에 드러나게 확인한다. 시장 투자자 천 주 단위는 기존 실호출 기록의 해석을 근거로 했으며 Codex가 이번에 독립 실측한 것이 아니다.

### Codex 검증

- 네트워크 차단·read-only source mount에서 Broker 변경 영역 85개 통과(`test_kis_connector`, `test_trading_api`, `test_orders`), Harness `test_broker_read` 14개 통과.
- 별도 메모리 내 가짜 Broker 검증: Harness by 13개, 삭제값 네 개 요청 0, 제거한 formatter 키 없음, 억 원·백만 원·%·주/천 주와 음수 부호, 근접 비율의 무단위 표시를 확인했다. 제품 코드·테스트 파일은 수정하지 않았다.
- 전체 Broker 205개·ruff·mypy·package build와 Harness 전체 184개는 Claude의 앞선 검증 보고다. 이번에는 변경 영역만 확인했고 삭제 기능과 관계없는 검사·전체 빌드는 반복하지 않았다. git diff --check 통과.
- KIS·회원 데이터·토큰 조회·실호출·강제 발급은 하지 않았다. 이 절은 검토 시점의 기록이며 실제 main CI·배포 결과는 이후 별도로 확인한다.

## 재확인 (Claude, 2026-10-02 22:42 KST, 사용자 승인, Broker `1066fc5` 배포 뒤)

| 호출 | 결과 | 확인한 것 |
|---|---|---|
| `gainers` | 200, 30행 | **순서가 전일 대비 등락률 순이 아님**: 1위가 −91.07%(한창, 112원), 이어 +29.91, +29.96, +29.95, +19.36, +29.8 … |
| `losers` | 200, 30행 | 앞 세 행은 −32.5, −20.77, −18.38%이지만 그 뒤에 +0.74, +1.37, +0.78%가 섞인다 |
| `near_high` | 200, 30행 | 거래가 있는 종목만 나옴(필터 동작). 모두 근접률 0(오늘 신고가)이고 종목코드 순 → 앞쪽이 채권·머니마켓 ETF |
| `near_low` | 200, 30행 | 같은 모양. 근접률 0, 코드 순, 해외 ETF·스팩이 앞쪽 |

**해석**
- 등락률: 정렬 `0`·`1`은 KIS가 받는다. 순서는 **전일 종가 대비가 아니라 당일 저가 대비(상승)·고가 대비(하락)**로 보인다. 그렇게 보면 한창(−91%지만 저가에서 많이 오름)이 1위이고, 하락 쪽에 오른 종목(고가에서 내려옴)이 섞이는 것이 설명된다. KIS 등락률 순위의 가격 구분 코드(`fid_prc_cls_code`)가 이 기준을 정하는 것으로 보이며, 지금은 `0`이다. `1`(종가 대비)로 바꿔 다시 확인할 필요가 있다. 근거는 응답 순서뿐이고 공식 설명("0: 전체")과는 다르다.
- 신고가·신저가 근접: 값은 맞아 보인다(근접률 오름차순, 동률은 코드 순). 다만 오늘 신고가인 종목이 많아 앞 10개가 코드 순 ETF로 채워져 사용자 질문("요즘 신고가 종목")에 쓸모가 적다. 신고가 기간(52주 등)도 응답만으로는 알 수 없다.

### 재확인 뒤 결정과 수정 (2026-10-02)

- 사용자 결정: 신고가·신저가 근접을 뺀다(`by` 13개 → 11개). 등락률은 KIS 자료를 다시 확인하고 고친다.
- 근거: KIS 공식 저장소 `legacy/postman/실전계좌_POSTMAN_샘플코드_v2.6.json`의 등락률 순위 인자 설명. 정렬 `0` 상승률순·`1` 하락률순·`2` 시가 대비 상승률·`3` 시가 대비 하락률·`4` 변동률. 가격 구분 `fid_prc_cls_code`는 상승률순이면 `0` 저가 대비·`1` 종가 대비, 하락률순이면 `0` 고가 대비·`1` 종가 대비. 재확인에서 본 순서(저가·고가 대비)와 맞는다.
- Broker `claude/broker-ranking-fluctuation` `8e62cf0`(main `1066fc5` 기준): 가격 구분 `1`, 신고가·신저가 순위 삭제. ruff·mypy·pytest 204.
- harness(이 커밋): 신고가·신저가 삭제와 문구. 테스트 184 통과.
- 다시 확인할 것(Broker 재배포 뒤 2회): `gainers`·`losers`가 전일 대비 등락률 순인지.

## Codex Postman 기준 수정 검토 (2026-10-02)

대상: Broker `claude/broker-ranking-fluctuation` `8e62cf0a85371d711689b42e8c6c9f927f2d7168`(main `1066fc5` 대비), Harness `claude/broker-ranking` `ad0bd4a1e93855b3de7bb6ccf8b78b7d9fc143d8`(이전 `f9f4bb5` 대비). 본문 재확인·재확인 뒤 사용자 결정과 각 README·코드·테스트를 대조했다.

**판정: blocker 없음.** 사용자 승인 범위대로 검토 기록을 push한 뒤 Broker만 main 병합·CI 확인·broker-credential dev 재배포를 진행할 수 있다.

- **공식 근거 확인:** 앞선 공식 KIS 저장소 고정 커밋 `277ec0e`의 `legacy/postman/실전계좌_POSTMAN_샘플코드_v2.6.json`에서 등락률 요청의 설명을 직접 읽었다. 정렬 0은 상승율순, 1은 하락율순이며 가격 구분은 상승 정렬에서 0=저가 대비/1=종가 대비, 하락 정렬에서 0=고가 대비/1=종가 대비다. Postman 요청 예의 기본 value는 0이지만 그 값을 그대로 복사하는 것이 아니라, 원하는 종가 대비 기준에 해당하는 설명의 1을 선택한 구현이 맞다. 가짜 요청 테스트가 gainers/losers 모두 가격 구분 1과 정렬 0/1을 확인한다. 변경한 실제 순서가 전일 대비인지에 대한 운영 확인은 아직 별개다.
- **삭제 계약:** Broker enum·순위 표·근접 요청 함수·전용 필드·테스트에서 near_high/near_low를 제거했다. Harness enum·설명·근접 formatter와 README도 함께 정리됐다. 양쪽 순위 값은 동일한 11개이며, 두 삭제값은 Harness에서 안내 문장으로 끝나고 HTTP 요청을 보내지 않는다. Broker의 기존 enum 경계도 삭제값을 거부한다. 기존 quote의 52주 고가·저가 필드는 다른 데이터이므로 삭제하지 않았다.
- **불변 조건:** 시장 선택, period의 공매도 전용 사용, 부호·단위 표시, most_viewed의 market=all 전송, 핵심 지표 검사, Bot role·verified 연결 검사, 토큰 만료·lease 정책은 변경하지 않았다. API 경로·IAM을 바꾸거나 PIA/Bot을 배포할 필요가 없다.
- **Simple-first:** 가격 구분 상수 한 개를 바꾸고 목적에 맞지 않은 순위를 제거하는 최소 수정이다. 이미 알려진 기준을 맞추기 위해 결과를 다시 정렬하거나 저가·고가 기준을 보정하는 별도 계산·재시도·fallback을 추가하지 않았다. 삭제한 기능의 전용 테스트도 함께 줄였고 남은 회귀 검증은 필요하다. 추가 설계 과잉이나 덜어내야 할 예외 경로는 발견하지 못했다.

**Codex 검증:** 네트워크 차단·read-only source mount에서 Broker `test_kis_connector`·`test_trading_api` 52개와 Harness `test_broker_read` 14개 통과. 별도 AST 비교로 두 저장소의 by 11개 일치를 확인하고, 가짜 Broker로 near_high/near_low의 HTTP 요청 0을 확인했다. 전체 Broker 204개·ruff·mypy와 Harness 전체 184개는 Claude 보고를 근거로 하며 전체 검증·빌드는 반복하지 않았다. git diff --check 통과.

검토 중 제품 코드·테스트 파일을 수정하지 않았고 회원·KIS·토큰 실조회도 하지 않았다. 이후 승인된 Broker 배포만 진행하며 Harness 병합·릴리스, PIA·IAM 변경, Bot 배포, KIS 호출은 하지 않는다. 재배포 뒤 gainers/losers 두 건의 실호출 확인은 사용자 지시대로 Claude가 수행할 다음 단계다. 이 절은 검토 시점 기록이고 실제 CI·배포 결과는 이후 별도로 확인한다.
