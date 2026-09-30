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
