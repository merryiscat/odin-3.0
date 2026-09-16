"""
오딘 3.0 list_module — 매매 리스트 채점 (수익 채점 아님 — "볼 가치가 있는 종목을 놓치지 않았나").

잣대(2026-09-17 설계):
  · 포착률  — 그날(list_date) 실제로 크게 움직인 종목(당일 진폭 상위 5% · 대금 배율 상위 5% · 상한가 마감, 유니버스 안)이
              아침 리스트에 몇 % 들어 있었나.
  · 알맹이 비율 — 리스트 종목 중 그날 진폭 3% 이상 · 대금 배율 2배 이상인 비율.
  · 렌즈별 같은 지표(어느 렌즈가 실제로 움직이는 종목을 잡나).
  · D+1·D+5 수익은 **기록만**(매매모델 측정의 재료) — 잣대 아님.
기준선 3개(무작위·시총 상위·어제 그대로)와 나란히 놓는다.

입력: run_pick.py --from/--to --out 이 만든 JSON({기준일: {model_id: {market: [picks]}}}) + raw_stock_ohlc(그날 실제 값).
실행: uv run --project list_module python list_module/measure_pick.py --picks picks.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import date, timedelta
from statistics import mean

import pick_rules as pr
import stock_params as sp
from run_params import load_ohlc, load_stocks
from run_pick import next_trading_day
from supa import Supa

MEAT_SHARE = 0.2                    # 알맹이: 그날 유니버스에서 진폭 상위 20% 또는 대금 배율 상위 20%
                                    #   (절대 문턱 3%는 이 시장에선 무작위 종목의 68%가 넘어 잣대가 안 됐음 — 2026-09-17 실측, 백분위로 교체)
TOP_SHARE = 0.05                    # 포착 대상: 상위 5%


def main() -> None:
    ap = argparse.ArgumentParser(description="매매 리스트 채점(포착률·알맹이 비율)")
    ap.add_argument("--picks", required=True)
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    with open(args.picks, encoding="utf-8") as f:
        picks = json.load(f)
    days = sorted(picks)
    db = Supa(write=False)
    stocks = load_stocks(db)
    d0, d1 = date.fromisoformat(days[0]) - timedelta(days=40), next_trading_day(date.fromisoformat(days[-1])) + timedelta(days=12)
    print(f"[읽기] 일봉 {d0}~{d1}")
    ohlc = load_ohlc(db, d0, d1)
    # 날짜별 인덱스
    by_date: dict[str, dict[str, dict]] = defaultdict(dict)
    dates_of: dict[str, list[str]] = {}
    for code, rows in ohlc.items():
        dates_of[code] = [r["date"] for r in rows]
        for r in rows:
            by_date[r["date"]][code] = r

    def realized(code: str, ld: str) -> dict | None:
        """list_date 그날의 실제 움직임 + 뒤 수익(기록용)."""
        rows = ohlc.get(code)
        if not rows or ld not in dates_of[code]:
            return None
        i = dates_of[code].index(ld)
        r = rows[i]
        pc = float(r["prev_close"]) if r.get("prev_close") else (float(rows[i - 1]["close"]) if i else None)
        if not pc:
            return None
        rng = ((float(r["high"]) - float(r["low"])) / pc * 100) if r.get("high") and r.get("low") else None
        prev = [x.get("trade_value") for x in rows[max(0, i - 20):i]]
        avg = mean(v for v in prev if v is not None) if len(prev) == 20 and all(v is not None for v in prev) else None
        mr = (r["trade_value"] / avg) if avg and r.get("trade_value") is not None else None
        out = {"range": rng, "money_ratio": mr, "limit_up": sp.is_limit_up(r), "chg": float(r["close"]) / pc * 100 - 100}
        out["ret1"] = (float(rows[i + 1]["close"]) / float(r["close"]) - 1) * 100 if i + 1 < len(rows) else None
        out["ret5"] = (float(rows[i + 5]["close"]) / float(r["close"]) - 1) * 100 if i + 5 < len(rows) else None
        return out

    models = sorted({m for d in picks.values() for m in d})
    agg = {m: defaultdict(list) for m in models}
    lens_agg = defaultdict(list)
    n_days = 0
    for ds in days:
        ld = next_trading_day(date.fromisoformat(ds)).isoformat()
        if ld not in by_date:
            continue
        n_days += 1
        for mk in ("KR-KOSPI", "KR-KOSDAQ"):
            # 그날 유니버스 안 종목의 실제 움직임 → 큰 움직임 집합
            uni_codes = [c for c, s in stocks.items() if s.get("type") == "보통주" and s.get("status") not in pr.UNIVERSE_EXCL_STATUS
                         and s.get("market") == mk.replace("KR-", "") and c in by_date[ld]]
            real = {c: realized(c, ld) for c in uni_codes}
            real = {c: v for c, v in real.items() if v}
            k = max(1, int(len(real) * TOP_SHARE))
            big_rng = set(sorted((c for c in real if real[c]["range"] is not None), key=lambda c: -real[c]["range"])[:k])
            big_mr = set(sorted((c for c in real if real[c]["money_ratio"] is not None), key=lambda c: -real[c]["money_ratio"])[:k])
            big_lu = {c for c in real if real[c]["limit_up"]}
            big = big_rng | big_mr | big_lu
            km = max(1, int(len(real) * MEAT_SHARE))
            meat_set = set(sorted((c for c in real if real[c]["range"] is not None), key=lambda c: -real[c]["range"])[:km]) | \
                       set(sorted((c for c in real if real[c]["money_ratio"] is not None), key=lambda c: -real[c]["money_ratio"])[:km])
            is_meat = lambda c: 1.0 if c in meat_set else 0.0   # noqa: E731
            for m in models:
                lst = picks[ds].get(m, {}).get(mk) or []
                codes = [x["stock_code"] for x in lst]
                if not codes:
                    continue
                a = agg[m]
                a["n"].append(len(codes))
                a["catch_all"].append(len(big & set(codes)) / len(big) if big else None)
                a["catch_lu"].append(len(big_lu & set(codes)) / len(big_lu) if big_lu else None)
                in_real = [c for c in codes if c in real]
                rs = [real[c] for c in in_real]
                a["meat"].append(mean(is_meat(c) for c in in_real) if in_real else None)
                a["range"].append(mean(v["range"] for v in rs if v["range"] is not None) if rs else None)
                a["ret1"].extend(v["ret1"] for v in rs if v["ret1"] is not None)
                a["ret5"].extend(v["ret5"] for v in rs if v["ret5"] is not None)
                if m == pr.MODEL_ID:
                    for x in lst:
                        if x["stock_code"] in real:
                            for l in x["reasons"]:
                                lens_agg[l].append(is_meat(x["stock_code"]))
            # 유니버스 기준(전체 평균 알맹이 비율 = 정의상 약 36%)
            a = agg.setdefault("(유니버스 전체)", defaultdict(list))
            a["n"].append(len(real))
            a["meat"].append(mean(is_meat(c) for c in real))
            a["range"].append(mean(v["range"] for v in real.values() if v["range"] is not None))
            a["ret1"].extend(v["ret1"] for v in real.values() if v["ret1"] is not None)
            a["ret5"].extend(v["ret5"] for v in real.values() if v["ret5"] is not None)

    def m(xs):
        xs = [x for x in (xs or []) if x is not None]
        return mean(xs) if xs else None
    fmt = lambda v, p=1: "—" if v is None else f"{v * 100:.{p}f}%"  # noqa: E731
    print(f"\n### 채점 — {n_days}거래일 (list_date 기준), 시장 2개 합산 평균")
    print(f"{'모델':<28}{'종목수':>6}{'포착률(큰움직임)':>16}{'상한가 포착':>12}{'알맹이 비율':>12}{'당일 진폭':>10}{'D+1':>8}{'D+5':>8}")
    for name, a in agg.items():
        print(f"{name:<28}{m(a['n']) or 0:>6.0f}{fmt(m(a.get('catch_all'))):>16}{fmt(m(a.get('catch_lu'))):>12}{fmt(m(a['meat'])):>12}"
              f"{(m(a['range']) or 0):>9.2f}%{(m(a['ret1']) or 0):>+7.2f}%{(m(a['ret5']) or 0):>+7.2f}%")
    print("\n### rule-pick-v1 렌즈별 알맹이 비율(그 렌즈로 뽑힌 종목이 그날 실제로 움직인 비율)")
    for l, v in sorted(lens_agg.items(), key=lambda kv: -len(kv[1])):
        print(f"  {l:<16}{len(v):>6}건  {mean(v) * 100:5.1f}%")


if __name__ == "__main__":
    main()
