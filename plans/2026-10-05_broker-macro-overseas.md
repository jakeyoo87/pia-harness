# broker 도구: 해외 지수·지수선물·거시 지표·상품 가격

날짜: 2026-10-05 (Asia/Seoul)
브랜치: pia-harness `claude/broker-macro-overseas`(계획, harness 구현), pia-broker 같은 이름(Broker 구현)
담당: Claude 계획·구현·실호출 확인, Codex 계획·구현 검토와 병합·릴리스·배포
기준: harness main `45a8d56`, Broker main `7c5ea5c`, broker 도구 구조는 `plans/2026-10-04_broker-tool-structure.md`

## 목표

사용자가 많이 물을 시장 수치를 증권사 값으로 답한다. 지금은 웹 검색으로 답한다.

- "미국 10년물 금리 얼마야?", "원달러 환율 3개월 추이 차트로", "금값", "유가"
- "나스닥 요즘 어때?", "밤사이 나스닥 선물", "필라델피아 반도체 지수", "VIX"

다음 릴리스(0.7.4)는 이것과 이미 main에 병합한 두 수정(시장 금액 단위 항목별 고정, 매수 가능 현재가 지정가)을 함께 담는다(사용자, 2026-10-05).

## 결정 (사용자, 2026-10-05)

- 새 액션을 만들지 않는다. `quote`(지금)와 `history`(기간별)의 **조회 대상**을 넓힌다.
- 대상 인자는 네 개이고, 각각 뜻이 하나다.

| 인자 | 뜻 | 이번 범위 |
|---|---|---|
| `name` | 질문 대상인 종목 하나 | 국내 종목·ETF (그대로) |
| `market` | 지수로 나타내는 시장(시장 전체 또는 업종) | 코스피·코스닥 + **해외 대표 지수, 업종 지수(SOX), CME 지수선물** |
| `macro` (새) | 거시 지표 | **미국·일본·한국 금리, 환율, VIX** |
| `commodity` (새) | 상품 가격 | **금·은·유가·구리** |

- 구분 규칙: 지수로 나타내는 시장은 `market`, 지수가 아닌 경제 값은 `macro`, 상품 하나의 가격은 `commodity`. VIX는 지수지만 시장이 아니라 변동성 지표라 `macro`. SOX는 업종 지수라 `market`.
- 해외 지수를 `market`에 넣어도 `market`의 뜻은 같고 **받는 값 목록만 액션·데이터마다 다르다**(기존 원칙). 해외 시장은 KIS가 지수 값만 주므로 `history`+`investors`와 `ranking`의 `market` 목록에는 들어가지 않는다.
- **해외 종목·해외 계좌·해외 주문은 다음 작업에서 한 번에 한다.** Broker의 종목 식별(6자리), 통화(KRW), 정수 가격, 계좌·주문 모델이 국내 전용이라, 해외 종목을 시세만 먼저 넣으면 계좌·주문 때 다시 넓혀야 한다. 그동안 미국 종목 주가는 웹 검색으로 답한다.
- 이번에 넣지 않는 것(자리는 정해져 있음): 코스피200 선물(`market`, 만기월마다 코드가 바뀌어 근월물 규칙이 따로 필요), 증시 자금 고객예탁금 등(`market`+새 `data`), DRAM·NAND 가격(KIS 마스터에는 DDR3·DDR4 8Gb·NAND 8Gb SLC 같은 예전 규격뿐이라 "요즘 DRAM 가격" 질문에 맞지 않음 → 웹 검색).
- KIS에 없는 것: CPI·고용·GDP 같은 경제 지표 발표값, 한국은행 기준금리·FOMC 결정, 가상자산 → 지금처럼 웹 검색. 도구 설명에 "목록에 없는 경제 지표는 웹 검색"을 적는다.
- 데이터원은 KIS(이미 연동, 회원별 키라 한도가 회원마다 나뉨, 추가 비용 없음). Investing.com은 공식 API가 없어 쓰지 않는다. KIS가 막히면 §8.

## 1. 액션별 대상

| 액션 + data | 받는 대상 | 비고 |
|---|---|---|
| `account` | `name`(선택) | 그대로 |
| `quote` + `prices` | `name`, `market`, `macro`, `commodity` 중 하나 | 지수·지표·상품은 지금 값과 전일 대비 |
| `history` + `prices` | 위 넷 중 하나 | 기간·일주월 단위 규칙은 지금과 같음 |
| `history` + `investors` | `name`, `market`(kospi·kosdaq) | 그대로 |
| `ranking` | `market`(all·kospi·kosdaq) | 그대로 |

- **대상이 여럿 오면:** `name` → `market` → `macro` → `commodity` 순서로 앞의 것을 쓰고 나머지는 무시한다(지금의 "name이 있으면 market 무시"를 네 개로 넓힌 같은 규칙). 하나도 없으면 "무엇을 조회할지"를 묻는 결과 문장.
- 값 검사는 지금 `_resolve` 규칙 그대로: 대상에는 기본값이 없고, 문자열·허용 값을 검사하며, 쓰지 않는 인자는 무시한다.

## 2. 값 목록 (처음 여는 것)

KIS 해외 코드 마스터(`frgn_code.mst`, 공개, 2026-10-05 다운로드)의 구분코드와 심볼. 구분코드 뜻은 KIS `stocks_info/해외주식지수정보.h`(W 세계주요지수, P 미국지수, F CME선물, X 환율, B 주요국정부채, R 국내금리, C 상품선물).

**`market` (해외·업종·선물)**

| 키 | 이름 | 구분 | 심볼 |
|---|---|---|---|
| `spx` | S&P500 | P | `SPX` |
| `nasdaq` | 나스닥 종합 | P | `COMP` |
| `nasdaq100` | 나스닥100 | P | `NDX` |
| `dow` | 다우존스 산업 | ? | `.DJI`(공식 예제의 코드, 마스터에 없음 → 실호출 확인, 안 되면 뺌) |
| `sox` | 필라델피아 반도체 | P | `SOX` |
| `nikkei` | 니케이 225 | W | `JP#NI225` |
| `hangseng` | 항셍 | W | `HK#HS` |
| `shanghai` | 상해종합 | W | `SHANG` |
| `dax` | 독일 DAX | W | `GR#DAX` |
| `ftse` | 영국 FTSE 100 | W | `GB#FTSE` |
| `sp500_futures` | E-Mini S&P500 선물 | F | `EDNH` |
| `nasdaq100_futures` | E-Mini 나스닥100 선물 | F | `ENXH` |

**`macro`**

| 키 | 이름 | 구분 | 심볼 |
|---|---|---|---|
| `us10y` | 미국 10년 국채 수익률 | B | `Y0202` |
| `us30y` | 미국 30년 국채 | B | `Y0201` |
| `us1y` | 미국 1년 T-Bill | B | `Y0203` |
| `fed_funds` | 미국 연방기금금리 | B | `Y0204` |
| `jp10y` | 일본 10년 국채 | B | `Y0207` |
| `kr3y` | 국고채 3년 | R | `Y0101` |
| `kr10y` | 국고채 10년 | R | `Y0106` |
| `cd91` | CD 91일 | R | `Y0112` |
| `usdkrw` | 원/달러 | X | `FX@KRW`(KMB) 또는 `FX@KRWKFTC` → 실호출로 하나 고름 |
| `jpykrw` | 원/엔 | X | `FX@KRWJS` |
| `eurusd` | 달러/유로 | X | `FX@EUR` |
| `usdjpy` | 엔/달러 | X | `FX@JPY` |
| `usdcny` | 위안/달러 | X | `FX@CNY` |
| `vix` | VIX | P | `VIX` |

**`commodity`**

| 키 | 이름 | 구분 | 심볼 |
|---|---|---|---|
| `gold` | 뉴욕 금 | C | `NYGOLD` |
| `silver` | 뉴욕 은 | C | `NYSILV` |
| `wti` | WTI 근월 | C | `WTIF` |
| `brent` | 브렌트 근월 | C | `BRENTF` |
| `copper` | 런던 구리 현물 | C | `LMECOC` |

- 표는 Broker에 하나만 둔다(키 → KIS 시장 구분·심볼·이름·단위). harness의 schema enum은 같은 키 목록이다. 키를 더하거나 빼는 것은 두 곳의 목록만 바꾸는 일이다.

## 3. KIS API

| 용도 | API | TR | 핵심 인자 | 응답 |
|---|---|---|---|---|
| 해외 지수·지수선물·환율·금리·상품의 지금 값과 기간 시세 | `/uapi/overseas-price/v1/quotations/inquire-daily-chartprice` | `FHKST03030100` | `FID_COND_MRKT_DIV_CODE`(N 해외지수·X 환율·I 국채·S 금선물), `FID_INPUT_ISCD`(심볼), `FID_INPUT_DATE_1`·`_2`, `FID_PERIOD_DIV_CODE` D·W·M | `output1` 지금 값(`ovrs_nmix_prpr`·전일 대비·등락률·전일 종가), `output2` 날짜별 줄(`stck_bsop_date`, `ovrs_nmix_oprc`·`hgpr`·`lwpr`·`prpr`, `acml_vol`) |
| 국내 지수 지금 값 | 기존 `inquire-daily-indexchartprice`의 `output1` | `FHKUP03500100` | 기존과 같음 | 지금 지수·전일 대비 |

- **분류마다 쓰는 시장 구분 코드**(W·P·F → N, X → X, B·R → I, C → S로 추정)는 공식 자료에 짝이 없다. 표에 키마다 시장 구분 코드를 적고 실호출로 확인한다. 안 되는 키는 표에서 뺀다.
- 기간 시세는 기존 이어 받기 규칙 `_dated_rows` 그대로(한 번 줄 수는 실호출로 확인). 최대 3번.
- 지금 값은 같은 API를 짧은 기간(오늘까지 일주일)으로 한 번 불러 `output1`을 쓴다(호출 1번).
- 근거: KIS 공식 저장소 `examples_llm/overseas_stock/inquire_daily_chartprice`와 `chk_inquire_daily_chartprice`의 필드·시장 구분 설명. 실제 KIS는 아직 부르지 않았다.

## 4. Broker

- 새 경로는 없다(IAM 변경 없음). 기존 `/quote`, `/history`가 대상을 하나 더 받는다.
  - `GET /quote?market=…|macro=…|commodity=…`: 지수·지표·상품의 지금 값. 응답 `{market|macro|commodity, name, unit, price, change, change_rate, observed_at}`. 국내 `market=kospi|kosdaq`도 같은 모양으로 지금 지수를 준다.
  - `GET /history?data=prices&period=…&(market|macro|commodity)=…`: 기존 응답 모양에 `name`(대상 이름)과 `price_unit`(가격 단위)을 더한다.
  - 대상이 둘 이상 오면 400(harness가 하나만 보낸다). 키가 표에 없으면 400.
- 지표 값은 소수가 있으므로 국내 지수처럼 유한 Decimal로 보존한다(정수 원 규칙을 쓰지 않는다).

### 단위

Broker 표가 키마다 KIS 값의 단위를 준다. 실호출로 확인해 채운다.

| 대상 | 단위(예상) |
|---|---|
| 지수·지수선물 | points |
| 금리 | % |
| 환율 | 기준 통화 1단위당 상대 통화(원/달러는 1달러당 원) |
| 금·은 | USD/트로이온스 |
| WTI·브렌트 | USD/배럴 |
| 구리(LME) | USD/톤 |

## 5. harness

- schema: `macro`·`commodity` 인자(enum은 §2 키), `market` enum에 §2 해외 키. 설명은 구분 규칙과 "목록에 없는 경제 지표(CPI·기준금리 등)와 해외 개별 종목은 web_search"를 적는다.
- `_ACTIONS` 표에 액션·data별 **받는 대상 종류**를 더한다(§1 표). `_resolve`가 대상 하나를 고른다(§1 우선순위).
- 결과 글:
  - 지금 값: "US 10-year Treasury yield, looked up at …: 4.12%; change from the previous close -0.03 (-0.72%)." 단위는 Broker의 `unit`을 그대로 붙인다. 날짜가 없는 값이라 기존 `_NO_SESSION_DATE` 문구를 붙인다.
  - 기간 시세: 지금 history prices 글 그대로, 단위 문구만 대상 단위로.

## 6. 진행

1. 이 계획 → Codex 계획 검토 → 반영.
2. Broker·harness 구현 → Codex 구현 검토 → Broker 병합·배포(승인).
3. 실호출(Claude, 승인): 해외 시세가 사용자 계좌에서 열리는지, 키별 시장 구분 코드, 단위, 한 번 줄 수, 실시간·지연, 날짜 기준(한국 날짜인지 현지 날짜인지). 안 되는 키는 표에서 뺀다(코드 표 수정만). **해외 시세가 막혀 있으면 §8로 다시 정한다.**
4. `broker` 시나리오에 질문을 더해 실행(유료, 승인).
5. harness 0.7.4 릴리스(병합된 두 수정 포함) → PIA 프롬프트·핀 → 배포 → Telegram 확인.

## 7. 실호출 예산과 시나리오

- **실호출:** 키마다 `quote` 한 번(31개, 시장 구분 코드가 틀리면 다른 코드로 한 번 더) + 분류별 `history` 하나씩(지수·선물·환율·금리·상품 5개, 각 최대 3번). 상한 **KIS GET 50번**. 값이 맞는지는 공개 시세와 크기만 비교한다. 기록은 형식·단위·줄 수·지연만.
- **시나리오(`broker` 세트에 추가):**

| 질문 | 기대 |
|---|---|
| 미국 10년물 금리 얼마야? | `quote` macro=us10y |
| 원달러 환율 3개월 추이 차트로 | `history` macro=usdkrw period=3m |
| 요즘 금값 어때? | commodity=gold |
| WTI 유가 1년 흐름 | `history` commodity=wti period=1y |
| 나스닥 요즘 어때? | market=nasdaq |
| 밤사이 나스닥 선물 어때? | `quote` market=nasdaq100_futures |
| 필라델피아 반도체 지수 | market=sox |
| VIX 얼마야? | macro=vix |
| 엔비디아 주가 | (관찰) web_search로 가는지 |
| 미국 CPI 발표 결과 알려줘 | (관찰) web_search로 가는지 |
| 요즘 DRAM 가격 | (관찰) web_search, 지어내지 않는지 |

- 오프라인 테스트: 대상 우선순위, 키 검사, 지표 결과 글과 단위, Broker 표·응답 해석·이어 받기.

## 8. KIS가 막히면

- 금리·환율이 막히면: FRED(미국 연준)·ECOS(한국은행)가 공식·무료다(일 단위). CPI·기준금리 같은 경제 지표를 나중에 `macro`에 더할 때도 같은 후보다.
- 해외 지수·상품이 막히면: Twelve Data(무료 하루 800회) 등이 후보지만 무료 한도는 서버 키 하나를 모든 회원이 나누므로 회원이 늘면 금방 닿고, 무료 요금제의 상업적 이용 조건도 봐야 한다.
- 어느 경우든 새 데이터원은 계획을 다시 써서 검토받는다.

## 9. 다음 작업으로 넘긴 것: 해외 종목·계좌·주문 (한 번에)

- 확인한 KIS 자료: 미국 종목 마스터 `{nas,nys,ams}mst.cod`(공개, 2026-10-05 기준 나스닥 5,251·뉴욕 2,840·아멕스 4,721행, 한글명 포함), 현재가 상세 `price-detail`(`HHDFS76200200`), 기간 시세 `dailyprice`(`HHDFS76240000`), 잔고·매수가능금액·외화 증거금·주문 API.
- 그때 함께 정할 것: 종목 식별(거래소+심볼), 통화와 소수 가격, 원화·외화 계좌 합계, 해외 주문 확인 흐름.

## 10. Codex 검토에서 특히 볼 것

1. 대상 인자 네 개와 우선순위가 `quote`·`history`에서 예외 없이 동작하는지.
2. Broker 표 하나와 harness enum이 같은 목록을 쓰는 구조. 경로·IAM을 늘리지 않은 것.
3. 지금 값을 기간 시세 API의 `output1`로 받는 방식(호출 1번).
4. 실호출 예산과 "안 되는 키는 표에서 뺀다"는 처리.
5. 덜어낼 것.
