# 붙인 글 저장 안 함 재측정 (2026-09-30, `claude/harness-appended` `1188007`)

릴리스 전 측정(`2026-09-30_release-check.md`)에서 나온 확인 질문 중복을 고친 뒤(`ebbcb18`: Turn에 모델 답변만 저장, Harness가 붙이는 글은 사용자에게만) execution 26개 전체와 read 8개(한 대화)를 다시 돌렸다. 실제 OpenRouter·Exa·NAVER·Jina, 주문은 가짜 pia-broker.

## execution

```text
===== summary (id | expect | model steps | check | time | max input tokens | calls model/search/page/notes)
o0 | answer | answer | PASS | 2.1s | 6740 | 1/0/0/0
o1 | order | order > answer | PASS | 4.5s | 7021 | 2/0/0/0
o1-1 | confirm | confirm > answer | PASS | 2.8s | 8754 | 2/0/0/0
o3 | order | order > answer | PASS | 4.2s | 8675 | 2/0/0/0
o3-1 | order | order > answer | PASS | 4.1s | 10420 | 2/0/0/0
o3-2 | answer | answer | PASS | 2.8s | 11320 | 1/0/0/0
o6 | order | order > answer | PASS | 3.8s | 10672 | 2/0/0/0
o6-1 | order | order > answer | PASS | 4.2s | 11286 | 2/0/0/0
o6-2 | - | news_search+web_search+web_search > web_extract+web_extract > answer | OBSERVE | 29.4s | 35212 | 3/3/4/4
o9 | order | order > answer | PASS | 4.6s | 27982 | 2/0/0/0
o9-1 | order | order > answer | PASS | 7.3s | 29726 | 2/0/0/0
o9-2 | order | order > answer | PASS | 8.2s | 30695 | 2/0/0/0
o9-3 | confirm | confirm > answer | PASS | 2.9s | 31540 | 2/0/0/0
o13 | order | order > answer | PASS | 5.7s | 31536 | 2/0/0/0
o13-1 | answer | answer | PASS | 3.3s | 33270 | 1/0/0/0
o13-2 | answer | answer | PASS | 3.6s | 32573 | 1/0/0/0
o16 | order | order > answer | PASS | 4.1s | 32898 | 2/0/0/0
o16-1 | order | order > answer | PASS | 11.8s | 34649 | 2/0/0/0
o16-2 | confirm | confirm > answer | PASS | 3.2s | 35629 | 2/0/0/0
o19 | order | order+order > answer | PASS | 6.1s | 35565 | 2/0/0/0
o19-1 | confirm | confirm > answer | PASS | 3.3s | 38025 | 2/0/0/0
o20 | order | order+order > answer | PASS | 3.9s | 38153 | 2/0/0/0
o20-1 | confirm | confirm > answer | PASS | 3.9s | 40614 | 2/0/0/0
o21 | order | order > answer | PASS | 4.4s | 40521 | 2/0/0/0
o21-1 | web_search | news_search+news_search+web_search > web_extract+web_extract > answer | PASS | 29.3s | 60948 | 3/3/4/4
```

- **확인 질문 중복 0건.** 이전 측정에서 중복이던 답변이 이번에는 한 번씩만 나왔다. 예: **질문: "삼전 1주 사줘"(o21)** → "삼성전자 1주, 285,500원 지정가 매수 주문을 준비했습니다. 아직 접수 전입니다." 뒤에 Harness 확인 질문 하나. o19·o20의 2건은 주문이 두 건이라 질문도 두 개인 정상 경우다.
- 주문 준비 → "응" 확정 → 접수, 여러 건·일부 확정 모두 PASS. 확인 대기 알림만으로 확정이 맞게 됐다.
- **질문: "오늘 삼성전자 뉴스 확인해보고 괜찮으면 사줘"(o21-1)** → 뉴스를 읽고 뉴스만으로 판단할 수 없다며 확정하지 않았다(이전과 같은 판단).

## read (Memory 알림·링크 후속 질문)

```text
===== summary (id | expect | model steps | check | time | max input tokens | calls model/search/page/notes)
2 | news_search | news_search > web_extract+web_extract > answer | PASS | 41.7s | 15798 | 3/1/3/3
6-1 | answer | answer | PASS | 2.6s | 15933 | 1/0/0/0
4 | answer | memory > answer | PASS | 5.2s | 16574 | 2/0/0/0
4-1 | answer | answer | PASS | 1.5s | 16855 | 1/0/0/0
10 | answer | memory > answer | PASS | 4.3s | 17451 | 2/0/0/0
15 | web_search | news_search+news_search+news_search > web_extract+web_extract+web_extract > news_search > web_extract > answer | CHECK | 40.3s | 41661 | 5/4/6/6
15-1 | web_extract | web_extract > answer | PASS | 20.5s | 49980 | 2/0/5/5
15-2 | web_extract | answer | CHECK | 6.7s | 52856 | 1/0/0/0
```

- **질문: "나는 보수적인 투자 성향이라고 기억해줘"(4)** → Memory "투자 성향: 보수적." 저장, 사용자에게 변경 안내 줄이 붙었다. **질문: "보수적 투자 성향이라고 한 거 잊어줘"(10)** → Memory 비움, 안내 줄. 안내는 저장되지 않아도 기억 동작은 그대로다.
- **질문: "아까 그 목표주가 기사 링크 다시 줘"(6-1)** → 앞 Turn의 기사를 번호 링크로 다시 줬다.
- **질문: "그중 두 번째 기사만 자세히 알려줘"(15-2)** → 도구 없이 저장된 페이지 요약으로 답했다(기대값이 `web_extract`라 CHECK). 15의 CHECK는 뉴스성 질문에 `news_search`를 쓴 것이다.

결론: 확인 질문 중복이 사라졌고 주문·Memory·링크 후속 질문에 회귀가 없다. 0.6.0 릴리스 조건(시나리오 통과)을 채웠다.
