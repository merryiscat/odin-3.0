-- ============================================================================
-- raw_market_snapshot — 실시간 시세 원천 (1분 단위, 오딘 3.0)
--   "그 순간 시장이 얼마였나"의 기록. 실시간 판별기의 원료가 전부 여기 쌓인다.
--   한 표에 여러 종류를 같이 담는다(kind로 구분):
--     · 공식 지수(코스피·코스닥 등)          = kind 'index'
--     · 바스켓 개별 종목(업종 대장주·프록시)  = kind 'stock'
--     · 동시호가 예상지수(08:30~09:00)       = kind 'expected_index'
--     · 미국 지수선물(저녁 프록시 재료)       = kind 'us_futures'
--   규칙: 외부 API는 data_pipeline만 호출한다. 이 표는 data_pipeline이 쓰고
--        상태 모듈이 읽는다. append(추가)만 — 덮어쓰기·삭제 금지(장중 이력 보존).
-- ============================================================================

create table if not exists raw_market_snapshot (
    id            bigint generated always as identity primary key,

    -- ── 언제·어디 ────────────────────────────────────────────────────────
    ts            timestamptz not null,             -- 수집 시각
    market        text not null,                    -- 시장 코드: KR-KOSPI / KR-KOSDAQ / US-SP500 /
                                                    --   US-NASDAQ / JP-N225 / JP-GROWTH / CN-SSE / CN-CHINEXT
    -- ── 무엇 ────────────────────────────────────────────────────────────
    kind          text not null,                    -- index / stock / expected_index / us_futures
    code          text not null,                    -- 지수코드(예: 0001) 또는 종목코드(예: 005930)

    -- ── 값 ──────────────────────────────────────────────────────────────
    price         numeric not null,                 -- 현재가(지수는 포인트, 종목은 원)
    change_pct    numeric,                          -- 전일 종가 대비 등락률(%). API가 주면 그대로, 없으면 계산해 채움
    acc_volume    bigint,                           -- 당일 누적 거래량(주면 저장, 없으면 NULL)
    acc_amount    bigint,                           -- 당일 누적 거래대금(KIS 원값 그대로: 종목=원, 지수=백만원. 주면 저장)

    -- ── 출처 메타 ────────────────────────────────────────────────────────
    source        text not null default 'kis',      -- 어느 API에서 왔나(kis 등)
    created_at    timestamptz not null default now()
);

-- ── 인덱스 (자주 거는 조회 기준) ────────────────────────────────────────
-- "이 시장의 최근 흐름" — 판별기가 매분 거는 조회
create index if not exists idx_rms_market_ts on raw_market_snapshot (market, ts);
-- "이 종목의 시계열" — 바스켓 종목별 흐름 조회
create index if not exists idx_rms_code_ts   on raw_market_snapshot (code, ts);

-- ── 표/칸 설명(문서화) ──────────────────────────────────────────────────
comment on table  raw_market_snapshot is '1분 시세 원천. 지수·바스켓 종목·예상지수·미선물을 kind로 구분해 한 표에. append만(이력 보존).';
comment on column raw_market_snapshot.kind       is 'index(공식 지수) / stock(바스켓 개별 종목) / expected_index(동시호가 예상) / us_futures(미 지수선물).';
comment on column raw_market_snapshot.change_pct is '전일 종가 대비 %. API가 안 주면 data_pipeline이 계산해 채움 — 읽는 쪽 계산 부담 제거.';
comment on column raw_market_snapshot.market     is '8시장 코드. 바스켓 종목 줄에는 그 종목이 속한 시장을 적는다.';

-- ── 보안(RLS): 읽기 공개 + 쓰기 잠금 ────────────────────────────────────
-- 시장정보라 읽기는 공개(브레인 §7-1 읽기전용 대시보드). 쓰기는 data_pipeline의
-- service_role 키만(service_role은 RLS 우회).
alter table raw_market_snapshot enable row level security;
create policy "raw_market_snapshot 읽기 공개" on raw_market_snapshot for select using (true);
