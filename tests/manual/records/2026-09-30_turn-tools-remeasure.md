# Turn 도구 기록 재측정 (2026-09-30, `e2fa1f0`)

read 33개를 한 대화로 실행했다(실제 OpenRouter·Exa·Jina, Exa·Jina는 키 없음). 이전 측정(`037f471`, `2026-09-29_turn-tools.md`) 뒤 바꾼 것: 이전 Turn 뒤 경계선 한 줄, 검색 후보는 제목·날짜·링크만 저장, 동시 읽기 3개, 조사 시간 90초, 압축 기준 256K 직접 지정. smoke 도구의 예산은 `context_limit=1,050,000`에 기본 정책(256K에서 압축)이다. 입력 크기가 256K에 닿지 않아 **Compaction은 일어나지 않았다**. 입력 크기 열은 바이트 기준 보수 추정치다.

```text
===== summary (id | expect | model steps | check | time | max input tokens | calls model/search/page/notes)
1 | answer | answer | PASS | 17.0s | 4256 | 1/0/0/0
2 | web_search | web_search > web_extract > answer | PASS | 31.3s | 18447 | 3/1/4/4
2-1 | web_search | web_search > web_extract+web_search+web_search > answer | PASS | 36.3s | 36541 | 3/3/4/4
2-2 | web_search | web_search > web_search+web_search+web_search > web_search+web_search+web_search > web_search > web_extract > answer | PASS | 59.6s | 62564 | 6/8/5/5
2-3 | answer | answer | PASS | 2.7s | 48809 | 1/0/0/0
3 | web_extract | web_extract > answer | PASS | 18.8s | 51447 | 2/0/1/1
6 | answer | answer | PASS | 3.3s | 52555 | 1/0/0/0
6-1 | answer | answer | PASS | 3.1s | 52980 | 1/0/0/0
4 | answer | memory > answer | PASS | 5.5s | 53641 | 2/0/0/0
4-1 | answer | answer | PASS | 1.4s | 54008 | 1/0/0/0
5 | - | answer | OBSERVE | 5.6s | 54149 | 1/0/0/0
5-1 | web_search | web_search > web_extract > answer | PASS | 38.5s | 67160 | 3/1/4/4
10 | answer | memory > answer | PASS | 6.1s | 65100 | 2/0/0/0
7 | web_search | web_search > web_search > web_extract > web_search > web_extract > answer | PASS | 48.2s | 78994 | 6/3/5/5
9 | web_search | web_search+web_search+web_search > web_extract+web_extract > answer | PASS | 39.3s | 99398 | 3/3/6/6
11 | answer | answer | PASS | 4.7s | 93281 | 1/0/0/0
12 | web_search | web_search > web_extract > web_search > web_extract > web_search > web_search > web_extract > answer | PASS | 68.9s | 112303 | 8/4/5/5
13 | web_search | web_search > web_search+web_search+web_search+web_search > answer | PASS | 16.6s | 124067 | 3/5/0/0
14 | web_extract | web_extract > web_search > web_extract > answer | PASS | 35.5s | 119241 | 4/1/4/3
15 | web_search | web_search+web_search+web_search > web_extract+web_extract > answer | PASS | 42.5s | 139622 | 3/3/5/3
15-1 | web_extract | web_extract > answer | PASS | 37.9s | 138403 | 2/0/3/3
15-2 | web_extract | answer | CHECK | 6.3s | 140467 | 1/0/0/0
15-3 | answer | answer | PASS | 3.3s | 141990 | 1/0/0/0
16 | web_extract | web_extract > answer | PASS | 30.1s | 145945 | 2/0/1/1
16-1 | answer | answer | PASS | 3.4s | 147143 | 1/0/0/0
17 | web_search | web_search > web_extract+web_extract > answer | PASS | 33.3s | 161285 | 3/1/4/4
17-1 | web_extract | web_extract > answer | PASS | 30.4s | 164370 | 2/0/2/2
18 | web_extract | web_extract > answer | PASS | 22.1s | 170084 | 2/0/2/2
19 | web_search | web_search+web_search+web_search+web_search > web_search+web_search+web_search+web_search > web_search+web_search+web_search+web_search > web_extract+web_extract > web_search > answer | PASS | 72.7s | 233768 | 6/13/6/5
20 | web_search | web_search > web_extract > answer | PASS | 26.9s | 208074 | 3/1/3/3
21 | web_search | web_search > web_extract > answer | PASS | 31.8s | 213510 | 3/1/3/3
22 | answer | memory > answer | PASS | 8.3s | 212600 | 2/0/0/0
23 | web_search | web_search+web_search+web_search+web_search > web_extract+web_extract > memory > answer | PASS | 44.9s | 239408 | 4/4/6/6
```

## 확인한 점

**13** "크발로닉스테크 최근 뉴스 알려줘" (가상 회사)
- 검색 5번 뒤 "그 이름으로는 확인하지 못했다, 회사명·종목 코드를 확인해 달라"고 답했다. 이전 측정에서는 몇 Turn 전 질문(9번)에 답했다.

**19** "혹시 기판 관련 ETF 뭐가 있는지 조사해볼 수 있어 ?"
- 검색 13번·페이지 6개, 72.7초. 이번 질문에 답했다(PLUS 코리아HBM반도체, KIWOOM 글로벌MLCC&AI기판TOP4+ 상장 준비, SOL 반도체후공정). 이전 측정에서는 17번 질문에 답했다.

**15-2** "그중 두 번째 기사만 자세히 알려줘"
- 도구 없이 15-1에서 저장된 페이지 요약으로 6.3초에 답했다(투자액·기간·차단기 합작 등 세부 포함). 기대값이 `web_extract`라 CHECK로 표시됐지만, 저장된 조사를 다시 쓰는 것이 이 기능의 목적이다.

**17-1** "첫 번째랑 세 번째 기사 본문 비교해줘"
- 검색 없이 링크를 바로 읽었다(이전 측정은 검색 뒤 읽음).

**그 밖**
- 입력 크기 최대(추정) 약 24만, 이전 측정 약 34만. 둘 다 바이트 기준 추정이라 서로 비교만 가능하고 실제 토큰 256K 도달 여부는 알 수 없다.
- Exa 429 0건, 조사 시간 초과 0건.
- 경계선·저장본 축소·동시 3개·90초가 함께 바뀌어 어느 하나를 13·19번 정상화의 단독 원인으로 확정할 수 없다. 재발할 때 조건을 나눠 측정한다.

## 남은 문제

**출처 목록 중복 (6/33: 15·17·18·19·20·23)**
- 모델이 답변 끝에 직접 "출처" 목록을 쓰고, Harness 목록이 그 뒤에 또 붙었다. 모양은 번호만 남은 줄(`[1]`, 17·18·19·20)과 언론사 이름 + 번호(`뉴시스 [2]`, 15·23) 두 가지였다.
- 누적 없던 측정 0건 → 누적 측정 3건 → 이번 6건. 저장된 이전 답변 끝의 Harness 출처 목록을 모델이 흉내 내는 것으로 본다.
- 다음 브랜치에서 다룬다(사용자 결정). 후보: 답변 끝의 좁은 정리 규칙, 또는 Context에 넣는 과거 답변을 모델이 쓴 모양(`<url>`, 목록 없음)으로 두는 근본 해결.

**Compaction 실측 없음**
- 0.6.0 릴리스·PIA 연동 전 수용 항목: 실제 `usage.total_tokens`로 압축 발생·실패를 확인한다.
