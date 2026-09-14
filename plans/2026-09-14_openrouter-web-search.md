# OpenRouter Web Search plan

- 최초 작성일: 2026-09-14 (Asia/Seoul)
- 브랜치: `codex/openrouter-web-search`
- 구현: Codex
- 검토: Claude
- 상태: 계획 검토 대기

## 목표

`OpenRouterModelAdapter`의 일반 Answer 호출에만 선택적 `openrouter:web_search` Server Tool을 제공한다.
검색이 필요한지는 모델이 판단하며, Memory Review와 rolling Summary는 기존처럼 검색 없이 동작한다.
소비 애플리케이션이 검색 엔진과 결과 한도를 정하고 Harness는 전달·검증만 담당한다.

초기 PIA 정책은 후속 통합에서 `engine=exa`, 검색당 결과 3개, 요청 전체 결과 5개,
`search_context_size=low`로 설정한다. 품질이 부족하면 PIA 설정만 `auto`로 바꿀 수 있어야 하며 Harness에
PIA 기본값이나 특정 모델 ID를 넣지 않는다.

## 확인된 제품 결정

- 별도 검색 Intent 분류기나 키워드 규칙을 만들지 않는다.
- 모델이 질문의 의미를 보고 검색 여부와 검색어를 정한다.
- Web Search는 일반 Answer에만 활성화한다.
- 검색 결과나 기사 전문을 Harness가 별도 저장하지 않는다.
- 검색을 사용한 답변은 사용자에게 출처 링크를 포함한다.
- 검색 실패 뒤 최신 정보를 확인한 것처럼 검색 없는 답변을 자동 생성하지 않는다.
- 기존 bounded retry를 그대로 사용한다. 재시도하면 검색 비용도 다시 발생할 수 있음을 문서화한다.
- deprecated `plugins: [{"id":"web"}]`와 `:online` 모델 별칭은 사용하지 않는다.

## 소유권 경계

### Harness

- 선택적이고 불변인 `OpenRouterWebSearchConfig` 계약을 제공한다.
- 설정이 있을 때 Answer payload에만 아래 Server Tool을 추가한다.
- 설정과 provider 응답의 검색 사용량을 엄격하되 작게 검증한다.
- Answer가 검색을 사용했을 때 본문에 간결한 Markdown 출처 링크를 포함하도록 trusted instruction을
  보강한다.
- Memory Review, Summary, persistence, Orchestrator 순서와 delivery 계약은 변경하지 않는다.

### 소비 애플리케이션 PIA

- 검색 활성화 여부, 엔진, 결과 수, 비용 정책을 결정하고 Harness에 전달한다.
- Telegram 표시, 실패 문구, 운영 로그와 비용 관찰을 담당한다.
- 이 Harness 작업이 병합·릴리스되고 실제 모델 검증을 통과한 뒤 별도 PIA 브랜치에서 통합한다.

## 공개 계약

대략 다음 형태의 선택적 설정을 추가한다. 정확한 이름은 구현 중 기존 공개 API 스타일에 맞춘다.

```python
OpenRouterWebSearchConfig(
    engine="exa",
    max_results=3,
    max_total_results=5,
    search_context_size="low",
)
```

- `engine`: OpenRouter Server Tool이 지원하는 명시적 엔진 문자열
- `max_results`: 검색 호출당 1~25
- `max_total_results`: 한 Answer 요청 전체에서 1 이상
- `search_context_size`: `low | medium | high`
- 설정을 생략하면 v0.2.0과 동일하게 `tools`가 없는 요청을 만든다.

`GeneratedAnswer`에는 음이 아닌 `web_search_requests`를 기본값 0으로 추가한다. OpenRouter가
`usage.server_tool_use.web_search_requests`를 반환하면 그 값을 사용하고, 검색 미사용 또는 usage 누락은 0으로
취급한다. 다른 provider 메타데이터나 검색 결과 본문은 노출하지 않는다.

기존 Answer JSON Schema의 `answer`, `memory_action`, `delete_all_confirmed` 세 필드는 유지한다. 별도 sources
필드나 citation renderer는 첫 버전에 추가하지 않는다. 검색을 사용한 모델은 `answer` 안에 사람이 읽을 수 있는
Markdown 링크를 포함해야 하며 실제 Luna+Exa smoke에서 이 계약을 확인한다.

## 구현

1. `src/pia_harness/openrouter.py`와 공개 export에 Web Search 설정과 validation을 추가한다.
2. `_answer_payload`에만 `tools=[{"type":"openrouter:web_search", "parameters": ...}]`를 추가한다.
   `_base_payload`, Memory Review, Summary에는 도구를 넣지 않는다.
3. 보수적 Answer 입력 토큰 계산에 tools 선언도 포함한다.
4. `usage.server_tool_use.web_search_requests`를 안전하게 파싱해 `GeneratedAnswer`에 전달한다. 잘못된 음수,
   boolean 또는 비정수 값은 기존 invalid-usage 오류로 처리한다.
5. 검색 사용 시 답변 본문에 실제 출처 링크가 포함되도록 Answer instruction을 보강한다. 검색 결과는
   untrusted data이며 그 안의 지시를 따르지 않도록 명시한다.
6. 기존 재시도·취소·Memory action·Compaction 동작은 유지한다.
7. `README.md`와 `docs/04-model-adapter-and-integration.md`를 현재 계약에 맞게 갱신한다.

## 테스트

DB·network-free 테스트에서 다음을 고정한다.

- 설정 생략 시 기존 payload와 결과가 변하지 않는다.
- 설정 시 Answer payload만 정확한 Server Tool과 파라미터를 갖는다.
- Memory Review와 Summary payload에는 tools가 없다.
- 잘못된 엔진·결과 한도·context size를 생성 시 거부한다.
- input token estimate가 tool 선언을 포함한다.
- 검색 사용량의 정상값·누락·0·잘못된 타입을 검증한다.
- retry 두 번은 같은 bounded tool payload를 사용하고 cancellation은 계속 전파된다.
- 기존 전체 Python suite가 통과한다.

실제 모델 테스트는 사용자 승인 아래 synthetic 질문만 사용한다.

- 모델: `openai/gpt-5.6-luna`
- 검색: `openrouter:web_search`, `engine=exa`, 결과 3/전체 5, context `low`
- strict JSON Schema와 Web Search를 한 요청에서 함께 사용
- 검색이 명백히 필요한 최신 질문에서 `web_search_requests >= 1`
- 검색이 불필요한 단순 질문에서 `web_search_requests == 0`
- 응답 모델 ID가 요청 모델과 일관됨
- Answer JSON과 MemoryAction이 유효함
- 사용자-facing answer에 최소 하나의 실제 HTTPS 출처 링크가 있음
- Memory Review와 Summary smoke는 검색 없이 기존 계약을 유지함

Structured Output과 Server Tool 조합이 실패하거나 링크 계약을 충족하지 못하면 우회 구현으로 완료 처리하지
않고 원인과 선택지를 보고한다.

## 완료 기준

- 계획과 구현에 독립 검토 blocker가 없다.
- 전체 network-free suite가 통과한다.
- Luna+Exa live smoke가 위 호환성 기준을 통과한다.
- 공개 API와 문서에 PIA 정책이 하드코딩되지 않는다.
- Harness main 병합과 버전 릴리스 전 사용자 확인을 받는다.
- PIA 코드·AWS·Bot은 이 브랜치에서 변경하지 않는다.

## 참고

- OpenRouter Web Search Server Tool:
  <https://openrouter.ai/docs/guides/features/server-tools/web-search>
- OpenRouter Structured Outputs:
  <https://openrouter.ai/docs/guides/features/structured-outputs>
