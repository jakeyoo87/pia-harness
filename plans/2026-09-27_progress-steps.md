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

- `answer`, `confirm`: Jev 선택을 검증한 직후, 인자 `"{}"`.
- 인자가 있는 도구와 `web_fetch`: 인자를 만든 뒤, 실행 전. 인자 없는 도구는 선택 직후 `"{}"`.
- 한 번이라도 단계를 보냈으면, Turn이 어떻게 끝나든(성공·실패·취소) 마지막에 `None`을 보낸다. 지금 `COMPLETE`를 보내는 `finally` 자리를 그대로 쓴다.
- 기존과 같이 최선 노력이다. 콜백이 5초 안에 안 끝나거나 실패해도 Turn은 계속된다.

## 비용

- 단계마다 콜백을 기다린다. 사용자 채널 호출 1회(보통 수백 ms)가 단계마다 붙는다. 한 Turn의 단계는 보통 2~3개다.
- 기다리지 않고 던지는 방식은 순서가 뒤섞일 수 있어 택하지 않는다.

## 테스트

- 단계 순서: search → web_fetch → answer 흐름에서 세 단계가 인자와 함께 순서대로 오고 마지막에 `None`.
- 도구 없이 answer만: 단계 1개 + `None`.
- 도구 실패·Jev 실패로 끝나도 `None`이 온다.
- 콜백이 멈추거나 예외를 내도 답변이 나간다(기존 테스트를 새 신호로 바꿈).
- 검색 도구 전용 progress 테스트는 삭제한다.

## DEBUG 로그 삭제

- `openrouter.py`의 DEBUG 로그 두 개(`model.attempt_failed`, `model.retry_scheduled`)와 그 테스트를 지운다. 예전 테스트용이었고, 재시도 끝에 실패하면 결과 상태로 이미 드러난다.
- 이것으로 harness에 로그가 없어진다. PIA는 로그 레벨 설정을 없앤다(사용자 결정, 2026-09-27).

## 문서

- README의 진행 신호 설명을 새 방식으로 바꾼다.
