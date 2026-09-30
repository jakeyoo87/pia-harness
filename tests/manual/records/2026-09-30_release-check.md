# 릴리스 전 시나리오 측정 (2026-09-30, main `fb8d9b2`)

0.6.0 릴리스 전 확인. read 35개와 execution 26개를 각각 한 대화로 실행했다(실제 OpenRouter `openai/gpt-6-luna`, Exa(키), NAVER API HUB 뉴스(키), Jina(키 없음); 주문은 가짜 pia-broker). 이전 측정 뒤 바뀐 것: `news_search`(네이버 뉴스)와 네이버 기사 직접 읽기, Exa 키 필수, 호출 직전 압축·크기 확인 제거, 압축 뒤 구역별 크기(Summary 1만 자, 최근 Turn 10만 자, Memory 목표 2천 자). 입력 크기 열은 바이트 기준 추정치다.

## read

```text
===== summary (id | expect | model steps | check | time | max input tokens | calls model/search/page/notes)
1 | answer | answer | PASS | 2.6s | 5540 | 1/0/0/0
2 | news_search | news_search > news_search+news_search+news_search > web_extract+web_extract+web_extract > news_search > web_extract > answer | PASS | 54.8s | 30467 | 6/5/5/5
2-1 | news_search | news_search+news_search+news_search+news_search > web_extract+web_extract+web_extract+web_extract > answer | PASS | 34.2s | 49599 | 3/4/6/6
2-2 | news_search | news_search+news_search+news_search+news_search > web_extract+web_extract+web_extract+web_extract > answer | PASS | 40.2s | 65039 | 3/4/4/4
2-3 | answer | answer | PASS | 1.4s | 61229 | 1/0/0/0
3 | web_extract | web_extract > answer | PASS | 17.1s | 64669 | 2/0/1/1
6 | answer | answer | PASS | 3.1s | 65990 | 1/0/0/0
6-1 | answer | answer | PASS | 2.7s | 66654 | 1/0/0/0
4 | answer | memory > answer | PASS | 6.4s | 67311 | 2/0/0/0
4-1 | answer | answer | PASS | 1.5s | 67663 | 1/0/0/0
5 | - | answer | OBSERVE | 3.3s | 67804 | 1/0/0/0
5-1 | web_search | news_search+news_search+news_search+news_search > web_extract+web_extract+web_extract+web_extract > answer | CHECK | 44.0s | 86506 | 3/4/4/4
10 | answer | memory > answer | PASS | 4.9s | 82815 | 2/0/0/0
7 | news_search | news_search > web_extract > answer | PASS | 18.6s | 93682 | 3/1/4/4
9 | web_search | news_search+news_search+news_search > web_extract+web_extract+web_extract > news_search > web_extract > answer | CHECK | 55.0s | 117013 | 5/4/8/8
11 | answer | answer | PASS | 4.8s | 113636 | 1/0/0/0
12 | web_search | web_search > web_search > web_search > web_extract > answer | PASS | 30.0s | 125934 | 5/3/1/1
13 | news_search | web_search+web_search+news_search > web_search+web_search+web_search > answer | PASS | 15.0s | 143208 | 3/6/0/0
14 | web_extract | web_extract > web_search > web_extract > answer | PASS | 33.5s | 136563 | 4/1/3/2
15 | web_search | news_search+news_search+news_search+news_search > web_extract+web_extract+web_extract+web_extract > answer | CHECK | 37.1s | 157200 | 3/4/4/4
15-1 | web_extract | web_extract+web_extract+web_extract+web_extract > news_search > web_extract > answer | PASS | 64.7s | 169925 | 4/1/5/5
15-2 | web_extract | answer | CHECK | 8.2s | 170998 | 1/0/0/0
15-3 | answer | answer | PASS | 4.9s | 172962 | 1/0/0/0
16 | web_extract | web_extract > answer | PASS | 13.5s | 176037 | 2/0/1/1
16-1 | answer | answer | PASS | 2.6s | 176990 | 1/0/0/0
17 | news_search | news_search+news_search+news_search+news_search > web_extract+web_extract+web_extract+web_extract > answer | PASS | 38.6s | 196408 | 3/4/4/4
17-1 | web_extract | answer | CHECK | 7.6s | 193652 | 1/0/0/0
18 | web_extract | web_extract+web_extract > web_search > answer | PASS | 18.9s | 205772 | 3/1/2/2
19 | web_search | web_search+web_search+web_search+web_search > web_extract+web_extract+web_search+web_search > web_search+web_search+web_search+web_search > web_extract+web_extract+web_extract+web_extract > web_search+web_search+web_search+web_search > web_search+web_search+web_search+web_search > answer | PASS | 103.3s | 266951 | 7/15/5/5
20 | web_search | web_search+web_search+news_search > web_extract > answer | PASS | 26.1s | 246293 | 3/3/2/2
21 | web_search | web_search > web_search+web_search+web_search+web_search > web_extract > answer | PASS | 28.2s | 252534 | 4/5/2/2
22 | answer | memory > answer | PASS | 10.1s | 248101 | 2/0/0/0
23 | news_search | news_search+news_search+news_search+memory > web_extract+web_extract+web_extract > answer | PASS | 20.7s | 263541 | 3/3/3/3
24 | web_search | web_search+news_search+web_search > web_extract > web_search+news_search+web_search > web_search > answer | PASS | 47.9s | 283459 | 5/7/3/3
24-1 | answer | answer | PASS | 5.6s | 271667 | 1/0/0/0
```

**질문: "오늘 삼성전자 관련 주요 뉴스 알려줘"(2) 외 뉴스 질문** — 2, 2-1, 2-2, 7, 17, 23이 `news_search`를 썼다. CHECK인 5-1("SK하이닉스"), 9("삼성전자와 SK하이닉스의 HBM 전략을 기사 내용 기준으로 비교해줘"), 15("전력기기 회사 관련 주요 이슈는 ?")도 뉴스성 질문에 `news_search`를 쓴 것으로, 기대값이 예전 기준이다. 주가(12, 24)와 ETF 조사(19, 21)는 `web_search`를 썼다.

**질문: "혹시 기판 관련 ETF 뭐가 있는지 조사해볼 수 있어 ?"(19)** — 검색 15회·페이지 5개, KODEX AI반도체TOP2플러스(삼성전기 19.13% 등), KIWOOM 글로벌MLCC&AI기판TOP4+(상장 예정), PLUS 코리아HBM반도체를 번호 링크와 함께 정리했다. 103초(조사 한도 90초 안에서 조사를 마치고 답변).

**질문: "삼성전자 주가 지금 다시 알려줘"(24)** — 다시 검색해 11시 15분 기준 27만원(-0.92%), 실시간은 아니라고 밝혔다.

**질문: "아까 처음 알려준 삼성전자 주가로 100주 사면 얼마였어?"(24-1)** — 12번에서 알려준 27만4,250원으로 2,742만5,000원을 계산했다(다시 조회하지 않음).

**질문: "크발로닉스테크 최근 뉴스 알려줘"(13)** — 확인하지 못했다며 회사명·종목코드를 물었다(이번 질문에 답함).

- 본문 추출 66건 중 40건이 네이버 기사 직접 읽기였다. 읽지 못함 3건.
- Exa 429 0건, 조사 시간 초과 0건, 출처 목록 중복 0건(모두 번호 링크).
- 실제 사용량이 256K 토큰에 닿지 않아 **Compaction은 일어나지 않았다**(압축 뒤 동작은 오프라인 테스트로만 확인).

## execution

```text
===== summary (id | expect | model steps | check | time | max input tokens | calls model/search/page/notes)
o0 | answer | answer | PASS | 3.5s | 6740 | 1/0/0/0
o1 | order | order > answer | PASS | 6.1s | 7027 | 2/0/0/0
o1-1 | confirm | confirm > answer | PASS | 4.9s | 8894 | 2/0/0/0
o3 | order | order > answer | PASS | 4.2s | 8815 | 2/0/0/0
o3-1 | order | order > answer | PASS | 5.7s | 10676 | 2/0/0/0
o3-2 | answer | answer | PASS | 5.2s | 11685 | 1/0/0/0
o6 | order | order > answer | PASS | 6.3s | 10983 | 2/0/0/0
o6-1 | order | order > answer | PASS | 3.1s | 11600 | 2/0/0/0
o6-2 | - | news_search+web_search+web_search > web_extract+web_extract > answer | OBSERVE | 19.8s | 34794 | 3/3/4/4
o9 | order | order > answer | PASS | 6.8s | 27821 | 2/0/0/0
o9-1 | order | order > answer | PASS | 5.1s | 29683 | 2/0/0/0
o9-2 | order | order > answer | PASS | 10.1s | 30803 | 2/0/0/0
o9-3 | confirm | confirm > answer | PASS | 3.0s | 31902 | 2/0/0/0
o13 | order | order > answer | PASS | 3.9s | 31884 | 2/0/0/0
o13-1 | answer | answer | PASS | 3.2s | 33826 | 1/0/0/0
o13-2 | answer | answer | PASS | 2.9s | 33147 | 1/0/0/0
o16 | order | order > answer | PASS | 4.6s | 33419 | 2/0/0/0
o16-1 | order | order > answer | PASS | 7.5s | 35280 | 2/0/0/0
o16-2 | confirm | confirm > answer | PASS | 2.8s | 36272 | 2/0/0/0
o19 | order | order+order > answer | PASS | 7.1s | 36208 | 2/0/0/0
o19-1 | confirm | confirm > answer | PASS | 2.9s | 38916 | 2/0/0/0
o20 | order | order+order > answer | PASS | 5.3s | 39044 | 2/0/0/0
o20-1 | confirm | confirm > answer | PASS | 2.8s | 41742 | 2/0/0/0
o21 | order | order > answer | PASS | 4.2s | 41644 | 2/0/0/0
o21-1 | web_search | news_search > web_extract > answer | CHECK | 22.7s | 51161 | 3/1/2/2
```

- 주문 준비 → "응" 확정 → 접수, 여러 건 확정(o19-1), 일부 확정(o20-1 "삼전만 해줘")이 모두 맞게 동작했다. "과거 Turn은 끝난 것" 지침 때문에 확정을 못 하는 일은 없었다.
- **질문: "오늘 삼성전자 뉴스 확인해보고 괜찮으면 사줘"(o21-1)** — `news_search`로 찾고 네이버 기사 2건을 직접 읽은 뒤 호재·부담이 섞였다며 확정하지 않았다(이전과 같은 판단). CHECK는 기대값이 예전 도구 이름(`web_search`)이라서다.
- **문제: 확인 질문 중복 4건(o9-1, o9-2, o13, o21)**. 예: "삼전 1주 사줘"(o21)의 답변에 "삼성전자 1주를 285,500원 지정가로 매수할까요? (11:24 기준 현재가 285,500원)"이 모델이 쓴 것과 Harness가 붙인 것으로 두 번 나왔다. 주문 동작에는 영향이 없다. 도구 기록 저장 전(2026-09-29) 측정에는 없었다. 대응: `plans/2026-09-30_harness-appended.md`.
