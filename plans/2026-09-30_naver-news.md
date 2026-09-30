# 네이버 뉴스 검색 도구 · 네이버 기사 직접 읽기

브랜치 `claude/naver-news` (기준 main `6122d31`, 0.6.0 미릴리스). 사용자와 대화로 정했다. 예전에 설계·측정한 기능(`NaverNewsSearch`, `983d2f6`에서 삭제)을 되살리는 것이라 계획 검토는 건너뛰고 구현 뒤 Codex 검토를 받는다(사용자 결정).

## 목적

- 한국 뉴스 질문은 네이버 뉴스가 강하고 무료다(NAVER API HUB 월 775,000회 무료, 지금은 한시적 전면 무료, 유료 전환 뒤에도 무료 제공량 유지). Exa는 키 무료분이 월 약 1,400회라 뉴스 질문을 네이버로 돌리면 Exa 사용량이 준다.
- 예전 개발자센터 API(`openapi.naver.com`)는 API HUB로 이관 중이고 기존 키는 2027-06-30까지만 유효하다. 이미 있는 API HUB 키(`X-NCP-APIGW-API-KEY-ID`/`-KEY`)를 쓴다.

## 결정 사항 (사용자)

1. 도구는 둘: `news_search`(네이버 뉴스)와 `web_search`(Exa). "네이버 먼저, 없으면 Exa" 같은 순서 규칙은 두지 않는다. 네이버는 키워드만 맞으면 거의 항상 결과를 돌려줘 "없음"을 코드로 판단하기 어렵고, 그러면 Exa가 강한 질문(ETF 구성, 운용사 페이지, `site:`, 해외 자료)도 네이버에서 멈춘다. 모델이 도구 설명을 보고 고른다.
2. 네이버 웹 검색(webkr)은 날짜가 없어 붙이지 않는다. 뉴스만.
3. 한 번에 5건, 관련도순. 링크는 네이버 뉴스 주소가 있으면 그것을, 없으면 언론사 원문.
4. 본문 읽기 도구는 따로 만들지 않고 `web_extract` 하나로 둔다(이름도 유지). 안에서 `https://n.news.naver.com/(mnews/)article/<oid>/<aid>`만 직접 받아 읽고, 그 밖과 실패는 Jina로 읽는다. 모델이 두 읽기 도구 중 고르는 실수를 없애고, 요약·원문 대조·URL 경계를 그대로 쓰기 위해서다.
5. 직접 읽기는 `readability-lxml` 없이 파이썬 기본 파서로 한다. 네이버 기사 페이지는 완성된 HTML이고 본문이 `<article id="dic_area">`에 있다. 실제 기사 9건(경제·정치·사회·IT·방송 영상 기사)에서 본문 요소·제목(`h2#title_area`)·날짜(`data-date-time`)가 모두 잡혔고, 메뉴·광고·관련 기사·댓글은 들어가지 않았다. 스포츠·연예(`m.sports.naver.com`, `m.entertain.naver.com`)는 스크립트로 그려져 Jina로 보낸다.

## 변경

- `news_search.py` 되살림: 도구 이름 `news_search`, 설명에 "한국 뉴스는 여기, 회사·펀드 페이지·ETF 구성·공시·해외·`site:`는 `web_search`", 키 필수(예전과 같음), redirect 따르지 않음·`trust_env=False`(Exa와 같게).
- `exa_search.py` 설명에 "한국 뉴스 기사는 보통 `news_search`가 낫다" 한 문장.
- `web_extract.py`: `_read`가 네이버 기사 주소면 `_read_naver`(GET, redirect 따르지 않음, 200이 아니거나 본문 요소가 없으면 None → Jina). 결과는 "제목\n날짜\n\n본문". 블록 태그는 줄바꿈으로, `script`·`style`은 버림. 이후 요약·원문 대조는 같다.
- `__init__`에 `NaverNewsSearch` 노출. README 1·6.0·6.2·6.2.1·6.2.2. smoke 도구에 `news_search` 등록(`NAVER_API_HUB_CLIENT_ID`·`_SECRET`), 뉴스 시나리오 7개(2, 2-1, 2-2, 7, 13, 17, 23)의 기대 도구를 `news_search`로.

## 테스트

- 예전 `test_news_search.py`(요청 모양, 5건, 태그·엔티티 정리, 날짜, 네이버 링크 우선, 깨진 링크, 실패는 결과 문장, 키 필수) + 도구 이름.
- 네이버 기사 직접 읽기: 기사 주소 하나에만 GET, 제목·날짜·본문만(메뉴·관련 기사·댓글·스크립트 제외), 줄바꿈, `&nbsp;`. 본문 없음·redirect·오류 상태는 Jina로. 다른 네이버 주소(스포츠, 옛 `main/read`, http)는 Jina로.

## 측정 (유료, 실행 전 사용자 승인)

- read 세트: 뉴스 질문이 `news_search`로, 조사형(19, 21)·주가(12, 24)가 `web_search`로 가는지, 네이버 기사 읽기가 직접 경로로 되는지(Jina 호출 수), 24·24-1과 새 Summary(이전 측정의 남은 항목).

## PIA 연동 때

- `NaverNewsSearch(client_id, client_secret).tool()` 등록(키는 `pia/dev/naver-api-hub`). "NAVER 비밀 삭제" 할 일은 취소. Telegram 진행 표시에 `news_search` 문구.

## 검토 요청 시 볼 점

1. 두 검색 도구의 설명이 모델의 선택을 충분히 가르는지, 겹치는 질문에서 문제가 없는지.
2. 네이버 직접 읽기의 보안 경계(정확한 호스트·https·redirect 없음·그 밖의 주소는 직접 접속하지 않음)와 실패 시 Jina 경로가 맞는지.
3. 정규식으로 본문·제목·날짜 영역을 자르고 HTMLParser로 글자를 뽑는 방식이 충분히 단순하고 안전한지.
4. 빼도 되는 것, 빠진 것.

## Codex 검토 (2026-09-30)

기준 `main 6122d31...fb67925`의 구현·테스트·README를 대조했다. 검토 외 코드·README 수정, 병합, AWS 변경, 배포, 실제 NAVER·Exa·Jina·모델 호출은 하지 않았다. 로컬 `.venv`에서 새 검색·본문 테스트 22개와 전체 pytest 173개(추가 subtest 96개)가 통과했고 `git diff --check`도 통과했다. Ruff는 재실행하지 않았다.

### Blocker

없다. `news_search`의 endpoint·인증 헤더·`query/display/start/sort`와 `items` 처리(`src/pia_harness/news_search.py` 22, 63–69, 94–127행)는 [NAVER API HUB 뉴스 검색 명세](https://api.ncloud-docs.com/docs/naver-api-hub-search-news)와 맞는다. 인자가 없거나 응답·연결이 잘못되면 후보 없는 관측을 돌려주고 키를 로그·URL에 넣지 않는다. 본문 직접 GET은 `https://n.news.naver.com/(mnews/)article/숫자/숫자` 접두부의 고정 host에만 적용된다(`web_extract.py` 106, 302–326행). 생산 client의 `trust_env=False`·redirect 금지, 그 밖/실패의 Jina 경로와 기존 `url_argument` 대화 링크 경계가 유지된다. 응답에서 추출한 제목·날짜·본문은 기존 요약·원문 대조를 그대로 탄다.

### Non-blocker

1. `tests/manual/README.md` 8행은 `smoke_flow.py`가 OpenRouter 키만 요구하고 Exa는 키 없이 호출한다고 설명한다. 실제 스크립트는 `OPENROUTER_API_KEY`, `EXA_API_KEY`, `NAVER_API_HUB_CLIENT_ID`, `NAVER_API_HUB_CLIENT_SECRET` 네 값을 요구한다(`tests/manual/smoke_flow.py` 399–411행). 수동 측정 전에 그 한 문장을 맞추면 된다.
2. `README.md` 246행과 이 계획서 목적 절은 월 775,000회를 고정된 *무료 제공량*처럼 적었다. [NAVER API HUB 개요](https://guide.ncloud-docs.com/docs/apihub-overview)는 현재 한시적 무료와 검색 카테고리의 월 최대 호출 한도를 구분한다. 장래 유료 전환 뒤 무료량을 단정하지 말고 현재 요금 상태와 호출 상한으로 나눠 쓰는 것이 정확하다. 동작 결함은 아니다.

두 검색 도구의 역할은 설명에만 두고 자동 대체·새 라우터를 만들지 않은 점이 Simple-first에 맞는다. 실제 모델의 도구 선택과 최신 뉴스 품질은 계획된 유료 read 측정에서 확인하면 충분하다. PIA 연동 때는 `news_search` 등록과 NAVER Secret 유지를 반영해야 하며, 이 검토는 그 통합을 수행하지 않았다.
