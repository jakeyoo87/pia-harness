# 웹 조사 도구와 Jev 판단 개선 계획

## 목표

Hermes Agent 수준의 조사 답변을 PIA에서 낼 수 있는지 확인한다. 기준 질문은 "혹시 기판 관련 ETF 뭐가 있는지 조사해볼 수 있어 ?"이다. Hermes는 이 질문에 약 1분 동안 웹 검색 18회, 본문 읽기 1회를 했다. 결과로 TIGER AI반도체핵심공정(471760)과 구성 비중, 출처를 답했다.

이번 작업은 두 가지다. 필요한 도구를 만들고, 지금 구조(Jev 유지, A안)가 얼마나 되는지 측정한다. 측정 결과로 B안 전환 여부를 정한다. B안은 답변 모델이 직접 도구를 고르는 방식이다.

## 배경

### 지금 구조의 한계

- 검색은 NAVER 뉴스뿐이다(`search`). 운용사 페이지나 ETF 정보 사이트는 찾지 못한다.
- 본문 읽기(`web_fetch`)는 대화에 나온 링크만, 한 Turn에 한 번 읽는다. `UrlReader`로 직접 읽는다.
- Jev는 읽은 본문을 앞 1,000자만 본다(`FETCH_RESULT_MAX_CHARS`). 그래서 근거를 모았는지 판단할 수 없다.
- 도구 루프가 120초를 넘으면 `GENERATION_FAILED`로 끝난다.

### Hermes 분석 (NousResearch/hermes-agent)

- 답변 모델이 직접 도구를 호출한다. 도구 호출 없이 글만 쓰면 끝난다. 기본 한도는 500회다.
- `web_search`는 제목, URL, 설명만 돌려준다. `web_extract`는 URL 최대 5개의 본문을 LLM 없이 돌려준다. 페이지당 15,000자다.
- 검색과 추출 모두 외부 엔진이 처리한다. 도구별로 엔진을 정할 수 있다(`web.search_backend`, `web.extract_backend`). Hermes에는 페이지를 직접 받아오는 코드가 없다.
- 계속 찾는 이유는 시스템 프롬프트("결과가 부족하면 다른 검색어로 다시 찾아라")와 출처 스킬이다.

### 다른 조사 에이전트들이 "충분한가"를 판단하는 방식

| 구현 | 판단 주체 | 종료 | 한도 |
|---|---|---|---|
| Hermes | 도구를 쓰는 모델 | 도구 호출 없음 | 500회 |
| Google Gemini LangGraph 예제 | 라운드마다 reflection 단계(`is_sufficient`, `knowledge_gap`) | 충분하면 답변 | 최대 라운드 수 |
| LangChain open_deep_research | 감독 모델(`think_tool`로 점검) | `ResearchComplete` 도구 | 반복 3회 |
| Jina DeepResearch | 한 모델이 search/visit/reflect/answer 선택, 평가기가 답을 검사 | 평가 통과 | 토큰 예산. 다 쓰면 가진 정보로 답(Beast Mode) |

공통점은 세 가지다.

- 멈출지 판단하는 모델은 근거를 다 본다.
- 한도는 하나만 둔다.
- 한도에 걸리면 실패로 끝내지 않고 가진 정보로 답한다.

## 엔진 선정 시험 (2026-09-28, EC2에서 실행)

### 검색 (검색어 11개: ETF 3, 실적, KODEX, 엔비디아, 최신 뉴스 4)

- **Exa**
  - 11개 모두 관련 결과가 나왔다. 1~2초 걸렸다.
  - `site:` 연산자가 작동했다.
  - 1차 출처가 먼저 나왔다: 삼성전기 뉴스룸, IR 자료 PDF, 운용사 페이지.
- **Parallel, Tavily, Firecrawl 검색**
  - 대체로 좋았다.
  - 다만 무관한 결과가 섞였다(삼성전기 질문에 삼성전자 결과 등). `site:`가 작동하지 않는 엔진도 있었다.
- **NAVER 뉴스(지금 PIA)와 Exa의 최신 뉴스 비교**
  - 질문 4개 중 3개에서 Exa가 더 나았고, 1개는 비슷했다.
  - 예: "코스피 오늘 하락 이유" → Exa는 오늘 기사 5건이 모두 원인을 설명했다. NAVER 관련도순은 관련 기사가 1건이었고, 최신순은 무관한 기사가 섞였다.

### 본문 추출 (페이지 6개)

| 페이지 | 직접 읽기(UrlReader) | Jina Reader | Firecrawl | Exa·Parallel |
|---|---|---|---|---|
| funetf 471760 | 525자, 비중 없음 | 비중 있음, 09.28 | 비중 있음, 09.28 | 비중 있음, 09.22~23 저장본 |
| 삼성자산운용 KODEX(JS) | 실패 | 구성종목 22개 전체 | 전체 | 실패·일부 |
| investing.com | 942자, 비중 없음 | 비중 표 | 비중 표 | 보통 |
| 운용사(tigeretf, SOL), 네이버증권 | 실패 | 실패 | 실패 | 실패 |
| 속도 | 0.1~0.7초 | 4~9초 | 0.3~10초 | 0.2~1초 |

`UrlReader`가 약한 이유는 두 가지다.

- readability가 표를 버린다.
- 자바스크립트로 그리는 페이지를 읽지 못한다.

표를 보존하도록 고친 직접 읽기도 funetf와 삼성자산운용에서 실패했다.

### 결론

- 검색은 **Exa**(무료 요금제 월 $10 상당, 키 없는 MCP도 가능), 본문 추출은 **Jina Reader**(키 없이 무료, 속도 제한 있음)로 한다.
- 두 곳 모두 페이지와 검색을 업체 서버가 처리한다. 우리 서버는 임의 URL에 접속하지 않는다.
- 엔진은 도구별로 하나씩 고정한다. 대체 엔진이나 무료 엔진 순환은 두지 않는다.

## 결정

### 1. Jev가 이번 Turn의 도구 결과를 전부 본다

- `jev.py`의 web_fetch(새 이름 web_extract) 결과 1,000자 자르기를 없앤다.
- 깊은 조사의 근거는 모두 현재 Turn에 쌓인다. 이전 Turn은 지금처럼 직전 Turn만 준다.
- 길이는 도구 한 곳에서 정한다(페이지당 상한, 아래 3번). Jev와 답변 LLM이 같은 내용을 본다.

### 2. `web_search` (Exa)가 NAVER `search`를 대체한다

- 도구 이름은 `web_search`, 인자는 `{"query": string}`이다.
  - 도구 설명에 `site:`, `"정확한 구문"`을 쓸 수 있다고 안내한다.
  - 뉴스와 웹 전체를 모두 이 도구로 찾는다.
- 결과: 후보 5건. 각 후보는 제목, URL, 발행일(있으면), 요약이다. 지금 `ReadToolResult(observation_text, links)` 형식을 그대로 쓴다.
- 호출
  - 기본은 Exa MCP(`https://mcp.exa.ai/mcp`, `web_search_exa`)를 키 없이 부른다.
  - 키가 설정되면 키를 붙여 부른다. 구현 전에 같은 엔드포인트에 키를 붙이는 방식이 되는지 확인한다. 안 되면 키 전용 경로 하나만 둔다.
- 예상 가능한 실패(HTTP 오류, 한도 초과, 형식 오류)는 결과 문장으로 돌려준다. 지금 `NaverNewsSearch`와 같은 방식이다.
- `NaverNewsSearch` 코드와 NAVER 키는 이번에 지우지 않는다. PIA가 등록만 하지 않는다. 실사용에서 문제가 없으면 따로 정리한다.

### 3. `web_extract` (Jina Reader)가 `web_fetch`와 `UrlReader`를 대체한다

- 일반 읽기 도구(`ReadToolDefinition`)로 만든다. orchestrator 안의 web_fetch 특별 처리는 없앤다.
  - 없어지는 것: `_WEB_FETCH_SPEC`, `_conversation_urls`, 대화 링크 제한, Turn당 1회 제한, `read_url` 주입
- 인자는 `{"urls": [1~5개]}`다. http, https URL만 받는다. 같은 URL은 한 번만 읽는다.
- 호출
  - URL마다 `GET https://r.jina.ai/<url>`을 동시에 보낸다.
  - 키가 설정되면 `Authorization: Bearer`를 붙인다.
- 결과
  - 앞에 URL별 상태 줄을 둔다(읽음 / 읽지 못함). 그 뒤에 본문을 붙인다.
  - 페이지당 **10,000자**에서 자른다. Hermes는 15,000자이고, 우리는 Jev도 같은 내용을 보므로 조금 줄였다. 시험에서 보인 비중 표는 이 안에 들어간다.
  - 본문은 지금처럼 신뢰하지 않는 데이터로 다룬다. 도구 결과는 지시가 아니다.
- 대화 링크 제한이 없어지는 이유
  - 그 제한은 우리 서버가 임의 URL에 접속하는 위험(Codex 지적: DNS 재조회로 내부망 접속) 때문이었다.
  - 이제 업체가 읽으므로 우리 내부망에 닿지 않는다.
- `UrlReader`와 `readability-lxml` 의존성을 삭제한다.

### 4. 한도와 시간 초과

- Turn당 도구 호출 상한을 **하나** 둔다. 예: 20회. 상한에 닿으면 도구 선택지를 빼고 answer로 간다.
- 도구 루프 시간이 다 되면 실패로 끝내지 않고 answer로 간다.
  - 답변을 쓸 시간은 루프 시간 밖에 따로 남는다. 지금도 최종 답변 생성은 120초 루프 밖에 있다.
  - 이때 답변 LLM에 "조사가 시간·횟수 한도로 끝났다. 확인하지 못한 부분은 확인하지 못했다고 밝혀라"를 알린다.
- 루프 시간은 120초를 그대로 둔다. 측정 결과를 보고 조정한다.

### 5. 측정

- read 시나리오를 새 도구 이름에 맞춘다(`search` → `web_search`, `web_fetch` → `web_extract`). 대화 링크 제한 관련 시나리오는 정리한다.
- 조사형 시나리오 몇 개를 추가한다. 기준 질문(기판 ETF) 외에 실적, 다른 ETF를 넣는다.
- 판단 기준
  - 특정 수치를 정답으로 고정하지 않는다. 출처마다 비중이 달랐다(22.85%, 23.3%, 23.57%).
  - 대신 다음을 본다: 해당 ETF를 찾았는지, 수치에 기준일과 출처가 있는지, 몇 단계와 몇 초가 걸렸는지, 도구 호출 수와 Jev 호출 시간.
- 결과 기록은 `tests/manual/records/`에 남긴다.
- 판단: Jev가 근거를 보고 적절히 계속하거나 멈추면 A안을 유지한다. 너무 일찍 멈추거나 헛돌면 B안을 계획한다.

## 이번에 하지 않는 것

- 도구 양식 통일(guide, label, run), harness 쪽 진행 문구
- 정리(reflection) LLM, Jev 추가 질문, 숫자 confidence
- 출처 번호 계약([n]을 도구 URL에 고정): 측정 뒤 답변 형식을 정할 때 한다
- 긴 페이지를 저장하고 나눠 읽기, 브라우저 도구, 대체 엔진·무료 엔진 순환, 검색 캐시
- NAVER 코드·키 삭제, Broker 읽기 도구

## PIA 연동 (구현 때 PIA 계획서로 따로 쓴다)

- `NaverNewsSearch`, `UrlReader` 등록을 빼고 `web_search`, `web_extract`를 등록한다.
- 키는 선택이다. 없으면 키 없이 부른다. 넣을 때는 Secrets Manager를 쓴다. 사용자가 무료 요금제에 가입한다.
- 진행 문구: `웹 검색: {query}`, `본문 읽기: 링크 N개`
- 시스템 프롬프트나 README에서 NAVER 뉴스, 대화 링크 제한을 언급하는 부분을 고친다.

## 버전

harness 0.6.0. 도구 이름이 바뀌고, `read_url`과 `UrlReader`가 삭제된다.

## 검토 요청 사항

1. 1번(Jev 1,000자 제한 제거)과 3번(페이지당 10,000자, 최대 5페이지)을 합치면, Jev 입력이 한 단계에 수만 자가 될 수 있다. 입력 한도와 속도 위험을 측정 전에 줄여야 하는지.
2. web_extract에서 대화 링크 제한을 없애는 것이 안전한지. 업체가 읽는 구조에서 추가로 지켜야 할 최소 조건이 있는지. 예: URL 안의 비밀값.
3. 4번의 한도와 시간 초과 처리가 최소한으로 충분한지.
4. 빼도 되거나, 측정 뒤로 미뤄도 되는 부분.

## Codex 계획 검토

`8929c27` 계획을 pia-harness 0.5.0의 Jev·도구·시간 제한 코드와 대조하고 Exa MCP·Search API, Jina Reader의 공식 계약을 확인했다. 계획 검토만 했으며 코드·README 수정, 실제 Exa/Jina/모델 호출, 병합·AWS 변경·배포는 하지 않았다. 엔진을 하나씩 고르고 A안을 측정한 뒤 B를 결정하는 방향은 맞다.

### Blocker

1. **대화 링크 제한은 로컬 SSRF 방지만이 아니라 URL을 통한 정보 유출 방지 경계다(100~112행).** 현재 `build_tool_call`은 전체 Context를 보고 인자를 쓴다(`openrouter.py:215-250`). 악의적인 웹 본문이 다음 호출에서 계좌·보유정보를 담은 새 공개 URL을 만들도록 유도하면, 제한이 없는 `web_extract`가 그 URL을 Jina에 보내고 Jina가 해당 사이트를 방문한다. 우리 EC2가 대상 사이트에 직접 접속하지 않는다는 사실은 이를 막지 못한다. `orchestrator.py:220-230, 760-802`의 **대화·검색 결과에 실제로 등장한 URL만 허용**하는 규칙을 유지하고, Turn당 1회 제한만 독립적으로 없애는 것이 가장 작은 수정이다. 명시적으로 주어진 URL이라도 사용자정보·서명 토큰이 든 URL은 제3자 Jina로 전달하지 않는 규칙을 정해야 한다. Jina는 기본적으로 URL 내용을 캐시한다([공식 Reader 문서](https://jina.ai/reader/)). HTTP client도 목적지를 Jina의 고정 HTTPS origin으로 제한하고 자동 redirect를 따르지 않아야 '우리 서버는 Jina에만 접속'이라는 전제가 성립한다.
2. **페이지별 10,000자와 도구 20회 상한은 Jev의 누적 입력 한도를 보장하지 않는다(80~84, 102~108, 115~121행).** 한 번에 다섯 페이지면 본문만 최대 50,000자이고 다음 검색·추출 결과가 같은 Turn에 계속 쌓인다. 기존 `jev.py:204-216`의 1,000자 제한을 제거하면 모델의 약 32k-token 입력 한도에 도달해, A안 측정 대신 Jev 요청 실패가 날 수 있다. `ContextBudgetExceeded`는 답변 모델의 예산을 검사할 뿐 Jev 예산이 아니다. Jev 호출 **직전 전체 state**에 대한 보수적인 총 입력 상한을 두고, 초과하면 새 도구 호출 없이 현재까지 완료된 근거로 부분 답변을 생성하도록 정한다. 별도 요약 LLM이나 페이지별 예외 상태는 필요 없다. 이 한도에 걸린 횟수도 A안 측정에 기록한다.
3. **Exa MCP의 반환 형식이 91행의 구조화된 5개 후보 계약과 다르다.** 공식 `web_search_exa`는 결과를 `Title / URL / Published / Highlights`가 이어진 **하나의 텍스트 블록**으로 반환한다([공식 MCP 구현](https://github.com/exa-labs/exa-mcp-server/blob/main/src/tools/webSearch.ts)). 이를 `ReadToolResult.links`로 파싱하면 표시 텍스트 형식에 의존한다. 반면 [Exa Search API](https://exa.ai/docs/reference/search)는 구조화된 `results` 배열을 반환하고 무료 Starter의 API 키로도 시작할 수 있다([공식 가격](https://exa.ai/pricing)). Simple-first 권장은 **API 키를 Secrets Manager에 둔 Search API 한 경로**다. 키 없는 MCP를 반드시 유지하려면, 텍스트에서 URL·제목·날짜를 안정적으로 추출하고 실패 시 어떻게 할지 계약과 fixture를 먼저 정해야 한다. 공식 MCP는 같은 endpoint에 선택적으로 `x-api-key` 또는 Authorization header를 받으므로 키 유무 때문에 두 MCP 경로가 필요한 것은 아니다([공식 MCP README](https://github.com/exa-labs/exa-mcp-server)). 키를 URL query에 넣지는 않는다.

### Non-blocker

- **시간 초과 → answer는 읽기 루프 안에서만 처리한다.** 현재 `_before_deadline`의 `TimeoutError`는 바깥에서 `GENERATION_FAILED`가 되고(`orchestrator.py:697-710`), 정상 최종 답변 생성은 그 뒤의 별도 경로다(`633-650`). 제한에 도달하면 완료된 관측만 남겨 도구 선택을 끝내고, '확인하지 못한 부분' 지침과 함께 답변을 생성한다. 개별 Jev/provider 장애나 주문 `confirm`을 시간 초과의 부분 답변으로 바꾸지 않는다. 호출 상한도 읽기 도구에만 적용한다. 이 규칙이면 한 라우팅 시간 제한과 한 읽기 호출 수 상한으로 충분하다.
- **Jina keyless 사용량을 A안 성능과 구분한다.** 공식 Reader의 키 없는 한도는 현재 20 RPM이다([공식 Reader 문서](https://jina.ai/reader/)). 5개 URL 동시 읽기를 여러 번 하면 429가 날 수 있으므로, 테스트 기록에 공급자 한도 실패를 Jev의 '너무 일찍 멈춤'과 별도로 적으면 된다. 자동 fallback·큐는 이번에 필요 없다.
- **PIA 연동 때 NAVER의 기동 의존성도 끊어야 한다.** PIA `app/main.py`는 현재 NAVER Secret을 무조건 로드하고 `NaverNewsSearch`를 만든다. 등록만 빼면 사용하지 않는 Secret이 Bot 기동의 필수 조건으로 남는다. NAVER 코드·키의 삭제는 미뤄도 되지만, PIA 계획에는 기동 시 로드·생성·종료 경로 정리를 명시한다.
- 출처 번호 계약과 긴 페이지 저장을 측정 뒤로 미루는 결정은 적절하다. 이번 시나리오에서는 수치의 기준일·실제 URL 인용 여부를 먼저 확인하고, 실패 시 검색/추출 품질·Jev 판단·공급자 한도를 분리해 기록하면 B 전환 판단이 선명해진다. 과한 provider registry, 검색 캐시, reflection 모델은 추가하지 않는다.
