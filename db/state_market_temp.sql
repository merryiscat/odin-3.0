-- ============================================================================
-- state_market_temp — 시장 온도 판정 output (오딘 3.0, 마트 3단 구조의 2단)
--   예측모델이 뱉는 "표준 한 줄". 모든 매매모델이 이 규격 하나만 읽는다(모델 등급 구분 없음).
--   한 줄 = "어떤 모델이 · 언제 · 어느 시장을 · 뭐라고 판단했나"
--
--   상태는 **단일값 하나**(2026-09-13 확정 — 꼬리표 방식 폐기, 사용자 지시).
--   여러 조건이 동시에 맞으면 **우선순위가 높은 것 하나만** 뱉는다.
--
--   실시간(scope=rt) 어휘 v4 — 오늘의 단면 8개 (2026-09-13~14 확정. "~장" 국면 단어는 전부 주간으로):
--     1. crash     급락(긴급)         — 대장주 90%+가 각자 -0.5%↓ (시장별)
--     2. surge     급등               — 대장주 90%+가 각자 +0.5%↑ (급락과 대칭)
--     3. theme_up  테마주상승-{A..N}  — 하락 비율 70%+ 속 +1% 이상 역행 상승 업종 전부 나열
--     4. selloff   투매               — 하락(-0.3%↓) + 대금 폭증
--     5. fire      불장               — 상승(+0.3%↑) + 대금 폭증
--     6. up        상승               — 지수 당일 등락률 > +0.3%
--     7. down      하락               — 지수 당일 등락률 < -0.3%
--     8. flat      횡보               — 지수 당일 등락률 ±0.3% 안
--   대금 폭증 = (당일 누적 대금 ÷ 직전 20일 평균, 장중엔 같은 시각끼리 비교)이
--   그 시장의 최근 1년 비율 분포에서 상위 10% (매직넘버 대신 백분위 — 현재 실측 약 1.37배).
--   주간(scope=w) 어휘 — 국면·태도: depression(대공황)·uptrend(상승장)·downtrend(하락장)·
--   overheat(과열)·무드 등. 조건은 주간 설계 때(20일 이평 배경이 재료).
--   삭제: theme_down(불필요), rebound_try("실시간=상승 + 주간=하락장" 조합으로 파생).
--   기계(모델·프로그램)는 state_code·state_sectors만 매칭하고, 사람용 문구는 label_kr에.
--
--   규칙: append(추가)만, 덮어쓰기 금지 — 매분 저장(값이 안 변해도. 장중 이력 보존
--        + "그 시각에 시스템이 살아 있었다"는 증거). 상태 '전환'은 별도 표 없이
--        연속 줄 비교로 파생(필요하면 뷰로).
-- ============================================================================

create table if not exists state_market_temp (
    id            bigint generated always as identity primary key,

    -- ── 식별: 누가·언제·무엇을 ──────────────────────────────────────────
    ts            timestamptz not null,             -- 판정 시각
    as_of         timestamptz not null,             -- 판정에 쓴 데이터의 기준 시각(지연이면 ts와 벌어짐 — 신선도 확인용)
    market        text not null,                    -- 8시장 코드 (KR-KOSPI 등)
    model_id      text not null,                    -- 어느 예측모델의 판단인가 (dim_model 참조. 1호: rule-temp-rt-v1)
    scope         text not null default 'rt',       -- 시야: rt(실시간). 주(w)·월(m)은 재설계 후 추가
    horizon       text not null default 'now',      -- 어느 시점에 대한 판단인가. 기본 모델은 now.
                                                    --   미래 예측모델은 d1(내일)·w1(다음 주) 등 — 교체·병행 대비 칸
    basis         text not null,                    -- 재료 등급: live(정규장 실시간) / pre(아침 프록시)
                                                    --   / after(저녁 프록시) / eod(확정 종가)

    -- ── 판단: 단일 상태값 ───────────────────────────────────────────────
    state_code    text not null,                    -- 위 어휘표의 코드 하나 (단일값 — 우선순위로 하나만)
    state_sectors jsonb,                            -- theme_up일 때 역행 상승 업종 목록(상승률 순 배열). 그 외 NULL
    state_conf    numeric,                          -- 확신도 0~1 (조건 충족이 얼마나 여유 있나 + 지속 시간)

    -- ── 폭(breadth): 업종 대장주 바스켓 집계 ────────────────────────────
    breadth_up    int,                              -- 상승한 대장주 수
    breadth_down  int,                              -- 하락한 대장주 수
    breadth_total int,                              -- 분모(집계된 대장주 수 — 거래정지 등으로 21 미만일 수 있어 저장)

    -- ── 사람용 문구 (GUI 표시용 — 기계는 여길 매칭하지 말 것) ───────────
    label_kr      text,                             -- 예: '테마주상승 — 반도체·전자'

    -- ── 근거·버전 (검증·채점용) ─────────────────────────────────────────
    metrics       jsonb,                            -- 판정 근거 스냅샷: 쓴 파라미터 값들 + 바스켓 구성 (왜 이렇게 판정했나의 증거)
    rule_ver      text not null,                    -- 판정 규칙 버전 (예: rt-rules-v1). 규칙 바꾸면 올림
    created_at    timestamptz not null default now()
);

-- ── 인덱스 ──────────────────────────────────────────────────────────────
-- "이 모델의 이 시장 최신 판정" — 매매모델이 매분 거는 조회
create index if not exists idx_smt_lookup on state_market_temp (market, model_id, scope, ts);

-- ── 표/칸 설명(문서화) ──────────────────────────────────────────────────
comment on table  state_market_temp is '시장 온도 판정 output(예측모델별, model_id로 구분). 상태는 단일값(우선순위로 하나만). append만·매분 저장(이력 보존).';
comment on column state_market_temp.state_code    is '실시간(rt): crash/theme_up/up/down/flat. 주간(w): depression/uptrend/downtrend/overheat 등(설계 예정). 모델은 이 코드로 매칭.';
comment on column state_market_temp.state_sectors is 'theme_up일 때 역행 상승한 업종(sector_l1) 목록 — 상승률 순 jsonb 배열. 그 외 NULL.';
comment on column state_market_temp.horizon      is 'now=현재 상태(기본 모델). 미래 예측모델은 d1·w1 등 — 같은 표에서 병행·채점하기 위한 칸.';
comment on column state_market_temp.label_kr     is '사람용 문구(GUI 표시). 문구는 언제든 다듬을 수 있으니 기계 매칭 금지 — 코드로만.';
comment on column state_market_temp.metrics      is '판정 근거 스냅샷(파라미터 값+바스켓 구성). "왜 이렇게 판정했나"의 증거 — 측정 원칙 §6.';

-- ── 보안(RLS): 읽기 공개 + 쓰기 잠금 ────────────────────────────────────
alter table state_market_temp enable row level security;
create policy "state_market_temp 읽기 공개" on state_market_temp for select using (true);
