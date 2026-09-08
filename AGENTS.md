# PIA Harness Agent Instructions

작업 전 `/opt/pia-harness/README.md`, 변경 대상 `plans/*.md` 및 관련 소스·테스트를 읽고 현재 계약과 구현 상태를 확인한다. PIA 제품 원칙이 필요한 작업은 `/opt/pia/README.md`도 확인한다.

- 프로젝트 루트는 `/opt/pia-harness`다.
- Harness는 PIA의 대화·Memory 계약을 작게 검증하는 저장소다. `pia-agent` 통합, 실제 모델 네트워크 연결, AWS 인프라 및 배포는 명시된 범위를 넘어 자동으로 수행하지 않는다.
- 코드와 테스트는 Git으로 재현 가능하게 관리하고, 비밀정보·API 키·계좌정보·실제 사용자 데이터는 코드·문서·로그·명령 출력에 기록하지 않는다.
- 기존 사용자 변경사항을 보존하고 작업 전후 `git status`를 확인한다. 다른 Agent의 미커밋 변경이 있으면 덮어쓰거나 정리하지 말고 작업을 중단해 상태를 공유한다.
- Claude Code는 AWS에서 `/opt/pia`와 `/opt/pia-harness`의 push된 브랜치에 접근한다. Codex가 검토 대상 branch·commit을 push하고 계획 또는 handoff에 검토 요청을 기록하면 사용자가 메신저로 Claude에게 검토를 요청한다. 별도 요청이 없으면 Codex가 로컬 Claude CLI나 브라우저 실행 경로를 찾거나 새 Claude task를 만들지 않는다.
- Codex와 Claude는 기능 브랜치를 순차적으로 사용한다. Claude는 같은 브랜치에서 계획·최종 구현 검토와 필요한 수정까지만 수행하고 `main`을 병합하지 않는다.
- 하나의 기능은 같은 브랜치에서 계획 commit → Claude 계획 검토 → Codex 구현 → Claude 최종 검토 → Codex의 `main` 병합·push·main CI 확인까지 진행한다. 계획 검토가 끝났다는 이유로 구현 브랜치를 새로 만들지 않는다.
- Codex는 Claude의 blocker 없음 판정 전에는 `main`을 병합하지 않으며, main CI 성공 후에만 병합 완료 브랜치를 정리한다.
- 작업 시작 시 branch·HEAD·remote·clean 상태를 확인한다. `main`에서 작업할 때는 `git pull --ff-only origin main`으로 최신화하고, 진행 중 기능 브랜치는 이력을 임의로 재작성하지 않는다.
- 구현 중에는 변경 영역의 targeted test를 먼저 실행한다. commit 전 전체 Python suite를 한 번 실행하고, Docker build 정의가 있는 경우에만 candidate build를 한 번 실행한다.
- 기능 계약이 바뀌면 대응하는 `plans/*.md`와 `README.md`를 함께 갱신한다.
- 기능 브랜치에서는 로컬 검증을 사용하고 자동 GitHub CI는 `main` push로 실행한다. 고위험 변경이나 사용자 요청이 있을 때만 수동 CI를 실행한다.
- 배포, AWS/provider 변경, production 변경 및 외부 시스템 통합은 별도 사용자 승인 대상이다.
