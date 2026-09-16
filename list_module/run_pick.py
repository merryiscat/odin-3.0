"""
오딘 3.0 list_module — 매매 리스트 실행기: 아침 확정 리스트(phase=open) 만들기 + 과거 재현(백테스트).

두 가지 모드:
  1) 밤 배치(기본): stock_params(bg_daily, 기준일)를 읽어 rule-pick-v1 + 기준선 3개의 "다음 거래일 리스트"를 pick_list에 쓴다.
     list_date = 다음 거래일(평일 다음 날 — 휴장일이면 그다음 아침에 다시 만들면 됨), as_of = 기준일 15:30(데이터 시각).
  2) 백테스트(--from/--to): raw_stock_ohlc에서 기간의 파라미터를 메모리에서 계산(stock_params 저장 안 함)해
     날마다 리스트를 만들고, 원하면 pick_list에 basis=eod·phase=open으로 기록(--write). 채점은 measure_pick.py.

실행 (프로젝트 루트에서):
  uv run --project list_module python list_module/run_pick.py                      # 기준일 = stock_params 최신
  uv run --project list_module python list_module/run_pick.py --date 2026-09-16 --dry-run
  uv run --project list_module python list_module/run_pick.py --from 2025-09-01 --to 2026-09-15 --out picks.json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta, timezone

import pick_rules as pr
import stock_params as sp
from run_params import LOOKBACK_CAL_DAYS, compute_day, load_ohlc, load_rank_days, load_stocks
from supa import Supa

KST = timezone(timedelta(hours=9))
MARKETS = ("KR-KOSPI", "KR-KOSDAQ")
BASELINES = ("baseline-random-pick-v1", "baseline-topcap-pick-v1", "baseline-carry-pick-v1")


def next_trading_day(d: date) -> date:
    """다음 평일(휴장일은 모름 — 휴장일이면 그날 리스트가 남지만 해가 없다: 그날은 아무도 읽지 않는다)."""
    n = d + timedelta(days=1)
    while n.weekday() >= 5:
        n += timedelta(days=1)
    return n


def attach_stock(params_rows: list[dict], stocks: dict[str, dict]) -> list[dict]:
    """stock_params 줄 + dim_stock 칸 → 규칙이 읽는 모양."""
    out = []
    for r in params_rows:
        s = stocks.get(r["stock_code"]) or {}
        out.append({"stock_code": r["stock_code"], "market": r["market"], "params": r["params"],
                    "type": s.get("type"), "status": s.get("status"), "sector_l1": s.get("sector_l1"), "market_cap": s.get("market_cap")})
    return out


def make_lists(rows_by_market: dict[str, list[dict]], as_of: date, prev_rule: dict[str, list[dict]] | None) -> dict[str, dict[str, list[dict]]]:
    """{model_id: {market: picks}}. prev_rule = 전 거래일 rule-pick-v1 결과(carry 기준선용)."""
    out: dict[str, dict[str, list[dict]]] = {m: {} for m in (pr.MODEL_ID, *BASELINES)}
    for mk, rows in rows_by_market.items():
        out[pr.MODEL_ID][mk] = pr.pick(rows)
        out["baseline-random-pick-v1"][mk] = pr.baseline_random(rows, seed=f"{as_of}-{mk}")
        out["baseline-topcap-pick-v1"][mk] = pr.baseline_topcap(rows)
        out["baseline-carry-pick-v1"][mk] = pr.baseline_carry((prev_rule or {}).get(mk) or [])
    return out


def to_rows(lists: dict, as_of: date, list_date: date) -> list[dict]:
    ts = datetime.now(KST).isoformat()
    as_of_ts = datetime.combine(as_of, datetime.min.time(), KST).replace(hour=15, minute=30).isoformat()
    rows = []
    for model_id, by_mk in lists.items():
        for mk, picks in by_mk.items():
            for x in picks:
                rows.append({"ts": ts, "as_of": as_of_ts, "list_date": list_date.isoformat(), "market": mk, "model_id": model_id,
                             "phase": "open", "basis": "eod", "stock_code": x["stock_code"], "rank_no": x.get("rank_no"),
                             "score": x.get("score"), "reasons": x["reasons"], "warn": x.get("warn") or [],
                             "label_kr": x.get("label_kr"), "metrics": x.get("metrics"), "rule_ver": pr.RULE_VER})
    return rows


def summarize(lists: dict, as_of: date) -> None:
    for mk, picks in lists[pr.MODEL_ID].items():
        from collections import Counter
        c = Counter(l for x in picks for l in x["reasons"])
        w = Counter(l for x in picks for l in x["warn"])
        top = ", ".join(f"{x['stock_code']}[{'+'.join(x['reasons'])}]" for x in picks[:5])
        print(f"  {as_of} {mk} {len(picks)}종목 · 렌즈 {dict(c)} · 경고 {dict(w)} · 상위: {top}")


def main() -> None:
    ap = argparse.ArgumentParser(description="매매 리스트(아침 확정) 생성 — rule-pick-v1 + 기준선")
    ap.add_argument("--date", help="기준일(그날 종가로 다음 거래일 리스트)")
    ap.add_argument("--from", dest="d_from", help="백테스트 시작 기준일")
    ap.add_argument("--to", dest="d_to", help="백테스트 끝 기준일")
    ap.add_argument("--out", help="백테스트 결과 JSON 파일")
    ap.add_argument("--write", action="store_true", help="백테스트 결과도 pick_list에 기록")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    db = Supa(write=not args.dry_run)
    stocks = load_stocks(db)

    if args.d_from:                                   # ── 백테스트: 원천에서 메모리 계산
        d_from, d_to = date.fromisoformat(args.d_from), date.fromisoformat(args.d_to or args.d_from)
        print(f"[백테스트] {d_from}~{d_to} 일봉 읽는 중(앞 {LOOKBACK_CAL_DAYS}일 포함)…")
        ohlc = load_ohlc(db, d_from - timedelta(days=LOOKBACK_CAL_DAYS), d_to)
        rank_by_code, rank_dates = load_rank_days(db, d_from - timedelta(days=20), d_to)
        trading_days = sorted({r["date"] for rows in ohlc.values() for r in rows if d_from.isoformat() <= r["date"] <= d_to.isoformat()})
        results, prev_rule, all_rows = {}, None, []
        for ds in trading_days:
            as_of = date.fromisoformat(ds)
            prow = compute_day(as_of, ohlc, rank_by_code, rank_dates, stocks)
            by_mk = {mk: attach_stock([r for r in prow if r["market"] == mk], stocks) for mk in MARKETS}
            lists = make_lists(by_mk, as_of, prev_rule)
            prev_rule = lists[pr.MODEL_ID]
            results[ds] = lists
            summarize(lists, as_of)
            if args.write:
                all_rows.extend(to_rows(lists, as_of, next_trading_day(as_of)))
        if args.out:
            with open(args.out, "w", encoding="utf-8") as f:
                json.dump(results, f, ensure_ascii=False)
            print(f"[저장] {args.out}")
        if args.write and not args.dry_run:
            db.insert("pick_list", all_rows)
            print(f"[끝] pick_list {len(all_rows)}줄 기록")
        return

    # ── 밤 배치: stock_params에서
    if args.date:
        as_of = date.fromisoformat(args.date)
    else:
        latest = db.select("stock_params", {"select": "as_of_date", "scope": "eq.bg_daily", "order": "as_of_date.desc", "limit": 1})
        if not latest:
            sys.exit("[중단] stock_params가 비어 있음 — run_params.py 먼저")
        as_of = date.fromisoformat(latest[0]["as_of_date"])
    list_date = next_trading_day(as_of)
    if not args.dry_run and db.select("pick_list", {"select": "id", "list_date": f"eq.{list_date}", "model_id": f"eq.{pr.MODEL_ID}",
                                                    "phase": "eq.open", "limit": 1}):
        sys.exit(f"[중단] {list_date} 아침 리스트가 이미 있음 — append 표라 두 번 쓰지 않는다")
    prow = db.select("stock_params", {"select": "stock_code,market,params", "as_of_date": f"eq.{as_of}", "scope": "eq.bg_daily", "order": "stock_code"})
    if not prow:
        sys.exit(f"[중단] {as_of} stock_params 없음 — run_params.py --date {as_of} 먼저")
    prev = db.select("pick_list", {"select": "market,stock_code,rank_no,score,reasons,warn", "list_date": f"eq.{as_of}",
                                   "model_id": f"eq.{pr.MODEL_ID}", "phase": "eq.open", "order": "rank_no"})
    prev_rule = {mk: [x for x in prev if x["market"] == mk] for mk in MARKETS} if prev else None
    by_mk = {mk: attach_stock([r for r in prow if r["market"] == mk], stocks) for mk in MARKETS}
    lists = make_lists(by_mk, as_of, prev_rule)
    summarize(lists, as_of)
    rows = to_rows(lists, as_of, list_date)
    if args.dry_run:
        print(f"[dry-run] {list_date} 리스트 {len(rows)}줄(모델 4개) — 기록 안 함")
        return
    db.insert("pick_list", rows)
    print(f"[끝] pick_list {list_date} {len(rows)}줄 기록 (기준일 {as_of})")


if __name__ == "__main__":
    main()
