# data_pipeline — API 명세서 (오딘 3.0)

> 3.0이 원천 데이터를 어디서, 어떤 호출로 가져오는지 정리한 문서.
> 원칙: **공개 제품이므로 공식 API 우선.** 남의 사이트 HTML 크롤링은 지양(깨지기 쉬움 + 약관 리스크).
> ⚠️ 키(appkey/appsecret/crtfc_key 등)는 문서·코드·로그 어디에도 적지 않는다 → 각자 컴퓨터에만(브레인 §7-2).

## 0. 소스 3곳 역할 분담

| 소스 | 단위 | 담당 | 채우는 표 |
|------|------|------|-----------|
| **KRX**(거래소) | 상장종목(ticker) | 종목 뼈대·시장·종류·시총·상장/상폐·지수편입 | `dim_stock` |
| **DART**(금감원) | 회사(corp) | 회사정보·업종·재무·공시 | `dim_stock_profile`, `dim_sector`, 재무·공시 |
| **KIS**(한국투자증권) | 시세 | 실시간 시세·지수·순위·수급·상태·(실행: 주문) | `raw_*`, `state_*`, 상태 갱신 |

연결키: KRX·KIS는 `stock_code`(6자리), DART는 `corp_code` → **DART `corpCode.xml`이 `stock_code ↔ corp_code`를 이어준다.**

---

## 1. KIS (한국투자증권 OpenAPI)

- 포털: https://apiportal.koreainvestment.com/ · 예제: https://github.com/koreainvestment/open-trading-api
- **인증**: `appkey` + `appsecret` → OAuth 토큰(`/oauth2/tokenP`, POST). 토큰 23h 유효, **발급 1분 1회 제한**.
- **공통 헤더**: `authorization: Bearer <token>`, `appkey`, `appsecret`, `tr_id`(엔드포인트별 코드).
- **호출 한도**: 실전 ~초당 20건 수준(안전하게 5건/초로 직렬화 권장). 모의계좌는 더 낮음. 초과 시 `EGW00201`/`EGW00215`.
- 시세 시장구분 `FID_COND_MRKT_DIV_CODE`: `J`=KRX, `NX`=NXT, `UN`=통합, `U`=지수.

### 읽기 (data_pipeline·state_module용) — 검증됨
| 용도 | 경로 | TR ID | 핵심 응답 |
|------|------|-------|-----------|
| 종목 현재가 | `/uapi/domestic-stock/v1/quotations/inquire-price` | `FHKST01010100` | 현재가·시총(hts_avls)·PER/PBR/EPS·52주·거래정지(trht_yn)·관리(iscd_stat_cls_code)·상하한가 |
| 종목 일봉 | `/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice` | `FHKST03010100` | OHLCV (일/주/월, ~100봉/콜, 페이지네이션) |
| 종목 분봉 | `/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice` | `FHKST03010200` | 1분봉 (~30개/콜) |
| **지수 현재가** | `/uapi/domestic-stock/v1/quotations/inquire-index-price` | `FHPUP02100000` | 지수값·등락·거래량/대금·상승/하락/보합 종목수 (KOSPI 0001, KOSDAQ 1001, 업종 1024 등) |
| **지수 일봉** | `/uapi/domestic-stock/v1/quotations/inquire-daily-indexchartprice` | `FHKUP03500100` | 지수 OHLC (→ `raw_index_ohlc`) |
| 거래량 순위 | `/uapi/domestic-stock/v1/quotations/volume-rank` | `FHPST01710000` | 상위 ~30 (화면코드 20171) |
| 등락률 순위 | `/uapi/domestic-stock/v1/ranking/fluctuation` | `FHPST01700000` | 상승/하락 순위 |
| 52주 신고/신저 근접 | `/uapi/domestic-stock/v1/ranking/near-new-highlow` | `FHPST01870000` | 시장 폭(breadth) 신호. 상위 30건 한계 |
| 종목 투자자 수급 | `/uapi/domestic-stock/v1/quotations/inquire-investor` | `FHKST01010900` | 외국인/기관/개인 순매수 |
| 호가 10단계 | `/uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn` | `FHKST01010200` | 매도/매수 호가·잔량 |

### 계좌·주문 (execution_module 전용 — 비공개)
| 용도 | 경로 | TR ID |
|------|------|-------|
| 잔고조회 | `/uapi/domestic-stock/v1/trading/inquire-balance` | `TTTC8434R` |
| 주문 | (실행 모듈에서만) | — |

---

## 2. DART (전자공시 OpenAPI)

- 포털: https://opendart.fss.or.kr/ · 개발가이드: https://opendart.fss.or.kr/guide/main.do
- **인증**: 단일 인증키 `crtfc_key`(모든 호출 파라미터로 첨부).
- **호출 한도**: 분당 1,000 초과 시 제한(일 약 20,000건 — 정확치는 포털 FAQ 확인).
- 베이스: `https://opendart.fss.or.kr/api`

| 용도 | 경로 | 형식 | 핵심 |
|------|------|------|------|
| **기업코드 매핑** | `/corpCode.xml` | zip(xml) | 전체 상장사 `corp_code ↔ stock_code ↔ corp_name`. **DART 연결의 시작점**(1회 받아 캐시) |
| **기업개황** | `/company.json` | json | 대표자·설립일·결산월·**업종코드(induty_code)**·주소·홈페이지 → `dim_stock_profile`·`dim_sector` (⚠️ 옛 코드엔 미구현, 3.0에서 추가) |
| 단일회사 전체 재무 | `/fnlttSinglAcntAll.json` | json | 재무상태표+손익. `reprt_code`: 11011(사업)/11012(반기)/11013(1Q)/11014(3Q), `fs_div`: CFS(연결)/OFS(별도) |
| 공시목록 | `/list.json` | json | 종목별 최근 공시(수주·증자·임상·M&A 등) |

**⚠️ DART 키 = 라이브(옛) 시스템과 공유 + 같은 IP** → 대량 호출 시 라이브 DART 접근 방해 위험(브레인 §1).
- 한도: 분당 ~1,000회. 버스트로 넘기면 **IP throttle(연결 강제 끊김)** 발생.
- 적재 스크립트(`ingest_dart_corpcode.py`·`ingest_dart_company.py`)는 **느리게(0.3s 간격) + throttle 지속 시 자동중단**으로 하드닝됨.
- **적재 현황(2026-08-31)**: `dim_stock_profile` corp_code 연결 2,761 완료 / **기업개황 999/2,761 채움**.
- **보류·재검토**: 나머지 개황 1,762건은 **장 마감 후(라이브 한가할 때)** `ingest_dart_company.py` 재실행으로 이어서 채운다
  (induty_code 빈 것만 대상). 장기적으론 **3.0 전용 DART 키 분리**(§7-2) 권장.

---

## 3. KRX 계열 — 종목 마스터 + 시장 통계 (신규 도입)

KRX 데이터는 포털이 둘로 나뉜다. **키·엔드포인트가 서로 다르다.**

### 3-1. 공공데이터포털(data.go.kr) 금융위 API — 종목 마스터·시세 (`DATA_GO_KR_KEY`)
- 포털: https://www.data.go.kr/ · **무료**, JSON/XML, **서비스키 1개로 신청한 여러 API 사용**
- 공통: REST GET, `serviceKey`(=DATA_GO_KR_KEY)·`numOfRows`·`pageNo`·`resultType`(xml/json). 인증키는
  Encoding/Decoding 두 형태 — **Decoding 키** 권장(이중 인코딩 방지).

**(1) 금융위 주식시세정보 — dim_stock 주 소스** ★ (적재: `data_pipeline/ingest_krx_stock.py`)
- 서비스: `GetStockSecuritiesInfoService` / 오퍼레이션 `getStockPriceInfo`
- URL: `https://apis.data.go.kr/1160100/service/GetStockSecuritiesInfoService/getStockPriceInfo`
- 응답 필드: `basDt`·`srtnCd`·`isinCd`·`itmsNm`·`mrktCtg`(KOSPI/KOSDAQ/KONEX)·`clpr`(종가)·`mkp/hipr/lopr`·
  `vs/fltRt`·`trqu`(거래량)·`trPrc`(거래대금)·**`lstgStCnt`(상장주식수)·`mrktTotAmt`(시가총액)**
- → `dim_stock`: stock_code(srtnCd, 여기선 'A' 접두 없음)·name(itmsNm)·market(mrktCtg)·**market_cap(mrktTotAmt)·shares_out(lstgStCnt)**
- **왜 주 소스**: 보통주+**우선주**를 다 주고 시총·주식수까지 한 번에. (getItemInfo는 보통주 전용이라 부족)
- 조회: `basDt`로 최신 거래일 스냅샷. 코드 필터는 6자리 영숫자(신형우선주 `00088K` 포함).
- **실측(2026-08-27)**: **2,875건 적재, 시총 100% 채움** (KOSPI 944·KOSDAQ 1823·KONEX 108).

**(2) 금융위 KRX상장종목정보(getItemInfo) — DART 연결 보조** (가이드: `00_reference/…KRX상장종목정보.docx`)
- URL: `.../GetKrxListedInfoService/getItemInfo` · 필드에 **`crno`(법인등록번호)·`corpNm`(법인명)** 있음
- 용도: `dim_stock_profile`의 DART 연결 시 **법인등록번호(=DART jurir_no)로 정확 매칭**. (보통주 전용이라 로스터엔 부적합)

- **ETF·ETN — 보류(2026-08-31 결정)**: 주식시세정보엔 없음(별도 금융위 ETF시세·ETN시세 API 필요).
  조회 신호는 KIS 지수·업종지수로 대체 가능하고, ETF 매매는 NAV·괴리율·레버리지 decay 등 별도 취급이 필요 →
  **전량(~1,400) 편입 안 함.** 현재 dim_stock ≈ 2,875(주식). 옛 시스템 4,288과의 차이가 대략 ETF+ETN.
  **재검토 시점**: ETF를 매매에 쓰는 전략(모델)을 설계할 때 — 그때 핵심 ETF 수십 개만 `type='ETF'`로 선별 편입.
- **TODO(칸 채우기)**: `type`(보통주/우선주/ETF/ETN)·`listing_date`·`delisted_date`는 소스 확정 필요.
  (`market_cap`·`shares_out`는 채워짐)

### 3-2. KRX Data Marketplace(openapi.krx.co.kr) — 시장 통계·일별매매 (`KRX_API_KEY`)
- 포털: https://openapi.krx.co.kr/ · **서비스별 이용신청 + 별도 인증키**
- 제공: 유가증권/코스닥 일별 매매정보, 시장 통계 등 (2010-01-04~)
- 채우는 곳: `state_module`(시장 국면·구조) 및 분석용. **정확한 서비스·필드는 신청 후 확정**(TODO).
- KIS와 일부 겹칠 수 있음(지수·투자자) → 겹치면 KIS 우선, KRX는 KIS에 없는 통계 보완.

> 왜 KRX 계열인가: 종목 종류·정확 시총·지수편입·상폐를 공식 제공 —
> DART(회사 단위)로는 우선주·ETF 누락·상폐 방치 문제가 있어 종목 뼈대에 부적합.

---

## 4. 폐지 결정 — 네이버 금융 HTML 크롤링

옛 오딘은 종목마스터·업종·시장수급·환율·미국지수를 네이버 금융 HTML 파싱으로 가져왔다.
**3.0에서는 폐지 방향**(공개 제품 약관 리스크 + 잦은 파손). 대체:
- 종목마스터·업종 → **KRX 공식 + DART**
- 시장수급 → KIS 투자자 API
- 환율·미국지수 → 별도 공식 소스 확정 필요(상태 모듈 '맥락' 층, **미정 — 나중 결정**)

---

## 5. 갱신 주기(안, 확정 아님)

| 데이터 | 주기 |
|--------|------|
| `dim_stock` 뼈대(KRX) | 일 1회(장 마감 후) |
| `dim_stock_profile`·업종(DART) | 주 1회~비정기 |
| 상태(관리/거래정지, KIS) | 일 1회 |
| 지수/시세 | 실시간~분(상태 모듈 요구에 따라) |
