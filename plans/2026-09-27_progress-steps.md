# Jev 선택을 단계마다 알리는 진행 신호 계획

## 배경

- 지금 진행 신호는 `ConversationProgress` 두 값뿐이다: 검색이 처음 시작될 때 `WEB_SEARCH_STARTED`, 끝날 때 `COMPLETE`. 검색 도구에만 붙는다(`ReadToolDefinition.progress`).
- 사용자(운영자)가 실제 대화에서 Jev가 무엇을 골랐는지 보고 싶어 한다. harness에는 로그가 없어서 지금은 볼 방법이 없다.
- 로그 대신 사용자 채널에 보여주기로 했다(사용자 결정, 2026-09-27). 검색어 같은 내용은 사용자 본인 대화에만 보이고 로그에는 남지 않는다.

## 결정

- Jev가 고를 때마다 진행 신호를 보낸다. 신호에는 고른 선택지와 그 인자가 들어간다.
- 무엇을 어떻게 보여줄지(문구, 남길지 지울지)는 사용자 채널 쪽이 정한다. harness는 신호만 준다(README 원칙 유지).
- 검색 전용 신호(`WEB_SEARCH_STARTED`)와 도구별 `progress` 필드는 없앤다. 모든 선택이 같은 방식으로 알려진다.

## 인터페이스

```python
@dataclass(frozen=True, slots=True)
class ConversationStep:
    next_action: str      # "answer", "confirm", "web_fetch", 도구 이름
    arguments_json: str   # 인자 없는 선택은 "{}"

progress: Callable[[str, str, ConversationStep | None], Awaitable[None]]
#                  user_key, progress_id(turn_id), step (None = Turn 끝)
```

- `ConversationProgress` enum, `ReadToolDefinition.progress`는 삭제한다. 공개 API가 바뀌므로 버전은 0.5.0.

## 보내는 시점

- `answer`, `confirm`: Jev 선택을 검증한 직후, 인자 `"{}"`. 단계는 **Jev가 고른 것**이지 실행 결과가 아니다. `confirm` 단계 뒤에도 새 메시지가 오면 실행권(`_claim_commit`)을 못 얻어 주문이 나가지 않을 수 있다.
- 인자가 있는 도구와 `web_fetch`: 인자를 만든 뒤, 실행 전. 인자 없는 도구는 선택 직후 `"{}"`.
- 한 번이라도 단계를 보냈으면, Turn이 어떻게 끝나든(성공·실패·취소) 마지막에 `None`을 보낸다. 지금 `COMPLETE`를 보내는 `finally` 자리를 그대로 쓴다.
- 기존과 같이 최선 노력이다. 콜백이 5초 안에 안 끝나거나 실패해도 Turn은 계속된다.

## 비용

- 단계마다 콜백을 기다린다. 사용자 채널 호출 1회(보통 수백 ms)가 단계마다 붙는다. 한 Turn의 단계는 보통 2~3개다.
- 기다리지 않고 던지는 방식은 순서가 뒤섞일 수 있어 택하지 않는다.

## 테스트

- 단계 순서: search → web_fetch → answer 흐름에서 세 단계가 인자와 함께 순서대로 오고 마지막에 `None`.
- 도구 없이 answer만: 단계 1개 + `None`.
- 단계를 보낸 뒤 도구 실패·Jev 실패로 끝나도 마지막에 `None`이 온다. 첫 Jev 호출부터 실패하면 보낸 단계가 없으므로 `None`도 없다.
- `confirm` 콜백을 기다리는 동안 새 메시지가 오면 주문 실행은 0회다.
- 콜백이 멈추거나 예외를 내도 답변이 나간다(기존 테스트를 새 신호로 바꿈).
- 검색 도구 전용 progress 테스트는 삭제한다.

## DEBUG 로그 삭제

- `openrouter.py`의 DEBUG 로그 두 개(`model.attempt_failed`, `model.retry_scheduled`)와 그 테스트를 지운다. 예전 테스트용이었고, 재시도 끝에 실패하면 결과 상태로 이미 드러난다.
- 이것으로 harness에 로그가 없어진다. PIA는 로그 레벨 설정을 없앤다(사용자 결정, 2026-09-27).

## 문서

- README의 진행 신호 설명을 새 방식으로 바꾼다.

## Codex 계획 검토

현재 `orchestrator.py`의 Jev 선택·인자 생성·실행 확정 경로와 PIA의 진행 콜백을 대조했다. 계획만 검토했으며 코드·README, 릴리스, AWS는 변경하지 않았다.

### Blocker (두 계획의 계약 불일치)

1. `confirm`은 Jev 선택 직후에도 아직 **실행 확정이 아니다**. 현재 코드에서는 그 뒤 `_claim_commit`이 성공해야 주문 실행으로 넘어간다(`orchestrator.py:489-498`). 진행 콜백을 기다리는 사이 새 메시지가 오면 기존 generation은 취소되고 주문은 나가지 않는다(`orchestrator.py:378-390`). 따라서 이 신호는 "Jev가 confirm을 선택함"으로만 계약하고, PIA가 이 시점에 "주문 확정"이라고 표시하지 않도록 두 계획을 맞춰야 한다. 실행 확정 표시가 꼭 필요하다면 `_claim_commit` 성공 뒤, 취소되지 않는 owned commit 안에서만 보내야 한다. 선택을 보여주는 것이 목적이므로 **표시 문구만 고치는 쪽**이 더 단순하다. confirm 콜백 대기 중 새 입력으로 supersede되는 테스트에서 주문 호출 0회를 확인한다.

### Non-blocker

- 45행 테스트 항목은 첫 Jev 호출 자체가 실패하는 경우와, 앞선 단계가 전송된 뒤 다음 Jev 호출이 실패하는 경우를 구분해야 한다. 33행 계약상 전자는 전송한 단계가 없어 `None`도 없고, 후자만 마지막 `None`이 온다. 테스트 문장만 정확히 하면 된다.
- 나머지 단계 순서, 인자 생성 뒤·실행 전 통지, 5초 best-effort 콜백, 완료 신호는 현재 구조와 맞는다. 별도 큐나 상태를 추가할 이유는 없다.

### 검토 반영 (Claude, 사용자 확인 2026-09-27)

- Blocker `confirm`: 계약을 "Jev가 confirm을 고름"으로 정했다(보내는 시점 절). 알리는 시점은 그대로 두고, PIA 문구를 `확정 선택`으로 바꾼다. 주문 결과는 기존 답변이 알린다. supersede 테스트를 추가한다.
- Non-blocker 테스트 문구: 첫 Jev 실패(`None` 없음)와 단계 뒤 실패(`None` 있음)를 나눠 적었다.

## Codex 구현 검토

대상 `main 64b2278...cdf8501`의 전체 구현 diff를 계획·PIA 연결 경로에 대조했다. 코드·README 수정, 병합, 릴리스, 외부 API 호출은 하지 않았다. 진행 관련 targeted unittest 79개가 통과했다.

### Blocker

- 없음.

### Non-blocker

- 없음. `answer`·`confirm`은 Jev 선택 검증 뒤, 인자 있는 도구는 인자 검증 뒤 실행 전에 통지한다(`orchestrator.py:492-554`). `confirm`은 `_claim_commit` 전에 보낸 **선택 신호**이며, 콜백 대기 중 supersede되면 주문 실행 0회인 테스트가 있다(`tests/test_orchestrator.py:1655`). 단계가 있던 generation은 성공·실패·취소 후 `finally`에서 `None`을 보내고, 콜백 실패·5초 제한은 기존 best-effort 경계로 처리한다(`orchestrator.py:726-730`, `807-822`).
- `ConversationProgress`와 도구별 `progress` 필드, DEBUG 모델 재시도 로그가 제거됐고 공개 export·테스트도 맞춰졌다. 추가 상태나 추상화를 줄일 곳은 보이지 않는다. PIA가 아직 0.4.1 wheel을 고정한 것은 별도 0.5.0 릴리스·lock 갱신 단계의 선행 조건이지 이 구현의 결함은 아니다.
