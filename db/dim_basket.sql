-- ============================================================================
-- dim_basket — 바스켓 구성 명부 (오딘 3.0)
--   "매분 시세를 받아올 종목이 누구냐"의 목록. 용도가 다른 두 벌을 basket_type으로 구분:
--     · leader — 업종(sector_l1)별 시가총액 1위 = 업종 대장주 (~21개 × 시장).
--                용도: 폭(몇 개 업종이 오르나/내리나)과 역행 업종(테마주상승-반도체) 측정.
--     · proxy  — 시가총액 상위 20~30개 (시장별).
--                용도: 지수가 안 나오는 아침·저녁 시간대에 지수 흉내(시총 가중 등락률).
--   선정 규칙(2026-09-12): 시장별로 각자 선정 — 그 시장 상장 종목 중에서 뽑는다.
--     그 시장에 없는 업종은 빠질 수 있음 → 판별기는 분모(breadth_total)를 따로 저장.
--   갱신: 매일 아침 dim_stock의 시총으로 다시 뽑아 덮어씀(이 표는 "현재 명부").
--     판정 순간에 실제로 쓴 구성은 state_market_temp.metrics에 스냅샷으로 남는다(재현성).
-- ============================================================================

create table if not exists dim_basket (
    basket_type   text not null,                    -- leader(업종 대장주) / proxy(시총 상위)
    market        text not null,                    -- KR-KOSPI / KR-KOSDAQ (해외 확장 시 추가)
    stock_code    text not null,                    -- 종목코드 (dim_stock 참조)
    sector_l1     text,                             -- 업종 대분류 — leader만 채움(어느 업종의 대장인가)
    rank_no       int,                              -- proxy용 시총 순위(1=최대). leader는 NULL
    as_of         date not null,                    -- 이 명부를 뽑은 날짜
    updated_at    timestamptz not null default now(),

    primary key (basket_type, market, stock_code)
);

-- ── 인덱스 ──────────────────────────────────────────────────────────────
-- "이 시장의 leader 바스켓 전체" — 판별기·수집기가 매번 거는 조회
create index if not exists idx_dim_basket_lookup on dim_basket (basket_type, market);

-- ── 표/칸 설명(문서화) ──────────────────────────────────────────────────
comment on table  dim_basket is '실시간 수집 대상 바스켓 명부. leader=업종 대장주(폭·역행 측정), proxy=시총 상위(장외시간 지수 대용). 매일 갱신.';
comment on column dim_basket.sector_l1 is 'leader만 채움 — 이 종목이 어느 업종의 대장주인가. proxy는 NULL.';
comment on column dim_basket.as_of     is '명부 선정일. 판정 순간의 실제 구성 스냅샷은 state_market_temp.metrics에 별도 보존.';

-- ── 보안(RLS): 읽기 공개 + 쓰기 잠금 ────────────────────────────────────
alter table dim_basket enable row level security;
create policy "dim_basket 읽기 공개" on dim_basket for select using (true);
