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

## 구현 (Claude)

- `ToolObservation`을 `session.py`로 옮기고(`context`에서 다시 내보냄) `CompletedTurn.tool_observations`를 두었다. `ConversationStore.append_completed_turn`은 `tool_observations`를 받고, `validate_loaded_turns`는 그 타입을 확인한다.
- Context 조립은 이전 Turn마다 `USER_TURN` → 그 Turn의 호출·결과(round 순서) → `ASSISTANT_TURN`으로 닫는다. 이번 Turn의 기록 조립과 같은 함수(`_tool_parts`)를 쓴다.
- 저장: 모든 호출·결과(`confirm` Turn은 `confirm` 호출과 실행 결과 포함)를 넘기고, `TurnTooLargeError`면 도구 기록 없이 한 번 더 저장한다. 그것도 실패하면 `PERSISTENCE_FAILED`.
- 답변 뒤 Compaction: 저장이 끝난 뒤 같은 commit 안에서 `should_compact`(마지막 호출의 사용량 우선)가 참일 때만 `compact`. 예외는 `compaction_failed`로만 남고 결과는 `DELIVERED`.
- Compaction의 최근 Turn 선택 크기(`_turn_tokens`)에 도구 기록을 더했다. Summary 입력(`_turn_data`)과 원본 대비 길이 비교는 질문·답변만이다.
- 다시 읽기: 읽은 기록을 (링크, 나머지 인자 JSON[키 정렬])로 둔다.
- `InMemoryConversationStore`는 도구 기록을 저장·반환하고 바이트 한도에 포함하며, 저장소 계약 테스트에 왕복·재생 충돌·한도 초과를 추가했다.
- README 1·3·4·6.0·6.2·6.2.2·6.4를 고쳤다. 앞서 찾은 README 불일치 두 곳(4장 "여전히 초과" 줄, 6.0 타입이 틀린 인자)도 함께 고쳤다.
- unittest 159개 통과(python:3.12-slim).

## 측정 뒤 보완 (2026-09-29, 사용자와 정리)

측정 기록: `tests/manual/records/2026-09-29_turn-tools.md` (`037f471`, read 33개, Compaction 없는 예산 1,050,000).

### 실패

1. **이전 질문에 답했다 (2건).**
   - 13 "크발로닉스테크 최근 뉴스"(가상 회사): 검색 6번(2번 Exa 429) 뒤, 몇 Turn 전 9번 질문(HBM 전략 비교)의 답을 썼다.
   - 19 "기판 관련 ETF": 검색 4번(1번 429) 뒤, 17번 질문(HBM 뉴스)의 답을 썼다. 누적이 없던 이전 측정에서는 페이지 8개를 읽고 좋은 답을 냈다.
   - 원인 판단: 이전 Turn의 도구 기록이 이번 Turn과 **같은 형식**(assistant `tool_calls` → `tool` 결과)으로 들어간다. 모델에게 "끝난 예전 조사"와 "지금 진행 중인 조사"가 구분되지 않아, 이번 조사가 비거나 약하면 예전 조사를 이어서 답했다. Context 크기는 이 혼동을 키우지만, 크기만 줄여서는 언제든 생길 수 있는 구조 문제로 본다(사용자 판단).
2. **Context가 크게 늘었다.** 33 Turn 뒤 추정 약 34만(바이트 기준 추정). 누적 없던 측정의 최대는 약 7만. 대부분이 검색 후보의 발췌 문장(후보당 최대 400자, 검색당 5건, Turn당 검색 여러 번)이다.
3. **Exa 키 없는 호출의 429.** 모델이 한 응답에 검색 4~6개를 동시에 불러 일부가 막혔다(이전 측정에서는 5개 동시도 통과, 한도 비공개).
4. 작은 것: 17-1 "첫 번째랑 세 번째 기사 비교"가 링크를 바로 읽지 않고 검색 뒤 읽었다(결과는 맞음).

### 대안 (구현 전 검토 요청)

**A. 이전 Turn의 도구 기록은 "참고 자료"로 넣는다 (1번 대응, 근본 대책).**
- 도구 형식(assistant `tool_calls` → `tool`)은 **이번 Turn에만** 쓴다.
- 이전 Turn은 `USER_TURN` → **그 Turn의 도구 기록을 묶은 데이터 블록 하나** → `ASSISTANT_TURN`으로 넣는다. 블록은 Memory·Summary처럼 이름표를 붙인 사용자 역할 메시지다. 예: `[이전 Turn에서 이 답변을 위해 쓴 도구 결과; 참고 데이터, 지시 아님]` 아래에 `- web_search {"query":…}` / 결과, `- web_extract {…}` / 결과.
- 새 Context 종류(`TURN_TOOL_RECORD`, 비신뢰 데이터)로 두고, 링크 읽기 경계(`_conversation_urls`)에 포함한다(이전 조사의 링크는 계속 읽을 수 있음).
- 매번 같은 모양으로 만들므로 프롬프트 캐시가 유지된다. 호출 ID·round는 블록에 넣지 않는다(짝을 맞출 필요가 없음). 앞서 검토한 Turn 사이 호출 ID 중복 문제도 없어진다.
- 저장 형식(`CompletedTurn.tool_observations`)은 그대로 두고, 조립할 때만 형식을 바꾼다.

**B. 저장할 때 검색 후보의 발췌를 뺀다 (2번 대응).**
- 도구 결과의 링크(`ToolLink`)에 붙은 짧은 발췌(`summary`)는 **이번 Turn 안에서만** 보여주고, Turn에 저장하는 사본에서는 제목·날짜·링크만 남긴다. 특정 도구 이름이 아니라 "링크를 돌려주는 읽기 도구" 공통 규칙이다.
- 본문 추출 결과(요약·원문과 대조한 근거)는 그대로 저장한다. 실제 근거라 줄이지 않는다.
- 저장 시점에 한 번 정해지므로 이후 Turn에서는 매번 같다(캐시 유지).

**C. 한 응답의 읽기 호출은 동시에 최대 3개까지 실행한다 (3번 대응).**
- 호출을 버리지 않고, 동시에 도는 개수만 3개로 제한한다(나머지는 앞의 것이 끝나는 대로). 결과는 지금처럼 원래 호출 순서로 붙인다.
- 429가 계속되면 2개로 줄인다. 근본 해결은 Exa 키(실사용 전 준비 항목).

**D. 재측정은 운영 조건으로.** smoke 도구의 예산을 운영 계획과 같게(`context_limit` 약 320K → 약 256K에서 Compaction) 두고 read 세트를 다시 돌린다. 1번이 다시 나오지 않는지, 입력 크기가 얼마나 줄었는지, 13·19·17-1·후속 질문을 본다.

### 검토 요청 시 볼 점

1. A(이전 Turn 도구 기록을 참고 데이터 블록으로)가 "이전 질문에 답함"의 원인에 맞는 대책인지, 더 단순하거나 확실한 방법이 있는지. 블록의 역할(사용자 역할 데이터 메시지)과 위치(요청과 답변 사이)가 적절한지.
2. B(저장 사본에서 링크 발췌 제거)가 후속 질문 품질을 크게 해치지 않는지, 저장 시점에 줄이는 방식이 맞는지.
3. C의 동시 실행 제한이 결과 순서·한도(20회·60초)·실행 확정 경계와 맞는지.
4. 빼도 되는 것, 빠진 것.

## Codex 보완 검토

`aa4887c`의 “측정 뒤 보완”과 `037f471` 구현의 Context 조립·도구 결과 포맷·읽기 실행 경로, `tests/manual/records/2026-09-29_turn-tools.md`를 대조했다. 검토만 했고 코드·README 수정, 병합, AWS 변경, 배포, 실제 모델·공급자 호출은 하지 않았다. 13·19번의 오답은 기록으로 확인되지만, 같은 두 Turn에 Exa 429와 큰 Context도 함께 있었으므로 “예전 네이티브 도구 형식”이 **유일한 원인**이라고는 아직 확정할 수 없다.

### 차단 (해당 대안 구현 전)

1. **A의 사용자 역할 블록은 외부 자료를 사용자 발화처럼 보이게 할 수 있다.** 현재 저장된 `TOOL_RESULT`는 API에 `tool` 역할로 전달된다(`openrouter.py`의 `_context_messages` 530–538행). A는 검색 발췌·페이지 근거를 `USER_TURN`과 `ASSISTANT_TURN` 사이의 새 `user` 메시지로 바꾼다(128–133행). 이름표만 붙인 원문 결과에 지시처럼 보이는 텍스트가 있으면, 모델은 그 메시지를 실제 사용자의 추가 요청으로 읽을 수 있다. 이는 현재의 역할 경계보다 약하다([OpenAI Model Spec의 비신뢰 자료 원칙](https://model-spec.openai.com/2025-04-11.html)). **참고 블록의 내용은 인용·JSON 등으로 명확히 비신뢰 자료로 구획하고, 이전 사용자 요청과 답변의 연결이 끊어지지 않는 역할·위치를 정하라.** 필요하면 기존 assistant 답변에 구획된 참고 자료를 붙이는 한 가지 대안과 비교하면 된다. 어느 형식이 13·19번을 실제로 고치는지는 동일한 이전 Context와 검색 실패 결과를 둔 A 단독 재측정으로 확인해야 한다. 이 검증 없이 A를 근본 해결로 확정하면 안 된다.

2. **B의 축소 대상은 현재 저장 형식에 없다.** `ExaWebSearch`는 `ToolLink.summary`를 만들지만(`exa_search.py` 166–198행), `_tool_result_text`가 제목·발췌·링크를 한 `result_text` 문자열로 합친 뒤(`orchestrator.py` 1593–1613행), `CompletedTurn`에는 `ToolObservation.result_text`만 저장한다(`session.py` 47–69행). 저장 시점의 `ToolLink.summary` 필드만 지우는 구현은 불가능하다. B를 하려면 링크 객체가 남아 있는 포맷 단계에서 **실시간 결과와 저장용 결과를 따로 생성**하도록 계약을 적어야 한다. 그 분기가 필요한지는 A와 운영 크기(D)를 먼저 측정한 뒤 결정하는 편이 Simple-first에 맞는다. 저장된 문자열을 사후 패턴으로 깎는 방식은 피하라.

### 비차단

- **C는 임시 버스트 완화이지 429의 보장은 아니다.** `_run_round`는 지금 `asyncio.gather`로 모든 읽기를 함께 실행한다(`orchestrator.py` 843–864행). 3개 제한은 결과를 원래 인덱스로 넣고 같은 deadline을 적용하면 순서·20회 한도·`confirm`의 첫 응답 규칙을 깨지 않는다. 다만 모든 읽기 도구를 늦추고 60초 안에 마칠 조사량을 줄일 수 있다. 실제 Exa 키로도 429가 계속되는지 확인한 뒤 넣거나, 넣더라도 A·D의 품질 측정과 분리하라.
- **D는 필요하지만 A의 효과 판정과 분리해야 한다.** 33 Turn 측정은 `context_limit=1,050,000`이라 Compaction이 없었다(기록 첫머리). 약 320K 설정에서는 오래된 기록이 중간에 요약·삭제되므로 13·19번 결과가 A 없이도 달라질 수 있다. 우선 동일 예산에서 A만 비교하고, 다음에 운영 예산으로 재측정하면 원인을 구분할 수 있다. 17-1의 검색 한 번 추가는 결과가 맞았으므로 별도 규칙을 만들 근거는 약하다.

권장 순서: **A의 안전한 표현 하나를 정해 13·19번만 같은 조건에서 재실행 → D 운영 조건 측정 → Context 증가가 실제 문제일 때 B, 키 사용 후에도 429가 남을 때 C.** 새 라우터·반복 감지·추가 상태는 필요 없다.

## Claude 반영 (보완 검토 대응, 사용자와 정리)

Codex 보완 검토를 받아 사용자와 다시 정했다. A는 참고 블록 대신 **경계선 한 줄**로 바꾸고, B·C는 넣는다. 압축 기준은 비율이 아니라 **256K를 직접** 준다.

### 1. 경계선 (A 대체, 차단 1 대응)

- 과거 대화(Summary·이전 Turn)가 있을 때, 과거 Turn 뒤·현재 사용자 메시지 앞에 harness 시스템 메시지(`HARNESS_NOTE`, 신뢰) 한 줄을 넣는다.
  ```
  The messages above are earlier turns, each already answered.
  ```
- 이전 Turn의 도구 기록은 지금처럼 네이티브 형식(assistant `tool_calls` → `tool`)으로 둔다. 외부 자료는 계속 `tool` 역할이라 사용자 발화로 보일 문제가 없다. 새 Context 종류·저장 형식 변경도 없다.
- 이유: 모델이 현재 요청을 알아볼 단서가 "마지막 사용자 메시지"라는 위치뿐이다. 13·19번은 이번 검색이 429로 비자 과거 조사 쪽으로 끌려갔다. Hermes도 과거를 그대로 다시 넣지만 Context 50%에서 도구 결과를 줄이고 요약해 크기를 작게 유지한다. 우리는 256K까지 과거 조사가 원문으로 남으므로 경계를 명시한다.
- 문장은 최소로 둔다. "현재 요청은 다음 메시지"는 위치로 드러난다. 재측정에서 13·19번이 또 틀리면 문장을 더한다.
- 과거 대화가 없는 첫 Turn에는 넣지 않는다. 매 Turn 같은 문장이라 과거 부분의 캐시는 유지된다(경계선 위치만 한 Turn씩 내려감).

### 2. 압축 기준 256K 직접 지정

- 80% 비율은 `context_limit`이 모델 실제 한도(Nemotron 262K)였을 때 다음 Turn의 여유를 남기려던 규칙이다. luna 한도는 105만이고 256K는 비용·품질로 고른 값이라, 가짜 한도(약 327K)를 넘겨 80%로 맞추는 것은 헷갈리기만 한다.
- `CompactionPolicy(trigger_tokens=256_000, tail_tokens=32_000)`로 바꾼다. `trigger_ratio`·`protected_tail_ratio`는 없앤다.
  - `should_compact`: 사용량(없으면 추정치)이 `trigger_tokens` 이상이면 압축. 지금처럼 사용량 우선.
  - `tail_budget`: `tail_tokens`(압축 뒤 원문으로 남기는 최근 Turn 몫). 지금 기본(262K × 0.125 = 32K)과 같은 크기다.
  - 검증: `0 < tail_tokens < trigger_tokens`, 그리고 `trigger_tokens < token_budget.input_tokens`(아니면 `ValueError`).
- `ModelTokenBudget.context_limit`은 **모델 실제 한도**(PIA는 1,050,000)로 둔다. 모델 호출 직전 크기 확인(넘치면 압축·도구 없이 한 번 더)에만 쓴다.
- smoke 도구는 `context_limit=1_050_000` 그대로, 기본 정책으로 256K에서 압축이 켜진다.

### 3. 검색 후보는 메타데이터만 저장 (B, 차단 2 대응)

- 저장 시점이 아니라 **결과를 글로 만드는 단계**(`_tool_result_text`)에서 두 벌을 만든다.
  - 이번 Turn용(모델에게 보냄): 지금과 같음. `[날짜] 제목 — 발췌 <링크>`.
  - 저장용(`ToolObservation.result_text`): 발췌 없이 `[날짜] 제목 <링크>`. 마지막 안내 문장은 "Candidates are titles only; their bodies are unread."
- `ToolLink.summary`만 빠진다. `observation_text`(페이지 읽기의 요약·원문 확인 인용 포함)는 그대로 저장한다. 특정 도구 이름을 보지 않는 공통 규칙이다.
- 저장된 문자열을 나중에 패턴으로 깎지 않는다. 읽기 결과가 없는 호출(시간 초과·거절 등)은 두 벌이 같다.

### 4. 동시 읽기 최대 3개, 조사 시간 90초 (C)

- `_run_round`의 읽기를 `asyncio.Semaphore(3)`로 감싼다. 호출은 버리지 않고 결과는 원래 인덱스에 넣는다. deadline은 지금처럼 Turn 공통이라, 차례를 기다리다 시간이 다 되면 "Not finished: the research time ran out."이 된다.
- `RESEARCH_TIMEOUT_SECONDS`를 60 → 90으로 올린다(동시 제한으로 늦어지는 몫).
- 20회 한도·`confirm` 첫 응답 규칙·같은 링크 예약은 바뀌지 않는다.

### 5. README

- 4장: 압축 기준 256K 직접 지정, `context_limit`은 모델 실제 한도.
- 3장: 과거 Turn 뒤 경계선, 검색 후보는 제목·날짜·링크만 저장.
- 6.2: 동시 읽기 3개, 조사 시간 90초.

### 6. 측정

- read 세트를 운영 조건(위 기본 정책, 256K 압축)으로 한 번 돌린다. 13·19번이 이번 질문에 답하는지, 입력 크기, 17-1·후속 질문, 429·시간 초과를 본다. 유료라 실행 전 사용자 승인을 받는다.
- Codex 권장(경계선만 같은 조건에서 먼저, 그다음 운영 조건)은 원인 구분에는 낫지만 유료 실행이 한 번 더 든다. 목표가 운영에서 맞게 동작하는 것이라 한 번으로 두려 한다. 13·19번이 또 틀리면 그때 조건을 나눠 원인을 가린다.

### 검토 요청 시 볼 점

1. 경계선 한 줄이 13·19번의 원인에 맞는 대책으로 충분한지, 역할(시스템)·위치·첫 Turn 제외가 적절한지.
2. `CompactionPolicy`를 절대값(`trigger_tokens`, `tail_tokens`)으로 바꾸는 계약·검증이 맞는지, PIA 연동(`context_limit`=모델 실제 한도)에서 빠진 것이 없는지.
3. 저장용 결과를 포맷 단계에서 따로 만드는 계약이 맞는지, 발췌를 빼도 후속 질문(링크 다시 읽기)에 문제가 없는지.
4. 동시 3개 + 90초가 순서·한도·실행 확정 경계와 맞는지.
5. 측정을 운영 조건 한 번으로 두는 것이 괜찮은지.
6. 빼도 되는 것, 빠진 것.

### 구현 (Claude, 보완)

사용자 결정으로 이 절의 Codex 검토는 건너뛰었다. 위 1~5를 그대로 구현했다. 테스트 161개 통과.

- 경계선은 이전 Turn이 있을 때 넣는다. Summary만 있고 Turn이 없는 경우는 없다(Compaction은 가장 최신 Turn을 항상 남긴다).
- 저장용 결과는 `_Round.kept_results`(다를 때만)로 들고, 생성 루프가 `kept_observations`를 따로 쌓아 저장에 쓴다. 이번 Turn의 모델 호출에는 계속 발췌가 든 결과가 들어간다. 실행 확정 경로는 그 응답에 읽기가 없으므로 앞선 저장용 기록 + 이번 응답의 기록을 저장한다.
- 동시 제한은 `MAX_CONCURRENT_READS = 3`의 `asyncio.Semaphore`, 차례 대기도 같은 deadline 안에서 한다.
- 새 테스트: 저장본에서 발췌가 빠지고 다음 Turn에 그 모양으로 다시 들어감, 5개 읽기 중 동시 최대 3개·결과 순서 유지, 첫 Turn에는 경계선 없음, 256K 기준·설정 검증. 테스트 도우미 `_notes`는 이번 Turn의 알림만 보도록 바꿨다(경계선 제외).

## Codex 최종 검토

`35f2498..e2fa1f0`의 전체 구현 diff와 README·계획·오프라인 테스트를 확인했다. 실제 OpenRouter·Exa·Jina 호출은 하지 않았고, 사용자가 보고한 read 33개 재측정 결과는 재실행하지 않았다. 별도 Python 3.12 격리 의존성으로 unittest **161개를 재실행해 모두 통과**했다. 코드·README 수정, 병합, AWS 변경, 배포는 하지 않았다.

### 병합 전 수정 권장

1. **반복 노출되는 출처 중복은 좁은 답변 후처리로 닫는다.** 보고된 6/33 답변에서 모델이 직접 쓴 끝부분 `출처` + URL-only 줄이 `_with_sources`에서 `[n]`으로 바뀌고, Harness 목록이 다시 붙는다(`orchestrator.py` 1566–1611행). 출처 자체를 없애면 검증된 링크와 “두 번째 기사”의 후속 참조를 잃으므로 권장하지 않는다. 모델 문구만 고치는 것으로도 충분하지 않다(이미 “목록을 쓰지 말라”는 지침이 있다). **링크 허용·번호 변환 뒤, Harness 목록을 붙이기 전에** 답변 끝의 정확한 `출처` 제목과 번호-only 줄만 제거하는 제안은 이 재현형에 맞다. 번호 사전은 그대로 두면 삭제된 모델 목록의 검증된 링크도 Harness 목록에 한 번만 남는다. 본문 인용, 코드 블록, 설명이 붙은 `출처` 문단은 보존해야 한다. `본문 인용+모델 목록`, `모델 목록만`, 일반 “출처” 문장, 코드 블록을 테스트로 고정하라. 이 수정은 기존 저장·재생 형식을 바꾸지 않는다.

2. **Summary만 남은 세션에서도 경계선을 넣는다.** `context.py` 217행은 `conversation.turns`가 있을 때만 `EARLIER_TURNS_NOTE`를 추가한다. 계획 233행의 “Summary만 있고 Turn이 없는 경우는 없다”는 `InMemoryConversationStore.load_context`가 만료된 Turn은 빼고 Summary는 유지하는 경우(`testing.py` 109–127행)에 성립하지 않는다. `conversation.summary is not None or conversation.turns`면 경계선을 넣는 한 줄과 Summary-only 테스트면 충분하다. 드문 상태지만 새 규칙의 정확한 범위이므로 출처 수정과 함께 정리하는 편이 좋다.

### 비차단·릴리스 전 확인

- **변경 1–4의 핵심 경로는 맞는다.** 경계선은 이전 Turn 뒤·현재 요청 앞의 신뢰된 `HARNESS_NOTE`이며 첫 Turn에는 없다. `_Round.kept_results`가 검색 발췌를 뺀 저장본만 `kept_observations`로 넘기고, 현재 Turn의 모델 Context에는 발췌 포함 결과를 준다. `confirm` 실행 경로는 이전 저장본과 이번 실행 결과를 결합하며, 모델에는 실제 실행 결과를 준다. 3개 semaphore는 대기까지 공통 deadline 안에 넣고 `gather`의 원래 호출 순서를 유지한다. 읽기 20회와 첫 응답 `confirm` 분기는 바뀌지 않았다.
- **Compaction 정책의 단위를 정확히 표기하라.** `trigger_tokens=256_000`은 동일 모델의 `usage.total_tokens`가 있으면 그 **실제 사용량**을 우선한다(`compaction.py` 55–74행). 반면 지난 기록의 “최대 입력 약 34만”과 이번 보고의 “약 24만”은 UTF-8 바이트 기반 추정치라 서로 비교는 가능해도 256K 실토큰 도달 여부를 증명하지 않는다. `tail_tokens=32_000`도 현재 보수적 바이트 추정으로 Turns를 고른다. 정책 자체는 단순하고 PIA의 실제 `context_limit=1,050,000`과 양립한다. 이번 재측정에 Compaction 실측이 없었다는 점은 병합 차단보다 **0.6.0 릴리스/PIA 연동 전 별도 수용 항목**으로 둔다. 실제 사용량과 압축 발생·실패를 수치로 확인하라.
- **품질 측정의 증거 범위를 분명히 하라.** 보고된 13·19번 정상화와 429 0건은 긍정적이지만 경계선·저장본 축소·동시성·90초가 함께 바뀌었으므로 어느 하나를 단독 원인으로 확정할 수 없다. 현재 저장소의 `tests/manual/records/2026-09-29_turn-tools.md`는 이전 1,050,000 예산 측정만 담고 있다. 재측정의 안전한 요약(시나리오 판정·실제 사용량·압축 횟수)을 기록하고, 재발할 때만 조건을 나눠 측정하면 된다. 더 많은 라우팅 규칙은 지금 필요 없다.
- **작은 지침 불일치:** `openrouter.py`의 `AGENT_INSTRUCTION`에는 “later Turns keep only the answer”가 남아 있으나 이제 완료 Turn에는 도구 기록도 저장한다. 다음 한 줄 정리 때 맞춰 두면 된다. 동작 차단은 아니다.
