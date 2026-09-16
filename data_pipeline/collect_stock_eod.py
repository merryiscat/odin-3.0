"""
오딘 3.0 data_pipeline — 장 마감 직후 전 종목 종가 스냅샷 → raw_stock_ohlc (매매 리스트의 밑재료).

왜 이 방법인가: 공공데이터(주식시세정보)는 그날 시세를 다음 날 새벽에도 안 준다(2026-09-17 실측).
그래서 15:40에 KIS 묶음 시세(intstock-multprice, 30종목/콜)로 dim_stock 전 종목(약 2,900개 → 약 96콜)의
시가·고가·저가·종가·전일종가·상한가·하한가·거래량·거래대금(원)을 받아 그날 일봉으로 저장한다.
호출은 초당 10건 이하(kis.py 간격)라 20초 안에 끝난다. 옛 오딘과 같은 앱키로 돈다(2026-09-14 결정).

주의:
  - KIS 묶음 시세엔 "날짜"가 없다. 장 마감 뒤(15:35~24:00)에 부르면 오늘 값, 새벽에 부르면 전 거래일 값이다.
    → 기본은 15:35 이후에만 오늘 날짜로 저장. 그 밖의 시각엔 --date로 날짜를 직접 지정해야 한다.
  - 휴장일엔 전 거래일 값이 그대로 오므로 휴장일 저장을 막는다(KIS 휴장일 조회).
  - (date, stock_code) 덮어쓰기 — 원천 확정치.

실행 (프로젝트 루트에서):
  uv run --project data_pipeline python data_pipeline/collect_stock_eod.py                 # 15:35 이후, 오늘
  uv run --project data_pipeline python data_pipeline/collect_stock_eod.py --date 2026-09-16 --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, time, timedelta, timezone

import httpx

from kis import Kis, KisError, num

KST = timezone(timedelta(hours=9))
AFTER_CLOSE = time(15, 35)
MULTI_MAX = 30
MARKET_CODE = {"KOSPI": "KR-KOSPI", "KOSDAQ": "KR-KOSDAQ"}


class Db:
    def __init__(self, write: bool):
        self.url = (os.environ.get("SUPABASE_URL") or "").strip().rstrip("/")
        key = (os.environ.get("SUPABASE_SERVICE_KEY") or "").strip() or (
            "" if write else (os.environ.get("SUPABASE_ANON_KEY") or "").strip())
        if not self.url or not key:
            sys.exit("[오류] SUPABASE_URL / SUPABASE_SERVICE_KEY 가 .env 에 없습니다.")
        self.h = {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    def universe(self) -> dict[str, str]:
        """dim_stock의 살아있는 코스피·코스닥 종목 전부 → {종목코드: 3.0 시장코드}. 종류(보통주·우선주…)는 안 거른다
        (거르는 건 매매 리스트 유니버스 단계의 몫 — 여기는 원천이라 다 담는다)."""
        out, offset = {}, 0
        while True:
            r = httpx.get(f"{self.url}/rest/v1/dim_stock", headers=self.h, timeout=30.0, params={
                "select": "stock_code,market", "market": "in.(KOSPI,KOSDAQ)", "delisted_date": "is.null",
                "order": "stock_code", "limit": 1000, "offset": offset})
            r.raise_for_status()
            rows = r.json()
            out.update({x["stock_code"]: MARKET_CODE[x["market"]] for x in rows})
            if len(rows) < 1000:
                return out
            offset += 1000

    def upsert(self, rows: list[dict]) -> None:
        h = dict(self.h, Prefer="resolution=merge-duplicates,return=minimal")
        for i in range(0, len(rows), 500):
            r = httpx.post(f"{self.url}/rest/v1/raw_stock_ohlc?on_conflict=date,stock_code", headers=h,
                           content=json.dumps(rows[i:i + 500]), timeout=60.0)
            if r.status_code not in (200, 201, 204):
                raise RuntimeError(f"raw_stock_ohlc 쓰기 실패 {r.status_code}: {r.text[:300]}")


def is_open_day(kis: Kis, d: date) -> bool:
    """KIS 휴장일 조회(하루 1회 권고). 조회 실패면 평일=개장으로 본다."""
    if d.weekday() >= 5:
        return False
    ymd = d.strftime("%Y%m%d")
    try:
        body = kis.get("/uapi/domestic-stock/v1/quotations/chk-holiday", "CTCA0903R",
                       {"BASS_DT": ymd, "CTX_AREA_NK": "", "CTX_AREA_FK": ""})
        for item in body.get("output") or []:
            if item.get("bass_dt") == ymd:
                return item.get("opnd_yn") == "Y"
    except KisError as e:
        print(f"[경고] 휴장일 조회 실패({e}) — 평일이라 개장으로 보고 진행")
    return True


def snapshot(kis: Kis, universe: dict[str, str], d: date) -> tuple[list[dict], list[str]]:
    """묶음 시세를 30개씩 돌려 일봉 줄로 바꾼다. 실패한 묶음의 종목코드는 따로 돌려준다(재시도용)."""
    codes = sorted(universe)
    rows, failed = [], []
    for i in range(0, len(codes), MULTI_MAX):
        chunk = codes[i:i + MULTI_MAX]
        params = {}
        for n, c in enumerate(chunk, 1):          # 쓰는 칸만 보낸다(빈 칸은 OPSQ2002 거부 — collect_rt와 같은 교훈)
            params[f"FID_COND_MRKT_DIV_CODE_{n}"] = "J"
            params[f"FID_INPUT_ISCD_{n}"] = c
        try:
            out = kis.get("/uapi/domestic-stock/v1/quotations/intstock-multprice", "FHKST11300006", params).get("output") or []
        except KisError as e:
            print(f"  묶음 {i // MULTI_MAX + 1} 실패: {e}")
            failed.extend(chunk)
            continue
        for o in out if isinstance(out, list) else [out]:
            code = (o.get("inter_shrn_iscd") or "").strip()
            close = num(o.get("inter2_prpr"))
            if code not in universe or not close:      # 0원(거래 없음·상폐 직전)은 일봉이 아니다
                continue
            vol = int(num(o.get("acml_vol")) or 0)
            ohl = (lambda v: (v or None) if vol else None)   # 거래 0인 날(거래정지 등)은 시·고·저가 0으로 와서 비운다
            rows.append({
                "date": d.isoformat(), "stock_code": code, "market": universe[code],
                "open": ohl(num(o.get("inter2_oprc"))), "high": ohl(num(o.get("inter2_hgpr"))), "low": ohl(num(o.get("inter2_lwpr"))),
                "close": close, "prev_close": num(o.get("inter2_prdy_clpr")), "change_pct": num(o.get("prdy_ctrt")),
                "volume": vol, "trade_value": int(num(o.get("acml_tr_pbmn")) or 0),
                "upper_limit": num(o.get("inter2_mxpr")), "lower_limit": num(o.get("inter2_llam")), "source": "kis",
            })
    return rows, failed


def main() -> None:
    ap = argparse.ArgumentParser(description="장 마감 직후 전 종목 종가 스냅샷 → raw_stock_ohlc")
    ap.add_argument("--date", help="저장할 거래일(YYYY-MM-DD). 15:35 이후에 돌리면 생략 가능(오늘)")
    ap.add_argument("--dry-run", action="store_true", help="DB에 쓰지 않고 요약만")
    ap.add_argument("--force", action="store_true", help="휴장일 검사를 건너뛴다")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    now = datetime.now(KST)
    if args.date:
        d = date.fromisoformat(args.date)
    elif now.time() >= AFTER_CLOSE:
        d = now.date()
    else:
        sys.exit("[중단] 15:35 전에는 KIS 값이 '오늘 종가'가 아닙니다 — 저장할 날짜를 --date 로 지정하세요.")

    db, kis = Db(write=not args.dry_run), Kis()
    if not args.force and not is_open_day(kis, d):
        print(f"[끝] {d} 휴장일 — 저장 없음(휴장일엔 전 거래일 값이 그대로 와서 막는다)")
        return
    universe = db.universe()
    print(f"[시작] {d} 종가 스냅샷 · 대상 {len(universe)}종목 · 약 {-(-len(universe) // MULTI_MAX)}콜")
    rows, failed = snapshot(kis, universe, d)
    if failed:                                    # 실패 묶음 1회 재시도
        print(f"  실패 {len(failed)}종목 재시도")
        more, failed = snapshot(kis, {c: universe[c] for c in failed}, d)
        rows.extend(more)
    n_up = sum(1 for r in rows if r["upper_limit"] and r["close"] >= r["upper_limit"])
    n_zero_amt = sum(1 for r in rows if not r["trade_value"])
    print(f"  받음 {len(rows)}줄 (누락 {len(universe) - len(rows)}) · 상한가 마감 {n_up}종목 · 거래대금 0인 줄 {n_zero_amt}")
    if args.dry_run:
        for r in rows[:3]:
            print(f"  (dry-run) {r}")
        return
    db.upsert(rows)
    print(f"[끝] raw_stock_ohlc {d} {len(rows)}줄 저장 (source=kis)")
    if failed:
        sys.exit(f"[경고] 끝내 실패한 종목 {len(failed)}개: {failed[:10]}…")


if __name__ == "__main__":
    main()
