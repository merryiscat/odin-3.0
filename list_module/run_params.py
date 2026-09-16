"""
오딘 3.0 list_module — 종목 파라미터 계산 실행기 (raw_stock_ohlc + raw_rank_snapshot + dim_stock → stock_params bg_daily).

밤 배치: 그날 종가 스냅샷(collect_stock_eod)이 들어온 뒤 돌린다. 기준일(as_of_date)의 전 종목에 대해
stock_params.py의 순수 계산을 돌려 종목당 한 줄(scope=bg_daily)을 append 한다.
과거 기록은 저장하지 않는다 — 원천(raw_stock_ohlc)에서 언제든 같은 코드로 재계산되므로(결정적),
백테스트는 load_universe_frames()로 메모리에서 계산한다(저장은 실가동 날부터).

실행 (프로젝트 루트에서):
  uv run --project list_module python list_module/run_params.py                       # 기준일 = raw_stock_ohlc 최신 날짜
  uv run --project list_module python list_module/run_params.py --date 2026-09-16 --dry-run
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

import stock_params as sp
from supa import Supa

KST = timezone(timedelta(hours=9))
LOOKBACK_CAL_DAYS = 400   # 52주(252거래일) 창을 채우려면 달력으로 약 400일
OHLC_COLS = "date,stock_code,market,open,high,low,close,prev_close,change_pct,volume,trade_value,upper_limit"


def load_stocks(db: Supa) -> dict[str, dict]:
    rows = db.select("dim_stock", {"select": "stock_code,name,market,type,status,sector_l1,market_cap,is_kospi200,is_kosdaq150",
                                   "market": "in.(KOSPI,KOSDAQ)", "order": "stock_code"})
    return {r["stock_code"]: r for r in rows}


def load_ohlc(db: Supa, d_from: date, d_to: date) -> dict[str, list[dict]]:
    """기간의 전 종목 일봉 → {종목코드: [줄…오름차순]}. 날짜 단위로 끊어 읽는다(한 날 ~2,800줄 = 3페이지)."""
    by_code: dict[str, list[dict]] = defaultdict(list)
    d = d_from
    while d <= d_to:
        if d.weekday() < 5:
            rows = db.select("raw_stock_ohlc", {"select": OHLC_COLS, "date": f"eq.{d.isoformat()}", "order": "stock_code"})
            for r in rows:
                by_code[r["stock_code"]].append(r)
        d += timedelta(days=1)
    return by_code


def load_rank_days(db: Supa, d_from: date, d_to: date) -> dict[str, list[dict]]:
    """순위 축적(15:35 확정분)에서 종목별 날짜별 외인·기관 순매수 → {종목코드: [{date, foreign_net, inst_net, foreign_buy, inst_buy}…]}.
    그날 15:35 줄이 하나라도 있으면 '그날 축적 있음'으로 보고, 순위 밖 종목은 0으로 센다."""
    rows = db.select("raw_rank_snapshot", {
        "select": "ts,kind,stock_code,net_amount", "kind": "in.(foreign_buy,foreign_sell,inst_buy,inst_sell)",
        "ts": f"gte.{d_from.isoformat()}T00:00:00+09:00", "order": "ts"})
    # 하루의 마지막 조회(15:35)만 그날 확정치로
    by_day: dict[str, dict] = {}
    for r in rows:
        d = datetime.fromisoformat(r["ts"]).astimezone(KST).date().isoformat()
        by_day.setdefault(d, {})
        by_day[d].setdefault("_ts", r["ts"])
        if r["ts"] > by_day[d]["_ts"]:
            by_day[d] = {"_ts": r["ts"]}
        if r["ts"] == by_day[d]["_ts"]:
            by_day[d].setdefault(r["stock_code"], {"foreign_net": 0, "inst_net": 0, "foreign_buy": False, "inst_buy": False})
            e = by_day[d][r["stock_code"]]
            if r["kind"].startswith("foreign"):
                e["foreign_net"] += r["net_amount"] or 0
                e["foreign_buy"] = e["foreign_buy"] or r["kind"] == "foreign_buy"
            else:
                e["inst_net"] += r["net_amount"] or 0
                e["inst_buy"] = e["inst_buy"] or r["kind"] == "inst_buy"
    out: dict[str, list[dict]] = defaultdict(list)
    codes = {c for d in by_day.values() for c in d if c != "_ts"}
    for d in sorted(by_day):
        if d > d_to.isoformat():
            continue
        for c in codes:
            e = by_day[d].get(c) or {"foreign_net": 0, "inst_net": 0, "foreign_buy": False, "inst_buy": False}
            out[c].append({"date": d, **e})
    return out, sorted(by_day)


def compute_day(as_of: date, ohlc_by_code: dict[str, list[dict]], rank_by_code: dict[str, list[dict]], rank_dates: list[str],
                stocks: dict[str, dict]) -> list[dict]:
    """기준일 전 종목 파라미터 줄 목록(stock_params 표 모양)."""
    ts = datetime.now(KST).isoformat()
    out = []
    for code, rows in ohlc_by_code.items():
        hist = [r for r in rows if r["date"] <= as_of.isoformat()]
        if not hist or hist[-1]["date"] != as_of.isoformat():
            continue                                  # 기준일 일봉이 없는 종목(거래정지 등)은 건너뜀
        stock = stocks.get(code) or {}
        rank_days = None
        if rank_dates:                                # 축적이 있을 때만; 그 종목 줄은 순위 밖이면 0으로 채워져 있음
            rank_days = [d for d in rank_by_code.get(code, []) if d["date"] <= as_of.isoformat()] or \
                        [{"date": d, "foreign_net": 0, "inst_net": 0, "foreign_buy": False, "inst_buy": False}
                         for d in rank_dates if d <= as_of.isoformat()]
        p = sp.bg_daily(hist[-(sp.HIGH_252 + 1):], rank_days, stock)
        out.append({"ts": ts, "as_of_date": as_of.isoformat(), "market": hist[-1]["market"], "stock_code": code,
                    "scope": "bg_daily", "params": p, "calc_ver": sp.CALC_VER})
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="종목 파라미터(bg_daily) 계산 → stock_params")
    ap.add_argument("--date", help="기준일 YYYY-MM-DD (생략 = raw_stock_ohlc 최신 날짜)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    db = Supa(write=not args.dry_run)
    if args.date:
        as_of = date.fromisoformat(args.date)
    else:
        latest = db.select("raw_stock_ohlc", {"select": "date", "order": "date.desc", "limit": 1})
        if not latest:
            sys.exit("[중단] raw_stock_ohlc가 비어 있음")
        as_of = date.fromisoformat(latest[0]["date"])
    already = db.select("stock_params", {"select": "id", "as_of_date": f"eq.{as_of.isoformat()}", "scope": "eq.bg_daily", "limit": 1})
    if already and not args.dry_run:
        sys.exit(f"[중단] {as_of} bg_daily 파라미터가 이미 있음 — append 표라 두 번 쓰지 않는다")

    print(f"[시작] 기준일 {as_of} · 일봉 {LOOKBACK_CAL_DAYS}일치 읽는 중…")
    stocks = load_stocks(db)
    ohlc = load_ohlc(db, as_of - timedelta(days=LOOKBACK_CAL_DAYS), as_of)
    rank_by_code, rank_dates = load_rank_days(db, as_of - timedelta(days=20), as_of)
    rows = compute_day(as_of, ohlc, rank_by_code, rank_dates, stocks)
    n_full = sum(1 for r in rows if r["params"]["high252_gap_pct"] is not None)
    n_lu = sum(1 for r in rows if r["params"]["limit_up"])
    n_thin = sum(1 for r in rows if r["params"]["thin"])
    print(f"  종목 {len(rows)}줄 · 52주 창 채워진 종목 {n_full} · 상한가 {n_lu} · 얇음 {n_thin} · 수급 축적 {len(rank_dates)}일")
    if args.dry_run:
        for r in rows[:2]:
            print("  (dry-run)", r["stock_code"], r["params"])
        return
    db.insert("stock_params", rows)
    print(f"[끝] stock_params {as_of} bg_daily {len(rows)}줄 저장")


if __name__ == "__main__":
    main()
