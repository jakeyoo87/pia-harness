# 지침·도구 설명 자리 정리 시나리오 테스트 (2026-10-17)

계획 `plans/2026-10-17_prompt-placement.md`. 바꾼 지침(PIA 문장 사본)으로 broker·execution·onboarding 세트와 read의 Memory 시나리오를 돌렸다.

| 항목 | 값 |
|---|---|
| Harness | `claude/prompt-placement` `8565bfb` |
| PIA 지침 | onboarding은 pia-agent `claude/prompt-placement` `526281be0`의 ONBOARDING_PROMPT(`--extra-prompt-file`), 주문·broker 문장은 `smoke_flow.py` 사본 |
| 모델 | OpenRouter `openai/gpt-6-luna` |
| Broker | 가짜(`smoke_flow.py`), 실제 주문·계좌 조회 없음 |
| 키 | Secrets Manager에서 프로세스 안으로만, 로그에 키 값 없음 |

## 1차 결과

| 세트 | 결과 |
|---|---|
| broker 37개 | 기대 26개 PASS. 다른 세션 검토가 짚은 지수·환율·금리(b17~b24)는 모두 broker. 버튼 0개 |
| execution 31개 | u2~u2-2 회귀(아래). o21-1 CHECK는 `news_search`로 간 것으로 이전 기록(2026-09-30 release-check)과 같다 |
| onboarding 17개 | 모두 PASS. 버튼 규칙이 choices 설명으로 옮겨진 뒤에도 단계마다 버튼이 나왔다 |
| read 4·4-1·10·22·23 | 모두 PASS |

- **회귀, u2 "테슬라 시장가로 1주 사줘":** 모델이 인자 설명대로 지정가로 준비했다. 그런데 준비 뒤 답변 호출에는 도구가 없어서 order 인자 설명이 보이지 않는다. System에서도 "(US: limit orders only)"를 지운 뒤라, 답변이 "도구가 요청과 다르게 지정가로 준비했어요… 승인하지 마세요"라고 하면서 확인 버튼을 함께 보여줬다. 이전(2026-10-06)에는 정상이었다.
  - 대응: PIA ORDER_PROMPT와 사본에 "(US: limit orders only)"를 되살렸다. README §6.0에 "도구 없이 부르는 마지막 답변 호출에 필요한 사양은 System에도 둔다"를 적었다.
- **b28 "다우 지수 어때?":** CHECK. broker를 빈 인자로 한 번 부른 뒤 실패 결과를 보고 웹으로 가서 맞게 답했다. 이전(2026-10-05)에는 broker를 부르지 않았다.
- **b36 "미국 시가총액 Top10":** 웹. 2026-10-07에 KIS 거절로 뺀 기능이라 맞는 동작이다.

```text
===== summary (id | expect | model steps | check | time | max input tokens (estimate) | reported tokens of final answers | calls model/search/page/notes)
b1 | broker | broker > answer | PASS | 3.4s | 15590 | 3562 | 2/0/0/0
b2 | broker | broker > answer | PASS | 4.5s | 17236 | 4216 | 2/0/0/0
b3 | broker | broker > answer | PASS | 3.0s | 19302 | 4606 | 2/0/0/0
b4 | broker | broker > answer | PASS | 4.1s | 20462 | 4935 | 2/0/0/0
b5 | broker | broker > answer | PASS | 3.3s | 21715 | 5200 | 2/0/0/0
b6 | broker | broker > answer | PASS | 2.9s | 22819 | 5446 | 2/0/0/0
b7 | broker | broker > answer | PASS | 5.0s | 24394 | 6090 | 2/0/0/0
b8 | broker | broker > answer | PASS | 5.8s | 26167 | 6534 | 2/0/0/0
b9 | broker | broker > answer | PASS | 4.6s | 28073 | 7064 | 2/0/0/0
b10 | broker | broker > answer | PASS | 5.5s | 30039 | 7613 | 2/0/0/0
b10-1 | - | broker > answer | OBSERVE | 8.8s | 30991 | 7918 | 2/0/0/0
b11 | broker | broker > answer | PASS | 5.1s | 32777 | 8401 | 2/0/0/0
b12 | broker | broker > answer | PASS | 7.4s | 36048 | 9454 | 2/0/0/0
b13 | broker | broker > answer | PASS | 5.7s | 39123 | 10314 | 2/0/0/0
b14 | broker | broker > answer | PASS | 4.8s | 41788 | 11039 | 2/0/0/0
b15 | - | broker > web_search > web_extract > web_search > web_search > web_extract > answer | OBSERVE | 55.9s | 59110 | 16417 | 7/3/7/6
b17 | broker | broker > answer | PASS | 3.4s | 56036 | 14907 | 2/0/0/0
b18 | broker | broker > answer | PASS | 5.3s | 57464 | 15472 | 2/0/0/0
b19 | broker | broker > answer | PASS | 3.4s | 58995 | 15732 | 2/0/0/0
b20 | broker | broker > answer | PASS | 3.6s | 60373 | 16146 | 2/0/0/0
b21 | broker | broker > answer | PASS | 4.1s | 61696 | 16460 | 2/0/0/0
b22 | broker | broker > answer | PASS | 6.1s | 63074 | 16891 | 2/0/0/0
b23 | broker | broker > answer | PASS | 3.5s | 64419 | 17080 | 2/0/0/0
b24 | broker | broker > answer | PASS | 7.3s | 65656 | 17594 | 2/0/0/0
b25 | broker | broker > answer | PASS | 3.3s | 66839 | 17611 | 2/0/0/0
b26 | - | web_search > web_extract > answer | OBSERVE | 19.6s | 72681 | 19223 | 3/1/1/1
b27 | - | web_search > web_extract > answer | OBSERVE | 19.3s | 77317 | 20727 | 3/1/2/2
b28 | web_search | broker > web_search > web_extract > answer | CHECK | 28.8s | 80214 | 21242 | 4/1/2/1
b29 | broker | broker > answer | PASS | 5.5s | 80298 | 21091 | 2/0/0/0
b30 | broker | broker > answer | PASS | 5.3s | 81742 | 21400 | 2/0/0/0
b31 | broker | broker > answer | PASS | 3.9s | 83413 | 21801 | 2/0/0/0
b32 | - | web_search > web_search > web_extract > answer | OBSERVE | 27.5s | 92421 | 24282 | 4/2/2/2
b33 | - | web_search > answer | OBSERVE | 6.4s | 92992 | 24606 | 2/1/0/0
b34 | - | answer | OBSERVE | 3.2s | 91119 | 23892 | 1/0/0/0
b35 | broker | broker > answer | PASS | 5.1s | 92612 | 24273 | 2/0/0/0
b36 | - | web_search > web_search > web_extract > answer | OBSERVE | 37.0s | 102274 | 27818 | 4/2/3/2
b37 | broker | broker > answer | PASS | 5.6s | 101737 | 26918 | 2/0/0/0
b38 | - | broker > web_search > web_extract > answer | OBSERVE | 36.5s | 108859 | 29168 | 4/1/2/2

===== summary (id | expect | model steps | check | time | max input tokens (estimate) | reported tokens of final answers | calls model/search/page/notes)
o0 | answer | answer | PASS | 3.4s | 8267 | 1807 | 1/0/0/0
o1 | order | order > answer | PASS | 4.2s | 8630 | 815 | 2/0/0/0
o1-1 | confirm | confirm > answer | PASS | 2.5s | 10340 | 994 | 2/0/0/0
o3 | order | order > answer | PASS | 3.4s | 10258 | 1112 | 2/0/0/0
o3-1 | order | order > answer | PASS | 8.4s | 11980 | 1802 | 2/0/0/0
o3-2 | answer | answer | PASS | 2.3s | 12926 | 2658 | 1/0/0/0
o6 | order | order > answer | PASS | 4.9s | 12257 | 1462 | 2/0/0/0
o6-1 | order | order > answer | PASS | 3.6s | 12871 | 1635 | 2/0/0/0
o6-2 | - | news_search+web_search > web_extract+web_extract > answer | OBSERVE | 26.0s | 29599 | 7492 | 3/2/4/4
o9 | order | order > answer | PASS | 4.6s | 26766 | 5207 | 2/0/0/0
o9-1 | order | order > answer | PASS | 6.2s | 28488 | 5607 | 2/0/0/0
o9-2 | order | order > answer | PASS | 5.7s | 29437 | 5813 | 2/0/0/0
o9-3 | confirm | confirm > answer | PASS | 3.1s | 30348 | 5761 | 2/0/0/0
o13 | order | order > answer | PASS | 4.0s | 30330 | 5881 | 2/0/0/0
o13-1 | answer | answer | PASS | 2.3s | 32042 | 7218 | 1/0/0/0
o13-2 | answer | answer | PASS | 5.8s | 31336 | 7479 | 1/0/0/0
o16 | order | order > answer | PASS | 5.0s | 31663 | 6201 | 2/0/0/0
o16-1 | order | order > answer | PASS | 5.5s | 33392 | 6510 | 2/0/0/0
o16-2 | confirm | confirm > answer | PASS | 2.6s | 34324 | 6585 | 2/0/0/0
o19 | order | order+order > answer | PASS | 5.2s | 34257 | 6898 | 2/0/0/0
o19-1 | confirm | confirm > answer | PASS | 2.9s | 36709 | 7183 | 2/0/0/0
o20 | order | order+order > answer | PASS | 4.0s | 36840 | 7443 | 2/0/0/0
o20-1 | confirm | confirm > answer | PASS | 3.4s | 39327 | 7701 | 2/0/0/0
o21 | order | order > answer | PASS | 4.6s | 39231 | 7791 | 2/0/0/0
o21-1 | web_search | news_search > web_extract > answer | CHECK | 24.5s | 49870 | 12173 | 3/1/3/3
u1 | order | order > answer | PASS | 3.6s | 48859 | 10337 | 2/0/0/0
u1-1 | confirm | confirm > answer | PASS | 3.4s | 50577 | 10562 | 2/0/0/0
u2 | order | order > answer | PASS | 6.1s | 50503 | 10831 | 2/0/0/0
u2-1 | order | confirm > answer | CHECK | 3.5s | 52372 | 10940 | 2/0/0/0
u2-2 | confirm | answer | CHECK | 2.7s | 52264 | 12081 | 1/0/0/0
u3 | order | order > answer | PASS | 4.0s | 52494 | 11111 | 2/0/0/0

===== summary (id | expect | model steps | check | time | max input tokens (estimate) | reported tokens of final answers | calls model/search/page/notes)
o1 | - | memory > broker > choices > answer | PASS | 11.8s | 24819 | 5728 | 4/0/0/0
o2 | - | broker+broker+news_search > web_extract > answer | PASS | 17.1s | 35696 | 8789 | 3/1/3/3
o3 | - | choices > answer | PASS | 3.8s | 35788 | 8487 | 2/0/0/0
o4 | - | choices > answer | PASS | 5.6s | 36997 | 8729 | 2/0/0/0
o5 | - | choices > answer | PASS | 4.3s | 37920 | 8810 | 2/0/0/0
o6 | - | choices > answer | PASS | 4.0s | 38693 | 8962 | 2/0/0/0
o7 | - | choices > answer | PASS | 3.1s | 39370 | 9095 | 2/0/0/0
o8 | - | choices > answer | PASS | 2.8s | 40014 | 9214 | 2/0/0/0
o9 | - | choices > answer | PASS | 3.5s | 40638 | 9343 | 2/0/0/0
o10 | - | choices > answer | PASS | 5.2s | 41436 | 9541 | 2/0/0/0
o11 | - | choices > answer | PASS | 2.8s | 42362 | 9694 | 2/0/0/0
o12 | - | choices > answer | PASS | 14.3s | 43035 | 10427 | 2/0/0/0
o13 | - | answer | PASS | 2.5s | 44154 | 10206 | 1/0/0/0
o14 | plan | plan > answer | PASS | 6.0s | 44355 | 7684 | 2/0/0/0
o15 | confirm | confirm > answer | PASS | 3.1s | 46798 | 7844 | 2/0/0/0
o16 | broker | broker > choices > answer | PASS | 7.5s | 48974 | 11267 | 3/0/0/0
o17 | broker | broker > answer | PASS | 4.8s | 50637 | 11842 | 2/0/0/0

===== summary (id | expect | model steps | check | time | max input tokens (estimate) | reported tokens of final answers | calls model/search/page/notes)
4 | answer | memory > answer | PASS | 3.9s | 6842 | 1328 | 2/0/0/0
4-1 | answer | answer | PASS | 1.3s | 7215 | 1396 | 1/0/0/0
10 | answer | memory > answer | PASS | 4.8s | 7754 | 1496 | 2/0/0/0
22 | answer | memory > answer | PASS | 7.2s | 8190 | 1568 | 2/0/0/0
23 | news_search | news_search+memory > web_extract > answer | PASS | 16.9s | 16712 | 4266 | 3/1/3/3
```

## 재실행 (되살린 뒤)

- **u2~u2-2 두 번:** 두 번 모두 준비 없이 "미국 주식은 지정가 주문만 가능합니다. 지정가를 얼마로 할까요?(USD)"라고 묻고, 가격을 받을 때까지 다시 물었다. 거짓 안내와 잘못된 확인 버튼이 없다. CHECK는 시나리오가 주문 준비를 기대하기 때문이다(이전에는 현재가로 지정가를 제안했다).
- **b28 두 번:** 두 번 모두 1차와 같다(빈 인자 broker → 웹 → 맞는 답). 새 broker 한 줄("broker가 주는 시장 수치는 broker로")을 보고 broker에 다우가 있는지 먼저 확인해 보는 것으로 보인다. 호출 한 번(약 2초)이 늘 뿐 답은 맞다.

```text
===== summary (id | expect | model steps | check | time | max input tokens (estimate) | reported tokens of final answers | calls model/search/page/notes)
u2 | order | answer | CHECK | 2.6s | 8322 | 1776 | 1/0/0/0
u2-1 | order | answer | CHECK | 2.3s | 8664 | 1779 | 1/0/0/0
u2-2 | confirm | answer | CHECK | 2.3s | 8804 | 1806 | 1/0/0/0

===== summary (id | expect | model steps | check | time | max input tokens (estimate) | reported tokens of final answers | calls model/search/page/notes)
u2 | order | answer | CHECK | 3.1s | 8322 | 1819 | 1/0/0/0
u2-1 | order | answer | CHECK | 2.5s | 8651 | 1802 | 1/0/0/0
u2-2 | confirm | answer | CHECK | 2.6s | 8832 | 1839 | 1/0/0/0

===== summary (id | expect | model steps | check | time | max input tokens (estimate) | reported tokens of final answers | calls model/search/page/notes)
b28 | web_search | broker > web_search > web_extract > answer | CHECK | 14.3s | 18806 | 4754 | 4/1/2/0

===== summary (id | expect | model steps | check | time | max input tokens (estimate) | reported tokens of final answers | calls model/search/page/notes)
b28 | web_search | broker > web_search > web_extract > answer | CHECK | 12.5s | 18926 | 4867 | 4/1/2/0
```

## 미국 시장가 요청 → 현재가 지정가 (사용자 결정, 2026-10-17)

한국투자증권 Open API에는 미국 시장가 주문이 없다. 그래서 미국 주식에 시장가를 요청하면 되묻지 않고 현재가 지정가로 준비한다. 확인 문장 앞에 "미국 주식은 시장가 주문이 안 돼서 현재가 지정가로 준비했어요."를 붙인다. u2 시나리오는 "응"으로 바로 확인하게 바꾸고 execution 세트 전체를 다시 돌렸다.

- 30개 모두 PASS(o6-2 관찰). o21-1도 이번에는 `web_search`를 같이 불러 PASS.
- u2 "테슬라 시장가로 1주 사줘": `order` MARKET → 251.30달러 지정가로 준비, 답변이 "미국 주식은 시장가 대신 현재가 지정가로 주문하며, 아직 접수되지 않았습니다"라고 맞게 안내. u2-1 "응" → LIMIT 251.3으로 접수.
- 모델 답변과 확인 문장이 같은 안내를 한 번씩 해서 두 번 나온다. 국내 주문도 답변과 확인 문장이 겹치는 모양이라 그대로 둔다.

```text
===== summary (id | expect | model steps | check | time | max input tokens (estimate) | reported tokens of final answers | calls model/search/page/notes)
o0 | answer | answer | PASS | 2.9s | 8368 | 1731 | 1/0/0/0
o1 | order | order > answer | PASS | 4.5s | 8678 | 841 | 2/0/0/0
o1-1 | confirm | confirm > answer | PASS | 2.9s | 10443 | 1016 | 2/0/0/0
o3 | order | order > answer | PASS | 3.0s | 10368 | 1147 | 2/0/0/0
o3-1 | order | order > answer | PASS | 4.1s | 12145 | 1396 | 2/0/0/0
o3-2 | answer | answer | PASS | 2.3s | 13117 | 2700 | 1/0/0/0
o6 | order | order > answer | PASS | 4.0s | 12427 | 1493 | 2/0/0/0
o6-1 | order | order > answer | PASS | 3.5s | 13044 | 1681 | 2/0/0/0
o6-2 | - | news_search+web_search > web_extract+web_extract > answer | OBSERVE | 22.9s | 29439 | 7229 | 3/2/5/5
o9 | order | order > answer | PASS | 4.3s | 26899 | 5242 | 2/0/0/0
o9-1 | order | order > answer | PASS | 6.3s | 28646 | 5679 | 2/0/0/0
o9-2 | order | order > answer | PASS | 7.5s | 29638 | 6130 | 2/0/0/0
o9-3 | confirm | confirm > answer | PASS | 3.1s | 30545 | 5828 | 2/0/0/0
o13 | order | order > answer | PASS | 4.5s | 30527 | 5954 | 2/0/0/0
o13-1 | answer | answer | PASS | 2.9s | 32264 | 7271 | 1/0/0/0
o13-2 | answer | answer | PASS | 5.0s | 31571 | 7408 | 1/0/0/0
o16 | order | order > answer | PASS | 4.1s | 31911 | 6257 | 2/0/0/0
o16-1 | order | order > answer | PASS | 6.7s | 33712 | 6914 | 2/0/0/0
o16-2 | confirm | confirm > answer | PASS | 2.9s | 34694 | 6692 | 2/0/0/0
o19 | order | order+order > answer | PASS | 5.0s | 34634 | 7038 | 2/0/0/0
o19-1 | confirm | confirm > answer | PASS | 3.5s | 37136 | 7307 | 2/0/0/0
o20 | order | order+order > answer | PASS | 6.8s | 37281 | 7603 | 2/0/0/0
o20-1 | confirm | confirm > answer | PASS | 3.9s | 39794 | 7832 | 2/0/0/0
o21 | order | order > answer | PASS | 4.3s | 39700 | 7936 | 2/0/0/0
o21-1 | web_search | news_search+web_search > web_extract+web_extract > answer | PASS | 28.2s | 53613 | 13504 | 3/2/4/2
u1 | order | order > answer | PASS | 4.1s | 49848 | 10671 | 2/0/0/0
u1-1 | confirm | confirm > answer | PASS | 3.5s | 51628 | 10898 | 2/0/0/0
u2 | order | order > answer | PASS | 4.4s | 51561 | 11059 | 2/0/0/0
u2-1 | confirm | confirm > answer | PASS | 3.1s | 53450 | 11283 | 2/0/0/0
u3 | order | order > answer | PASS | 4.2s | 53370 | 11410 | 2/0/0/0
```
