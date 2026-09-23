"""
오딘 3.0 model_module — 주간 검토: 기간을 주면 실행 회차·의도·규칙 적중/위반/놓침·오류를 표로 뽑는다.

핵심 원칙: **저장된 결과를 믿지 않는다.** 회차마다 기록해 둔 입력 스냅샷(run_input)을 꺼내, 이 파일 안의 *독립 구현*
(trade_rules.py의 함수를 부르지 않고 규칙 숫자만 가져와 따로 짠 계산)으로 다시 계산해서 기록과 대조한다.
  · 적중  = 기록된 의도가 재계산과 같다(종목·방향·수량)
  · 위반  = 기록엔 있는데 재계산엔 없거나 수량이 다르다 / 문이 닫혔는데 매수 / 보유 상한 초과 / 리스트에 없는 종목 매수 …
  · 놓침  = 재계산으론 나와야 하는데 기록에 없다
  · 체결·현금도 다시 계산해 대조한다.
"안 돈 날"은 달력(평일·휴장일)과 run 표를 맞춰 보여준다 — 거래일인데 회차가 없으면 "실행 없음".

실행 (프로젝트 루트에서):
  uv run --project model_module python model_module/review_trade.py --from 2026-09-28 --to 2026-10-02
  uv run --project model_module python model_module/review_trade.py --from 2026-09-17 --to 2026-09-23 --db model_module/records/trial.sqlite --out review.md
  --refetch : 입력 스냅샷과 지금 DB를 비교해 "그때는 없었는데 지금은 있는" 늦은 데이터를 찍는다(네트워크 필요)
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import date

from kr_calendar import closed_reason, weekdays_between
from trade_rules import (BUY_TAGS, MAX_HOLD_DAYS, MAX_NEW_PER_MARKET, MAX_POS_PER_MARKET, NO_BUY_TAGS, NO_BUY_WARNS,
                         POSITION_BUDGET, RT_BLOCK_BUY, RT_LIQUIDATE, RULE_KR, STOP_LOSS_PCT, TAKE_PROFIT_PCT, W_LIQUIDATE, W_TIGHT,
                         LIST_MODEL_ID, MARKETS)
from trade_store import DEFAULT_DB, Store

WEEKDAY_KR = "월화수목금토일"


# ── 독립 재계산 (trade_rules의 함수를 쓰지 않는다 — 숫자 상수만 공유) ───────────
def recompute(inputs: dict) -> dict:
    """입력 스냅샷 → {fills:{intent_id: (status, price, qty)}, cash, holdings, gates, sells, buys}."""
    td, prev = inputs["trade_date"], inputs["prev_td"]
    bars = inputs.get("bars") or {}
    cash = float(inputs["account"]["cash"])
    hold = {h["stock_code"]: dict(h) for h in inputs["account"].get("holdings", [])}

    # 1) 체결: 매도 먼저, 날짜·번호 순
    fills = {}
    pend = [p for p in (inputs.get("pending_intents") or []) if p["trade_date"] < td]
    pend.sort(key=lambda p: (p["trade_date"], 0 if p["side"] == "sell" else 1, p["intent_id"]))
    for p in pend:
        b = (bars.get(p["trade_date"]) or {}).get(p["stock_code"])
        if b is None:
            fills[p["intent_id"]] = ("unfilled_no_data", None, 0)
        elif not b.get("open"):
            fills[p["intent_id"]] = ("unfilled_no_trade", None, 0)
        elif p["side"] == "buy":
            q = min(int(p["qty"]), int(cash // float(b["open"])))
            if q <= 0:
                fills[p["intent_id"]] = ("unfilled_no_cash", None, 0)
            else:
                cash -= q * float(b["open"])
                h = hold.get(p["stock_code"])
                if h:
                    h["avg_price"] = (h["avg_price"] * h["qty"] + float(b["open"]) * q) / (h["qty"] + q)
                    h["qty"] += q
                else:
                    hold[p["stock_code"]] = {"stock_code": p["stock_code"], "market": p["market"], "qty": q, "avg_price": float(b["open"]),
                                             "entry_date": p["trade_date"]}
                fills[p["intent_id"]] = ("filled", float(b["open"]), q)
        else:
            h = hold.get(p["stock_code"])
            if not h:
                fills[p["intent_id"]] = ("unfilled_not_held", None, 0)
            else:
                q = min(int(p["qty"]), h["qty"])
                cash += q * float(b["open"])
                h["qty"] -= q
                if h["qty"] == 0:
                    del hold[p["stock_code"]]
                fills[p["intent_id"]] = ("filled", float(b["open"]), q)

    # 2) 문
    gates = {}
    for mk in MARKETS:
        rt = (inputs.get("state_rt") or {}).get(mk)
        w = (inputs.get("state_w") or {}).get(mk)
        rt_c = rt["state_code"] if rt else None
        w_c = w["state_code"] if w else None
        gates[mk] = {"buy": rt is not None and rt_c not in RT_BLOCK_BUY and w_c not in W_LIQUIDATE,
                     "liq": rt_c in RT_LIQUIDATE or w_c in W_LIQUIDATE,
                     "att": "tight" if w_c in W_TIGHT else "normal"}

    # 3) 매도
    bars_prev = bars.get(prev) or {}
    cal = inputs.get("calendar") or []
    sells = []
    for h in hold.values():
        g = gates[h["market"]]
        b = bars_prev.get(h["stock_code"])
        if g["liq"]:
            sells.append((h["stock_code"], h["qty"], "liquidate"))
            continue
        if not b or not b.get("close"):
            continue
        ret = (float(b["close"]) / float(h["avg_price"]) - 1) * 100
        days = len([d for d in cal if h["entry_date"] <= d <= prev])
        if ret <= STOP_LOSS_PCT[g["att"]]:
            sells.append((h["stock_code"], h["qty"], "X1_stop_loss"))
        elif ret >= TAKE_PROFIT_PCT:
            sells.append((h["stock_code"], h["qty"], "X2_take_profit"))
        elif days >= MAX_HOLD_DAYS[g["att"]]:
            sells.append((h["stock_code"], h["qty"], "X3_time_exit"))

    # 4) 매수
    buys = []
    cash_left = cash
    rows = [r for r in (inputs.get("list") or []) if r.get("list_date") == td and r.get("model_id") == LIST_MODEL_ID and r.get("phase") == "open"]
    for mk in MARKETS:
        if not gates[mk]["buy"]:
            continue
        n_held = sum(1 for h in hold.values() if h["market"] == mk)
        slots = max(0, min(MAX_NEW_PER_MARKET, MAX_POS_PER_MARKET - n_held))
        cands = []
        for r in rows:
            if r["market"] != mk:
                continue
            rs, wn, m = set(r.get("reasons") or []), set(r.get("warn") or []), r.get("metrics") or {}
            b = bars_prev.get(r["stock_code"])
            ok = (rs & BUY_TAGS) and not (rs & NO_BUY_TAGS) and not (wn & NO_BUY_WARNS) and (m.get("chg1_pct") or 0) > 0 \
                and r["stock_code"] not in hold and b and b.get("close")
            if ok:
                cands.append(r)
        cands.sort(key=lambda r: (-(r.get("score") or 0), r.get("rank_no") or 9999))
        for r in cands[:slots]:
            close = float(bars_prev[r["stock_code"]]["close"])
            q = math.floor(min(POSITION_BUDGET, cash_left) / close)
            if q >= 1:
                cash_left -= q * close
                buys.append((r["stock_code"], q, "B7_slot"))
    return {"fills": fills, "cash": round(cash, 2), "holdings": hold, "gates": gates, "sells": sells, "buys": buys}


# ── 대조 ────────────────────────────────────────────────────────────────────
def compare_run(store: Store, run: dict) -> dict:
    rid = run["run_id"]
    inputs = store.inputs_of(rid)
    out = {"run_id": rid, "trade_date": run["trade_date"], "hits": [], "violations": [], "misses": [], "notes": []}
    if not inputs:
        out["notes"].append("입력 스냅샷 없음(오류·중복·휴장 회차)")
        return out
    exp = recompute(inputs)
    rec = store.intents_of_run(rid)
    list_codes = {r["stock_code"] for r in inputs.get("list") or [] if r.get("list_date") == run["trade_date"]}
    exp_set = {("sell", c, q) for c, q, _ in exp["sells"]} | {("buy", c, q) for c, q, _ in exp["buys"]}
    rec_set = set()
    for it in rec:
        key = (it["side"], it["stock_code"], it["qty"])
        rec_set.add(key)
        if key in exp_set:
            out["hits"].append(f"{it['side']} {it['stock_code']} {it['qty']}주 ({it['rule_id']})")
        else:
            same = [k for k in exp_set if k[0] == it["side"] and k[1] == it["stock_code"]]
            out["violations"].append(f"{it['side']} {it['stock_code']} {it['qty']}주 — 재계산 " + (f"수량 {same[0][2]}주" if same else "에 없음"))
        if it["side"] == "buy" and it["stock_code"] not in list_codes:
            out["violations"].append(f"buy {it['stock_code']} — 오늘 리스트에 없는 종목")
        if it["side"] == "buy" and not exp["gates"][it["market"]]["buy"]:
            out["violations"].append(f"buy {it['stock_code']} — 문 닫힘(매수 금지)인데 매수")
        if it["trade_date"] != run["trade_date"]:
            out["violations"].append(f"의도 #{it['intent_id']} trade_date {it['trade_date']} ≠ 회차 거래일")
    for k in exp_set - rec_set:
        out["misses"].append(f"{k[0]} {k[1]} {k[2]}주 — 재계산은 내는데 기록 없음")
    # 보유 상한
    acct = store.account_of_run(rid) or {"cash": None, "holdings": []}
    for mk in MARKETS:
        n = sum(1 for h in acct["holdings"] if h["market"] == mk) + sum(1 for it in rec if it["side"] == "buy" and it["market"] == mk)
        if n > MAX_POS_PER_MARKET:
            out["violations"].append(f"{mk} 보유+매수 {n}종목 > 상한 {MAX_POS_PER_MARKET}")
    # 체결·현금
    for it in store.settled_by_run(rid):
        e = exp["fills"].get(it["intent_id"])
        if e is None:
            out["violations"].append(f"체결 #{it['intent_id']} — 재계산엔 체결 대상이 아님")
        elif (it["status"], it["fill_price"] or None, it["fill_qty"] or 0) != (e[0], e[1], e[2]):
            out["violations"].append(f"체결 #{it['intent_id']} 기록 {it['status']}/{it['fill_price']}/{it['fill_qty']} ≠ 재계산 {e[0]}/{e[1]}/{e[2]}")
        else:
            out["hits"].append(f"체결 #{it['intent_id']} {it['status']}")
    if acct["cash"] is not None and abs(float(acct["cash"]) - exp["cash"]) > 0.01:
        out["violations"].append(f"현금 기록 {acct['cash']:,.0f} ≠ 재계산 {exp['cash']:,.0f}")
    return out


# ── 표 만들기 ───────────────────────────────────────────────────────────────
def md_table(header: list[str], rows: list[list]) -> str:
    esc = lambda v: str(v if v is not None else "").replace("|", "/").replace("\n", " ")   # noqa: E731
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(esc(c) for c in r) + " |" for r in rows]
    return "\n".join(lines)


def review(store: Store, d_from: date, d_to: date, refetch: bool = False) -> str:
    runs = store.runs_between(d_from.isoformat(), d_to.isoformat())
    by_date: dict[str, list[dict]] = {}
    for r in runs:
        by_date.setdefault(r["trade_date"], []).append(r)
    out = [f"# 매매모델 주간 검토 — {d_from} ~ {d_to}", "", f"기록 파일: `{store.path}`", ""]

    # 1. 회차
    rows, missing_days = [], []
    for d in weekdays_between(d_from, d_to):
        ds, wd = d.isoformat(), WEEKDAY_KR[d.weekday()]
        reason = closed_reason(d)
        rs = by_date.get(ds, [])
        if not rs:
            status = f"휴장({reason})" if reason else "**실행 없음**"
            if not reason:
                missing_days.append(ds)
            rows.append([ds, wd, status, "", "", "", "", "", ""])
            continue
        for r in rs:
            s = json.loads(r["summary"]) if r["summary"] else {}
            rows.append([ds, wd, r["status"] + (f"({reason})" if reason else ""), r["run_id"], r["mode"], (r["started_at"] or "")[11:19], r.get("prev_td") or "",
                         s.get("list_rows", ""), f"{s.get('buys', '')}/{s.get('sells', '')}/{s.get('filled', '')}" if s else ""])
    out += ["## 1. 실행 회차 (거래일마다 돌았나)", "", md_table(["날짜", "요일", "상태", "회차", "모드", "시작", "직전거래일", "리스트 줄", "매수/매도/체결"], rows), ""]
    if missing_days:
        out.append(f"거래일인데 실행 기록이 없는 날: **{', '.join(missing_days)}** (예약 실행이 안 돌았거나 컴퓨터가 꺼져 있었음)")
        out.append("")

    # 2. 의도
    irows = []
    for r in runs:
        names = (store.inputs_of(r["run_id"]).get("names") or {})
        for it in store.intents_of_run(r["run_id"]):
            reason = json.loads(it["reason"]) if it["reason"] else {}
            why = ("태그 " + "+".join(reason.get("reasons") or [])) if it["side"] == "buy" else \
                  (f"수익률 {reason.get('ret_pct')}% · {reason.get('held_days')}일 · {reason.get('attitude')}" if "ret_pct" in reason else json.dumps(reason, ensure_ascii=False)[:60])
            fill = it["status"] + (f" {it['fill_price']:,.0f}원x{it['fill_qty']}" if it["fill_price"] else "")
            irows.append([it["trade_date"], it["market"][3:], f"{it['stock_code']} {names.get(it['stock_code'], '')}", it["side"], it["qty"],
                          f"{it['ref_price']:,.0f}" if it["ref_price"] else "", f"{it['rule_id']} {RULE_KR.get(it['rule_id'], '')}", why, fill])
    out += ["## 2. 주문 의도와 가상 체결", "", md_table(["거래일", "시장", "종목", "방향", "수량", "기준가", "규칙", "근거", "체결"], irows) if irows else "(의도 없음)", ""]

    # 3. 대조
    crows, tot = [], {"hits": 0, "violations": 0, "misses": 0}
    details = []
    for r in runs:
        if r["status"] not in ("ok",):
            continue
        c = compare_run(store, r)
        for k in tot:
            tot[k] += len(c[k])
        crows.append([r["trade_date"], r["run_id"], len(c["hits"]), len(c["violations"]), len(c["misses"]), "; ".join(c["notes"])])
        for v in c["violations"]:
            details.append([r["trade_date"], "위반", v])
        for m in c["misses"]:
            details.append([r["trade_date"], "놓침", m])
    out += ["## 3. 규칙 대조 (입력 스냅샷으로 독립 재계산)", "",
            md_table(["거래일", "회차", "적중", "위반", "놓침", "메모"], crows) if crows else "(정상 회차 없음)", "",
            f"합계: 적중 {tot['hits']} · 위반 **{tot['violations']}** · 놓침 **{tot['misses']}**", ""]
    if details:
        out += [md_table(["거래일", "종류", "내용"], details), ""]

    # 4. 안 걸린 이유·데이터 결손 (규칙별 탈락 집계 + 메모)
    frows = []
    for r in runs:
        if r["status"] != "ok":
            continue
        evals = store.evals_of_run(r["run_id"])
        cand = [e for e in evals if e["stage"] == "candidate"]
        first_fail: dict[str, str] = {}
        for e in cand:                                    # 종목별 첫 탈락 규칙
            if not e["fired"] and e["stock_code"] not in first_fail and e["rule_id"] not in ("B7_slot",):
                first_fail[e["stock_code"]] = e["rule_id"]
            if e["rule_id"] == "B7_slot" and not e["fired"]:
                first_fail.setdefault(e["stock_code"], "B7_slot")
        from collections import Counter
        cnt = Counter(first_fail.values())
        gate_fired = [f"{e['market'][3:]}:{e['rule_id']}" for e in evals if e["stage"] == "gate" and e["fired"] and e["rule_id"] != "A1_attitude"]
        frows.append([r["trade_date"], len({e["stock_code"] for e in cand}), " · ".join(f"{k} {v}" for k, v in sorted(cnt.items())) or "-",
                      ", ".join(gate_fired) or "-"])
    out += ["## 4. 매수 후보가 걸러진 이유 (리스트 종목별 첫 탈락 규칙 개수) · 문 발동", "",
            md_table(["거래일", "리스트 종목", "탈락 규칙별 개수", "발동한 문"], frows) if frows else "(없음)", "",
            "규칙 이름: " + " / ".join(f"{k}={v}" for k, v in RULE_KR.items() if k.startswith(("B", "G"))), ""]
    erows = []
    for r in runs:
        for ev in store.events_of_run(r["run_id"]):
            erows.append([r["trade_date"], ev["level"], ev["msg"]])
    out += ["## 5. 데이터 결손·지연 처리 메모 (회차가 남긴 것)", "", md_table(["거래일", "수준", "메모"], erows) if erows else "(없음)", ""]

    # 6. 오류
    xrows = [[r["trade_date"], r["run_id"], r["status"], (r["error"] or "").split("\n")[0][:200]] for r in runs if r["status"] == "error"]
    out += ["## 6. 오류", "", md_table(["거래일", "회차", "상태", "오류"], xrows) if xrows else "(오류 회차 없음)", ""]

    # 7. 늦은 데이터(선택)
    if refetch:
        out += ["## 7. 입력 스냅샷 vs 지금 DB (늦게 들어온 데이터)", ""] + refetch_diff(store, runs) + [""]
    return "\n".join(out)


def refetch_diff(store: Store, runs: list[dict]) -> list[str]:
    from supa import Supa
    import trade_data as td
    db = Supa(write=False)
    rows = []
    for r in runs:
        if r["status"] != "ok":
            continue
        inp = store.inputs_of(r["run_id"])
        d, prev = date.fromisoformat(r["trade_date"]), date.fromisoformat(inp["prev_td"])
        now_list = td.load_list(db, d)
        now_rt = td.load_state_rt(db, prev)
        codes = {x["stock_code"] for x in inp.get("list") or []} | {h["stock_code"] for h in inp["account"].get("holdings", [])} | {x["stock_code"] for x in now_list}
        now_bars = td.load_bars(db, {prev.isoformat(): codes}) if codes else {prev.isoformat(): {}}
        then_bars = (inp.get("bars") or {}).get(prev.isoformat()) or {}
        diffs = []
        if len(now_list) != len(inp.get("list") or []):
            diffs.append(f"리스트 {len(inp.get('list') or [])}→{len(now_list)}줄")
        for mk in MARKETS:
            if (inp["state_rt"].get(mk) is None) != (now_rt.get(mk) is None):
                diffs.append(f"{mk} 종가 판정 {'없음→있음' if now_rt.get(mk) else '있음→없음'}")
        late = sorted(set(now_bars[prev.isoformat()]) - set(then_bars))
        if late:
            diffs.append(f"일봉 늦게 들어온 종목 {len(late)}개")
        rows.append([r["trade_date"], r["run_id"], "; ".join(diffs) or "차이 없음"])
    return [md_table(["거래일", "회차", "차이"], rows) if rows else "(비교할 회차 없음)"]


def main() -> None:
    ap = argparse.ArgumentParser(description="매매모델 주간 검토 — 실행 회차·의도·규칙 적중/위반/놓침·오류 표")
    ap.add_argument("--from", dest="d_from", required=True)
    ap.add_argument("--to", dest="d_to", required=True)
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--out", help="마크다운 파일로도 저장")
    ap.add_argument("--refetch", action="store_true", help="입력 스냅샷과 지금 DB를 비교(네트워크)")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    text = review(Store(args.db), date.fromisoformat(args.d_from), date.fromisoformat(args.d_to), args.refetch)
    print(text)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"\n[저장] {args.out}")


if __name__ == "__main__":
    main()
