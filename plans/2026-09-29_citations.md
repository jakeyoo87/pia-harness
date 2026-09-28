# 출처 번호 · web_extract 실패 규칙 · NAVER 제거

0.6.0 릴리스 전에 한 브랜치(`claude/citations`)에서 처리한다. 설계는 사용자와 대화로 정했다(A안: 번호 방식). 도구 양식 통일은 다음 작업이다.

## 배경

- 답변 LLM이 출처를 직접 적었다. 4차 측정 21번에서 수치 5개·기준일을 여러 후보에서 가져오고 출처는 하나만 달았다. 답변 속 URL이 실제 도구 결과에 있던 것인지도 코드가 확인하지 않았다.
- Codex 로드맵 검토: 출처 `[n]`은 모델 문구만 믿지 말고 실제 도구가 돌려준 URL에 번호를 붙이는 최소 계약이 필요하다.
- README 점검에서 `web_extract`가 코드 오류까지 "읽지 못함"으로 바꾸는 것을 찾았다(`web_search`는 코드 오류면 Turn 종료). Codex 로드맵 검토가 하지 말라고 한 형태다.
- NAVER 뉴스 검색은 Exa로 대체되어 등록되지 않는다.

## 변경

1. **출처 번호** (`orchestrator._with_sources`)
   - 답변 LLM은 근거 링크를 주장 바로 뒤에 `<url>`로 적는다(수치마다). 번호·목록은 쓰지 않는다(`ANSWER_INSTRUCTION`).
   - Harness가 답변의 링크(Markdown 링크, `<url>`, 맨 URL)를 처음 나온 순서대로 `[n]`으로 바꾸고, 끝에 `출처` 목록 `[n] 제목 URL`을 붙인다. 제목은 이번 Turn 검색 후보의 제목, 없으면 URL만.
   - 허용 링크는 `web_extract`의 URL 경계와 같은 집합(`_conversation_urls`: 사용자 메시지, 저장된 답변, 이번 Turn 도구 결과)이다. 없는 링크는 지운다(Markdown 링크는 글자만 남김).
   - 모델이 직접 쓴 `[n]`은 지운다. 이전 답변 목록의 번호를 베끼면 이번 목록과 어긋난다.
   - 일반 답변과 실행 뒤 답변 모두 적용한다. 실행 뒤 고정 문장(LLM 실패 시)은 그대로 둔다.
   - 목록은 답변과 함께 Turn에 저장되어 다음 Turn에서도 링크가 남는다.
   - 모델이 잘못된 번호를 고르는 것은 막지 못한다.
2. **web_extract 실패 규칙**
   - `PageReadError`를 orchestrator의 계약으로 옮긴다. `extract_page`는 예상 가능한 실패에만 이것을 낸다.
   - `JinaPageExtractor`: 연결·상태 코드·빈 응답, 링크·이미지를 빼니 글자가 없음, 요약 LLM 실패(`OpenRouterModelError`)를 `PageReadError`로 낸다.
   - Orchestrator: `PageReadError`만 "읽지 못함" 결과로 바꾸고, 그 밖의 오류는 `TOOL_FAILED`로 Turn을 끝낸다. 잘못된 반환 타입도 코드 오류다.
3. **NAVER 제거**: `news_search.py`, `tests/test_news_search.py`, export, README 문장. PIA의 NAVER 등록·비밀값 로드는 PIA 0.6.0 연동에서, Secrets Manager 키 삭제는 사용자 승인 뒤 따로 한다.
4. **README 정리**: 0번 그림의 읽기 도구 이름, 1번의 `web_extract` 표시 조건("이번 Turn에 아직 안 읽은 링크"), 6.1 출처 번호, 6.1.2 실패 규칙.

## 테스트

- 링크가 순서대로 번호가 되고, 모델 번호·없는 링크가 지워지고, 목록에 검색 후보 제목이 붙는다.
- `extract_page`의 코드 오류는 `TOOL_FAILED`, `PageReadError`는 "읽지 못함" 결과.
- 글자 없는 페이지와 요약 LLM 실패는 `PageReadError`.
