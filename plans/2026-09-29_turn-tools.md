# Turn에 도구 기록 저장 · 답변 뒤 Compaction

브랜치 `claude/turn-tools` (기준 main `c1d7f2b`, B안 병합 후, 0.6.0 미릴리스). 사용자와 대화로 정했다. Hermes Agent 코드(main, 2026-09-29)를 참고했지만 따라가지 않고 단순하고 효율적인 쪽을 골랐다.

## 배경

- 지금 Turn에는 사용자 요청과 최종 답변만 저장한다(README 3). 도구 호출·결과는 그 Turn이 끝나면 사라진다.
- 그래서 "좀 더 찾아봐", "아까 표에서 3위 비중은?" 같은 후속 질문에서 모델은 답변에 적힌 것만 본다. 답변에 없는 세부는 다시 검색하거나 다시 읽어야 한다.
- Hermes는 사용자·도구 호출·도구 결과·답변을 모두 저장하고 매 Turn 그대로 다시 보낸다(`agent/turn_context.py` `build_turn_context`, SQLite `messages` 표). 크기는 Context 50%에서 압축(오래된 도구 결과를 한 줄로 먼저 줄이고, 그다음 가운데를 LLM 요약)으로 관리한다.
- 우리 도구 결과는 이미 작다. 검색은 후보 5건, 본문 추출은 페이지당 요약·근거 약 2,000자이며 페이지 원문(최대 30,000자)은 도구 안에서만 쓰고 버린다. 조사 한 Turn이 보통 1만 자 안팎, 깊은 조사(19번)도 3만 자 정도다.

## 결정 사항 (사용자)

1. Turn에 **질문 + 도구 호출·결과 + 답변**을 저장하고, 다음 Turn Context에 **모든 Turn을 누적**한다. 선택 규칙("직전 Turn만")은 두지 않는다. 누적은 앞부분이 매번 같아 프롬프트 캐시에도 유리하다.
2. 저장된 도구 기록은 다시 가공하지 않고 그대로 넣는다(캐시 유지).
3. 완료된 Turn만 저장한다. 중간에 끊긴 도구 호출은 남지 않는다.
4. **웹 원문은 저장하지 않는다.** 저장되는 것은 도구 결과(요약·근거)뿐이다. 본문 요약은 지금처럼 대화 Context 없이 따로 호출한다(지침·목적·사용자 요청·페이지만).
5. **같은 링크를 다른 목적으로는 다시 읽을 수 있다.** "한 링크는 Turn당 한 번"은 A안에서 Jev의 반복을 막던 규칙이다. 요약이 놓친 것을 다른 목적으로 다시 읽을 수 있어야 한다.
6. **Compaction은 답변을 보낸 뒤, Context가 약 256K 토큰을 넘으면** 한다. 모델 호출 직전 확인은 넘칠 때만 쓰는 안전장치로 남긴다.
7. Compaction 요약과 Memory 작성에는 지금처럼 **질문과 답변만** 준다(도구 결과 제외). 조사의 결론과 출처는 답변에 있다.
8. Hermes에서 가져오지 않는 것: Compaction 전 오래된 도구 결과 한 줄 요약(지금은 불필요, 누적이 커지면 추가), Memory를 세션 시작 때 고정(우리는 다음 Turn부터 바로 반영하는 쪽이 낫다), 주기적 Memory 검토(측정에서 모델이 잘 불렀다, 놓치는 게 보이면 추가), 큰 결과를 파일로 빼기.

## 변경

### 1. 저장

- `CompletedTurn`에 `tool_observations: tuple[ToolObservation, ...] = ()`를 둔다(호출 이름·인자·결과·호출 ID·round). `ConversationStore.append_completed_turn`이 이 값을 받아 저장하고 `load_context`가 돌려준다.
- 저장 대상은 그 Turn의 도구 호출·결과 전부다. `confirm` Turn은 `confirm` 호출과 실행 결과 문장이 들어간다. `HARNESS_NOTE`(확인 대기 목록, 한도 알림)는 저장하지 않는다.
- 저장 한도(`TurnTooLargeError`, 저장소의 바이트 한도)를 넘으면 그 Turn은 도구 기록 없이 질문·답변만 저장한다. 지금보다 나빠지지 않는다.
- `InMemoryConversationStore`와 저장소 계약 테스트(`ConversationStoreContract`)를 맞춘다. PIA의 DynamoDB 저장소는 PIA 연동 때 이 칸을 저장한다(아래 Claude 반영의 저장소 계약, 기본 Turn 상한 256KiB).

### 2. Context 조립

- 이전 Turn마다 `USER_TURN` → 그 Turn의 `TOOL_REQUEST`/`TOOL_RESULT`(round 순서, 호출 ID로 짝) → `ASSISTANT_TURN` 순서로 넣는다. 모델에게는 사용자 → assistant `tool_calls` → `tool` → … → assistant 답변으로 보인다.
- 링크 읽기 경계(`_conversation_urls`)는 이미 `TOOL_RESULT`를 포함하므로, 이전 Turn의 검색 후보·근거 속 링크도 읽을 수 있게 된다(요약·Memory는 계속 제외).
- 호출 ID는 모델이 준 값을 그대로 쓴다. 이전 Turn과 ID가 겹쳐도 각 `tool` 메시지는 바로 앞 assistant 메시지의 호출과 짝이 되므로 문제가 없다고 본다(검토 요청).

### 3. 다시 읽기 규칙

- 읽은 기록을 링크가 아니라 **(링크, 그 밖의 인자)** 로 센다. `url_argument`를 뺀 나머지 인자(예: `goal`)가 다르면 같은 링크를 다시 읽는다. 같은 목적으로는 다시 읽지 않는다. 공통 규칙이라 특정 도구 이름을 보지 않는다.

### 4. 답변 뒤 Compaction

- Turn을 저장한 뒤(같은 commit 안, 실행 확정 상태라 새 메시지는 기다림) 마지막 모델 호출의 사용량(`usage.total_tokens`, 없으면 추정치)이 기준을 넘으면 Compaction을 한다. 기존 `CompactionPolicy.should_compact`(사용량 우선)를 그대로 쓴다.
- 기준 256K는 PIA가 `ModelTokenBudget`으로 정한다. 기본 비율(입력 예산의 80%)을 유지하고 PIA가 `context_limit`을 약 320K로 넘기면 된다(모델 한도 105만보다 작게). harness에 새 설정값은 두지 않는다.
- Compaction 실패는 지금처럼 `compaction_failed`로만 알리고 답변 전달에는 영향이 없다.
- 호출 직전 확인과 도구 없이 다시 조립하는 경로(B안)는 그대로 둔다.

### 5. README

- 3장: Turn에 도구 기록이 들어가고 모든 Turn에 누적된다.
- 4장: 답변 뒤 Compaction, 기준은 PIA의 `context_limit`, 요약에는 질문·답변만.
- 6.2.2: 같은 링크는 다른 목적으로 다시 읽을 수 있다.

## 테스트

- 도구 기록이 저장되고 다음 Turn Context에 사용자 → 호출 → 결과 → 답변 순서로 들어간다(여러 round, 여러 Turn).
- 저장 한도를 넘으면 질문·답변만 저장된다.
- 이전 Turn 도구 결과 속 링크를 읽을 수 있다. Summary·Memory 속 링크는 여전히 못 읽는다.
- 같은 링크: 같은 목적은 다시 읽지 않고 다른 목적은 읽는다.
- 답변 뒤 사용량이 기준을 넘으면 Compaction이 한 번 돌고, 넘지 않으면 돌지 않는다. Compaction 요약 입력에 도구 결과가 없다.
- 저장소 계약 테스트에 도구 기록 왕복을 추가한다.

## 측정

- read 세트의 후속 질문(2-2, 6, 6-1, 15-1~15-3, 16-1, 17-1)이 이전 도구 결과를 써서 다시 검색·읽기가 줄어드는지, 입력 토큰이 얼마나 늘어나는지 본다.

## 검토 요청 시 볼 점

1. 이전 Turn의 도구 호출·결과를 네이티브 메시지로 다시 넣는 순서·호출 ID 처리에 문제가 있는지.
2. 저장 한도 초과 시 도구 기록을 빼고 저장하는 방식이 적절한지.
3. 답변 뒤 Compaction의 위치(commit 안, 저장 뒤)와 사용량 기준이 기존 동시성·실패 처리와 맞는지.
4. (링크, 그 밖의 인자) 기준 다시 읽기가 반복 위험을 만드는지(20회·60초 한도 안에서).
5. 빼도 되는 것, 빠진 것.

## Codex 검토

`main c1d7f2b`의 Context 조립, Turn 저장, Compaction, Memory 경로와 이 계획을 대조했다. 계획 검토만 했으며 코드·README 수정, 병합, AWS 변경, 배포, 실제 모델·공급자 호출은 하지 않았다. 도구 결과를 완료 Turn에만 저장하고 웹 원문은 제외하는 방향은 후속 질문의 재검색을 줄이면서 현재 루프를 유지한다.

### 차단

1. **Compaction의 보호 예산에 새 도구 기록이 빠져 있다(19·27·44행).** 현재 `TokenCompactor._split_turns`는 `_turn_tokens`로 최근 Turns를 남길지 정하며, `_turn_tokens`는 사용자 메시지와 최종 답변만 센다(`compaction.py` 203–224행). 계획대로 예전 Turns의 도구 결과를 Context에 넣어도 이 계산을 그대로 두면, 예를 들어 질문·답변 각 1KB에 도구 결과 80KB가 붙은 Turns 여러 개가 보호 예산 12.5%에 모두 들어간 것으로 판단된다. Context는 256K/입력 한도를 넘는데 `covered`가 비어 Compaction이 아무것도 줄이지 못하고 다음 Turn이 `CONTEXT_OVERFLOW`가 된다. **남길 Turns를 고를 때만** 도구 호출·결과 크기도 세고, Summary 길이 비교와 Summary/Memory LLM 입력은 계획대로 질문·답변만 세면 된다. 큰 도구 Turns가 여러 개 누적된 뒤 답변 후 압축과 다음 요청이 성공하는 테스트를 넣어야 한다.

### 비차단·연동 전 필수

- **PIA 저장소 계약(27–30행).** 현재 Harness `ConversationStore.append_completed_turn`과 PIA DynamoDB 저장소는 도구 기록 인자를 받지 않는다(`persistence.py` 52–61행; 확인한 PIA `app/dynamodb_conversation_store.py` 104–140행). PIA 구현의 기본 Turn 상한도 **256KiB**이며 도구 기록이 아니라 질문·답변 UTF-8 바이트만 계산한다(같은 파일 50, 123–128행). 따라서 “400KB 안에 충분”을 전제로 두지 말고 PIA 연동 작업에 **새 인자·직렬화/복원, 기존 row의 빈 기록 처리, 전체 item 바이트 상한, 명확한 `TurnTooLargeError` 매핑, 동일 turn_id 재생 비교**를 필수 계약으로 적으라. Harness만 먼저 버전을 올리면 모든 Turn 저장이 시그니처 오류로 실패한다. 저장 한도 초과 시 질문·답변만 재시도하는 선택은 적절하며, 두 번째 저장도 실패하면 기존 `PERSISTENCE_FAILED`를 유지한다.
- **네이티브 메시지(34–36행).** 이전 Turn마다 `USER_TURN` → 같은 round의 assistant `tool_calls` 전체 → 각 ID의 `tool` 결과 → `ASSISTANT_TURN`으로 닫으면 현재 `_context_messages`와 [OpenRouter의 도구 응답 형식](https://openrouter.ai/docs/guides/features/tool-calling)이 맞는다. round 번호는 Turn마다 다시 0이므로 이전 Turns 전체의 관측을 하나로 정렬·그룹화하지 말고 **Turn별로** 조립해야 한다. 호출 ID가 서로 다른 완료 Turns에서 중복될 때 공급자가 거부한다는 근거는 확인되지 않았다. 우선 짝을 Turn 안에서 고정하고 중복 ID를 가진 두 Turn의 조립 테스트를 추가하면 되며, 선제적인 ID 재작성은 필요하지 않다.
- **답변 뒤 압축(44–47행).** 현재 `_commit_response`는 전달 뒤 Turn을 저장하고 COMMITTING 동안 새 메시지를 기다리게 한다(`orchestrator.py` 1143–1245행). 같은 경계에서 뒤이어 Compaction하는 것은 가능하다. 다만 압축 예외를 저장 예외와 구분해, **전달·저장까지 성공했다면** `DELIVERED`와 `compaction_failed=True`로 끝내고 pending 메시지를 다시 실행하지 않는 테스트를 명시하라. `openrouter.py`의 `_turn_data`는 이미 질문·답변만 보내므로 Summary·Memory용 별도 도구 결과 제거 단계는 필요 없다.
- **다시 읽기(40행).** `(링크, 나머지 인자)`는 파싱된 객체를 기준으로 비교하면 공백·JSON key 순서만 다른 동일 목적을 같은 요청으로 본다. 목적을 바꿔 반복해도 기존 20회·60초가 상한이므로 별도 의미 비교나 반복 상태는 필요 없다.

## Claude 반영 (Codex 검토 대응)

- **차단: Compaction 보호 예산.** 최근 Turn을 남길지 정하는 크기 계산(`_turn_tokens`, `_split_turns`)에는 그 Turn의 도구 호출·결과도 센다. Summary 길이 비교와 Summary·Memory 작성 입력은 질문·답변만 그대로 둔다(`_turn_data`는 이미 질문·답변만 보냄). 큰 도구 Turn 여러 개가 쌓인 뒤 답변 뒤 Compaction과 다음 요청이 성공하는 테스트를 넣는다.
- **조립은 Turn별로.** round 번호는 Turn마다 0부터라, 이전 Turn들의 기록을 한데 모아 정렬하지 않고 각 Turn 안에서 `USER_TURN` → round별 호출 → 결과 → `ASSISTANT_TURN`으로 닫는다. 호출 ID는 다시 쓰지 않고, 서로 다른 Turn이 같은 ID를 가진 조립 테스트를 넣는다.
- **답변 뒤 Compaction의 실패 구분.** 전달·저장까지 성공했으면 Compaction이 실패해도 `DELIVERED` + `compaction_failed=True`로 끝내고, 대기 메시지를 다시 실행하지 않는다. 테스트로 명시한다.
- **다시 읽기 비교.** (링크, 나머지 인자)를 파싱한 객체로 비교한다(공백·키 순서가 달라도 같은 목적이면 같은 요청).
- **저장 한도 가정 정정.** 400KB가 아니라 PIA 저장소의 기본 Turn 상한 256KiB 기준이다. 한도를 넘으면 질문·답변만으로 한 번 더 저장하고, 그것도 실패하면 기존대로 `PERSISTENCE_FAILED`.
- **PIA 저장소 계약 (연동 때 필수).** Harness 저장소 계약(`append_completed_turn`)에 도구 기록 인자가 생기므로 PIA DynamoDB 저장소도 같은 릴리스에 맞춰야 한다(Harness만 올리면 모든 저장이 실패). PIA 연동 계획에 적을 것: 새 인자 직렬화·복원, 기존 row는 빈 기록으로 읽기, 도구 기록을 포함한 item 전체 바이트 상한, 한도 초과는 `TurnTooLargeError`, 같은 turn_id 재생 비교에 도구 기록 포함.
