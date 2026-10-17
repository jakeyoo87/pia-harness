# 지침과 도구 설명의 자리 정리 (도구 사양은 도구 설명에, System 지침에는 선택·흐름·대화 규칙만)

날짜: 2026-10-17
브랜치: pia-harness `claude/prompt-placement` (계획서만, 구현 전)
기준: pia-harness main `0ad215d`(v0.7.14 + README), pia-agent main `d36515e7f`

## 배경

매 모델 호출에는 두 칸이 함께 간다(README §3).
- `messages`의 첫 system 메시지: PIA 지침과 harness 지침(AGENT_INSTRUCTION)
- 요청의 `tools` 필드: 등록된 도구 전부의 이름·설명·인자 schema

모델은 둘 다 읽는다. 지금은 일부 도구 사양이 두 칸에 겹쳐 있거나, 한 도구의 사양이 System 지침에 들어가 있다. 동작은 정상이지만, 한쪽만 고치면 서로 어긋나고 매 호출 같은 글을 두 번 읽는다. broker를 고칠 때 System의 목록은 두고 도구 설명만 바꾸는 일이 생길 수 있다.

## 기준 (사용자 결정, 2026-10-17)

모델 제공자 가이드를 따른다.
- [OpenAI Function calling](https://developers.openai.com/docs/guides/function-calling): 함수 설명에 목적·인자·결과를 자세히 적고, System Prompt에는 각 함수를 언제 쓰고 언제 쓰지 말지를 적는다.
- [Anthropic, Writing effective tools for agents](https://www.anthropic.com/engineering/writing-tools-for-agents): 도구 성능은 자세한 도구 설명에 가장 크게 좌우된다.

정리하면 규칙은 하나다.

| 칸 | 둘 것 |
|---|---|
| 도구 설명(`tools`) | 그 도구 하나의 사양: 하는 일, 인자, 언제 부르고 언제 다른 도구를 쓸지, 주의점 |
| System 지침 | 여러 도구 중 무엇을 고를지, 여러 도구·여러 Turn에 걸친 흐름, 대화 태도 규칙 |

**같은 사양을 두 곳에 쓰지 않는다.** 원칙에 따라(simple-first) 새 구조나 장치는 만들지 않고, 중복을 지우거나 한 곳으로 옮기기만 한다.

## 전체 점검 (2026-10-17)

점검한 것:
- System 지침 전부: pia-agent `app/core.py`의 SYSTEM_PROMPT·BROKER_READ_PROMPT·ORDER_PROMPT·ONBOARDING_PROMPT, harness `openrouter.py` AGENT_INSTRUCTION, `investment_plan.py` PLAN_PROMPT
- PIA가 등록하는 도구 12개의 설명과 인자 schema 안의 설명 전부: broker·order·news_search·web_search·web_extract·past_financials·plan·plan_history·memory·choices·confirm

| # | 지금 | 바꿈 | 이유 |
|---|---|---|---|
| 1 | pia-agent BROKER_READ_PROMPT: broker가 조회하는 항목 목록(계좌·시세·지수·금리·환율·상품·투자자 동향·순위) | 한 줄로 바꾼다: `For the user's own account and for live market figures (quotes, indices, rates, exchange rates, commodities, investor flows, rankings), use the broker tool rather than web search.` | 목록은 broker 설명과 인자 설명(`action`·`market`·`macro`·`commodity`)에 이미 있다. System에는 broker와 web_search 사이의 선택만 남긴다. broker가 등록될 때만 붙는 지금 방식은 그대로다. |
| 2 | pia-agent ORDER_PROMPT 첫 문장의 `(US: limit orders only)` | 삭제한다 | order 인자 설명(`order_type`: "US stocks take limit orders only")에 있고, 코드도 막는다(`broker_order.py` prepare). |
| 3 | pia-agent SYSTEM_PROMPT의 `Never claim a new order was placed without an order tool result.` | 삭제한다 | ORDER_PROMPT에 같은 규칙이 더 정확하게 있다("Report a new order as accepted or rejected only from an order tool result; results already in the conversation may be explained again"). broker가 없으면 order 도구도 없어서 주문 자체가 불가능하다. |
| 4 | pia-agent ONBOARDING_PROMPT의 `A button exists only when you call the choices tool before your answer, including after text you send exactly: whenever a reply offers the next step or mentions a button, call it.` | 초기 안내에서 지우고, harness choices 설명(`CHOICES_TOOL_SPEC`)에 사양으로 넣는다: `Buttons appear only when you call this tool before your answer, even when the answer text is given word for word; whenever your answer offers a choice or mentions a button, call it.` | 버튼이 생기는 조건은 choices 도구의 사양이다. 초기 안내뿐 아니라 버튼을 쓰는 모든 답변에 해당한다. |
| 5 | harness AGENT_INSTRUCTION 끝의 `Do not claim that a Memory change has already persisted; the application adds success or failure information after the durable write.` | 삭제한다 | memory 도구 설명에 같은 규칙이 있다("The change is made from this request after your answer; do not say it is already saved."). memory는 항상 등록되는 기본 도구다. |

그대로 두는 것과 이유:
- **ORDER_PROMPT 나머지:** 확인 질문 → 다음 메시지에서 동의 → 결과는 도구 결과로만 → 확인이 지났으면 다시 요청 → "주문할 수 없다"고 하지 않기. order·confirm 도구와 여러 Turn에 걸친 흐름이다.
- **PLAN_PROMPT:** 투자 계획을 다루는 대화 규칙이다. 명시적 요청일 때만 바꾸고, 선택지를 주고 고르게 하고, 버전은 날짜로 부른다.
- **ONBOARDING_PROMPT 나머지:** memory·broker·choices·plan을 순서대로 쓰는 흐름이다.
- **AGENT_INSTRUCTION 나머지:** 검색 → 읽기 → 인용 같은 여러 도구 공통 조사 규칙이다.
- **SYSTEM_PROMPT 나머지:** 언어, 투자 권유 금지, 차트 답변 형식이다.
- **도구 설명 안의 "이럴 땐 다른 도구"**: news_search ↔ web_search, past_financials → broker·web_search, broker의 "다우·CPI·DRAM은 web_search" 등. 가이드의 "그 도구를 언제 쓰지 말지"라서 제자리다.
- **broker와 order의 `name` 인자 설명이 거의 같은 것:** 서로 다른 도구의 인자이고, 도구마다 설명이 혼자 완결되어야 한다. 지침과 도구 사이의 중복과는 다르다.

범위 밖:
- 도구 결과 문장(예: broker 결과의 안내)과 harness 알림 줄(확인 대기·조사 한도): 실행 중에 생기는 결과다.
- MEMORY_INSTRUCTION·PLAN_MEMORY_RULE: Memory 작성 호출용 지침이다.

## 변경

**pia-harness** (먼저, 릴리스 v0.7.15)
- `orchestrator.py` `CHOICES_TOOL_SPEC` 설명에 4의 한 문장을 더한다.
- `openrouter.py` `AGENT_INSTRUCTION`에서 5의 한 문장을 지운다.
- `tests/manual/smoke_flow.py`의 PIA 지침 사본(`BROKER_PROMPT`, `SYSTEM_PROMPT`)을 1·2·3에 맞춘다. 실측이 PIA와 같은 글로 돌게 하기 위해서다.
- README §6.0 공통 규칙에 기준 한 줄을 적는다: "도구 설명에는 그 도구의 사양, System 지침에는 도구 선택·여러 도구 흐름·대화 규칙만 둔다. 같은 사양을 두 곳에 쓰지 않는다."
- 단위 테스트: 문장을 고정한 테스트가 있으면 맞춘다.

**pia-agent** (harness v0.7.15 릴리스 뒤)
- harness pin을 v0.7.15로 올린다(requirements.txt·lock·관련 테스트).
- `app/core.py`에서 1·2·3·4를 바꾼다.
- `tests/test_core.py:82`의 `assertIn("Never claim a new order was placed", SYSTEM_PROMPT)`를 3에 맞춘다. 같은 보장은 ORDER_PROMPT 검사(:83-84)가 맡는다.
- README의 지침 설명 문장이 있으면 맞춘다.

순서가 중요하다. 4는 PIA에서 지우는 문장이 harness choices 설명으로 가는 것이므로, PIA 변경은 v0.7.15를 고정한 커밋에서 함께 나간다. 그래야 둘 다 없는 시점이 생기지 않는다.

## 검증

- 오프라인: 두 저장소 전체 테스트(harness unittest, pia-agent `verify-pia.sh`).
- **유료 시나리오(사용자 승인 뒤):** `tests/manual/smoke_flow.py`에서 바뀐 문장이 영향을 주는 세트만 돌린다.
  - `--set broker`: 1번. 계좌·시세·지수·순위 질문이 broker로 가는지, 다우 b28처럼 broker에 없는 것은 web_search로 가는지.
  - `--set execution`: 2·3번. 주문 확인 흐름과 미국 시장가 거부 안내.
  - `--set onboarding`(`--extra-prompt-file`로 바뀐 ONBOARDING_PROMPT): 4번. 다음 단계와 버튼이 있는 답변마다 choices를 부르는지.
  - `--set read`의 Memory 시나리오(4·4-1·10·22): 5번. 저장됐다고 단정하지 않는지.
  - 기준: 이전 기록(`tests/manual/records/`)과 비교해 나빠진 것이 없다. 나빠지면 그 문장만 되돌린다.
- 배포 뒤 dev Telegram 확인(테스트 회원): 시세 한 번, 초기 안내 버튼 한 번.

## 순서

1. 이 계획 → EC2 Codex 계획 검토
2. harness 구현(Claude) → 유료 시나리오(사용자 승인) → Codex 구현 검토 → 병합 → v0.7.15 릴리스(Codex)
3. pia-agent 구현(Claude) → Codex 구현 검토 → 병합 → `bot-only` 배포(Codex, 사용자 승인)

## Codex 검토 요청 시 볼 점

1. 기준(도구 사양은 도구 설명, System에는 선택·흐름·대화 규칙)과 5곳의 판단이 맞는지, 놓친 중복이 있는지.
2. 1번 새 한 줄이 broker와 web_search 선택을 지금보다 흐리게 하지 않는지.
3. 3번 삭제로 broker 없는 구성에서 잃는 보장이 있는지.
4. 4번을 choices 설명으로 옮길 때 문장이 원래 뜻(정해진 문장을 그대로 보낼 때도 choices를 불러야 함)을 지키는지.
5. 시나리오 세트가 충분하고 줄일 것은 없는지.

## Codex 계획 검토 (b4b0bfe, 2026-10-17)

중복을 줄이는 방향은 타당하다. P2 두 건은 보완한다.

**보완 필요**
1. [P2] 주문 결과 없이 주문을 말하지 않는 규칙이 Broker 없는 구성에서 사라진다.
   - `broker_enabled=False`이면 ORDER_PROMPT도 빠진다(`app/core.py:105`). 그래서 3번을 지우면 그 구성에서는 명시적 금지가 없어진다.
   - 규칙은 SYSTEM_PROMPT 한 곳에 두고, ORDER_PROMPT의 중복 부분을 덜어낸다. 실행 가능 여부와 답변의 진실성은 별개다.
2. [P2] execution 실측에 바꿀 주문 지침이 들어가지 않는다.
   - `execution` 세트는 주문 도구를 등록하지만, `BROKER_PROMPT`는 `_BROKER_SETS`에만 붙는다(`tests/manual/smoke_flow.py:833`).
   - 그 사본도 실제 ORDER_PROMPT의 첫 문장만 담고 있다.
   - 실측 전에, 실제로 남길 주문 지침을 주문 도구가 있는 세트에 똑같이 넣도록 계획에 명시한다. 그래야 2·3번을 검증할 수 있다.

**적절함과 다듬을 것**
- 1번은 `live market figures`보다 "broker가 지원하는 계좌·시장 수치"로 줄이는 것이 좋다. 실시간이라는 오해와, 지원하지 않는 지수까지 포함되는 혼동이 줄어든다.
- 2·4·5번은 설명·코드·Memory 준비 결과에 필요한 규칙이 남으므로 정리할 수 있다.
- 배경의 "매 호출에 도구 전부가 들어간다"는 고쳐야 한다. 조사 한도 뒤나 실행 뒤의 최종 답변 호출에는 도구가 없다.
- 시나리오 세트는 더 늘릴 필요가 없다. 주문 지침 주입만 맞추고, Memory는 검색과 함께 쓰는 기존 23번도 넣는다.
- 공식 가이드는 도구 설명을 명확히 하고 System에서 사용 시점을 안내하는 방향을 뒷받침한다. "같은 사양을 두 곳에 쓰지 않는다"는 가이드가 금지한 것이 아니라 프로젝트의 설계 선택이므로, 그렇게 구분해 적는다.
