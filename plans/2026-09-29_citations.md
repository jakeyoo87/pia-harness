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

## Codex 검토

구현 `983d2f6`과 그 뒤의 README 정리 `44ccd84`를 기준 main `71fabc4` 및 Turn 저장·Jev 직전 Turn 경로에 대조했다. 코드·README 수정, 병합·AWS 변경·배포, 실제 모델·공급자 호출은 하지 않았다. 오프라인 unittest 163개는 통과했다. 아래 합성 입력은 외부 호출 없이 `_with_sources`만 실행해 확인했다.

### Blocker

1. **정리 후 본문이 비면 금지 링크·모델 번호가 원문 그대로 되살아난다.** `_with_sources`는 허용되지 않은 링크와 모델 작성 `[n]`을 지우지만, 번호가 하나도 없고 결과가 빈 문자열이면 `body or answer.text`를 돌린다(`orchestrator.py:1497-1502`). 답변이 `<https://not-allowed.example/x>` 하나일 때 그 링크가 그대로 전달되고, `[1]` 하나일 때도 번호가 남는 것을 합성 입력으로 확인했다. 빈 결과에는 원문 복구 대신 고정된 안전 안내를 사용해야 '없는 링크·번호 제거' 계약이 성립한다.
2. **모든 답변에 대한 전역 치환이 정상 코드·문장을 훼손한다.** `_MODEL_NUMBER`는 `items[1]`의 `[1]`도 지우고, `_with_sources`의 연속 공백 정리는 코드 들여쓰기를 한 칸으로 줄인다(`orchestrator.py:90-96, 1497-1500`). 허용 URL이 코드 블록에 있으면 `url = 'https://allowed.example/x'`도 `url = '[1]'`로 바뀌었다. 링크 변환과 번호 제거는 실제 인용 문맥에만 적용하고 코드 블록·인라인 코드와 일반 공백은 보존해야 한다. 모델에게 요구한 `<url>` 표기만 인용으로 처리하는 것이 가장 작은 시작점이다.
3. **모든 `OpenRouterModelError`를 페이지 읽기 실패로 바꾸면 공통 모델 장애가 숨는다.** `JinaPageExtractor.extract`는 페이지 요약 LLM의 `OpenRouterModelError`를 조건 없이 `PageReadError`로 바꾼다(`web_extract.py:55-63`). 예를 들어 모델 API의 401·403(키/권한 문제)도 '해당 페이지만 읽지 못함'이 되어 Jev가 다른 페이지를 계속 고른다. 페이지 내용 때문에 난 출력 잘림·형식 실패만 `PageReadError`로 다루고, 인증·설정 같은 공통 오류는 기존 `TOOL_FAILED`로 올리면 된다. 새 오류 계층은 필요 없다.

### Non-blocker

- **유효한 괄호 포함 URL이 인용에서 빠진다.** `[article](https://example.com/topic_(A))`는 Markdown URL 정규식이 첫 `)`에서 멈추고, `_conversation_urls`도 괄호 앞까지만 URL로 읽어 결과가 `article)`로 남았다(`orchestrator.py:86-96, 1465-1500`). URL 인식 범위를 계획대로 넓히기 전에 이 실제 URL 형식을 테스트에 넣는다. 현재 ETF 시나리오의 URL에는 없으므로 위 차단 사항보다 우선순위는 낮다.
- 출처 목록을 답변과 함께 저장하는 것은 다음 Turn의 링크 재읽기에 맞는다. 다만 이 목록은 **URL이 대화·도구 결과에 있었다는 출처 확인**이지 해당 페이지를 성공적으로 읽었거나 수치 주장을 뒷받침한다는 검증은 아니다. 사용자 메시지의 링크나 `could not be read` 결과 속 URL도 허용 집합에 들어간다. 이 한계를 README의 출처 설명과 구분해 두면 충분하며 이번에 주장별 증거 판정 계층을 만들 필요는 없다. 긴 출처 목록은 Jev가 직전 답변의 끝 4,000자만 볼 때 본문을 밀어낼 수 있으나(`jev.py:224-242`), 실제 긴 답변 회귀가 관측되기 전에는 별도 상태를 추가하지 않는다.
- `PageReadError` 경계에서 Jina의 연결·HTTP 상태·빈 페이지와 코드 오류를 나눈 방향, `confirm` 뒤 고정 결과를 변환하지 않는 방향은 맞다. NAVER 소스·export·테스트 제거도 함께 맞춰졌고, 남은 NAVER 언급은 이전 수동 측정 기록뿐이다. PIA의 의존성·Secret 정리는 별도 0.6.0 연동 단계로 남는다.
