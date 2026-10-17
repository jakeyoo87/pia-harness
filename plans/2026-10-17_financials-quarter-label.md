# past_financials 0.7.14: 미국 분기에 달력 분기 이름, "도구 먼저" 한 줄

- 작성: 2026-10-17, Claude (EC2). 구현: Claude / 검토: Codex(계획 → 구현).
- 저장소: pia-harness. 다음 릴리스 v0.7.14, 그 뒤 PIA 고정 버전 올리기·Bot 배포.
- 앞선 계획: `plans/2026-10-17_financials-followups.md`(v0.7.13, PIA dev Bot 배포 2026-10-17).

## 배경: 0.7.13 뒤 실제 대화(2026-10-17, 사용자 Telegram)

- "마이크로소프트 최근 4개 분기 실적"에서 모델이 도구를 건너뛰거나 웹을 여러 번 검색했다.
- 사용자 대화를 읽기 전용으로 복사해 같은 질문을 다시 던지는 재현 스크립트로 확인했다(실제 시스템 지침·도구, 메모리 저장소).
  - 지금 설명: 3번 중 2번 웹 먼저.
  - 설명에 "도구 먼저" 한 줄: 3번 모두 도구 먼저. 그래도 웹 검색 6~14번.
  - 시스템 지침에 같은 규칙 추가: 더 나아지지 않음.
- 검색어를 보니 모델은 숫자가 아니라 **분기 이름**을 찾고 있었다("Microsoft FY2026 Q1 … Q4"). 미국 결과는 기간을 종료일 날짜로만 주고, 같은 대화 앞의 엔비디아 답은 "FY2027 2분기"처럼 회계 분기 이름을 썼다. 날짜만으로는 회계 분기 이름을 확신할 수 없어 웹으로 갔다.

## 사용자 결정

1. 회계연도·회계 분기 이름(FY2027 Q1)은 쓰지 않는다. 대부분 사람에게 익숙하지 않다.
2. 기간은 **달력 분기 이름**으로 쓴다(답에서는 '26.3Q처럼). 이름은 모델이 짓지 않고 **도구가 붙여서** 준다(모델에게 맡기면 확인하러 다시 웹에 갈 수 있음).
3. 이름 규칙은 SEC `frame` 대신 **우리 규칙 하나**(권고대로): `frame`은 같은 기간이 여러 공시에 있으면 하나에만 붙고, 계산한 Q4에는 없어서 빈 곳을 채우는 규칙이 따로 필요하다.

## 변경

- `past_financials.py` `render`, 미국 분기(`country == "us"`, `frequency == "quarterly"`)만:
  - 기간 이름 = **종료일 45일 전(분기의 가운데)이 속한 달력 분기**, 형식은 국내와 같은 `YYYY-Qn`.
    - 마이크로소프트 2026-07-01..2026-09-30 → `2026-Q3`
    - 엔비디아 2026-04-27..2026-07-26 → `2026-Q2`
    - 애플 2026-03-29..2026-06-27 → `2026-Q2`
  - 기간 줄: `Period 2026-Q3 (ends 2026-09-30)`. 값 줄의 실제 기간(span)은 그대로 둔다. 머리말 `latest filed period`도 같은 이름.
  - 머리말 `year basis`를 미국 분기에서는 `calendar quarter of each period's middle` 로 적는다.
  - 기간을 고르고 정렬하는 기준은 지금처럼 종료일이다(이름만 붙임). 미국 연간과 국내는 바꾸지 않는다.
- 도구 설명에 두 줄:
  - "For a company's past revenue, profit, balance sheet or cash flow, call this tool first, even when earlier turns answered such questions with web search."
  - "US quarters come labeled with their calendar quarter (e.g. 2026-Q3); use these labels with the dates and do not look up fiscal-quarter names."
- README 6.2.4 한 줄(미국 분기 이름 규칙).
- 단위 테스트: 가짜 SEC 자료(엔비디아·애플, 달력과 어긋난 분기)에서 기간 이름과 `latest filed period`, 설명의 두 줄.

## 검증

- 단위 테스트 전체 통과, 바꾼 파일 ruff·format 통과.
- 모델 시나리오 `--set financials`(유료, 사용자 승인 뒤): f1~f11 유지.
- 재현 스크립트(유료·실제 SEC 호출, 사용자 승인 뒤): 새 코드로 같은 마이크로소프트 질문 3번 → 도구 먼저, 분기 이름 웹 검색 없음. 이때 실제 SEC `frame`과 우리 이름이 같은지도 한 번 비교해 기록한다(코드에는 쓰지 않음).
- 그 뒤 v0.7.14 릴리스 → PIA 고정 버전 올리기 → bot-only 배포 → Telegram 재확인(같은 질문).

## 범위 밖

- 국내 기간 이름(이미 `YYYY-Qn`, 사업연도 기준).
- 미국 연간 기간 이름, `end_year` 인자의 뜻(그대로 종료일의 달력 연도).
- 시스템 지침 변경(재현에서 효과 없음).
