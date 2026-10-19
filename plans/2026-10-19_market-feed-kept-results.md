# 시장 소식 검색 결과는 Turn에 남기지 않는다 (2026-10-19)

## 배경

- `market_feed_search`(v0.7.17)는 결과(최대 50행·2만 자)를 그 Turn의 도구 기록에 그대로 저장한다. 저장된 Turn은 Compaction 전까지 다음 모든 모델 호출에 다시 들어간다.
- 실측(dev, 2026-10-18, 회원 1명의 Turn 4개·검색 7번, 내용은 보지 않고 SQL과 크기만 확인):
  - 조사 한 번이 요약 24~50행(약 5~9천 자) 뒤에 본문 9~13건(약 7~12천 자)을 읽는다.
  - "하이퍼스케일러 투자 동향" 질문은 1만 4천 자로, 그 뒤 매 호출에 약 8~10K 토큰이 더해졌다(`/status` 77K/128K).
- 이 DB는 Bot 안의 파일이라 다시 조회해도 비용이 없고 1초도 걸리지 않는다. 답변(결론·기사 링크)은 따로 저장된다. 그래서 결과까지 들고 다닐 이유가 약하다.
- 웹 본문 추출 결과는 그대로 둔다. 다시 읽으면 비용·시간이 들고, 페이지가 바뀌거나 막힐 수 있으며, 확인된 인용구가 답변의 근거이기 때문이다(사용자 결정 2026-10-19: DB 검색만 먼저).

## 설계

**규칙 하나: 읽기 도구는 Turn에 남길 짧은 사본을 따로 줄 수 있다.** 지금 검색 후보의 짧은 설명을 Turn에 남기지 않는 것과 같은 자리(`_tool_result_text`)에서 처리한다.

- `ReadToolResult`에 `kept_text: str | None = None`을 더한다. None이면 지금처럼 결과 전체를 남긴다.
- `_tool_result_text`: `kept_text`가 있으면 이번 Turn에는 결과 전체, Turn에는 `kept_text`를 남긴다(링크 후보가 있는 결과는 지금 방식 그대로).
- `market_feed_search`는 성공한 조회에만 `kept_text`를 준다:
  `market_feed_search: 35 rows (results are not kept with the Turn; run the query again if a later question needs them).`
  - SQL은 도구 호출 인자로 그대로 남으므로 모델은 지난번에 무엇을 찾았는지 안다.
  - SQL 오류·DB 없음 같은 짧은 결과는 그대로 남긴다.
- 도구 설명에 한 문장: "Results are not kept for later turns; to use them again, run a query again."

**바뀌지 않는 것**
- 그 Turn 안의 모델 호출은 결과 전체를 본다(요약 → 본문 두 단계 그대로).
- 답변과 번호 링크는 그대로 저장된다. 답변에 인용한 기사 링크는 다음 Turn에도 읽을 수 있다(URL 경계는 저장된 답변을 포함).
- 웹 검색·본문 추출·broker 등 다른 도구.

**대가**
- "아까 그 소식 자세히" 같은 후속 질문은 다시 조회한다. 그 사이 DB가 갱신되면(2시간마다) 결과가 조금 달라질 수 있다.
- 답변에 인용하지 않은 기사 링크는 다음 Turn에 바로 읽을 수 없고, 다시 조회하면 다시 나온다.

## 테스트

- 단위: `kept_text`가 있으면 이번 Turn 결과는 전체, 저장된 Turn은 `kept_text`인지(orchestrator). `market_feed_search` 성공 결과에 `kept_text`가 있고 오류 결과에는 없는지.
- 유료 시나리오(승인 필요, `smoke_flow --set market_feed`): 후속 질문 m6("아까 SK하이닉스 목표가 올린 근거 자세히")·m21이 다시 조회해 맞게 답하는지, 나머지 세트에 나빠진 것이 없는지. Context가 Turn마다 얼마나 덜 쌓이는지 기록.

## 배포

harness v0.7.18 → pia-agent 핀 → bot-only. PIA 코드 변경은 없다.

## 순서

계획 → Codex 계획 검토 → 구현 → 유료 시나리오(승인) → Codex 구현 검토 → 병합·릴리스·배포(Codex, 승인).
