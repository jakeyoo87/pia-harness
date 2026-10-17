# past_financials 후속: 출처 두 개, 웹 재확인 없음, 설비투자 태그

- 작성: 2026-10-17, Claude (EC2). 구현: Claude / 검토: Codex(계획과 구현을 한 번에, 사용자 결정).
- 저장소: pia-harness. 다음 릴리스 v0.7.13, 그 뒤 PIA 고정 버전 올리기·Bot 배포.
- 앞선 계획: `plans/2026-10-16_past-financials.md`(v0.7.12, PIA dev Bot 배포 2026-10-17).

## 배경: 실제 자료 확인(2026-10-17, 사용자 Telegram)

- 국내(OpenDART): 연간·분기 모두 정상. 그런데 삼성전자 8개 분기 답에 **출처 링크가 7개** 붙었다(분기마다 다른 보고서). 도구 설명이 "각 값의 출처를 유지하라"고 해서 모델이 모두 인용했다.
- 미국(SEC): 처음엔 SEC가 연락처 도메인(naver.com)을 막아 403 → PIA 설정을 gmail 주소로 바꿔 해결(코드 무관). 그 뒤 도구는 엔비디아·애플 4개 분기를 모두 줬는데, 모델이 **도구 숫자를 웹으로 하나하나 다시 확인**했다(애플 10단계·약 1분, "영업이익 143756" 같은 숫자 검색). 같은 대화 앞부분에 웹으로 실적을 찾던 흐름이 남아 있었다. 시나리오(새 대화)에서는 생기지 않았다.
- 엔비디아 설비투자(`capex`)가 8개 분기 모두 미제공. 엔비디아는 2020년 이후 `PaymentsToAcquireProductiveAssets`로 공시한다(2026-10-16 받아 둔 Company Facts 표본으로 확인: `PaymentsToAcquirePropertyPlantAndEquipment`는 2020-07-26 이후 없음).

## 사용자 결정

1. 답에 붙이는 출처는 **대표 두 개까지**: 가장 최근 보고서, 그리고 계산된 Q4가 있으면 그 사업보고서. 나머지는 도구 결과에 남는다("출처 다 보여줘"로 다시 꺼낼 수 있음).
2. 도구 숫자는 공시 원문 값이므로 **웹으로 다시 확인하지 않는다. 웹은 결과에 없는 것만**(실패로 숫자가 없을 때 포함 — 따로 적지 않음).

## 변경

- `past_financials.py`: 도구 설명과 결과 머리말에 위 두 규칙을 한 줄씩(설명: "These are the filed figures: do not re-check them with web search; search the web only for what this result lacks." / "in the answer cite at most two of their URLs (the newest report, and the annual report behind a computed Q4)"). "각 값의 출처를 유지" 문장에서 sources를 뺀다(출처는 결과에 남음).
- `sec_financials.py`: `capex` 후보 태그에 `PaymentsToAcquireProductiveAssets`를 추가. 다른 후보처럼 기간마다 최신 정기공시 값을 고른다(같은 공시에 둘 다 있으면 PP&E가 먼저). 이 태그는 무형자산 취득도 포함하므로 결과 이름을 **"유형·무형자산 취득"**으로 바꿔 적는다(`ACCOUNT_LABELS`, 계정 하나의 뜻이 지표 이름보다 넓을 때만).
- README 6.2.4 한 줄, 단위 테스트 2개(ProductiveAssets로 capex, 설명·머리말의 두 규칙), 시나리오 f11(웹 결과가 쌓인 대화에서 애플 4개 분기 → `past_financials`, 웹 도구 금지).

## 검증

- 단위 테스트 전체 통과, 바꾼 파일 ruff·format 통과.
- 모델 시나리오 `--set financials`(유료, 사용자 승인 뒤): 기존 f1~f10 유지 + f11 PASS, 답 출처 두 개 이하 관찰.
- 그 뒤 v0.7.13 릴리스 → PIA 고정 버전 올리기(requirements·lock·README·테스트) → bot-only 배포 → Telegram 재확인(같은 질문).

## Codex 검토 반영 (2026-10-17, 기준 8c82271)

- [P2] ProductiveAssets 값을 "유형자산 취득"으로 보여 주던 문제: 이 태그에서 온 값은 결과에 `capex (유형·무형자산 취득)`으로 적는다(NVIDIA 공시 표현과 같음). 테스트에 이름 확인 추가.
- [비차단] "첫째 태그가 없을 때만"이라는 계획 설명을 실제 규칙(기간마다 최신 정기공시, 같은 공시면 후보 순서)으로 고쳤다.
