-- ============================================================================
-- dim_sector 채우기 + dim_stock 분류(sector_l1/l2, type, is_holding) 시드
--   순서가 중요하다. 위에서 아래로 한 번 돌리면 재현된다(각 단계는 덮어쓰기라 재실행 안전).
--   1) 업종번호→큰묶음  2) 대분류  3) 중분류  4) type/지주  5) 개별보정
--   6) 지주 재분류(상속→개별)  7) 중분류 오버라이드  8) 우선주 상속(맨 뒤)
--   ⚠️ v1 근사 — KSIC가 회사 성격을 못 담는 경우(지주·복합기업)가 있어 개별 보정을 얹는다.
-- ============================================================================

-- 1) dim_sector: KSIC 2자리(중분류) → 큰묶음(sector_l1) ~21개
insert into dim_sector (induty_code, sector_l1, updated_at)
select distinct p.induty_code,
  case substr(p.induty_code, 1, 2)
    when '26' then '반도체·전자'  when '28' then '전기·2차전지'
    when '29' then '기계·장비'    when '25' then '기계·장비'    when '33' then '기계·장비'
    when '30' then '자동차·부품'  when '31' then '조선·방산·중공업'  when '24' then '철강·금속'
    when '20' then '화학'         when '22' then '화학'         when '19' then '화학'
    when '21' then '제약·바이오'  when '70' then '제약·바이오'  when '73' then '제약·바이오'
    when '27' then '의료기기·정밀'
    when '10' then '음식료·담배'  when '11' then '음식료·담배'  when '12' then '음식료·담배'
    when '13' then '섬유·의류·종이' when '14' then '섬유·의류·종이' when '15' then '섬유·의류·종이'
    when '16' then '섬유·의류·종이' when '17' then '섬유·의류·종이' when '18' then '섬유·의류·종이'
    when '41' then '건설·엔지니어링' when '42' then '건설·엔지니어링'
    when '23' then '건설·엔지니어링' when '72' then '건설·엔지니어링'
    when '58' then '소프트웨어·IT' when '62' then '소프트웨어·IT' when '63' then '소프트웨어·IT'
    when '59' then '미디어·엔터·레저' when '60' then '미디어·엔터·레저'
    when '90' then '미디어·엔터·레저' when '91' then '미디어·엔터·레저'
    when '61' then '통신'
    when '46' then '유통·도소매'  when '47' then '유통·도소매'  when '45' then '유통·도소매'
    when '49' then '운송·물류'    when '50' then '운송·물류'    when '51' then '운송·물류'    when '52' then '운송·물류'
    when '64' then '금융·지주'    when '71' then '금융·지주'
    when '66' then '증권·보험'    when '65' then '증권·보험'
    when '35' then '유틸리티·환경' when '38' then '유틸리티·환경'
    else '기타·서비스'
  end, now()
from dim_stock_profile p
where p.induty_code is not null
on conflict (induty_code) do update set sector_l1 = excluded.sector_l1, updated_at = now();

-- 2) dim_stock.sector_l1 (대분류) — dim_sector에서
update dim_stock s set sector_l1 = ds.sector_l1
from dim_stock_profile p join dim_sector ds on ds.induty_code = p.induty_code
where s.stock_code = p.stock_code;

-- 3) dim_stock.sector_l2 (중분류) — KSIC 3자리(소분류). 매핑 없으면 대분류로 폴백
update dim_stock s set sector_l2 = case substr(p.induty_code,1,3)
  when '261' then '반도체' when '262' then '전자부품' when '263' then '컴퓨터·주변기기'
  when '264' then '가전·전자기기' when '265' then '영상·음향·카메라'
  when '292' then '특수목적기계' when '291' then '일반기계·엔진' when '259' then '금속가공'
  when '251' then '구조용금속' when '339' then '기타제조'
  when '204' then '화장품·생활화학' when '201' then '기초소재화학' when '222' then '플라스틱·포장'
  when '205' then '화섬소재' when '203' then '비료·농약' when '221' then '타이어·고무' when '202' then '정밀화학'
  when '212' then '제약(완제)' when '701' then '바이오·신약개발' when '211' then '바이오의약품'
  when '213' then '바이오·의료용품' when '739' then '시험·검사'
  when '582' then '소프트웨어·게임' when '620' then 'SI·IT서비스' when '639' then '정보서비스'
  when '631' then '포털·플랫폼' when '581' then '출판·콘텐츠'
  when '271' then '의료기기' when '272' then '정밀계측기' when '273' then '광학·검사기기'
  when '303' then '자동차부품'
  when '281' then '전력·전기기기' when '282' then '2차전지' when '289' then '기타전기장비'
  when '285' then '전기·가정기기' when '283' then '전선·케이블'
  when '241' then '철강' when '242' then '비철금속'
  when '661' then '증권' when '651' then '보험'
  when '649' then '은행·금융지주' when '642' then '인프라·리츠펀드' when '715' then '지주·경영관리' when '713' then '광고'
  when '411' then '건축' when '412' then '토목' when '721' then '엔지니어링'
  when '233' then '시멘트·레미콘' when '239' then '비금속광물' when '423' then '플랜트·설비'
  when '591' then '영화·영상' when '602' then '방송' when '901' then '공연·기획' when '592' then '음반·엔터'
  when '141' then '의류' when '171' then '제지' when '172' then '골판지' when '132' then '직물·섬유'
  when '493' then '육상물류' when '529' then '운송지원' when '501' then '해운' when '511' then '항공'
  when '467' then '전문도매·상사' when '465' then '전자·부품유통' when '468' then '생활용품유통'
  when '464' then '가정용품도매' when '471' then '종합소매' when '474' then '패션소매'
  when '463' then '음식료유통' when '461' then '종합상사'
  when '352' then '가스·에너지공급'
  when '108' then '기타식품' when '111' then '주류' when '109' then '사료' when '105' then '곡물가공'
  when '101' then '육류가공' when '102' then '수산가공'
  when '311' then '조선' when '313' then '항공·방산'
  when '612' then '통신서비스'
  when '681' then '부동산·리츠' when '320' then '가구' when '759' then '사업지원서비스'
  when '752' then '여행·레저' when '855' then '교육'
  else s.sector_l1 end
from dim_stock_profile p
where s.stock_code = p.stock_code and p.induty_code is not null;

-- 4) 구조 속성: type(종류) + is_holding(지주)
--    ⚠️ 테마 아님. 진짜 시장테마(HBM·AI…)는 나중에 별도 테이블(dim_theme+raw_stock_theme).
update dim_stock set type='보통주';
update dim_stock set type='우선주'
  where not exists (select 1 from dim_stock_profile p where p.stock_code=dim_stock.stock_code);
update dim_stock set type='리츠' where name like '%리츠%' and type='보통주';
update dim_stock set type='스팩' where name like '%스팩%' and type='보통주';
update dim_stock s set is_holding=true
from dim_stock_profile p
where s.stock_code=p.stock_code
  and (p.induty_code='64992' or s.name like '%홀딩스' or s.name like '%지주');

-- 5) 대분류 개별 오분류 보정(비지주): 반도체 장비/테스트, 조선지주, 광고사
update dim_stock set sector_l1='조선·방산·중공업' where stock_code='009540';        -- HD한국조선해양
update dim_stock set sector_l1='반도체·전자' where stock_code in ('042700','131290'); -- 한미반도체·티에스이
update dim_stock set sector_l1='기타·서비스' where sector_l2='광고' and sector_l1='금융·지주';

-- 6) 지주 재분류 → 자회사 업종으로(테마가 아니라 실제 업종을 따라가게)
-- 6a) 이름 상속: '홀딩스/지주/그룹'을 뗀 이름이 사업회사와 일치하면 그 업종 복사
update dim_stock h set sector_l1=op.sector_l1, sector_l2=op.sector_l2, updated_at=now()
from dim_stock op
where h.sector_l1='금융·지주' and h.is_holding
  and op.sector_l1<>'금융·지주' and op.is_holding=false
  and regexp_replace(h.name,'(홀딩스|지주|그룹)$','')=op.name
  and regexp_replace(h.name,'(홀딩스|지주|그룹)$','')<>h.name;
-- 6b) 나머지 개별 지정(바 이름 그룹지주, 자회사 이름이 달라 상속 안 된 것). sector_l2는 지주·경영관리.
--     ※ 6a에서 이미 옮겨진 건 sector_l1<>'금융·지주'라 여기서 안 건드림.
update dim_stock set sector_l1 = case name
    when 'SK스퀘어' then '반도체·전자'  when 'SK' then '반도체·전자'
    when 'HD현대' then '조선·방산·중공업' when '한진칼' then '운송·물류'
    when '한국앤컴퍼니' then '자동차·부품' when 'CJ' then '음식료·담배'
    when 'HDC' then '건설·엔지니어링'  when '한미사이언스' then '제약·바이오'
    when 'SK디스커버리' then '화학'     when 'LS' then '전기·2차전지'
    when '에코프로' then '전기·2차전지' when '삼성에피스홀딩스' then '제약·바이오'
    when '현대지에프홀딩스' then '유통·도소매' when '쿠쿠홀딩스' then '반도체·전자'
    when 'LS에코에너지' then '전기·2차전지' when '동원산업' then '음식료·담배'
    when 'LG' then '반도체·전자'        when 'GS' then '유통·도소매'
    when '효성' then '화학'             when '롯데지주' then '유통·도소매'
    when '코오롱' then '화학'           when '동국홀딩스' then '철강·금속'
    when '대덕' then '반도체·전자'      when 'HL홀딩스' then '자동차·부품'
    when '콜마홀딩스' then '화학'       when '동아쏘시오홀딩스' then '제약·바이오'
    when '삼양홀딩스' then '화학'       when 'LX홀딩스' then '반도체·전자'
    when 'BGF' then '유통·도소매'       when '일진홀딩스' then '전기·2차전지'
    when 'SNT홀딩스' then '자동차·부품' when '한화머시너리앤서비스홀딩스' then '기계·장비'
    when '아세아' then '건설·엔지니어링' when 'GS피앤엘' then '유통·도소매'
    else sector_l1 end,
  sector_l2='지주·경영관리', updated_at=now()
where sector_l1='금융·지주' and name in (
  'SK스퀘어','SK','HD현대','한진칼','한국앤컴퍼니','CJ','HDC','한미사이언스','SK디스커버리','LS',
  '에코프로','삼성에피스홀딩스','현대지에프홀딩스','쿠쿠홀딩스','LS에코에너지','동원산업',
  'LG','GS','효성','롯데지주','코오롱','동국홀딩스','대덕','HL홀딩스','콜마홀딩스','동아쏘시오홀딩스',
  '삼양홀딩스','LX홀딩스','BGF','일진홀딩스','SNT홀딩스','한화머시너리앤서비스홀딩스','아세아','GS피앤엘');
-- 6c) 안전망: 대분류는 옮겼는데 중분류가 '은행·금융지주'로 남은 지주 → 지주·경영관리
update dim_stock set sector_l2='지주·경영관리'
  where sector_l1<>'금융·지주' and sector_l2='은행·금융지주';

-- 7) 중분류 개별 오버라이드
--    삼성전자(+우): KSIC 264(휴대폰)이나 실질 반도체 대장주 → 반도체
update dim_stock set sector_l2='반도체' where stock_code in ('005930','005935');

-- 8) 우선주 상속(맨 뒤!): 부모 보통주의 최신 sector_l1/l2/is_holding을 그대로.
--    부모가 위에서 다 재분류된 뒤라야 우선주가 올바른 값을 물려받는다.
update dim_stock pref set sector_l1=p.sector_l1, sector_l2=p.sector_l2, is_holding=p.is_holding, updated_at=now()
from dim_stock p
where pref.type='우선주' and pref.stock_code ~ '^[0-9]{6}$'
  and p.stock_code = substr(pref.stock_code,1,5)||'0' and p.stock_code<>pref.stock_code;
-- 코드가 안 맞는 우선주(신형 letter코드 등): 이름 베이스 매칭
update dim_stock pref set sector_l1=p.sector_l1, sector_l2=p.sector_l2, is_holding=p.is_holding, updated_at=now()
from dim_stock p
where pref.type='우선주' and p.type='보통주'
  and regexp_replace(pref.name,'[0-9]*우[A-Z]?(\(전환\))?$','')=p.name
  and regexp_replace(pref.name,'[0-9]*우[A-Z]?(\(전환\))?$','')<>pref.name;
