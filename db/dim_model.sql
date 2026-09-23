-- ============================================================================
-- dim_model — 마트 모델 명부 (오딘 3.0)
--   마트에 진열되는 모델의 목록. 예측모델(상태 판단)과 매매모델(주문 의도) 둘 다 여기 등록.
--   측정 기준선(무작위·"내일=오늘")도 그냥 모델로 등록한다 — 순위표에서 기준선을
--   못 이기는 모델은 "운"이라는 게 바로 드러난다(측정 원칙 §6).
--
--   씨앗 줄(Supabase 프로젝트 생성 후 넣을 것):
--     ('rule-temp-rt-v1',    'state_pred', '규칙 기반 실시간 시장 온도', 'v1', 'dev')
--     ('baseline-random-v1', 'state_pred', '기준선: 무작위 판정',        'v1', 'dev')
--     ('baseline-carry-v1',  'state_pred', '기준선: 내일=오늘',          'v1', 'dev')
--   매매 리스트 모델(kind=pick, 2026-09-17 추가 — 등록 완료):
--     ('rule-pick-v1',            'pick', '규칙 렌즈 합집합 매매 리스트', 'v1', 'dev')
--     ('baseline-random-pick-v1', 'pick', '기준선: 무작위 선정',          'v1', 'dev')
--     ('baseline-topcap-pick-v1', 'pick', '기준선: 시총 상위',            'v1', 'dev')
--     ('baseline-carry-pick-v1',  'pick', '기준선: 어제 리스트 그대로',    'v1', 'dev')
--   매매모델(kind=trade, 2026-09-23 추가 — 등록 완료):
--     ('rule-trade-v1',           'trade', '규칙 기반 매매모델 1호(가상 계좌)', 'v1', 'dev')
-- ============================================================================

create table if not exists dim_model (
    model_id      text primary key,                 -- 모델 고유 이름 (예: rule-temp-rt-v1)
    kind          text not null,                    -- state_pred(예측모델) / pick(매매 리스트 모델) / trade(매매모델)
    name          text not null,                    -- 사람용 이름
    version       text not null,                    -- 버전 (model_id에도 있지만 조회 편의로 중복 저장)
    status        text not null default 'dev',      -- dev(개발) / live(가동) / retired(퇴역)
    is_public     boolean not null default false,   -- 마트 공개 여부 (프리미엄 모델은 false)
    description   text,                             -- 뭘 하는 모델인가(쉬운 말로)
    created_at    timestamptz not null default now()
);

-- ── 표/칸 설명(문서화) ──────────────────────────────────────────────────
comment on table  dim_model is '마트 모델 명부. 예측모델·매매모델·기준선 모두 등록. 매매모델은 구독할 예측모델 model_id를 자기 설정에 가짐.';
comment on column dim_model.kind   is 'state_pred=예측모델(state_market_temp에 씀) / trade=매매모델(order_intent에 씀).';
comment on column dim_model.status is 'dev=개발 중 / live=실제 가동 / retired=퇴역. 퇴역해도 줄은 지우지 않는다(과거 기록의 주인).';

-- ── 보안(RLS): 읽기 공개 + 쓰기 잠금 ────────────────────────────────────
alter table dim_model enable row level security;
create policy "dim_model 읽기 공개" on dim_model for select using (true);
