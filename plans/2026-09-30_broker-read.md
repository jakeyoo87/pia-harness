# Broker 조회 도구 `broker`

날짜: 2026-09-30
브랜치: pia-harness `claude/broker-read`(계획서와 harness 구현), pia-broker `claude/broker-read`(Broker 구현)

## 목표

PIA가 사용자의 증권사 정보를 직접 조회해 답한다. 예:

- "삼성전자 지금 얼마야? 오늘 몇 % 올랐어? 시가총액은?"
- "내 잔고 보여줘", "예수금 얼마 있어?", "삼성전자 몇 주 팔 수 있어?"
- "삼성전자 몇 주 살 수 있어?"
- "증권사 연결은 돼 있어?"

지금은 증권사 조회 도구가 없다. 그래서 시세는 웹 검색으로 찾고(시나리오 12: 검색 5회), 잔고와 연결 상태는 "확인할 수 없다"고 답한다(2026-09-30 운영 대화).

## 결정 (사용자, 2026-09-30)

- 증권 관련 도구는 `broker`(조회)와 `order`(주문) 두 개다. `order`는 그대로 둔다.
- `broker`는 도구 하나에 `action` 인자를 둔다(Hermes의 `memory`·`cronjob`과 같은 형태).
- action은 네 개다: `status`, `quote`, `account`, `buyable`.
- 매수 가능 금액·수량은 계산하지 않는다. KIS 매수 가능 조회(`TTTC8908R`)를 쓴다.
  - Broker README 규칙: "증권사가 증거금·수수료를 반영해 준 값을 쓰고 직접 계산하지 않는다".
  - 예수금÷가격은 결제 대기, 미체결 주문, 증거금률 때문에 실제와 크게 다를 수 있다.
  - **시장가(`ORD_DVSN=01`) 기준으로 조회한다.** KIS 공식 예제에 따르면 지정가(00)는 종목 증거금률을 반영하지 않는다.
  - **미수 없는 값(`nrcvb_buy_amt`, `nrcvb_buy_qty`)을 쓴다.** 미수 포함 최대값은 사용자가 모르고 미수를 쓰게 할 수 있다.
- 매도 가능 수량은 잔고 조회의 종목별 `ord_psbl_qty`로 답한다. 별도 action은 두지 않는다.
- 증권사 연결 자체(키·계좌 등록)는 도구로 만들지 않는다. 지금처럼 웹에서 한다.
- 구현: harness와 Broker는 Claude가 한다. PIA 연동, IAM, 배포는 Codex가 한다.

다음 단계 후보(이번 범위 밖), 우선순위 순:
1. 주문·체결 내역
2. 기간 시세(일별)
3. 투자자별 매매 동향
4. 순위(`ranking`: 시가총액·등락률·거래량)
5. 호가

같은 범위 밖으로 `order`의 정정·취소가 있다.

## 설계

### 도구 인터페이스 (harness)

```json
{"action": "status | quote | account | buyable", "name": "종목명 또는 null"}
```

| action | 하는 일 | Broker 경로 |
|---|---|---|
| `status` | 증권사 연결 여부와 상태 | 기존 `GET /internal/members/{id}/broker-status` |
| `quote` | 한 종목의 현재가·전일 대비·등락률·거래량·거래대금·시가총액·PER·PBR·52주 최고·최저 | 기존 `GET /internal/instruments` → 기존 `GET /internal/members/{id}/quote` (응답 필드 추가) |
| `account` | 보유 종목(종목명·수량·매도 가능 수량·평균단가·현재가·평가금액·평가손익·수익률)과 계좌 요약(예수금·총평가금액·총평가손익) | **신규** `GET /internal/members/{id}/account` |
| `buyable` | 한 종목의 미수 없는 매수 가능 금액·수량(시장가 기준) | 같은 신규 경로에 `?code=` |

- `quote`와 `buyable`은 `name`이 필요하다. `status`와 `account`는 `name`을 쓰지 않는다.
- 종목명 → 코드 변환은 `order`와 같은 `/internal/instruments`를 쓴다(아래 "종목 코드" 참고).
  - AMBIGUOUS면 후보를 보여 주고 사용자에게 묻게 한다.
  - NOT FOUND면 정식 종목명을 묻게 한다.
  - `order`와 같은 문구 방식이다. 검색 코드는 `broker_order.py`와 공유한다.
- 결과 텍스트에는 조회 시각(KST)을 붙인다.
  - 지난 Turn의 시세·잔고는 저장된 기록이라 모델이 다시 조회해야 한다.
  - AGENT_INSTRUCTION에 "시간에 따라 바뀌는 사실은 다시 확인" 규칙이 이미 있다.
- `buyable` 결과 문장에는 "미수 없이, 시장가 기준"과 KIS가 쓴 계산 단가만 적는다.
- 409(`BROKER_NOT_CONNECTED`) 안내: "이 조회에 쓸 수 있는 확인된 KIS 연결이 없다. 웹에서 KIS 계좌를 연결·확인해 달라." NH 연결만 있어도 409가 나오므로 "연결되지 않았다"고 단정하지 않는다.
- Broker 오류(5xx, 시간 초과)는 도구 실패 결과로 돌려준다. 재시도는 하지 않는다. 조회라서 사용자가 다시 물으면 된다.

### 종목 코드

- 이미 있는 것을 그대로 쓴다: Broker `src/pia_broker/data/instruments.json`(KIS 종목 마스터 KOSPI·KOSDAQ, 2026-09-26 기준)과 `GET /internal/instruments`.
- 검색 순서: 6자리 코드, 정규화한 종목명 정확 일치, 이름 일부 포함. 후보는 최대 5개다.
- 별명("삼전")은 모델이 인자를 쓸 때 정식 이름으로 바꾼다(`order`와 같은 스키마 설명).
- ETF·ETN: 목록에 이름은 있다(3,545종목 중 ETF 계열 이름 784개, `SOL 반도체후공정` 있음).
  - 일반 현재가 조회(FHKST01010100, 시장 구분 `J`)가 ETF·ETN에서도 되는지는 **배포 뒤 실호출로 확인**한다.
  - KIS에는 ETF/ETN 전용 현재가 API가 따로 있다. 일반 경로가 안 되면 ETF·ETN은 "지원하지 않음"으로 안내하도록 범위를 좁히고, 전용 API는 다음 단계로 미룬다.
- 이번 범위 밖: 목록 자동 갱신.

### 구현 위치

**pia-harness** (Claude)
- 새 파일 `src/pia_harness/broker_read.py`: `BrokerReadTool(base_url=..., client=...)`의 `.tool()`이 `ReadToolDefinition(name="broker", ...)`을 돌려준다.
  - `BrokerOrderTool`과 같은 Broker origin과 서명된 httpx client를 받는다. harness는 AWS를 모른다.
- 종목 검색은 `broker_order.py`와 공유하는 작은 함수로 뺀다(주문 동작은 바꾸지 않음).
- 도구 설명(description)과 인자 스키마는 이 파일에 둔다.
- 테스트:
  - action별 성공, 음수(하락·손실) 표시
  - 종목 AMBIGUOUS/NOT FOUND, 409 안내, Broker 오류, 잘못된 인자
  - 결과에 계좌번호가 없는지
- README 도구 목록.

**pia-broker** (Claude)

현재 구조 (main `6b63c94`, 확인 2026-09-30):
- 조회·주문 경로는 `credential_api.py`의 `_trading_route`가 처리한다. `TradingService`(`orders.py`)가 회원의 검증된 KIS 연결을 찾아 `KisOrderConnector`를 부른다. 검증된 연결의 조건은 `_verified_connection`: KIS·PENDING·VERIFIED다.
- `KisOrderConnector`는 `KisReadConnector`를 상속한다. `TradingConnector` Protocol은 `get_price`와 `place_cash_order`만 노출한다.
- `Quote`·`Holding`·`BuyingPower` 모델과 `BrokerReadConnector` 포트는 NH 커넥터와 계약 테스트도 쓴다. 이번에 바꾸지 않는다.
- 기존 금액 파서(`parse_krw`, `parse_nonnegative_*`)는 음수를 거부한다.

변경:
1. **새 모델** (`models.py`)
   - `QuoteDetail`, `AccountPosition`, `AccountSummary`, `Buyable`.
   - 손익·전일 대비·등락률은 부호 있는 `Decimal`이다.
   - 값이 없거나 형식이 다르면 해당 필드는 `None`이다. 필수 필드는 현재가, 종목코드, 보유 수량, 미수 없는 매수 금액·수량뿐이다.
2. **파서** (`transport.py`)
   - 부호 있는 Decimal을 선택적으로 읽는 함수를 추가한다. 쉼표, 비유한수, 빈 값은 `None`으로 처리한다.
   - 기존 파서는 그대로 둔다.
3. **KIS 커넥터** (`KisReadConnector`에 메서드 추가)
   - `get_quote_detail(instrument)`
     - 현재가 조회 한 번으로 추가 필드를 읽는다: `prdy_vrss`, `prdy_ctrt`, `acml_vol`, `acml_tr_pbmn`, `hts_avls`, `per`, `pbr`, `w52_hgpr`, `w52_lwpr`.
     - 전일 대비와 등락률의 부호는 `prdy_vrss_sign`(4·5 하락, 3 보합)을 따른다. KIS가 부호 없이 줘도 하락이 음수가 된다.
   - `get_account_summary()`
     - 기존 잔고 조회(TTTC8434R) 페이지 처리를 공유한다.
     - output1에서 `pdno`, `prdt_name`, `hldg_qty`, `ord_psbl_qty`, `pchs_avg_pric`, `prpr`, `evlu_amt`, `evlu_pfls_amt`, `evlu_pfls_rt`를 읽는다. 보유 수량 0인 행은 뺀다.
     - output2에서 `dnca_tot_amt`, `tot_evlu_amt`, `evlu_pfls_smtl_amt`를 읽는다.
     - output2는 계좌 합계라 **페이지마다 합산하지 않는다**. 마지막 페이지 값을 쓴다.
   - `get_buyable(instrument, price)`
     - TTTC8908R을 `ORD_DVSN=01`로 부르고 `nrcvb_buy_amt`, `nrcvb_buy_qty`, `psbl_qty_calc_unpr`을 읽는다.
     - `ORD_UNPR`은 공식 예제처럼 현재가를 넣는다.
   - 기존 `get_holdings`·`get_price`·`get_buying_power`는 바꾸지 않는다.
4. **서비스** (`orders.py`)
   - `TradingConnector`에 `get_quote_detail`, `get_account_summary`, `get_buyable`을 추가한다.
   - `TradingService.quote`는 `QuoteDetail`을 돌려준다. 새로 `account(member_id)`와 `buyable(member_id, code)`를 둔다. `buyable`은 현재가를 먼저 조회한 뒤 그 가격으로 부른다.
   - 셋 다 검증된 KIS 연결이 없으면 `BROKER_NOT_CONNECTED`다.
   - 테스트용 가짜 커넥터(`tests/test_orders.py`, `tests/test_trading_api.py`)도 맞춘다.
5. **경로** `GET /internal/members/{id}/account` (`credential_api.py`)
   - `code`가 없으면 계좌 요약을, 있으면 `{"buyable": {...}}`만 돌려준다.
   - 인증은 기존 조회 경로와 같다(`allowed_status_role_arn`).
   - 오류 매핑은 `quote`와 같다: Retryable 503, 미연결 409, 그 밖의 확정 오류 502, 잘못된 code 400.
   - `quote` 응답은 기존 `code`·`price`·`observed_at`을 유지하고 필드만 더한다.
   - 응답에 계좌번호·상품코드·자격 증명은 넣지 않는다.
6. **인프라**
   - `infra/broker-credential.template.json`에 `GetAccount` HttpApi 이벤트를 추가한다(AWS_IAM, `GET /internal/members/{member_id}/account`).
   - `tests/test_credential_infrastructure.py`의 AWS_IAM 경로 목록에 추가한다.
7. **테스트·픽스처**
   - `kis.json`에 새 필드와 하락·손실 사례를 넣는다: 음수 전일 대비·손익, 하락 부호.
   - 추가 픽스처: `buyable`, output2 합계.
   - 커넥터 테스트: 여러 페이지 output2 비합산, 필드 누락 시 None, 필수 필드 누락 시 오류, 요청 파라미터(`ORD_DVSN=01`).
   - 서비스 테스트: 미연결.
   - API 테스트: 인증, 오류 매핑, 계좌번호 미노출, `code` 유무 응답 형태.
8. **README**
   - "4. 종목 찾기·현재가" 절에 시세 상세를 추가한다.
   - 계좌 조회 절을 새로 쓴다.
   - 전체 구조 그림과 "harness 도구 연결 예정" 문구를 맞춘다.
9. **범위 밖**
   - NH 조회
   - 종목별 매도 가능 조회(TTTC8408R)
   - 미수 포함 최대값
   - 로그는 기존처럼 응답 본문(잔고·금액)을 남기지 않는다.
10. **호출 한도·시간**
    - `account`는 KIS 잔고 조회를 페이지 수만큼 부른다. 개인 계좌는 보통 1~2페이지다.
    - `buyable`은 KIS를 두 번 부른다(현재가, 매수 가능).
    - Lambda 시간 제한은 30초다. harness의 90초는 이를 늘려 주지 않는다. 여러 페이지 잔고의 시간 초과는 운영 확인 항목이다.
    - KIS 초당 호출 한도는 기존 `EGW00201` 처리(503)를 그대로 쓴다.

**pia-agent (PIA)** (Codex)
- `app/main.py`: 같은 `broker_client`로 `BrokerReadTool`을 만들어 `read_tools`에 추가한다. Broker origin이 없으면 등록하지 않는다(`order`와 같음).
- `infra/dev-deployment-bootstrap.template.json`:
  - account 경로 ARN 파라미터(기존 quote 파라미터와 같은 AllowedPattern 형식, `GET/internal/members/*/account`)를 추가한다.
  - Bot 역할 `execute-api:Invoke` 허용 리소스에 추가한다.
  - 계약 테스트와 change set 입력에도 반영한다.
- Telegram 진행 표시에 `broker` 라벨을 추가한다(예: "증권사 조회: 시세 삼성전자").
- 시스템 프롬프트에 한 문장을 도구 등록 시에만 붙인다(ORDER_PROMPT와 같은 방식): "PIA는 증권사 연결 상태·시세·잔고·매수 가능 금액을 broker 도구로 확인할 수 있다". 과거에 저장된 "확인할 수 없다" 답변이 있어도 도구를 쓰게 하려는 것이다.
- harness 버전 올림(Compaction 크기 변경 포함)과 PIA README 반영을 함께 한다.

## 개인정보·보안

- Broker 응답 본문(잔고·평가금액)은 로그에 남기지 않는다. 기존 규칙대로 크기와 상태만 남긴다.
- 조회 결과는 Turn 도구 기록으로 DynamoDB에 저장된다(30일 보존, 탈퇴 시 삭제).
  - `account`는 한 번에 보유 종목 전체와 예수금을 돌려준다. 그래서 예수금만 물어도 보유 종목 전체가 도구 기록으로 저장된다. 답변보다 저장 범위가 넓을 수 있다.
  - 한 경로를 택한 결과이며, 같은 사용자의 데이터를 같은 보존 규칙으로 저장한다.
- Memory 지침은 이미 보유 종목·계좌 정보를 기억하지 말라고 되어 있다(PIA `MEMORY_INSTRUCTION`). 바꾸지 않는다.
- 계좌번호는 Broker 밖으로 나오지 않는다.

## 순서

1. 계획 → Codex 계획 검토 (완료, 아래 기록) → 반영 (이 개정)
2. Broker·harness 구현(Claude) → Codex 구현 검토 → 병합
3. harness 릴리즈, PIA 연동, Broker 배포(Codex) → 검토 → 배포(사용자 승인)
4. 실제 확인(KIS 실호출, 사용자 승인):
   - 시세: 주식 1개, ETF 1개
   - 잔고: `hts_avls` 단위, 음수 손익 표시
   - 매수 가능: 시장가 기준 값
   - 연결 상태

## Codex 계획 검토 (b91776d, 2026-09-30)

방향은 맞다. 금융 수치에 관한 두 규칙은 구현 전에 고친다.

**구현 전 수정 필요**
1. 예수금÷현재가를 매수 가능 수량으로 답하면 안 된다.
   - Broker README의 기존 계약은 이 값을 직접 계산하지 않도록 명시한다.
   - 이번에는 예수금만 답하고 매수 가능 수량은 제공하지 않도록 범위를 좁힌다.
   - 제공한다면 KIS `TTTC8908R`(`max_buy_qty`)을 쓴다.
2. 하락·손실 값은 음수로 보존한다.
   - 기존 금액 파서(`transport.py`)는 음수를 거부한다.
   - 새 모델은 부호 있는 값을 다루고, 하락·손실 픽스처를 둔다.
   - KIS의 전일 대비 부호 필드도 있다.

**열린 질문 판단**
- 1·5: `account` 한 경로와 새 모델이 맞다. 기존 `Quote`·`Holding`·포트는 유지한다. `TradingConnector`에 메서드를 추가하면 테스트용 구현도 맞춘다.
- 3: 필드 이름은 공식 예제와 대체로 일치한다.
  - `hts_avls`의 표시 단위를 확인하고 시가총액 문구를 정한다.
  - 여러 페이지의 `output2` 총액을 합산하지 않는 테스트를 넣는다.
- 4: Broker `GetAccount` 이벤트와 함께 PIA 쪽에도 다음이 필요하다.
  - bootstrap의 새 account ARN 파라미터와 정확한 허용 리소스(기존 정책은 네 경로만 허용)
  - 계약 테스트
  - change set 입력
  - harness 90초는 Lambda 30초를 늘리지 않는다. 다중 페이지 잔고의 시간 초과는 테스트·운영 확인 항목으로 남긴다.
- NH: 409만 보고 "연결되지 않았다"고 안내하면 틀린다. "이 조회에 쓸 수 있는 확인된 KIS 연결이 없다"로 안내한다.
- ETF·ETN: "추가 작업 없음"은 단정하기 어렵다. KIS에는 ETF/ETN 전용 현재가 API가 있다. 일반 현재가 경로가 ETF·ETN에서 되는지 확인하고, 안 되면 지원 범위를 좁혀 표시한다.
- 데이터 범위: 예수금만 물어도 `account`는 전체 보유 종목을 돌려주고, 그 결과가 Turn에 저장된다. "저장 범위는 늘지 않는다"는 문장은 항상 성립하지 않는다. 경로를 나누자는 뜻이 아니라 결과를 정확히 기록하자는 의견이다.

### 반영 (2026-09-30)

사용자와 정한 대로 계획을 고쳤다.
- 매수 가능 금액·수량은 KIS `TTTC8908R`(시장가 `01`, 미수 없는 값)을 `buyable` action으로 제공한다. 같은 신규 `account` 경로에 `?code=`를 붙여 새 IAM 허용은 하나로 유지한다.
- 부호 있는 값을 다루는 파서와 모델을 새로 두고, 하락·손실 픽스처를 추가한다.
- 그 밖의 지적은 모두 반영했다: `hts_avls` 단위 확인, output2 비합산 테스트, PIA bootstrap ARN 파라미터·계약 테스트·change set, Lambda 30초 운영 확인, 409 안내 문구, ETF·ETN 실호출 확인, 데이터 범위 문장.
- Broker 구현도 Claude가 한다(사용자 결정).

## 구현 (Claude, 2026-09-30)

**pia-broker** `claude/broker-read` `8a96cb9` (main `6b63c94` 기준)
- `models.py`: `QuoteDetail`, `AccountPosition`, `AccountSummary`, `Buyable`. `AccountPosition.code`는 문자열이다. 6자리가 아닌 잔고 코드 하나로 계좌 조회 전체가 실패하지 않게 하기 위해서다.
- `transport.py`: `optional_decimal`(부호 허용, 없거나 읽을 수 없으면 None), `optional_nonnegative_int`, `optional_text`. 기존 파서는 그대로다.
- `kis.py`:
  - 잔고 페이지 처리를 `_balance_pages()`로 빼서 `get_holdings`와 `get_account_summary`가 함께 쓴다. `get_holdings`의 동작은 같다.
  - `get_quote_detail`의 전일 대비·등락률은 `prdy_vrss_sign`을 따른다(4·5 음수, 1·2 양수, 그 밖은 받은 값 그대로).
  - `get_buyable`은 `ORD_DVSN=01`로 조회하고 `nrcvb_buy_amt`, `nrcvb_buy_qty`, `psbl_qty_calc_unpr`을 읽는다.
- `orders.py`: `TradingConnector`에 세 메서드를 추가했다. `TradingService.quote`는 `QuoteDetail`을 돌려준다. `account`, `buyable`을 추가했다. `buyable`은 현재가를 먼저 조회한다.
- `credential_api.py`:
  - `GET /internal/members/{id}/account[?code=]`를 추가했다. `code`가 있으면 `{"buyable": ...}`만 돌려준다.
  - `quote` 응답에는 필드를 추가했다.
  - 숫자는 정수면 int, 아니면 float로 낸다.
- 템플릿 `GetAccount`, 인프라 테스트, 픽스처(하락·손실, output2, buyable), 테스트, README 6절.
- 확인: ruff format·check, mypy, pytest 174개 통과.

**pia-harness** (이 브랜치)
- `broker_instruments.py`: 종목 찾기를 `order`와 `broker`가 공유한다. `order`의 결과 문장은 이전과 같다.
- `broker_read.py`: `BrokerReadTool`, 도구 이름 `broker`.
  - 인자 스키마의 `required`는 `action`뿐이다. Orchestrator는 필수 인자가 null이면 읽기 호출을 실행하지 않기 때문이다.
  - 실패는 모두 결과 문장이다: 409는 확인된 KIS 연결 없음, 503·그 밖의 상태·무응답·잘못된 응답.
  - 시가총액은 "억 원"으로 적는다(KIS `hts_avls`, 단위는 실호출로 확인할 항목).
- `__init__.py`: `BrokerReadTool`을 내보낸다. README 6.2.3.
- 확인: 단위 테스트 180개 통과(새 테스트 10개). 새 파일은 ruff를 통과한다.
- 유료 시나리오 측정은 하지 않았다. PIA 연동 뒤 실호출로 확인한다(순서 4).

**PIA 연동 때 필요한 것(Codex)**: 위 "pia-agent" 항목 그대로다. `BrokerReadTool(base_url=str(broker_client.base_url), client=broker_client)`를 `read_tools`에 넣는다.

## Codex 구현 검토 (broker 8a96cb9, harness 382a540, 2026-09-30)

판정: 병합 전 수정 1건. 두 브랜치의 `git diff --check`는 통과했다. 테스트는 재실행하지 않았다.

**차단**
- `cash_d2`의 의미가 잘못 표시된다.
  - Broker는 KIS `prvs_rcdl_excc_amt`를 `cash_d2`로 내보내고, harness는 이를 "D+2 cash"로 설명한다.
  - KIS 공식 예제는 이 필드를 "가수도정산금액"으로 정의한다. D+2 예수금과 같다고 확인되지 않았다.
  - 첫 버전에서 요청된 것은 예수금이므로, 가장 단순한 수정은 필드와 표시를 빼는 것이다. 테스트도 잘못된 명칭을 고정하고 있다.

**비차단**
- 확인된 것:
  - 음수 보존, output2 비합산
  - KIS `nrcvb_buy_amt`·`nrcvb_buy_qty`와 `ORD_DVSN=01` 사용, 자체 계산 없음
  - 종목 찾기 공통화 뒤 주문 문구·순서 유지, `price`·`observed_at` 유지
  - 잔고 본문 로깅·계좌번호 응답 없음
- `hts_avls`의 "억 원" 표시와 ETF 일반 현재가 경로는 실호출 전 미확인이다. dev에서 확인하기 전에는 사용자 대상 배포를 완료로 보지 않는다.
- "시장가는 상한가로 계산된다"는 harness 문장은 KIS 예제가 보장하는 범위보다 강하다. KIS가 돌려준 계산 단가와 "시장가 기준"만 표시해도 충분하다.
- PIA 연동은 Broker 경로와 권한을 적용한 뒤 Bot에 도구를 등록하는 순서로 한다.

### 반영 (2026-09-30)

- `cash_d2`(KIS `prvs_rcdl_excc_amt`, 공식 정의 "가수도정산금액")를 뺐다: Broker 모델·응답·픽스처·테스트·README와 harness 결과 문장·테스트·README. 첫 버전은 예수금(`dnca_tot_amt`)만 답한다. 위 계획 본문의 같은 표현도 고쳤다.
- 매수 가능 결과에서 "시장가는 상한가로 잡아 계산하니 지정가로 더 살 수 있다"는 문장을 뺐다. 결과 문장은 "미수 없이, 시장가 기준"과 KIS 계산 단가만 적는다. harness README의 같은 이유 문구도 뺐다.
- 확인: Broker ruff format·check, mypy, pytest 174. harness 단위 테스트 180 통과.
