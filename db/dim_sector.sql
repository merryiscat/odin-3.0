-- ============================================================================
-- dim_sector — 업종 번역표 (오딘 3.0)
--   DART 업종번호(induty_code, 예: '2612') → 우리가 쓰는 큰 묶음 이름(sector_l1, 예: '반도체').
--   목적(브레인 §5·§9): DART 세부업종(약 79개, 그중 35%가 '기타')을 큰 묶음 ~20개로 정리해
--     dim_stock.sector_l1을 안정적으로 채운다. 국면·통계 집계는 이 sector_l1 기준.
--   작은 참고표라 손으로 큐레이션 가능(브레인 §8: 기록성 표는 맘껏 수정).
-- ============================================================================

create table if not exists dim_sector (
    induty_code text primary key,   -- DART 업종번호 (예: '2612')
    induty_name text,               -- DART 세부업종명 (참고용, 예: '반도체 제조업')
    sector_l1   text,               -- ★ 큰 묶음 이름 (~20개, 예: '반도체') = 우리가 쓰는 대분류
    sector_l2   text,               -- 중분류(선택). 없으면 비움
    updated_at  timestamptz default now()
);

-- 큰 묶음별 역조회(이 대분류에 속한 업종번호들)
create index if not exists idx_dim_sector_l1 on dim_sector (sector_l1);

-- 보안(RLS): 읽기 공개 + 쓰기 잠금
alter table dim_sector enable row level security;
create policy "dim_sector 읽기 공개" on dim_sector for select using (true);

comment on table  dim_sector is 'DART 업종번호 → 큰 묶음 이름(sector_l1) 번역표. 세부업종 ~79개 → ~20개로 정리.';
comment on column dim_sector.induty_code is 'DART 업종번호. dim_stock_profile.induty_code와 짝.';
comment on column dim_sector.sector_l1   is '큰 묶음 이름(~20개). dim_stock.sector_l1을 이 값으로 채운다.';
