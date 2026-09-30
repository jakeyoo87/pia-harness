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

## 추가 결정 (사용자): 압축 뒤 Context 구역별 크기

Harness는 토큰을 셀 수 없으므로, Harness가 직접 재는 크기는 글자 수로 정한다. 모델이 세어 주는 것(압축 기준, 출력 토큰 한도)만 토큰이다.

| 구역 | 값 | 비고 |
|---|---|---|
| 시스템 지침 + 도구 정의 | 한도 없음(고정) | 지침 약 2,440자(주문 켜면 약 2,840자) + 도구 정의 약 3,830자(주문 켜면 약 4,800자) |
| Memory | 목표 2,000자 / 한도 4,000자 | `MEMORY_TARGET_CHARS` 새로 둠. 작성 LLM에 `target_characters`와 "넘으면 합치거나 덜 중요한 것을 뺀다"를 준다. 저장·스키마·불러오기 검사는 `MEMORY_MAX_CHARS` 4,000 그대로라, 예전에 저장된 Memory나 목표를 조금 넘긴 문서도 막히지 않는다 |
| Summary | 최대 10,000자 | `CompactionPolicy.summary_chars`. 요약 LLM에 `max_characters`를 주고, 넘으면 요약 실패(기존 Summary·원본 유지). 출력 토큰 한도는 같은 수(한 토큰은 한 글자 이상) |
| 최근 Turn 원문 | 가장 최근 1개 + 그 앞 100,000자 | `CompactionPolicy.tail_chars`(`tail_tokens` 대체). 가장 최근 Turn은 몫에 세지 않고 항상 남긴다. 도구 기록까지 글자 수로 센다 |
| 압축 기준 | 실제 토큰 256,000 | 유지(384K·512K도 검토했으나 비용·속도·긴 Context 품질 때문에 재측정 뒤 판단) |

- Summary가 원본보다 짧은지도 글자 수로 비교한다(요약 대상 Turns의 도구 기록 포함 글자 수).
- 압축 직후 Context는 기준의 약 20~30%(약 5만~7.5만 토큰, 추정)에서 다시 시작한다. 재측정에서 실제 사용량으로 확인한다.
- 테스트: 기본값, 설정 검증, 요약 글자 한도 초과 거절(요청에 한도 전달, 원본 유지), 출력 토큰 한도 초과 거절, 최근 Turn 선택(가장 최근 1개는 항상).

## 검토 요청 시 볼 점 (추가)

4. 글자 수 기준(최근 Turns 몫, Summary 길이·원본 비교)과 토큰 기준(압축 기준, 출력 한도)의 구분이 맞는지, Summary 출력 토큰 한도를 글자 한도와 같게 두는 것이 충분한지.
5. Memory 목표(2,000)와 한도(4,000)를 나눈 방식이 단순하고 안전한지.

## Codex 검토 (2026-09-30)

기준 `main f69594c...cb36dab`의 코드·테스트·README를 대조했다. 코드·README 수정, 병합, AWS 변경, 배포, 실제 모델·공급자 호출은 하지 않았다. 로컬 `.venv`에서 관련 테스트 102개와 전체 pytest 170개(추가 subtest 96개)가 통과했고 `git diff --check`도 통과했다. Ruff는 재실행하지 않았다.

### Blocker

없다. `_assemble_context`는 메모리·완료 Turns·이번 도구 결과를 조립할 뿐 압축이나 모델 한도 판정을 하지 않고, `_run_generation`은 기존 생성 취소·claim 경계를 유지한다(`orchestrator.py` 519–605, 900–944행). 답변 전달·저장 뒤에만 `usage`를 우선해 Compaction을 판단한다(1178–1205행). `confirm` 뒤 모델 답변 오류는 도구의 고정 결과 문장으로 돌아가므로 주문을 다시 실행하지 않는다(1028–1056행). 새 `tail_chars`는 가장 최근 Turn을 무조건 보존하면서 그 앞의 연속된 Turns를 도구 기록까지 글자 수로 센다(`compaction.py` 202–230행). Summary가 길거나 작성에 실패하면 기존 Summary·Turns를 유지한다. Memory의 2,000자 목표는 작성 지침이고 4,000자 저장 한도는 그대로라 기존 문서도 막지 않는다.

모델 한도 초과 시 모델 오류로 끝나며 pre-call 압축·도구 없는 재조립·`CONTEXT_OVERFLOW`로 복구하지 않는 것은 이 브랜치의 명시적인 사용자 결정이다. 압축이 계속 실패해 대화가 1.05M 한도에 도달하면 답변이 없어서 다시 압축할 수 없고, 실패한 입력은 기존 `GENERATION_FAILED` 의미대로 pending에 남는다(`orchestrator.py` 670–682행). 이 한계는 README에 알려져 있고 이미 앞선 `compaction_failed` 신호가 있으므로, 새 예외 상태를 요구하지 않는다.

### Non-blocker

1. `compaction.py` 146–153행의 “한 토큰은 한 글자 이상이므로 `max_tokens=summary_chars`면 충분”이라는 보장은 틀리다. 모델의 토큰이 한국어 글자 일부를 나타낼 수 있고, 응답 JSON 형식에도 토큰이 든다. 10,000자에 가까운 Summary는 10,000 출력 토큰에서 잘려 작성 실패가 날 수 있다(`openrouter.py` 318–340행). 실패 시 원본을 보존하므로 데이터 손실이나 현재 테스트 실패는 없지만, 코드 주석·README 4장의 단정은 고쳐야 한다. 실제 Summary 길이·출력 사용량을 측정한 뒤 한도가 부족할 때만 조정하면 된다. 별도 재시도나 상태는 필요 없다.

### 연계 메모

PIA는 아직 구형 Harness를 pin하므로 이 브랜치만으로 Bot이 깨지지는 않는다. PIA가 새 wheel로 올릴 때는 `app/core.py`의 제거된 `OrchestratorStatus.CONTEXT_OVERFLOW` 분기를 함께 지워야 한다. 이번 검토는 PIA를 수정하지 않았다.
