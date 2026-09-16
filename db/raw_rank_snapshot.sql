-- ============================================================================
-- raw_rank_snapshot — KIS 순위 조회 축적 (오딘 3.0)
--   "지금 시장에서 돈이 몰리는 종목"을 전 종목 스캔 없이 싸게 보는 창. KIS 순위 TR은 상위 30건만 주므로
--   10분마다 받아 쌓아 두면 (a) 장중 매매 리스트 추가 재료 (b) 외인/기관 수급의 일별 이력이 된다.
--   옛 오딘 교훈: 종목별 투자자 일별(FHKST01010900)은 최근 30일 창만 주고 과거 백필이 안 된다 → 순위 결과를
--   매일 15:35에 append 축적하는 것이 수급 이력을 남기는 유일한 방법. 휴장일엔 전 거래일 순위가 그대로 오므로
--   휴장일 저장 금지(연속일 부풀림 사고 재발 방지).
--   kind(시장별 상위 30):
--     volume / vol_inc / amount   — 거래량 순위 TR(FHPST01710000)의 정렬 기준 3가지(거래량·거래증가율·거래대금)
--     gainer / loser              — 등락률 순위 TR(FHPST01700000), 전일 종가 대비(저가 대비 아님)
--     foreign_buy / foreign_sell / inst_buy / inst_sell — 외인·기관 순매수/순매도 순위 TR(FHPTJ04400000), 금액 기준
--   원천이라 상품 유형(ETF·스팩 등)을 거르지 않는다 — 거르는 건 리스트 유니버스 단계.
-- ============================================================================

create table if not exists raw_rank_snapshot (
    id            bigint generated always as identity primary key,
    ts            timestamptz not null,             -- 조회한 분(초 버림, 한국시간)
    market        text not null,                    -- KR-KOSPI / KR-KOSDAQ (시장별로 따로 조회)
    kind          text not null,                    -- 위 kind 목록
    rank_no       int not null,                     -- 1 = 1위
    stock_code    text not null,
    name          text,                             -- 종목명(조회 시점 표기 — 확인용)
    price         numeric,                          -- 현재가(원)
    change_pct    numeric,                          -- 전일 대비 %
    acc_volume    bigint,                           -- 당일 누적 거래량
    acc_amount    bigint,                           -- 당일 누적 거래대금(원)
    net_volume    bigint,                           -- 순매수 수량 (외인/기관 kind만)
    net_amount    bigint,                           -- 순매수 금액(원 — KIS 백만원 값을 원으로 환산) (외인/기관 kind만)
    source        text not null default 'kis',
    created_at    timestamptz not null default now()
);

-- ── 인덱스 ──────────────────────────────────────────────────────────────
-- "이 종류의 최근 순위" — 장중 리스트 추가가 거는 조회
create index if not exists idx_rrs_kind_ts on raw_rank_snapshot (market, kind, ts);
-- "이 종목이 최근 며칠 순위에 들었나" — 연속일(수급·거래량) 계산
create index if not exists idx_rrs_code_ts on raw_rank_snapshot (stock_code, ts);

-- ── 표/칸 설명(문서화) ──────────────────────────────────────────────────
comment on table  raw_rank_snapshot is 'KIS 순위 조회(거래량·등락률·외인/기관 순매수) 상위 30 축적. 10분 + 15:35. 휴장일 저장 금지. append만.';
comment on column raw_rank_snapshot.net_amount is '순매수 금액 원 단위(KIS 응답은 백만원 → ×1,000,000). 순매도 kind면 음수.';

-- ── 보안(RLS): 읽기 공개 + 쓰기 잠금 ────────────────────────────────────
alter table raw_rank_snapshot enable row level security;
create policy "raw_rank_snapshot 읽기 공개" on raw_rank_snapshot for select using (true);
