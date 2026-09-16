"""종목 파라미터 계산 단위 테스트 — 창이 기준일을 제외하는지, 상한가 판정, 재료 부족 시 None."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import stock_params as sp  # noqa: E402


def mk(n: int, close: float = 100.0, tv: int = 1_000_000_000, vol: int = 1000, **last) -> list[dict]:
    rows = [{"date": f"2026-01-{i + 1:02d}", "open": close, "high": close + 1, "low": close - 1, "close": close,
             "prev_close": close, "change_pct": 0.0, "volume": vol, "trade_value": tv, "upper_limit": close * 1.3}
            for i in range(n)]
    rows[-1].update(last)
    return rows


def test_windows_exclude_today():
    rows = mk(21, tv=1_000_000_000, vol=1000, trade_value=3_000_000_000, volume=2000)
    p = sp.bg_daily(rows, None, {"type": "보통주", "status": "정상"})
    assert p["money_avg20"] == 1_000_000_000 and p["money_ratio"] == 3.0 and p["vol_ratio"] == 2.0
    assert p["high20_gap_pct"] == sp.pct(100.0, 101.0)   # 오늘 종가 vs 직전 20일 고가


def test_insufficient_history_is_none():
    p = sp.bg_daily(mk(5), None, {"type": "보통주", "status": "정상"})
    assert p["money_avg20"] is None and p["chg20_pct"] is None and p["high252_gap_pct"] is None
    assert p["chg5_pct"] is None            # 직전 5일이 4일뿐


def test_limit_up_by_value_and_by_pct():
    rows = mk(3, close=100.0, upper_limit=130.0)
    rows[-1]["close"] = 130.0
    assert sp.is_limit_up(rows[-1])
    assert sp.is_limit_up({"close": 129.5, "change_pct": 29.6, "upper_limit": None})   # krx 백필 줄
    assert not sp.is_limit_up({"close": 120.0, "change_pct": 20.0, "upper_limit": 130.0})
    p = sp.bg_daily(rows, None, {"type": "보통주", "status": "정상"})
    assert p["limit_up"] and p["limit_up_streak"] == 1


def test_rank_days_and_flags():
    days = [{"date": "d1", "foreign_net": 10, "inst_net": -5, "foreign_buy": True, "inst_buy": False},
            {"date": "d2", "foreign_net": 20, "inst_net": 0, "foreign_buy": True, "inst_buy": False}]
    p = sp.bg_daily(mk(21, tv=100), days, {"type": "DR", "status": "정상"})
    assert p["foreign_net5"] == 30 and p["foreign_streak"] == 2 and p["inst_streak"] == 0
    assert p["thin"] and not p["tradable"]
    p2 = sp.bg_daily(mk(21), None, {"type": "보통주", "status": "거래정지"})
    assert p2["foreign_net5"] is None and not p2["tradable"]
