-- ============================================================================
-- dim_stock — 종목 마스터 (오딘 3.0)
--   "무엇이 상장돼 거래되는가"의 단일 출처. 자주 조회되니 가볍게 유지.
--   출처 분담(브레인 §5):
--     · 뼈대(코드·이름·시장·종류·시총·상장/상폐·지수편입) = KRX 공식 API
--     · 업종(sector_l1/l2)                               = DART(corpCode→기업개황) → dim_sector 번역
--     · 실시간 상태(status: 관리/거래정지)                = KIS inquire-price로 매일 갱신
--     · 테마                                            = 이 표에 두지 않음. 별도 테이블(뉴스 단계에서)
--   규칙: 모듈끼리 import 금지, DB(표)로만 대화. 이 표는 data_pipeline이 쓰고 나머지가 읽는다.
-- ============================================================================

create table if not exists dim_stock (
    -- ── 뼈대 (KRX) ──────────────────────────────────────────────────────
    stock_code        text primary key,            -- 종목코드 6자리 (우선주도 고유코드, 예: 005935)
    name              text not null,               -- 종목명
    market            text,                         -- 시장: KOSPI / KOSDAQ / KONEX

    -- 종목 종류 — 유니버스 필터의 핵심(브레인 §5). 보통주/우선주/ETF/ETN/리츠/스팩 등.
    -- 값이 계속 늘 수 있어 CHECK로 가두지 않고 자유 텍스트 + 주석으로 관리한다.
    type              text,

    -- ── 업종 (DART 기업개황 → dim_sector 번역표) ────────────────────────
    -- 잘 안 변함 → 고정 칸. 국면·통계 집계는 sector_l1(대분류) 기준으로.
    sector_l1         text,                         -- 업종 대분류 (예: 반도체)
    sector_l2         text,                         -- 업종 중분류

    -- ── 대표지수 편입 (KRX) ─────────────────────────────────────────────
    is_kospi200       boolean default false,
    is_kosdaq150      boolean default false,

    -- 지주회사 여부(구조 속성). 지주도 보통주라 type과 별개.
    is_holding        boolean default false,
    -- (테마는 이 표에 두지 않는다 — 뉴스로 계속 수집·변하는 다중값이라 dim(안 변하는 목록) 원칙 위반.
    --  나중에 dim_theme(테마 목록) + raw_stock_theme(종목↔테마·출처·시점) 별도 테이블로 설계.)

    -- ── 규모 (KRX) ──────────────────────────────────────────────────────
    -- 실제 시가총액을 원(KRW) 단위로 저장. 대/중/소 등급은 저장하지 말고 필요할 때 계산(브레인 §5).
    market_cap        bigint,                       -- 시가총액 (원)
    shares_out        bigint,                       -- 상장주식수 (시총 = 가격 × 주식수 재계산용)

    -- ── 상장 상태 (KRX + KIS) ───────────────────────────────────────────
    status            text default '정상',          -- 정상 / 관리 / 투자위험 / 거래정지 (KIS로 갱신)
    listing_date      date,                         -- 상장일
    -- 상폐일. 비어 있으면 '상장중'. ⚠️ 상폐 종목도 절대 지우지 말 것 = 백테스트 생존편향 방지(브레인 §5).
    delisted_date     date,

    -- ── 메타 ────────────────────────────────────────────────────────────
    updated_at        timestamptz default now()     -- 갱신 시각
);

-- ── 인덱스 (자주 거는 필터 기준) ────────────────────────────────────────
-- 시장별 조회
create index if not exists idx_dim_stock_market   on dim_stock (market);
-- 업종 국면 집계는 sector_l1로
create index if not exists idx_dim_stock_sector_l1 on dim_stock (sector_l1);
-- '지금 살아있는 종목만' 빠르게(가장 흔한 조회) — 상폐 안 된 행만 부분 인덱스
create index if not exists idx_dim_stock_listed    on dim_stock (stock_code) where delisted_date is null;

-- ── 표/칸 설명(문서화) ──────────────────────────────────────────────────
comment on table  dim_stock is '종목 마스터. 뼈대=KRX, 업종=DART, 실시간상태=KIS. 상폐 종목도 보존(생존편향 방지).';
comment on column dim_stock.type          is '보통주/우선주/ETF/ETN/리츠/스팩 등. 유니버스 필터용. CHECK로 안 가둠(값 증가 대비).';
comment on column dim_stock.market_cap    is '실제 시가총액(원). 대/중/소 등급은 저장 말고 계산.';
comment on column dim_stock.status        is '정상/관리/투자위험/거래정지. KIS inquire-price(iscd_stat_cls_code)로 매일 갱신.';
comment on column dim_stock.delisted_date is '상폐일. NULL=상장중. 상폐 종목도 지우지 말 것(백테스트 생존편향 방지).';
comment on column dim_stock.is_holding    is '지주회사 여부. type과 별개 구조 속성(지주도 보통주).';

-- ── 보안(RLS): 읽기 공개 + 쓰기 잠금 ────────────────────────────────────
-- 시장정보라 읽기는 공개(브레인 §7-1 읽기전용 대시보드). 쓰기는 data_pipeline의
-- service_role 키만(service_role은 RLS 우회). anon/authenticated는 SELECT만 가능.
alter table dim_stock enable row level security;
create policy "dim_stock 읽기 공개" on dim_stock for select using (true);
