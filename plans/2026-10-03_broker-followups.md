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

## Codex 계획 검토

2026-10-03. 대상 `1008f01`, 기준 Harness main `1d6f65b`(v0.7.1), Broker main `8e62cf0`, PIA main `d21f9a75e`. 세 저장소의 지침·README와 관련 코드를 정적으로 대조했다. 제품 코드·테스트 수정, 병합, AWS 변경, 배포, KIS 실호출은 하지 않았다. 검토 기록만 같은 브랜치에 commit·push한다.

**판정: 구현 전 보완 3건.** 사용자 결정인 공개 master 직접 받기, 영문 6자리 코드, Compaction 128K·1만 자·2만 자는 유지한다. 별도 달력·예약 작업·S3·검증 프레임워크를 추가할 필요는 없다.

### 구현 전 보완

**1. [P2] 거래일이 없는데 특정 거래일을 말하도록 요구하지 않는다** — 계획 36~43행; Harness `src/pia_harness/broker_read.py:277,326,355`.

`looked up at`은 조회 시각과 데이터 시각을 구분하는 올바른 수정이다. 하지만 `say which session`과 "규칙만 알려 모델이 날짜를 판단"은 거래일이 없는 응답에서 날짜를 추측하게 만든다. `Received`와 `observed_at`은 휴장일 달력도 통계 기준일도 아니므로, 휴장일·연휴·장 시작 전 요청에서 정확한 마지막 거래일을 보장하지 않는다. 반대로 정상 거래일 16시의 마지막 세션은 그날 세션이므로 장외라는 이유만으로 모든 "오늘" 표현이 틀리는 것도 아니다.

- 응답에 기준일이 있으면 그대로 쓴다. 없으면 **최근 제공된 거래 세션 값이며 정확한 기준 거래일은 응답에 없다고 밝히고, 날짜를 계산하거나 지어내지 않는다**고 계약을 바꾼다. "10월 2일"을 반드시 말하게 하지 않아도 이번 문제인 "휴장일의 오늘 값" 단정은 막을 수 있다.
- 09:00~15:30은 현재 KRX 정규장 범위의 안내로 두어도 되지만, 그 시간과 조회 날짜만으로 개장 여부나 데이터 기준일을 판정하는 장치로 쓰지 않는다. 이번 범위 밖인 NXT 지원이나 휴장일 API를 추가하지 않는다.
- 결과 첫 줄뿐 아니라 도구 설명의 `whole market today`(`broker_read.py:68`)와 README의 "오늘 시장별"도 함께 정리한다. 가집계와 종목별 확정치가 다를 수 있다는 안내는 유지한다.
- 오프라인 검증은 조회 시각을 `looked up at`으로 표시하고, 거래일 없는 결과는 날짜 추론 금지를 포함하며, 기준일 있는 결과는 그 기준일을 보존하는지 확인한다. 실제 모델이 이를 지키는지는 별도 승인된 Telegram 확인에서 검증한다.

**2. [P2] 목록 감소 검증을 두 시장 각각에 적용하고 성공한 묶음만 교체한다** — 계획 48~52행; Broker `src/pia_broker/instruments.py:47~59`.

현재 `parse_master`는 지원 코드가 하나도 없거나 빈 파일이면 예외가 아니라 빈 목록을 반환한다. 패키지 내용을 읽어 세어 보니 KOSPI 1,779건, KOSDAQ 1,766건, 전체 3,545건이다. 따라서 코스닥 파싱이 빈 목록이어도 코스피 1,779건만으로 전체 절반(1,772.5) 이상이 되어, 합계에만 "절반" 검사를 하면 정상 갱신처럼 코스닥 전체가 빠질 수 있다. 영문 코드 추가 뒤에도 합계 검증만으로 한 시장 누락을 막는 계약은 아니다.

- 제안한 감소 기준을 **각 시장의 패키지 건수와 해당 시장의 파싱 건수**에 적용한다. 둘 다 다운로드·파싱·검증에 성공한 뒤 `_entries` 전체를 한 번만 교체한다. 한쪽이 실패하면 기존 전체 목록을 유지한다. 새 목록과 오래된 시장 목록을 섞는 별도 fallback은 필요 없다.
- HTTP 응답 크기와 ZIP 해제 후 master 크기 모두 같은 상한으로 제한한다고 명시한다. 기존 `scripts/update_instruments.py:25~29`는 제한 없이 응답과 압축 해제 결과를 읽는다. 합성 ZIP에서 12,351바이트 압축 파일이 12,582,912바이트로 풀리는 것을 확인했으므로, ZIP 크기만 제한하면 계획의 메모리 크기 경계가 성립하지 않는다. 상한을 넘으면 기존 실패 처리로 목록 유지·1시간 뒤 재시도를 사용한다. 새 오류 상태는 필요 없다.
- 검증에는 "한 시장만 빈/과소 목록인데 합계는 절반 이상"과 "두 번째 시장 실패 시 첫 시장까지 교체하지 않음", 압축 전후 크기 상한 초과를 넣는다. 공개 파일을 실제로 받아 수치를 정할 검증은 이번 리뷰에서 수행하지 않았다.

**3. [P2] PIA 변경 목록에 Context 표시 테스트를 포함한다** — 계획 68·76행; PIA `app/conversation.py:53~54,124~126`, `tests/test_conversation.py:124~136`.

운영 코드는 기본 `CompactionPolicy()`를 읽으므로 정책 변경이 `/status` 분모까지 자동 반영되는 설명은 맞다. 하지만 PIA의 `test_the_last_answer_size_is_kept_for_status` 한 테스트에 `256_000` 기대값이 3곳(126·130·136행) 고정돼 있다. 문서와 wheel만 바꾸면 새 기본값 128,000과 달라져 PIA CI가 실패한다.

- PIA 구현 위치에 `tests/test_conversation.py`의 정책 기준 기대값 갱신을 추가한다. 운영 코드에 별도 숫자나 새 설정은 넣지 않는다.
- README의 `/status` 예시 분모뿐 아니라 비율도 새 기준에 맞춘다. 예를 들어 기존 123K/256K(48%)를 123K/128K(96%)로 정리한다. Compaction 본문·rollout 문서의 세 크기도 맞춘다.
- 모델 실제 context_limit(1,050,000), Memory 목표/상한(2,000/4,000), Turn 저장 한도(256KiB), 모델 timeout은 다른 기준이므로 함께 줄이지 않는다. 과거 측정 기록에 적힌 당시 수치는 변경하지 않는다.

### 비차단 의견과 열린 질문

**종목 목록 주기·실패 처리(열린 질문 2·3)**

- 24시간 성공 주기와 실패 후 1시간 재시도, 마지막 정상 목록 유지·처음에는 패키지 목록 사용으로 충분하다. 다음 시도 시각 하나로 처리할 수 있으며 백그라운드 갱신·즉시 추가 재시도·새 저장소는 필요 없다. 패키지 목록은 유지한다.
- 다만 이는 **Lambda 실행 환경마다 첫 사용 때 받기 + 그 환경의 메모리 캐시가 24시간 지난 뒤 받기**이지 서비스 전체의 하루 1회가 아니다. 새 실행 환경은 이전 성공/실패 시각을 모른다. 문서에서 범위를 정확히 밝히되, 이를 해결하려고 사용자가 제외한 S3·예약 작업을 다시 넣지 않는다.
- `credential_api.py:726`의 `InstrumentCatalog.load()`는 Lambda 의존성 초기화에서 호출된다. 여기서는 패키지를 읽고, 실제 `search`/`name_of`에서만 갱신하도록 계획의 "처음 목록을 쓸 때"를 지킨다. status·연결 관리·인증 거부까지 외부 다운로드를 기다리게 만들지 않는다. 과도한 설정은 필요 없고, 두 파일의 대기 시간이 요청 경로에 더해진다는 점만 포함한다.

**공개 다운로드 보안(열린 질문 2)**

- 별도 공개 master downloader에 고정 HTTPS 두 주소·redirect 금지·유효한 TLS 검증·시간/압축 전후 크기 제한을 둔다. 기존 Credential용 KIS 9443 transport의 host allowlist를 넓히지 않고 Credential·Token·공용 주문 client를 전달하지 않는다. ZIP은 메모리에서 읽으면 되며 파일 시스템에 풀 이유가 없다.
- KIS 공식 [KOSPI master 예제](https://github.com/koreainvestment/open-trading-api/blob/main/stocks_info/kis_kospi_code_mst.py)와 [KOSDAQ 예제](https://github.com/koreainvestment/open-trading-api/blob/main/stocks_info/kis_kosdaq_code_mst.py)의 URL은 계획과 일치한다. **두 공식 예제의 다운로드 함수는 TLS 검증을 끄고 extractall을 사용하므로 그대로 복사하지 않는다.** 현재 Broker 수동 함수는 기본 HTTPS 검증과 메모리 ZIP 읽기를 쓰지만 redirect·크기 제한이 없어서 이동만 해서는 새 계약을 충족하지 않는다.

**영문 종목코드(질문 3)**

- `InstrumentId`의 `_SYMBOL`, master/Instrument의 `_CODE`, `Catalog.search`의 대문자 비교, `testing.py:137`을 맞추는 범위는 적절하다. `orders.py:83`의 `OrderRequest`와 quote·buyable·stock_investors 경로는 실제로 `InstrumentId`를 사용하므로 API별 숫자 검증기를 추가하지 않는다. 이름 정규화의 casefold는 유지하고 코드 분기만 uppercase로 비교한다.
- 단, 계획 60행의 "순위 행도 InstrumentId를 거친다"는 표현은 정정한다. `RankingRow`(`models.py:313~315`)는 비어 있지 않은 문자열만 확인하며, KIS 순위에서 받은 영문 코드도 이미 전달할 수 있다. 이 경로에 새 정규식 검사를 넣을 필요는 없다.
- Harness의 설명 변경은 조회 도구(`broker_read.py:74`)와 주문 도구(`broker_order.py:38`) **둘 다** 포함한다. 현재 `broker_instruments.py`와 주문 준비는 Broker가 반환한 문자열을 그대로 전달한다. 코드 접두어를 int로 바꾸거나 선행 0을 없애지 않는다.
- 오프라인에서는 영문 master 행·소문자 코드 검색·기존 숫자/이름 검색·조회/주문 HTTP body의 영문 코드 보존을 확인하면 충분하다. `kis.py:554`의 6자리 숫자 검사는 주문시각 HHMMSS이며 계좌번호·수량의 isdigit/isdecimal 검사도 종목코드와 다르므로 일괄 치환하지 않는다. 실제 주문 검증은 하지 않는다.

**Compaction 영향(질문 4)**

- 요청한 기본값 세 개 변경만으로 충분하다. 출력 상한은 기존 두 배 규칙에 따라 20,000토큰이 되고, Summary는 기존 Summary+요청/답변으로 교체된다. 저장소 스키마·자동 Memory 검토·호출 전 압축을 새로 도입할 이유는 없다.
- 계획 27행의 "압축 간격을 넓힌다"는 기대는 정정한다. trigger와 보존 크기를 함께 절반으로 줄이면 매 호출은 가벼워지지만 다음 압축까지의 누적 여유도 대체로 줄어, 동일한 대화에서는 압축이 더 자주 일어날 수 있다. 시간·비용·품질 개선 폭은 아직 측정하지 않았다.
- `tail_chars=20,000`은 **가장 최신 Turn 앞에 추가로 남기는** 예산이다(`compaction.py:203~217`). 최신 Turn은 크기와 무관하게 별도로 남는다. 따라서 압축 직후 1.7만~3.4만 토큰이나 원문 Turn 개수를 보장하지 않는다. 최근 조사 원문이 줄어 Summary에 없는 도구 세부는 다시 조회해야 할 수 있다는 영향은 계획대로 수용하되, 기존 최신 Turn 보존 테스트를 유지한다.

### 검증 범위

계획·현재 코드·테스트·공식 공개 예제의 정적 검토를 수행했다. 로컬/EC2에서 패키지의 시장별 건수를 읽고 합성 ZIP을 메모리에서 만들어 크기 경계 사례만 확인했다. 공개 master 다운로드·KIS 호출·실제 모델·사용자 데이터·AWS 조회는 하지 않았다. 계획 단계이므로 제품 전체 테스트·빌드는 실행하지 않았다. 기록에 `git diff --check`를 수행하고 변경 파일이 이 계획서 하나인지 확인한다. 배포·실호출·릴리스·PIA 연동 승인은 이 검토 결과로 대체하지 않는다.
