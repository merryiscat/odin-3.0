"""
오딘 3.0 data_pipeline — 전 종목 일봉 과거 백필 (공공데이터 → raw_stock_ohlc, source='krx').

공공데이터포털 '금융위 주식시세정보'(getStockPriceInfo)는 날짜(basDt) 하나에 전 종목 시·고·저·종·거래량·
거래대금을 준다(하루 약 2,900행, 1,000행/페이지 → 3콜). KIS 호출 0건이라 2년 백필에 알맞다.
단, 그날 시세는 다음 날 새벽에도 안 나오므로(2026-09-17 실측) 당일분은 collect_stock_eod.py(KIS)가 맡는다.

규칙: 이미 kis 줄이 있는 (date, stock_code)는 덮지 않는다(ignore-duplicates) — KIS 쪽이 상한가 값까지 있어 더 완전.
      KONEX·빈 값(거래 없는 날의 0)은 담지 않는다. 시장은 KOSPI/KOSDAQ만.

실행 (프로젝트 루트에서):
  uv run --project data_pipeline python data_pipeline/backfill_stock_ohlc_krx.py --from 2024-08-22 --to 2026-09-15
  uv run --project data_pipeline python data_pipeline/backfill_stock_ohlc_krx.py --from 2026-09-15 --to 2026-09-15 --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.parse
from datetime import date, timedelta
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

KRX_URL = "https://apis.data.go.kr/1160100/service/GetStockSecuritiesInfoService/getStockPriceInfo"
MARKET_CODE = {"KOSPI": "KR-KOSPI", "KOSDAQ": "KR-KOSDAQ"}
PER_PAGE = 1000


def _service_key_qs() -> str:
    key = (os.environ.get("DATA_GO_KR_KEY") or "").strip()
    if not key:
        sys.exit("[오류] DATA_GO_KR_KEY 가 .env 에 없습니다.")
    return key if "%" in key else urllib.parse.quote(key, safe="")


def _get(client: httpx.Client, key_qs: str, params: dict) -> dict:
    qs = "&".join(f"{k}={urllib.parse.quote(str(v), safe='')}" for k, v in params.items())
    for attempt in range(3):                       # 공공데이터는 가끔 잠깐 막힌다 → 3번까지
        try:
            r = client.get(f"{KRX_URL}?serviceKey={key_qs}&{qs}", timeout=30.0)
            data = json.loads(r.text)
            resp = data.get("response", {})
            code = resp.get("header", {}).get("resultCode")
            if code not in ("00", None):
                raise RuntimeError(f"resultCode={code} {resp.get('header', {}).get('resultMsg')}")
            return resp.get("body", {}) or {}
        except (httpx.HTTPError, json.JSONDecodeError, RuntimeError) as e:
            if attempt == 2:
                raise
            print(f"    재시도({e})")
            time.sleep(3.0)
    return {}


def _num(v) -> float | None:
    s = str(v or "").strip()
    try:
        return float(s) if s else None
    except ValueError:
        return None


def fetch_day(client: httpx.Client, key_qs: str, d: date) -> list[dict]:
    """하루치 전 종목 → raw_stock_ohlc 줄. 데이터 없는 날(휴장)은 빈 리스트."""
    bas = d.strftime("%Y%m%d")
    rows, page = [], 1
    while True:
        body = _get(client, key_qs, {"resultType": "json", "numOfRows": PER_PAGE, "pageNo": page, "basDt": bas})
        total = int(body.get("totalCount", 0) or 0)
        items = (body.get("items") or {}).get("item") or []
        items = [items] if isinstance(items, dict) else items
        for it in items:
            mk = MARKET_CODE.get((it.get("mrktCtg") or "").strip())
            srtn = (it.get("srtnCd") or "").strip()
            code = srtn[1:] if srtn[:1].isalpha() else srtn
            close = _num(it.get("clpr"))
            if not mk or len(code) != 6 or not close:
                continue
            vol = int(_num(it.get("trqu")) or 0)
            ohl = (lambda v: (v or None) if vol else None)   # 거래 0인 날은 시·고·저가가 0으로 와서 비운다
            rows.append({
                "date": d.isoformat(), "stock_code": code, "market": mk,
                "open": ohl(_num(it.get("mkp"))), "high": ohl(_num(it.get("hipr"))), "low": ohl(_num(it.get("lopr"))),
                "close": close, "prev_close": (close - (_num(it.get("vs")) or 0)) if _num(it.get("vs")) is not None else None,
                "change_pct": _num(it.get("fltRt")),
                "volume": vol, "trade_value": int(_num(it.get("trPrc")) or 0),
                "source": "krx",
            })
        if not items or page * PER_PAGE >= total:
            return rows
        page += 1


def insert_ignore(url: str, headers: dict, rows: list[dict]) -> None:
    h = dict(headers, Prefer="resolution=ignore-duplicates,return=minimal")
    for i in range(0, len(rows), 500):
        r = httpx.post(f"{url}/rest/v1/raw_stock_ohlc?on_conflict=date,stock_code", headers=h,
                       content=json.dumps(rows[i:i + 500]), timeout=60.0)
        if r.status_code not in (200, 201, 204):
            raise RuntimeError(f"raw_stock_ohlc 쓰기 실패 {r.status_code}: {r.text[:300]}")


def main() -> None:
    ap = argparse.ArgumentParser(description="전 종목 일봉 과거 백필 (공공데이터 → raw_stock_ohlc)")
    ap.add_argument("--from", dest="d_from", required=True)
    ap.add_argument("--to", dest="d_to", required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    url = (os.environ.get("SUPABASE_URL") or "").strip().rstrip("/")
    key = (os.environ.get("SUPABASE_SERVICE_KEY") or "").strip()
    if not args.dry_run and (not url or not key):
        sys.exit("[오류] SUPABASE_URL / SUPABASE_SERVICE_KEY 가 .env 에 없습니다.")
    headers = {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    key_qs = _service_key_qs()

    d, d_to = date.fromisoformat(args.d_from), date.fromisoformat(args.d_to)
    total_rows, days = 0, 0
    with httpx.Client() as client:
        while d <= d_to:
            if d.weekday() < 5:                     # 주말은 부르지도 않는다(휴일은 0건으로 자연히 빠짐)
                rows = fetch_day(client, key_qs, d)
                if rows:
                    days += 1
                    total_rows += len(rows)
                    if args.dry_run:
                        print(f"  {d} {len(rows)}줄 (dry-run) 예: {rows[0]}")
                    else:
                        insert_ignore(url, headers, rows)
                        print(f"  {d} {len(rows)}줄 저장")
                else:
                    print(f"  {d} 데이터 없음(휴장 또는 미공개)")
            d += timedelta(days=1)
    print(f"[끝] {days}거래일 · {total_rows}줄 ({'dry-run' if args.dry_run else '저장'})")


if __name__ == "__main__":
    main()
