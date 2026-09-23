"""
오딘 3.0 model_module — 매매모델 1호 `rule-trade-v1` 규칙 (순수 계산, DB·파일을 모른다).

한 줄 정체: **"매매 리스트에서 돈이 들어오는 종목을 따라가되, 꼭지·얇은 종목·급락장은 피하고, 며칠 안에 나온다."**
사람이 읽는 규칙 원문은 model_module/README.md "규칙" 절이고, 이 파일은 그 규칙을 그대로 코드로 옮긴 것이다.
숫자를 하나라도 바꾸면 RULE_VER를 올린다(기록과 대조할 때 어떤 규칙으로 판정했는지 알아야 하므로).

실행 시점: 거래일 아침(장 전). 입력은 전부 "직전 거래일 종가까지"의 것.
  - 매매 리스트: 오늘(list_date = 오늘) 아침 확정 리스트(rule-pick-v1, phase=open)
  - 시장 온도: 직전 거래일 종가 판정(rule-temp-rt-v1, basis=eod) + 주간 최신 판정(rule-temp-w-v0, as_of ≤ 직전 거래일 15:30)
  - 시세: 직전 거래일 종목 일봉(raw_stock_ohlc) — 보유 종목 평가·매수 수량 산정
  - 가상 계좌: 직전 실행이 남긴 현금·보유
출력: 오늘의 주문 의도(매수/매도, 수량, 왜) — **오늘 시가에 체결된다고 가정**하고 다음 실행에서 실제 시가로 가상 체결한다.

이 파일의 함수는 같은 입력이면 항상 같은 출력을 낸다(무작위·시계·네트워크 없음) — 주간 검토가 입력으로 다시 계산해 대조하는 근거.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

MODEL_ID = "rule-trade-v1"
RULE_VER = "trade-rules-v1"
LIST_MODEL_ID = "rule-pick-v1"        # 구독하는 매매 리스트 모델
STATE_RT_MODEL_ID = "rule-temp-rt-v1"  # 구독하는 시장 온도(실시간 어휘, 종가 판정 줄)
STATE_W_MODEL_ID = "rule-temp-w-v0"    # 구독하는 주간 무드(매도 태도)
MARKETS = ("KR-KOSPI", "KR-KOSDAQ")

# ── 규칙 숫자 (README "규칙" 절과 1:1) ──────────────────────────────────────
INITIAL_CASH = 10_000_000            # 가상 계좌 시작 현금(원)
MAX_POS_PER_MARKET = 3               # 시장별 최대 보유 종목 수
MAX_NEW_PER_MARKET = 2               # 하루 시장별 최대 신규 매수 수
POSITION_BUDGET = INITIAL_CASH // (MAX_POS_PER_MARKET * len(MARKETS))   # 한 종목 예산 = 1,666,666원(균등)
BUY_TAGS = {"new_high", "money_surge", "foreign_buy", "inst_buy", "both_buy"}   # "돈이 들어온다" 태그 — 하나라도 있어야 매수 후보
NO_BUY_TAGS = {"top_loser"}          # 하락 상위는 사지 않는다(평균회귀 모델이 아니다)
NO_BUY_WARNS = {"blowoff", "thin", "untradable"}   # 꼭지 추격·얇은 유동성·매수 불가는 제외(경고를 배제로 쓰는 건 이 모델의 선택)
RT_BLOCK_BUY = {"crash", "selloff"}  # 직전 거래일 종가 판정이 급락·투매면 오늘은 사지 않는다
RT_LIQUIDATE = {"crash"}             # 급락이면 전량 정리
W_LIQUIDATE = {"emergency"}          # 주간 긴급이면 전량 정리 + 매수 금지
W_TIGHT = {"downtrend", "emergency"} # 주간 하락장·긴급이면 매도 태도를 조인다
STOP_LOSS_PCT = {"normal": -5.0, "tight": -3.0}     # 손절선(직전 종가 대비 평균 매입가 수익률)
TAKE_PROFIT_PCT = 10.0                              # 익절선
MAX_HOLD_DAYS = {"normal": 5, "tight": 3}           # 보유 거래일 상한(매입일 포함해 센다)

# 사람용 규칙 이름표(기록·검토 표에 그대로 찍힌다). 기계는 rule_id로 매칭.
RULE_KR = {
    "G1_rt_crash_liquidate": "직전 거래일 급락 → 전량 매도",
    "G2_rt_block_buy": "직전 거래일 급락·투매 → 매수 금지",
    "G3_w_emergency": "주간 긴급 → 전량 매도 + 매수 금지",
    "G4_state_missing": "시장 온도 종가 판정 없음 → 매수 금지(보수적)",
    "A1_attitude": "주간 무드로 매도 태도 결정(normal/tight)",
    "X0_no_price": "보유 종목 직전 시세 없음 → 판단 보류(보유 유지)",
    "X1_stop_loss": "손절",
    "X2_take_profit": "익절",
    "X3_time_exit": "보유일 상한 도달 → 매도",
    "B1_buy_tag": "돈 유입 태그(new_high/money_surge/수급) 있어야 함",
    "B2_not_loser": "하락 상위(top_loser) 종목은 제외",
    "B3_no_warn": "blowoff/thin/untradable 경고 제외",
    "B4_up_day": "직전 거래일 등락률 > 0 이어야 함",
    "B5_not_held": "이미 보유 중이면 제외",
    "B6_price": "직전 거래일 종가 있어야 함(수량 산정)",
    "B7_slot": "시장별 빈 자리·하루 신규 상한 안에서 점수 순",
    "B8_qty": "예산으로 1주 이상 살 수 있어야 함",
    "S_fill": "가상 체결(다음 실행에서 시가로)",
}


@dataclass
class Result:
    """하루 실행의 결과 묶음(전부 JSON으로 저장 가능한 값)."""
    settlements: list[dict] = field(default_factory=list)   # 지난 의도의 가상 체결 결과
    gates: dict = field(default_factory=dict)                # 시장별 문(매수 허용/전량 정리/태도)
    evals: list[dict] = field(default_factory=list)          # 규칙 하나·종목 하나마다 한 줄(안 걸린 이유 포함)
    intents: list[dict] = field(default_factory=list)        # 오늘의 주문 의도
    account: dict = field(default_factory=dict)              # 체결 반영 후 계좌(현금·보유)
    notes: list[str] = field(default_factory=list)           # 사람용 메모(데이터 결손 등)


def pct(now: float, base: float) -> float:
    return (now / base - 1.0) * 100.0


# ── 1. 가상 체결: 지난 실행이 낸 의도를 그날 시가로 채운다 ───────────────────
def settle(pending: list[dict], bars: dict[str, dict[str, dict]], account: dict, trade_date: str) -> tuple[list[dict], dict]:
    """pending = 아직 체결 안 된 의도들(trade_date < 오늘). bars = {날짜: {종목코드: 일봉}}.
    체결 규칙: 그 의도의 날짜 시가. 시세 줄이 없으면 미체결(무효), 시가가 없으면(거래 없는 날) 미체결(무효).
    매수는 현금 한도로 수량을 줄일 수 있고, 매도는 보유 수량까지만. 수수료·세금은 0(가상, 수익을 채점하지 않으므로)."""
    cash = float(account["cash"])
    holdings = {h["stock_code"]: dict(h) for h in account.get("holdings", [])}
    out = []
    for it in sorted(pending, key=lambda x: (x["trade_date"], x["side"] != "sell", x["intent_id"])):   # 매도 먼저(현금 확보)
        if it["trade_date"] >= trade_date:
            continue                                     # 오늘 이후 의도는 아직 체결 시점이 아니다
        bar = (bars.get(it["trade_date"]) or {}).get(it["stock_code"])
        rec = {"intent_id": it["intent_id"], "stock_code": it["stock_code"], "side": it["side"], "trade_date": it["trade_date"],
               "fill_price": None, "fill_qty": 0, "status": None, "note": ""}
        if bar is None:
            rec.update(status="unfilled_no_data", note="그날 종목 일봉이 없어 체결 확인 불가 → 무효")
        elif bar.get("open") in (None, 0):
            rec.update(status="unfilled_no_trade", note="그날 시가 없음(거래 없는 날) → 무효")
        else:
            px = float(bar["open"])
            if it["side"] == "buy":
                qty = min(int(it["qty"]), math.floor(cash / px)) if px > 0 else 0
                if qty <= 0:
                    rec.update(status="unfilled_no_cash", note=f"현금 {cash:,.0f}원으로 시가 {px:,.0f}원 1주도 못 삼")
                else:
                    cash -= qty * px
                    h = holdings.get(it["stock_code"])
                    if h:
                        tot = h["qty"] + qty
                        h["avg_price"] = (h["avg_price"] * h["qty"] + px * qty) / tot
                        h["qty"] = tot
                    else:
                        holdings[it["stock_code"]] = {"stock_code": it["stock_code"], "market": it["market"], "qty": qty,
                                                      "avg_price": px, "entry_date": it["trade_date"], "entry_intent_id": it["intent_id"]}
                    note = "" if qty == int(it["qty"]) else f"현금 부족으로 수량 {it['qty']}→{qty}"
                    rec.update(status="filled", fill_price=px, fill_qty=qty, note=note)
            else:
                h = holdings.get(it["stock_code"])
                if not h or h["qty"] <= 0:
                    rec.update(status="unfilled_not_held", note="보유하지 않은 종목의 매도 의도 → 무효")
                else:
                    qty = min(int(it["qty"]), h["qty"])
                    cash += qty * px
                    h["qty"] -= qty
                    if h["qty"] == 0:
                        del holdings[it["stock_code"]]
                    rec.update(status="filled", fill_price=px, fill_qty=qty,
                               note=f"평균 매입가 {h['avg_price']:,.0f}원 대비 {pct(px, h['avg_price']):+.2f}%")
        out.append(rec)
    return out, {"cash": round(cash, 2), "holdings": sorted(holdings.values(), key=lambda h: h["stock_code"])}


# ── 2. 시장별 문(gate)과 매도 태도 ─────────────────────────────────────────
def gates(state_rt: dict, state_w: dict, evals: list[dict]) -> dict:
    """state_rt/state_w = {시장: 상태 줄 or None}. 시장마다 buy_allowed·liquidate·attitude를 정하고 evals에 근거를 남긴다."""
    out = {}
    for mk in MARKETS:
        rt = (state_rt or {}).get(mk)
        w = (state_w or {}).get(mk)
        rt_code = rt.get("state_code") if rt else None
        w_code = w.get("state_code") if w else None
        g = {"rt_state": rt_code, "rt_as_of": rt and rt.get("as_of"), "w_state": w_code, "w_as_of": w and w.get("as_of"),
             "buy_allowed": True, "liquidate": False, "attitude": "normal", "reasons": []}

        def ev(rule_id, fired, **detail):
            evals.append({"stage": "gate", "market": mk, "stock_code": None, "rule_id": rule_id, "fired": bool(fired),
                          "detail": dict(detail, rt_state=rt_code, w_state=w_code)})

        ev("G1_rt_crash_liquidate", rt_code in RT_LIQUIDATE)
        ev("G2_rt_block_buy", rt_code in RT_BLOCK_BUY)
        ev("G3_w_emergency", w_code in W_LIQUIDATE)
        ev("G4_state_missing", rt is None)
        if rt is None:
            g["buy_allowed"] = False
            g["reasons"].append("G4_state_missing")
        if rt_code in RT_BLOCK_BUY:
            g["buy_allowed"] = False
            g["reasons"].append("G2_rt_block_buy")
        if rt_code in RT_LIQUIDATE:
            g["liquidate"] = True
            g["reasons"].append("G1_rt_crash_liquidate")
        if w_code in W_LIQUIDATE:
            g["buy_allowed"] = False
            g["liquidate"] = True
            g["reasons"].append("G3_w_emergency")
        g["attitude"] = "tight" if w_code in W_TIGHT else "normal"
        ev("A1_attitude", g["attitude"] == "tight", attitude=g["attitude"], w_missing=w is None)
        out[mk] = g
    return out


# ── 3. 보유 종목 매도 판단 ──────────────────────────────────────────────────
def held_days(entry_date: str, calendar: list[str], prev_td: str) -> int:
    """매입일부터 직전 거래일까지의 거래일 수(매입일 포함). calendar = 거래일 목록(오름차순)."""
    return sum(1 for d in calendar if entry_date <= d <= prev_td)


def exits(account: dict, bars_prev: dict[str, dict], gates_: dict, calendar: list[str], prev_td: str, evals: list[dict]) -> list[dict]:
    intents = []
    for h in account.get("holdings", []):
        mk, code = h["market"], h["stock_code"]
        g = gates_.get(mk, {})
        att = g.get("attitude", "normal")
        bar = bars_prev.get(code)
        base = {"stage": "exit", "market": mk, "stock_code": code}
        if g.get("liquidate"):
            rule = "G1_rt_crash_liquidate" if "G1_rt_crash_liquidate" in g.get("reasons", []) else "G3_w_emergency"
            evals.append(dict(base, rule_id=rule, fired=True, detail={"qty": h["qty"]}))
            intents.append({"market": mk, "stock_code": code, "side": "sell", "qty": h["qty"],
                            "ref_price": bar and bar.get("close"), "rule_id": rule, "reason": {"gate": g.get("reasons")}})
            continue
        if bar is None or bar.get("close") in (None, 0):
            evals.append(dict(base, rule_id="X0_no_price", fired=True, detail={"note": "직전 거래일 일봉 없음 — 보유 유지"}))
            continue
        close = float(bar["close"])
        ret = pct(close, float(h["avg_price"]))
        days = held_days(h["entry_date"], calendar, prev_td)
        stop, maxd = STOP_LOSS_PCT[att], MAX_HOLD_DAYS[att]
        detail = {"close": close, "avg_price": h["avg_price"], "ret_pct": round(ret, 4), "held_days": days, "attitude": att,
                  "stop_pct": stop, "take_profit_pct": TAKE_PROFIT_PCT, "max_hold_days": maxd}
        fired = None
        checks = [("X1_stop_loss", ret <= stop), ("X2_take_profit", ret >= TAKE_PROFIT_PCT), ("X3_time_exit", days >= maxd)]
        for rule, hit in checks:
            evals.append(dict(base, rule_id=rule, fired=bool(hit and fired is None), detail=detail))
            if hit and fired is None:
                fired = rule
        if fired:
            intents.append({"market": mk, "stock_code": code, "side": "sell", "qty": h["qty"], "ref_price": close,
                            "rule_id": fired, "reason": detail})
    return intents


# ── 4. 매수 후보 → 매수 의도 ───────────────────────────────────────────────
def buys(list_rows: list[dict], account: dict, bars_prev: dict[str, dict], gates_: dict, evals: list[dict]) -> list[dict]:
    """리스트 종목마다 B1~B6 필터를 차례로 걸고(첫 탈락 규칙을 기록), 통과한 종목을 점수 순으로 빈 자리만큼 산다."""
    held = {h["stock_code"] for h in account.get("holdings", [])}
    held_by_mk = {mk: sum(1 for h in account.get("holdings", []) if h["market"] == mk) for mk in MARKETS}
    cash_left = float(account["cash"])
    intents = []
    for mk in MARKETS:
        g = gates_.get(mk, {})
        rows = sorted((r for r in list_rows if r["market"] == mk), key=lambda r: (-(r.get("score") or 0), r.get("rank_no") or 9999))
        passed = []
        for r in rows:
            code, reasons, warn = r["stock_code"], set(r.get("reasons") or []), set(r.get("warn") or [])
            m = r.get("metrics") or {}
            bar = bars_prev.get(code)
            chg1 = m.get("chg1_pct")
            checks = [
                ("B1_buy_tag", bool(reasons & BUY_TAGS), {"reasons": sorted(reasons), "buy_tags_hit": sorted(reasons & BUY_TAGS)}),
                ("B2_not_loser", not (reasons & NO_BUY_TAGS), {"reasons": sorted(reasons)}),
                ("B3_no_warn", not (warn & NO_BUY_WARNS), {"warn": sorted(warn)}),
                ("B4_up_day", chg1 is not None and float(chg1) > 0, {"chg1_pct": chg1}),
                ("B5_not_held", code not in held, {"held": code in held}),
                ("B6_price", bar is not None and bar.get("close") not in (None, 0), {"close": bar and bar.get("close")}),
            ]
            failed = None
            for rule, ok, detail in checks:
                evals.append({"stage": "candidate", "market": mk, "stock_code": code, "rule_id": rule, "fired": bool(ok),
                              "detail": dict(detail, rank_no=r.get("rank_no"), score=r.get("score"))})
                if not ok:
                    failed = rule
                    break
            if failed is None:
                passed.append(r)
        slots = max(0, min(MAX_NEW_PER_MARKET, MAX_POS_PER_MARKET - held_by_mk.get(mk, 0)))
        if not g.get("buy_allowed", False):
            slots = 0
        for i, r in enumerate(passed):
            code = r["stock_code"]
            in_slot = i < slots
            evals.append({"stage": "candidate", "market": mk, "stock_code": code, "rule_id": "B7_slot", "fired": in_slot,
                          "detail": {"order": i + 1, "slots": slots, "buy_allowed": g.get("buy_allowed"), "gate_reasons": g.get("reasons"),
                                     "held_in_market": held_by_mk.get(mk, 0), "score": r.get("score"), "rank_no": r.get("rank_no")}})
            if not in_slot:
                continue
            close = float(bars_prev[code]["close"])
            budget = min(POSITION_BUDGET, cash_left)
            qty = math.floor(budget / close)
            evals.append({"stage": "candidate", "market": mk, "stock_code": code, "rule_id": "B8_qty", "fired": qty >= 1,
                          "detail": {"budget": budget, "close": close, "qty": qty}})
            if qty < 1:
                continue
            cash_left -= qty * close
            intents.append({"market": mk, "stock_code": code, "side": "buy", "qty": qty, "ref_price": close, "rule_id": "B7_slot",
                            "reason": {"reasons": r.get("reasons"), "score": r.get("score"), "rank_no": r.get("rank_no"),
                                       "chg1_pct": (r.get("metrics") or {}).get("chg1_pct"), "budget": budget, "list_as_of": r.get("as_of")}})
    return intents


# ── 5. 하루 전체 ────────────────────────────────────────────────────────────
def run_day(inputs: dict) -> Result:
    """inputs(전부 값) → Result. 같은 inputs면 같은 Result. 키:
       trade_date, prev_td, calendar, list, state_rt, state_w, bars{날짜:{코드:일봉}}, account{cash,holdings}, pending_intents"""
    res = Result()
    td, prev_td = inputs["trade_date"], inputs["prev_td"]
    # 1) 가상 체결
    res.settlements, account = settle(inputs.get("pending_intents") or [], inputs.get("bars") or {}, inputs["account"], td)
    for s in res.settlements:
        res.evals.append({"stage": "settle", "market": None, "stock_code": s["stock_code"], "rule_id": "S_fill",
                          "fired": s["status"] == "filled", "detail": s})
    # 2) 문
    res.gates = gates(inputs.get("state_rt") or {}, inputs.get("state_w") or {}, res.evals)
    # 3) 매도
    bars_prev = (inputs.get("bars") or {}).get(prev_td) or {}
    sells = exits(account, bars_prev, res.gates, inputs.get("calendar") or [], prev_td, res.evals)
    # 4) 매수 — 리스트는 오늘 것·구독 모델·아침 확정 줄만(그 외는 걸러서 기록)
    list_rows, dropped = [], 0
    for r in inputs.get("list") or []:
        if r.get("list_date") == td and r.get("model_id") == LIST_MODEL_ID and r.get("phase") == "open" and r.get("market") in MARKETS:
            list_rows.append(r)
        else:
            dropped += 1
    if dropped:
        res.notes.append(f"리스트 줄 {dropped}개는 오늘 날짜·구독 모델·아침 확정이 아니어서 무시")
    if not list_rows:
        res.notes.append(f"{td} 매매 리스트 없음 → 매수 후보 0 (매도·체결만 처리)")
    buy_intents = buys(list_rows, account, bars_prev, res.gates, res.evals)
    for it in sells + buy_intents:
        it["trade_date"] = td
        res.intents.append(it)
    res.account = account
    return res
