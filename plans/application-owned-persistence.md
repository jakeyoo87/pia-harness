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

Protocol에는 현재 기능이 실제로 사용하는 메서드만 포함한다.

```text
get_or_create_active_session
append_completed_turn
load_context
get_summary
get_memory
replace_memory
delete_memory
load_unreviewed_turns
replace_summary
delete_turns_through
reset_active_session
delete_all_for_user
```

- 현재 메서드 이름, domain input/output과 CAS boolean 의미를 유지한다.
- Protocol은 runtime service locator나 registration framework가 아니다.
- read/write protocol을 여러 단계로 세분화하지 않는다.
- `AutomaticMemoryReviewer`, `TokenCompactor`, `ConversationOrchestrator` 생성자는
  `ConversationStore`를 받는다.
- domain/persistence 예외 중 구체 DB와 무관한 것은 persistence 계약 모듈로 이동한다.
- Harness runtime에는 특정 DB adapter를 두지 않는다.

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
- provider 호출 전 store read/create에서 발생하면 모델 호출과 delivery 없이 종료한다.
- Memory/Compaction처럼 delivery 전 durable work에서 발생하면 실패 안내를 답변에 붙이지 않고 전체
  conversation을 조용히 중단한다.
- delivery port에서 발생해도 일반 `DELIVERY_FAILED`와 구분해 `ABANDONED`로 종료한다.
- 이미 delivery가 성공한 뒤 마지막 Turn append에서 거부되면 사용자에게 보낸 답변을 되돌릴 수
  없으므로 현재와 같이 `PERSISTENCE_FAILED`로 보고하고 중복 전달하지 않는다.
- `ConversationAbandoned`는 재시도, fallback, background recovery를 유발하지 않는다.
- `reset`과 `delete_all_for_user`의 호출 여부는 애플리케이션 lifecycle이 결정한다.

Harness는 사용자가 왜 abandon됐는지 알지 않는다. `DELETION_PENDING`, suspended member, deleted
workspace 등 제품별 상태는 소비 애플리케이션 내부에만 남는다.

### 4.3 구체 DB 구현 제거

Harness runtime에서 `DynamoDBConversationStore`를 제거한다.

- Harness는 boto3 client, table 이름, IAM, physical key 또는 DynamoDB expression을 받지 않는다.
- `pk`/`sk`, `PK`/`SK`, `USER#` 같은 물리 persistence 표현을 공개 library 계약으로 고정하지 않는다.
- boto3를 runtime dependency에서 제거한다.
- 기존 DynamoDB 구현에 있던 CAS, idempotency, ordering, TTL filtering 의미는
  `ConversationStore` method contract로 문서화한다.
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
`except Exception`으로 삼켜 Memory 실패나 Compaction 실패로 바꾸지 않는다. delivery 전 발생한
abandon은 Orchestrator 최상위까지 전파되어 사용자 응답 없이 종료되어야 한다.

`ConversationResult`에 별도 제품 상태, 회원 상태, DB 이름 또는 사용자 메시지를 추가하지 않는다.

## 7. 예상 변경 파일

- `src/pia_harness/persistence.py` 신규
  - `ConversationStore` Protocol
  - DB 독립적인 persistence exceptions
  - `ConversationAbandoned`
- `src/pia_harness/memory.py`
  - concrete DynamoDB import 제거 및 Protocol 의존
  - abandon을 best-effort failure로 삼키지 않음
- `src/pia_harness/compaction.py`
  - concrete DynamoDB import 제거 및 Protocol 의존
  - abandon 전파
- `src/pia_harness/orchestrator.py`
  - Protocol 의존
  - `ABANDONED` 상태 및 phase별 처리
- `src/pia_harness/dynamodb.py`
  - runtime에서 제거하고 필요한 DB 독립 exception만 persistence 계약으로 이동
- `src/pia_harness/__init__.py`
  - 새 공개 계약 export
- `tests/test_orchestrator.py`
  - read/create, Memory, Compaction, delivery와 delivery 후 append의 abandon 의미
- `tests/test_memory.py`, `tests/test_compaction.py`
  - direct component에서 abandon 전파
- `tests/test_conversation_store.py`
  - DynamoDB adapter 검증은 제거하고 필요한 Protocol 의미는 test-only store 계약 검증으로 교체
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
- delivery가 끝난 후 Turn append veto는 `PERSISTENCE_FAILED`이며 답변을 다시 보내지 않음
- ordinary exceptions와 CAS conflict의 기존 status가 유지됨

### persistence contract test

- test-only store로 Session, Turn, Summary, Memory, reset과 전체 삭제 의미 검증
- CAS winner 보존, Turn replay, ordering, expiry filtering과 사용자 격리 검증
- Memory, Compaction과 Orchestrator가 구체 DB module 없이 같은 store contract를 공유함을 검증
- 실제 DynamoDB key, transaction과 pagination은 Harness가 아니라 이후 PIA contract test에서 검증

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
6. 기존 DynamoDB adapter와 boto3 runtime dependency 제거
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
