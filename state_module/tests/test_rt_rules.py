"""판별기 규칙·파라미터 단위 테스트 (DB 없이). 실행: uv run --project state_module pytest state_module/tests"""

import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rt_params import intraday_money_ratio, surge_threshold, upper_decile  # noqa: E402
from rt_rules import Breadth, StateHold, breadth_from_changes, classify  # noqa: E402

SECTORS = [f"업종{i}" for i in range(21)]


def changes(values):
    return dict(zip(SECTORS, values))


def test_index_band_boundaries():
    b = Breadth(n=21, up=10, down=10)
    assert classify(0.3, None, None, b).state_code == "flat"      # 경계 ±0.3%는 횡보
    assert classify(0.3001, None, None, b).state_code == "up"
    assert classify(-0.3, None, None, b).state_code == "flat"
    assert classify(-0.31, None, None, b).state_code == "down"


def test_money_surge_turns_up_into_fire_and_down_into_selloff():
    b = Breadth(n=21, up=10, down=10)
    assert classify(1.0, 1.40, 1.37, b).state_code == "fire"
    assert classify(1.0, 1.37, 1.37, b).state_code == "fire"       # 경계 포함(≥)
    assert classify(1.0, 1.36, 1.37, b).state_code == "up"
    assert classify(-1.0, 1.40, 1.37, b).state_code == "selloff"
    assert classify(0.1, 2.00, 1.37, b).state_code == "flat"       # 횡보는 대금과 무관
    assert classify(1.0, None, 1.37, b).state_code == "up"         # 대금 재료 없으면 폭증 아님


def test_crash_needs_90pct_each_down_half_percent_and_sample_15():
    vals = [-0.6] * 19 + [-0.4, 0.2]                  # 19/21 = 90.5%가 −0.5% 이하
    j = classify(-2.0, None, None, breadth_from_changes(changes(vals)))
    assert j.state_code == "crash" and j.state_conf is None
    assert classify(-2.0, None, None, breadth_from_changes(changes([-0.6] * 18 + [-0.4] * 3))).state_code != "crash"
    small = breadth_from_changes(dict(zip(SECTORS[:14], [-1.0] * 14)))   # 표본 14 < 15
    assert classify(-2.0, None, None, small).state_code == "down"


def test_crash_all_down_marks_exogenous_shock():
    j = classify(-5.0, None, None, breadth_from_changes(changes([-1.0] * 21)))
    assert j.state_code == "crash" and j.state_conf == 1.0 and j.metrics["exogenous_shock"]


def test_surge_mirror():
    assert classify(3.0, None, None, breadth_from_changes(changes([0.5] * 19 + [0.1, -0.2]))).state_code == "surge"


def test_theme_up_lists_rising_sectors_in_order_and_beats_selloff():
    vals = [-0.2] * 15 + [0.1, 0.1, 0.1, 0.1, 3.3, 1.2]   # 하락 15/21 = 71%
    j = classify(-1.0, 2.0, 1.37, breadth_from_changes(changes(vals)))
    assert j.state_code == "theme_up"
    assert j.state_sectors == ["업종19", "업종20"]         # 상승률 높은 순
    assert j.label_kr == "테마주상승 — 업종19·업종20"


def test_theme_needs_a_sector_at_least_plus_one_percent():
    vals = [-0.2] * 15 + [0.1] * 5 + [0.99]
    assert classify(-1.0, None, None, breadth_from_changes(changes(vals))).state_code == "down"


def test_crash_beats_theme():
    vals = [-0.6] * 19 + [5.0, 5.0]
    assert classify(-3.0, None, None, breadth_from_changes(changes(vals))).state_code == "crash"


def test_hold_switches_only_after_three_consecutive_minutes():
    up = classify(1.0, None, None, Breadth(n=21))
    down = classify(-1.0, None, None, Breadth(n=21))
    h = StateHold()
    assert h.update(up).state_code == "up"                  # 첫 판정은 바로 채택
    first = h.update(down)
    assert first.state_code == "up" and first.metrics["raw_state"] == "down" and first.metrics["pending_minutes"] == 1
    assert h.update(up).state_code == "up"                  # 끊기면 카운트 리셋
    assert h.update(down).state_code == "up"
    assert h.update(down).state_code == "up"
    assert h.update(down).state_code == "down"              # 3분 연속 → 전환


def test_surge_threshold_uses_only_past_year():
    start = date(2025, 1, 1)
    ratios = [((start + timedelta(days=i)).isoformat(), 1.0) for i in range(365)]   # 2025년 매일
    ratios += [("2026-01-05", 99.0), ("2026-02-01", 99.0)]  # 판정일 당일·미래 값은 기준에 섞이면 안 됨
    thr, n = surge_threshold(ratios, "2026-01-05")
    assert thr == 1.0 and n == 361                          # 2025-01-05 ~ 2025-12-31
    assert surge_threshold(ratios[:10], "2026-01-05") == (None, 6)   # 표본 60 미만이면 기준 없음


def test_upper_decile_matches_simulation_definition():
    assert upper_decile(list(range(1, 11))) == 10          # int(0.9*10)=9번째(0부터) = 10
    assert upper_decile([float(i) for i in range(100)]) == 90.0


def test_intraday_money_ratio_needs_five_days_same_time():
    assert intraday_money_ratio(100.0, [50.0] * 4) == (None, 4)
    ratio, n = intraday_money_ratio(100.0, [50.0] * 25)
    assert n == 20 and ratio == 2.0
