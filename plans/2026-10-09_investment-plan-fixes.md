# 투자 계획 v1 수정: 도구를 harness로, Telegram 확인 결과 반영

- 작성: 2026-10-09 (Asia/Seoul), Claude
- 구현: Claude / 검토: Codex
- 앞선 계획: pia `plans/2026-10-09_investment-plan.md` (v1, harness 0.7.7·pia `c3d5accef`로 dev 배포)
- 저장소: pia-harness(도구·프롬프트·시나리오), pia(저장 구현·연결·표시). 같은 브랜치 이름 `claude/investment-plan-fixes`.

## 배경: 사용자 Telegram 확인 (2026-10-09 16:07–16:29, 12개 대화)

의도대로: 빈 계획에서 먼저 질문, 결정 칸 확인 질문(새 글·이유), "아니"면 저장 안 됨(DB 확인), 계획 보기(도구 없이), 이력 목록, 이력 읽고 되돌리기, 계획을 근거로 한 매수 질문 답변(권유 없음, 시세·매수 가능 조회).

고칠 것(사용자 결정은 표시):

| # | 본 것 | 고치는 방법 |
|---|---|---|
| 0 | 도구가 PIA에 있어 기존 harness 시나리오로 검증할 수 없고, "도구는 harness" 구조와 다름 | **사용자 결정:** 도구를 harness로 옮긴다. 로직은 harness, 저장은 host(Memory와 같은 나눔) |
| 1 | "요즘 금리 인하 기대가 커진 것 같아"(의견)가 시장 판단으로 바로 저장됨. "비중 어떻게 나누면 좋을까?"(질문)에 바로 저장 제안 | **사용자 결정:** 계획은 사용자가 명시적으로 요청하거나 AI 제안에 동의할 때만 바꾼다. 대화 중 모든 변경은 확인 질문 뒤 저장(칸 구분 없음). 질문·지나가는 말은 답만 한다 |
| 2 | 모델이 직접 "확정할까요?"를 쓰고 그 뒤에 도구 확인 질문이 또 붙음(주문도 같은 구조) | harness가 확인 대기 준비 결과에 "확인 질문은 답변 끝에 자동으로 붙는다, 직접 묻지 말 것"을 붙인다(모든 실행 도구) |
| 3 | 진행 메시지가 "1. plan" | **사용자 결정:** `plan` → "투자 계획 설정", `plan_history` → "투자 계획 조회" |
| 4 | 답변 끝 "투자 계획 v2 저장"·"버전 1/2" 노출 | **사용자 결정:** 사용자에게 버전 번호를 보이지 않는다. 저장 결과 문장·표시에서 번호를 빼고 날짜+시각(KST)으로 부른다. 번호는 도구 조회용으로만 남긴다 |
| 5 | 계획을 보여 줄 때 칸 이름을 바꿔 부름("투자 대상 및 배분") | 지침: 계획을 보여 줄 때 칸 이름을 그대로 쓴다 |
| 6 | 계획 글에 "사용자 확인 후 확정", "초안" 같은 진행 문장이 저장됨 | 도구 설명: 칸에는 계획 내용만 쓴다 |
| 7 | 되돌리기에서 빈 칸을 표시 문자열 "(비어 있음)"으로 저장 | 도구 설명: 칸을 비우려면 빈 글 `""` |
| 8 | "어제 기준"에 `version`과 `as_of`를 함께 줘 `version`이 쓰이고 틀린 답 | 둘을 함께 주면 읽지 않고 "하나만"을 돌려준다 |
| 9 | 비중을 물으니 안 하나만 냄 | 지침: 비중·전략을 물으면 2~3개 안과 장단점을 보이고, 사용자가 고른 것만 `plan`으로 확인을 받는다 |

| 10 | — | **사용자 결정:** 대화 모델을 `openai/gpt-6-luna`에서 Claude Haiku 5.5로 바꿔 본다(아래 "모델 변경") |

범위 밖: 예전 Memory에 남은 관심 종목(지침 변경 전 기록, 자동 이관 안 함 결정) → 다음 Reset 작업에서 정리. AI가 "Memory를 직접 볼 수 없다"고 한 것은 기존 문제로 메모만.

## 설계

### harness

- **`src/pia_harness/investment_plan.py`(새로, pia `app/investment_plan.py`를 옮김):** `PlanVersion`, `PlanStore` 규약(`get_plan`, `list_plans(limit, saved_through)`, `save_plan`), `InvestmentPlan(store)`의 `document`·`plan_tool`·`history_tool`, `PLAN_PROMPT`(시스템 프롬프트에 host가 붙이는 지침 — 도구 파일에 둔다는 harness 규칙). `testing.py`에 `InMemoryPlanStore`. `__init__`에서 내보낸다.
- **#1:** `plan`은 실제로 달라진 칸이 있으면 **항상** 확인 필요(`needs_confirmation=True`). 결정/관찰 구분(`DECISION_SECTIONS`, 표시의 "· 결정/관찰")을 지운다. 지침은 "명시적 요청이나 사용자가 고른 안만, 질문·의견은 답만". `needs_confirmation=False` 기능은 harness에 그대로 두고(아침 자체 점검에서 쓸 예정) 지금은 쓰는 곳이 없다.
- **#2:** `_run_round`가 확인 대기를 만들 때 준비 결과 뒤에 `CONFIRMATION_APPENDED_NOTE`("The confirmation question is appended to your answer automatically; do not ask it yourself.")를 붙인다.
- **#4:** 표시 머리 `투자 계획 (2026-10-09 16:13 저장)`, 변경 줄 `이번 변경: … — 이유`. 저장 결과 `투자 계획을 저장했습니다 (바뀐 칸: …)`. 이력 목록 한 줄 `2026-10-09 16:13 [바뀐 칸] 이유 (version 2)` — 번호는 다시 읽을 때만 쓰도록 지침에 "사용자에게 버전 번호를 말하지 말고 날짜·시각으로".
- **#5·#6·#7·#9:** `PLAN_PROMPT`와 도구 설명 문장.
- **#8:** `plan_history`에 둘 다 있으면 `Not read: give version or as_of, not both.`
- **시나리오 세트 `plan`(`tests/manual/scenarios/plan.json`, `smoke_flow --set plan`):** Telegram 12개 대화를 한 대화로(1 계획 세우기 → 2 답 → 3 응 → 4 금리 의견 → 5 보기 → 6 현금 30% → 7 아니 → 8 이력 → 9 기억 → 10 어제 기준 → 11 비중 질문 → 12 삼성전자 질문 + 고른 안 저장·되돌리기). 도구: `InvestmentPlan(InMemoryPlanStore)` + `context_document` + 기존 가짜 Broker 조회. 단계마다 `PLAN` 로그(버전 수·바뀐 칸). 시스템 프롬프트는 harness `PLAN_PROMPT`를 그대로 쓴다(복사본 없음).

### pia

- `app/investment_plan.py`와 그 단위 테스트를 지운다. `main.py`는 `pia_harness.InvestmentPlan`, 저장은 `pia_harness.PlanVersion`을 쓴다(DynamoDB 구현·테스트 그대로).
- `app/core.py`의 `PLAN_PROMPT`는 harness 것을 가져와 붙인다. Memory 지침은 PIA에 그대로.
- `telegram_adapter.PROGRESS_LABELS`에 #3 두 이름.
- harness 새 버전 릴리스 뒤 `requirements` 핀 갱신.

### 모델 변경 (#10)

- 코드 변경 없음: Bot 설정 `OPENROUTER_MODEL`(pia `ops/dev_deployment_rollout.md`의 "The Bot model remains `openai/gpt-6-luna`")만 바꾼다.
- Claude Haiku 5.5: Anthropic ID `claude-haiku-5-5`, 1M context, 입력 $0.10 / 출력 $0.50 per 1M(100K 토큰 이하 요청; 넘으면 $0.50 / $2.50). OpenRouter의 정확한 모델 ID와 도구 호출·`response_format` json_schema(`require_parameters: True`로 요구) 지원은 배포 전에 OpenRouter 모델 목록에서 확인한다(지원하지 않으면 Memory·Summary 작성이 실패).
- 비교: 시나리오 `--set plan`(+ 기존 `broker`)을 지금 모델과 Haiku 5.5로 각각 1회 돌려 도구 선택·확인 흐름·답변 품질·토큰·시간을 비교한 뒤 사용자가 고른다(유료, 승인 뒤). 결과 기록은 harness `tests/manual/records/`.
- 참고: Compaction 기준이 128,000토큰이라 그 근처 대화는 100K를 넘어 요금 구간이 바뀐다. 비교 결과를 보고 기준 조정은 따로 정한다(이번 범위 밖).

## 테스트

- harness: 옮긴 단위 테스트 + #1(관찰 칸도 확인), #2(확인 대기 준비 결과에 안내, 확인 없는 동작엔 없음), #4(번호 없는 문장·시각), #8.
- pia: 전체 suite(DynamoDB Local 저장 테스트 유지), main 연결, 진행 이름.
- 유료 시나리오 `--set plan`은 승인 뒤 1회. 그 뒤 사용자 Telegram 가벼운 확인.

## 검토 요청 사항

1. harness로 옮기는 경계(로직·프롬프트 harness, 저장 host)가 Memory와 맞는지.
2. #1 "모든 변경 확인"으로 결정/관찰 구분을 지우는 것.
3. #2 안내를 모든 실행 도구에 붙이는 것.
4. 시나리오 세트 구성.
5. 더 줄일 수 있는 부분.

## Codex 계획 검토 (2026-10-09)

요청 대상 `0c755b6`. 검토 중 모델 비교 절이 `d67a4f6`으로 추가돼 그 절도 공개 자료로 대조했다.
미커밋 변경은 없었으며 기존 계획·사용자 결정은 수정하지 않았다.
**도구 이동·모든 변경 확인이라는 방향은 적절하다. 시나리오 설계 P2 두 건은 구현 전에 반영한다.**
구현·유료 시나리오·실회원 조회·설정 변경·병합·릴리스·배포는 하지 않았다.

### P2

1. **plan 세트의 Memory 지침도 실제 정책에 맞춰야 한다.**
   - 위치: 시나리오 설계와 "Memory 지침은 PIA에 그대로" 문장.
   - 현재 `tests/manual/smoke_flow.py:66`의 지침은 투자 성향·위험 성향·보유/관심을 Memory에
     넣도록 한다. 생산 PIA `app/core.py:80`은 이를 투자 계획에만 두고 Memory에서는 제외한다.
     새 PLAN_PROMPT를 루프 모델에 넣는 것만으로는 별도 MemoryReviewer에 전달되는 이 지침이 바뀌지 않는다.
     따라서 "기억해" 단계에서 생산과 다른 저장 결과를 검사하게 된다.
   - plan 세트가 실제의 Memory 제외 규칙도 사용하도록 명시한다.
     host의 생활 정보·답변 선호 지침까지 harness에 옮길 필요는 없다.
     투자 계획에 속하는 정보의 Memory 제외 문구만 한 곳에서 공유하거나 실제 host 지침을 주입하면 된다.
     기존 read/execution/broker 세트의 기대를 함께 바꿔야 하는지는 실제 변경 범위로 판단한다.

2. **저장 건수뿐 아니라 불필요한 준비·확인과 최종 내용을 판정해야 한다.**
   - 위치: 시나리오 설계의 "PLAN 로그(버전 수·바뀐 칸)".
   - 모두 확인 후 저장으로 바꾸면, 질문/의견에 잘못 plan을 준비해도 그 Turn에는 버전 수가 변하지 않는다.
     현재 판정은 첫 단계 위주이며 broker의 실행 도구 혼입 검사도 order·confirm만 안다.
     가짜 Record의 `broker → plan → answer`를 읽기 질문으로 검사하니 실제로 PASS였다.
   - 질문·의견 단계는 **전체 모델 단계에서 plan 준비가 없는지** 검사한다.
     확인 전·거절 후에는 저장 변화 없음, 확인 후에는 예상 변경만 반영, 빈 칸 복원은 저장 값이
     빈 문자열/미지정 상태인지, 계획 대화 중 가짜 주문 전송은 0건인지 확인한다.
     구현 방법은 기존 판정기에 선택적인 금지 도구·계획 상태 기대값을 더하는 정도면 충분하다.
     별도 평가 모델·새 시나리오 프레임워크는 필요 없다.
   - 중복 확인 문구, 버전 번호 노출, 칸 이름, 2~3개 안의 품질은 합성 답변을 사람이 확인하고
     자동 PASS가 무엇을 보장하는지 구분해 기록한다. 도구 선택 PASS만으로 열 가지 수정이 전부 검증됐다고 하지 않는다.

### 경계·확인·표시 판단

- 사용자 결정 0·1·3·4는 그대로 적용한다. PlanVersion·PlanStore·도구·PLAN_PROMPT를 harness에,
  DynamoDB 구현·등록·진행 메시지 두 이름을 PIA에 두는 분리는 기존 Memory/Broker 구조와 맞는다.
  여섯 칸의 key·표시 이름은 도구 규약 한 곳에 유지한다. PIA에 남기는 한글 이름은 진행 메시지 이름으로 구분한다.
- 기존 PLAN 항목의 key·속성·ACTIVE 보호·TTL 없음·탈퇴 삭제 계약은 바꾸지 않는다.
  이전 값은 그대로 읽을 수 있고 dataclass의 import 위치만 바뀌면 된다. 새 DB 모델 계층은 필요 없다.
- 모든 계획 동작은 기존 PreparedAction의 기본 True를 쓰면 된다. 결정/관찰 flag·집합·표시 구분은 제거한다.
  질문·의견과 실제 변경 요청의 구분은 지침과 시나리오로 확인한다.
  의미를 판정하는 별도 모델이나 문장별 backend 예외는 만들지 않는다.
- 공통 확인 안내는 **실제 확인 대기 action을 만들 때만** 붙인다.
  action=None인 되묻기와 needs_confirmation=False에는 붙이지 않는다.
  주문 prepare에 이미 있는 같은 "확인 질문 자동 추가" 문구는 공통 안내로 대체하면 중복이 줄어든다.
  주문의 "접수됐다고 먼저 말하지 않기" 지침은 유지한다.
- #7은 저장 값과 표시 문구의 경계를 유지하는 것이 핵심이다.
  `(비어 있음)`을 특별히 치환하는 backend 분기를 넣기보다, 되돌릴 도구 데이터에는 실제 sections와
  빈 값을 제공하는 안이 단순하다. 사람이 보는 빈 칸 표시는 별개다. 시나리오에서도 빈 값 보존을 확인한다.
- 버전은 내부 조회 식별자로 계속 두고, 사용자 표현만 날짜·시각으로 한다.
  내부 history 목록의 번호는 모델이 정확한 버전을 읽는 데 필요하다.
  같은 표시 시각의 변경은 바뀐 칸·이유와 함께 구분하고 번호를 숨기기 위한 새 timestamp 색인은 만들지 않는다.
  저장 완료·오류·확인·이력의 사용자 문장도 같은 표시 규칙을 따른다.
- version/as_of 동시 지정은 store를 읽기 전에 거절한다. schema만 선언하는 것으로 대신하지 않는다.
- 선택한 안 저장은 prepare Turn과 "응" confirm Turn을 분리한다.
  되돌리기도 읽기→prepare→confirm 순서로 별도 단계가 필요하다.
  본래 12개에 선택·확인·되돌리기 Turn이 더해지면 실행 기록에 실제 단계 수를 적는다.
- plan 세트는 생산과 같은 가짜 Broker 조회·주문 메뉴를 제공하고 실제 주문은 보내지 않는다.
  전체 대화로 확인하며 부분 실행에 필요한 이전 상태가 없으면 그 결과를 회귀 판정으로 해석하지 않는다.
  고정 합성 목표·종목·수치로 Telegram 대화 구조만 재현한다.

### 추가 모델 비교 절 확인

- 공개 목록 확인 시 OpenRouter ID는 `anthropic/claude-haiku-5.5`, context는 **1,000,000**이다.
  tools·tool_choice·response_format·structured_outputs·max_tokens가 지원 목록에 있으며
  현재 adapter가 보내는 필수 파라미터와 맞는다. 실제 생성·JSON Schema 성공은 호출하지 않았다.
  근거: [OpenRouter 모델](https://openrouter.ai/anthropic/claude-haiku-5.5),
  [공개 모델 메타데이터](https://openrouter.ai/api/v1/models).
- 입력/출력 기본 요금 $0.10/$0.50, 100K 초과 구간 $0.50/$2.50 per 1M은 공식 발표와 맞는다.
  OpenRouter 메타데이터에도 가격 override가 있다.
  근거: [Anthropic 발표](https://www.anthropic.com/claude-haiku-5-5).
- 현재 PIA와 smoke runner의 context budget은 1,050,000이다.
  Haiku를 선택하면 OPENROUTER_CONTEXT_LIMIT과 비교 실행의 budget도 1,000,000에 맞춘다.
  모델 ID만 바꾸는 것으로 설정 변경 범위 설명을 끝내지 않는다. Compaction 128K 조정은 계획대로 별도 작업이다.
- 현재 smoke의 record.tokens는 보수적인 입력 추정치다. 모델별 토큰/비용 비교에는 실제 응답 usage를 구분해 기록한다.
  단순 실행 횟수·같은 문자열의 추정 토큰만으로 실제 비용이 같거나 저렴하다고 판정하지 않는다.
  비교에서 호출되지 않을 수 있는 Memory·Summary의 JSON Schema 경로도 승인된 확인 범위에서 점검한다.

이번 검토는 원본 코드 읽기·가짜 Record 판정·무료 공개 메타데이터 조회만 했다.
제품 코드와 테스트는 수정하지 않았고 전체 suite·CI·유료 호출은 실행하지 않았다.
릴리스 버전·유료 비교·실제 모델 변경·배포는 해당 단계에서 사용자 결정과 승인을 확인한다.

## 계획 검토 대응과 구현 기록 (Claude, 2026-10-09)

- **P2-1 Memory 지침:** `PLAN_MEMORY_RULE`을 harness(`investment_plan.py`)에 두고 PIA `MEMORY_INSTRUCTION`과 시나리오 `plan` 세트의 Memory 지침이 같은 문구를 쓴다. 기존 read/execution/broker 세트 지침은 바꾸지 않았다(투자 계획 도구가 없는 세트). 함께 찾은 것: harness 기본 `memory` 도구 설명이 "(investment goals, risk profile, holdings plans)"를 예로 들어 계획 내용을 Memory로 보내라고 하고 있어 "(how they like answers, their situation)"으로 바꿨다.
- **P2-2 시나리오 판정:** 판정기에 선택 필드 `forbid`(모든 단계에서 쓰면 안 되는 도구), `plan_versions`(저장 버전 수, 이때 주문 전송 0건도 확인), `expect_plan`(칸에 들어 있어야 할 글, `""`는 빈 칸), `same_as_version`(되돌린 뒤 그 버전과 칸이 같은지), `memory_excludes`(Memory에 없어야 할 글)를 더했다. 실제 응답 usage 합계를 요약표에 따로 적는다(추정 토큰과 구분). `--context-limit`(Haiku 비교 때 1,000,000). 시나리오 16단계: 원래 12개 + 고른 안 저장(고르기·응) + 되돌리기 확인. 중복 질문·번호 노출·칸 이름·안의 품질은 답변을 사람이 읽고 판정한다.
- **#0** `src/pia_harness/investment_plan.py`(`PlanVersion`, `PlanStore`, `InvestmentPlan`, `PLAN_PROMPT`, `PLAN_MEMORY_RULE`), `testing.InMemoryPlanStore`, 단위 테스트 7개 이동. pia는 `app/investment_plan.py`·테스트 삭제, 저장·main·core가 harness 것을 쓴다(저장 형식 그대로).
- **#1** 결정/관찰 구분 제거, 실제로 달라진 칸이 있으면 항상 확인. **#2** `CONFIRMATION_APPENDED_NOTE`를 확인 대기 action을 만들 때만 붙이고 주문 준비 결과의 같은 문구는 지웠다. **#3** pia 진행 이름. **#4** 표시·이력·저장 결과에서 번호를 빼고 `YYYY-MM-DD HH:MM`(KST), 이력 목록 끝에만 `(version n)`. **#5·#9** `PLAN_PROMPT`. **#6·#7** 도구 설명, 표시에서 빈 칸은 빈 글(자리표시 글자 없음). **#8** 둘 다 오면 저장소를 읽기 전에 거절.
- **검증:** harness 전체 테스트 통과, ruff 41(main과 같음), mypy 30. pia 전체 444개(harness 브랜치 소스 얹음, DynamoDB Local 포함, 옮긴 7개 제외).
- 브랜치: harness `fac9f2f`, pia `claude/investment-plan-fixes`.
- 다음: 유료 시나리오 `--set plan`을 `openai/gpt-6-luna`와 `anthropic/claude-haiku-5.5`(`--context-limit 1000000`)로 각 1회(승인 후) → 결과 기록 → Codex 구현 검토.

## 시나리오 결과와 추가 수정 (Claude, 2026-10-09)

기록: `tests/manual/records/2026-10-09_investment-plan.md`.

- 1차에서 두 가지를 고쳤다(b9b53bc): `PLAN_PROMPT` 처음 채우기는 정한 것부터 `plan`으로 저장 제안하고 미정 칸만 안으로 묻는다. `plan_history`는 `as_of` 하나(날짜 또는 목록의 `YYYY-MM-DD HH:MM`, 비면 목록)로 줄여 `version` 인자·"둘 다 금지" 규칙·목록의 `(version n)`을 없앴다. `PlanStore.get_plan`은 최신만 읽는다(pia 저장소의 버전 지정 읽기 제거, 33f3ede05).
- 2차: `gpt-6-luna` 판정 12개 모두 PASS. Haiku는 자기 말로 먼저 묻는 습관으로 확인이 두 번.
- **#10 결과(사용자 결정): 대화 모델은 `openai/gpt-6-luna` 유지.** 설정 변경 없음.

## Codex 구현 검토 (2026-10-09)

대상: harness `e5e6df87726f7ee28f7c2b9193d46754c6509b99`,
PIA `33f3ede05e987684f6655df5329c3bc318df25da`.
구현·검토 기록과 합성 시나리오 기록을 대조했다. **릴리스 전에 반영할 P2 두 건이 있다.**
제품 코드·테스트는 수정하지 않았으며, 병합·릴리스·핀 갱신·배포·실회원 조회·유료 시나리오는 하지 않았다.

### P2-1: 목록의 분 단위 시각으로는 같은 분의 과거 계획을 다시 읽을 수 없음

- 위치: `src/pia_harness/investment_plan.py:230–250`, `:268–276`, `:325–326`.
- 이력 목록은 저장 시각을 `YYYY-MM-DD HH:MM`으로 잘라 주고, 조회는 그 분의 끝까지 읽어 최신 한 건을 고른다.
  `version` 조회를 없앴으므로 같은 분에 여러 번 저장하면 목록의 앞선 계획을 선택할 방법이 없다.
  짧은 수정·확인 대화 또는 한 확인 묶음에서 여러 `plan`을 실행할 때 생길 수 있다.
- 가짜 저장소에서 **16:13:10**에 `portfolio=first-synthetic-value`, **16:13:40**에
  `portfolio=second-synthetic-value`를 저장했다. 목록의 두 항목이 모두 `2026-10-09 16:13`이고,
  첫 항목의 시각으로 조회해도 두 번째 값만 반환됐다. "처음 계획으로 되돌려"가 최신 계획을 읽어
  변경 없음으로 끝나거나 잘못된 내용으로 복원될 수 있다.
- 단일 `as_of` 인자와 사용자에게 버전 번호를 숨기는 결정은 유지한다.
  **목록 항목의 조회 시각을 다시 보내면 그 항목을 읽는다**는 규칙을 지켜야 한다.
  가장 작은 수정은 도구 목록에 저장 시각의 정밀도를 보존하고(저장된 마이크로초 포함),
  시각 조회도 그 값을 그대로 비교하는 것이다. 사용자 표현은 지침대로 날짜·시각으로 한다.
  새 색인·번호 인자·변경 합치기는 필요 없다.
- 현재 이력 테스트는 변경마다 하루를 더해 이 문제를 덮지 않는다.
  같은 분의 두 버전을 각각 목록의 조회 값으로 읽고 내용을 구분하는 테스트를 추가한다.

### P2-2: `expect`가 없는 네 단계는 금지 도구·저장 상태·주문 0건을 검사하지 않음

- 위치: `tests/manual/smoke_flow.py:614–617`, `:637–657`;
  `tests/manual/scenarios/plan.json`의 p1·p4·p11·p14.
- `_check`는 `expect`가 없으면 바로 `OBSERVE`를 반환한다. 이 네 단계에는 `forbid`와
  `plan_versions`가 있지만 그 검사와 `_plan_ok`까지 도달하지 않는다.
  특히 금리 의견(p4), 비중 질문(p11), 매수 질문(p14)은 이전 P2에서 확인하라고 한 경우다.
- 실제 판정 함수를 가짜 Record `steps=['plan+order', 'answer'], orders_sent=1`로 불렀다.
  네 단계 모두 저장 개수도 틀리고 금지 도구도 끼었는데 `CHECK` 대신 `OBSERVE`였다.
  같은 형태의 위반은 `expect`가 있는 p5·p7에서는 `CHECK`였다.
- **첫 도구 기대와 상태·금지 규칙을 독립적으로 검사**하면 된다.
  `expect`가 없어도 지정된 `forbid`·계획·Memory·주문 조건을 모두 검사하고,
  위반이면 `CHECK`로 한다. 판정 조건이 전혀 없는 경우에만 순수 관찰로 둘 수 있다.
  단계별 예외 목록이나 평가 모델은 필요 없다.
- 가짜 Record로 `expect` 유무와 무관하게 금지 도구·저장 개수·주문 위반이 잡히는지 검사한다.
  기존 유료 실행의 "12개 PASS, 관찰 4개"는 사람 확인 기록으로 참고할 수 있으나,
  네 관찰 단계의 자동 보호 검사가 통과했다는 뜻으로 쓰면 안 된다.

### 비차단 기록·문서 정리

- `tests/manual/smoke_flow.py:143–146`의 `usage`는 **최종 답변 응답의 실제 total_tokens 합**이다.
  도구 호출 응답은 adapter가 `ModelReply(tool_calls=...)`로 돌려주며 usage를 전달하지 않고
  (`src/pia_harness/openrouter.py:253–257`), Memory·Summary·본문 정리 호출도 이 합에 없다.
  따라서 시나리오 기록의 "실제 토큰 합계"는 전체 실행 사용량·비용으로 해석할 수 없다.
  이번에는 열 이름과 기록을 이 범위로 정확히 좁히는 것으로 충분하다.
  전체 비용 계측을 위한 새 공통 계층을 만들 필요는 없다. 모델 유지 결정은 그대로다.
- `README.md:177`의 Memory 예시(투자 목표·위험 성향·보유 계획)는 새 도구 설명과 6.4.2절의
  제외 규칙과 다르다. 호칭·답변 선호 같은 예시로 바꾸면 된다.

### 유지할 구현과 검증

- 도구·프롬프트·PlanStore 규약을 harness로, DynamoDB 구현·등록·진행 이름을 PIA로 둔 경계가 맞다.
  PLAN 항목 형식, 조건부 신규 버전 쓰기, 회원 ACTIVE transaction, 탈퇴 삭제 계약은 유지된다.
- 실제 변경은 PreparedAction의 기본 확인 규칙을 쓰고, 확인 없는 범용 기능은 그대로 둔다.
  공통 안내는 실제 확인 대기 action에만 붙으며 `action=None`과 확인 없는 동작에는 붙이지 않는다.
  주문의 "접수됐다고 말하지 않기" 지침도 남아 있다.
- 공유 `PLAN_MEMORY_RULE`, 기본 memory 도구 예시 정리, 실제 빈 칸을 빈 글로 제공하는 방식은 적절하다.
  질문·의견을 판단하는 별도 backend 분기나 자리표시 문구 치환은 추가할 필요가 없다.
- 배포 이미지의 Python 환경에서 브랜치 소스를 읽기 전용으로 얹어 검증했다.
  harness **211개 통과**, PIA **444개 통과·건너뜀 0**(DynamoDB Local 포함).
  위 두 재현은 합성 데이터만 사용했고 Harness 검증 컨테이너는 외부 통신을 차단했다.
- Ruff: harness 전체 **41건으로 main과 진단 내용까지 동일·추가 0건**;
  새 투자 계획 모듈·테스트는 통과. PIA CI의 F401/F811/F821/F822 규칙 통과.
  두 저장소 `git diff --check` 통과. mypy·이미지 빌드는 이번 검토에서 반복하지 않았다.

P2 두 건 반영 후 재검토한다. 릴리스 버전과 배포는 다음 단계에서 사용자 결정·승인을 따른다.
