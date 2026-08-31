# data_pipeline — 데이터(원천) 수집·적재

> 데이터 흐름: **[보다]** → 판단 → 실행 → 보여주다. 모든 모듈의 밑재료.

## 하는 일
바깥 API(KIS·DART)에서 긁어서 → **원천 표(DB)에 담는다.** 3.0은 **새 Supabase 프로젝트**(빈 통)를 쓴다.
옛 오딘 라이브 DB는 안 건드린다.

## 이름 규칙 (표)
- 계속 쌓이는 것(시세·뉴스) → `raw_` (예: `raw_index_ohlc`, `raw_news`)
- 잘 안 변하는 참고 목록 → `dim_` (예: `dim_stock`, `dim_sector`)
- 한 줄: **쌓이면 `raw_`, 목록이면 `dim_`.**

## 데이터 소스 (API)
- **KIS(한국투자증권)**: https://apiportal.koreainvestment.com/ · 예제 https://github.com/koreainvestment/open-trading-api
  · 지수 일봉 `inquire-daily-indexchartprice`(FHKUP03500100), 지수 현재가 `inquire-index-price`(FHPUP02100000) 등.
- **DART(전자공시)**: https://opendart.fss.or.kr/ · 기업개황 `company.json`, 재무 `fnlttSinglAcntAll.json`,
  공시목록 `list.json`, 기업코드 `corpCode.xml`.

## 다음 할 일 (브레인 §9)
- `dim_sector`(업종 번역표) 첫 버전 — 79개 세부업종 → ~20개 큰묶음. DART `induty_code`로 "기타"(35%) 메움.

## 공개/비공개
- 원천 수집 코드는 **공개 가능**(돈·주문 안 만짐). 단 API 키는 절대 커밋 금지.
