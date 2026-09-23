"""매매모델 1호 규칙·기록·검토 테스트 (DB 없이). 실행: uv run --project model_module pytest model_module/tests

확인하는 것: 같은 입력 → 같은 결과 / 09-17 모양의 리스트에서 시장별 매수 1건 이상 + 규칙으로 설명 /
리스트에 없는 종목·날짜가 안 맞는 줄·시세는 걸러진다 / 문(급락·상태 없음)·매도 규칙·가상 체결 / 주간 검토의 독립 재계산이 기록과 맞는다."""

import sys
from dataclasses import asdict
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import trade_rules as tr  # noqa: E402
from kr_calendar import closed_reason, is_trading_day  # noqa: E402
from review_trade import compare_run, recompute  # noqa: E402
from trade_store import Store  # noqa: E402

TD, PREV = "2026-09-17", "2026-09-16"
CAL = ["2026-09-08", "2026-09-09", "2026-09-10", "2026-09-11", "2026-09-14", "2026-09-15", "2026-09-16"]


def lrow(code, market, reasons, warn=(), chg1=1.0, score=2.0, rank=1, **kw):
    r = {"list_date": TD, "model_id": "rule-pick-v1", "phase": "open", "market": market, "stock_code": code, "rank_no": rank,
         "score": score, "reasons": list(reasons), "warn": list(warn), "metrics": {"chg1_pct": chg1}, "as_of": f"{PREV}T15:30:00+09:00"}
    r.update(kw)
    return r


def bar(o, c):
    return {"open": o, "high": max(o, c), "low": min(o, c), "close": c}


def state(code, as_of=f"{PREV}T15:30:00+09:00"):
    return {"state_code": code, "as_of": as_of}


def base_inputs():
    """2026-09-17 실제 리스트를 본뜬 축소판: 코스피 = 코웨이(신고가, 경고 없음)·꼭지 종목·하락 상위, 코스닥 = 오이솔루션(신고가) + 얇은 종목."""
    return {
        "trade_date": TD, "prev_td": PREV, "calendar": CAL,
        "list": [
            lrow("024900", "KR-KOSPI", ["money_surge", "top_gainer", "new_high"], warn=["blowoff", "thin"], chg1=14.65, score=5.5, rank=1),
            lrow("021240", "KR-KOSPI", ["new_high", "top_gainer"], chg1=6.01, score=3.0, rank=7),
            lrow("377300", "KR-KOSPI", ["top_loser"], chg1=-11.37, score=1.9, rank=10),
            lrow("000150", "KR-KOSPI", ["top_gainer"], chg1=7.37, score=1.6, rank=15),
            lrow("138080", "KR-KOSDAQ", ["new_high"], chg1=8.25, score=1.1, rank=32),
            lrow("192650", "KR-KOSDAQ", ["new_high"], warn=["thin"], chg1=4.71, score=1.7, rank=13),
        ],
        "state_rt": {"KR-KOSPI": state("up"), "KR-KOSDAQ": state("up")},
        "state_w": {"KR-KOSPI": state("downtrend"), "KR-KOSDAQ": state("box")},
        "bars": {PREV: {"024900": bar(1900, 1925), "021240": bar(101000, 107600), "377300": bar(45000, 40550),
                        "000150": bar(1300000, 1384000), "138080": bar(23000, 25600), "192650": bar(6800, 7110)}},
        "account": {"cash": tr.INITIAL_CASH, "holdings": []}, "pending_intents": [],
    }


def test_same_input_same_output():
    a, b = tr.run_day(base_inputs()), tr.run_day(base_inputs())
    assert asdict(a) == asdict(b)


def test_each_market_gets_a_buy_and_it_is_explained():
    res = tr.run_day(base_inputs())
    buys = {i["stock_code"]: i for i in res.intents if i["side"] == "buy"}
    assert set(buys) == {"021240", "138080"}                         # 코웨이·오이솔루션
    assert buys["021240"]["qty"] == 15 and buys["138080"]["qty"] == 65   # 1,666,666원 예산 ÷ 종가
    first_fail = {}
    for e in res.evals:
        if e["stage"] == "candidate" and not e["fired"]:
            first_fail.setdefault(e["stock_code"], e["rule_id"])
    assert first_fail["024900"] == "B3_no_warn"     # 꼭지+얇음 경고
    assert first_fail["377300"] == "B1_buy_tag"     # 하락 상위만 — 돈 유입 태그 없음
    assert first_fail["000150"] == "B1_buy_tag"     # 상승 상위만으로는 안 산다
    assert first_fail["192650"] == "B3_no_warn"     # thin
    assert res.gates["KR-KOSPI"]["attitude"] == "tight" and res.gates["KR-KOSDAQ"]["attitude"] == "normal"


def test_rows_not_for_today_or_other_model_are_ignored():
    inp = base_inputs()
    inp["list"].append(lrow("005930", "KR-KOSPI", ["new_high"], chg1=2.0, score=9.0, rank=1, list_date="2026-09-16"))   # 어제 리스트
    inp["list"].append(lrow("000660", "KR-KOSPI", ["new_high"], chg1=2.0, score=9.0, rank=1, model_id="baseline-random-pick-v1"))
    inp["list"].append(lrow("000270", "KR-KOSPI", ["new_high"], chg1=2.0, score=9.0, rank=1, phase="intraday"))
    inp["bars"][PREV].update({"005930": bar(1, 1), "000660": bar(1, 1), "000270": bar(1, 1)})
    res = tr.run_day(inp)
    assert {i["stock_code"] for i in res.intents} == {"021240", "138080"}
    assert any("3개는 오늘 날짜" in n for n in res.notes)


def test_stock_without_prev_bar_is_skipped_and_bar_of_other_date_not_used():
    inp = base_inputs()
    inp["bars"]["2026-09-15"] = {"138080": bar(1, 1)}
    del inp["bars"][PREV]["138080"]
    res = tr.run_day(inp)
    assert {i["stock_code"] for i in res.intents} == {"021240"}
    assert any(e["rule_id"] == "B6_price" and e["stock_code"] == "138080" and not e["fired"] for e in res.evals)


def test_missing_state_blocks_buys_conservatively():
    inp = base_inputs()
    inp["state_rt"]["KR-KOSPI"] = None
    res = tr.run_day(inp)
    assert not res.gates["KR-KOSPI"]["buy_allowed"] and "G4_state_missing" in res.gates["KR-KOSPI"]["reasons"]
    assert {i["stock_code"] for i in res.intents} == {"138080"}


def test_crash_liquidates_and_blocks_buy():
    inp = base_inputs()
    inp["state_rt"]["KR-KOSPI"] = state("crash")
    inp["account"] = {"cash": 8_000_000, "holdings": [{"stock_code": "000150", "market": "KR-KOSPI", "qty": 1, "avg_price": 1_300_000, "entry_date": "2026-09-15"}]}
    res = tr.run_day(inp)
    sells = [i for i in res.intents if i["side"] == "sell"]
    assert len(sells) == 1 and sells[0]["rule_id"] == "G1_rt_crash_liquidate" and sells[0]["qty"] == 1
    assert not any(i["side"] == "buy" and i["market"] == "KR-KOSPI" for i in res.intents)


def test_exit_rules_stop_take_time_and_attitude():
    inp = base_inputs()
    inp["list"] = []
    hold = lambda code, avg, entry: {"stock_code": code, "market": "KR-KOSPI", "qty": 10, "avg_price": avg, "entry_date": entry}  # noqa: E731
    inp["account"] = {"cash": 0, "holdings": [hold("A", 100.0, PREV), hold("B", 100.0, PREV), hold("C", 100.0, "2026-09-10"), hold("D", 100.0, PREV)]}
    inp["bars"][PREV] = {"A": bar(96, 96.5), "B": bar(100, 110), "C": bar(100, 101), "D": bar(100, 99)}
    inp["state_w"]["KR-KOSPI"] = state("box")                   # normal: 손절 -5%, 5일
    res = tr.run_day(inp)
    by = {i["stock_code"]: i["rule_id"] for i in res.intents}
    assert by == {"B": "X2_take_profit", "C": "X3_time_exit"}   # A -3.5%는 normal에선 유지, C는 09-10~16 = 5거래일
    inp["state_w"]["KR-KOSPI"] = state("downtrend")             # tight: 손절 -3%, 3일
    res = tr.run_day(inp)
    by = {i["stock_code"]: i["rule_id"] for i in res.intents}
    assert by["A"] == "X1_stop_loss" and by["C"] == "X3_time_exit" and "D" not in by
    assert any(e["rule_id"] == "X0_no_price" for e in tr.run_day({**inp, "bars": {}}).evals)   # 시세 없으면 보류


def test_settlement_fills_at_open_and_handles_missing_data():
    inp = base_inputs()
    inp["trade_date"], inp["prev_td"] = "2026-09-18", "2026-09-17"
    inp["list"] = []
    inp["pending_intents"] = [
        {"intent_id": 1, "trade_date": "2026-09-17", "market": "KR-KOSPI", "stock_code": "021240", "side": "buy", "qty": 15, "ref_price": 107600, "rule_id": "B7_slot"},
        {"intent_id": 2, "trade_date": "2026-09-17", "market": "KR-KOSDAQ", "stock_code": "138080", "side": "buy", "qty": 65, "ref_price": 25600, "rule_id": "B7_slot"},
        {"intent_id": 3, "trade_date": "2026-09-17", "market": "KR-KOSDAQ", "stock_code": "999999", "side": "buy", "qty": 1, "ref_price": 1, "rule_id": "B7_slot"},
        {"intent_id": 4, "trade_date": "2026-09-18", "market": "KR-KOSDAQ", "stock_code": "138080", "side": "sell", "qty": 65, "ref_price": 1, "rule_id": "X1_stop_loss"},
    ]
    inp["bars"] = {"2026-09-17": {"021240": bar(108000, 109000), "138080": bar(26000, 26500)}}
    res = tr.run_day(inp)
    st = {s["intent_id"]: s for s in res.settlements}
    assert st[1]["status"] == "filled" and st[1]["fill_price"] == 108000 and st[1]["fill_qty"] == 15
    assert st[2]["status"] == "filled" and st[3]["status"] == "unfilled_no_data"
    assert 4 not in st                                            # 오늘 의도는 아직 체결 시점이 아니다
    assert res.account["cash"] == tr.INITIAL_CASH - 15 * 108000 - 65 * 26000
    assert {h["stock_code"]: h["qty"] for h in res.account["holdings"]} == {"021240": 15, "138080": 65}


def test_store_and_review_agree_and_detect_tampering(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    inp = base_inputs()
    rid = store.start_run(tr.MODEL_ID, tr.RULE_VER, TD, "manual")
    res = tr.run_day(inp)
    store.save_inputs(rid, inp)
    store.save_evals(rid, res.evals)
    store.save_intents(rid, res.intents)
    store.save_account(rid, res.account)
    store.finish_run(rid, "ok", prev_td=PREV, summary={})
    run = store.runs_between(TD, TD)[0]
    c = compare_run(store, run)
    assert c["violations"] == [] and c["misses"] == [] and len(c["hits"]) == 2
    exp = recompute(inp)
    assert {(k[0], k[1]) for k in exp["buys"]} == {("021240", 15), ("138080", 65)}
    # 기록을 몰래 고치면 위반·놓침으로 드러나야 한다
    store.con.execute("update intent set qty=99 where stock_code='021240'")
    store.con.execute("insert into intent(run_id,trade_date,market,stock_code,side,qty,rule_id,status) values(?,?,?,?,?,?,?,?)",
                      (rid, TD, "KR-KOSPI", "024900", "buy", 1, "B7_slot", "pending"))
    store.con.commit()
    c = compare_run(store, run)
    assert any("021240 99주" in v for v in c["violations"]) and any("024900" in v for v in c["violations"])
    assert any("021240 15주" in m for m in c["misses"])


def test_calendar():
    assert is_trading_day(date(2026, 9, 28)) and not is_trading_day(date(2026, 9, 25)) and not is_trading_day(date(2026, 9, 27))
    assert closed_reason(date(2026, 10, 5)) == "개천절 대체공휴일"
