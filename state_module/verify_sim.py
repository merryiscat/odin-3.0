"""
오딘 3.0 state_module — 판별기 코드 검증: 2026-09-14에 DB에 적재한 과거 2년 종가 판정(996줄)을
이 코드(rt_rules·rt_params)로 다시 만들어 한 줄씩 대조한다. 읽기만 한다.

두 가지 모드:
  --threshold global   : 첫 시뮬레이션과 같은 방식(대금 폭증 기준을 2년 전체 분포로) → 불일치 0이어야 정상
  --threshold trailing : 라이브 방식(판정일 이전 1년만) → 첫 시뮬레이션의 미래 참조가 바꾼 날을 보여줌

한계(정직하게): 대장주 개별 등락률은 3.0 DB에 없어 저장된 metrics의 폭 집계(n·up·down·u05·d05)를
그대로 쓴다. 역행 상승 업종도 저장된 state_sectors로 대신한다 → 급락·급등·우선순위·지수 규칙은 검증되지만,
"어떤 업종이 +1% 넘었나" 계산 자체는 여기서 검증되지 않는다(tests/에서 따로 검증).

실행: uv run --project state_module python state_module/verify_sim.py --threshold global
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict

from rt_params import daily_money_ratios, pct_change, surge_threshold, upper_decile
from rt_rules import MODEL_ID, Breadth, classify
from supa import Supa


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--threshold", choices=["global", "trailing"], default="global")
    ap.add_argument("--show", type=int, default=15, help="불일치 예시 몇 줄 보여줄지")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    db = Supa(write=False)
    stored = db.select("state_market_temp", {
        "select": "as_of,market,state_code,state_sectors,metrics", "model_id": f"eq.{MODEL_ID}",
        "basis": "eq.eod", "order": "as_of,market"})
    ohlc = db.select("raw_index_ohlc", {"select": "date,market,close,trade_value", "order": "date,market"})
    by_mk: dict[str, list[dict]] = defaultdict(list)
    for r in ohlc:
        by_mk[r["market"]].append(r)

    idx = {}
    for mk, rows in by_mk.items():
        ratios = daily_money_ratios(rows)
        ratio_of = dict(ratios)
        glob = upper_decile([v for _, v in ratios])
        for i in range(1, len(rows)):
            d = rows[i]["date"]
            thr = glob if args.threshold == "global" else surge_threshold(ratios, d)[0]
            idx[(d, mk)] = (pct_change(float(rows[i]["close"]), float(rows[i - 1]["close"])), ratio_of.get(d), thr)

    bad, changes = [], Counter()
    for s in stored:
        d, mk, m = s["as_of"][:10], s["market"], s["metrics"]
        chg, money, thr = idx[(d, mk)]
        b = Breadth(n=m["n"] or 0, up=m["up"] or 0, down=m["down"] or 0, u05=m["u05"] or 0, d05=m["d05"] or 0,
                    rising_sectors=s["state_sectors"] or [])
        got = classify(chg, money, thr, b).state_code
        if got != s["state_code"]:
            bad.append((d, mk, s["state_code"], got, round(chg, 2), money and round(money, 3), thr and round(thr, 3)))
            changes[(s["state_code"], got)] += 1

    print(f"[대조] 저장된 판정 {len(stored)}줄 · 대금 기준={args.threshold} · 불일치 {len(bad)}줄")
    for c, n in changes.most_common():
        print(f"  {c[0]} → {c[1]}: {n}줄")
    for row in bad[:args.show]:
        print("  ", row)
    if args.threshold == "global" and bad:
        sys.exit(1)


if __name__ == "__main__":
    main()
