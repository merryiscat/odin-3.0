"""
오딘 3.0 data_pipeline — 실시간 수집 대상 바스켓 명부(dim_basket) 만들기.

- leader: 시장(코스피/코스닥)별로, 업종(sector_l1)마다 시가총액 1위 보통주 1개 = 업종 대장주(~21개)
- proxy : 시장별 시가총액 상위 30개 보통주 (지수가 없는 아침·저녁 시간대의 지수 대용 — 수집은 아직 안 함)
재료는 dim_stock(시총·업종·종류·상태). 매일 아침 다시 뽑아 "현재 명부"로 덮어쓴다
(판정 순간 실제로 쓴 구성은 state_market_temp.metrics.basket에 따로 남는다).

실행 (프로젝트 루트에서):  uv run --project data_pipeline python data_pipeline/build_basket.py [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

KST = timezone(timedelta(hours=9))
MARKET_CODE = {"KOSPI": "KR-KOSPI", "KOSDAQ": "KR-KOSDAQ"}
PROXY_SIZE = 30
EXCLUDED_STATUS = {"거래정지"}   # 시세가 안 움직여 폭 계산을 왜곡 → 명부에서 뺀다


def _conn(write: bool) -> tuple[str, dict]:
    """쓰기는 Secret 키 필수. --dry-run(읽기만)은 Secret 키가 없으면 공개 읽기 키(SUPABASE_ANON_KEY)로."""
    url = (os.environ.get("SUPABASE_URL") or "").strip().rstrip("/")
    key = (os.environ.get("SUPABASE_SERVICE_KEY") or "").strip()
    if not key and not write:
        key = (os.environ.get("SUPABASE_ANON_KEY") or "").strip()
    if not url or not key:
        sys.exit("[오류] SUPABASE_URL / SUPABASE_SERVICE_KEY 가 .env 에 없습니다. scripts\\setup-keys.cmd 를 실행하세요.")
    return url, {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"}


def fetch_stocks(url: str, headers: dict) -> list[dict]:
    """dim_stock 전체(코스피·코스닥 보통주, 상폐 제외)를 1,000행씩 끊어 읽는다."""
    out, offset = [], 0
    while True:
        r = httpx.get(f"{url}/rest/v1/dim_stock", headers=headers, timeout=30.0, params={
            "select": "stock_code,name,market,sector_l1,market_cap,status",
            "market": "in.(KOSPI,KOSDAQ)", "type": "eq.보통주", "delisted_date": "is.null",
            "market_cap": "not.is.null", "order": "stock_code", "limit": 1000, "offset": offset})
        if r.status_code != 200:
            sys.exit(f"[오류] dim_stock 조회 실패 {r.status_code}: {r.text[:300]}")
        rows = r.json()
        out.extend(rows)
        if len(rows) < 1000:
            return out
        offset += 1000


def build(stocks: list[dict], as_of: str) -> list[dict]:
    rows = []
    for mk, code in MARKET_CODE.items():
        pool = [s for s in stocks if s["market"] == mk and s.get("status") not in EXCLUDED_STATUS]
        leaders: dict[str, dict] = {}
        for s in pool:
            sec = s.get("sector_l1")
            if sec and (sec not in leaders or s["market_cap"] > leaders[sec]["market_cap"]):
                leaders[sec] = s
        for sec, s in sorted(leaders.items()):
            rows.append({"basket_type": "leader", "market": code, "stock_code": s["stock_code"],
                         "sector_l1": sec, "rank_no": None, "as_of": as_of})
        top = sorted(pool, key=lambda s: s["market_cap"], reverse=True)[:PROXY_SIZE]
        for i, s in enumerate(top, 1):
            rows.append({"basket_type": "proxy", "market": code, "stock_code": s["stock_code"],
                         "sector_l1": None, "rank_no": i, "as_of": as_of})
        names = {s["stock_code"]: s["name"] for s in pool}
        print(f"  {code} 대장주 {len(leaders)}개: " + ", ".join(f"{sec}={names[s['stock_code']]}" for sec, s in sorted(leaders.items())))
    return rows


def replace_basket(url: str, headers: dict, rows: list[dict]) -> None:
    """현재 명부로 교체: 새 명부 upsert → 새 명부에 없는 옛 줄 삭제(대장주가 바뀐 업종 정리)."""
    h = dict(headers, Prefer="resolution=merge-duplicates,return=minimal")
    r = httpx.post(f"{url}/rest/v1/dim_basket?on_conflict=basket_type,market,stock_code", headers=h,
                   content=json.dumps(rows, ensure_ascii=False), timeout=30.0)
    if r.status_code not in (200, 201, 204):
        sys.exit(f"[오류] dim_basket 쓰기 실패 {r.status_code}: {r.text[:300]}")
    for bt in ("leader", "proxy"):
        for mk in MARKET_CODE.values():
            keep = [x["stock_code"] for x in rows if x["basket_type"] == bt and x["market"] == mk]
            d = httpx.delete(f"{url}/rest/v1/dim_basket", headers=headers, timeout=30.0, params={
                "basket_type": f"eq.{bt}", "market": f"eq.{mk}", "stock_code": f"not.in.({','.join(keep)})"})
            if d.status_code not in (200, 204):
                sys.exit(f"[오류] dim_basket 옛 줄 삭제 실패 {d.status_code}: {d.text[:300]}")


def main() -> None:
    ap = argparse.ArgumentParser(description="dim_basket(업종 대장주·시총 상위) 명부 갱신")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    url, headers = _conn(write=not args.dry_run)
    as_of = datetime.now(KST).date().isoformat()
    stocks = fetch_stocks(url, headers)
    print(f"[명부] dim_stock 보통주 {len(stocks)}개 → {as_of} 기준")
    rows = build(stocks, as_of)
    if args.dry_run:
        print(f"[dry-run] {len(rows)}줄 (기록 안 함)")
        return
    replace_basket(url, headers, rows)
    print(f"[끝] dim_basket {len(rows)}줄 갱신")


if __name__ == "__main__":
    main()
