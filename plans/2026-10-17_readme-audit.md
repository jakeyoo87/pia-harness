# README를 현재 코드에 맞추기 (2026-10-17)

README는 확정한 설계를 기록하고 Agent가 작업 전에 먼저 읽는 문서다(AGENTS.md). broker 조회, 과거 실적, 투자 계획, 버튼, 미국 주식이 차례로 들어오면서 README 일부가 코드와 어긋났다. 코드(main `cf7fe6d`, v0.7.15)와 한 줄씩 대조해 고칠 곳을 모으고, Codex 검토 뒤 반영한다.

## 대조 결과 (Claude)

- 기준: pia-harness `claude/prompt-placement` `898ac26` (배포 뒤 main이 될 코드). 줄 번호는 이 커밋의 `README.md`.
- 아직 고치지 않았다. 이 목록을 검토받은 뒤 README에 반영한다.
- 표시: **틀림**(코드와 다름) / **낡음**(예전 상태) / **누락**(지금 동작인데 안 적힘)

### §0 전체 구조

1. **L23–33 그림** "읽기: 뉴스·웹 검색, 본문 추출 (예정: 시세·잔고)" — **낡음**
   - 코드: `broker`(시세·잔고·순위), `past_financials`, `plan_history`가 이미 읽기 도구이고, 실행에는 `plan`도 있다. 공급자에 OpenDART·SEC가 빠짐.
   - 수정: 읽기 "뉴스·웹 검색, 본문 추출, 증권사 조회(시세·잔고·순위), 과거 실적, 계획 이력", 실행 "주문(매수·매도)·투자 계획 변경(사용자 확인 후)", 공급자에 "OpenDART·SEC(과거 실적)".

### §1 Harness Flow

2. **L59–64** 읽기 6.2.1~6.2.4, 실행 "6.4.1 주문" — **누락**: `plan_history`(읽기), `plan`(6.4.2, 실행)을 넣는다.
3. **L54–56** 답변 뒤 순서 — **누락**
   - 코드: 확인 없는 동작 실행 → Memory → 전달 → 확인 대기 저장 → Turn 저장 → 기준 넘으면 Compaction (`orchestrator.py:1230-1363`).
   - 수정: 그 순서로, Compaction 단계를 넣는다.
4. **L80 구현 현황** "실행 도구(주문), memory·confirm 기본 도구 … 확인 대기만 만든다" — **낡음**
   - 코드: `plan`, `choices`도 있다. `needs_confirmation=False`인 동작은 답변 확정 뒤 한 번 실행한다.
5. **L82** "모델은 System 지침, Memory, Summary, …를 모두 본다" — **누락**: Host 문서와 Harness 문장(이전 Turns 끝 표시, 확인 대기, 한도 알림)을 넣는다.
6. **L86** "조사 시간 90초 … 주문 `confirm`은 이 경로로 바꾸지 않는다" — **누락**
   - 코드: 실행 도구 준비도 90초 안에서 하며, 넘으면 "Not prepared: the time ran out."(`orchestrator.py:983-989`). `confirm`은 주문 전용이 아니라 모든 실행 도구 공용.

### §2 처리 중 새 메시지

7. **L100** "실행 확정 후 (주문의 confirm 호출 시점부터, 또는 답변 전달·저장 단계)" — **낡음**
   - 수정: "실행 도구의 confirm 호출 시점부터, 또는 최종 답변이 나온 뒤(확인 없는 동작·Memory·전달·저장·Compaction)".

### §3 Context

8. **L114·L129 도구 정의** "등록된 도구 전부" — **누락**: 기본 도구 memory·choices가 늘 붙고, confirm은 확인 대기가 있을 때만(`orchestrator.py:820-829`).
9. **L109–119 그림** — **누락**: Harness 문장 셋의 자리. 이전 Turns 뒤 `EARLIER_TURNS_NOTE`(이전 대화가 있으면), 이번 요청 뒤 확인 대기 목록, 이번 Turn 도구 결과 뒤 조사 한도 알림(`context.py:208-221`).
10. **L129** "System 지침은 host가 제공하는 규칙이다" — **틀림**
    - 코드: host 지침 뒤에 Harness 공통 지침 `AGENT_INSTRUCTION`(조사·근거·인용)을 붙인 한 system 메시지(`openrouter.py:485-489`).
    - 같은 이유로 L205 "모델 어댑터는 호출만 한다", L227 "System 지침에는 조사 방식이 들어 있다"도 고친다(16·17번).
11. **L129 Host 문서** "Harness는 저장·수정하지 않으며" — **누락**
    - 코드: Orchestrator는 쓰지 않지만 `plan` 도구가 host `PlanStore`에 새 버전을 저장한다. PIA는 계획이 없어도 "투자 계획: 아직 없음"을 줘서 항상 들어간다.
12. **L133** "확인 대기 목록과 한도 알림 같은 Harness 문장은 저장하지 않는다" — **누락**
    - 코드: Context에만 넣는 문장(이전 Turns 끝, 확인 대기, 한도 알림, 받은 시각)은 저장하지 않는다. 도구 결과에 붙인 Harness 문장(확인 질문 첨부 안내·action ID, "Not run" 사유, 후보 안내)은 도구 결과로 저장된다.
13. **L133 답변 뒤 붙는 글** — **누락**: 확인 없는 동작의 결과 문장도 답변 뒤에 붙는다(그 도구 결과로는 저장, `assistant_message`에는 없음).

### §4 Compaction

14. **L143** "usage.total_tokens; 없을 때만 추정치" — **누락**: 응답 모델 ID가 설정한 `model_id`와 다를 때도 추정치(요청 JSON UTF-8 바이트)를 쓴다(`compaction.py:69`).
15. **L164**
    - "trigger_tokens가 입력 예산 이상이면 설정 오류다" — **누락**: 생성 시가 아니라 답변 뒤 확인 때마다 실패해 `compaction_failed`로 보고된다.
    - "압축 직후 Context는 … 시스템 지침·도구 정의 + Memory + Summary + 최근 Turns" — **누락**: Host 문서(투자 계획, 최대 약 6천 자)와 Memory 최대 4천 자.

### §5 Memory

16. **L179** memory 도구를 부르는 조건 괄호의 "투자 계획에 속하는 것은 제외" — **틀림(위치)**: 도구 설명에는 이 규칙이 없고, 작성 단계 지침 `PLAN_MEMORY_RULE`이 거른다. 작성 단계 줄로 옮긴다.
17. **L182·L193** "답변이 확정된 뒤" — **누락**: 사용자에게 보내기 전에 한다(변경 안내를 답변 끝에 붙이려고). 사용자는 이 호출을 기다린다.
18. **L183·L193** "PIA의 금융 투자용 Memory 지침" — **누락**: 뒤에 Harness 출력 규칙 `MEMORY_OUTPUT_INSTRUCTION`(2,000자 목표, 유지·교체·비우기)이 붙는다.
19. **L197** "대화 문맥 전체를 초기화하는 별도 기능은 제공하지 않는다" — **낡음**: `clear_if_idle(user_key)`가 있다(L380도 언급). "memory 도구에는 전체 초기화가 없고, 대화 초기화는 host가 저장된 대화를 지우기 전에 `clear_if_idle`을 부른다."

### §6.0 공통 규칙 / §6.1 답변

20. **L205** "모델 어댑터는 호출만 한다" — **틀림**: 어댑터가 `AGENT_INSTRUCTION`과 Memory·Summary 출력 지침도 갖는다.
21. **L227** "System 지침에는 조사 방식이 들어 있다" — **틀림**: "Harness가 host System 뒤에 붙이는 `AGENT_INSTRUCTION`에 …".
22. **L229 (그리고 L290)** 링크 허용 범위 — **틀림**
    - 코드: 이전 Turn의 저장된 도구 결과도 허용 범위(TOOL_RESULT)이고, Host 문서는 제외(`orchestrator.py:336-343`).
    - 수정: "사용자 메시지, 이전 답변, 이전·이번 Turn의 도구 결과에 없던 링크는 지운다(Summary·Memory·Host 문서 제외)".
23. **§6.1** — **누락**: 답변 출력 상한 `ModelTokenBudget.response_tokens`(기본 4,096). 잘리면 재시도 없이 생성 실패.

### §6.2 읽기 도구

24. **L237** "반복 탐색은 1번의 세 한도가 제한한다" — **낡음**: 두 한도(읽기 20회·조사 90초).
25. **L243·L256** 링크 중복 제외 — **누락**: web_search만이 아니라 news_search에도 적용. 전부 중복이면 "No new candidates; every returned link was already listed."
26. **L288** "`max_attempts` 2 이상, 기본 1" — **틀림**: 허용 값은 1·2뿐(그 밖은 ValueError).
27. **L290** "같은 응답의 두 호출이 같은 링크를 요청해도 한 번만 읽는다" — **틀림**: 링크와 목적(goal) 쌍으로 묶는다. goal이 다르면 두 번 읽는다.
28. **L309** "(+ 국내 종목·코스피·코스닥은 거래량·거래대금)" — **틀림**: 미국 종목도 거래량·거래대금(억 달러)이 붙는다. 없는 것은 해외 지수·지표·상품.
29. **L321** "허용 값은 `_ACTIONS` 하나에서 나오고, schema·설명·검사가 모두 이 표를 쓴다" — **틀림**
    - 코드: `by`는 `_RANKINGS`, `period`는 기간 상수에서 나온다. action·data·by·period 설명 문장은 손으로 쓴 문자열(표에서 만드는 설명은 market·macro·commodity뿐).
    - 수정: "허용 값은 `_ACTIONS`(by는 `_RANKINGS`)에서 나오고 schema enum과 검사가 쓴다. 설명 문장은 손으로 맞춘다."
30. **L325** "미국 종목은 Broker를 부르지 않고" — **틀림**: 종목 찾기는 부르고, history만 부르지 않는다.
31. **L346** "이미 확보한 국내 기간 뒤의 HTTP 실패는 부분 결과" — **틀림**: HTTP뿐 아니라 OpenDART 오류 코드·해석 실패도 같다. 확보한 기간이 없으면 "기간 없음"과 설명.
32. **L347** "모델 시나리오는 Claude가 기존 smoke_flow에 연결한다" — **낡음**: 이미 `--set financials`와 기록(2026-10-16, 10-17)이 있다.
33. **L342** — **누락**: 국내는 2015 사업연도까지 읽는다.
34. **L341** — **누락**: 미국 설비투자가 `PaymentsToAcquireProductiveAssets`로만 공시되면 "유형·무형자산 취득"으로 표시.
35. **L235–239 머리말** — **누락**: `plan_history`는 6.4.2에 있다는 안내.

### §6.4 실행 도구

36. **L355** "`needs_confirmation=False`, … host가 정함" — **틀림**: 도구 코드가 정한다. 지금 이를 쓰는 도구는 없다.
37. **L355·L364** "버튼 없이" — **낡음**: 확인 질문에는 확인·취소 보기(`CONFIRMATION_CHOICES`)가 붙는다. 누르면 그 글이 사용자 메시지가 되고, 확정은 모델의 `confirm`이 한다.
38. **L366–367** "5분 이내" — **누락**: 준비한 요청을 받은 시각부터 이번 요청을 받은 시각까지(조사·답변 시간 포함), `confirmation_ttl_seconds` 기본 300.
39. **L373** "답변이 전달되면 확인 대기는 … 버림" — **낡음(경미)**: confirm Turn은 실행 확정 때 버린다.
40. **L376** 늦은 `confirm`의 결과 문장 — **틀림(경미)**: "confirm은 Turn의 첫 호출에서만, 다시 확인받을 것"(`orchestrator.py:1505-1509`). "confirming comes first"는 같은 응답의 다른 호출이 받는 문장.
41. **L377** "없는 ID면 아무것도 실행하지 않는다" — **누락(경미)**: 모르는 ID가 하나라도 섞이면 아는 ID도 실행하지 않는다.
42. **L397–404 주문 흐름** — **누락**: 준비 중 Broker 오류(409 외 상태·무응답·해석 실패)는 결과 문장이 아니라 `TOOL_FAILED`로 Turn을 끝낸다(읽기 도구 `broker`와 다름).
43. **L416** "시장가는 사용자가 요청할 때만 쓴다" — **누락(일관성)**: "(국내만. 미국은 요청해도 현재가 지정가로 준비)". L412 시장가 예시는 국내 전용이라고 적는다.
44. **L420** UNKNOWN 문장의 범위 — **누락**: 주문번호 없는 ACCEPTED, 200이 아닌 응답·해석 실패도 같은 문장.
45. **L422** "취소·체결 조회·잔고는 나중에 한다" — **낡음**: 잔고·매수 가능은 `broker` account로 본다. 취소·체결 조회만 아직 없다.
46. **L430 plan** — **누락**: 실행 결과 문장 "투자 계획을 저장했습니다 (바뀐 칸: …).", 그 사이 계획이 저장됐으면(`save_plan` False) 저장하지 않고 다시 요청하라고 함, 저장 실패 문장.

### 기타: 지원 계약

47. **L440** — **누락**: 결과 형식 `ConversationResult(status, final_text, turn_id, memory_failed, compaction_failed)`와 상태 7개(DELIVERED·SUPERSEDED·ABANDONED·GENERATION_FAILED·DELIVERY_FAILED·PERSISTENCE_FAILED·TOOL_FAILED). README에는 둘만 나온다.
48. **L440** "모델 요청의 재시도는 제한적" — **누락**: `max_attempts` 1·2(기본 1 = 재시도 없음), 시간 초과·연결 오류·408·5xx·형식 오류일 때 1초 뒤 한 번.
49. **L441** — **누락**: 생성·전달에 실패한 입력은 남아 다음 메시지와 합쳐 다시 처리된다(`clear_if_idle`로 비움).
50. **지원 계약 전체** — **누락**: host 진입점. `ConversationOrchestrator(...)` + `await submit(user_key, message, accepted_at) -> ConversationResult`, 모델 `OpenRouterModelAdapter`, 저장소 `ConversationStore`(검증 `testing.ConversationStoreContract`, 참조 구현 `testing.InMemoryConversationStore`).

### 코드 쪽에서 발견한 것 (README 수정과 별개, 따로 다룰 후보)

- **A. 실행 뒤 답변 호출에 확인 대기 알림이 남는다**
  - `orchestrator.py:1139-1147`: confirm으로 실행한 뒤의 답변 호출에도 Turn 시작 때 만든 "Actions … are waiting for the user's confirmation. Call confirm …"(`note`)이 그대로 들어간다. 그 호출에는 도구가 없다.
  - 지금 시나리오에서는 답이 맞게 나왔다(execution 세트 PASS). 하지만 실행된 동작을 "아직 대기 중"으로 읽을 여지가 있다.
  - 수정은 그 호출에 `note`를 넘기지 않는 한 줄이다.
- **B. 전달 실패(DELIVERY_FAILED) 뒤 다시 처리하면 Memory 반영이 한 번 더 될 수 있다**
  - Memory 반영과 확인 없는 동작은 전달 전에 실행되는데, DELIVERY_FAILED는 입력을 남겨 다음 메시지와 합쳐 다시 처리한다.
  - 확인 없는 동작은 지금 쓰는 도구가 없다. Memory는 같은 내용을 다시 쓰는 정도다.
- **C. 낡은 주석**
  - `orchestrator.py:42-44`: 조사 한도에 "a request too large to send with tools"가 남아 있지만 그 검사는 없다.
  - `broker_read.py:93`: 설명까지 `_ACTIONS`에서 나온다는 같은 과장이 있다.

## Codex 검토 (2026-10-17)

방향은 맞다. 수정안에서 바로잡을 것 5건, 문구를 좁힐 것 5건.

- 14: usage가 있으면 그 값을 쓴다(모델 ID가 달라도). "usage가 없으면 바이트 추정"까지만 적는다.
- 29: 내부 이름을 늘어놓지 말고 "인자 스키마와 실행 검사를 맞춰 관리하며, 설명 문장은 따로 쓴다".
- 31: 모든 해석 실패가 부분 결과를 남기지는 않는다. "처리 가능한 제공기관 오류는 확보한 결과와 함께 알린다".
- 46: 다른 계획이 저장됐다는 이유만으로 실패하지 않는다. 실행 순간의 최신 계획에 합치고, 새 버전 저장이 충돌해 `save_plan`이 False일 때 다시 요청하게 한다.
- 48: 501은 재시도하지 않고, 형식 오류도 모두는 아니다. "기본 1회, 설정하면 재시도 가능한 실패에 한 번 더"만 적는다.
- 11: "Orchestrator는 전달받은 문서 자체를 저장·수정하지 않는다". plan의 저장은 §6.4.2.
- 19: `clear_if_idle`은 메모리 상태만 비운다. 저장된 대화 삭제는 host 몫.
- 23: 실행 뒤 답변 호출이면 잘려도 고정 실행 결과를 전달한다.
- 33: "2015 사업연도 보고서까지 요청한다"(비교 열에 그 전 값이 나올 수 있다).
- 50: `submit`은 키워드 인자로.
- 묶어 반영: 3·7·13·17(처리 순서), 8~12·20~22(Context·지침·저장 경계), 26·48(재시도). 25·40·44·46의 정확한 문구는 코드·테스트에 둔다.
- 목록 밖: §6.1 Telegram 변환 설명은 "채널에 맞게 표현하는 것은 host 몫"까지만. 32를 고칠 때 "PIA 연동은 검증하지 않았다"도 지금 상태에 맞춘다.
- A: 고칠 만하다(가짜 실행으로 재현). 실행 뒤 답변에서 대기 알림을 뺀다. B: 새 장치 없이 README에 "전달 실패가 이미 끝난 Memory 변경을 되돌리지 않는다"고 적는다. C: 주석을 실제 규칙에 맞춘다.
- Codex 직접 검증: 관련 테스트 183개·subtest 108개 통과, 14·A·B는 가짜 응답으로 확인.

## 반영 (Claude, 2026-10-17)

- README: 위 검토대로 고쳤다. 50건을 하나씩 늘리지 않고 해당 문장을 바꾸거나 한 줄로 묶었다. 25·40·44·46은 규칙만 남겼다.
- A: `orchestrator.py` `_answer_after_execution`이 Turn 시작 때의 확인 대기 알림을 받지 않는다. `test_an_order_waits_for_confirmation_in_the_next_turn`에 실행 뒤 답변 호출에 이번 Turn의 Harness 문장이 없다는 검사를 더했다(이전 코드에서는 실패).
- C: `orchestrator.py`의 조사 한도 주석(두 한도), `broker_read.py`의 `_ACTIONS` 주석(enum·검사에 쓰고 설명은 손으로).
- 확인: 단위 테스트 244개 통과.
- 다음: Codex 구현 검토 → 병합 → v0.7.16 릴리스 → PIA 핀 → bot-only 배포.
