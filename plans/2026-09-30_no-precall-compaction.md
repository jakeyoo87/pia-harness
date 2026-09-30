# 모델 호출 직전 Compaction 제거

브랜치 `claude/no-precall-compaction` (기준 main `f69594c`, 0.6.0 미릴리스). 사용자와 대화로 정했다.

## 문제

README 대조(`plans/2026-09-30_naver-news.md` "Codex 검토 반영")에서 찾았다. Compaction 확인이 두 곳에 있었다.

| 시점 | 크기 | 기준 |
|---|---|---|
| ① 답변을 보낸 뒤 (`_commit_response`) | 모델이 알려 준 `usage.total_tokens` (없을 때만 추정) | `trigger_tokens` 256K |
| ② 매 모델 호출 직전 (`_assemble_with_overflow`) | UTF-8 바이트 추정(`conservative_token_estimate`) | 같은 256K |

②가 두 문제를 만들었다.
1. 답변 전에 압축된다: Turn 도중 모델 호출 직전에 요약 LLM을 불러 사용자가 기다린다. 답변 뒤 압축(①)을 둔 이유와 반대다.
2. 너무 일찍 압축된다: 한국어는 바이트가 실제 토큰의 두세 배라 실제 약 100K 안팎에서 걸린다. 2026-09-30 source-links 측정의 압축(19번 뒤, 추정 약 25만)이 이 경로로 보인다.

## 결정 (사용자)

- ②의 Compaction을 없앤다. 직전 확인은 요청이 모델 한도(입력 예산)에 드는지만 본다. 넘치면 지금처럼 도구 없이 한 번 더 조립해 답하고, 그래도 넘치면 `CONTEXT_OVERFLOW`.
- 256K 판단은 ①(실제 사용량)에만 둔다.
- "넘칠 때만 한 번 압축" 안전장치도 두지 않는다. 답변 뒤 Compaction이 계속 실패해 대화가 모델 한도(105만)까지 커지는 경우는 README에 알려진 한계로 적는다(그 전에 `compaction_failed`가 여러 번 보고된다).

## 변경

- `orchestrator.py`: `_assemble_with_overflow`(Compaction·재조립·실패 표시)를 `_assemble_context`(조립, 넘치면 None)로 바꿨다. 생성 루프의 `compacted` 상태와 `compact` 인자, `_answer_after_execution`의 쓰지 않게 된 `state`·`generation_id` 인자를 없앴다.
- 테스트: 넘치면 Compaction 없이 `CONTEXT_OVERFLOW`, Compaction은 답변 뒤에만(모델 호출 전 확인·압축 0회), 호출 직전 압축의 중단 테스트 삭제(해당 경로 없음), 나머지 넘침 테스트에서 쓰지 않는 설정 제거.
- README 1·3·4장: 직전 확인은 넘침만, 압축은 답변 뒤 실제 사용량으로, 최근 Turns 몫 32K는 바이트 추정, 알려진 한계.

## 검토 요청 시 볼 점

1. ② 제거 뒤 넘침 경로(도구 없이 재조립 → `CONTEXT_OVERFLOW`, 대기 메시지 처리, 실행 확정 뒤 답변 경로)가 맞는지.
2. 알려진 한계를 받아들이는 판단이 괜찮은지, 더 단순하게 막을 방법이 있는지.
3. 빼도 되는 것, 빠진 것.

## 추가 결정 (사용자): 호출 직전 크기 확인도 없앤다

- 256K에서 압축하면 모델 한도(105만)까지 약 75%가 남는다. 답변 뒤 확인만으로 충분하다.
- 직전 크기 확인도 바이트 추정을 입력 예산(105만)과 비교해, 실제 약 25만~35만 토큰에서 가짜 넘침(도구를 빼고 답하거나 `CONTEXT_OVERFLOW`)을 낼 수 있었다. 256K 근처의 긴 대화에서 조사가 끊길 수 있다.
- 없앤 것: 조립 때의 예산 검사와 `ContextBudgetExceeded`(공개 이름), 도구를 빼고 다시 조립하는 경로, `OrchestratorStatus.CONTEXT_OVERFLOW`, 조사 한도 알림의 "input size". 모델 한도를 넘는 요청은 모델이 거절해 일반 모델 오류로 끝난다.
- 남긴 것: 조립 결과의 추정치(`estimated_input_tokens`)와 `input_budget`(관찰용), `CompactionPolicy`의 `trigger_tokens < 입력 예산` 검증.
- PIA는 아직 연동 전이라 공개 이름 제거의 영향이 없다. 연동 때 `CONTEXT_OVERFLOW` 처리를 두지 않는다.
