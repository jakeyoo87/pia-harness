# Generic Multi-step Tool Context Plan

최초 작성일: 2026-09-24 (Asia/Seoul)

상태: 사용자 단순화 결정 반영 계획 — 구현·릴리스·PIA 통합·AWS·실모델 호출 없음

브랜치: `pia-harness/codex/multistep-read-tools` (`main` `c1866dc` 기준)

## 현재 상태와 변경 이유

`pia-harness` main은 모델 Answer가 선택한 host tool을 `_claim_commit` 뒤 한 번 호출할 수 있다. `OpenRouterToolDefinition`, `ToolCall`, `ToolResult`가 있지만 도구 결과를 모델에게 다시 보여주지는 않는다. 새 계약은 아직 릴리스되지 않았고 pia-agent는 `v0.3.2` wheel을 사용한다. pia-agent·pia-broker의 `codex/trading-mvp` 브랜치도 구현이 아닌 계획 단계다.

사용자는 미리 코딩한 질문 유형에만 답하는 앱보다, 모델이 필요한 도구를 사용하고 결과를 보며 다시 판단하는 Agent를 원한다. Harness는 이 **범용 대화·Context 생명주기**만 맡는다. 금융정보 분류, 권한, Credential, Broker 응답 형식과 주문 정책은 도구를 제공하는 host와 pia-broker 책임이다. Harness에 별도 비밀 탐지·redaction 엔진, 도메인별 예외 처리, 금융 용어를 넣지 않는다.

기존 `plans/2026-09-24_generic-tool-lifecycle.md`와 이 계획의 이전 커밋에는 도구 결과를 Turn 종료 때 버리고 별도 `finalize_tool_turn`으로 저장 문구를 치환하는 설계가 있다. 사용자는 그 분리가 대화의 연속성을 떨어뜨리고 구현을 과하게 만든다고 판단했다. 이 문서가 그 부분을 대체한다. 기존 단일 도구 API와 no-tool 동작은 호환을 유지한다.

## 목표 Flow

```text
사용자 요청 → Context 구성 → 모델 판단
                           ├─ 최종 답변 → 전달·완료 Turn 저장
                           ├─ 읽기 도구 → 결과를 Context에 추가 → 모델 재판단
                           └─ 실행 도구 → commit 소유권 확정 → 한 번 실행 → 결과 전달·저장
```

읽기 왕복은 모델이 최종 Answer를 낼 때까지 이어진다. 근거 없는 고정 호출 횟수는 없다. 기존 Context hard budget, 취소와 host의 전체 deadline이 적용된다. Harness가 도구를 자동 재시도하거나 별도 계획기를 만들지는 않는다.

## 최소 범용 계약

1. Host는 사용할 도구의 이름·설명·인자 schema와 실행 callback을 제공한다. Host가 이름으로 읽기 도구를 명시하면 Harness는 그 도구만 `GENERATING` 중 호출한다. 결과는 host가 준 문자열 그대로 **도구 결과 데이터**로 취급한다. Harness는 내용에서 계좌·비밀·Provider 필드를 탐지하거나 도구별로 저장 여부를 판단하지 않는다. 도구를 제공하는 host가 인자·권한과 반환 내용을 책임진다.
2. 모델이 요청한 `(도구 이름·인자, 결과)`를 순서대로 현재 Context에 추가한다. 요청은 assistant의 도구 요청, 결과는 비신뢰 도구 데이터로 렌더한다. 각 모델 호출 전 실제 입력 크기를 기존 token budget으로 확인한다. 모델의 다음 출력이 또 읽기 도구면 같은 흐름을 이어가고, 최종 Answer면 `_claim_commit`한다.
3. 완료된 Turn에는 **사용자 입력·순서 있는 도구 요청/결과·최종 assistant 답변**을 일반 대화 Context로 저장한다. 다음 Turn은 이를 기존 대화와 함께 읽는다. 도구 결과만을 위한 임시 캐시, Turn 종료 시 별도 폐기, placeholder 치환, 최종 redaction callback은 만들지 않는다. 현재 Turn은 user/assistant 한 쌍이므로, 하나의 완료 Turn에 선택적 순서 있는 도구 교환을 보존하는 최소 store 계약 변경이 필요하다. 구체적인 자료형은 구현 전 현재 `ConversationStore`·PIA DynamoDB adapter와 대조해 가장 작은 형태로 고정한다. 미완료·supersede된 generation의 교환은 완료 Turn으로 저장하지 않는다.
4. 기존 부작용 도구는 지금처럼 `_claim_commit` **이후 한 번** 호출하며, 결과를 모델에 다시 넣지 않는다. Harness는 부작용의 의미나 사용자 승인 정책을 소유하지 않는다. 외부 중복 방지와 미확정 결과 처리는 host/실행 서비스가 책임진다. 기존 `execute_tool`·`ToolResult`는 호환을 위해 유지하되 새 읽기 왕복에 불필요한 저장 문구 callback을 요구하지 않는다.
5. 생성 중 새 입력·reset은 기존 generation을 취소한다. commit을 소유한 뒤에는 기존 shield와 전달·저장 의미를 유지한다. 읽기 callback 오류는 기존 안전한 실패 상태로 끝내고 그 호출을 자동 반복하지 않는다. 도구 미등록 시 Answer schema, 일반 Memory, Web Search, Turn 동작은 그대로 둔다.
6. Context가 길어지면 기존 Compaction을 사용한다. 조회 왕복에서 입력 예산의 약 80%에 도달하면 저장된 이전 대화를 한 번 요약해 여유를 만드는 사용자의 결정을 반영한다. 도구 결과도 완료 Turn에 남으므로 이후의 일반 Compaction·Summary 입력에 포함되며, 결과를 제외한 별도 compaction 계산 경로를 만들지 않는다. 처음부터 hard budget에 담을 수 없는 경우는 기존 Context 실패로 끝낸다.
7. 기존 Adapter는 `tool_call`과 `needs_web_search`의 동시 선택을 무효로 처리한다. 이 검증은 유지한다. 읽기 도구를 거친 뒤 Web Search나 Memory action을 어떻게 결합할지는 금융정보 보안 예외로 선제 차단하지 말고, 현재 Adapter 계약과 실모델 smoke를 바탕으로 필요한 최소 동작만 정한다.

## 책임 경계와 적용 순서

- Harness: 모델·Context·도구 왕복·중단·완료 Turn·Compaction의 범용 계약. AWS, Telegram, 계좌, 주문, 비밀 탐지 코드 없음.
- pia-agent: 도구 등록·회원/권한·도구 입력과 출력·채널 전달. 도구 결과에 무엇을 넣을지는 pia-agent가 정한다.
- pia-broker: Provider Credential/Token, 시세·계좌 데이터와 주문 원본·실행. 현재 PIA용 조회/주문 route는 아직 구현 전이다.

이 브랜치에서는 Harness 계약과 network-free fake 테스트만 구현한다. 먼저 한 읽기 도구가 결과를 돌려준 뒤 모델이 답하는 수직 슬라이스를 검증하고 여러 왕복으로 넓힌다. 독립 코드 검토 후 별도 승인을 받아 synthetic 실모델 smoke, 버전 릴리스, PIA wheel pin, Broker 읽기 route와 PIA 도구 연결 순으로 진행한다. 요청별 코드 실행 환경은 나중에 host가 제공할 수 있는 별도 도구이며 Harness에 구현하지 않는다. live Broker/주문/AWS 변경은 각각 별도 승인이다.

## 구현 검증

- 도구 미등록 기존 Answer/schema·Memory·Web Search·Turn 테스트 비회귀.
- 읽기 도구 0/1/여러 번 왕복 뒤 최종 Answer, 요청/결과 순서와 다음 Turn에서의 Context 재현.
- 생성 중 supersede/reset, 늦게 끝난 callback, commit 경합에서 미완료 교환이 저장·전달되지 않음.
- 기존 쓰기 도구는 commit 이후 한 번만 실행되고 읽기 왕복으로 실행 시점이 당겨지지 않음.
- 기존 token budget·Compaction과 완료 Turn의 도구 교환이 같은 경로에서 동작함. 별도 redaction/cleanup/도구별 보관 정책은 테스트 범위에 추가하지 않음.
- 계약 문서(`docs/01`, `03`, `04`)와 README는 실제 코드 구현 뒤 현재 동작에 맞춰 갱신. 변경 영역 테스트와 전체 suite·wheel build를 실행.

## Claude 재검토 질문

1. 일반 Turn에 도구 요청·결과를 순서대로 남기는 최소 `ConversationStore` 계약은 무엇인가? 별도 도구 저장소나 host finalizer 없이 가능한가?
2. 읽기 호출은 GENERATING, 기존 쓰기 호출은 COMMITTING이라는 경계를 현재 cancellation/전달 실패 코드가 지키는가?
3. 도구 결과를 다음 Turn의 일반 Context·Compaction으로 넘기면서 별도 상태나 결과 제외 계산이 필요한 실제 이유가 있는가? 없다면 추가하지 말아 달라.
4. 사용자 요청 범위를 넘는 도메인 보안 분기, 저장용 치환, 임의 호출 횟수 상한, 다른 추측성 예외가 남아 있는가?

검토는 계획만 대상으로 한다. 코드 수정·릴리스·PIA/Broker 통합·AWS/실모델/증권사 호출은 이 계획 commit의 범위 밖이다.
