# 도구 실패 처리와 옛 투자 계획 정리 (2026-11-14)

사용자 2026-11-14: "Harness 도구들 싹다 한번 Revisit", "오늘거 하면서 안쓰는거, 예전거는 한번 제거했으면 좋겠다",
"이전거는 날리자". 근거는 11/11~11/12 실제 대화와 Bot 기록이다. 범위는 그날 이야기한 것만이다(미뤄 둔 항목은 그대로 미룸).

## 고칠 것

### 1. 도구 하나의 오류가 답 전체를 끊는다

- 지금: `_run_round`에서 읽기 도구(`execute`·결과 검사)나 실행 도구 준비(`prepare`)가 예상 밖 예외를 내면 그대로 올라가
  Turn이 `TOOL_FAILED`로 끝난다. 사용자는 "도구 호출에 실패했습니다"만 받는다. 11/12 12:27 `market_feed_search` 한 번의
  오류로 답이 통째로 사라졌다.
- 바꿈: 도구 호출 하나의 예상 밖 예외는 그 호출의 결과가 된다. 읽기는 `"Failed: the tool had an error. Answer without
  it or use another tool."`, 준비는 `"Not prepared: the tool had an error."`. 모델은 다른 결과로 계속 답한다. 시간 초과·
  조사 한도와 같은 자리에서 처리한다. `ConversationAbandoned`·취소는 지금처럼 올라간다.
- `TOOL_FAILED` 상태는 남는다(Harness 자체 코드의 오류). 호스트 쪽 변경은 없다.

### 2. 어느 도구가 왜 실패했는지 기록이 없다

- 지금: Bot 기록에는 `agent.conversation_completed status=TOOL_FAILED`뿐이다.
- 바꿈: 1번에서 결과로 바꿀 때 `logging.getLogger("pia_harness")`에 WARNING 한 줄:
  `harness.tool_failed tool=<이름> error_type=<예외 클래스>`. 인자·메시지·사용자 정보는 남기지 않는다(예외 메시지에
  URL·검색어가 들어갈 수 있다). PIA는 root logger를 설정하므로 따로 할 일이 없다.

### 3. 실패한 Turn의 질문이 사라진다

- 지금: 생성 실패(`GENERATION_FAILED`)는 받은 메시지를 대기열에 남겨 다음 메시지와 합쳐 다시 답하지만, `TOOL_FAILED`는
  지운다. 11/12에는 "현금 90%" 요청이 사라져, 다음 "잉?"에 PIA가 엉뚱한 설명을 했다.
- 바꿈: `TOOL_FAILED`도 생성 실패와 같이 대기열을 남긴다(`clear_pending=False`). 새 저장 방식은 만들지 않는다. 1번
  뒤로는 드물어진다.

## 정리할 것

### 4. 옛 투자 계획 제거

포트폴리오 운영(투자 운영 현황, pia `plans/2026-11-0x_portfolio-*`)이 대신하고, PIA는 `plan`·`plan_history`를 등록하지
않는다(pia README "옛 투자 계획은 쓰지 않는다").

- Harness: `investment_plan.py`(PlanStore·PlanVersion·InvestmentPlan·PLAN_PROMPT·PLAN_MEMORY_RULE), `__init__` 내보내기,
  `testing.InMemoryPlanStore`, `tests/test_investment_plan.py`, `tests/manual/smoke_flow.py`의 계획 부분, README §6.4.2와
  투자 계획을 가리키는 문장(구조도·도구 목록·구현 현황·Memory 지침·Context 크기).
- `context_document`(Host 문서)는 남는다: PIA가 투자 운영 현황을 넣는다. README의 "Host 문서(투자 계획 전용)"를
  "Host 문서(호스트가 사용자별로 주는 문서, PIA: 투자 운영 현황)"로.
- PIA(별도 브랜치, harness 새 버전 고정과 함께): `dynamodb_conversation_store`의 `get_plan`·`list_plans`·`save_plan`·
  `PLAN#` 변환과 테스트, README 223행. 남은 `PLAN#` 항목은 일회성 스크립트 `ops/delete_old_plans.py`(기본은 개수만,
  `--apply`일 때 삭제, 회원 ID 출력 없음)로 배포 때 한 번 지운다(dev: 회원 1명 2개). DynamoDB 쓰기는 이 배포의
  승인 범위다.

### 5. README "도구 정의" 섹션

§3 Context 구성에서 도구 정의 설명이 System 지침 문단 안에 섞여 있다. "도구 정의" 소단락을 따로 두고(무엇이 들어가는지,
언제 빠지는지, 크기가 고정분이라는 것), 도구별 규칙은 도구 설명에 둔다는 원칙을 한 줄로 적는다. 내용 추가 없이 옮기기만 한다.

### 6. 기존 ruff·mypy 오류

지금 main 기준 ruff 41개(I001 11, TRY004 11, BLE001 7, SIM117 7, ISC004 3, UP047 1, RUF022 1), mypy 30개(`testing.py`
계약 mixin의 `assert*` 26개, `orchestrator.py` `should_compact(**dict)` 4개). 4번으로 일부가 사라진 뒤 남은 것을 고친다.
동작은 바꾸지 않는다: import 정렬, 의도한 broad except에는 이유 주석과 `noqa`, mixin은 `unittest.TestCase` 타입 선언,
`should_compact`는 인자를 이름으로 넘김. 규칙을 끄는 설정 변경은 하지 않는다.

## 릴리스

- Harness 0.7.24 (1~6), 태그 릴리스.
- PIA: harness 0.7.24 고정 + 4번의 PIA 부분 + 일회성 삭제 스크립트. 배포는 bot-only(대화) + 스크립트 한 번.

## 확인

- Harness 테스트: 읽기 도구·준비가 예외를 내면 그 결과 문구로 이어서 답하고 `TOOL_FAILED`가 아니다, WARNING 한 줄에
  도구 이름·예외 클래스만, `TOOL_FAILED`(Harness 코드 오류) 뒤 다음 메시지가 앞 메시지와 합쳐진다.
- PIA 테스트 전체, 삭제 스크립트는 DynamoDB Local로 개수·삭제 확인.
- 배포 뒤: Bot 시작 로그, 스크립트 결과(개수만).

## 계획 검토 (Local Codex, a373dce): 차단 없음

- 반복 호출: 실패 결과에도 같은 도구를 부르면 기존 조사 한도(20회)로 끝나고 도구 없는 답으로 간다. 실패한 시도도
  횟수에 들어가므로 따로 재시도·차단 목록을 두지 않는다. 90초는 조사 한도다(최종 답 생성 시간은 별도).
- `TOOL_FAILED` 입력 보존: 실행 확정 전 오류 경로라 `clear_pending=False`로 충분하다.
- 구현 때 지킬 것: 실행 도구는 `prepare`와 `_validate_preparation()` 검사를 같은 오류 경계에 넣는다(읽기의
  `_validate_read_tool_result`와 같이). "동작 변경 없는 정리"이므로 TRY004 때문에 `ValueError`를 `TypeError`로 바꾸지
  않는다(이유 주석 + `noqa`).
- 추가로 지울 참조: 수동 시나리오의 `plan.json`, onboarding 계열의 `seed.plan`·`plan_versions` 검사, `context.py`의
  옛 투자 계획 주석, PIA README 저장 표의 `PLAN#` 설명.
- 삭제 스크립트는 `USER#… / PLAN#…`만 대상으로 하고 `PORTFOLIO#…`는 남긴다. 과거 계획서·검토 기록은 지우지 않는다.

## 구현 기록

- 1·2: `_run_round`의 읽기(`execute` + `_validate_read_tool_result`)와 준비(`prepare` + `_validate_preparation`)를 같은
  경계에서 받는다. `ConversationAbandoned`는 올리고, 시간 초과는 지금처럼. 로그는 `pia_harness` logger WARNING
  `harness.tool_failed tool=… error_type=…`. 실패한 읽기도 조사 횟수에 든다.
- 3: `TOOL_FAILED`의 `clear_pending=False`. 테스트: 묶음 처리 오류 뒤 "잉?"이 앞 요청과 합쳐져 답한다.
- 4: `investment_plan.py`, 내보내기 5개, `testing.InMemoryPlanStore`, `tests/test_investment_plan.py`,
  `tests/manual/scenarios/plan.json` 삭제. `smoke_flow.py`에서 plan 세트·계획 검사·`seed.plan` 제거. onboarding 세
  세트는 온보딩 흐름 검사로 남기고 `plan_versions` 키만 뺐다. 과거 기록(`tests/manual/records`, `plans/`)은 그대로.
- 5: README §3에 "도구 정의"·"Host 문서" 단락을 나누고, 도구 하나의 사양은 그 도구 설명에 둔다는 원칙 한 줄. §6.0에
  "도구 하나의 오류는 그 호출의 결과" 규칙, §6.2·6.2.2·6.4.1의 "Turn을 끝낸다" 문장을 새 규칙으로. §6.4.2 삭제.
- 6: 설정 파일이 없어 ruff 0.17.5 기본 규칙과 mypy 2.4.1 기본으로 쟀다. ruff: import 정렬·`__all__` 정렬 자동 수정,
  중첩 `with` 합침, 이어 쓴 문자열에 괄호, `_before_deadline`은 PEP 695 타입 인자, TRY004는 예외 종류를 바꾸지 않고
  `noqa` + 이유, 의도한 broad except(BLE001)는 `noqa` + 이유(위 주석이 이미 있으면 주석). mypy: 계약 mixin은
  `TYPE_CHECKING`에서만 `unittest.TestCase`를 부모로, `should_compact(**size)`는 계획의 "이름으로 넘김" 대신 `size`에
  `dict[str, Any]` 타입 표시(같은 효과, 더 작음), `delete_all_for_user`의 반복 변수 이름 분리. 결과 ruff 0, mypy 0.
- 버전 0.7.24. 테스트 261개 통과.
