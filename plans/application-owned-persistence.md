# Application-owned persistence boundary

## 1. 목적

`pia-harness`를 특정 서비스의 회원 DB와 DynamoDB key schema에 결합되지 않는 재사용 라이브러리로
정리한다.

현재 conversation 정책과 동작은 유지한다.

- Session과 완료 Turn
- Rolling Summary compaction
- 자동·명시적 장기 Memory
- Context assembly
- 같은 사용자의 generation supersede와 durable commit 직렬화
- OpenRouter structured-output Adapter

이번 변경의 핵심은 위 기능이 구체 `DynamoDBConversationStore`에 의존하지 않고, 소비 애플리케이션이
주입한 persistence 계약에 의존하도록 만드는 것이다.

계획부터 구현·수정·최종 검토까지 `codex/application-owned-persistence` 한 브랜치에서 진행한다.

이번 handoff의 역할은 다음과 같다.

- 계획 작성, 구현 및 검토 반영: Codex
- 계획과 최종 구현 독립 검토: Claude
- 실제 provider, AWS, 배포 및 공개 package 변경 승인: 사용자

## 2. 합의한 책임 경계

### 소비 애플리케이션이 소유하는 것

- 사용자가 누구인지와 stable opaque `user_key`
- 사용자의 존재 여부, ACTIVE/탈퇴 중/삭제 완료 같은 lifecycle
- 어떤 DB와 table을 사용할지
- 물리 key 이름, IAM, encryption, TTL 활성화와 운영
- 각 persistence 요청을 실행할지 abandon할지
- Telegram 등 채널 수신·전달과 애플리케이션 전체 orchestration
- `/reset`과 회원 탈퇴 시점
- model ID, token budget, timeout, retry와 secret

### Harness가 소유하는 것

- Session, Turn, Summary, Memory domain record와 불변식
- 어떤 conversation 데이터를 언제 읽고 쓸지
- Memory review와 Compaction 정책
- Context 구성과 token budget 적용
- 동일 사용자의 generation 취소·결합과 commit 순서
- 저장소와 delivery port 호출 및 결과 상태

`ConversationOrchestrator`는 PIA 전체를 지휘하는 application orchestrator가 아니라, 주입된 I/O를
사용해 한 conversation의 내부 순서를 관리하는 library engine이다. 이름 변경이나 PIA 기능 흡수는
이번 범위가 아니다.

Harness는 PIA 모듈을 import하거나 callback으로 PIA 서비스를 찾아 호출하지 않는다. 대신 Harness가
정의한 좁은 persistence port를 소비 애플리케이션이 구현해 생성자에 주입한다.

```text
pia-harness
  ConversationStore protocol
            ▲
            │ implements and injects
            │
consuming application
  membership/lifecycle-aware store
  DynamoDB, RDS, local DB, test fake, ...
```

## 3. 현재 문제

현재 다음 library components가 구체 구현을 직접 import하고 생성자 타입으로 사용한다.

- `AutomaticMemoryReviewer`
- `TokenCompactor`
- `ConversationOrchestrator`

```python
from .dynamodb import DynamoDBConversationStore
```

이 결합은 실제 동작에 필요한 것이 아니다. Orchestrator 테스트는 이미 같은 메서드를 가진 fake store로
동작한다. 하지만 공개 계약이 없기 때문에 소비 애플리케이션이 회원 상태를 반영한 store를 구현할 때
필요한 메서드, CAS 의미와 실패 의미가 명확하지 않다.

또한 현재 `DynamoDBConversationStore`가 boto3 client, table 이름과 물리 key `pk`/`sk`를 직접
소유한다. 이 책임은 conversation 정책이 아니라 소비 애플리케이션의 persistence 구현에 속한다.

## 4. 제안하는 최소 설계

### 4.1 `ConversationStore` protocol

새 persistence 계약 모듈을 추가하고, conversation 구성요소가 이 Protocol만 참조하게 한다.

Protocol에는 Harness 구성요소가 실제로 호출하는 메서드만 포함한다. 현재 호출되는 메서드는 9개다.

```text
get_or_create_active_session   Orchestrator
append_completed_turn          Orchestrator
load_context                   Orchestrator, TokenCompactor
get_memory                     Orchestrator, AutomaticMemoryReviewer
replace_memory                 Orchestrator, AutomaticMemoryReviewer
load_unreviewed_turns          AutomaticMemoryReviewer
replace_summary                TokenCompactor
delete_turns_through           TokenCompactor
reset_active_session           Orchestrator
```

`get_summary`, `delete_memory`, `delete_all_for_user`는 어떤 Harness 구성요소도 호출하지 않으므로
Protocol에 넣지 않는다. 특히 `delete_all_for_user`는 애플리케이션의 탈퇴 작업이며 애플리케이션 store가
자체 메서드로 가진다.

- 현재 메서드 이름, domain input/output과 CAS boolean 의미를 유지한다.
- Protocol은 runtime service locator나 registration framework가 아니다.
- read/write protocol을 여러 단계로 세분화하지 않는다.
- `AutomaticMemoryReviewer`, `TokenCompactor`, `ConversationOrchestrator` 생성자는
  `ConversationStore`를 받는다.
- domain/persistence 예외 중 구체 DB와 무관한 것은 persistence 계약 모듈로 이동한다.
- Harness runtime에는 특정 DB adapter를 두지 않는다.

#### Store 구현이 지켜야 하는 의미

현재 Memory와 Compaction 정책은 아래 의미에 의존한다. 지금은 Harness 소유 `dynamodb.py`가 보장하지만,
구현이 애플리케이션으로 넘어가면 이 목록과 공유 contract suite(§8)가 유일한 보장이 된다.

- `load_context`와 `load_unreviewed_turns`는 조건에 맞는 모든 Turn을 `turn_id` 오름차순으로 반환한다.
  pagination 등으로 일부를 빠뜨리면 안 된다.
- `load_context`는 `summary.through_turn_id`보다 큰 Turn만, `load_unreviewed_turns`는 `after_turn_id`보다
  큰 Turn만(`None`이면 전부) 반환하며 후자는 Summary 경계를 무시한다.
- 두 load 모두 `expires_at <= now`인 Turn을 반환하지 않는다. `expires_at` 계산과 보존 기간은 store 소관이다.
- `replace_memory`와 `replace_summary`는 관찰한 경계가 바뀌었으면(첫 쓰기에서는 item이 이미 있으면)
  `False`를 반환하고 winner를 덮어쓰지 않는다.
- `append_completed_turn`은 같은 key와 완전히 같은 내용의 재전송을 성공으로, 같은 `turn_id`의 다른 내용을
  `TurnConflictError`로 처리한다.
- `get_or_create_active_session`은 사용자당 하나의 active session만 만들고 동시 생성 시 winner를 반환한다.
- `reset_active_session`은 기대한 session ID가 바뀌었으면 `SessionConflictError`를 발생시킨다.
- 모든 연산은 해당 `user_key` 밖의 데이터를 읽거나 바꾸지 않는다.

순서 의미 하나는 store에만 맡기지 않는다. `TokenCompactor`는 `covered[-1].turn_id`까지
`delete_turns_through`로 삭제하므로 순서가 틀린 Turn 목록을 받으면 요약되지 않은 Turn을 영구 삭제한다.
`AutomaticMemoryReviewer`도 `turns[-1].turn_id`를 경계로 쓰고 명시적 Review의 stale 판정에서 Turn ID
tuple을 비교한다. 따라서 두 구성요소는 store에서 받은 Turn이 strictly increasing이고 자신의 경계보다
큰지를 write/delete 전에 검사하고, 위반하면 fail closed 한다. `PromptContextAssembler`에 이미 같은 검사가
있으므로 그것을 재사용한다. 누락은 Harness가 감지할 수 없으므로 contract suite로 고정한다.

`ConversationAbandoned`는 `SessionStoreError`를 상속하지 않는다. DB 오류를 처리하는 어떤 handler도
lifecycle veto를 저장소 실패로 잡지 않게 하기 위해서다.

### 4.2 애플리케이션의 정상적인 abandon

소비 애플리케이션은 persistence operation 직전에 사용자가 없거나 탈퇴 중임을 알 수 있다. 이는 장애나
CAS 충돌과 다른 정상적인 lifecycle 결정이다.

library에 하나의 명시적인 중단 신호를 둔다.

```text
ConversationAbandoned
OrchestratorStatus.ABANDONED
```

- 애플리케이션 store 또는 delivery port는 더 이상 해당 conversation을 진행하면 안 될 때
  `ConversationAbandoned`를 발생시킬 수 있다.
- 어느 단계에서 발생하든 규칙은 하나다. 남은 model 호출, Memory·Compaction, delivery와 write를 하지
  않고, 실패 안내를 붙이지 않으며, pending batch를 비우고 `ABANDONED`로 종료한다. 재시도, fallback,
  background recovery는 없다.
- pending batch를 비우는 것이 핵심이다. 현재 `DELIVERY_FAILED`는 batch를 남겨 다음 입력과 함께 다시
  전달하므로, delivery port의 veto가 그 경로를 타면 탈퇴 중인 사용자에게 같은 batch를 다시 보낸다.
- delivery가 성공한 뒤 마지막 Turn append에서 거부돼도 `ABANDONED`다. 중복 전달 방지라는 목적은 batch를
  비우는 것으로 똑같이 달성되며, 이를 `PERSISTENCE_FAILED`로 보고하면 탈퇴와 겹친 응답이 활성 회원의
  실제 저장 실패와 구분되지 않는다. `final_text`는 `DELIVERY_FAILED`에도 채워지므로 전달 여부 신호가
  아니며, 사용자가 답변을 받았는지는 애플리케이션 delivery port가 이미 안다.
- `reset` 중 veto는 호출자에게 전파하되 `reset`은 `finally`에서 per-user 상태를 복구한다. 지금은
  `get_or_create_active_session`이나 `reset_active_session`의 예외가 복구 블록을 건너뛰어
  `reset_requested=True`가 남고, 이후 그 사용자의 모든 `submit`이 process 재시작 전까지 `SUPERSEDED`를
  반환한다. 기존 DB 오류에도 있던 결함이지만 veto는 이를 정상 경로로 만든다.
- `reset`과 `delete_all_for_user`의 호출 여부는 애플리케이션 lifecycle이 결정한다.

Harness는 사용자가 왜 abandon됐는지 알지 않는다. `DELETION_PENDING`, suspended member, deleted
workspace 등 제품별 상태는 소비 애플리케이션 내부에만 남는다.

### 4.3 구체 DB 구현 제거

Harness runtime에서 `DynamoDBConversationStore`를 제거한다.

- Harness는 boto3 client, table 이름, IAM, physical key 또는 DynamoDB expression을 받지 않는다.
- `pk`/`sk`, `PK`/`SK`, `USER#` 같은 물리 persistence 표현을 공개 library 계약으로 고정하지 않는다.
- boto3를 runtime dependency에서 제거한다.
- 기존 DynamoDB 구현에 있던 CAS, idempotency, ordering, completeness, TTL filtering 의미는
  `ConversationStore` method contract로 문서화하고 공유 contract suite로 고정한다(§4.1, §8).
- 실제 DynamoDB 구현과 DynamoDB Local contract test는 PIA 저장소로 이동한다.
- Harness unit test는 test-only in-memory/fake store로 동일한 정책과 Orchestrator 순서를 검증한다.
- test-only store는 package의 production adapter로 export하지 않는다.

이는 DynamoDB 기능을 다른 이름으로 감싸 Harness에 남기는 작업이 아니다. Harness는 persistence를
요청할 뿐이며 실제 접근은 항상 소비 애플리케이션 코드가 수행한다.

## 5. PIA가 이후 구현할 수 있는 방식

이 저장소에서는 PIA 코드를 구현하지 않지만, 새 계약이 다음 사용법을 가능하게 해야 한다.

```text
PIA 일반 요청
  → membership gate에서 ACTIVE 확인
  → Harness submit
  → Harness가 PIAConversationStore에 read/write 요청
  → store가 operation 시점의 회원 상태 확인
      ACTIVE: 저장
      missing/DELETION_PENDING: ConversationAbandoned
```

단순한 `상태 조회 → 별도 write`는 그 사이 상태가 바뀌는 race가 있다. PIA가 동일 DynamoDB table을
사용한다면 write operation에서 Member ACTIVE ConditionCheck와 conversation mutation을 하나의
transaction으로 묶을 수 있어야 한다. 이 원자성은 PIA store 구현 책임이며 Harness가 Member key나
status 값을 알아서는 안 된다.

이 seam으로 충분하다. Harness의 write는 모두 단일 Protocol 호출이므로 PIA는 호출마다 Member
ConditionCheck와 conversation mutation을 하나의 `TransactWriteItems`로 묶을 수 있다. 계약에는 두 가지를
명시한다.

- veto와 CAS를 구분한다. transaction이 취소되면 PIA는 `CancellationReasons`의 item 위치로 Member 조건
  실패와 conversation 조건 실패를 구분한다. Member 조건 실패는 항상 `ConversationAbandoned`이며 CAS
  `False`, replay 성공, `TurnConflictError`, `SessionConflictError`로 보고하면 안 된다. 모든
  `ConditionalCheckFailed`를 conversation 조건으로 해석하는 자연스러운 구현은 `replace_memory`를 stale로
  만들어 탈퇴 중인 회원에게 명시적 Memory 실패 안내를 전달하고, `append_completed_turn`을 replay 비교
  경로로 보낸다.
- gate는 conversation 데이터를 만들거나 유지하는 write에만 둔다. `delete_turns_through`와 reset의 이전
  Summary 삭제는 탈퇴와 같은 방향의 삭제라 조건이 필요 없고, `delete_turns_through`는 transaction의
  100개 item 제한을 넘을 수 있어 하나로 묶을 수도 없다.

따라서 Harness의 새 Protocol과 예외 계약은 애플리케이션이 다음을 구현하는 데 방해가 없어야 한다.

- 기존 PIA table의 `PK`/`SK` 사용
- `MEMBER#{uuid}` lifecycle item 조건 검사
- `USER#{uuid}` conversation item mutation
- 사용자가 없거나 탈퇴 중이면 정상 abandon
- 탈퇴 worker의 `delete_all_for_user` replay

Harness는 transaction 표현, Member partition, UUID, Cognito, Telegram 또는 탈퇴 worker를 API에
추가하지 않는다.

## 6. 실패 의미와 기존 동작 보존

기존 상태 의미는 그대로 유지한다.

- `SUPERSEDED`: 더 최신 입력이 generation을 대체함
- `CONTEXT_OVERFLOW`: 필수 입력이 budget을 초과함
- `GENERATION_FAILED`: model/context processing 실패
- `DELIVERY_FAILED`: 실제 channel delivery 실패
- `PERSISTENCE_FAILED`: delivery 후 완료 Turn 저장 실패
- `DELIVERED`: 전달과 완료 Turn 저장 성공

`ABANDONED`만 소비 애플리케이션의 명시적 lifecycle veto로 추가한다. 일반 DB 오류를
`ABANDONED`로 바꾸지 않는다.

Memory와 Compaction의 기존 best-effort 오류 처리는 유지하되 `ConversationAbandoned`를 broad
`except Exception`으로 삼켜 Memory 실패나 Compaction 실패로 바꾸지 않는다.

`memory.py`와 `compaction.py`에는 broad catch가 없다(검증용 `except (TypeError, ValueError)`만 있다).
abandon을 삼키는 위치는 모두 `orchestrator.py`에 있으며, 각 `except Exception` 앞에서
`ConversationAbandoned`를 먼저 처리한다.

- generation 단계 catch-all: 지금은 `GENERATION_FAILED`가 된다
- context overflow의 forced Memory Review와 compaction: `memory_failed`와 `CONTEXT_OVERFLOW`
- commit 단계의 due·explicit Memory Review, confirmed clear, 응답 후 forced Review와 compaction
- delivery: `DELIVERY_FAILED`
- 마지막 Turn append: `PERSISTENCE_FAILED`
- `reset`의 forced Memory Review

`ConversationResult`에 별도 제품 상태, 회원 상태, DB 이름 또는 사용자 메시지를 추가하지 않는다.

## 7. 예상 변경 파일

- `src/pia_harness/persistence.py` 신규
  - `ConversationStore` Protocol
  - DB 독립적인 persistence exceptions
  - `ConversationAbandoned`
- `src/pia_harness/memory.py`
  - concrete DynamoDB import 제거 및 Protocol 의존
  - load한 Turn의 순서·경계 검사
- `src/pia_harness/compaction.py`
  - concrete DynamoDB import 제거 및 Protocol 의존
  - `delete_turns_through` 전 load한 Turn의 순서·경계 검사
- `src/pia_harness/orchestrator.py`
  - Protocol 의존
  - §6의 모든 broad catch 앞에서 `ConversationAbandoned`를 `ABANDONED`로 처리하고 pending batch 비움
  - `reset`의 상태 복구를 `finally`로 이동
- `src/pia_harness/testing.py`
  - store 구현을 받아 §4.1의 의미를 검증하는 재사용 contract test suite
  - runtime adapter가 아닌 test artifact이며 package root에서 import하지 않는다
- `src/pia_harness/dynamodb.py`
  - runtime에서 제거하고 필요한 DB 독립 exception만 persistence 계약으로 이동
- `src/pia_harness/__init__.py`
  - 새 공개 계약 export
- `tests/test_orchestrator.py`
  - §6의 각 위치(generation read/create, overflow, commit Memory·Compaction, delivery, delivery 후
    append, reset)의 abandon 의미와 reset veto 후 다음 `submit`의 정상 동작
- `tests/test_memory.py`, `tests/test_compaction.py`
  - 지금 DynamoDB Local이 있어야만 실행되는 19개 정책 테스트를 contract suite를 통과한 test-only store로 이전
  - store가 순서가 틀린 Turn을 반환하면 write/delete 없이 fail closed
  - direct component에서 abandon 전파
- `tests/test_conversation_store.py`
  - DynamoDB adapter 검증을 제거하고 test-only store로 `testing.py` contract suite 실행
- `pyproject.toml`
  - boto3 runtime dependency 제거
- `README.md`, `docs/01-architecture.md`, `docs/02-persistence-and-data.md`,
  `docs/03-conversation-lifecycle.md`, `docs/04-model-adapter-and-integration.md`
  - application-owned persistence와 DB 없는 library 경계 및 주입 예시 갱신

구현 중 실제 import graph를 확인해 예외 순환 참조가 생기지 않도록 domain exception의 위치를
결정한다. 단순 이름 변경만을 위한 호환 alias는 실제 외부 import를 깨뜨리지 않는 범위에서만 둔다.

## 8. 검증 계획

### 순수 unit test

- fake `ConversationStore`로 Memory, Compaction, Orchestrator가 DynamoDB import 없이 동작
- 사용자별 기존 generation supersede와 commit 직렬화가 변하지 않음
- delivery 전 각 phase에서 `ConversationAbandoned` 발생 시 model/delivery/후속 write가 중단됨
- abandon은 Memory 실패 안내 또는 Compaction 실패 표시로 변환되지 않음
- delivery가 끝난 후 Turn append veto도 `ABANDONED`이며 pending batch를 비워 답변을 다시 보내지 않음
- reset 중 veto 후에도 같은 사용자의 다음 `submit`이 `SUPERSEDED`로 막히지 않음
- ordinary exceptions와 CAS conflict의 기존 status가 유지됨

### persistence contract test

- `testing.py`의 contract suite 하나를 Harness에서는 test-only store로, 이후 PIA에서는 실제 store로 실행
- fake만 통과하는 테스트는 fake를 검증할 뿐이므로 이 suite가 store 의미의 단일 출처다
- 대상: CAS winner 보존, Turn replay와 conflict, `turn_id` 오름차순, 경계의 strict `>`, 사용자 격리,
  reset conflict, 반환된 `expires_at` 기준 expiry filtering(보존 기간과 무관하게 검사)
- completeness: 합계 1 MiB를 넘는 여러 Turn을 저장한 뒤 load가 모두 반환하는지 확인해 pagination 누락 고정
- Member ConditionCheck, transaction 취소 사유 구분과 실제 key는 이후 PIA contract test에서 검증

### 최종 검증

1. 변경 영역 unit test
2. DB나 network가 필요 없는 전체 Python suite 1회
3. Ruff import/name 검사
4. `python -m compileall src tests`
5. `git diff --check`
6. Claude 최종 구현 검토

실제 PIA, AWS table, IAM, OpenRouter live call, package 공개 또는 배포는 수행하지 않는다.

## 9. 비범위와 단순성 제한

- PIA 회원 model이나 lifecycle enum을 Harness에 추가하지 않는다.
- authorization callback chain, plugin framework, repository registry를 만들지 않는다.
- distributed lock, deletion queue, scheduler, worker, outbox를 만들지 않는다.
- DynamoDB, RDS 또는 local production adapter를 Harness에 구현하지 않는다.
- automatic retry나 fallback을 persistence layer에 추가하지 않는다.
- 기존 Session/Memory/Compaction schema와 정책을 필요 없이 변경하지 않는다.
- PIA의 Telegram concurrency나 통합 코드는 이 브랜치에서 수정하지 않는다.

## 10. 구현 순서

1. Claude가 책임 경계, Protocol 범위와 abandon 의미를 검토
2. blocker가 있으면 같은 브랜치의 계획을 수정해 재검토
3. persistence Protocol과 독립 exception 정의
4. Memory, Compaction, Orchestrator의 concrete store dependency 제거
5. Orchestrator의 phase별 abandon 처리 구현
6. contract suite를 기존 `DynamoDBConversationStore`와 DynamoDB Local로 한 번 통과시켜 suite가 실제
   의미와 같음을 확인한 뒤 DynamoDB adapter와 boto3 runtime dependency 제거
7. focused test와 DB 없는 전체 검증
8. README와 current docs 갱신
9. commit/push 후 Claude 최종 구현 검토
10. blocker 없음 확인 후 사용자 승인에 따라 `main` 병합
11. 이후 PIA 통합 계획을 새 계약 기준으로 수정

## 11. Claude 집중 검토 요청

1. 하나의 `ConversationStore` Protocol이 현재 기능에 충분하면서 과도하게 넓지 않은가?
2. 구체 DynamoDB adapter와 boto3를 Harness runtime에서 완전히 제거하면서 보존해야 할 domain contract가
   빠지지 않았는가?
3. `ConversationAbandoned`와 `OrchestratorStatus.ABANDONED`가 소비 애플리케이션의 lifecycle veto를
   표현하는 가장 작은 계약인가?
4. Memory/Compaction의 broad exception 처리 중 abandon이 삼켜질 위치가 더 있는가?
5. delivery 전 abandon과 delivery 후 Turn append abandon의 서로 다른 결과가 정확한가?
6. 기존 DynamoDB test가 검증하던 CAS, replay, ordering, expiry와 격리 의미를 DB 독립 contract test가
   충분히 보존하는가?
7. 이 변경이 기존 Harness 사용자의 API나 저장 데이터와 불필요하게 호환성을 깨뜨리는가?
8. PIA가 같은 table의 Member ACTIVE 조건과 conversation write를 원자적으로 묶는 구현을 이 계약으로
   만들 수 있는가? 불가능하다면 Harness가 PIA schema를 알지 않으면서 필요한 최소 seam은 무엇인가?

## Claude 계획 검토: 2026-09-13, plan commit b3d9773

계획만 검토했고 구현이나 병합은 하지 않았다. blocker 1건과 필요한 수정 4건을 위 본문(§4.1, §4.2, §4.3,
§5, §6, §7, §8, §10)에 직접 반영했다. 책임 경계 자체는 옳고 바꾸지 않았다. Harness는 DB에 접근하지
않고 Protocol만 정의하며, 실제 접근·key·회원 상태·transaction은 PIA가 소유하고, lifecycle veto는 예외
하나와 상태 하나로 표현한다.

### Blocker: 정책이 기대는 store 의미가 검증 불가능해지고, 그중 하나는 요약되지 않은 Turn을 삭제한다

`TokenCompactor`는 `load_context` 결과를 앞쪽 covered와 뒤쪽 tail로 나누고 `covered[-1].turn_id`까지
`delete_turns_through`로 지운다. 이 삭제가 안전하려면 store가 조건에 맞는 모든 Turn을 `turn_id`
오름차순으로 반환해야 한다. 순서가 틀리면 covered 마지막 ID보다 오래된 tail Turn이, 일부가 빠지면 빠진
Turn이 요약에 들어가지 않은 채 영구 삭제된다. `AutomaticMemoryReviewer`도 `turns[-1].turn_id`를 경계로
쓰고, 명시적 Review의 stale 판정은 Turn ID tuple을 비교하므로 순서가 달라지면 매 요청이 stale이 되어
실패 안내가 붙는다. `PromptContextAssembler`는 순서를 검사하지만 두 구성요소는 검사하지 않는다.

지금은 DynamoDB Query의 sort-key 순서와 Harness 소유 pagination loop가 이를 보장한다. 계획은 구현을
PIA로 옮기면서 의미를 문서화하고 test-only store로만 검증한다고 했지만, fake만 통과하는 테스트는 fake를
검증할 뿐이다. 이 의미를 end-to-end로 고정하던 Memory 12개와 Compaction 7개 정책 테스트는 지금 DynamoDB
Local에서만 실행되고, Orchestrator의 `FakeStore`는 9개 Protocol 메서드 중 6개만 구현한다. 계획이 RDS와
local DB를 명시적으로 허용하므로 `ORDER BY` 없는 구현은 현실적인 경로다.

반영: store 의미 목록을 §4.1에 명시하고, Memory와 Compactor가 write/delete 전에 Assembler와 같은
순서·경계 검사로 fail closed 하며, `testing.py`의 contract suite 하나를 Harness의 test-only store와 PIA의
실제 store가 함께 실행한다. 1 MiB를 넘는 load로 completeness를 고정하고, 제거 전에 기존 DynamoDB store로
suite를 한 번 통과시켜 suite가 fake가 아닌 실제 의미를 담았음을 확인한다.

### 필요한 수정

1. Protocol이 3개 넓었다. Harness가 실제 호출하는 메서드는 9개이며 `get_summary`, `delete_memory`,
   `delete_all_for_user`는 어떤 구성요소도 호출하지 않는다. 계획 자신의 "실제로 사용하는 메서드만"
   규칙에 맞춰 뺐다. `delete_all_for_user`는 애플리케이션 탈퇴 작업이다.
2. abandon 처리 위치가 잘못 지정됐다. `memory.py`와 `compaction.py`에는 broad catch가 없고 삼키는 위치는
   모두 `orchestrator.py`에 있으며, 계획의 테스트 목록에서 overflow 경로와 reset이 빠져 있었다. reset에는
   store 예외가 상태 복구 블록을 건너뛰어 `reset_requested=True`가 남고 이후 그 사용자의 모든 `submit`이
   `SUPERSEDED`가 되는 기존 결함이 있으며, veto가 이를 정상 경로로 만든다. `finally` 복구를 추가했다.
3. abandon 규칙을 단계마다 나누지 않고 하나로 만들었다. delivery veto가 `DELIVERY_FAILED` 경로를 타면
   pending batch가 남아 다음 입력과 함께 다시 전달되므로 `ABANDONED`는 batch를 비워야 한다. delivery 후
   append veto를 `PERSISTENCE_FAILED`로 두면 계획 자신의 "veto와 DB 오류를 구분한다"는 규칙이 깨지고,
   중복 전달 방지라는 이유는 batch를 비우는 것으로 똑같이 충족된다.
4. transaction seam은 충분하지만 계약 두 줄이 빠져 있었다. Member 조건 실패는 항상
   `ConversationAbandoned`이고 CAS `False`, replay, conflict로 보고하면 안 되며 취소 사유 위치로 구분한다.
   gate는 데이터를 만들거나 유지하는 write에만 둔다. `ConversationAbandoned`가 `SessionStoreError`를
   상속하지 않는다는 점도 명시했다.

### 질문별 답

1. 9개로 줄이면 충분하고 넓지 않다.
2. 잃는 것은 메서드가 아니라 실행되는 보장이다. 위 blocker의 반영으로 보존한다.
3. 예외 하나와 상태 하나가 최소다. 단계별 분기를 없애 계약이 더 작아졌다.
4. 모두 `orchestrator.py`의 broad catch다. §6에 위치를 적었다.
5. 구분하지 않는다. 모든 단계에서 `ABANDONED`와 batch 비움이다.
6. test-only store만으로는 보존되지 않는다. 공유 contract suite로 보존된다.
7. 가능하다. 호출마다 transaction 하나이며, §5의 두 계약이 필요하다.
8. 과한 추상화는 없었다. 추가한 suite는 runtime이 아닌 test artifact이고, 순서 검사는 이미 있는
   Assembler 검사를 재사용한다.

### Codex 지시

수정된 본문을 기준으로 구현한다. persistence 계약과 `testing.py` suite를 먼저 만들고, suite를 기존
`DynamoDBConversationStore`와 DynamoDB Local로 통과시킨 뒤 test-only store도 통과하게 한다. 그다음
Memory·Compaction 테스트를 test-only store로 옮기고, 마지막에 `dynamodb.py`와 boto3를 제거한다. suite가
실제 store로 통과하기 전에는 DynamoDB 구현을 지우지 않는다.

## 구현 결과: 2026-09-13

Claude 수정 계획을 기준으로 다음을 구현했다.

- DB 독립 `ConversationStore` Protocol과 persistence exception을 `persistence.py`에 추가했다.
- lifecycle veto를 `ConversationAbandoned`와 `OrchestratorStatus.ABANDONED` 하나로 통일했다.
- abandon은 모든 pre-delivery 경로에서 broad failure 처리보다 우선하며 pending batch와 전체 Memory 삭제
  confirmation marker를 정리한다.
- delivery 후 Turn append가 abandon돼도 batch를 비워 재전달하지 않는다.
- reset은 store/reviewer 결과와 관계없이 `finally`에서 per-user 상태를 복구한다.
- Session, Memory, Summary, Turn의 owner, identity, boundary, expiry를 검사하고 Memory와 Compaction은 잘못된
  Turn 순서에서 write/delete 전 fail closed한다.
- `pia_harness.testing`에 재사용 가능한 `ConversationStoreContract`와 test-only
  `InMemoryConversationStore`를 추가했다.
- contract suite를 제거 전 실제 `DynamoDBConversationStore`와 DynamoDB Local에서 먼저 통과시켰다.
- 이후 `dynamodb.py`, boto3 runtime dependency와 DynamoDB 전용 테스트 설정을 제거했다.
- package version을 breaking persistence boundary에 맞춰 `0.2.0`으로 올리고 README/current docs를
  application-owned persistence 기준으로 갱신했다.

검증:

- 제거 전 기존 DynamoDB Adapter + 공유 contract suite: 11 tests passed
- 최종 DB/network-free 전체 suite: 91 tests passed
- Ruff `F401,F811,F821,F822`: passed
- `python -m compileall -q src tests scripts`: passed
- `git diff --check`: commit 직전 별도 확인
- read-only source copy에서 `pia-harness==0.2.0` wheel build/install: passed; runtime requirement는
  `httpx`만 포함

실제 PIA, AWS, OpenRouter live call, package 공개와 배포는 수행하지 않았다.
