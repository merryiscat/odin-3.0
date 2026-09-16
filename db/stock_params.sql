-- ============================================================================
-- stock_params — 공용 종목 파라미터 (오딘 3.0, 매매 리스트 3단 구조의 1단)
--   state_params(시장)와 같은 원리를 종목에 적용: "그날 볼 종목을 고르는 재료"를 한 번만 계산해 저장하고,
--   모든 리스트 모델(pick)이 이 똑같은 입력을 읽는다 → 모델 간 비교가 공정하고, 새 모델이 과거 기록으로 바로 검증된다.
--   scope:
--     · bg_daily  — 밤 배치. 그날 확정 일봉(raw_stock_ohlc)까지로 종목마다 한 줄. 다음 거래일 아침 리스트의 재료.
--                   키(params-v1): prev_close, chg1_pct(당일 등락), chg5_pct, chg20_pct, money(당일 대금 원),
--                   money_avg20(직전 20일 평균 대금), money_ratio(당일÷평균), vol_ratio(거래량 배율),
--                   high20_gap_pct(20일 최고가 대비 %), high252_gap_pct(52주), low20_gap_pct, ma20_gap_pct(20일 이평 이격),
--                   range_pct(당일 진폭), limit_up(상한가 마감 여부), limit_up_streak(연속 상한가 일수),
--                   gap_open_pct(시가 갭), foreign_net5/inst_net5(순위 축적으로 본 최근 5일 순매수 합, 원 — 순위 밖이면 0),
--                   foreign_streak/inst_streak(순매수 순위 연속 일수), thin(20일 평균 대금 < 50억), tradable(종류·상태로 계좌 매수 가능)
--     · intraday  — 장중 10분. 순위(raw_rank_snapshot)에 든 종목만(전 종목 매 10분은 과함) — 그 순간의 등락·대금·순매수.
--   규칙: append만. 계산식이 바뀌면 calc_ver를 올린다. 없는 값은 null(추정해 채우지 않는다).
-- ============================================================================

create table if not exists stock_params (
    id            bigint generated always as identity primary key,
    ts            timestamptz not null,             -- 계산 시각
    as_of_date    date not null,                    -- 계산에 쓴 마지막 거래일 (bg_daily = 그날 일봉의 날짜)
    market        text not null,                    -- KR-KOSPI / KR-KOSDAQ
    stock_code    text not null,
    scope         text not null,                    -- bg_daily / intraday
    params        jsonb not null,                   -- 위 키 사전
    calc_ver      text not null,                    -- 계산식 버전 (예: params-v1)
    created_at    timestamptz not null default now()
);

-- ── 인덱스 ──────────────────────────────────────────────────────────────
-- "이 날 이 시장 전 종목 파라미터" — 리스트 모델이 매일 거는 조회
create index if not exists idx_sp_date_market on stock_params (as_of_date, market, scope);
-- "이 종목의 파라미터 이력"
create index if not exists idx_sp_code_date on stock_params (stock_code, as_of_date);

comment on table stock_params is '공용 종목 파라미터. bg_daily(밤, 전 종목)/intraday(장중, 순위 종목만). 모든 리스트 모델이 같은 입력을 읽는다. append만.';

-- ── 보안(RLS): 읽기 공개 + 쓰기 잠금 ────────────────────────────────────
alter table stock_params enable row level security;
create policy "stock_params 읽기 공개" on stock_params for select using (true);
