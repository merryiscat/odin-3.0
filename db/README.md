# db — DB 스키마 (표 설계)

> 모듈끼리는 **DB(표)로만** 대화한다. 여기는 그 표들의 설계(스키마)를 모아두는 곳.

## 하는 일
Supabase(새 3.0 프로젝트, 빈 통)에 만들 **표 정의 SQL**을 둔다.
표를 바꾸면 여기 SQL을 고치고 기록을 남긴다.

## 3.0 첫 씨앗 표 (먼저 만들 것)
- `dim_stock`(종목 마스터), `raw_index_ohlc`(지수 일봉), 상태 국면 표 — 이 3개부터 새로 짓는다.
  (옛 오딘 표를 어떻게 참고할지 정리한 내부 지도는 로컬 전용으로 따로 둔다.)

## 지금까지 정한 표 (브레인 §5 참조)
- `dim_stock` — 종목 마스터(가볍게). 상폐 종목도 지우지 말 것(생존편향 방지).
  - 구조 속성만 담는다: `type`(보통주/우선주/리츠/스팩) · `is_holding`(지주 여부) · `sector_l1/l2` · 시총 등.
  - **테마는 여기 안 넣는다**(뉴스로 계속 변하는 다중값 → dim 원칙 위반).
- `dim_stock_profile` — 회사 정보(무거운 텍스트, DART 기업개황).
- `dim_sector` — 업종 번호(KSIC) → 큰 묶음 이름 번역표. 채우기 로직은 `seed_dim_sector.sql`.

## 실시간 판별기 표 5개 (2026-09-12 설계 — 상세는 `state_module/README.md`)
- `raw_market_snapshot` — 1분 시세 원천. 지수·바스켓 종목·예상지수·미선물을 kind로 구분해 한 표에.
- `dim_basket` — 실시간 수집 대상 명부. leader(업종 대장주, 폭·역행 측정) / proxy(시총 상위, 장외 지수 대용).
- `state_params` — 공용 판정 파라미터. bg_daily(아침 배경) / intraday(1분). 모든 예측모델이 같은 입력.
- `state_market_temp` — 판정 output. 사다리(대공황~과열)+꼬리표, model_id로 예측모델 구분, 매분 append.
- `dim_model` — 마트 모델 명부. 예측·매매·기준선(무작위, 내일=오늘) 모두 등록.

## 매매 리스트 표 (2026-09-17 설계 — 상세는 `list_module/README.md` 예정, 계획은 브레인 §9)
- `raw_stock_ohlc` — **전 종목 일봉**(생성 완료 2026-09-17). 당일은 15:40 KIS 묶음 스냅샷(kis), 과거는 공공데이터
  백필(krx). trade_value는 **원**(지수 표는 백만원). 상한가 값(upper_limit)이 있어 상한가 마감을 정확히 판정.
- 예정: `raw_rank_snapshot`(순위 조회 축적 — 거래량·등락률·외인/기관 순매수, 10분+15:35) ·
  `stock_params`(공용 종목 파라미터, bg_daily/intraday) · `pick_list`(리스트 모델 output, list_date·phase·reasons·warn) ·
  `dim_model.kind`에 `pick` 추가.

### 예정 표
- **테마**(뉴스 단계에서): `dim_theme`(테마 목록) + `raw_stock_theme`(종목↔테마·출처·시점·점수).
  themes를 dim_stock 칸으로 두지 않기로 결정(2026-08-31) — 계속 수집·변하는 데이터라 별도 표.
- `raw_index_ohlc` 지수 일봉(배경·백분위 재료), `order_intent` 주문 의도 표, 체결 기록 표.

## 관리 도구 (직접 만들지 말 것)
- 표 보기/편집 → **Supabase Studio**.
- 쿼리 없이 조인·대시보드 → **Metabase**(도커, 읽기전용 연결).
- 라이브(주문 굴리는) 표는 손으로 고치면 위험, 기록성 표는 맘껏.
