# 초기 안내 시나리오 테스트 (2026-10-10)

`python -m tests.manual.smoke_flow --set onboarding|onboarding_reset|plan --model openai/gpt-6-luna --extra-prompt-file <pia ONBOARDING_PROMPT>`, 계획 pia `plans/2026-10-09_onboarding.md`. 가짜 Broker(`broker_not_connected` 단계는 409), 메모리 저장소, `seed`로 가입 완료 Turn(·기존 계획).

| 세트 | 결과 |
|---|---|
| `onboarding` 9단계 | 모두 PASS(최종 지침). 호칭 기억 → 소개 4개(정한 문구) + 계좌 제안 한 문장, 다른 질문에 뉴스로 답하고 연결하면 실시간 시세 가능하다고 한 번, 건너뛰기 설명, 질문 5개(정한 문구), 비중 안 3개 표, 고름 → 확인 한 번 → 저장 → 마무리 문구, "계좌 완료"(미연결)는 조회 후 아직 아님, 연결 후 확인 + 계획이 있으니 계획 제안 없음 |
| `onboarding_reset` 2단계 | PASS. 연결·계획이 다 있으면 끝 줄 하나("계좌 연결과 투자 계획이 준비돼 있어요…") |
| `plan` 17단계(1차) | 15 PASS, CHECK 2개는 시나리오 사정(이미 30%인 현금에 되물음 / 되돌린 글이 한 단어 달라 `same_as_version` 불일치) |

1차에서 상태별 고정 끝 줄이 겹쳐 두 개 나오던 것을 "아직 안 끝난 첫 단계를 제안하는 자연스러운 한 문장" 규칙 하나로 바꿨다. 판정 PASS는 도구·저장·주문 조건이며 문구·질문 수·마무리 시점은 사람이 읽고 확인했다.
