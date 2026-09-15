"""
오딘 3.0 state_module — 주간 시장 온도 종가 판정·과거 백필 (scope=w, basis=eod, 초안 모델 rule-temp-w-v0).

재료: raw_index_ohlc 지수 일봉(고가·저가·종가). 규칙은 w_rules.py 하나.
운영에서는 주간도 장중 실시간으로 재판정한다(README 확정) — 장중 루프 연결은 이 브랜치 머지 후
실시간 판별기(run_rt.py)와 맞춰 붙인다. 이 스크립트는 종가 기준 판정과 과거 백필용.

실행 (프로젝트 루트에서):
  uv run --project state_module python state_module/run_w.py --dry-run                        # 최신 거래일
  uv run --project state_module python state_module/run_w.py --from 2025-01-01 --dry-run      # 과거 백필 미리보기
  uv run --project state_module python state_module/run_w.py --from 2025-01-01 --out w.json --dry-run
  uv run --project state_module python state_module/run_w.py --from 2024-08-22               # 실제 기록(서비스 키 필요)
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, time

from rt_data import KST, MARKETS, kst_iso
from supa import Supa
from w_rules import MODEL_ID, RULE_VER, judge_series

CLOSE_AS_OF = time(15, 30)


def load_ohlc_full(db: Supa, market: str, until: date) -> list[dict]:
    """지수 일봉 전체(until 포함, 오름차순) — 1년 진폭 분포를 만들려면 판정일보다 1년 이상 앞부터 필요."""
    return db.select("raw_index_ohlc", {
        "select": "date,high,low,close", "market": f"eq.{market}",
        "date": f"lte.{until.isoformat()}", "order": "date",
    })


def existing_as_of(db: Supa, market: str) -> set[str]:
    rows = db.select("state_market_temp", {
        "select": "as_of", "market": f"eq.{market}", "model_id": f"eq.{MODEL_ID}",
        "scope": "eq.w", "basis": "eq.eod", "order": "as_of",
    })
    return {datetime.fromisoformat(r["as_of"]).astimezone(KST).date().isoformat() for r in rows}


def build_rows(db: Supa, start: date | None, until: date) -> list[dict]:
    rows = []
    for market in MARKETS:
        ohlc = load_ohlc_full(db, market, until)
        series = judge_series(ohlc)
        if start is None:            # 기본: 최신 거래일 하루
            series = series[-1:]
        else:
            series = [(d, j) for d, j in series if d >= start.isoformat()]
        for d, j in series:
            m = j.metrics
            rows.append({
                "ts": kst_iso(datetime.now(KST)),
                "as_of": kst_iso(datetime.combine(date.fromisoformat(d), CLOSE_AS_OF, KST)),
                "market": market, "model_id": MODEL_ID, "scope": "w", "horizon": "now", "basis": "eod",
                "state_code": j.state_code, "state_sectors": None, "state_conf": None,
                "breadth_up": None, "breadth_down": None, "breadth_total": None,
                "label_kr": j.label_kr, "metrics": m, "rule_ver": RULE_VER,
            })
        if series:
            d, j = series[-1]
            m = j.metrics
            print(f"  {market}: {len(series)}일 판정 · 마지막 {d} {j.label_kr} "
                  f"(1주 {m['chg5']:+.2f}% · 장중최저 {m['low_pct_today']:+.2f}% · "
                  f"5일진폭 {m['rng5']:.2f}% / 기준 {m['rng_threshold']})")
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description="주간 시장 온도 종가 판정·백필 (scope=w)")
    ap.add_argument("--from", dest="start", default=None, help="이 날짜부터 백필 YYYY-MM-DD (없으면 최신 거래일 하루)")
    ap.add_argument("--to", dest="until", default=None, help="이 날짜까지 YYYY-MM-DD (기본: 오늘)")
    ap.add_argument("--out", default=None, help="판정 줄을 JSON 파일로도 저장(화면 검증용)")
    ap.add_argument("--dry-run", action="store_true", help="DB에 쓰지 않고 출력만")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    until = date.fromisoformat(args.until) if args.until else datetime.now(KST).date()
    start = date.fromisoformat(args.start) if args.start else None
    write = not args.dry_run
    db = Supa(write=write)
    print(f"[주간 판정] {start or '최신'} ~ {until} ({'기록' if write else 'dry-run'})")
    rows = build_rows(db, start, until)
    if not rows:
        sys.exit(1)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False, indent=1)
        print(f"  JSON 저장: {args.out} ({len(rows)}줄)")
    if write:
        fresh = []
        for market in MARKETS:   # append 표라 재실행 시 같은 날 줄이 두 번 쌓이지 않게 막는다
            done = existing_as_of(db, market)
            fresh += [r for r in rows if r["market"] == market
                      and datetime.fromisoformat(r["as_of"]).date().isoformat() not in done]
        for i in range(0, len(fresh), 500):
            db.insert("state_market_temp", fresh[i:i + 500])
        print(f"[끝] {len(fresh)}줄 기록 (이미 있던 {len(rows) - len(fresh)}줄 건너뜀)")


if __name__ == "__main__":
    main()
