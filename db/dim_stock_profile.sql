-- ============================================================================
-- dim_stock_profile — 회사 정보 (오딘 3.0)
--   dim_stock이 '거래되는 종목'의 가벼운 뼈대라면, 여기는 '그 회사'의 무거운 정보(별도 표).
--   출처: DART. 흐름 = corpCode.xml(연결키) → company.json(개황) → (나중)사업보고서(overview).
--   ★ corp_code = DART가 회사를 식별하는 열쇠. 이게 있어야 재무·공시·개황을 부를 수 있다.
--   연결: stock_code로 dim_stock과 이어짐(느슨한 링크 — 하드 FK는 안 건다. 적재 순서 자유롭게).
-- ============================================================================

create table if not exists dim_stock_profile (
    stock_code     text primary key,          -- 종목코드 (dim_stock과 같은 값으로 연결)
    corp_code      text,                        -- ★ DART 고유 회사코드(8자리). 모든 DART 호출의 열쇠
    corp_name      text,                        -- 회사명(DART 표기)
    corp_name_eng  text,                        -- 영문 회사명
    ceo            text,                        -- 대표자명
    est_date       date,                        -- 설립일
    fiscal_month   text,                        -- 결산월 (예: '12' = 12월 결산)
    induty_code    text,                        -- 업종번호 → dim_sector 번역표의 입력값
    address        text,                        -- 주소
    homepage       text,                        -- 홈페이지 URL
    overview       text,                        -- 사업 서술 텍스트 (나중에 사업보고서에서 채움)
    updated_at     timestamptz default now()
);

-- corp_code로 자주 역조회(DART 호출 전 열쇠 찾기)
create index if not exists idx_dim_stock_profile_corp_code on dim_stock_profile (corp_code);
-- 업종번호로 집계/번역
create index if not exists idx_dim_stock_profile_induty    on dim_stock_profile (induty_code);

-- 보안(RLS): 읽기 공개 + 쓰기 잠금 (공개된 회사정보라 읽기 공개, 쓰기는 data_pipeline만)
alter table dim_stock_profile enable row level security;
create policy "dim_stock_profile 읽기 공개" on dim_stock_profile for select using (true);

comment on table  dim_stock_profile is '회사 정보(DART). corp_code=모든 DART 호출의 열쇠. stock_code로 dim_stock과 연결.';
comment on column dim_stock_profile.corp_code   is 'DART 고유 회사코드(8자리). corpCode.xml에서 받음. 재무/공시/개황 호출의 필수 열쇠.';
comment on column dim_stock_profile.induty_code is 'DART 업종번호. dim_sector로 큰 묶음 이름(sector_l1)으로 번역.';
comment on column dim_stock_profile.overview    is '사업 서술 텍스트. 나중에 사업보고서에서 채움.';
