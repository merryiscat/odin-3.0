"""
오딘 3.0 model_module — 매매모델 1호 `rule-trade-v1` 하루 실행기 (거래일 아침, 장 전).

하는 일(순서대로):
  1) 오늘이 거래일인지(주말·휴장일 정적 달력) · 오늘 이미 돌았는지 확인 → 아니면 그 사실을 기록하고 끝
  2) DB에서 입력을 읽는다: 오늘 매매 리스트 · 직전 거래일 시장 온도(종가) · 주간 무드 · 직전 거래일 종목 일봉 · 지난 의도의 그날 시가
  3) 입력 스냅샷을 통째로 기록(run_input) → 규칙 실행(trade_rules.run_day) → 가상 체결·규칙 근거·주문 의도·계좌를 기록
  4) 오류가 나면 run.status=error + 오류 문구를 남기고 종료 코드 1(예약 실행에서 실패로 보이게)

실행 (프로젝트 루트에서):
  uv run --project model_module python model_module/run_trade.py                       # 오늘(예약 실행용, mode=live)
  uv run --project model_module python model_module/run_trade.py --date 2026-09-17 --db model_module/records/trial.sqlite
  uv run --project model_module python model_module/run_trade.py --date 2026-09-17 --dry-run   # 기록 안 하고 출력만
기록 파일 기본 위치: model_module/records/rule-trade-v1.sqlite  (주간 검토: review_trade.py)
"""

from __future__ import annotations

import argparse
import sys
import traceback
from datetime import date, datetime, timedelta

import trade_data as td
import trade_rules as tr
from kr_calendar import closed_reason, is_trading_day
from supa import Supa
from trade_store import DEFAULT_DB, KST, Store

CALENDAR_LOOKBACK_DAYS = 90   # 보유일수 계산용 거래일 목록 범위(보유 상한 5일이라 넉넉)


def prev_trading_day(d: date) -> date:
    p = d - timedelta(days=1)
    while not is_trading_day(p):
        p -= timedelta(days=1)
    return p


def trading_days_until(prev_td: date, lookback: int = CALENDAR_LOOKBACK_DAYS) -> list[str]:
    out, d = [], prev_td - timedelta(days=lookback)
    while d <= prev_td:
        if is_trading_day(d):
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def build_inputs(db: Supa, store: Store, d: date) -> tuple[dict, dict]:
    """DB + 기록 파일에서 오늘 실행의 입력을 모은다. (inputs, as_of 표) 반환."""
    prev_td = prev_trading_day(d)
    account, base_run = store.latest_account(tr.INITIAL_CASH)
    pending = store.pending_intents()
    list_rows = td.load_list(db, d)
    state_rt = td.load_state_rt(db, prev_td)
    state_w = td.load_state_w(db, prev_td)
    need: dict[str, set[str]] = {prev_td.isoformat(): set()}
    need[prev_td.isoformat()] |= {r["stock_code"] for r in list_rows} | {h["stock_code"] for h in account["holdings"]}
    for it in pending:
        if it["trade_date"] < d.isoformat():
            need.setdefault(it["trade_date"], set()).add(it["stock_code"])
    bars = td.load_bars(db, need)
    names = td.load_names(db, {c for s in need.values() for c in s})
    inputs = {
        "trade_date": d.isoformat(), "prev_td": prev_td.isoformat(), "calendar": trading_days_until(prev_td),
        "list": list_rows, "state_rt": state_rt, "state_w": state_w, "bars": bars,
        "account": account, "pending_intents": pending, "names": names,
        "meta": {"base_account_run_id": base_run, "list_model_id": tr.LIST_MODEL_ID, "state_rt_model_id": tr.STATE_RT_MODEL_ID,
                 "state_w_model_id": tr.STATE_W_MODEL_ID, "initial_cash": tr.INITIAL_CASH},
    }
    as_of = {"list": list_rows[0]["as_of"] if list_rows else None,
             "state_rt": next((v["as_of"] for v in state_rt.values() if v), None),
             "state_w": next((v["as_of"] for v in state_w.values() if v), None),
             "bars": prev_td.isoformat()}
    return inputs, as_of


def data_notes(inputs: dict) -> list[str]:
    """입력 결손·지연을 사람용 문장으로(기록 event에 남긴다)."""
    notes = []
    prev = inputs["prev_td"]
    if not inputs["list"]:
        notes.append(f"매매 리스트 없음: pick_list에 list_date={inputs['trade_date']}·{tr.LIST_MODEL_ID}·phase=open 줄이 없다(리스트 밤 배치 미실행?)")
    for mk in tr.MARKETS:
        if inputs["state_rt"].get(mk) is None:
            notes.append(f"{mk} 시장 온도 종가 판정 없음: state_market_temp에 as_of={prev} 15:30·{tr.STATE_RT_MODEL_ID}·eod 줄이 없다(밤 배치 미실행?) → 매수 금지")
        w = inputs["state_w"].get(mk)
        if w is None:
            notes.append(f"{mk} 주간 무드 없음 → 매도 태도 normal로 처리")
        elif w.get("stale"):
            notes.append(f"{mk} 주간 무드가 오래됨(as_of {w['as_of'][:10]}, 직전 거래일 {prev}) → 그 값으로 처리하되 stale 표시")
    bars_prev = inputs["bars"].get(prev) or {}
    codes = {r["stock_code"] for r in inputs["list"]} | {h["stock_code"] for h in inputs["account"]["holdings"]}
    missing = sorted(codes - set(bars_prev))
    if codes and not bars_prev:
        notes.append(f"직전 거래일({prev}) 종목 일봉이 하나도 없다(15:40 종가 스냅샷 미실행?) → 매수 수량 산정·보유 평가 불가")
    elif missing:
        notes.append(f"직전 거래일({prev}) 일봉 없는 종목 {len(missing)}개: {', '.join(missing[:10])}{' …' if len(missing) > 10 else ''}")
    for it in inputs["pending_intents"]:
        if it["trade_date"] < inputs["trade_date"] and it["stock_code"] not in (inputs["bars"].get(it["trade_date"]) or {}):
            notes.append(f"의도 #{it['intent_id']} {it['stock_code']} {it['side']}({it['trade_date']}) 그날 일봉 없음 → 미체결 무효 처리")
    return notes


def print_result(inputs: dict, res: tr.Result) -> None:
    names = inputs.get("names") or {}
    nm = lambda c: f"{c} {names.get(c, '')}".strip()   # noqa: E731
    print(f"[매매모델 {tr.MODEL_ID}] 거래일 {inputs['trade_date']} · 직전 거래일 {inputs['prev_td']} · 리스트 {len(inputs['list'])}종목")
    for mk, g in res.gates.items():
        print(f"  문 {mk}: 실시간 {g['rt_state']} / 주간 {g['w_state']} → 매수 {'허용' if g['buy_allowed'] else '금지'}"
              f"{' · 전량 정리' if g['liquidate'] else ''} · 매도 태도 {g['attitude']}{(' · ' + ','.join(g['reasons'])) if g['reasons'] else ''}")
    for s in res.settlements:
        px = f"{s['fill_price']:,.0f}원 x {s['fill_qty']}" if s["fill_price"] else "-"
        print(f"  체결 #{s['intent_id']} {nm(s['stock_code'])} {s['side']} {s['trade_date']} → {s['status']} {px} {s['note']}")
    if not res.intents:
        print("  주문 의도: 없음(관망)")
    for it in res.intents:
        ref = f"{it['ref_price']:,.0f}" if it.get("ref_price") else "-"
        why = f"태그 {'+'.join(it['reason'].get('reasons') or [])}" if it["side"] == "buy" else f"수익률 {it['reason'].get('ret_pct', '-')}%"
        print(f"  의도 {it['side']:4} {nm(it['stock_code'])} {it['qty']}주 @직전종가 {ref} · {it['rule_id']} {tr.RULE_KR.get(it['rule_id'], '')} · {why}")
    a = res.account
    print(f"  계좌: 현금 {a['cash']:,.0f}원 · 보유 {len(a['holdings'])}종목 " + ", ".join(f"{nm(h['stock_code'])} {h['qty']}주@{h['avg_price']:,.0f}" for h in a["holdings"]))
    for n in res.notes:
        print(f"  메모: {n}")


def main() -> int:
    ap = argparse.ArgumentParser(description="매매모델 1호 rule-trade-v1 — 하루 실행(주문 의도 + 가상 체결 기록)")
    ap.add_argument("--date", help="거래일 YYYY-MM-DD (기본: 오늘, 한국시간)")
    ap.add_argument("--db", default=str(DEFAULT_DB), help="기록 SQLite 파일 경로")
    ap.add_argument("--dry-run", action="store_true", help="기록하지 않고 출력만")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    d = date.fromisoformat(args.date) if args.date else datetime.now(KST).date()
    mode = "dry" if args.dry_run else ("manual" if args.date else "live")
    store = Store(args.db)

    reason = closed_reason(d)
    if reason:
        print(f"[휴장] {d} {reason} — 실행할 것 없음")
        if not args.dry_run:
            rid = store.start_run(tr.MODEL_ID, tr.RULE_VER, d.isoformat(), mode)
            store.finish_run(rid, "holiday", summary={"reason": reason})
        return 0
    if store.has_ok_run(d.isoformat()) and not args.dry_run:
        print(f"[중복] {d} 은 이미 정상 실행됨 — 두 번 돌지 않는다(기록만 남김)")
        rid = store.start_run(tr.MODEL_ID, tr.RULE_VER, d.isoformat(), mode)
        store.finish_run(rid, "skipped_duplicate")
        return 0

    rid = None if args.dry_run else store.start_run(tr.MODEL_ID, tr.RULE_VER, d.isoformat(), mode)
    try:
        db = Supa(write=False)
        inputs, as_of = build_inputs(db, store, d)
        notes = data_notes(inputs)
        res = tr.run_day(inputs)
        res.notes = notes + res.notes
        print_result(inputs, res)
        if args.dry_run:
            print("[dry-run] 기록하지 않음")
            return 0
        store.save_inputs(rid, inputs, as_of)
        store.apply_settlements(rid, res.settlements)
        store.save_evals(rid, res.evals)
        store.save_intents(rid, res.intents)
        store.save_account(rid, res.account)
        for n in res.notes:
            store.event(rid, "warn", n)
        summary = {"intents": len(res.intents), "buys": sum(1 for i in res.intents if i["side"] == "buy"),
                   "sells": sum(1 for i in res.intents if i["side"] == "sell"),
                   "filled": sum(1 for s in res.settlements if s["status"] == "filled"),
                   "unfilled": sum(1 for s in res.settlements if s["status"] != "filled"),
                   "gates": {mk: {"rt": g["rt_state"], "w": g["w_state"], "buy_allowed": g["buy_allowed"], "attitude": g["attitude"]} for mk, g in res.gates.items()},
                   "list_rows": len(inputs["list"]), "cash": res.account["cash"], "holdings": len(res.account["holdings"])}
        store.finish_run(rid, "ok", prev_td=inputs["prev_td"], summary=summary)
        print(f"[끝] 회차 #{rid} 기록 → {store.path}")
        return 0
    except Exception as e:   # noqa: BLE001 — 무슨 오류든 기록에 남기는 것이 목적
        msg = f"{type(e).__name__}: {e}"
        print(f"[오류] {msg}")
        traceback.print_exc()
        if rid is not None:
            store.finish_run(rid, "error", error=msg + "\n" + traceback.format_exc())
            store.event(rid, "error", msg)
        return 1


if __name__ == "__main__":
    sys.exit(main())
