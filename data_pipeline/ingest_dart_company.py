"""
오딘 3.0 data_pipeline — DART 기업개황(company.json) 적재 (dim_stock_profile 채우기 2단계).

앞서 연결한 corp_code 로 각 회사의 개황을 불러 dim_stock_profile 을 채운다.
동시에 업종코드(induty_code)를 모아 두면 다음 작업(dim_sector 번역표)의 재료가 된다.

- 읽기: DART company.json (DART_API_KEY, corp_code 별 1콜)
- 쓰기: Supabase dim_stock_profile
- 채우는 칸: corp_name_eng, ceo, est_date, fiscal_month, induty_code, address, homepage

실행:  uv run python data_pipeline/ingest_dart_company.py
"""

from __future__ import annotations

import json
import os
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

DART_COMPANY = "https://opendart.fss.or.kr/api/company.json"


def _env(name: str) -> str:
    v = (os.environ.get(name) or "").strip()
    if not v:
        sys.exit(f"[오류] {name} 가 .env 에 없습니다.")
    return v


def _date(v: str) -> str | None:
    """DART 'YYYYMMDD' → 'YYYY-MM-DD'. 비정상은 None."""
    s = (v or "").strip()
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}" if len(s) == 8 and s.isdigit() else None


def _txt(v) -> str | None:
    s = (v or "").strip()
    return s or None


def fetch_targets() -> list[dict]:
    """corp_code 가 있는 dim_stock_profile 행(=DART 조회 대상)."""
    url, key = _env("SUPABASE_URL"), _env("SUPABASE_SERVICE_KEY")
    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
    rows, offset, step = [], 0, 1000
    while True:
        r = httpx.get(
            f"{url}/rest/v1/dim_stock_profile",
            headers={**headers, "Range": f"{offset}-{offset + step - 1}"},
            params={"select": "stock_code,corp_code",
                     "corp_code": "not.is.null",
                     "induty_code": "is.null"},  # 아직 개황 안 채운 것만(재실행 시 이어서)
            timeout=60.0,
        )
        if r.status_code not in (200, 206):
            sys.exit(f"[오류] profile 조회 실패 {r.status_code}\n{r.text[:300]}")
        batch = r.json()
        rows.extend(batch)
        if len(batch) < step:
            break
        offset += step
    return rows


def upsert(records: list[dict]) -> None:
    url, key = _env("SUPABASE_URL"), _env("SUPABASE_SERVICE_KEY")
    endpoint = f"{url}/rest/v1/dim_stock_profile?on_conflict=stock_code"
    headers = {
        "apikey": key, "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=minimal",
    }
    r = httpx.post(endpoint, headers=headers, content=json.dumps(records), timeout=60.0)
    if r.status_code not in (200, 201, 204):
        sys.exit(f"[오류] 쓰기 실패 {r.status_code}\n{r.text[:400]}")


def fetch_company(client: httpx.Client, key: str, corp_code: str) -> dict | None:
    """company.json 1콜. 분당 한도(status 020) 걸리면 백오프 재시도. 정상 dict 또는 None."""
    for attempt in range(5):
        try:
            d = client.get(DART_COMPANY, params={"crtfc_key": key, "corp_code": corp_code}).json()
        except Exception:
            time.sleep(2.0)  # 연결 끊김(throttle) 시 잠깐 쉬고 재시도
            continue
        st = d.get("status")
        if st == "000":
            return d
        if st == "020":  # 사용 한도 초과 → 점점 길게 쉬고 재시도
            time.sleep(8 * (attempt + 1))
            continue
        return None  # 013(데이터 없음) 등은 재시도 무의미
    return None


def main() -> None:
    key = _env("DART_API_KEY")
    print("[1/2] 대상(corp_code 있는 종목) 불러오는 중...")
    targets = fetch_targets()
    print(f"      대상 {len(targets)}건")

    print("[2/2] DART 기업개황 조회 + 저장(중간중간 upsert)...")
    now = datetime.now(timezone.utc).isoformat()
    buf: list[dict] = []
    induty = Counter()
    ok = fail = consec_fail = 0
    with httpx.Client(timeout=20.0) as client:  # 연결 재사용
        for i, t in enumerate(targets, 1):
            # 아주 느리게 — DART 키/IP를 라이브(옛) 시스템과 공유하므로 간섭·throttle 최소화.
            # 무거운 DART 적재는 장 마감 후(라이브 한가할 때) 돌리는 걸 권장.
            time.sleep(0.3)
            d = fetch_company(client, key, t["corp_code"])
            if d is None:
                fail += 1
                consec_fail += 1
                if consec_fail >= 20:
                    print("  연속 실패 20회 — DART throttle 지속으로 판단해 중단"
                          "(진행분은 저장됨, 나중에 재실행하면 induty_code 빈 것만 이어서 채움).")
                    break
                continue
            consec_fail = 0
            code = _txt(d.get("induty_code"))
            if code:
                induty[code] += 1
            buf.append({
                "stock_code": t["stock_code"],
                "corp_name_eng": _txt(d.get("corp_name_eng")),
                "ceo": _txt(d.get("ceo_nm")),
                "est_date": _date(d.get("est_dt")),
                "fiscal_month": _txt(d.get("acc_mt")),
                "induty_code": code,
                "address": _txt(d.get("adres")),
                "homepage": _txt(d.get("hm_url")),
                "updated_at": now,
            })
            ok += 1
            # 300건마다 저장(중간 실패해도 진행분 보존)
            if len(buf) >= 300:
                upsert(buf)
                buf.clear()
            if i % 300 == 0:
                print(f"  진행 {i}/{len(targets)} (성공 {ok}, 실패 {fail})")
    if buf:
        upsert(buf)

    print(f"완료: 개황 {ok}건 채움 (실패 {fail}). 서로 다른 업종코드 {len(induty)}종.")
    top = ", ".join(f"{c}:{n}" for c, n in induty.most_common(10))
    print(f"상위 업종코드(빈도): {top}")


if __name__ == "__main__":
    main()
