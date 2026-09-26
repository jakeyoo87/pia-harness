# 주문 실행 도구 v1

## 목적

pia-broker 주문 v1(main `6b63c94`)을 쓰는 첫 실행 도구를 만든다. 사용자가 "삼전 10주 사줘"라고 하면 확인 문장을 보여 주고, 다음 Turn에서 동의하면 주문한다.

## 사용자와 합의한 결정 (2026-09-26)

- 항상 확인한다. 실행 도구를 골라도 바로 실행하지 않고 확인 대기만 만든다.
- 확인은 다음 Turn에서, 5분 이내에만 받는다. `confirm`은 모든 실행 도구가 함께 쓰는 선택지 하나다.
- `confirm` 선택 = 실행 확정. 기존 `_claim_commit`(COMMITTING)을 그 시점에 부른다. 새 메시지는 기다리고, 읽기 라우팅 120초 제한은 적용하지 않는다.
- 저장된 요청을 다시 작성하지 않고 그대로 실행한다. 결과는 Jev를 다시 거치지 않고 바로 답변한다.
- 실행 뒤 답변 생성이 실패하면 도구의 고정 결과 문장을 보낸다.
- 확인 대기는 Harness 메모리에만 둔다(재시작 시 사라짐). 확인 문장·안내 문구는 Harness 코드에 둔다.
- 확인 문장은 답변 맨 끝에 따로 붙여 Turn에 저장한다. Memory 안내 목록의 개수 제한에 밀리지 않고, 다음 Turn의 직전 답변(끝부분 유지)에 남는다.
- 되묻기는 `answer`로 끝내고, 대답은 직전 Turn 참고로 다음 Turn에서 해석한다(`question` 선택지 없음).
- 종목은 LLM이 공식 종목명을 쓰고 Broker 종목 찾기로 코드를 얻는다. 지정가 기본(가격 없으면 현재가), 시장가는 요청 시만(예상 금액 표시).
- 범위는 매수·매도. 현재가 단독 읽기 도구, 취소, 체결 조회, 잔고는 나중에.
- 과한 예외 처리는 하지 않는다. 계좌 미연결은 현재가 409를 도구 결과로 설명하는 것만 둔다.

## 구현

- `orchestrator.py`
  - `ExecutionToolDefinition(name, description, arguments_schema, prepare, execute)`, `PreparationResult`, `PreparedAction(summary, confirmation, arguments_json)`
  - `execution_tools` 인자, `confirm`·`answer` 이름 예약, `confirmation_ttl_seconds`(기본 300)
  - 사용자별 `_pending_actions`. 새 확인 대기는 답변 전달이 성공한 뒤에만 저장하고, 확인 대기가 없는 Turn이 전달되면 지운다. `confirm` 실행 직전에 지운다.
  - 만료 판단은 이번 요청의 `accepted_at`과 확인 대기를 만든 요청의 `accepted_at` 차이
  - `_is_current`는 COMMITTING도 현재 generation으로 본다(확정 뒤 답변 작성)
  - 준비 단계의 실패는 읽기 도구와 같이 `TOOL_FAILED`
- `broker_order.py`: `BrokerOrderTool(base_url, auth)` → `definition()`
  - 준비: 인자 확인 → `GET /internal/instruments` → `GET /internal/members/{user_key}/quote` → 확인 문장
  - 실행: `POST /internal/members/{user_key}/orders`, 200의 ACCEPTED·REJECTED 외에는 모두 "결과를 확인하지 못함" 문장
- README 0·1·2번과 6.2절

## 테스트

- 오케스트레이터: 다음 Turn에만 confirm, 한 번만 실행, 관련 없는 Turn 뒤 확인 대기 삭제, 5분 만료와 안내, 부족한 인자는 확인 대기 없음, 확정 뒤 새 메시지는 대기, 확정 뒤 답변 실패 시 고정 문장, 이름 예약
- 주문 도구(httpx MockTransport): 지정가 기본값과 확인 문장, 시장가 예상 금액, 부족·잘못된 인자·AMBIGUOUS·NOT_FOUND, 미연결 409, 저장된 주문 그대로 전송, 시장가는 price 없음, 거부·UNKNOWN·502·timeout 문장

## 남은 일

- 시나리오 테스트(실제 Jev·LLM, 가짜 Broker): "삼전 10주 사줘" → 확인 문장, "응" → confirm, "28만원에" → 다시 order, 관련 없는 말 → answer. 비용이 들어 사용자 승인 후 실행.
- PIA 연결: Bot role의 Broker 경로 3개 권한(bootstrap), `BrokerOrderTool` 등록과 서명기, dev 배포
- 실제 KIS 확인(Broker 직접 호출 → 대화 흐름), 각 단계 승인

## Codex 구현 검토 반영 (2026-09-27, `5573e92` 대상)

- Blocker: 실행 확정 뒤의 실행·답변이 외부 취소 보호(shield) 밖이었다. 실행 → 답변 → 전달·저장을 하나의 소유 작업(`_execute_and_commit`)으로 묶어 기존 shield로 보호한다. 실행 중 외부 취소 테스트 추가.
- Blocker: 한 Turn에서 다시 준비할 때 이전 확인 대기가 남았다. 실행 도구를 고르면 이번 generation에서 이전 Turn의 `confirm`을 숨기고, 준비 결과가 없으면 이번 Turn의 확인 대기도 비운다. 대체된 generation은 저장된 확인 대기를 다시 읽으므로 영향이 없다. 테스트 추가.
- Blocker: 주문번호 없는 `ACCEPTED`를 접수로 안내했다. `broker_order_no`가 없으면 미확정 문구로 보낸다. `order_id` 일치 확인은 Broker 계약과 겹쳐 넣지 않았다.
- 작은 수정이라 재검토 없이 Claude가 확인하고 `verify-harness.sh`로 검증한 뒤 병합했다(사용자 승인).
