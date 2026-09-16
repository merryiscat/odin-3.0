-- ============================================================================
-- state_params — 공용 판정 파라미터 (오딘 3.0, 마트 3단 구조의 1단)
--   "판단의 재료"를 한 번만 계산해 저장하는 표. 모든 예측모델이 이 똑같은 입력을 읽는다.
--   왜 공용인가: ① 모델 간 성적 비교가 공정해짐("입력이 달랐다" 변명 불가)
--              ② 새 예측모델이 과거 파라미터 기록으로 바로 개발·검증 가능.
--   scope 두 종류:
--     · bg_daily  — 배경. 아침 1회, 어제까지의 일봉으로 계산(장중엔 안 변하는 고정값).
--                   실제 키(params-v1): as_of_date, prev_close, ma20, ma_gap_pct, ma_slope_pct, chg5_pct,
--                       money_avg20, surge_threshold(대금 폭증 경계), surge_hist_n.
--     · intraday  — 장중. 1분마다 raw_market_snapshot에서 계산.
--                   실제 키(params-v1): index_price, index_chg(당일 등락률), acc_amount, money_ratio,
--                       money_hist_days, surge_threshold, breadth_n/up/down/u05/d05, sector_chg(업종별 대장주 등락률).
--                   (키 사전의 정본은 state_module/SPEC_market_state_v1.md — 2026-09-17 실제 값에 맞춰 정정)
--   params를 jsonb(이름 붙은 값 묶음)로 두는 이유: 파라미터 목록이 아직 진화 중
--   (분봉 이력이 쌓이면 "30분 낙폭" 같은 속도 지표 추가 예정) — 칼럼 고정은 v2에서.
--   규칙: append(추가)만 — 덮어쓰기 금지. 계산식이 바뀌면 calc_ver를 올린다.
-- ============================================================================

create table if not exists state_params (
    id            bigint generated always as identity primary key,

    ts            timestamptz not null,             -- 계산 시각
    market        text not null,                    -- 8시장 코드 (KR-KOSPI 등)
    scope         text not null,                    -- bg_daily(아침 1회 배경) / intraday(1분 장중)

    -- 파라미터 값 묶음. 키 이름은 state_module/README.md "판정 파라미터" 절이 사전.
    -- 정규화값(백분위)과 원값을 같이 담는다(검증용).
    params        jsonb not null,

    calc_ver      text not null,                    -- 계산식 버전 (예: params-v1)
    created_at    timestamptz not null default now()
);

-- ── 인덱스 ──────────────────────────────────────────────────────────────
-- "이 시장의 최신 배경/장중 파라미터" — 예측모델이 매분 거는 조회
create index if not exists idx_state_params_lookup on state_params (market, scope, ts);

-- ── 표/칸 설명(문서화) ──────────────────────────────────────────────────
comment on table  state_params is '공용 판정 파라미터. 한 번 계산해 저장, 모든 예측모델이 같은 입력을 읽음(공정 비교·재검증). append만.';
comment on column state_params.scope  is 'bg_daily=아침 1회 일봉 배경(장중 고정) / intraday=1분 장중 값.';
comment on column state_params.params is '이름 붙은 파라미터 묶음(jsonb). 키 사전은 state_module/README.md. 원값+백분위 동봉.';

-- ── 보안(RLS): 읽기 공개 + 쓰기 잠금 ────────────────────────────────────
alter table state_params enable row level security;
create policy "state_params 읽기 공개" on state_params for select using (true);
