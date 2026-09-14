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

## 스크립트
| 파일 | 언제 | 하는 일 |
|---|---|---|
| `build_basket.py` | 아침 1회(장 전) | `dim_stock` → `dim_basket` 명부(시장별 업종 대장주 21개 + 시총 상위 30개) |
| `collect_rt.py` | 장중(09:00~15:30) | KIS 1분 시세 → `raw_market_snapshot`. 지수 2 + 대장주 42종목, 분당 4콜(하루 ~1,560콜) |
| `copy_from_old.py` | 밤(장 마감 후) | 옛 오딘 DB → 3.0: 지수 일봉·지수 분봉 복사 (`data_pipeline/.env`의 OLD_/NEW_ 키) |
| `ingest_krx_stock.py` 등 | 필요 시 | 종목 마스터·DART 회사정보 적재 |

- **3.0 오픈 전까지 원칙(2026-09-14 사용자 결정)**: 옛 오딘에 이미 있는 데이터는 **옛 DB 덤프(밤 복사)**로 가져온다.
  옛 오딘에 없는 1분 대장주 시세만 3.0이 KIS로 직접 받되, **옛 오딘과 같은 앱키로 동시에 돌린다**
  (호출량이 작고 사용자가 한 명이라 문제 생기면 그때 분리). KIS 토큰은 6시간 안 재발급 시 같은 값이라 옛 오딘 토큰을 끊지 않는다.
- 지수 대금 단위: KIS 지수 `acml_tr_pbmn`과 `raw_index_ohlc.trade_value`는 **백만원**(옛 오딘 일봉 실측: 코스닥 하루 9,216,819 = 약 9.2조).
  종목 대금은 원 단위. `collect_rt.py`가 첫 분에 전일 대금을 찍어 대조한다.

## 공개/비공개
- 원천 수집 코드는 **공개 가능**(돈·주문 안 만짐). 단 API 키는 절대 커밋 금지.
