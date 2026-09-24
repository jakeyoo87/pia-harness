# PIA Harness Agent Instructions

작업 전 `/opt/pia-harness/README.md`, `/opt/pia-harness/docs/README.md`, 변경 대상 현재 문서 및 관련 코드·테스트를 읽는다. 과거 결정이나 검토 근거가 필요할 때만 관련 `plans/` 문서를 읽는다. 프로젝트 루트는 `/opt/pia-harness`다.

- Simple-first: 현재 요구와 확인된 실패를 해결하는 최소한의 범용 변경만 한다. 추정 가능한 예외를 미리 세분화하거나 중복 상태·추상화·재시도·인프라를 추가하지 않는다. 새 장치가 필요하면 기존 메커니즘으로 부족한 이유를 계획·검토에서 밝힌다.
- 단순화는 권한 경계, 비밀 보호, 외부 부작용의 중복 방지와 미확정 결과의 무분별한 재실행 금지 같은 필수 안전 불변식을 약화하지 않는다.
- 작업 전후 branch와 `git status`를 확인하고, 다른 Agent의 미커밋 변경이 있으면 중단해 공유한다. `main`은 `git pull --ff-only origin main`으로 최신화하고 진행 중인 기능 브랜치의 push된 이력은 재작성하지 않는다.
- 비밀정보·API 키·계좌정보·실제 사용자 데이터는 Git, 문서, 로그 또는 명령 출력에 기록하지 않는다.
- 각 작업의 구현 Agent와 검토 Agent는 계획 또는 handoff에 명시하며 사용자가 언제든 바꿀 수 있다. 최신 역할 지정이 이전 지정보다 우선한다.
- 하나의 기능은 같은 브랜치에서 계획 → 계획 검토 → 구현·수정 → 최종 검토 → 지정된 Agent의 `main` 병합까지 진행한다. 역할이 바뀌어도 새 구현 브랜치를 만들지 않는다.
- `plans/` 문서는 Asia/Seoul 기준 최초 작성일을 앞에 붙여 `YYYY-MM-DD_<lowercase-kebab-title>.md` 형식으로 작성한다.
- Claude Code 작업은 push된 브랜치를 사용하며 사용자가 메신저로 요청한다. 별도 요청이 없으면 다른 Agent가 별도의 Claude 실행 경로나 새 task를 만들지 않는다.
- 구현 시 변경 영역 테스트를 먼저 실행하고 commit 전 전체 Python suite를 한 번 실행한다. 계약 변경은 대응하는 `docs/0N-*.md`와 필요 시 `README.md` 및 현재 기능 계획에 반영한다.
- 실제 모델 연결, `pia-agent` 통합, AWS 또는 배포 변경은 별도 사용자 승인 없이는 수행하지 않는다.
