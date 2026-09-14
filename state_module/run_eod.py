"""
오딘 3.0 state_module — 종가 기준 하루 판정 (basis=eod, 하루 한 시장 한 줄).

GUI 첫 화면(S1)의 "종가 기준" 상태·최근 10거래일 띠가 이 줄을 읽는다.
재료:
  - 지수 등락률·대금 배율: raw_index_ohlc (밤 복사 data_pipeline/copy_from_old.py가 그날 일봉을 넣은 뒤)
  - 대장주 폭: 그날 장 마감 무렵 raw_market_snapshot의 대장주 마지막 시세(collect_rt.py가 쌓음)
장중 루프와 같은 rt_rules.classify를 쓴다(깜빡임 방지는 없음 — 하루 1판정).

실행 (프로젝트 루트에서, 장 마감 + 밤 복사 뒤):
  uv run --project state_module python state_module/run_eod.py                 # 오늘
  uv run --project state_module python state_module/run_eod.py --date 2026-09-15 --dry-run
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, time, timedelta

from rt_data import KST, MARKETS, kst_iso, latest_by_code, load_leaders, load_ohlc, load_snapshots
from rt_params import daily_money_ratios, pct_change, surge_threshold
from rt_rules import MIN_SAMPLE, MODEL_ID, RULE_VER, breadth_from_changes, classify
from supa import Supa

CLOSE_AS_OF = time(15, 30)


def judge_day(db: Supa, d: date, leaders: dict, allow_no_breadth: bool) -> list[dict]:
    rows = []
    snaps = load_snapshots(db, datetime.combine(d, time(15, 0), KST), datetime.combine(d, time(15, 45), KST),
                           kinds=("stock",))
    latest = latest_by_code(snaps)
    for market in MARKETS:
        ohlc = load_ohlc(db, market, d)
        if not ohlc or ohlc[-1]["date"] != d.isoformat() or len(ohlc) < 2:
            print(f"  {market}: raw_index_ohlc에 {d} 일봉이 없음 — 밤 복사(copy_from_old.py) 먼저. 건너뜀")
            continue
        chg = pct_change(float(ohlc[-1]["close"]), float(ohlc[-2]["close"]))
        ratios = daily_money_ratios(ohlc)
        money_ratio = ratios[-1][1] if ratios and ratios[-1][0] == d.isoformat() else None
        thr, hist_n = surge_threshold(ratios, d.isoformat())

        sector_chg, basket = {}, {}
        for code, sector in leaders.get(market, {}).items():
            r = latest.get((market, "stock", code))
            if r is not None and r["change_pct"] is not None:
                sector_chg[sector] = float(r["change_pct"])
                basket[sector] = code
        b = breadth_from_changes(sector_chg)
        if b.n < MIN_SAMPLE and not allow_no_breadth:
            print(f"  {market}: 대장주 종가 시세가 {b.n}개뿐(표본 {MIN_SAMPLE} 미만) — 급락·테마 판정이 불가해 기록 안 함."
                  " 폭 없이라도 남기려면 --allow-no-breadth")
            continue

        j = classify(chg, money_ratio, thr, b)
        print(f"  {market}: {j.label_kr} · 지수 {chg:+.2f}% · 폭 {b.down}↓/{b.up}↑/{b.n} · "
              f"대금배율 {'-' if money_ratio is None else f'{money_ratio:.2f}'} (기준 {thr and round(thr, 2)}, 표본 {hist_n})")
        rows.append({
            "ts": kst_iso(datetime.now(KST)), "as_of": kst_iso(datetime.combine(d, CLOSE_AS_OF, KST)),
            "market": market, "model_id": MODEL_ID, "scope": "rt", "horizon": "now", "basis": "eod",
            "state_code": j.state_code, "state_sectors": j.state_sectors, "state_conf": j.state_conf,
            "breadth_up": b.up, "breadth_down": b.down, "breadth_total": b.n, "label_kr": j.label_kr,
            "metrics": dict(j.metrics, basket=basket, surge_hist_n=hist_n), "rule_ver": RULE_VER,
        })
    return rows


def already_judged(db: Supa, d: date, market: str) -> bool:
    r = db.client.get(f"{db.url}/rest/v1/state_market_temp", headers=db.headers, params={
        "select": "id", "market": f"eq.{market}", "model_id": f"eq.{MODEL_ID}", "basis": "eq.eod",
        "as_of": f"eq.{kst_iso(datetime.combine(d, CLOSE_AS_OF, KST))}", "limit": 1})
    return r.status_code == 200 and bool(r.json())


def main() -> None:
    ap = argparse.ArgumentParser(description="종가 기준 하루 판정 (basis=eod)")
    ap.add_argument("--date", default=None, help="판정일 YYYY-MM-DD (기본: 오늘)")
    ap.add_argument("--dry-run", action="store_true", help="DB에 쓰지 않고 출력만")
    ap.add_argument("--allow-no-breadth", action="store_true", help="대장주 시세 없이 지수만으로라도 기록")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")   # Windows 콘솔(cp949)에서 '—' 등 출력 오류 방지

    d = date.fromisoformat(args.date) if args.date else datetime.now(KST).date()
    write = not args.dry_run
    db = Supa(write=write)
    leaders = load_leaders(db)
    print(f"[종가 판정] {d} ({'기록' if write else 'dry-run'})")
    rows = judge_day(db, d, leaders, args.allow_no_breadth)
    if not rows:
        sys.exit(1)   # 재료 부족으로 한 시장도 판정 못 함 — 예약 실행에서 실패로 보이게
    if write:
        fresh = []
        for r in rows:   # append 표라 재실행 시 같은 날 줄이 두 번 쌓이지 않게 막는다
            if already_judged(db, d, r["market"]):
                print(f"  {r['market']}: 이미 기록됨 — 건너뜀")
            else:
                fresh.append(r)
        db.insert("state_market_temp", fresh)
        print(f"[끝] {len(fresh)}줄 기록")


if __name__ == "__main__":
    main()
