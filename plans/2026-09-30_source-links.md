# 출처를 번호 링크로 · 과거 대화 사용 지침

브랜치 `claude/source-links` (기준 main `ab0ca0a`, 0.6.0 미릴리스). 사용자와 대화로 정했다.

## 배경

- 재측정(`tests/manual/records/2026-09-30_turn-tools-remeasure.md`)에서 답변 6/33개에 출처 목록이 두 번 나왔다. 모델이 답변 끝에 직접 "출처" 목록을 쓰고, Harness가 그 링크를 `[n]`으로 바꾼 뒤 자기 목록을 또 붙였다. 모양은 번호만 남은 줄(`[1]`)과 언론사 이름 + 번호(`뉴시스 [2]`) 두 가지였다.
- 원인: Harness는 모델의 `<url>`을 `[n]`으로 바꾸고 끝에 "출처" 목록을 붙여 보낸다. 이 최종본이 Turn에 저장되어 다음 Turn Context에 "모델이 예전에 쓴 답변"으로 들어간다. 지침은 "`<url>`만 쓰고 목록은 쓰지 마라"인데 모델이 보는 자기 답변은 모두 목록으로 끝나므로, 누적될수록 예시를 따른다(누적 없던 측정 0건 → 누적 3건 → 6건).
- 원칙: **저장되어 모델에게 다시 보이는 답변은 모델이 그대로 써도 문제없는 모양이어야 한다.** ChatGPT·Perplexity·Claude API도 출처를 본문 글자가 아니라 따로 다루고, 모델이 보는 과거 답변은 모델이 쓴 모양이다.
- 답변 끝 목록을 지우는 좁은 규칙은 실제 6건 중 4건만 잡았고 원인이 남아 쓰지 않는다.

## 결정 사항 (사용자)

1. 출처는 **번호에 링크를 건 Markdown 링크**로 문장 안에 둔다: `기판 비중은 18.75%입니다 [1](https://…)`. 답변 끝의 "출처" 목록은 붙이지 않는다.
2. 목록의 기사 제목은 사라진다. 사용자는 번호를 눌러 확인한다.
3. Telegram 표시는 PIA 연동 때 한다. PIA는 지금 일반 텍스트로 보내므로(`parse_mode` 없음), Markdown을 Telegram 서식(HTML)으로 바꿔 보내야 링크·굵게가 제대로 보인다. 긴 답변을 나눌 때 링크·서식이 잘리지 않게 한다. 봇은 PIA 연동 전까지 0.5.0이라 사용자 영향은 없다.
4. 과거 대화를 쓰는 규칙을 지침(`AGENT_INSTRUCTION`)에 두 문장 더한다. 경계선 한 줄은 위치만 표시하고, 규칙은 지침에 한 번 둔다(항상 맨 앞이라 캐시 영향 없음).

## 변경

### 1. 출처 변환 (`_with_sources`)

지금 규칙(허용 링크만 남김, 처음 나온 순서로 번호, 같은 링크는 같은 번호, 모델이 쓴 `[n]` 제거, 코드 그대로, 지우고 나서 비면 실패)은 그대로 두고 **출력 모양만** 바꾼다.

| 모델이 쓴 것 | 지금 | 바뀐 뒤 |
|---|---|---|
| `18.75% <https://a>` | `18.75% [1]` + 끝 목록 | `18.75% [1](https://a)` |
| 맨 URL `https://a.` | `[1].` + 끝 목록 | `[1](https://a).` |
| `[ETF쇼핑](https://a)` | `ETF쇼핑 [1]` + 끝 목록 | `ETF쇼핑 [1](https://a)` |
| `[3](https://a)` (과거 답변 흉내) | `3 [1]` + 끝 목록 | `[1](https://a)` (글자가 숫자뿐이면 번호로 다시 매김) |
| 허용되지 않은 링크 | 지움(Markdown은 글자만) | 같음 |
| 모델이 쓴 `[2]`(링크 없는 번호) | 지움 | 같음 |

- 답변 끝 목록과 `SOURCES_HEADING`을 없앤다. 제목을 쓰지 않으므로 `_with_sources`의 제목 인자와 `listed_urls`의 제목 값은 필요 없다(`listed_urls`는 검색 후보 중복 표시용 링크 집합으로 남는다).
- 저장되는 답변도 같은 모양이다. 링크 읽기 경계(`_conversation_urls`)는 답변 속 URL을 계속 읽는다(`)`에서 끊기는 기존 규칙 그대로).
- 모델이 과거 답변을 흉내 내 `[n](url)`을 써도 위 표대로 다시 매겨지므로 번호가 어긋나지 않는다. 모델이 직접 목록을 쓰는 경우는 Harness 목록이 없으니 중복이 되지 않는다.

### 2. 지침 (`openrouter.py` `AGENT_INSTRUCTION`)

- 출처 문장 수정: "the application turns the links into numbers and adds the list" → 링크를 번호 링크로 바꾼다는 뜻으로.
- 과거 대화 두 문장 추가:
  ```
  Earlier turns are finished; answer only the current request, reusing earlier results when they still hold.
  Facts that change over time, such as prices or the latest news, reflect when they were found; check them again when the current request depends on them.
  ```
- 이유: 과거 Turn을 넣어서 생기는 문제는 ① 과거 질문에 답함(경계선으로 대응), ② 과거 답변 모양 흉내(이번 1로 대응), ③ 과거 조사 결과를 지금 사실처럼 씀(대책 없음)이다. ③은 도구 기록을 다시 쓰게 되면서 생길 수 있다. 다른 Harness도 날짜 + "바뀌는 정보는 다시 확인" 지침으로 다룬다. 날짜는 이미 이번 요청 앞에 넣는다(`[Received … KST]`).

### 3. README

- 6.1 "출처 번호" 문단을 번호 링크 방식으로 고친다(목록·제목 설명 삭제, 흉내 낸 번호 링크 재번호, Telegram 표시는 PIA 몫).
- 3장에 원칙 한 줄: 저장되어 다시 보이는 답변은 모델이 써도 되는 모양으로 둔다.

### 지켜볼 것 (이번에 바꾸지 않음)

- Harness가 답변 끝에 붙이는 다른 문구(Memory 실패 알림, 확인 만료 알림, 주문 확인 질문)도 과거 답변에 남아 같은 흉내 위험이 있다. 아직 사례가 없고, 주문 확인 질문은 다음 Turn의 "응"을 해석하는 데 필요하므로 그대로 둔다.

## 테스트

- 위 표의 여섯 경우(기존 `test_answer_links_become_numbered_sources`를 고쳐 포함).
- 같은 링크 두 번 → 같은 번호. 코드 블록 안 링크·번호는 그대로.
- 끝 목록이 붙지 않는다. 모델이 직접 쓴 "출처" + `<url>` 줄은 번호 링크로 바뀔 뿐 중복되지 않는다.
- 저장된 답변의 번호 링크 URL을 다음 Turn에 읽을 수 있다.

## 측정 (유료, 실행 전 사용자 승인)

- read 세트 33개를 한 대화로: 출처 중복 0건, 답변마다 번호 링크, 흉내 낸 `[n](url)` 재번호, 15-2 같은 번호 후속 질문("두 번째 기사"), 13·19번 유지.
- 바뀌는 정보 재확인: 가격·최신 뉴스를 앞 Turn에서 조사한 뒤 뒤 Turn에서 다시 물을 때 다시 확인하는지(해당 시나리오가 없으면 read 세트에 하나 추가).

## 검토 요청 시 볼 점

1. 출력 모양 변경이 기존 출처 규칙(허용 링크·번호·코드·빈 답변 실패)과 URL 경계를 깨지 않는지.
2. 흉내 낸 `[n](url)` 재번호 규칙이 충분히 단순하고, 일반 Markdown 링크(`[텍스트](url)`)와 헷갈리지 않는지.
3. 지침 두 문장이 적절한지, 더 짧게 할 수 있는지, ③을 다른 방식으로 다뤄야 하는지.
4. PIA 연동 때 할 Telegram 서식 변환에서 미리 정해 둘 계약이 있는지(harness 출력은 Markdown이라는 것 등).
5. 빼도 되는 것, 빠진 것.

## Codex 검토

기준 `main ab0ca0a`의 `_with_sources`·`_conversation_urls`·OpenRouter 답변 지침 및 PIA의 현재 Telegram 전송 경로와 계획을 대조했다. 계획 검토만 했으며 코드·README 수정, 병합, AWS 변경, 배포, 실제 모델·공급자 호출은 하지 않았다. **Harness 계획의 구현 차단 이슈는 없다.** 검증된 URL만 번호 링크로 바꾸고 Harness의 끝 목록을 없애는 방향은 보고된 중복(6/33)에 맞고, 기존 출처 기능을 제거하는 것보다 단순하다.

### 비차단·연동 계약

1. **기존 출처 경계는 유지 가능하다(21–36행).** 현재 `_with_sources`는 `_conversation_urls`에 있는 URL만 허용하고, `_CODE`로 나눈 코드 밖에서 `_MODEL_NUMBER`를 먼저 지운 뒤 `_ANSWER_LINK`를 변환한다(`orchestrator.py` 1554–1611행). 새 출력에서 허용 링크만 `[n](url)`로 만들고 URL별 최초 번호를 재사용하면 된다. `[3](url)`은 `_MODEL_NUMBER`의 `(?!\()` 때문에 먼저 지워지지 않으므로, Markdown label이 숫자뿐일 때 기존 `3 `을 남기지 않고 새 `[n](url)`로 바꾸는 분기면 충분하다. URL이 없는 `[2]`, 허용되지 않은 링크, 코드 블록, 문장 끝 구두점의 기존 테스트를 유지하라. 제목 인자를 없애더라도 검색 후보 중복 제거용 URL 집합은 유지하면 된다.

2. **“끝 목록 없음”의 보장 범위를 정확히 쓰라(14·36·61행).** Harness가 끝에 목록을 **추가하지 않는 것**은 코드로 보장된다. 하지만 모델이 직접 쓴 `출처` + `<url>` 줄은 `[n](url)`로 바뀐 **목록 하나로 남는다**(계획 36·61행도 이를 인정). 그러므로 이번 수용 기준은 “Harness와 모델 목록의 중복 0건”이고, “모든 답변의 출처가 문장 옆에만 있음”은 보장하지 않는다. 이 잔여 형태가 측정에서 거슬릴 때만 별도 정리를 검토하면 된다. 앞서 4/6만 잡던 후미 regex를 이번 계획에 다시 넣을 이유는 없다.

3. **Telegram 표시는 PIA가 새 Harness를 pin하기 전 필수 연동 단계다(16행).** 확인한 PIA `telegram_adapter.py`는 현재 `split_message`로 **원문 문자열을 먼저 자르고** `parse_mode` 없이 보낸다(183–196, 483–493행). 이 상태로 `[n](url)`을 보내면 문법이 그대로 보이고, 긴 URL·태그가 분할 지점에서 잘릴 수 있다. Telegram `sendMessage`는 HTML inline link를 지원하고 4096자는 **entity 해석 후** 기준이다([Bot API](https://core.telegram.org/bots/api)). PIA 연동 계약은 “검증된 번호 링크를 안전한 `<a href=...>`로, 계획에서 약속한 굵게 표기는 `<b>`로 변환하고 나머지 텍스트는 HTML escape; 분할은 링크·태그를 온전히 유지”까지면 충분하다. Harness가 임의 Markdown 전체의 Telegram 변환까지 책임지거나 범용 렌더러를 지금 만들 필요는 없다.

4. **과거 정보 지침(40–46행)은 작고 적절하다.** 이미 있는 경계선은 과거 Turn의 종료를 표시하고, 추가 두 문장은 최신 가격·뉴스를 현재 사실로 단정하지 않도록 돕는다. 다만 지침은 모델 행동의 보장이 아니므로 계획대로 “현재가/최신 뉴스 재질문”을 실제 모델 시나리오로 확인하고, 과거 시점의 가격을 묻는 질문에는 불필요한 현재가 조회를 강요하지 않는지도 본다. Memory·주문 확인의 고정 문구는 재현 사례가 없어 이번 범위에서 그대로 두는 것이 맞다.

## 구현 (Claude)

Codex 검토에 차단 이슈가 없어 계획대로 구현했다. 테스트 162개 통과, ruff 결과는 main과 같다.

- `_with_sources(answer, context)`: 허용 링크를 `[n](url)`로, Markdown 링크는 `글자 [n](url)`, 글자가 숫자뿐인 링크(과거 답변 흉내)는 `[n](url)`로 다시 매긴다. 끝 목록과 `SOURCES_HEADING`, 제목 인자를 없앴다. `listed_urls`는 링크 집합(`set[str]`)이 됐다.
- `AGENT_INSTRUCTION`: 과거 대화 두 문장 추가, 출처 문장을 번호 링크로, "an earlier answer's sources" → "links".
- 테스트: 기존 출처 테스트를 새 모양으로 고치고 흉내 낸 `[4](url)` 재번호, 모델이 직접 쓴 목록은 하나만 남음, 저장된 답변이 보낸 답변과 같음을 추가했다. 코드 블록 테스트는 끝 목록 없이 그대로 통과.
- README 6.1(번호 링크, 목록 없음의 이유, Markdown 표시는 host 몫과 PIA Telegram 계약), 흐름도·표 문구.
- 측정용 read 시나리오 추가: 24 "삼성전자 주가 지금 다시 알려줘"(다시 확인해야 함), 24-1 "아까 처음 알려준 삼성전자 주가로 100주 사면 얼마였어?"(과거 값이라 다시 조회하지 않아야 함).
- 수용 기준(Codex 검토 2): 출처 목록 중복 0건. 모델이 직접 쓴 목록 하나가 남는 것은 허용한다.

## 측정 뒤 보완 (Claude, 사용자와 정리)

측정 기록: `tests/manual/records/2026-09-30_source-links.md` (`e4870d3`, read 35개).

- 출처 목표 달성: 중복 0건, 모두 번호 링크.
- 24-1이 압축 뒤 12번에서 알려준 가격을 "알려준 적 없다"고 답했다. 모델은 Summary가 세부를 잃는다는 것을 알 수 없었다. `AGENT_INSTRUCTION`에 한 문장 추가: "The Conversation Summary stands in for older turns and leaves out details; when the current request needs a detail it lacks, do not say it was never given: find it again with the tools, or say it is no longer in the conversation."
- `tests/manual/smoke_flow.py`가 압축 뒤 새 Summary와 압축 실패를 출력한다(이번에 원인을 가리지 못한 이유).
- Exa 키 없는 호출이 19번부터 429(24건)로 막혔다. 24·24-1·20·21은 Exa 한도가 풀리거나 키가 생긴 뒤 다시 측정한다.
- 다음: 재측정 → Codex 구현 검토 → 병합.
