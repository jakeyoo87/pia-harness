# 시장 소식 DB schema 4에 맞추기: 섹터 7개, 목표가 증권사 (2026-10-20)

- 작성: Claude (EC2). 검토: Local Codex(계획 → 구현). 다음 릴리스 v0.7.19, 그 뒤 PIA 고정 버전 올리기·Bot 배포.
- 기준: pia-harness main `ad7b60f`(v0.7.18).

## 배경

- Market Daily(매일 07:30 KST 전 회원 시장 요약, 별도 계획) 준비 중 사용자가 시장 소식 DB를 바꾸기로 했다. 수집기(pia-collector main `abd658b`, 계획 `plans/2026-10-20_market-daily-prep.md`)가 **2026-10-20 11:46에 실제 DB를 schema 4로 바꿨고, 12:01부터 S3에 schema 4가 올라간다.**
  - 섹터 8개 → 7개: `반도체`, `AI·SW·DC`, `매크로`, `2차전지·전기차`, `에너지·방산·산업재`, `소비재`, `기타`(금융·바이오 포함). 기존 30일 치도 다시 분류했다.
  - `targets.broker` 칸 추가: 목표가를 낸 증권사 이름(국내는 한글 공식명, 해외는 영문 공식명, 회사마다 한 표기), 모르면 NULL. 기존 278줄 중 273줄이 채워졌다. 증권사 이름이 DB에 남는 곳은 이 칸 하나이고, 본문·요약·키워드·일정에서는 지금처럼 지운다.
- 사용자 결정(2026-10-20): **목표가를 말할 때만 증권사 이름을 허용한다.** Market Daily와 PIA 답변이 같은 규칙을 쓴다. 그 밖의 소식·의견에는 지금처럼 채널·증권사·사람 이름을 쓰지 않는다.
- 지금 dev Bot(v0.7.18)의 도구 설명에는 옛 섹터 이름이 있어 `message_sectors`로 찾으면 결과가 비고, 출처 규칙은 증권사 이름을 모두 막는다. 이 계획이 배포될 때까지 어긋나 있다(dev Bot, 사용자 1명).

## 변경 (pia-harness)

- `src/pia_harness/market_feed.py`
  - `DESCRIPTION`: 섹터 목록을 7개로 바꾼다. `targets` 칸에 `broker`를 더하고 "the brokerage that issued the target, or null"로 적는다.
  - `SOURCE_RULE`: 출처는 기본적으로 유형 이름표로만 쓴다. **목표가를 설명할 때만** 그 목표가 행의 `targets.broker` 값을 증권사 이름으로 쓸 수 있고, 값이 null이면 이름을 추정하지 않는다. 채널·애널리스트·사람 이름은 쓰지 않는다. 목표가 없이 투자의견만 말할 때는 이름을 쓰지 않는다.
    - 예: "Source rule: cite these only by type, as 뉴스 (…), 증권사 의견 (broker), 커뮤니티 의견 (community) or 시장 알림 (alert). Only when you state a price target, you may name the brokerage given in that target's targets.broker row; if it is null, do not guess one. Never name the channels, analysts or people behind them. These are the channels' messages, not verified facts."
    - 이 문장은 결과 머리와 Turn에 남는 짧은 사본(`kept_text`)에 같이 들어가므로 다음 Turn에도 보인다(바뀌는 곳 없음).
  - 모듈 docstring의 `schema_version 3` → 4.
- `README.md` 6.2.5: schema 4, 수집 주기(06~18시 매시간, 그 밖 2시간마다), "채널·사람 이름은 지운다, 증권사 이름은 목표가의 `targets.broker`에만 있다", 답에서 증권사 이름은 목표가에만.
- 테스트(`tests/test_market_feed.py`): 설명 검사 문자열을 새 섹터 목록과 `broker`로, 출처 규칙 검사에 "Name a brokerage only with its price target". 테스트용 DB의 `targets`에 `broker` 칸.
- 수동 시나리오
  - `tests/manual/fake_market_feed.py`: schema 4(섹터 7개, `targets.broker`). 목표가 행에는 **가상의 증권사 이름**(예: 가온증권, 나래증권, Northwind Securities)을 넣는다. 실제 증권사 이름은 쓰지 않는다.
  - `tests/manual/scenarios/market_feed.json`
    - 실제 증권사 이름 `answer_excludes`는 그대로 둔다(모델이 지어내지 않는지).
    - 목표가 행이 있는 질문에는 그 행의 가상 증권사 이름을 `answer_includes`에 더한다: m3 "SK하이닉스 목표주가 바뀐 거 있어?", m14 "대한전선 목표가 어떻게 됐어?", m18 "목표주가 내린 종목들 알려줘", m21 "삼성전자 증권사 의견, 그거 어느 증권사에서 나온 거야?"(가짜 DB의 삼성전자 의견에 목표가 행이 있다. 지금은 "이름을 대지 않는다"를 보는 질문이었는데, 이제 목표가의 증권사는 말해도 된다).
    - m23 추가(관찰): "요즘 AI 데이터센터 쪽 소식 정리해줘". 기록에 SQL을 남겨 옛 섹터 이름을 쓰지 않는지 본다.
    - m24 추가(확인): 목표가가 없는 일반 증권사 의견을 묻는 질문. 가짜 DB에 같은 종목의 목표가 행(가상 증권사)과 목표가 없는 증권사 의견을 함께 두고, 답에 가상 증권사 이름이 나오지 않는지 `answer_excludes`로 본다(이름 허용 사례만 확인하는 빈틈을 막음, Codex).

## 하지 않는 것

- PIA 회원 계획(온보딩)의 "관심 섹터" 보기(`반도체·IT | … | 소비재·플랫폼 …`, pia-agent `app/core.py`). 회원 취향을 묻는 목록이고 DB 분류와 따로 쓰인다. 맞출지는 사용자와 따로 정한다.
- 증권사 이름 정규화 코드(수집기가 한 표기로 적는다).
- Market Daily 본체(별도 계획).

## 검증

- 단위 테스트 전체, ruff·format·mypy, 전체 트리 공백 검사.
- 유료 시나리오 `--set market_feed`(사용자 승인 뒤): m1~m24. 바뀐 것은 m3·m14·m18·m21·m23·m24.
- 그 뒤 v0.7.19 릴리스 → PIA 고정 버전 올리기(requirements.txt·lock·test_build_security·README) → bot-only 배포 → Telegram 확인("SK하이닉스 목표가 최근에 어디서 바꿨어?", "요즘 반도체 분위기 어때?").

## Local Codex 계획 검토 반영 (2026-10-20, 기준 ccd9cfb)

- [P2] 이름 허용 범위가 "목표가·투자의견"(`price target or rating`)이라 목표가 없는 투자의견에도 이름이 허용됐다. 사용자 결정대로 **목표가를 설명할 때만, 그 행의 `targets.broker` 값으로, null이면 추정하지 않음**으로 좁혔다(위 `SOURCE_RULE`).
- 검증에 목표가 없는 일반 의견에서 가상 증권사 이름이 나오지 않는지 보는 m24를 더했다.

## Local Codex 구현 검토 (2026-10-20, 기준 077bf2d)

- 차단 이슈 없음. P2 해결(목표가를 설명할 때만 그 행의 `targets.broker`, null이면 추정 안 함), 섹터 7개·`broker`가 수집기 schema 4와 일치, 결과 전체와 `kept_text`에 같은 출처 규칙, m24 포함.
- Codex 직접 검증: 테스트 256개·subtest 153개, Ruff·mypy 신규 오류 0, format·공백 검사 통과, 가짜 DB를 실제 도구로 조회.
- 릴리스 0.7.19(`pyproject.toml`).
