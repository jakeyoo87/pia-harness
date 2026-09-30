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
- 종목명 → 코드는 `order`와 같은 `/internal/instruments`를 쓴다. AMBIGUOUS면 후보를 보여 주고 사용자에게 묻게 하고, NOT FOUND면 정식 종목명을 묻게 한다(`order`와 같은 문구 방식).
- 결과 텍스트에는 조회 시각(KST)을 붙인다. 지난 Turn의 시세·잔고는 저장된 기록이므로 모델이 다시 조회해야 한다. AGENT_INSTRUCTION의 "시간에 따라 바뀌는 사실은 다시 확인" 규칙이 이미 있다.
- 연결이 없거나(`BROKER_NOT_CONNECTED`) 검증되지 않았으면 "웹에서 증권사를 연결하라"고 안내하게 하는 결과 문장을 준다.
- Broker 오류(5xx, 시간 초과)는 도구 실패 결과로 돌려준다. 재시도는 하지 않는다. 조회라서 사용자가 다시 물으면 된다.

### 구현 위치

**pia-harness** (Claude)
- 새 파일 `src/pia_harness/broker_read.py`: `BrokerReadTool(base_url=..., client=...)` → `.tool()`이 `ReadToolDefinition(name="broker", ...)`을 돌려준다. `BrokerOrderTool`과 같은 Broker origin과 서명된 httpx client를 받는다. harness는 AWS를 모른다.
- 종목 검색 코드는 `broker_order.py`와 겹치므로 작은 공통 함수로 뺀다(동작 변경 없음).
- 도구 설명(description)과 인자 스키마는 이 파일에 둔다.
- 테스트: action별 성공, 종목 AMBIGUOUS/NOT FOUND, 미연결, Broker 오류, 잘못된 인자, 결과 텍스트에 계좌번호가 없는지.
- README 도구 목록에 추가.

**pia-broker** (Codex)
- `quote` 응답에 필드 추가: 전일 대비, 등락률, 거래량, 거래대금, 시가총액, PER, PBR, 52주 최고·최저. KIS 현재가 조회(FHKST01010100) 한 번에 오는 값이라 추가 호출은 없다. 기존 필드(`code`, `price`, `observed_at`)는 유지하므로 `order` 도구에 영향이 없다.
- 신규 `GET /internal/members/{id}/account`: 기존 `get_holdings`의 잔고 조회(TTTC8434R)를 확장해 output1의 종목명·주문 가능 수량·현재가·평가금액·평가손익·수익률과 output2의 예수금·총평가금액·총평가손익을 파싱한다. 여러 페이지 처리는 기존 코드를 쓴다.
- 응답에 계좌번호나 자격 증명은 넣지 않는다.
- 연결 확인은 `TradingService._verified_connection`을 재사용한다.
- 인프라: `broker-credential.template.json`에 경로 하나를 추가한다.
- 필드 이름은 KIS 문서와 `tests/fixtures/kis.json`에 맞춰 확인한다. 실제 KIS 호출 확인은 사용자 승인 뒤에 한다.

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
