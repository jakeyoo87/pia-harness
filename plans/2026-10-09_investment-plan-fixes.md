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
