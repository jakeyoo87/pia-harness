# 번호 링크 출처 측정 (2026-09-30, `e4870d3`)

read 35개를 한 대화로 실행했다(실제 OpenRouter·Exa·Jina, Exa·Jina는 키 없음). 바꾼 것: 출처를 문장 안 번호 링크 `[n](url)`로, 답변 끝 목록 없음, 과거 대화 지침 두 문장, 시나리오 24·24-1 추가. 예산은 `context_limit=1,050,000`, 기본 정책(256K에서 압축). 입력 크기 열은 바이트 기준 추정치다.

```text
===== summary (id | expect | model steps | check | time | max input tokens | calls model/search/page/notes)
1 | answer | answer | PASS | 2.1s | 4454 | 1/0/0/0
2 | web_search | web_search > web_extract+web_search+web_search > web_extract+web_search+web_search > web_extract+web_search+web_extract > answer | PASS | 65.7s | 43569 | 5/6/9/9
2-1 | web_search | web_search > web_search+web_search+web_search > web_extract+web_search+web_search > web_extract+web_extract > answer | PASS | 56.7s | 68676 | 5/6/8/8
2-2 | web_search | web_search > web_extract+web_search+web_search+web_search > web_extract+web_search+web_search+web_search > answer | PASS | 54.3s | 92901 | 4/7/5/5
2-3 | answer | answer | PASS | 1.3s | 72607 | 1/0/0/0
3 | web_extract | web_extract > answer | PASS | 17.7s | 75102 | 2/0/1/1
6 | answer | answer | PASS | 2.7s | 76140 | 1/0/0/0
6-1 | answer | answer | PASS | 2.1s | 76500 | 1/0/0/0
4 | answer | memory > answer | PASS | 5.6s | 77144 | 2/0/0/0
4-1 | answer | answer | PASS | 1.7s | 77531 | 1/0/0/0
5 | - | answer | OBSERVE | 3.2s | 77686 | 1/0/0/0
5-1 | web_search | web_search > web_extract+web_search+web_search > web_extract+web_search+web_search > answer | PASS | 51.4s | 103472 | 4/5/6/6
10 | answer | memory > answer | PASS | 5.2s | 93621 | 2/0/0/0
7 | web_search | web_search > web_extract+web_extract+web_extract > answer | PASS | 33.2s | 108291 | 3/1/5/5
9 | web_search | web_search > web_extract+web_search+web_search > web_extract+web_extract > answer | PASS | 55.9s | 138496 | 4/3/7/7
11 | answer | answer | PASS | 3.3s | 128466 | 1/0/0/0
12 | web_search | web_search > web_search+web_search+web_search > web_extract > answer | PASS | 35.6s | 140206 | 4/4/1/1
13 | web_search | web_search > web_search+web_search+web_search+web_search > answer | PASS | 13.6s | 153725 | 3/5/0/0
14 | web_extract | web_extract > web_search+web_search+web_search+web_search > web_extract+web_extract > answer | PASS | 33.5s | 155400 | 4/4/5/3
15 | web_search | web_search > web_extract+web_search+web_search+web_search+web_search > web_extract+web_search+web_search+web_extract > answer | PASS | 53.8s | 205245 | 4/7/9/9
15-1 | web_extract | answer | CHECK | 11.2s | 179357 | 1/0/0/0
15-2 | web_extract | web_extract > answer | PASS | 22.8s | 185301 | 2/0/1/1
15-3 | answer | answer | PASS | 5.6s | 187259 | 1/0/0/0
16 | web_extract | web_extract > answer | PASS | 21.8s | 192117 | 2/0/1/1
16-1 | answer | answer | PASS | 3.5s | 193286 | 1/0/0/0
17 | web_search | web_search > web_extract+web_search+web_search > web_extract+web_extract > answer | PASS | 45.1s | 217937 | 4/3/7/7
17-1 | web_extract | answer | CHECK | 7.6s | 212336 | 1/0/0/0
18 | web_extract | web_extract+web_extract > web_search > answer | PASS | 30.7s | 225340 | 3/1/2/2
19 | web_search | web_search+web_search+web_search+web_search > web_search+web_search+web_search+web_search+web_search+web_search > web_search+web_search+web_search+web_search > web_extract > web_search+web_search+web_search+web_search > answer | PASS | 57.3s | 254656 | 6/18/5/4
20 | web_search | web_search > web_search > answer | PASS | 6.5s | 64643 | 3/2/0/0
21 | web_search | web_search > web_search > web_search > web_search > web_search > answer | PASS | 14.8s | 67928 | 6/5/0/0
22 | answer | memory > answer | PASS | 7.9s | 68853 | 2/0/0/0
23 | web_search | web_search+web_search+memory > answer | PASS | 13.4s | 70915 | 2/2/0/0
24 | web_search | web_search > web_search > answer | PASS | 6.7s | 73246 | 3/2/0/0
24-1 | answer | answer | PASS | 3.6s | 73663 | 1/0/0/0
```

## 확인한 점

**출처**
- 출처 목록 중복 0건. 모델이 직접 목록을 쓴 답변도 0건.
- 링크를 쓴 답변은 모두 번호 링크였다. 링크 없는 `[n]`, 남은 `<url>` 0건.

**과거 조사 재사용**
- 15-1 "기사들 본문 읽어보고 요약해줘", 17-1 "첫 번째랑 세 번째 기사 본문 비교해줘": 도구 없이 저장된 페이지 요약으로 답했다(기대값이 `web_extract`라 CHECK).

**Compaction 첫 실측**
- 19번 뒤 입력 추정치가 약 25만 → 약 6만으로 줄었다. 압축 실패 표시는 없고 이후 대화가 이어졌다. 이 스크립트는 Summary 내용을 남기지 않았다(아래 24-1 원인을 가리지 못함 → 이후 스크립트가 새 Summary를 출력하도록 고침).

## 문제

**24-1 "아까 처음 알려준 삼성전자 주가로 100주 사면 얼마였어?" — 오답**
- 12 "삼성전자 지금 주가 얼마야?"에서 "9월 29일 종가 27만 2,500원"을 알려줬는데, "가격을 알려드린 적이 없어요"라고 답했다.
- 12번은 압축으로 Summary에 들어간 Turn이다. 원인 후보: Summary에 가격이 빠짐, 또는 바로 앞 24번의 조회 실패와 혼동. Summary 내용이 기록되지 않아 확정하지 못했다.
- 대응: 지침에 "Summary는 오래된 Turn을 대신하며 세부가 빠진다. 필요한 세부가 없으면 준 적 없다고 하지 말고 도구로 다시 찾거나, 대화에 더는 없다고 말하라"를 추가했다. 모델은 Summary가 세부를 잃는다는 것을 알 수 없었다(이름표는 "Conversation Summary"뿐).

**Exa 429 24건 (19번부터)**
- 19번 일부, 20·21·24번은 검색 결과를 전혀 얻지 못했다. 모델은 확인하지 못했다고 답했다(수치를 지어내지 않음).
- 20·21번처럼 순차 호출도 막혀 동시성보다 키 없는 호출의 사용량 한도로 본다. 실사용 전 Exa 키가 필요하다.
- 24 "삼성전자 주가 지금 다시 알려줘"는 다시 검색했으나(행동은 맞음) 결과를 얻지 못해 검증되지 않았다.

## 다음 측정

- Exa 한도가 풀린 뒤 또는 키를 쓸 수 있을 때 read 세트를 다시 돌린다: 24·24-1, 새 Summary 내용, 20·21번.
