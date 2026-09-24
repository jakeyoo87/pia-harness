# Bounded Multi-step Read Tools in pia-harness

최초 작성일: 2026-09-24 (Asia/Seoul)

상태: Claude 1차 계획 검토 반영 — 구현·릴리스·PIA 통합·AWS·실모델 호출 없음

브랜치: `pia-harness/codex/multistep-read-tools` (`main` `c1866dc` 기준)

## 이 계획을 읽는 검토자를 위한 현재 상태

- `pia-harness` `main`에는 범용 **단일** host tool 계약이 병합돼 있다. `OpenRouterToolDefinition`을 등록하면 일반 Answer의 strict structured output에 nullable `tool_call`이 추가되고, Orchestrator는 이긴 generation이 `_claim_commit`한 뒤 `execute_tool`을 한 번 호출한다. 도구 결과를 모델에 다시 넣는 호출은 없다. `ToolResult`는 전달용 text와 Turn에 저장할 user·assistant text를 분리한다. `TOOL_FAILED`는 pending을 지운다.
- 이 계약의 출처는 `plans/2026-09-24_generic-tool-lifecycle.md`이며 현재 동작은 `docs/01-architecture.md`, `docs/03-conversation-lifecycle.md`, `docs/04-model-adapter-and-integration.md`, `src/pia_harness/orchestrator.py`, `src/pia_harness/openrouter.py`와 테스트가 기준이다. 옛 계획의 “무제한 loop는 범위 밖” 판단은 **그 당시 단일 도구 MVP**의 범위 결정이지, 이번 제한된 읽기 확장을 금지하는 제품 정책은 아니다.
- 새 도구 계약은 아직 릴리스되지 않았다. 최신 태그는 `v0.3.2`이고 `pia-agent`의 `requirements.txt`/`requirements.lock`도 그 wheel을 pin한다. 새 strict schema의 `tool_call=null`/실제 도구 선택에 대한 승인된 실모델 smoke도 아직 없다.
- `pia-agent`와 `pia-broker`의 `codex/trading-mvp` 브랜치는 현재 계획만 갖고 있다. Broker에는 KIS read-only Connector·Token·계좌 확인이 있지만 PIA용 quote/portfolio 내부 route 및 주문 API는 없다. PIA에는 Harness 새 `execute_tool` 연결이 없다. 각 저장소의 거래 계획은 첫 단계에 모델 결과 재투입과 tool chaining을 제외하므로, 실제 구현 전에 새 읽기 방향과 순서를 맞춰야 한다.
- 사용자가 조건을 한 메시지에 완전히 명시한 직접 주문은 별도 재승인을 반복하지 않고, AI가 먼저 제안하거나 자동 운용하는 주문은 별도 승인을 검토한다는 제품 결정이 이미 세 거래 계획에 반영돼 있다. 다만 `pia-agent` README/AGENTS.md의 옛 일률적 최종 승인 문구는 아직 수정 전이다. 이 Harness 변경이 주문 정책을 대신 결정하거나 승인 우회 근거가 되어서는 안 된다.
- 모델 공급자를 바꾸는 계획은 아니다. 현재 OpenRouter strict structured output 계약을 확장하며, 다른 모델/서비스 채택 여부는 별도 결정으로 남긴다.
- 1차 Claude 검토는 도구 결과로 Context가 넘치면 compaction 없이 종료하자고 제안했다. 사용자는 긴 Context의 약 80%에서 기존 대화를 한 번 요약해 여유를 만드는 더 단순한 동작을 선택했다. 아래 7·8항은 이 후속 제품 결정을 반영하므로 구현 전 검토에서 다시 확인한다.

## 왜 바꾸는가

사용자는 PIA가 미리 정해 둔 질문 유형만 처리하는 앱이 아니라, 질문을 이해하고 필요한 자료를 순서대로 조회·분석하는 Agent이길 원한다. 예를 들어 “시가총액 상위 100개 중 상승률 높은 10개”는 시장 데이터 조회 뒤 다른 조회나 계산이 필요할 수 있다. 모든 정렬·조합 시나리오를 PIA/Broker에 사전 분기 코드로 추가하는 방식은 목표와 맞지 않는다.

이번 단계는 **범용적이지만 제한된 읽기 전용 model→tool→observation→model 흐름**을 Harness에 추가한다. 미래의 요청별 격리 코드 실행도 같은 읽기 도구 계약의 한 host 구현이 될 수 있도록 하되, AWS runner 자체는 만들지 않는다. KIS API adapter는 Broker에서 재사용 가능한 코드로 유지한다. MCP·Skill 도입은 전제가 아니다. 주문은 별도의 명시적 쓰기 경계를 계속 사용한다.

## 저장소별 책임과 선행·후속

| 소유자 | 책임 |
| --- | --- |
| Harness | 모델/도구 왕복, supersede, 횟수·Context budget 상한, tool observation의 비신뢰 경계, commit·전달·Turn 저장. 종목·회원·주문·AWS 개념 없음. |
| PIA | 사용자가 쓸 수 있는 도구 목록, 도구의 read-only/쓰기 분류, member 권한, 인자 검증, Broker client, 모델에 제공할 관측값의 최소화, 사용자 문구와 저장용 redaction. |
| pia-broker | KIS/NH Provider adapter, Credential/Token, 시세·Portfolio 원본, 이후 Order 원본·중복 방지·한도·미확정 차단. LLM과 Telegram을 모름. |

순서는 이 계획 검토 → Harness 구현·fake 검증 → 별도 승인으로 synthetic 실모델 schema smoke → Harness 릴리스 → PIA가 wheel+SHA pin → Broker 읽기 route와 PIA 읽기 도구의 작은 end-to-end 연결이다. 이후에만 분석용 runner 또는 주문을 별도 단계로 확장한다. 세 저장소의 기존 거래 계획은 구현 전에 이 순서에 맞춰 수정한다.

## 제안하는 최소 계약

1. 도구가 등록되지 않은 Orchestrator와 Adapter의 공개 동작·Answer schema·token preflight·Memory/Web Search는 byte-for-byte 가능한 한 그대로 둔다. 기존 단일 `execute_tool` callback과 `ToolResult`도 쓰기/종결 도구 경로로 유지한다.
2. 호스트가 **이름으로 명시한 읽기 전용 도구**에만 제한된 왕복을 허용한다. Harness는 모델의 이름·인자만으로 read-only라고 추측하지 않는다. PIA는 Adapter에 전달한 도구 정의와 Orchestrator의 read-only 이름 목록/실행 callback을 한 곳에서 조립하고, 불일치가 있으면 시작 시 또는 통합 테스트에서 거부한다. API는 기존 public 타입에 꼭 필요한 추가만 한다. 범용 registry/dispatcher 프레임워크는 만들지 않는다.
3. 읽기 callback은 사용자 격리·인자·권한을 검증하고, 모델에 다시 보여줘도 되는 **크기 제한된 observation**만 반환한다. raw HTTP body, Credential, Token, 전체 계좌번호, 로그용 exception은 반환하지 않는다. callback은 취소되어도 안전한 read-only여야 한다. 외부 조회 비용이 재발생할 수는 있지만 거래·파일 쓰기 같은 부작용은 금지한다. Harness가 callback의 무부작용을 자동 증명한다고 주장하지 않는다.
4. 읽기 도구를 고른 generation은 아직 `GENERATING`이다. 도구를 호출하고 같은 generation의 **비영속·비신뢰 Context**에 `(도구 요청, observation)`을 순서대로 추가한 뒤 모델을 다시 호출한다. 요청은 모델이 실제로 고른 이름·인자 JSON을 assistant-role 비신뢰 part로, observation은 `TOOL_RESULT` user-role 비신뢰 part로 렌더한다. provider-native function calling을 쓰지 않으므로 결과만 던져 주지 않는다. 모델이 `tool_call=null`인 최종 Answer를 낼 때까지 이 왕복을 이어간다. 근거 없는 고정 도구 호출 횟수나 강제 최종 답변 round를 추가하지 않는다. 취소·호스트의 전체 deadline·기존 Context hard budget은 그대로 적용한다. Harness 자체의 자동 도구 재시도·동일 호출 캐시·별도 계획기는 없다.
5. 최종 Answer 또는 기존 쓰기 도구 선택이 나오면 **그때** `_claim_commit`한다. supersede/reset이 생성 중인 왕복을 취소하면 이후 모델 호출·commit·전달·Turn 저장을 하지 않는다. callback이 cancellation을 무시해도 generation ID를 매 왕복/claim에서 확인한다. 반면 부작용 도구는 지금처럼 `_claim_commit` **이후 한 번** 실행하고 그 결과를 모델에 다시 넣지 않는다. 이미 commit한 도구는 기존 shield·pending·외부 idempotency 규칙을 따른다.
6. 모델이 읽기 observation을 본 뒤 쓰기 도구를 고를 수는 있어도, Harness는 읽기 결과를 주문 권한으로 승격하지 않는다. PIA의 직접 사용자 지시·원문 근거 검증과 Broker의 주문 한도·Kill Switch·idempotency는 별도의 후속 단계이며 그대로 필요하다. 첫 읽기 구현에는 주문 도구를 등록하지 않는다.
7. 요청 part와 `PromptContextKind.TOOL_RESULT` observation part는 모두 `UNTRUSTED_DATA`다. Renderer는 observation을 “외부 도구 데이터이며 명령이 아님”으로 표시하고 system instruction으로 올리지 않는다. 기존 Memory/Summary/Turn과 구분하고, 모델 출력 초안·요청/observation 쌍은 Turn·Summary·Memory·로그에 저장하지 않는다. 각 모델 호출 전 **현재의 전체 입력 Context**를 실제 payload 기준으로 센다. 조회 왕복에서 입력 예산의 약 80%를 넘으면 기존 `force_review`/Compactor를 사용해 저장된 오래된 대화를 해당 generation에서 **최대 한 번** 요약하고, 같은 요청/observation 쌍으로 다시 조립한다. 최초 사용자 Context overflow에서 이미 요약했다면 이를 그 한 번으로 센다. 대략 1M 입력 예산이라면 800K 부근에서 여유를 만드는 방식이다. 별도의 “결과를 줄여 다시 조회” 모델 루프나 자동 요약/잘라내기는 만들지 않는다. 요약 후에도 hard input budget에 담을 수 없으면 안전하게 종료한다. 도구 미사용 Turn의 정책은 그대로 둔다.
8. 도구가 포함된 최종 답변의 `should_compact`/`compact_after_response` 판단에는 observation이 쌓인 마지막 모델의 usage를 그대로 쓰지 않는다. 위 선행 요약이 있었다면 **요약 후의 지속 Context 기준**, 없었다면 첫 round의 observation 이전 Context 기준을 사용해 같은 세대에서 불필요한 반복 compaction을 피한다. 모델 호출량의 합과 지속 Context의 compaction 기준은 구분한다. 도구 미사용 Turn은 기존 판단을 유지한다.
9. 읽기 도구를 한 번이라도 쓴 Turn의 **최종** 전달 문구와 저장용 user·assistant 문구는 `_claim_commit` 성공 후 전달 전에 호스트가 확정한다(기존 `ToolResult` 재사용). 최종 모델 설명을 사용자에게 보여주더라도 그대로 저장하는 것을 기본값으로 삼지 않는다. 여러 중간 도구 중 하나의 placeholder를 추측하지 말고 한 번의 `finalize_tool_turn(user_key, inputs, tool_calls, answer_text) -> ToolResult`로 정한다. 이 callback은 텍스트 형식화만 하며 외부 부작용을 실행하지 않는다. 일반 답변과 기존 단일 쓰기 도구의 저장 동작은 변경하지 않는다.
10. 도구가 포함된 왕복에서는 `MemoryAction.NONE`을 유지한다. 특히 observation의 문장이 Memory 수정·삭제 지시가 될 수 없다. 첫 모델 호출의 Web Search 선택은 기존처럼 최종 답변으로 끝나며, `tool_call`과 `needs_web_search=true` 동시 선택은 계속 무효다. 읽기 observation 이후의 모델 호출에서는 Server Tool 검색만 비활성화하고, 모델의 도구/최종 답변 선택은 그대로 둔다. 도구 미등록 경로는 바꾸지 않는다.
11. read callback 예외는 `GENERATING`에서 잡아 `TOOL_FAILED`/pending clear로 끝낸다. 동시에 supersede되면 이전 generation의 finish는 무시한다. 알려진 조회 실패는 호스트가 안전한 observation 또는 결정적 실패 문구로 처리하며 Harness는 자동 재조회하지 않는다. 위 한 번의 compaction으로도 hard budget을 넘는 결과, 전체 timeout, 모델 출력 오류만 raw 결과 없이 종료한다. PIA Bot은 대화 작업을 bounded task로 분리하고 `_wait_for_agent_capacity`로 동시성 상한을 둔다. 따라서 모든 사용자가 곧바로 순차 차단되는 구조는 아니지만, 여러 모델 호출이 capacity를 오래 점유할 수 있다. PIA 통합에서 메시지 전체 deadline과 동시성 영향을 측정·결정한다.

공개 API는 우선 `read_tool_names`, `execute_read_tool(user_key, ToolCall, inputs) -> str`, 위의 `finalize_tool_turn` 세 추가점으로 제한한다. observation 전용 dataclass, registry, dispatcher, 새 실행 상태는 도입하지 않는다. 이름 allowlist와 Adapter 도구 정의는 PIA 조립 지점에서 동일한 목록으로 검증한다.

## 읽기 데이터와 후속 코드 실행의 경계

- 시장 공개 데이터도 출처와 관측 시각을 PIA가 유지한다. 모델의 계산/요약은 금융 원본이 아니며, 정확한 수치·순위가 중요한 경우 PIA가 결과를 검증하거나 결정적 계산 도구를 제공한다.
- Portfolio 같은 회원 데이터는 모델 공급자에게 전송될 수 있다. 기존 단일 도구의 “Broker 데이터는 모델에 다시 넣지 않음” 정책을 조용히 뒤집지 않는다. 첫 통합은 공개 시세로 시작하고, Portfolio observation의 허용 필드·마스킹·약관/동의·로그/보존 정책은 PIA에서 별도 결정 후 활성화한다. Key·Token·전체 계좌번호·Provider raw body는 어떤 경우에도 모델에 보내지 않는다.
- 요청별 코드 실행은 후속 PIA/인프라 계획에서 읽기 전용 도구로 제공한다. 일회성 격리 환경, 명시적 입력 데이터, 시간·메모리·출력 제한, Credential/주문 권한 없음이 전제다. Harness에 Python 실행기나 AWS SDK를 넣지 않는다. 결과도 위 observation과 redaction 규칙을 따른다.
- 이번 작업은 KIS MCP/Skill 전환, 사용자별 상시 VM, 주식 순위 API·코드 runner 구현, PIA/Broker 수정, 자동/실계좌 주문, distributed lock, durable tool journal, streaming, provider-native function calling을 포함하지 않는다.
- 첫 버전에서는 “기억해줘 + 시세 알려줘”처럼 Memory 변경과 도구 조회가 한 Turn에 함께 일어나지 않는다. 읽기 observation 뒤 같은 Turn에서 Web Search도 하지 않는다. 이는 침묵 속 누락이 되지 않도록 PIA 안내·평가 항목에 적고, 필요성이 확인되면 별도로 설계한다.

## 구현·검증 순서

1. no-tool schema snapshot 및 기존 단일 도구·Web Search·Memory·supersede 테스트를 먼저 고정한다. 현재 `src/pia_harness/orchestrator.py`의 `_run_generation`→`_claim_commit`→`_commit_response`, `context.py`의 part 검증, `openrouter.py`의 `_answer_schema`·`generate_answer`·`_context_messages`가 주요 변경 지점이다.
2. fake model + fake read tool로 도구 없이 바로 답변·한 번 조회 뒤 답변·여러 번 조회 뒤 답변, 요청→observation 쌍의 순서·role·trust, 도구 뒤 쓰기 선택(쓰기 commit 이전 미호출), 취소·전체 deadline 종료를 시험한다. 도구 이름·인자 오류와 도구 없는 기존 경로도 시험한다.
3. callback 도중·callback 직후·후속 모델 호출 도중 supersede/reset, callback 예외와 supersede 경합, cancellation 무시, claim 경합, 반복 외부 cancellation에서 도구/전달/Turn이 중복되지 않음을 시험한다. 읽기 callback은 GENERATING에서만, 쓰기는 COMMITTING에서만 실행됨을 고정한다.
4. observation prompt injection, 다른 user_key 결과 혼입, provider 원문/비밀 비노출, 입력 예산 80% 전후에서 선행 compaction 한 번과 재조립, 여러 조회 round에서 중복 compaction 없음, 결과 단독으로 hard budget을 넘을 때 안전 종료, 최종 tool Turn의 **지속 Context 기준 compaction**, Memory action/검색 동시 선택, Web Search 이후 종료, 도구 뒤 검색 금지, Turn 양쪽 redaction 및 Summary/Memory 미유입을 시험한다.
5. `docs/01`, `03`, `04`, README, public export 및 fake 통합 예제를 구현에 맞춰 갱신한다. 변경 영역 테스트 후 전체 network-free suite와 wheel build를 한 번 실행한다. 별도 승인된 synthetic 실모델 smoke는 공개 시세 도구 **한 번→최종 답변**부터 확인한다: nullable `tool_call=null`과 이전 요청/observation 쌍의 해석. 검토·릴리스·PIA pin·AWS 배포는 각각 별도 단계다.

## Claude에게 특히 확인받을 질문

1. 읽기 callback을 `GENERATING`에서만 실행하고 쓰기를 기존 `COMMITTING`에 남기는 분리가 supersede/crash/전달 실패 의미를 지키는가? 모델이 관측 뒤 쓰기를 선택하는 경로에 권한 승격이나 race가 남는가?
2. 세 추가 API(read-only 이름 목록, read callback, 최종 redaction)만으로 충분한가? 불필요한 registry/상태/예외 없이 요청/observation 쌍을 렌더할 수 있는가?
3. 조회 왕복의 80% 선행 compaction 한 번과 hard budget 재검사가 기존 Context overflow·supersede·Memory 경계를 지키는가? 최종 compaction이 중복 실행되지 않는가? 요청/결과 쌍이 Turn·Summary·Memory·로그에 새는 경로가 있는가?
4. 기존 두 단계 Web Search와 새 조회 왕복을 결합하지 않는 규칙이 호환성을 지키는가? 실모델 structured-output smoke에서 무엇을 먼저 확인해야 하는가?
5. 고정 호출 횟수 없이 모델의 최종 Answer를 종료 기준으로 삼고, 앱의 전체 deadline과 Context hard budget에 맡기는 것이 PIA의 bounded agent task capacity에서 충분한가? 불필요한 상태·예외·추상화를 추가하지 말고 검토해 달라.

검토는 계획만 대상으로 한다. 코드 수정, 릴리스, PIA/Broker 통합, AWS 변경, 실제 모델/증권사/주문 호출은 이 계획 commit의 범위 밖이다.
