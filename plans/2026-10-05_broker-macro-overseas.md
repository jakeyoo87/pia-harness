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

- **실호출:** 아래 "실호출 범위" 절에 적은 대로 31개 키 모두와 기간 시세 3가지. 값이 맞는지는 공개 시세와 크기만 비교한다. 기록은 형식·단위·줄 수·지연만.
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

## Codex 계획 검토 (2026-10-05, Asia/Seoul)

- 대상: 계획 `effb3d43e8fab8988e24c5adcdd4cc11f13ab24f`, harness main `45a8d56`, Broker main `7c5ea5c`. 현행 도구의 인자·숫자 표시·단위 변환·route·history 모델과 공식 자료, 이미 다운로드된 공개 마스터를 읽었다. 해외 종목·계좌·주문은 다음 작업, DRAM·NAND 제외, 데이터원 KIS라는 사용자 결정은 유지한다.
- **판정: 현재 네 대상 인자 구조를 유지하는 것을 권장한다. 구현 전에 보완할 P2는 표시 정밀도, 해외 history 단위 범위, 실호출 예산 세 건이다.** 시장 구분 코드와 서비스 지원 여부는 아직 실호출로 확인하지 않았으며, 마스터에 있다는 이유만으로 API 지원을 확정하지 않는다.

### 1. 구분과 대상 선택

- `name`은 종목, `market`은 주식시장·업종을 나타내는 지수와 그 지수선물, `macro`는 금리·환율·변동성, `commodity`는 상품 가격으로 두면 된다. §결정의 '지수가 아닌 경제 값'은 '금리·환율·변동성 지표'로 표현해 지수 형태인지에 따른 예외 설명을 없앤다. SOX는 market, VIX는 macro, CME 지수선물은 market, 환율은 macro라는 경계를 설명에 명시한다. VIX의 제공자 구분이 P라는 이유로 market에 옮길 필요는 없다. 사용자에게 어떤 수치인가와 KIS가 어떤 코드로 묶는가는 다른 정보이며, KIS 요청 코드는 지원표가 결정하면 된다. VIX만 따로 처리하는 런타임 분기는 필요 없다.
- 나스닥 종합 / 나스닥100 / 나스닥100 선물은 schema 설명에 이름을 각각 명시한다. 이 셋은 이름이 비슷하지만 다른 대상이다. `fed_funds`는 마스터의 **연방기금금리(콜)**로 표현해 FOMC의 목표 범위·결정과 혼동하지 않도록 한다. 한국 국고채 키도 기준금리가 아니라 수익률이다.
- 비교할 수 있는 축소안은 `name·market·indicator` 세 인자로 macro와 commodity를 합치는 것이다. 대상 인자와 우선순위 단계가 하나 줄고 VIX/상품 경계를 모델이 고를 필요가 줄지만, indicator의 의미가 넓어지고 금리·환율·상품의 이름·단위·키 검사는 그대로 필요하다. 기존 합의와 설명도 바꿔야 하므로 코드 감소 이득은 작다. **이번에는 네 인자를 유지한다.** target_kind+target 같은 새 축이나 종목/지표 통합 검색 계층은 권하지 않는다.
- 선택 규칙은 **action/data가 받는 대상 종류만 → 그중 우선순위상 첫 non-None 인자 → 그 값만 검사 → 하나만 전달**로 정한다. 쓰지 않는 종류는 먼저 제외한다. quote/history prices에는 넷, history investors에는 name·국내 market만 적용한다. 선택된 값이 빈 문자열·배열·잘못된 키면 오류 문장을 돌려주며 다음 대상으로 넘어가지 않는다. name이 유효하면 잘못 준 market/macro/commodity도 무시한다. schema 전체 market enum과 액션별 허용 목록을 구분한다.
- §1의 '대상 기본값 없음/없으면 묻기'는 quote와 history에 적용한다. account의 name 생략(전체 계좌), ranking의 market 기본 all은 '그대로'라는 표의 계약을 유지한다. Broker의 code/market/macro/commodity 중 하나만 허용하는 규칙과 harness의 우선순위는 충돌하지 않는다(harness는 선택된 하나만 보내고 직접 다중 요청은 400).

### 2. 지원표·API·현재 값

- 새 `/quote`·`/history` 경로나 IAM은 필요 없다. 현재 Bot role·연결 확인·오류 계약을 유지한다. 국내 종목 quote의 6자리 code·KRW 가격 모델과 주문 도구 경로는 건드리지 않고, 비종목 값은 계획대로 유한 Decimal과 해당 단위를 사용한다. 새 값에 기존 stock `_quote_json`의 int 변환을 적용하지 않는다.
- Broker의 새 지원표에 대상 종류·KIS 구분·심볼·표시 이름·단위를 둔다. harness enum은 같은 종류별 키 목록을 정적으로 반영한다. **두 저장소에 있는 목록은 자동으로 같아지지 않는다.** 키를 빼거나 바꿀 때 Broker 표와 harness enum/설명·해당 시나리오를 함께 맞춘다. 공유 패키지나 enum 조회용 새 API는 필요 없다. 국내 시장 코드와 새 해외 지원표까지 새 공통 계층으로 합칠 필요도 없다.
- [공식 해외 예제](https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/overseas_stock/inquire_daily_chartprice/inquire_daily_chartprice.py)의 경로·TR `FHKST03030100`·N/X/I/S 설명과 D/W/M 인자는 계획과 맞는다. [chk 필드](https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/overseas_stock/inquire_daily_chartprice/chk_inquire_daily_chartprice.py)는 output1의 `ovrs_nmix_prpr`·`ovrs_nmix_prdy_vrss`·`prdy_ctrt`, output2의 날짜와 `ovrs_nmix_*` OHLC를 확인하는 근거다. 국내 output1은 [공식 지수 chk](https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/domestic_stock/inquire_daily_indexchartprice/chk_inquire_daily_indexchartprice.py)의 `bstp_nmix_*` 필드를 사용한다.
- **quote를 한 번의 기간 시세 요청에서 output1으로 읽는 방식에 찬성한다.** quote는 그 응답의 필수 현재 값만 읽고 output2의 빈 자료나 기간 완주 여부에 묶지 않는다. history는 output2와 기존 날짜 감소/완료 규칙을 쓴다. 한 번은 논리 조회이고 `_send`의 기존 인증 재조회가 있으면 실제 GET은 추가된다.
- 공식 해외 예제는 tr_cont로 이어 받는 코드를 포함하지만 한 번 행 수나 끝 날짜를 옮긴 재조회 성공을 보장하지 않는다. §3의 기존 `_dated_rows` 재사용은 실호출에서 진행·완주를 확인하는 조건으로 유지한다. 예제의 재귀 구현을 복사하거나 새 자동 재시도·부분 결과·임의 정렬을 넣지 않는다.
- 공개 마스터를 새로 받지 않고 기존 파일을 직접 읽었다. 제안 키 31개 중 .DJI 외 심볼은 존재하며 usdkrw의 두 후보도 존재한다. .DJI는 마스터에는 없지만 공식 예제에 있어 현재의 확인 후보가 타당하다. [공식 헤더](https://github.com/koreainvestment/open-trading-api/blob/main/stocks_info/해외주식지수정보.h)의 구분코드는 마스터 형식이다. W/P/F→N, B/R→I, C→S의 API 대응을 증명하지 않으며, 계획의 '추정' 표시를 유지한다. 읽은 공개 마스터 SHA-256: `32a673c85ed53ca14f70bfd296bc201cf9ebfc7f2631458dd0e4def99a439bb9`.

### 3. 구현 전에 보완할 P2

1. **[P2] 새 값의 소수를 기존 두 자리 포맷으로 줄이지 않는다.** 위치: §4의 소수 보존과 §5:138–140의 결과 글; 현재 `src/pia_harness/broker_read.py`의 `_format`·`_plain`.
   - 합성 입력으로 기존 `_plain(1.1701)`과 `_plain(1.1749)`가 모두 `1.17`, `_plain(0.0042)`가 `0.00`인 것을 확인했다. 환율 추이와 금리의 작은 변동이 사라진다. Broker의 Decimal 보존만으로는 전달되는 값의 정밀도가 유지되지 않는다.
   - 새 비종목 지표의 값·OHLC·절대 변화는 Broker가 돌려준 소수를 그대로 표시한다는 규칙을 추가한다. 기존 시장 금액의 억/조 소수 둘째 자리 규칙은 별개로 유지한다. 키마다 임의 자릿수 표나 Decimal 표시용 새 계층을 만들 필요는 없다. 가짜 환율 두 값이 결과에도 서로 다르게 남는 검사가 충분하다.

2. **[P2] 해외 history는 단위 문구만 바꾸어 국내 결과 함수를 그대로 쓰면 안 된다.** 위치: §3:105의 acml_vol, §4:123–132의 단위, §5:140의 '지금 history prices 글 그대로'.
   - 현재 history는 code가 없으면 국내 시장 표를 골라 거래량을 1,000배 하고 shares를 붙이며 거래대금은 KRW 출처에서 억 원으로 바꾼다. 이를 해외 acml_vol에도 적용하면 선물 계약수 등 다른 값을 주식 수량으로 바꾼다. 합성 raw volume=123을 이 경로에 넣으면 `123,000 shares`가 된다. 새 API의 acml_vol 존재는 단위의 근거가 아니다.
   - **이번 새 비종목 history는 이름·단위·OHLC에 집중하고, 단위가 확인되지 않은 거래량·거래대금은 제외하는 것이 가장 작다.** 현재 PriceBar의 선택 필드를 None으로 두면 되고 새 단위 변환 체계는 필요 없다. 국내 종목·코스피/코스닥의 기존 거래량/거래대금은 유지한다. 비종목 값에는 수정주가·KRW·shares 문구를 붙이지 않는다.
   - 단위 표는 방향과 기준량도 확정한다. jpykrw는 1엔/100엔 기준이 아직 마스터만으로 확정되지 않으므로 확인 전 1엔이라고 단정하지 않는다. 금리의 절대 변화는 퍼센트포인트, 상대 등락률은 %로 구분한다. VIX는 변동성 지수의 points로 표에 명시한다. 상품은 해당 KIS 상품·통화·거래 단위로 표시해 국내 소매 금값 등으로 바꾸지 않는다. 코드가 정상 응답했다는 것과 대상·단위가 맞다는 것을 구분한다.

3. **[P2] 50회는 검증 목록의 최악 횟수가 아니라 실행 전체 상한으로 쓴다.** 위치: §7:152.
   - 31 quote + 5 history×3 = **46 GET**이다. 모든 키에서 대체 코드 1회까지 허용하면 **77 GET**이고, usdkrw 후보 비교·기존 인증 재조회 등도 실제 GET을 더 쓴다. 지금 서술대로 전체가 50회 안에 끝난다고 보장할 수 없다.
   - 승인받을 예산은 50회를 유지하고, **실제 GET 발행 시점에 page·코드 후보·인증 재조회까지 세며 상한에 닿으면 남은 항목을 미확인으로 두고 멈춘다**고 적는다. Token 발급 POST는 별도로 센다. 추가 요청은 새 승인이다. 런타임 Broker에 코드 후보 순회 기능을 넣는 것이 아니라 승인된 검증 실행의 계산이다.
   - 분류별 대표 확인으로 코드 대응을 먼저 정리하고 남은 키를 확인하면 반복되는 잘못된 후보 호출을 줄일 수 있다. history 다섯 개에는 기간을 적어 D/W/M을 나누고, 긴 자료의 날짜 진행·완주를 한 사례에서 확인한다. API가 3회 안에 완료되지 않으면 기존 계약대로 실패이며 미완성 줄을 정상 결과로 내지 않는다. 50회로 끝나지 않은 확인을 자동으로 '미지원 키'로 분류하지 않는다.

### 4. 안 되는 키 제거와 덜어낼 것

- **확인된 미지원 키를 지원표에서 빼는 방식에 찬성한다.** 다만 인증/회원 권한·요청 과다·timeout 같은 공통/일시 실패, 아직 안 끝난 검증, 구현 파서 오류를 개별 키 미지원으로 판단하지 않는다. 실패 원인이 키의 미지원인지 확인하고, 공통 문제가 생기면 검증을 멈춰 보고한다. 새 런타임 예외 분류·자동 데이터원 fallback은 제안하지 않는다. 삭제가 확정되면 양쪽 키 목록과 설명을 함께 수정한다.
- output1에 기준일이 없다면 조회 시각과 'KIS가 준 최근 값'으로 표현한다. '현재'는 조회 시각이며 실시간 체결·지연 없음·현지 거래일을 의미하지 않는다. 이전 `_NO_SESSION_DATE` 취지는 유지하되 금리/환율까지 무조건 오늘 장의 값이라고 쓰지 않는다. quote의 기준일을 history의 마지막 행 날짜와 같다고 자동 추정하지 않는다. 실호출 비교는 같은 대상·단위·시점으로 한다.
- §8의 대체 업체·무료 한도 상세는 이번 KIS 구현 범위 밖이므로 'KIS에서 열리지 않으면 멈추고 별도 계획' 한 줄로 줄일 수 있다. 지금 다른 제공자·해외 주식 검색·계좌/주문 모델·선물 만기 규칙·마스터 자동 갱신을 추가하지 않는다. 31개 고정 키 지원표부터 확인하면 된다.
- 유료 시나리오는 이 단계에서 실행하지 않는다. 선택 우선순위·잘못된 선택값의 요청 0회·안 쓰는 인자 무시·각 액션의 허용 market·Broker 표와 schema 키·숫자 보존·단위·output1-only quote와 날짜 이어 받기는 오프라인 검사가 맡는다. paid 사례는 질문의 대상과 action을 모델이 고르는지를 확인한다.

### 이번 검증·작업 범위

- 공식 GitHub 예제·현재 코드·다운로드된 공개 master/헤더만 읽었다. 네트워크를 끈 기존 PIA 이미지에서 위 합성 값의 표시 결과와 예산 산술만 확인했다. 계획만 바뀌므로 전체 suite·build는 실행하지 않았다.
- 기존 master는 12,980행이며 고정 바이트 경계(구분 1, 심볼 10, 영문명 39, 한글명 40 등)로 읽었다. 마스터에 종목 Q/H 등이 섞여 있지만 이번에는 지정된 비종목 키만 확인했다. 종목·계좌·주문 범위를 넓히지 않았다.
- 검토 결과만 이 계획서 끝에 추가해 같은 harness 브랜치에 커밋·push한다. 구현 코드·다른 저장소·브랜치 구조 변경, 실제 KIS·AWS 호출, 배포, 유료 시나리오 실행은 하지 않았다. 50회는 향후 별도 승인할 계획이며 이번 실호출 승인이 아니다.

## Codex 계획 검토 반영 (Claude, 2026-10-05)

네 인자 구조는 그대로 간다(Codex도 유지 권장). 지적은 모두 받는다. 본문과 다르면 이 절이 우선한다.

- **소수 보존(P2-1):** 새 지표(해외 지수·선물·금리·환율·VIX·상품)의 값·시가·고가·저가·종가·전일 대비는 Broker가 준 소수를 그대로 표시한다(자릿수 표 없음). 시장 금액의 억·조 둘째 자리 규칙은 별개로 그대로.
- **해외 기간 시세의 거래량·거래대금 제외(P2-2):** 새 지표의 history는 이름·단위·시가·고가·저가·종가만 준다(`volume`·`trading_value`는 None). 국내 종목·코스피·코스닥의 기존 거래량·거래대금은 그대로. 새 지표에는 수정주가·KRW·shares 문구를 붙이지 않는다.
- **단위:** 금리 값은 %, 전일 대비는 percentage points. 원/엔이 1엔 기준인지 100엔 기준인지는 실호출로 확인하고 그 전에는 단정하지 않는다. 상품은 KIS 상품의 통화·거래 단위 그대로.
- **실호출 예산(P2-3):** 아래 "실호출 범위" 절로 대체한다(사용자). 인증·요청 과다·timeout 같은 공통 실패는 키의 미지원으로 판단하지 않는다.
- **표현:** `macro`는 "금리·환율·변동성 지표"로 설명한다. 나스닥 종합·나스닥100·나스닥100 선물은 설명에 각각 적는다. `fed_funds`는 "연방기금금리(콜)", 국고채는 수익률이라고 적는다(기준금리·FOMC 목표와 구분). 지금 값은 "KIS가 준 최근 값, 조회 시각 기준"이며 실시간·오늘 장이라고 단정하지 않는다.
- **선택 규칙:** 액션·data가 받는 대상 종류만 본다 → 그중 `name`·`market`·`macro`·`commodity` 순서로 첫 non-None → 그 값만 검사(잘못이면 결과 문장, 다음 대상으로 넘어가지 않음) → 하나만 Broker에 보낸다. `account`의 name 생략(전체 계좌), `ranking`의 market 기본 all은 그대로.
- **§8 축소:** KIS에서 열리지 않으면 멈추고 별도 계획을 쓴다(대체 업체 상세는 그때).
- 두 저장소의 키 목록은 자동으로 같아지지 않는다. 키를 빼거나 바꿀 때 Broker 표, harness enum·설명, 시나리오를 함께 고친다.

## 구현 기록 (Claude, 2026-10-05)

| 저장소·브랜치 | 내용 |
|---|---|
| pia-broker `claude/broker-macro-overseas` `c067a85` | `series.py` 표 하나(31개 키 → 종류·이름·단위·KIS 시장 구분·심볼), `/quote`·`/history`가 대상 정확히 하나(code·market·macro·commodity)를 받음, `get_series_quote`(기간 시세 API의 `output1`), `get_price_history`가 series도 받음(거래량·거래대금 없음), 국내 지수 지금 값도 같은 방식. 응답에 `name`·`price_unit`. 테스트 236개 통과, ruff·mypy 통과 |
| pia-harness `claude/broker-macro-overseas` (이 커밋) | `macro`·`commodity` 인자와 `market` 해외 키, `_TARGETS` 표(액션·data별 받는 대상), `_target`(name → market → macro → commodity, 고른 값만 검사), 지표 지금 값·기간 시세 글(Broker 소수 그대로 `_exact`, 금리 전일 대비는 percentage points, 거래량 없음), 설명에 웹 검색으로 갈 것들, 시나리오 b17~b27과 가짜 Broker, README. 테스트 188개 통과 |

- Broker의 `/history`는 대상이 둘 이상이면 400이 됐다(전에는 code가 market보다 앞섰음). harness는 하나만 보낸다.
- 국내 `quote`에 `market=kospi|kosdaq`(지금 지수)도 생겼다. 같은 기간 시세 API의 `output1`.
- 실호출 전이라 시장 구분 코드(N·X·I·S)와 단위는 짐작이다. 실호출에서 안 되는 키는 Broker 표와 harness 목록에서 함께 뺀다.

## 실호출 범위 (사용자, 2026-10-05)

처음엔 50회를 서비스 상한으로 오해해 분류별 대표 8가지로 줄였다가, 배포 후 한 번 하는 확인이라는 것을 확인하고 키마다 보기로 되돌렸다(사용자). 상한 규칙은 두지 않는다.

- **지금 값:** 31개 키 모두 quote 한 번씩(31번). 시장 구분 코드, 단위, 실시간·지연, 마스터에 없던 `.DJI`, 원/엔 기준량(1엔·100엔)을 본다. 첫 조회 `spx`에서 해외 시세가 막히면 거기서 멈추고 §8로 간다.
- **기간 시세:** 일·주·월 단위마다 하나씩 `usdkrw` 1m, `spx` 1y, `gold` 3y(각 최대 3번). 날짜별 줄, 날짜 기준(한국·현지), 주·월 줄의 날짜를 본다.
- KIS 요청은 40번 안팎이다. 시장 구분 코드가 틀린 분류만 다른 코드로 한 번 더 묻는다.
- 안 되는 키는 Broker 표, harness 목록·설명, 시나리오에서 함께 뺀다. 단위가 다르면 단위 문구만 고친다.

## Codex 구현 검토 반영 (Claude, 2026-10-05)

Codex 구현 검토: P2 세 건(모두 harness), 대상 우선순위·Broker 단일 대상·31개 키와 스키마 동기화·해외 거래량 제외·국내 회귀는 확인. 세 건 모두 실제로 생기므로 그대로 고쳤다.

| P2 | 고친 것 |
|---|---|
| `_exact`가 `1e-05` 같은 지수 표기를 못 읽어 정상 응답이 형식 오류가 됨 | `Decimal(repr(x)).normalize()`를 `,f`로 쓰는 공통 포맷 하나로 바꿈. 작은 소수·정수·음수 단위 테스트 추가 |
| 시나리오 채점이 `target` 튜플을 못 읽어 b8·b10·b17–b24 정상 인자가 CHECK | `_filled`가 고른 대상을 원래 인자명(`market`·`macro`·`commodity`·`name`)으로 펼침 |
| fake의 단위·봉 단위가 Broker와 다름(WTI 1년이 points·day) | fake 단위에 `wti` USD per barrel 추가, 국내·series 모두 같은 기간별 봉 단위 표(`_BAR_UNIT`) 사용 |

- 오프라인으로 시나리오 채점을 기대 인자로 재현: `expect_args`가 있는 22개 모두 PASS. fake WTI 1년은 `USD per barrel`·`week`.
- harness 테스트 189개 통과, ruff·mypy 오류 수는 main과 같음.

## 실호출 결과와 후보 시험 (Claude·사용자, 2026-10-05)

Broker `c067a85` dev 배포 후 31개 키 quote + 기간 시세 3가지를 운영 Broker로 불렀다(요청 사이 1.5초).

| 분류 | 결과 |
|---|---|
| 해외 지수·지수선물(N) | dow 빼고 11개 정상(예: spx 7,722.72, nikkei 69,946.86, 나스닥100 선물 31,022.25) |
| 금리(I) | 8개 정상. 한국 금리도 I(마스터 분류 R이어도) |
| 환율(X)·VIX(N) | 6개 정상. 원/엔은 **1엔 기준**(8.51 = 1,344 ÷ 157.82) → 단위 "KRW per JPY" |
| dow(`.DJI`, 공식 예제 코드) | 200인데 값 0. 마스터에도 다우 지수 없음 |
| 상품 5개(S) | 모두 200인데 값 0, gold 3y history는 502 SCHEMA_INVALID. 공식 문서의 S는 "금선물"뿐이고 심볼 예시 없음 |
| usdkrw 1m | 일봉 20줄, 1번 호출 |
| spx 1y | 주봉 52줄, 1번 호출, 날짜는 주 첫 거래일(월) |

- KIS는 없는 심볼에 오류 대신 0을 준다. 0을 오류로 보는 규칙은 넣지 않는다(금리는 실제로 0일 수 있음). 안 되는 키는 표에서 뺀다.
- **후보 시험(사용자):** Broker는 표에 있는 키만 받으므로, Broker 표에 임시 키 6개를 넣어 배포하고 부른다. harness에는 넣지 않아 PIA가 부르지 않는다.
  - `probe_gold_n`·`probe_gold_x`·`probe_gold_i`: `NYGOLD`를 시장 구분 N·X·I로
  - `probe_dow_dji`·`probe_dow_djia`·`probe_dow_indu`: 시장 구분 N에 `DJI`·`DJIA`·`INDU`
  - 금이 되는 코드가 나오면 나머지 상품 4개를 그 코드로 확인(최대 4번 더).
  - 끝나면 되는 코드를 원래 키에 반영하고, 임시 키와 끝내 안 되는 키를 Broker 표·harness 목록·시나리오에서 함께 지운 뒤 다시 배포한다.
- Broker `claude/broker-series-probe`: 임시 키 6줄, 원/엔 단위 확정. 테스트 236개 통과.

### 후보 시험 결과와 정리 (2026-10-05)

Broker `738e12f` 배포 후 임시 키 6개(요청 사이 1.5초):

| 임시 키 | 결과 |
|---|---|
| gold N | 4,162.3 USD/온스, 전일 대비 -40 (-0.95%) |
| gold X | 4,162.2998 (같은 값, 소수 잡음) |
| gold I | 0 |
| dow `DJI`·`DJIA`·`INDU` (N) | 모두 0 |

- **정리:** 상품 5개는 시장 구분 N(금 확인, 나머지 4개는 배포 후 확인). dow는 표에서 뺀다. 임시 키 삭제. 키는 30개.
- Broker `claude/broker-series-final` `2e3f96c`: 표·docstring·README·테스트 키 수 30.
- harness(이 커밋): `_SERIES_MARKETS`에서 dow 삭제, `market` 설명에 "다우 지수는 없음: web_search", 시나리오 b28 "다우 지수 어때?" → `web_search`, README.
- 다음: Codex 검토 → Broker 배포 → 상품 5개 quote + gold 3y history 확인(6번) → 안 되는 상품은 뺌 → b28 시나리오만 실행(유료) → harness 병합.

### 상품 확인 (Broker `2e3f96c` 배포 후, 2026-10-05)

| 조회 | 결과 |
|---|---|
| gold | 4,162.3 USD/온스, -40 (-0.95%) |
| silver | 60.42 USD/온스, -0.76 (-1.24%) |
| wti | 91.11 USD/배럴, -1.76 (-1.89%) |
| brent | 102.25 USD/배럴, -0.06 (-0.06%) |
| copper | 14,355 USD/톤, +20 (+0.14%) |
| gold 3y history | 월봉 36줄, 1번 호출. 월봉 날짜는 그 달 1일(국내 지수는 마지막 거래일이었음) |

- 상품 5개 모두 시장 구분 N에서 정상. 키 30개 모두 실호출로 값을 확인했다.
