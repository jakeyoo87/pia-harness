# Harness가 붙인 글은 다음 Turn에 다시 넣지 않는다

브랜치 `claude/harness-appended` (기준 main `fb8d9b2`, 0.6.0 미릴리스). 사용자와 대화로 정했다. 릴리스 전 측정(`tests/manual/records/2026-09-30_release-check.md`) execution에서 찾았다.

## 문제

- 주문을 준비하면 Harness가 모델 답변 끝에 주문 도구가 만든 확인 질문(`PreparedAction.confirmation`, 예: "삼성전자 1주를 285,500원 지정가로 매수할까요? (11:24 기준 현재가 285,500원)")을 붙여 보낸다. Memory 결과 알림과 확인 만료 알림도 같은 방식으로 붙인다(`_final_text`, `_add_notice`).
- 주문 도구 결과에는 이미 "확인 질문은 자동으로 붙으니 반복하지 마라"가 있다. 그런데 execution의 주문 준비 답변 중 4건(o9-1, o9-2, o13, o21)에서 모델이 확인 질문을 직접 쓰고 Harness 질문이 또 붙었다.
- 원인은 출처 목록 중복과 같다. Turn에는 사용자가 본 최종본(모델 답변 + Harness가 붙인 글)이 `assistant_message`로 저장되고, 다음 Turn Context에 "모델이 예전에 쓴 답변"으로 다시 들어간다. 도구 기록이 함께 저장되면서, 모델은 "도구는 반복하지 말라 했는데 내 답변은 질문으로 끝났다"는 예시를 보고 따른다. 도구 기록 저장 전 측정에는 없었다.
- 출처 때 세운 원칙("저장되어 모델에게 다시 보이는 답변은 모델이 써도 되는 모양")이 Harness가 붙이는 다른 글에서 깨졌다.

## 결정 사항 (사용자)

1. Harness가 글을 붙이는 것은 유지한다. 확인 질문은 실행될 주문 값과 한 글자도 달라선 안 되므로 도구가 만든 문구를 코드가 붙인다(모델이 옮겨 쓰면 틀릴 수 있다). Memory 결과는 답변 뒤에 저장되므로 모델이 쓸 수 없고, 확인 만료는 Harness만 안다.
2. Turn에 **모델이 쓴 답변**과 **사용자가 본 최종본**을 나눠 저장하고, 다음 Turn Context에는 모델이 쓴 답변만 넣는다. 사용자에게 보내는 글은 지금과 같다.
3. 지침만 더 강하게 하는 방법(이미 도구 결과로 금지했는데도 예시를 따랐다), 확인 질문을 모델이 쓰게 하는 방법(주문 안전), 저장된 글에서 붙인 부분을 사후에 잘라내는 방법(깨지기 쉬움)은 쓰지 않는다.

## 변경

- `CompletedTurn`에 `model_answer: str = ""`를 둔다. 모델이 쓴 답변(출처를 번호 링크로 바꾼 뒤, Harness가 붙이기 전)이다. `assistant_message`는 지금처럼 사용자가 본 최종본이다.
- 저장: `append_completed_turn(..., model_answer=...)`. `_commit_response`가 `answer.text`(번호 링크 적용 뒤)를 넘긴다. 실행 확정 뒤 답변 경로도 같다.
- Context 조립: 이전 Turn의 `ASSISTANT_TURN`은 `turn.model_answer or turn.assistant_message`. 칸이 빈 예전 Turn(0.5.0 저장분)은 지금처럼 최종본을 넣는다.
- 그대로 두는 것: Summary·Memory 작성 입력은 `assistant_message`(사용자가 본 최종본, 무엇을 확인·알렸는지 남김). 링크 읽기 경계는 `ASSISTANT_TURN`을 보므로 모델 답변의 링크만 읽는다(붙인 글에는 링크가 없다). 확인 대기 목록은 지금처럼 매 Turn Harness 알림(`_waiting_note`)으로 들어가므로 "응" 해석에 필요한 정보는 남는다.
- 크기: Turn 저장 한도(256KiB)에는 두 칸을 모두 센다. 압축의 최근 Turn 몫(10만 자)은 Context에 들어가는 쪽(모델 답변, 없으면 최종본)으로 센다.
- `InMemoryConversationStore`·저장소 계약 테스트·`validate_loaded_turns`를 맞춘다. PIA DynamoDB는 0.6.0 연동 때 도구 기록 칸과 함께 이 칸도 저장한다(빈 값이면 예전 Turn과 같게 동작).
- README 3장(다시 넣는 답변은 모델이 쓴 부분), 6.4(확인 질문은 사용자에게만).

## 테스트

- 주문 준비 Turn: 사용자는 확인 질문이 붙은 최종본을 받고, 저장된 `model_answer`에는 확인 질문이 없고, 다음 Turn Context의 과거 답변에도 없다. 확인 대기 알림은 다음 Turn에 있다.
- Memory 실패 알림이 붙은 Turn도 같다.
- `model_answer`가 빈 예전 Turn은 최종본을 넣는다.
- 저장소 계약: 칸 왕복, 크기 한도에 포함.
- Summary 입력은 최종본.

## 측정 (유료, 실행 전 사용자 승인)

- execution 세트: 확인 질문 중복 0건, 주문 흐름 그대로. read 세트는 바뀌는 곳이 과거 답변 모양뿐이라 다시 돌리지 않는다(Codex가 필요하다고 보면 돌린다).

## 검토 요청 시 볼 점

1. 두 칸으로 나누는 것이 가장 단순한 범용 대응인지, 더 단순한 방법이 있는지.
2. Context에 모델 답변만 넣을 때 확인 대기·"응" 해석·링크 경계·Summary/Memory에 빠지는 정보가 없는지.
3. 저장 계약(새 칸, 빈 값 호환, 크기 한도)과 PIA 연동에서 챙길 것.
4. 빼도 되는 것, 빠진 것.

## Codex 검토 (2026-09-30)

기준 `main fb8d9b2`의 Turn 저장·Context 조립·Summary·Memory·확인 대기 코드와 이 계획, 측정 기록을 대조했다. 검토만 했으며 구현·README 수정·병합·AWS 변경·배포·실제 모델/공급자 호출은 하지 않았다. 계획만 있는 브랜치라 테스트는 재실행하지 않았다.

### Blocker

없다. 현재 `_commit_response`는 출처를 정리한 `answer.text`에 Memory/만료 안내와 `PreparedAction.confirmation`을 붙여 전달·저장한다(`orchestrator.py` 1114–1158행). 이전 Turn의 `assistant_message`는 도구 기록 뒤에 네이티브 assistant 메시지로 재생된다(`context.py` 179–194행). 따라서 사용자가 본 최종본과 모델이 쓴 부분을 나누고, 재생에만 후자를 쓰는 변경은 보고된 중복의 원인에 맞는다. 확인 대기는 최종 답변 전달 뒤 Harness 메모리에 저장되고, 다음 Turn에 별도 `_waiting_note`로 action ID와 주문 요약이 주어진다(`orchestrator.py` 1134–1143, 1288–1298행). 확인 질문을 이전 assistant 메시지에서 빼도 `confirm`의 첫 호출·ID 검증·재실행 방지 규칙을 바꾸지 않는다. Summary/Memory의 `_turn_data`가 최종 `assistant_message`를 쓰는 경계(`openrouter.py` 615–620행)도 계획과 맞는다.

### Non-blocker

1. **바로 다음 Turn에서는 사용자가 본 Harness 안내 일부가 보이지 않는다(22–23행).** 예를 들어 Memory 저장 실패 뒤 사용자가 “왜 방금 기억에 반영하지 못했어?”라고 묻거나, 확인 만료 안내 뒤 “왜 시간이 지났다고 했어?”라고 물으면 `model_answer`에는 그 안내가 없고, Summary는 아직 만들어지지 않았을 수 있다. 현재 Memory 내용과 확인 대기 여부는 별도로 보이지만, *어떤 문구를 사용자에게 보여 줬는지*는 복원되지 않는다. 이것은 중복 방지를 위해 최종본을 네이티브 assistant 문맥에서 빼는 선택의 범위다. 새 상태를 추가하기보다 이 UX 한계를 명시하고, 실제 필요가 확인될 때만 별도 개선하는 편이 Simple-first에 맞는다.
2. **기존 Turn의 중복 위험은 남는다(22·32행).** `model_answer`가 없는 0.5.0 row는 최종본을 재생하므로, 기존 대화에 붙은 확인 문장이 있으면 모델이 계속 볼 수 있다. PIA 연동 때 기존 기록이 즉시 새 모양으로 바뀐다고 설명하지 말고, 새로 저장되는 Turn부터 개선된다고 명시하라. 과거 기록에 대한 별도 migration을 이번 계획에 넣을 필요는 없다.
3. **측정 범위(36–38행):** execution 26개 재측정이 핵심이다. 다만 Context 재생은 읽기 대화에도 적용되므로 전체 read 35개를 다시 돌릴 필요는 없지만, Memory 안내가 붙은 Turn의 바로 다음 질문과 번호 링크 후속 질문을 소수만 고른 수동 확인은 유효하다. 유료 실행은 계획대로 별도 승인 후에만 한다.

### 구현 때 고정할 계약

`model_answer`는 기존 `CompletedTurn`의 기본값 필드 뒤에 추가해 위치 인자 사용처를 깨지 않도록 한다. 저장소의 동일 `turn_id` 재호출 비교와 256KiB 한도에는 두 답변 칸을 모두 포함하고, 구형 row의 누락 필드는 빈 문자열로 읽는다. 최근 Turn 보호 크기는 Context에 실제 재생하는 답변을 세되, Summary/Memory 작성 데이터는 계속 최종본을 쓴다. PIA DynamoDB 저장소가 이 선택적 필드를 왕복하도록 바꾸기 전에는 PIA의 Harness wheel pin을 올리지 않는다. 이 이상의 라우터·후처리 추측 규칙은 필요 없다.

## 계획 변경 (사용자, Codex 검토 뒤): 붙인 글은 저장하지 않는다

- 두 칸(`model_answer` + 최종본)으로 나누지 않는다. `assistant_message`에 **모델이 쓴 답변만** 저장하고, Harness가 붙이는 글은 사용자에게 보낼 때만 붙인다. 새 칸, 저장소 계약·PIA DynamoDB 변경이 없다.
- 두 칸 방식이 더 지켜 주는 것은 "저장본 = 사용자가 본 글" 하나다. PIA에서 저장된 답변을 쓰는 곳은 대화 저장소(`app/dynamodb_conversation_store.py`)뿐이고 화면에 보여주는 곳이 없다. 주문 기록은 pia-broker에 따로 남는다.
- 잃는 것: Summary·Memory 작성 입력에서도 붙인 글이 빠진다. 모델 답변에 준비한 주문의 핵심(종목·수량·가격)이 들어 있어 요약에 남는 내용은 거의 같다. 모델이 과거 알림 문구를 못 보는 점은 두 칸 방식과 같다(Codex 비차단 1).
- 예전에 저장된 Turn(최종본)은 그대로 재생되고, 새 Turn부터 적용된다(Codex 비차단 2).
- 대화 기록을 사용자 화면에 보여줄 일이 생기면 그때 최종본 저장을 다시 검토한다.

## 구현 (Claude)

- `_commit_response`가 저장하는 `assistant_message`를 `final_text`에서 `answer.text`(번호 링크 적용 뒤, 붙이기 전)로 바꿨다. 실행 확정 뒤 답변도 같은 경로다. 사용자 전달과 `ConversationResult.final_text`는 그대로다.
- 테스트: 주문 준비 Turn은 사용자에게 확인 질문이 마지막에 가고, 저장된 답변과 다음 Turn Context의 과거 답변에는 없으며, 확정 정보는 확인 대기 알림에 있다. Memory 실패 알림도 사용자에게만 간다.
- README 3장.
- 측정(Codex 비차단 3): execution 전체 + read 일부(Memory 알림 4·4-1·10, 링크 후속 2→6-1, 15→15-1→15-2).

## Codex 구현 검토 (2026-09-30)

기준 `main fb8d9b2...ebbcb18`의 변경 전체를 계획 변경 절과 현재 저장·재생·확인·Summary/Memory 코드에 대조했다. 로컬 `.venv`에서 orchestrator 테스트 59개와 전체 pytest 170개(추가 subtest 96개)를 다시 실행해 통과했고 `git diff --check`도 통과했다. Ruff는 재실행하지 않았다. 검토 외 코드·README 수정, 병합, AWS 변경, 배포, 실제 모델·공급자 호출은 하지 않았다.

### Blocker

없다. `_commit_response`는 `final_text`를 사용자에게 전달하고 `ConversationResult.final_text`로 돌려주되, 같은 Turn의 저장 `assistant_message`만 출처 번호를 적용한 `answer.text`로 바꿨다(`orchestrator.py` 1118–1155, 1203–1208행). 이전 Turn의 assistant 메시지는 바로 그 저장 칸을 Context에 넣는다(`context.py` 179–194행). 따라서 별도 필드·저장소 변경 없이 보고된 모방 경로를 줄인다. 주문 준비는 전달 뒤에만 `_pending_actions`로 보관하고, 다음 Turn의 `_waiting_note`가 action ID와 종목·방향·수량·지정가를 포함한 요약을 준다(`orchestrator.py` 1137–1143, 1291–1300행; `broker_order.py` 153–180행). “응” 처리와 실행권 확정·Broker 재전송 금지 경계는 바뀌지 않았다. 실행 뒤 모델 답변이 실패하면 도구의 고정 결과가 `answer.text`가 되어 같은 저장 경로를 탄다(1028–1052행). Summary/Memory의 `_turn_data`도 `assistant_message`를 쓰므로 붙인 안내가 빠지는 것은 새 사용자 결정과 일치한다(`openrouter.py` 615–620행).

### Non-blocker

1. **README 계약 한 줄이 반대다.** README 3장 130행은 붙인 글을 저장하지 않는다고 고쳤지만 6.4절 323행은 확인 문장을 “Turn에 저장한다”고 그대로 적었다. 구현에 맞게 “사용자에게 전달하고 Turn에는 저장하지 않는다”로 고치면 된다.
2. **저장되지 않는 것의 범위를 정확히 설명하면 좋다.** 계획 변경 절의 “주문 기록은 Broker에 따로 남는다”는 *실행된 주문*에는 맞지만, 사용자가 확인하지 않은 준비 단계의 질문은 Broker에 제출되지 않는다(`broker_order.py` 153–187행). Bot 재시작 뒤 확인 대기가 사라지는 것은 기존 계약이므로 이번 변경을 막지 않지만, 그때 사용자가 봤던 정확한 질문은 Telegram 외에는 복원할 수 없다는 점을 명시하면 된다.

현재 테스트는 확인 대기 알림이 있는 다음 Turn과 Memory 실패 안내의 전달/미저장을 고정한다. 모델이 같은 Turn에서 자발적으로 확인 질문을 쓰는 것까지 코드로 금지한 것은 아니므로, 계획한 execution 재측정에서 중복 0건을 확인하는 순서가 맞다. 추가 상태·마이그레이션·후처리 규칙을 지금 만들 이유는 없다.

## Codex 구현 검토 반영 (Claude)

- 비차단 1: README 6.4의 "확인 문장을 Turn에 저장한다"를 "사용자에게 보내고 저장하지 않는다, 다음 Turn은 확인 대기 알림으로 받는다"로 고쳤다.
- 비차단 2: 확인하지 않은 주문의 정확한 확인 질문은 이제 대화 저장소에도 남지 않고 사용자의 Telegram 대화에만 남는다. pia-broker에는 실행된 주문만 기록된다. 실행되지 않은 주문이라 지금은 문제로 보지 않는다. 준비 단계의 감사 기록이 필요해지면 그때 따로 검토한다.
