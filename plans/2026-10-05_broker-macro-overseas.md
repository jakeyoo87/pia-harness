# broker 도구: 해외 지수·거시 지표·상품 가격·미국 종목

날짜: 2026-10-05 (Asia/Seoul)
브랜치: pia-harness `claude/broker-macro-overseas`(계획, harness 구현), pia-broker 같은 이름(Broker 구현)
담당: Claude 계획·구현·실호출 확인, Codex 계획·구현 검토와 병합·릴리스·배포
기준: harness main `45a8d56`, Broker main `7c5ea5c`, broker 도구 구조는 `plans/2026-10-04_broker-tool-structure.md`

## 목표

사용자가 많이 물을 시장 수치를 증권사 값으로 답한다. 지금은 웹 검색으로 답하거나 답하지 못한다.

- "미국 10년물 금리 얼마야?", "원달러 환율 3개월 추이 차트로", "금값", "유가"
- "나스닥 요즘 어때?", "밤사이 나스닥 선물", "필라델피아 반도체 지수"
- "엔비디아 주가", "애플 1년 차트"

다음 릴리스(0.7.4)는 이것과 이미 main에 병합한 두 수정(시장 금액 단위 항목별 고정, 매수 가능 현재가 지정가)을 함께 담는다(사용자, 2026-10-05).

## 결정 (사용자, 2026-10-05)

- 새 액션을 만들지 않는다. `quote`(지금)와 `history`(기간별)의 **조회 대상**을 넓힌다.
- 대상 인자는 네 개이고, 각각 뜻이 하나다.

| 인자 | 뜻 | 이번 범위 |
|---|---|---|
| `name` | 질문 대상인 종목 하나 | 국내 종목·ETF + **미국 종목·ETF(나스닥·뉴욕·아멕스)** |
| `market` | 지수로 나타내는 시장(시장 전체 또는 업종) | 코스피·코스닥 + **해외 대표 지수, 업종 지수(SOX), CME 지수선물** |
| `macro` (새) | 거시 지표 | **미국·일본·한국 금리, 환율, VIX** |
| `commodity` (새) | 상품 가격 | **금·은·유가·구리, DRAM·NAND** |

- 구분 규칙: 지수로 나타내는 시장은 `market`, 지수가 아닌 경제 값은 `macro`, 상품 하나의 가격은 `commodity`. VIX는 지수지만 시장이 아니라 변동성 지표라 `macro`. SOX는 업종 지수라 `market`.
- 해외 지수를 `market`에 넣어도 `market`의 뜻은 같고 **받는 값 목록만 액션·데이터마다 다르다**(기존 원칙). 해외 시장은 KIS가 지수 값만 주므로 `history`+`investors`와 `ranking`의 `market` 목록에는 들어가지 않는다.
- 이번에 넣지 않는 것(자리는 정해져 있음): 미국 외 해외 종목(`name`), 코스피200 선물(`market`, 만기월마다 코드가 바뀌어 근월물 규칙이 따로 필요), 증시 자금 고객예탁금 등(`market`+새 `data`).
- KIS에 없는 것: CPI·고용·GDP 같은 경제 지표 발표값, 한국은행 기준금리·FOMC 결정, 가상자산 → 지금처럼 웹 검색. 도구 설명에 "목록에 없는 경제 지표는 웹 검색"을 적는다.
- 데이터원은 KIS(이미 연동, 회원별 키라 한도가 회원마다 나뉨, 추가 비용 없음). Investing.com은 공식 API가 없어 쓰지 않는다. KIS가 막히면 §9.

## 1. 액션별 대상

| 액션 + data | 받는 대상 | 비고 |
|---|---|---|
| `account` | `name`(국내 종목만, 선택) | 계좌·주문은 국내 그대로. 미국 종목 이름이면 "국내 계좌만 조회한다"는 결과 문장 |
| `quote` + `prices` | `name`, `market`, `macro`, `commodity` 중 하나 | 지수·지표·상품은 지금 값과 전일 대비 |
| `history` + `prices` | 위 넷 중 하나 | 기간·일주월 단위 규칙은 지금과 같음 |
| `history` + `investors` | `name`(국내), `market`(kospi·kosdaq) | 그대로 |
| `ranking` | `market`(all·kospi·kosdaq) | 그대로 |

- **대상이 여럿 오면:** `name` → `market` → `macro` → `commodity` 순서로 앞의 것을 쓰고 나머지는 무시한다(지금의 "name이 있으면 market 무시"를 네 개로 넓힌 같은 규칙). 하나도 없으면 "무엇을 조회할지"를 묻는 결과 문장.
- 잘못된 값 검사는 지금 `_resolve` 규칙 그대로: 생략만 기본값 없음(대상에는 기본값이 없다), 문자열·허용 값 검사, 쓰지 않는 인자는 무시.

## 2. 값 목록 (처음 여는 것)

KIS 해외 코드 마스터(`frgn_code.mst`, 공개, 2026-10-05 다운로드)의 구분코드와 심볼. 구분코드 뜻은 KIS `stocks_info/해외주식지수정보.h`(W 세계주요지수, P 미국지수, F CME선물, X 환율, B 주요국정부채, R 국내금리, C 상품선물, M 반도체; 파일의 E는 정의에 없는 원자재 근월물).

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
| `dram` | DRAM DDR4 8Gb | M | `4D8G182` |
| `nand` | NAND 8Gb SLC | M | `N8108S` |

- 마스터의 DRAM·NAND는 DDR3·DDR4·SLC 같은 예전 규격뿐이다(DDR5 없음). 결과 문장에 규격 이름을 그대로 적어 "DRAM 가격"을 최신 규격 가격으로 말하지 않게 한다.
- 표는 Broker에 하나만 둔다(키 → KIS 시장 구분·심볼·이름·단위). harness의 schema enum은 같은 키 목록이다. 키를 더하거나 빼는 것은 두 곳의 목록만 바꾸는 일이다.

## 3. KIS API

| 용도 | API | TR | 핵심 인자 | 응답 |
|---|---|---|---|---|
| 해외 지수·환율·금리·상품의 지금 값과 기간 시세 | `/uapi/overseas-price/v1/quotations/inquire-daily-chartprice` | `FHKST03030100` | `FID_COND_MRKT_DIV_CODE`(N 해외지수·X 환율·I 국채·S 금선물), `FID_INPUT_ISCD`(심볼), `FID_INPUT_DATE_1`·`_2`, `FID_PERIOD_DIV_CODE` D·W·M | `output1` 지금 값(`ovrs_nmix_prpr`·전일 대비·등락률), `output2` 날짜별 줄(`stck_bsop_date`, `ovrs_nmix_oprc`·`hgpr`·`lwpr`·`prpr`, `acml_vol`) |
| 미국 종목 지금 값 | `/uapi/overseas-price/v1/quotations/price-detail` | `HHDFS76200200` | `EXCD`(NAS·NYS·AMS), `SYMB` | `last`·`base`(전일 종가)·`tvol`·`tamt`·`tomv`(시가총액)·`perx`·`pbrx`·`h52p`·`l52p`·`curr`, 원환산 `t_xprc`·`t_rate`(당일 환율) |
| 미국 종목 기간 시세 | `/uapi/overseas-price/v1/quotations/dailyprice` | `HHDFS76240000` | `EXCD`, `SYMB`, `GUBN` 0일·1주·2월, `BYMD` 기준일, `MODP` 1(수정주가) | `output2` 줄(`xymd`, `open`·`high`·`low`·`clos`, `tvol`·`tamt`) |
| 국내 지수 지금 값 | 기존 `inquire-daily-indexchartprice`의 `output1` | `FHKUP03500100` | 기존과 같음 | 지금 지수·전일 대비 |

- **분류마다 쓰는 시장 구분 코드**(W·P·F → N, X → X, B·R → I, C·M → S로 추정)는 공식 자료에 짝이 없다. 표에 키마다 시장 구분 코드를 적고 실호출로 확인한다. 안 되는 키는 표에서 뺀다.
- 해외 지수·지표 기간 시세는 기존 이어 받기 규칙 `_dated_rows` 그대로(한 번 줄 수는 실호출로 확인). 미국 종목 기간 시세는 `BYMD`를 가장 오래된 날짜의 전날로 옮겨 이어 받는다(같은 규칙, 날짜 인자 이름만 다름). 최대 3번.
- 근거: KIS 공식 저장소 `examples_llm/overseas_stock/{inquire_daily_chartprice,price_detail,dailyprice}`와 `chk_*`의 필드, `stocks_info/overseas_stock_code.py`(마스터 형식). 실제 KIS는 아직 부르지 않았다.

## 4. Broker

### 4.1 미국 종목 목록

- 지금 국내 종목 목록(`instruments.json` + 하루 한 번 공개 마스터 갱신)과 같은 장치에 미국 마스터 세 개를 더한다: `https://new.real.download.dws.co.kr/common/master/{nas,nys,ams}mst.cod.zip`(공개, 키 불필요, 탭 구분, CP949). 2026-10-05 기준 나스닥 5,251, 뉴욕 2,840, 아멕스 4,721행(종목 2와 ETF 3, 한글명 포함).
- 넣는 행: 보안 유형 2(종목)·3(ETF). 저장 값은 `{code, name, market}`로 국내와 같고, 미국 종목의 `code`는 `NAS:NVDA`처럼 **거래소:심볼**, `market`은 `NAS`·`NYS`·`AMS`, `name`은 한글명(없으면 영문명).
- 찾기: 지금처럼 이름(공백·대소문자 무시)과 코드로 찾고, 미국은 심볼(`NVDA`)과 영문명도 찾는다. 여러 개면 지금처럼 후보를 돌려준다(예: "알파벳" A·C주).
- 다운로드·검사 규칙은 지금 그대로(고정 https 주소, TLS 검증, 크기 상한, 시장별로 패키지 목록의 절반 이상일 때만 교체). 패키지 목록 파일에 미국 종목을 더하므로 크기가 약 3배가 된다.

### 4.2 경로

새 경로는 없다(IAM 변경 없음). 기존 `/quote`, `/history`가 대상을 하나 더 받는다.

- `GET /quote?code=…`: 국내 6자리(지금) 또는 미국 `NAS:NVDA`.
- `GET /quote?market=…|macro=…|commodity=…`: 지수·지표·상품의 지금 값. 응답 `{target, name, unit, price, change, change_rate, observed_at}`.
- `GET /history?data=prices&period=…&(code|market|macro|commodity)=…`: 기존 응답에 `unit`(가격 단위)과 `name`(대상 이름)을 더한다.
- 미국 종목 `/quote` 응답은 국내와 같은 필드에 `currency`(USD), 원환산 가격·당일 환율(KIS 값 그대로)을 더한다. 계산하지 않는다.
- 대상이 둘 이상 오면 400(harness가 하나만 보낸다). 키가 표에 없으면 400.

### 4.3 단위

Broker 표가 키마다 KIS 값의 단위를 함께 준다(`unit`). 실호출로 확인해 채운다.

| 대상 | 단위(예상) |
|---|---|
| 지수·지수선물 | points |
| 금리 | % |
| 환율 | 기준 통화 1단위당 상대 통화(예: 원/달러는 1달러당 원) |
| 금·은 | USD/트로이온스 |
| WTI·브렌트 | USD/배럴 |
| 구리(LME) | USD/톤 |
| DRAM·NAND | USD(개당) |
| 미국 종목 가격 | USD |

## 5. harness

- schema: `macro`·`commodity` 인자(enum은 §2 키), `market` enum에 §2 해외 키. 설명은 구분 규칙과 "목록에 없는 경제 지표(CPI·기준금리 등)는 web_search"를 적는다.
- `_ACTIONS` 표에 액션·data별 **받는 대상 종류**를 더한다(§1 표). `_resolve`가 대상 하나를 고른다(§1 우선순위).
- 결과 글:
  - 지수·지표·상품 지금 값: "US 10-year Treasury yield, looked up at …: 4.12%; change from the previous close -0.03 (-0.72%)." 단위는 Broker의 `unit`을 그대로 붙인다.
  - 기간 시세: 지금 history prices 글 그대로, 단위 문구만 대상 단위로.
  - 미국 종목 지금 값: 가격·전일 대비·거래량·거래대금·시가총액은 달러. 금액 단위는 국내 규칙과 같은 "항목마다 고정": 시가총액은 조 달러, 거래대금은 억 달러. 원환산 가격과 당일 환율은 KIS 값 그대로 함께 적는다.
  - 시장 금액 단위 규칙(시가총액 조, 나머지 억, 항목마다 고정)은 통화만 다를 뿐 같다.
- `_NO_SESSION_DATE`(조회 시각 기준)는 날짜 없는 지금 값에 그대로 쓴다. 해외는 한국 시간과 거래일이 달라 "the latest session's figures"라는 기존 문구가 그대로 맞다.
- `account` + 미국 종목 이름: 결과 문장 "The account lookup covers the domestic KIS account only."

## 6. 진행 단계

1. 이 계획 → Codex 계획 검토 → 반영.
2. **1단계 구현:** `market` 해외·업종·선물, `macro`, `commodity`(KIS API 하나, Broker 표 하나) + harness. Codex 구현 검토 → Broker 병합·배포(승인).
3. **1단계 실호출(Claude, 승인):** 해외 시세가 사용자 계좌에서 열리는지, 키별 시장 구분 코드, 단위, 한 번 줄 수, 지연 여부. 안 되는 키는 표에서 뺀다. **해외 시세가 막혀 있으면 2단계를 멈추고 §9로 다시 정한다.**
4. **2단계 구현:** 미국 종목 목록·현재가·기간 시세 + harness. Codex 구현 검토 → Broker 병합·배포(승인) → 실호출.
5. `broker` 시나리오에 매크로·해외 질문을 더해 실행(유료, 승인).
6. harness 0.7.4 릴리스(병합된 두 수정 포함) → PIA 프롬프트·핀 → 배포 → Telegram 확인.

## 7. 실호출 확인 (단계마다, 승인 후)

- **1단계:** 키마다 `quote` 한 번(키 33개 → 최대 33번, 시장 구분 코드가 틀리면 다른 코드로 한 번 더) + 분류별 `history` 하나씩(지수·환율·금리·상품·선물 5개, 각 최대 3번). 상한 **KIS GET 60번**. 지금 값과 줄이 맞는지는 공개 시세(뉴스·거래소)와 크기만 비교한다.
- **2단계:** 미국 종목 3개(나스닥 NVDA, 뉴욕 하나, 아멕스 ETF SPY) `quote`·`history` 1y. 상한 **15번**.
- 확인할 것: 해외 시세 권한, 실시간·지연, 시장 구분 코드, 단위, 날짜(한국 날짜인지 현지 날짜인지), 이어 받기.
- 기록은 형식·단위·줄 수·지연만. 계좌 정보는 다루지 않는다.

## 8. 시나리오 (`broker` 세트에 추가)

| 질문 | 기대 |
|---|---|
| 미국 10년물 금리 얼마야? | `quote` macro=us10y |
| 원달러 환율 3개월 추이 차트로 | `history` macro=usdkrw period=3m |
| 요즘 금값 어때? | `quote` 또는 `history` commodity=gold |
| WTI 유가 1년 흐름 | `history` commodity=wti period=1y |
| 나스닥 요즘 어때? | `quote`/`history` market=nasdaq |
| 밤사이 나스닥 선물 어때? | `quote` market=nasdaq100_futures |
| 필라델피아 반도체 지수 | market=sox |
| VIX 얼마야? | macro=vix |
| 엔비디아 주가 | `quote` name=엔비디아 |
| 애플 1년 차트 | `history` name=애플 period=1y |
| 미국 CPI 발표 결과 알려줘 | (관찰) web_search로 가는지 |
| 비트코인 시세 | (관찰) web_search, 지어내지 않는지 |

오프라인 테스트: 대상 우선순위, 키 검사, 지표 결과 글과 단위, 미국 종목 코드 `NAS:NVDA` 전달, 미국 금액 단위, account+미국 종목 문장, Broker 표·마스터 해석·이어 받기.

## 9. KIS가 막히면

- 해외 시세가 사용자 계좌로 막히면: 미국 종목은 Finnhub(무료 분당 60회)·Twelve Data(무료 하루 800회)가 후보다. 무료 한도는 서버 키 하나를 모든 회원이 나누므로 회원이 늘면 금방 닿고, 무료 요금제의 상업적 이용 조건도 봐야 한다.
- 금리·환율만 막히면: FRED(미국 연준)·ECOS(한국은행)가 공식·무료다(일 단위). CPI·기준금리 같은 경제 지표를 나중에 `macro`에 더할 때도 같은 후보다.
- 어느 경우든 새 데이터원은 계획을 다시 써서 검토받는다.

## 10. Codex 검토에서 특히 볼 것

1. 대상 인자 네 개와 구분 규칙(§0 결정, §1 우선순위)이 `quote`·`history`에서 예외 없이 동작하는지.
2. Broker 표 하나(키 → 시장 구분·심볼·이름·단위)와 harness enum이 같은 목록을 쓰는 구조. 경로·IAM을 늘리지 않은 것.
3. 미국 종목 코드 `NAS:NVDA` 형식과 종목 목록 확장(마스터 세 개, 같은 갱신 규칙).
4. 단계 나누기(1단계 실호출로 해외 시세 권한을 먼저 확인)와 실호출 예산.
5. 덜어낼 것.
