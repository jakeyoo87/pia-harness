# Manual tests

실제 모델·API를 호출하는 수동 테스트와 그 결과 기록이다. 비용이 들고 모델 판단이 매번 달라서 pytest·CI에는 넣지 않는다. 프롬프트, 도구 설명, 모델, 도구를 바꿨을 때 필요한 것만 돌리고 결과를 기록한다.

| 파일 | 용도 |
|---|---|
| `smoke_openrouter_model.py` | OpenRouter 모델 어댑터만 고정 합성 시나리오로 확인한다. `OPENROUTER_API_KEY` 필요. 스크립트 동작은 `tests/test_openrouter_model_smoke.py`가 가짜 응답으로 검증한다. |
| `smoke_flow.py` | README 1번 흐름 전체(루프 모델 → 도구 → 답변)를 `--set`으로 고른 시나리오 파일 순서대로 한 대화에서 실행한다. `OPENROUTER_API_KEY`, `EXA_API_KEY`, `NAVER_API_HUB_CLIENT_ID`, `NAVER_API_HUB_CLIENT_SECRET` 필요(Jina 본문만 키 없이 호출). 모델은 `--model`(기본 `openai/gpt-6-luna`), 일부만 돌릴 때는 `--only 2,6-1`처럼 id를 준다. 요약 끝 열은 호출 수(루프 모델/검색/페이지/발췌 LLM)다. `execution`·`broker`는 가짜 pia-broker(고정 응답)를 써서 실제 주문·계좌 조회를 하지 않는다. `broker`는 PIA처럼 broker 조회·주문 도구와 그 안내 문장을 함께 넣는다. |
| `scenarios/{분류}.json` | 분류별 흐름 시나리오. `read`: 일반 답변·Memory·웹 검색·본문 추출·직전 Turn·조사형 질문(19~21)·조사 중 Memory(22·23)·바뀌는 정보 재확인(24), `execution`: 주문 확인·실행, `broker`: broker 조회의 액션·인자 선택(계좌·매수 가능·현재가·기간별·순위, 지원하지 않는 요청 b10-1·b15는 관찰). `expect`는 모델의 첫 응답(answer·news_search·web_search·web_extract·order·confirm; memory 도구는 빼고 봄), `expect_memory`는 memory 도구 호출(UPDATE·FORGET, 없으면 NONE), `expect_args`는 첫 응답의 그 도구 호출에 있어야 할 인자(broker는 기본값을 채운 뒤 비교), `then`은 처리 중에 보내는 추가 메시지, `then_after`는 그 메시지를 보내기 전 기다리는 초(기본 3)다. `expect`가 없으면 관찰만 한다. 조회를 기대한 질문의 어느 단계에든 order·confirm이 끼면 CHECK다. |
| `records/{날짜}_{분류}.md` | 분류별 실행 결과와 판단 기록. 같은 분류를 다시 돌리면 새 날짜 파일을 만든다. |

```bash
python -m tests.manual.smoke_openrouter_model --model openai/gpt-6-luna --context-limit 1050000
python -m tests.manual.smoke_flow
python -m tests.manual.smoke_flow --only 6,6-1
python -m tests.manual.smoke_flow --set execution
python -m tests.manual.smoke_flow --set broker
```

`smoke_flow.py` 요약의 `CHECK`는 기대와 다른 선택을 뜻하며, 모델 판단이 흔들린 것인지 회귀인지 사람이 로그를 보고 판단한다. 키는 환경변수로만 받고 출력하지 않는다.
