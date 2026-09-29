# Turn 도구 기록 측정 (2026-09-29, `037f471`)

read 33개를 한 대화로 실행했다(실제 OpenRouter·Exa·Jina). 이전 Turn의 도구 호출·결과가 누적된다. smoke 도구의 예산은 `context_limit=1,050,000`이라 **Compaction이 한 번도 일어나지 않았다**(운영 계획은 약 320K로 두어 약 256K에서 Compaction). 입력 크기 열은 바이트 기준 보수 추정치다.

```text
===== summary (id | expect | model steps | check | time | max input tokens | calls model/search/page/notes)
1 | answer | answer | PASS | 2.6s | 4256 | 1/0/0/0
2 | web_search | web_search > web_extract+web_search+web_search > web_extract+web_search > web_extract > answer | PASS | 64.7s | 35984 | 5/4/7/6
2-1 | web_search | web_search > web_search > web_extract+web_search > answer | PASS | 42.5s | 53646 | 4/3/4/4
2-2 | web_search | web_search > web_search > web_extract > web_search > web_extract > answer | PASS | 67.1s | 74686 | 6/3/6/5
2-3 | answer | answer | PASS | 1.3s | 76931 | 1/0/0/0
3 | web_extract | web_extract > answer | PASS | 22.2s | 80534 | 2/0/1/1
6 | answer | answer | PASS | 6.1s | 81703 | 1/0/0/0
6-1 | answer | answer | PASS | 2.7s | 82604 | 1/0/0/0
4 | answer | memory > answer | PASS | 5.5s | 83275 | 2/0/0/0
4-1 | answer | answer | PASS | 1.7s | 83650 | 1/0/0/0
5 | - | answer | OBSERVE | 4.3s | 83774 | 1/0/0/0
5-1 | web_search | web_search > web_extract > answer | PASS | 39.4s | 92899 | 3/1/2/2
10 | answer | memory > answer | PASS | 5.4s | 94514 | 2/0/0/0
7 | web_search | web_search > web_extract+web_search+web_search > web_extract > answer | PASS | 47.6s | 114328 | 4/3/4/4
9 | web_search | web_search > web_extract+web_search+web_search > web_search > web_search > web_search > web_extract+web_search+web_search > answer | PASS | 67.9s | 150940 | 7/8/4/4
11 | answer | answer | PASS | 3.2s | 154592 | 1/0/0/0
12 | web_search | web_search > web_search > web_extract > answer | PASS | 22.4s | 163046 | 4/2/1/1
13 | web_search | web_search > web_search+web_search+web_search+web_search+web_search > answer | PASS | 16.5s | 180020 | 3/6/0/0
14 | web_extract | web_extract > web_search > web_extract > web_search+web_search+web_search > web_extract > answer | PASS | 60.6s | 194818 | 6/4/4/3
15 | web_search | web_search > web_extract+web_search+web_search > web_search > web_extract > web_search > answer | PASS | 73.1s | 233163 | 6/4/7/7
15-1 | web_extract | web_extract+web_extract+web_extract > answer | PASS | 32.5s | 243603 | 2/0/3/3
15-2 | web_extract | web_extract > answer | PASS | 31.3s | 248041 | 2/0/1/1
15-3 | answer | answer | PASS | 3.8s | 249225 | 1/0/0/0
16 | web_extract | web_extract > answer | PASS | 23.4s | 252222 | 2/0/1/1
16-1 | answer | answer | PASS | 2.4s | 253227 | 1/0/0/0
17 | web_search | web_search+web_search+web_search > web_extract+web_extract+web_extract > answer | PASS | 36.8s | 272421 | 3/3/5/5
17-1 | web_extract | web_search > web_extract > answer | CHECK | 28.0s | 279653 | 3/1/2/2
18 | web_extract | web_extract+web_extract > answer | PASS | 25.8s | 286401 | 2/0/2/2
19 | web_search | web_search+web_search+web_search+web_search > answer | PASS | 18.3s | 300681 | 2/4/0/0
20 | web_search | web_search+web_search > web_extract > answer | PASS | 22.2s | 313516 | 3/2/2/2
21 | web_search | web_search > web_extract > answer | PASS | 25.7s | 318853 | 3/1/2/2
22 | answer | memory > answer | PASS | 10.7s | 319933 | 2/0/0/0
23 | web_search | web_search+web_search+memory > web_extract+web_extract > answer | PASS | 33.2s | 335962 | 3/2/3/3
```

## 판정

- **좋아진 점**: 15-2 "그중 두 번째 기사만"이 검색 없이 바로 읽었다(이전 측정 CHECK). 6·6-1·15-3 같은 이전 내용 질문은 모두 맞게 답했다.
- **심각한 문제: 두 건이 이전 질문에 답했다.**
  - 13 "크발로닉스테크 최근 뉴스"(가상 회사): 검색 6번(2번은 Exa 429) 뒤 9번 질문(HBM 전략 비교)의 답을 했다.
  - 19 "기판 관련 ETF": 검색 4번(1번 429) 뒤 17번 질문(HBM 뉴스)의 답을 했다. 이전 측정(누적 없음)에서는 페이지 8개를 읽고 좋은 답을 냈다.
  - 공통점: 이번 Turn의 조사가 비거나 약할 때, 누적된 이전 Turn의 풍부한 도구 결과(비슷한 모양의 tool 메시지)로 넘어갔다. Context가 약 18만~30만(추정)으로 커진 뒤다.
- **Context가 빨리 커진다**: 33 Turn 뒤 추정 약 34만. 이전 측정(누적 없음) 최대 약 7만.
- **Exa 키 없는 호출의 429**: 모델이 한 응답에 검색 4~6개를 동시에 부르면 일부가 429로 막힌다(키가 필요하다는 기존 판단과 같음).
