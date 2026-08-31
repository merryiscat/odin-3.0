"""
오딘 3.0 data_pipeline — DART 기업코드 연결 (dim_stock_profile 채우기 1단계).

DART corpCode.xml(전체 상장사의 corp_code ↔ stock_code ↔ corp_name)을 받아,
dim_stock 에 있는 종목에 한해 dim_stock_profile 에 corp_code·corp_name 을 upsert 한다.
corp_code 는 이후 DART 재무·공시·기업개황 호출의 '열쇠'.

- 읽기: DART API (DART_API_KEY) — corpCode.xml (zip 안의 xml)
- 쓰기: Supabase dim_stock_profile (Secret 키로 RLS 우회)
- 채우는 칸: stock_code, corp_code, corp_name

주의: corpCode.xml 은 회사(corp) 단위라 대표 '보통주' 코드만 담긴다 →
      우선주(예 005935)는 여기서 안 잡힘(나중에 부모 보통주에 연결). 지금은 보통주 위주로 채워진다.

실행:  uv run python data_pipeline/ingest_dart_corpcode.py
"""

from __future__ import annotations

import io
import json
import os
import sys
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

DART_CORPCODE = "https://opendart.fss.or.kr/api/corpCode.xml"


def _env(name: str) -> str:
    v = (os.environ.get(name) or "").strip()
    if not v:
        sys.exit(f"[오류] {name} 가 .env 에 없습니다. scripts\\setup-keys.cmd 를 확인하세요.")
    return v


def fetch_corp_rows() -> list[dict]:
    """DART corpCode.xml(zip) 을 받아 상장사(stock_code 보유) 목록으로 파싱."""
    key = _env("DART_API_KEY")
    r = httpx.get(DART_CORPCODE, params={"crtfc_key": key}, timeout=90.0)
    # 키/한도 오류면 zip 대신 XML 에러(<result><status>...)가 온다.
    try:
        z = zipfile.ZipFile(io.BytesIO(r.content))
        xml_bytes = z.read(z.namelist()[0])
    except zipfile.BadZipFile:
        sys.exit(f"[오류] corpCode 응답이 zip 이 아님(키/한도 문제 가능). 응답 앞부분:\n{r.text[:400]}")

    root = ET.fromstring(xml_bytes)
    rows = []
    for corp in root.findall("list"):
        stock = (corp.findtext("stock_code") or "").strip()
        corp_code = (corp.findtext("corp_code") or "").strip()
        corp_name = (corp.findtext("corp_name") or "").strip()
        if len(stock) == 6:  # stock_code 가 있으면 상장사
            rows.append({"stock_code": stock, "corp_code": corp_code, "corp_name": corp_name})
    return rows


def fetch_dim_stock_codes() -> set[str]:
    """dim_stock 에 이미 있는 종목코드 집합(프로필을 이 종목들로만 채우기 위함)."""
    url, key = _env("SUPABASE_URL"), _env("SUPABASE_SERVICE_KEY")
    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
    codes: set[str] = set()
    step = 1000
    offset = 0
    while True:
        r = httpx.get(
            f"{url}/rest/v1/dim_stock",
            headers={**headers, "Range-Unit": "items", "Range": f"{offset}-{offset + step - 1}"},
            params={"select": "stock_code"},
            timeout=60.0,
        )
        if r.status_code not in (200, 206):
            sys.exit(f"[오류] dim_stock 조회 실패 {r.status_code}\n{r.text[:300]}")
        batch = r.json()
        codes.update(x["stock_code"] for x in batch)
        if len(batch) < step:
            break
        offset += step
    return codes


def upsert_profile(records: list[dict]) -> None:
    """dim_stock_profile 에 corp_code·corp_name upsert."""
    url, key = _env("SUPABASE_URL"), _env("SUPABASE_SERVICE_KEY")
    now = datetime.now(timezone.utc).isoformat()
    for rec in records:
        rec["updated_at"] = now
    endpoint = f"{url}/rest/v1/dim_stock_profile?on_conflict=stock_code"
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=minimal",
    }
    CHUNK = 500
    for i in range(0, len(records), CHUNK):
        chunk = records[i:i + CHUNK]
        r = httpx.post(endpoint, headers=headers, content=json.dumps(chunk), timeout=60.0)
        if r.status_code not in (200, 201, 204):
            sys.exit(f"[오류] Supabase 쓰기 실패 {r.status_code}\n{r.text[:400]}")
        print(f"  upsert {i + len(chunk)}/{len(records)}")


def main() -> None:
    print("[1/3] DART corpCode.xml 받는 중...")
    corp_rows = fetch_corp_rows()
    print(f"      DART 상장사(corp_code 보유) {len(corp_rows)}건")

    print("[2/3] dim_stock 종목코드 불러오는 중...")
    dim_codes = fetch_dim_stock_codes()
    records = [r for r in corp_rows if r["stock_code"] in dim_codes]
    print(f"      dim_stock 과 매칭 {len(records)}건 (나머지는 상폐/비대상)")

    print("[3/3] dim_stock_profile 에 upsert...")
    upsert_profile(records)
    print(f"완료: dim_stock_profile {len(records)}건 연결(corp_code 채움).")


if __name__ == "__main__":
    main()
