# Compaction 크기 조정

날짜: 2026-09-30
브랜치: `claude/compaction-sizes`

## 목표

압축 직후 Context를 더 가볍게 시작한다. 최근 원문 몫은 줄이고, 오래된 대화를 담는 Summary는 늘린다.

## 변경

| 항목 | 지금 | 변경 |
|---|---|---|
| `CompactionPolicy.summary_chars` | 10,000자 | 20,000자 |
| `CompactionPolicy.tail_chars` (가장 최근 Turn 앞에 더 남기는 몫) | 100,000자 | 40,000자 |
| `trigger_tokens` | 256,000 | 그대로 |

- 가장 최근 Turn은 지금처럼 크기와 상관없이 항상 남는다.
- Summary 출력 상한(`max_tokens`)은 지금 규칙대로 글자 한도의 두 배, 즉 40,000이 된다.
- 압축 직후 Context는 약 13만 자에서 약 7만 자로 줄어든다. 내역은 지침·도구 약 6천 자, Memory 목표 2천 자, Summary 최대 2만 자, 최근 Turns 4만 자, 가장 최근 Turn이다.
- 조사 Turn 저장본이 1만~1.5만 자이므로, 원문으로 남는 앞 Turn은 약 7~10개에서 2~3개로 줄어든다.

## 이유

- 사용자 결정(2026-09-30): Summary 최대 2만 자, 최근 Turns 4만 자.
- Context가 작을수록 매 호출이 가볍다. 긴 Context에서 모델이 예전 질문에 답하던 혼동 위험도 줄어든다.
- 대가: 조사 Turn 2~3개보다 오래된 도구 기록(페이지 요약·인용·링크)은 Summary에 남지 않는다. Summary는 요청과 답변만으로 쓴다. 필요하면 AGENT_INSTRUCTION에 따라 다시 찾는다.

## 범위

- harness: `compaction.py` 기본값 두 개, `tests/test_compaction.py` 기본값 테스트, README Compaction 절.
- 로직, 저장 형식, PIA 인터페이스는 바뀌지 않는다. PIA는 `CompactionPolicy()` 기본값을 쓰므로 새 harness 버전만 받으면 된다.
- PIA README의 Compaction 줄(100,000자·10,000자)은 PIA 반영 때 함께 고친다(Codex).
- 버전 번호와 릴리즈는 Codex가 정한다.

## 검증

- harness 전체 단위 테스트, ruff.
- 유료 시나리오 측정은 하지 않는다. 실제 압축은 운영에서 `compaction_failed`와 Summary 길이로 관찰한다(기존 수용 항목).
