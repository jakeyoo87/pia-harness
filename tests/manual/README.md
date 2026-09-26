# Manual tests

실제 모델·API를 호출하는 수동 테스트와 그 결과 기록이다. 비용이 들고 모델 판단이 매번 달라서 pytest·CI에는 넣지 않는다. 프롬프트, Jev 기준, 모델, 도구를 바꿨을 때 필요한 것만 돌리고 결과를 기록한다.

| 파일 | 용도 |
|---|---|
| `smoke_openrouter_model.py` | OpenRouter 모델 어댑터만 고정 합성 시나리오로 확인한다. `OPENROUTER_API_KEY` 필요. 스크립트 동작은 `tests/test_openrouter_model_smoke.py`가 가짜 응답으로 검증한다. |
| `smoke_flow.py` | README 1번 흐름 전체(Jev → 읽기 도구 → 답변)를 `scenarios.json` 순서대로 한 대화에서 실행한다. `OPENROUTER_API_KEY`, `NAVER_API_HUB_CLIENT_ID`, `NAVER_API_HUB_CLIENT_SECRET` 필요. |
| `scenarios.json` | 흐름 시나리오. `expect`는 Jev의 첫 선택(answer·search·read), `expect_memory`는 MemoryAction, `then`은 처리 중에 보내는 추가 메시지다. `expect`가 없으면 관찰만 한다. |
| `YYYY-MM-DD_*.md` | 실행 결과와 판단 기록. |

```bash
python -m tests.manual.smoke_openrouter_model --model openai/gpt-6-luna --context-limit 1050000
python -m tests.manual.smoke_flow
python -m tests.manual.smoke_flow --only 6,6-1
```

`smoke_flow.py` 요약의 `CHECK`는 기대와 다른 선택을 뜻하며, 모델 판단이 흔들린 것인지 회귀인지 사람이 로그를 보고 판단한다. 키는 환경변수로만 받고 출력하지 않는다.
