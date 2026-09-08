# PIA Harness Agent Instructions

작업 전 `/opt/pia-harness/README.md`, 현재 기능 계획 및 관련 코드·테스트를 읽는다. 프로젝트 루트는 `/opt/pia-harness`다.

- 작업 전후 branch와 `git status`를 확인하고, 다른 Agent의 미커밋 변경이 있으면 중단해 공유한다. `main`은 `git pull --ff-only origin main`으로 최신화하고 진행 중인 기능 브랜치의 push된 이력은 재작성하지 않는다.
- 비밀정보·API 키·계좌정보·실제 사용자 데이터는 Git, 문서, 로그 또는 명령 출력에 기록하지 않는다.
- 하나의 기능은 같은 브랜치에서 계획 → Claude 계획 검토 → Codex 구현 → Claude 최종 검토 → Codex의 `main` 병합까지 진행한다. Claude는 `main`을 병합하지 않는다.
- Codex가 검토 대상 branch·commit을 push하면 사용자가 메신저로 AWS의 Claude Code에 검토를 요청한다. 별도 요청이 없으면 Codex가 다른 Claude 실행 경로를 찾거나 새 task를 만들지 않는다.
- 구현 시 변경 영역 테스트를 먼저 실행하고 commit 전 전체 Python suite를 한 번 실행한다. 계약 변경은 현재 계획과 `README.md`에 반영한다.
- 실제 모델 연결, `pia-agent` 통합, AWS 또는 배포 변경은 별도 사용자 승인 없이는 수행하지 않는다.
