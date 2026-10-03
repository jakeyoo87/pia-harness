# Broker 2단계 후속: 거래일 표기·종목 목록·영문 종목코드·Compaction 크기

날짜: 2026-10-03 (Asia/Seoul)
브랜치: pia-harness `claude/broker-followups`(계획서와 harness 구현), pia-broker `claude/broker-followups`(Broker 구현)
담당: Claude 계획·구현(harness, Broker), Codex 계획·구현 검토, 릴리스·PIA 연동·배포는 Codex

## 배경

2026-10-03 사용자가 Telegram에서 broker 2단계를 확인했다(Bot `d21f9a75e`, harness 0.7.1, Broker `8e62cf0`). 순위 11개와 투자자 동향의 값·단위는 모두 맞았다. 아래 문제가 나왔고, 사용자가 한 번에 고치기로 했다. Compaction 크기를 줄이는 것도 함께 한다(사용자 요청: 기준 128K, Summary 1만 자, 최근 Turns 2만 자).

1. **휴장일·장 마감 뒤 "오늘" 표현.** 10월 3일(개천절, 휴장) 저녁에 물었는데 "오늘(10월 3일) 상승률 상위", "오늘 기관은 순매수"라고 답했다. 실제로는 10월 2일 장 결과다.
   - 원인: 결과 문장에 조회 시각만 있고 그 값의 거래일이 없다. 시장 투자자 동향은 결과 문장이 "Net buying by investor group today"로 시작한다. 외국인·기관 순위는 "a provisional intraday tally while the market is open"이라고 해서, 휴장일에도 "장중 잠정 집계"라고 답했다.
   - 거래일이 결과에 있는 경우(공매도 기준일, 종목 투자자 동향의 날짜별 행)는 모델이 기준일을 바르게 말했다.
2. **종목 목록이 오래됨.** HTS 조회 상위 9위가 "종목코드 468670"으로 나왔다. 468670(브릴스)은 목록(2026-09-26 수동 생성) 뒤에 상장한 코스닥 종목이다. 목록 갱신은 수동이다.
3. **영문이 섞인 종목코드를 지원하지 않음(주문 1단계부터 있던 문제).** 최근 상장 종목은 `0016X0`, `0001A0`처럼 숫자·영문 6자리 코드를 쓴다. 종목 목록(`instruments.py` `_CODE`)과 `InstrumentId`(`models.py` `_SYMBOL`)가 6자리 숫자만 받아서 이 종목들을 모두 뺀다. 2026-10-03 KIS 공개 종목 파일 기준 **400개**(코스피 341개, 대부분 신규 ETF / 코스닥 59개, 예: 덕양에너젠)가 빠져 있다. 이 종목은 시세·매수 가능·주문·투자자 동향을 할 수 없다.
4. **Compaction 크기.** 지금 `CompactionPolicy` 기본값은 기준 256,000토큰(`/status`의 `Context: …/256K`), Summary 20,000자, 최근 Turns 40,000자다.

함께 확인한 것(고치지 않음):
- 배당 질문은 웹 검색으로 답했다(정상). 그 Turn에서 모델이 배당주로 아는 종목 5개의 시세를 미리 불렀다가 쓰지 않았다. 앞선 대화의 영향으로 보이고, 프롬프트로 막으면 필요한 조회까지 줄일 수 있어 두기로 했다.
- 외국인·기관 순위의 가집계(삼성전자 외국인 −1,419억)는 종목 투자자 동향의 확정치(−962억)와 차이가 컸다. 모델은 "최종 집계와 다를 수 있다"고 덧붙였다.

## 결정 (사용자, 2026-10-03)

- 네 가지를 한 계획으로 묶는다.
- 종목 목록은 **Broker가 KIS 공개 종목 파일을 하루 한 번 직접 받는다.** 받기에 실패하면 패키지 목록을 쓰고 경고 로그를 남긴다. 다른 방법(매일 자동 커밋, 예약 작업 + S3)은 배포나 인프라가 늘어 택하지 않았다.
- 종목 파일 출처는 지금처럼 KIS 공개 파일(`new.real.download.dws.co.kr`, KIS 공식 예제 `stocks_info/kis_kospi_code_mst.py`와 같은 경로)이다. KRX Open API·공공데이터포털은 키가 필요하고, KIS 코드 체계와 어긋날 수 있어 쓰지 않는다.
- Compaction은 기준 128,000토큰, Summary 최대 10,000자, 최근 Turns 최대 20,000자로 줄인다(2026-09-30에 정한 2만·4만 자에서 다시 줄임). 압축 직후 크기를 줄여 압축 간격을 넓히고 매 호출을 가볍게 하려는 것이다.

## 설계

### 1. 결과 문장의 시각 표기 (harness `broker_read.py`)

모든 시세성 결과(`quote`, `ranking`, `investors`)의 시각 표기를 같은 뜻으로 바꾼다.

- "at 2026-10-03 19:13 KST" → "looked up at 2026-10-03 19:13 KST"
- 그리고 한 줄을 붙인다: "Outside regular trading hours (09:00–15:30 KST on trading days) these are the last trading session's figures; say which session, not 'today'."
  - harness는 휴장일 달력을 모른다. 그래서 날짜를 계산하지 않고, 규칙만 알려 모델이 날짜를 판단하게 한다. 모델은 메시지의 수신 시각(`[Received …]`)과 결과의 조회 시각을 이미 본다.
  - 결과에 거래일이 있는 경우(공매도 `basis_dates`, 종목 투자자 동향의 날짜)는 그 날짜를 그대로 쓴다.
- 시장 투자자 동향: "Net buying by investor group today" → "Net buying by investor group for the current or last trading session".
- 외국인·기관 순위: "a provisional intraday tally while the market is open" → "KIS's provisional tally (updated during the session; after the close it stays the last session's provisional figure and can differ from the final per-stock figures that action investors gives)".
- `account`, `buyable`은 바꾸지 않는다. 잔고·주문 가능 금액은 조회 시점 값이 맞다.

거래일을 정확히 적으려면 KIS에서 날짜를 따로 받아야 한다(예: 일별 시세). 호출이 하나 늘고 Broker 응답도 바뀌므로 하지 않는다. 문장으로 충분한지는 배포 뒤 휴장일·장 마감 뒤 질문으로 다시 본다.

### 2. 종목 목록 자동 갱신 (Broker)

- `instruments.py`에 KIS 공개 종목 파일을 받아 읽는 함수를 둔다. 수동 갱신 스크립트(`scripts/update_instruments.py`)도 이 함수를 쓴다(지금 스크립트의 `download`를 옮김).
  - 고정 주소 두 개(코스피·코스닥), https, redirect 없음, 시간 제한(5초), 압축 파일 하나·크기 상한(예: 10MB).
- `InstrumentCatalog`에 갱신을 넣는다.
  - Lambda가 처음 목록을 쓸 때와 마지막으로 받은 지 24시간이 지났을 때 새로 받는다. 받은 목록은 Lambda 메모리에 둔다.
  - 받기나 해석에 실패하면 지금 쓰던 목록(처음에는 패키지 목록)을 그대로 쓰고 경고 로그(`instruments.refresh_failed`, 오류 종류만)를 남긴다. 다음 갱신은 1시간 뒤에 다시 시도해 실패 때마다 다운로드하지 않게 한다.
  - 받은 목록이 비정상적으로 작으면(예: 패키지 목록의 절반 미만) 실패로 본다. 파일 형식이 바뀌어 대부분을 못 읽는 경우를 막기 위해서다.
- 패키지 목록(`data/instruments.json`)은 받기가 실패할 때를 위해 남긴다. 이번에 수동 스크립트로 한 번 갱신해 같이 커밋한다.
- 보안: 공개 파일이고 자격 증명이 오가지 않는다. Broker가 부르는 외부 주소가 KIS 9443 하나에서 하나 더 는다(README에 적는다).

### 3. 영문 섞인 종목코드 (Broker)

- 종목코드 규칙을 `^[0-9A-Z]{6}$`로 넓힌다: `instruments.py` `_CODE`, `models.py` `_SYMBOL`(`InstrumentId`). KIS 파일의 다른 길이 코드(예: 코스피 파일의 9자리 `F70100030` 등)는 계속 뺀다.
- 종목 찾기는 코드 입력을 대문자로 맞춰 비교한다(`_normalize`가 소문자로 바꾸기 때문). 이름 검색은 그대로다.
- 이 코드를 쓰는 곳(시세, 매수 가능, 주문, 투자자 동향, 순위 행)은 `InstrumentId`를 거치므로 규칙 한 곳만 바꾸면 된다. 테스트 도우미 `testing.py`의 6자리 숫자 검사도 맞춘다.
- harness는 바꿀 것이 없다(Broker가 준 코드를 그대로 쓴다). 도구 설명의 "6-digit code"는 "6-character code"로 고친다.
- 배포 뒤 실호출로 확인: `quote`에 `0016X0`(SOL 중단기회사채액티브)와 덕양에너젠. KIS가 이 코드를 받는지 아직 확인하지 않았다(순위 응답에는 이 코드가 나왔다). 주문은 실호출로 확인하지 않는다.

### 4. Compaction 크기 (harness)

- `compaction.py` `CompactionPolicy` 기본값: `trigger_tokens` 256,000 → 128,000, `summary_chars` 20,000 → 10,000, `tail_chars` 40,000 → 20,000. Summary 출력 상한(글자 한도의 두 배)은 그 규칙대로 따라 줄어든다. 테스트와 README 4절(141·142·161·163행)의 숫자를 고친다.
- 영향: 압축 직후 Context(시스템 지침·도구 약 6천 자 + Memory 2천 자 + Summary 1만 자 + 최근 Turns 2만 자)는 약 1만 7천~3만 4천 토큰(한국어 답변과 영어 도구 결과가 섞여 글자당 약 0.5~1토큰으로 추정), 즉 128K의 약 13~27%다. 웹 조사 Turn 하나가 도구 기록 포함 1만~2만 자쯤이라, 가장 최근 Turn 앞으로는 1~2개만 원문으로 남고 그 앞은 Summary에 의존한다. Summary 호출이 짧아져 40초 제한 초과 위험은 줄어든다.
- PIA는 기본 `CompactionPolicy()`를 쓰므로 코드는 그대로이고, README(46·93행)와 rollout 문서(106행)의 256K·4만 자·2만 자 문장만 Codex가 고친다. `/status`는 정책 값을 읽어 128K로 보인다.

## 구현 위치

**pia-harness**: `broker_read.py`(1, 3의 설명 한 줄), `compaction.py`(4), 테스트, README 4절·6.2.3.

**pia-broker**: `instruments.py`(2·3), `models.py`(3), `testing.py`(3), `credential_api.py`(목록 생성 방식), `scripts/update_instruments.py`(받기 함수 공유), `data/instruments.json`(1회 갱신), 테스트, README(종목 목록 절, 외부 주소).

**PIA(Codex)**: harness 버전 고정, README·rollout의 Compaction 숫자.

## 개인정보·보안

- 종목 파일은 공개 데이터다. 회원 데이터·자격 증명은 오가지 않는다.
- 다운로드 주소는 고정하고 redirect를 따르지 않는다. 응답 크기에 상한을 둔다.

## 검증

1. 오프라인: 결과 문장, 종목 파일 받기 성공·실패·작은 목록·24시간·1시간 재시도, 영문 코드 검색·`InstrumentId`, Compaction 기준.
2. 배포 뒤 실호출(사용자 승인): `quote` `0016X0`·덕양에너젠, `ranking` `most_viewed`(신규 종목 이름), 로그에 `instruments.refresh_failed`가 없는지(Codex).
3. 배포 뒤 Telegram(사용자): 휴장일·장 마감 뒤 "오늘 상승률 상위", "오늘 기관은 샀어?"에서 거래일을 바르게 말하는지.

## 순서

1. 계획 → Codex 계획 검토 → 반영
2. Broker·harness 구현(Claude) → Codex 구현 검토 → Broker 병합·dev 배포(Codex, 승인)
3. 실호출 확인(Claude, 승인)
4. harness 릴리스(버전은 사용자가 정함), PIA 연동·Bot 배포(Codex, 승인) → Telegram 확인(사용자)

## 열린 질문

1. 결과 문장의 장 시간 규칙(09:00–15:30)을 harness 문장에 고정해도 되는지. NXT(08:00–20:00)는 지금 범위 밖이라 쓰지 않는다.
2. 종목 목록 갱신 주기(24시간)와 실패 재시도(1시간), "절반 미만이면 실패" 기준이 적절한지, 더 줄일 것이 있는지.
3. 패키지 목록을 계속 둘지. 지금 계획은 받기 실패 때를 위해 둔다.
