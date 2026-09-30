# Broker 조회 도구 `broker`

날짜: 2026-09-30
브랜치: pia-harness `claude/broker-read` (계획서는 여기에 둔다. Broker·PIA 변경은 각 저장소 브랜치에서 한다)

## 목표

PIA가 사용자의 증권사 정보를 직접 조회해 답한다. 예:

- "삼성전자 지금 얼마야? 오늘 몇 % 올랐어? 시가총액은?"
- "내 잔고 보여줘", "삼성전자 몇 주 팔 수 있어?", "예수금 얼마 있어?"
- "증권사 연결은 돼 있어?"

지금은 증권사 조회 도구가 없어서 시세는 웹 검색으로 찾고(시나리오 12: 검색 5회), 잔고와 연결 상태는 "확인할 수 없다"고 답한다(2026-09-30 운영 대화).

## 결정 (사용자, 2026-09-30)

- 증권 관련 도구는 두 개다: `broker`(조회)와 `order`(주문). `order`는 그대로 둔다.
- `broker`는 도구 하나에 `action` 인자를 두는 방식이다(Hermes의 `memory`·`cronjob`과 같은 형태).
- 첫 버전 범위: 시세, 보유 종목, 주문 가능 금액·매도 가능 수량, 연결 상태.
- 다음 단계로 미룸: 주문·체결 내역, 순위 조회(`ranking`: 시가총액·등락률·거래량), `order`의 정정·취소.
- 증권사 연결 자체(키·계좌 등록)는 도구로 만들지 않는다. 지금처럼 웹에서 한다.

## 설계

### 도구 인터페이스 (harness)

```json
{
  "action": "status | quote | account",
  "name": "종목명 (quote에서만, 그 외 null)"
}
```

| action | 하는 일 | Broker 경로 |
|---|---|---|
| `status` | 증권사 연결 여부와 상태 | 기존 `GET /internal/members/{id}/broker-status` |
| `quote` | 한 종목의 현재가·전일 대비·등락률·거래량·거래대금·시가총액 (+PER·PBR·52주 최고·최저) | 기존 `GET /internal/instruments` → 기존 `GET /internal/members/{id}/quote` (응답 필드 추가) |
| `account` | 보유 종목(종목명·수량·매도 가능 수량·평균단가·현재가·평가금액·평가손익·수익률)과 계좌 요약(예수금·총평가금액·총평가손익) | **신규** `GET /internal/members/{id}/account` |

- 주문 가능 금액·매도 가능 수량은 별도 action으로 두지 않는다.
  - 매도 가능 수량: KIS 잔고 조회(TTTC8434R) 결과에 종목별 주문 가능 수량(`ord_psbl_qty`)이 있다. `account` 결과에 포함한다.
  - 주문 가능 금액: 같은 잔고 조회의 예수금으로 답한다. "삼성전자 몇 주 살 수 있어?"는 모델이 예수금과 `quote` 가격으로 계산하고, 수수료 때문에 조금 다를 수 있다고 말한다.
  - 종목별 정확한 매수 가능 수량(TTTC8908R)은 필요해지면 action으로 추가한다. 지금 추가하면 Broker 경로와 IAM 허용이 하나 더 늘어난다.
- 종목명 → 코드는 `order`와 같은 `/internal/instruments`를 쓴다(아래 "종목 코드" 참고). AMBIGUOUS면 후보를 보여 주고 사용자에게 묻게 하고, NOT FOUND면 정식 종목명을 묻게 한다(`order`와 같은 문구 방식).
- 결과 텍스트에는 조회 시각(KST)을 붙인다. 지난 Turn의 시세·잔고는 저장된 기록이므로 모델이 다시 조회해야 한다. AGENT_INSTRUCTION의 "시간에 따라 바뀌는 사실은 다시 확인" 규칙이 이미 있다.
- 연결이 없거나(`BROKER_NOT_CONNECTED`) 검증되지 않았으면 "웹에서 증권사를 연결하라"고 안내하게 하는 결과 문장을 준다.
- Broker 오류(5xx, 시간 초과)는 도구 실패 결과로 돌려준다. 재시도는 하지 않는다. 조회라서 사용자가 다시 물으면 된다.

### 종목 코드

- 이미 있는 것을 그대로 쓴다: Broker `src/pia_broker/data/instruments.json`(KIS 종목 마스터 KOSPI·KOSDAQ, 2026-09-26 기준)과 `GET /internal/instruments`.
- 검색 순서: 6자리 코드, 정규화한 종목명 정확 일치, 이름 일부 포함. 후보는 최대 5개다.
- 별명("삼전")은 모델이 인자를 쓸 때 정식 이름으로 바꾼다(`order`와 같은 스키마 설명).
- ETF·ETN: 목록에 들어 있다(확인 2026-09-30: 3,545종목 중 ETF 계열 이름 784개, `SOL 반도체후공정` 있음). 추가 작업은 없다. ETF 현재가도 같은 KIS 현재가 조회로 받는다.
- 이번 범위 밖: 목록 자동 갱신. 신규 상장이나 종목명 변경은 목록을 다시 넣어야 반영된다. 갱신 방식(주기적 다운로드 등)은 나중에 정한다.

### 구현 위치

**pia-harness** (Claude)
- 새 파일 `src/pia_harness/broker_read.py`: `BrokerReadTool(base_url=..., client=...)` → `.tool()`이 `ReadToolDefinition(name="broker", ...)`을 돌려준다. `BrokerOrderTool`과 같은 Broker origin과 서명된 httpx client를 받는다. harness는 AWS를 모른다.
- 종목 검색 코드는 `broker_order.py`와 겹치므로 작은 공통 함수로 뺀다(동작 변경 없음).
- 도구 설명(description)과 인자 스키마는 이 파일에 둔다.
- 테스트: action별 성공, 종목 AMBIGUOUS/NOT FOUND, 미연결, Broker 오류, 잘못된 인자, 결과 텍스트에 계좌번호가 없는지.
- README 도구 목록에 추가.

**pia-broker** (Codex)

현재 구조 (main `6b63c94`, 확인 2026-09-30):
- 조회·주문 경로는 `credential_api.py`의 `_trading_route`가 처리하고, `TradingService`(`orders.py`)가 회원의 검증된 KIS 연결(`_verified_connection`: KIS·PENDING·VERIFIED)을 찾아 `KisOrderConnector`를 부른다.
- `KisOrderConnector`는 `KisReadConnector`를 상속하므로 `get_holdings`(TTTC8434R), `get_price`(FHKST01010100)가 이미 있다. 지금 `TradingConnector` Protocol은 `get_price`와 `place_cash_order`만 노출한다.
- `Quote` 모델은 가격만, `Holding` 모델은 코드·수량·평균단가만 담는다. 두 모델과 `BrokerReadConnector` 포트는 NH 커넥터와 계약 테스트(`test_connector_contract.py`)도 쓴다.
- `tests/fixtures/kis.json`에는 지금 파싱하는 필드만 있다. 잔고는 `pdno`·`hldg_qty`·`pchs_avg_pric`만 있고 output2는 비어 있으며, 현재가는 `stck_prpr`만 있다.

변경:
1. **시세 상세**
   - `KisReadConnector`에 현재가 조회 응답에서 추가 필드를 읽는 메서드를 둔다. 추가 필드는 전일 대비, 등락률, 거래량, 거래대금, 시가총액, PER, PBR, 52주 최고·최저다.
   - 새 모델(예: `QuoteDetail`)로 돌려준다. 기존 `get_price`와 `Quote`는 바꾸지 않으므로 NH·포트·`order` 도구에 영향이 없다.
   - `TradingConnector`와 `TradingService.quote`가 이 값을 쓰게 한다. `quote` 경로 응답은 기존 `code`·`price`·`observed_at`을 유지하고 필드만 더한다.
   - 값이 비거나 형식이 다르면 그 필드는 null로 둔다. 가격(`stck_prpr`)이 없을 때만 오류다.
2. **계좌 조회**
   - `KisReadConnector`에 잔고 조회 한 번(여러 페이지)으로 계좌 요약을 만드는 메서드를 둔다.
   - output1에서 종목별 코드·종목명·보유 수량·주문 가능 수량·평균단가·현재가·평가금액·평가손익·수익률을 읽는다. output2에서 예수금·총평가금액·총평가손익을 읽는다.
   - 새 모델(예: `AccountSummary`)로 돌려준다. `Holding`·`get_holdings`·포트는 바꾸지 않는다.
   - `TradingConnector`에 이 메서드를 추가한다. `TradingService.account(member_id)`는 `quote`와 같이 검증된 연결이 없으면 `BROKER_NOT_CONNECTED`를 낸다.
   - 보유 종목이 없어도 정상이다. 빈 목록과 예수금을 돌려준다.
3. **경로** `GET /internal/members/{id}/account`
   - `_trading_route`에 추가한다. 인증은 기존 조회 경로와 같다: `allowed_status_role_arn` 역할만 허용한다.
   - 오류 매핑도 `quote`와 같다: Retryable → 503, 미연결 → 409, 그 외 확정 오류 → 502.
   - 응답에 계좌번호·상품코드·자격 증명은 넣지 않는다.
4. **인프라**
   - `infra/broker-credential.template.json`에 `GetAccount` HttpApi 이벤트를 추가한다(AWS_IAM, GetQuote와 같은 형식).
   - `test_deployment_infrastructure.py`의 경로 목록 검사를 맞춘다.
5. **테스트·픽스처**
   - `kis.json`의 `price`와 `balance_page_*`에 새 필드를 넣는다. output2도 채운다.
   - 추가할 테스트:
     - 파싱: 정상, 필드 누락 시 null, 가격 누락 시 오류, 여러 페이지
     - `TradingService.account`: 미연결, 커넥터 오류
     - `_trading_route`: 인증, 오류 매핑, 계좌번호 미노출
   - 필드 이름은 KIS 공식 문서와 예제 저장소(koreainvestment/open-trading-api)로 확인한다. 실계좌 값 확인은 배포 뒤 사용자 승인을 받아 한 번 한다.
6. **범위 밖**
   - NH 연결은 지금처럼 조회·주문 대상이 아니다(`_verified_connection`은 KIS만 찾는다). 이 경우 `broker` 도구의 `status`는 연결을 보여 주고, `quote`와 `account`는 "지원하지 않는 증권사" 안내가 아니라 미연결(409)로 나온다. 문구 차이는 도구 결과 문장에서 `status`와 함께 설명한다.
   - 매수 가능 수량(TTTC8908R)과 매도 가능 수량 전용 조회(TTTC8408R) 경로는 만들지 않는다.
   - 로그: 기존처럼 응답 본문(잔고·금액)은 남기지 않는다.
7. **호출 한도·시간**
   - `account`는 KIS 잔고 조회를 페이지 수만큼 부른다. 개인 계좌는 보통 1~2페이지다.
   - Lambda 시간 제한은 30초이고 harness 쪽 client 제한은 35초다. 조회 전체는 harness 조사 시간 90초 안에 끝난다.
   - KIS 초당 호출 한도는 기존 `EGW00201`(RATE_LIMITED, 503) 처리를 그대로 쓴다.

**pia-agent (PIA)** (Codex)
- `app/main.py`: 같은 `broker_client`로 `BrokerReadTool`을 만들어 `read_tools`에 추가한다. Broker origin이 없으면 등록하지 않는다(`order`와 같음).
- `dev-deployment-bootstrap.template.json`: Bot 역할의 `execute-api:Invoke` 허용에 `GET/internal/members/*/account`를 추가한다.
- Telegram 진행 표시에 `broker` 라벨을 추가한다(예: "증권사 조회: 시세 삼성전자").
- 시스템 프롬프트: "PIA는 증권사 연결 상태·시세·잔고를 broker 도구로 확인할 수 있다"는 한 문장을 도구 등록 시에만 붙인다(ORDER_PROMPT와 같은 방식). 과거 "확인할 수 없다" 답변이 저장돼 있어도 도구를 쓰게 하려는 것이다.
- harness 버전 올림(Compaction 크기 변경 포함)과 PIA README를 함께 반영한다.

## 개인정보·보안

- Broker 응답 본문(잔고·평가금액)은 로그에 남기지 않는다. 기존 규칙대로 크기와 상태만 남긴다.
- 조회 결과는 Turn 도구 기록으로 DynamoDB에 저장된다(30일 보존, 탈퇴 시 삭제). 대화 답변에도 같은 내용이 들어가므로 저장 범위는 늘지 않는다.
- Memory 지침은 이미 보유 종목·계좌 정보를 기억하지 말라고 되어 있다(PIA `MEMORY_INSTRUCTION`). 바꾸지 않는다.
- 계좌번호는 Broker 밖으로 나오지 않는다.

## 순서

1. 이 계획 → Codex 계획 검토
2. Broker 구현(Codex) → 검토 → 병합. 배포는 사용자 승인 뒤
3. harness 구현(Claude) → Codex 검토 → 병합
4. harness 릴리즈 + PIA 연동(Codex) → 검토 → 배포
5. 실제 확인: 시세, 잔고, 연결 상태를 Telegram에서 확인한다. KIS 실호출이므로 사용자 승인 뒤에 한다.

## 열린 질문 (Codex 검토 요청)

1. `account` 한 경로로 보유 종목과 예수금을 함께 주는 것이 맞는가? 아니면 기존 `get_holdings`를 두고 경로를 나누는 편이 나은가?
2. 매수 가능 수량을 예수금÷가격으로 대신하는 판단이 괜찮은가?
3. `quote`에 추가할 KIS 필드 이름(`prdy_vrss`, `prdy_ctrt`, `acml_vol`, `acml_tr_pbmn`, `hts_avls`, `per`, `pbr`, `w52_hgpr`, `w52_lwpr`)과 잔고 필드(`prdt_name`, `ord_psbl_qty`, `prpr`, `evlu_amt`, `evlu_pfls_amt`, `evlu_pfls_rt`, `dnca_tot_amt`, `tot_evlu_amt`, `evlu_pfls_smtl_amt`)가 맞는가?
4. Broker 쪽 누락: IAM, API Gateway 경로, 테스트 계약, 시간 초과(Lambda 30초).
5. 새 모델(`QuoteDetail`·`AccountSummary`)로 기존 `Quote`·`Holding`·포트를 건드리지 않는 방식이 맞는가? NH 연결일 때 `quote`·`account`가 미연결(409)로 나오는 처리가 충분한가?

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
