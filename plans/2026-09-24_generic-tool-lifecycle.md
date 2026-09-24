# Generic Model Tool Lifecycle Plan

최초 작성일: 2026-09-24 (Asia/Seoul)

상태: 세 저장소 공동 계획 검토 대기 — 구현·릴리스·AWS·실모델 호출 없음

브랜치: `pia-harness/codex/trading-tool-lifecycle` (`main` 284a2c3 기준)
연계: `pia-agent/plans/2026-09-24_trading-mvp.md`, `pia-broker/plans/2026-09-24_trading-mvp.md`

## 책임과 목표

Harness는 모델 생성, Context/Memory, 재시도, supersede, commit, 전달, Turn 저장을 이미 소유한다. 같은 일반 Answer 모델이 답변 또는 호스트가 등록한 도구 한 개를 선택할 수 있게 하고, 도구의 실행 생명주기도 Harness가 관리한다. 주문을 위한 별도 intent LLM, 단어별 라우터, 범용 무제한 agent loop를 만들지 않는다.

Harness는 도구 이름·설명·입력 schema와 호출 callback을 **호스트로부터 주입**받는다. Harness 안에는 PIA, 증권사, 회원, Risk Check, 종목, 주문, IAM, DB, Telegram 개념이 없다. PIA가 사용 가능한 도구·입력 검증·회원 정책·실제 Broker client를 소유하고 Broker가 금융 실행을 소유한다.

## 최소 공개 계약

- 기본값은 도구 없음이며 기존 Answer JSON schema, `GeneratedAnswer`, `submit`, 일반 Memory/Web Search/전달 동작을 변경하지 않는다.
- 호스트가 도구를 설정한 경우 Adapter의 **일반 Answer 한 번**이 `answer + memory_action + optional tool_call`을 strict structured output으로 반환한다. 도구는 하나만 선택한다. 도구 없는 답변은 기존 경로를 사용한다.
- 호스트가 허용한 이름과 간단한 argument schema/설명을 trusted system instruction에 넣는다. Provider schema는 과도한 조건부 분기를 피하는 고정 envelope(`name`, bounded `arguments_json` 문자열 또는 null)로 두고, Harness는 envelope와 허용 이름을 검증한다. 호스트가 arguments JSON·schema·의미·권한을 재검증한다. 실제 schema 표현과 토큰 예산은 구현 전 fake Adapter 테스트로 확인한다.
- tool_call이 있으면 `MemoryAction.NONE`만 허용한다. `needs_web_search=true`와 tool_call이 동시에 나오면 우선순위를 추정하지 않고 출력 검증 실패로 처리한다. 도구의 유무와 결과는 모델의 자연어 성공 주장으로 판정하지 않는다. 도구 있는 `answer` 초안은 사용자에게 전달·저장하지 않는다.
- `submit(source_id=...)`는 선택적 opaque 입력 식별자를 각 pending `ConversationInput`에 보존한다. 현재의 sortable timestamp+UUID `turn_id`와 `created_at` 검증은 그대로 유지한다. source ID는 모델 프롬프트/Turn/Memory에 넣지 않으며, 호스트 callback의 입력 metadata로만 전달한다. 호스트가 쓰기 도구에 안정 source ID를 요구할 수 있다.
- 호스트가 generation마다 주는 선택적 짧은 `ephemeral_tool_context(user_key, inputs)`를 Context 예산에 포함한다. 이 provider는 GENERATING 중 취소될 수 있으므로 **읽기 전용**이며 초안을 소비·갱신하지 않는다. 값은 비영속·비신뢰 데이터로 별도 표시하고 trusted SYSTEM이나 tool 지침으로 승격하지 않는다. 모델에게 현재 진행 중인 앱 도구 초안을 알려주지만 Turn·Summary·Memory에는 저장하지 않는다. 값 부재 시 기존 Context 구조가 정확히 유지된다.
- 호스트의 async `execute_tool(user_key, tool_call, inputs)`는 한 번의 commit 시도에서 한 번 호출한다. `inputs`는 결합된 각 사용자 입력의 원문·source ID·수신 시각을 순서대로 담는다. Harness는 raw 값이나 도구 인자를 로그에 남기지 않는다.
- callback 결과는 `delivery_text`, `persisted_user_text`, `persisted_assistant_text`를 분리한다. 일반 답변은 기존 원문 Turn 계약을 유지하고 도구 Turn만 호스트가 두 저장 문구를 치환한다. 세 문구는 비어 있지 않아야 하며 Harness는 도구 결과 원문을 Memory/Compaction/Turn에 자동 추가하지 않는다.

## 실행 순서와 실패 의미

```text
Context 조립 → 일반 Answer/도구 선택 → 모델 출력 검증
→ _claim_commit 성공(이후 supersede 불가)
→ 호스트 execute_tool 1회 → 호스트가 제공한 결과 문구 검증
→ 채널 전달 → sanitized completed Turn 저장
```

- GENERATING 중 supersede되거나 출력 검증이 실패한 세대는 도구를 전혀 호출하지 않는다. `_deliver`의 text-only 포트에 숨은 action을 밀어 넣지 않고, `generate_answer` wrapper에서도 부작용을 실행하지 않는다.
- Harness의 in-process commit lock은 같은 회원의 두 도구 호출 순서를 보장할 뿐 외부 부작용의 exactly-once를 보장하지 않는다. 호출 후 프로세스 중단·전달 실패·재전달에서는 callback이 다시 실행될 수 있다. 쓰기 도구는 호스트가 stable source ID로 외부 idempotency를 구현해야 한다.
- 도구 callback의 알려진 조회 실패·외부 결과 미확정(쓰기 timeout·5xx·연결 오류 포함)은 호스트가 안전한 `ToolResult`로 반환한다. 예기치 않은 프로그래밍 오류만 `TOOL_FAILED`(이름은 구현 시 확정)로 반환한다. 이때 `clear_pending=True`, 전달·Turn 저장 없음으로 다음 입력의 조용한 자동 재실행을 막는다. 호출 앱은 결정적 실패 안내와 별도 상태조회 경로를 제공하며 Harness는 원문/인자를 로그에 남기지 않는다. `ConversationAbandoned`는 기존 ABANDONED 동작을 유지한다.
- 전달 실패 시 기존처럼 pending은 재시도 가능하지만, 도구가 이미 성공했다는 사실을 Harness가 추정하지 않는다. 저장 실패는 전달 성공 후 재전달하지 않는 기존 원칙을 유지한다. reset/shutdown/`ConversationAbandoned`도 기존 의미를 지킨다.
- `execute_tool`은 한 회원의 commit lock 안에서 실행되므로 앱 callback이 짧은 전체 timeout을 강제한다. Harness는 부작용 호출을 임의로 재시도하거나 취소 후 재실행하지 않는다.
- 도구 결과를 LLM에 다시 넣어 설명시키는 두 번째 호출, 도구 chaining, stream, durable tool-call journal, provider native function-calling, 검색 결과의 도구 권한 승격은 이번 범위 밖이다. 사용자에게 보이는 결과 문구는 호스트가 결정한다.

## 검증·릴리스 순서

1. `GeneratedAnswer`/Adapter의 옵션 없는 기존 schema snapshot과 token preflight 비회귀를 먼저 고정한다.
2. fake 모델·fake tool로 정상 read/write, schema 거부, tool+Web Search 동시 요청, supersede, commit 중 새 입력, callback 프로그래밍 오류 시 `TOOL_FAILED`/pending clear, 알려진 외부 timeout의 정상 ToolResult, 프로세스 재생성에 준하는 동일 source ID 재호출, 전달 실패, Turn 저장 실패, 두 방향 redaction, ephemeral context의 읽기 전용·비신뢰·비영속·예산 계산, Memory/Compaction 비유입을 시험한다. 실제 증권사·AWS·사용자 데이터는 쓰지 않는다.
3. `docs/01`, `03`, `04`, README와 public export를 갱신한다. 기존 앱에 미등록이면 동작이 바뀌지 않아야 한다.
4. 독립 검토 후 범용 Harness 버전을 릴리스하고, PIA가 정확한 wheel+SHA로 pin한 뒤 통합한다. 실모델 smoke·배포는 각각 별도 승인이다.

## Claude 검토 질문

- 이 계약이 정말 Harness 범용 생명주기 책임에만 머무르는가? 도구 이름/인자·결과의 도메인 처리가 새지 않는가?
- `_claim_commit` 이후 execute 시점과 at-least-once + host idempotency가 supersede·delivery failure·crash에서 안전한가? `TOOL_FAILED`의 pending clear와 앱 실패 안내가 기존 Orchestrator 결과와 충돌하는가?
- ephemeral tool context가 기존 trust boundary와 token budget을 지키면서 다중 턴 도구 초안을 모델에 전달하는 최소 방법인가?
- 사용자·assistant 양쪽 persisted text override가 Memory/Compaction으로 민감값이 가는 경로를 닫는가?
- OpenRouter strict schema, 기존 MemoryAction/Web Search, token budget에 대한 옵션 없는 비회귀가 가능한가? MVP에 과한 추상화는 무엇인가?
