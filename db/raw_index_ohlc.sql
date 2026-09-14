-- ============================================================================
-- raw_index_ohlc — 지수 일봉 (오딘 3.0)
--   배경(20일 이동평균 위치·기울기)과 백분위 정규화(열 지표)의 원료.
--   최소 1년 백필 필요 — 첫 채움은 옛 오딘 DB의 index_daily_ohlc를 밤에 복사
--   (data_pipeline/copy_from_old.py). 이후는 data_pipeline이 매일 밤 KIS로 갱신.
--   칼럼은 옛 표와 호환되게 유지(복사 단순화). market 코드만 3.0 코드로 변환해 담는다
--   (KOSPI → KR-KOSPI, KOSDAQ → KR-KOSDAQ).
-- ============================================================================

create table if not exists raw_index_ohlc (
    date          date not null,                    -- 거래일
    market        text not null,                    -- 8시장 코드: KR-KOSPI / KR-KOSDAQ / US-SP500 /
                                                    --   US-NASDAQ / JP-N225 / JP-GROWTH / CN-SSE / CN-CHINEXT
    open          numeric,                          -- 시가
    high          numeric,                          -- 고가 (일중 진폭 계산용)
    low           numeric,                          -- 저가 ("장중 -2.5% 터치" 검증용)
    close         numeric not null,                 -- 종가
    volume        bigint,                           -- 거래량
    trade_value   bigint,                           -- 거래대금(백만원 — KIS 지수 원값 단위. 2026-09-14 실측 정정, 옛 주석 "원"은 오류) — "돈 몰림"의 재료
    source        text not null default 'kis',      -- 출처: kis / old_odin(복사) 등
    created_at    timestamptz not null default now(),

    primary key (date, market)                      -- 하루 한 시장 한 줄 (재적재는 덮어씀 — 원천 확정치)
);

-- ── 인덱스 ──────────────────────────────────────────────────────────────
-- "이 시장의 최근 N일" — 배경·백분위 계산이 매일 거는 조회
create index if not exists idx_rio_market_date on raw_index_ohlc (market, date);

-- ── 표/칸 설명(문서화) ──────────────────────────────────────────────────
comment on table  raw_index_ohlc is '지수 일봉(8시장). 배경·백분위 재료. 첫 채움은 옛 DB 복사, 이후 매일 밤 KIS 갱신.';
comment on column raw_index_ohlc.market is '3.0 시장 코드(KR-KOSPI 등). 옛 표의 KOSPI/KOSDAQ은 복사 시 변환.';
comment on column raw_index_ohlc.low    is '저가 — "장중 -2.5% 터치했나" 같은 급락 규칙의 과거 검증에 필수.';

-- ── 보안(RLS): 읽기 공개 + 쓰기 잠금 ────────────────────────────────────
alter table raw_index_ohlc enable row level security;
create policy "raw_index_ohlc 읽기 공개" on raw_index_ohlc for select using (true);
