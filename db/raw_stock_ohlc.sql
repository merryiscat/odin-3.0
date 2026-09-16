-- ============================================================================
-- raw_stock_ohlc — 종목 일봉, 전 종목 (오딘 3.0)
--   매매 리스트(그날 볼 종목)의 밑재료: 대금 배율·등락률·신고가·상한가·이격 같은 종목 파라미터를
--   전 종목에 대해 계산하려면 "모든 종목의 하루 한 줄"이 필요하다.
--   출처 두 가지(source):
--     · kis — 장 마감 직후(15:40) KIS 묶음 시세(intstock-multprice, 30종목/콜, 약 96콜)로 그날 종가를
--             바로 받는다. 시·고·저·종·전일종가·상한가·하한가·거래량·거래대금(원)이 한 번에 온다.
--     · krx — 공공데이터포털 '금융위 주식시세정보'(getStockPriceInfo). KIS 호출 0건이라 과거 백필과
--             대조용. 단, 그날 시세가 다음 날 새벽에도 안 나와서(2026-09-17 04:40 실측) 당일분엔 못 쓴다.
--   단위 주의: trade_value는 **원**(지수 표 raw_index_ohlc.trade_value는 백만원 — 다르다).
--   규칙: 원천 확정치라 (date, stock_code) 재적재는 덮어씀(raw_index_ohlc와 같은 규칙).
--         백필(krx)은 이미 있는 kis 줄을 덮지 않는다(ignore-duplicates).
-- ============================================================================

create table if not exists raw_stock_ohlc (
    date          date not null,                    -- 거래일
    stock_code    text not null,                    -- 종목코드 6자리 (dim_stock 참조)
    market        text not null,                    -- KR-KOSPI / KR-KOSDAQ
    open          numeric,                          -- 시가
    high          numeric,                          -- 고가
    low           numeric,                          -- 저가
    close         numeric not null,                 -- 종가
    prev_close    numeric,                          -- 전일 종가 (등락률 재계산·검산용)
    change_pct    numeric,                          -- 전일 대비 등락률 %
    volume        bigint,                           -- 거래량
    trade_value   bigint,                           -- 거래대금(원) — 지수 표(백만원)와 단위가 다름
    upper_limit   numeric,                          -- 그날 상한가 값 (kis만) — close = upper_limit 이면 상한가 마감
    lower_limit   numeric,                          -- 그날 하한가 값 (kis만)
    source        text not null default 'kis',      -- kis(15:40 스냅샷) / krx(공공데이터 백필)
    created_at    timestamptz not null default now(),

    primary key (date, stock_code)                  -- 하루 한 종목 한 줄
);

-- ── 인덱스 ──────────────────────────────────────────────────────────────
-- "이 종목의 최근 N일" — 종목 파라미터(20일 평균·신고가) 계산이 거는 조회
create index if not exists idx_rso_code_date on raw_stock_ohlc (stock_code, date);
-- "이 날 이 시장 전부" — 렌즈 순위(시장별 상위 K)가 거는 조회
create index if not exists idx_rso_date_market on raw_stock_ohlc (date, market);

-- ── 표/칸 설명(문서화) ──────────────────────────────────────────────────
comment on table  raw_stock_ohlc is '전 종목 일봉. 매매 리스트 파라미터 재료. 당일은 15:40 KIS 묶음 스냅샷(kis), 과거는 공공데이터 백필(krx).';
comment on column raw_stock_ohlc.trade_value is '거래대금 원 단위 — raw_index_ohlc.trade_value(백만원)와 다르다.';
comment on column raw_stock_ohlc.upper_limit is '그날 상한가 값(kis만). 상한가 마감 판정은 close >= upper_limit (등락률 29.5% 추정보다 정확).';

-- ── 보안(RLS): 읽기 공개 + 쓰기 잠금 ────────────────────────────────────
alter table raw_stock_ohlc enable row level security;
create policy "raw_stock_ohlc 읽기 공개" on raw_stock_ohlc for select using (true);
