"""주간 판별 규칙 단위 테스트 (DB 없이). 실행: uv run --project state_module pytest state_module/tests"""

import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from w_rules import avg_ranges, classify, day_ranges, judge_series, upper_pctl, vol_threshold  # noqa: E402


def test_priority_emergency_first():
    # 장중 −8%면 1주 누적이 +10%여도 긴급
    assert classify(-8.0, 10.0, 5.0, 1.0).state_code == "emergency"      # 경계 포함(≤)
    assert classify(-7.99, -10.0, 5.0, 1.0).state_code == "downtrend"


def test_trend_band_boundaries():
    assert classify(-1.0, -3.0, None, None).state_code == "downtrend"     # 경계 포함
    assert classify(-1.0, 3.0, None, None).state_code == "uptrend"
    assert classify(-1.0, 2.99, None, None).state_code == "box"
    assert classify(None, -2.99, None, None).state_code == "box"          # 장중 저가 없으면 긴급 판정만 빠짐


def test_volatile_only_inside_trend_band_and_needs_threshold():
    assert classify(-1.0, 1.0, 2.0, 2.0).state_code == "volatile"        # 경계 포함(≥)
    assert classify(-1.0, 1.0, 1.99, 2.0).state_code == "box"
    assert classify(-1.0, 4.0, 9.0, 2.0).state_code == "uptrend"         # 방향이 변동보다 먼저
    assert classify(-1.0, 1.0, 9.0, None).state_code == "box"            # 1년 분포 없으면 변동장 불가


def test_ranges_and_upper_pctl():
    ohlc = [{"date": f"d{i}", "close": 100.0, "high": 101.0, "low": 99.0} for i in range(6)]
    r = day_ranges(ohlc)
    assert len(r) == 5 and all(abs(v - 2.0) < 1e-9 for _, v in r)
    assert avg_ranges(r) == [("d5", 2.0)]
    assert upper_pctl([float(i) for i in range(10)]) == 8.0              # int(0.8*10)=8번째


def test_vol_threshold_uses_only_past_year():
    start = date(2025, 1, 1)
    hist = [((start + timedelta(days=i)).isoformat(), 1.0) for i in range(365)]
    hist += [("2026-01-05", 99.0), ("2026-02-01", 99.0)]                 # 당일·미래 값은 섞이면 안 됨
    thr, n = vol_threshold(hist, "2026-01-05")
    assert thr == 1.0 and n == 361
    assert vol_threshold(hist[:50], "2026-01-05") == (None, 46)          # 표본 120 미만이면 기준 없음


def test_judge_series_has_no_lookahead():
    base = date(2024, 1, 1)
    ohlc = [{"date": (base + timedelta(days=i)).isoformat(), "close": 100.0, "high": 100.5, "low": 99.5}
            for i in range(200)]
    a = dict(judge_series(ohlc))
    crash = [dict(r) for r in ohlc] + [{"date": (base + timedelta(days=200)).isoformat(),
                                        "close": 85.0, "high": 100.0, "low": 84.0}]
    b = dict(judge_series(crash))
    last_common = ohlc[-1]["date"]
    assert a[last_common].state_code == b[last_common].state_code       # 뒤에 폭락이 붙어도 과거 판정은 그대로
    assert b[crash[-1]["date"]].state_code == "emergency"                # 장중 −16% → 긴급
