-- ============================================================================
-- pick_list — 매매 리스트 output (오딘 3.0, 마트 3단 구조의 2단: 리스트 모델별)
--   "그날 볼 종목" 한 종목 한 줄. state_market_temp와 같은 문법: model_id로 모델을 구분하고 append만 한다.
--   phase:
--     · open     — 밤 배치가 만든 "다음 거래일 아침 확정 리스트"(list_date = 다음 거래일, as_of = 그날 15:30 확정 일봉).
--                  시장별 상한(30~50)은 여기만 적용.
--     · intraday — 장중 10분 순위로 덧붙인 종목(list_date = 오늘, as_of = 조회 분). 상한 없음, 삭제 없음.
--   최신 줄 규칙(계약): 같은 (list_date, market, model_id, stock_code)에서 as_of가 가장 늦은 줄이 현재값.
--     장중 줄은 아침 사유를 포함해 reasons를 다시 쓴다 → 읽는 쪽은 줄을 합칠 필요 없음.
--   as_of는 항상 **데이터 시각**(내일 날짜를 찍지 않는다 — 재현 때 "그날 종가까지 본 것"으로 오독 방지).
--   reasons(왜 뽑혔나 태그, 배열): money_surge / top_gainer / top_loser / limit_up / new_high / foreign_buy / inst_buy /
--     both_buy / sector_counter / oversold / intraday_add …  warn(경고 태그, 배열): blowoff / thin / untradable
--   리스트는 시장 온도를 조건으로 뽑히지 않는다(온도로 거르는 건 매매모델 몫 — 옛 오딘 "국면으로 종목 고르면 망함" 교훈).
-- ============================================================================

create table if not exists pick_list (
    id            bigint generated always as identity primary key,
    ts            timestamptz not null,             -- 쓴 시각
    as_of         timestamptz not null,             -- 판정에 쓴 데이터의 시각
    list_date     date not null,                    -- 이 줄이 "볼 날"(거래일)
    market        text not null,                    -- KR-KOSPI / KR-KOSDAQ
    model_id      text not null,                    -- dim_model(kind=pick) 등록값
    phase         text not null,                    -- open / intraday
    basis         text not null,                    -- eod(확정 일봉) / live(장중 순위)
    stock_code    text not null,
    rank_no       int,                              -- 아침 확정 순위(장중 추가는 아침 뒤 번호)
    score         numeric,                          -- 맞은 렌즈 수·순위 가중 점수(모델이 정의, metrics에 계산 근거)
    reasons       jsonb not null,                   -- 태그 배열
    warn          jsonb,                            -- 경고 태그 배열
    label_kr      text,                             -- 사람용 한 줄(기계 매칭 금지)
    metrics       jsonb,                            -- 근거 숫자(파라미터 값·렌즈 설정 lens_cfg)
    rule_ver      text not null,
    created_at    timestamptz not null default now()
);

-- ── 인덱스 ──────────────────────────────────────────────────────────────
-- "오늘 이 모델의 이 시장 리스트" — 매매모델·화면이 거는 조회
create index if not exists idx_pl_lookup on pick_list (list_date, market, model_id, stock_code, as_of);
-- "이 종목이 언제 리스트에 들었나" — 포착률 채점
create index if not exists idx_pl_code on pick_list (stock_code, list_date);

comment on table pick_list is '매매 리스트(그날 볼 종목) output, 리스트 모델별(model_id). phase=open 아침 확정/intraday 장중 추가. append만, 최신 줄 규칙은 as_of.';

-- ── 보안(RLS): 읽기 공개 + 쓰기 잠금 ────────────────────────────────────
alter table pick_list enable row level security;
create policy "pick_list 읽기 공개" on pick_list for select using (true);
