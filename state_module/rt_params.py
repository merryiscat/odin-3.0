"""
오딘 3.0 state_module — 판정 파라미터 계산 (순수 계산, DB 모름).

- 배경(bg_daily): 어제까지의 지수 일봉으로 아침 1회 계산. 장중엔 고정.
- 장중(intraday): 1분 시세로 매분 계산.
결과는 state_params 표에 그대로 저장되는 dict 모양이다(키 사전 = README "판정 파라미터").
"""

from __future__ import annotations

from datetime import date, timedelta

CALC_VER = "params-v1"

MA_DAYS = 20              # 이동평균 창(거래일 한 달 관례)
SLOPE_LAG = 5             # 기울기 = 오늘 이평 vs 5거래일 전 이평
MONEY_AVG_DAYS = 20       # 대금 배율 분모: 직전 20거래일 평균
SURGE_PCTL = 0.9          # 대금 폭증 = 최근 1년 배율 분포 상위 10%
SURGE_LOOKBACK_DAYS = 365 # "최근 1년"(달력 기준)
SURGE_MIN_HIST = 60       # 분포가 이보다 짧으면 기준을 안 만든다(첫 두세 달의 들쭉날쭉한 기준 방지)
INTRADAY_MIN_DAYS = 5     # 장중 같은-시각 대금 비교에 필요한 최소 과거 일수


def pct_change(now: float, base: float) -> float:
    return (now / base - 1.0) * 100.0


def daily_money_ratios(ohlc: list[dict]) -> list[tuple[str, float]]:
    """일봉(날짜 오름차순, trade_value 포함) → [(날짜, 당일 대금 ÷ 직전 20일 평균)].
    앞의 20일은 분모가 없어 빠진다."""
    out = []
    for i in range(MONEY_AVG_DAYS, len(ohlc)):
        prev = [r.get("trade_value") for r in ohlc[i - MONEY_AVG_DAYS:i]]
        today = ohlc[i].get("trade_value")
        if today is None or any(v is None for v in prev):
            continue
        avg = sum(prev) / MONEY_AVG_DAYS
        if avg > 0:
            out.append((ohlc[i]["date"], today / avg))
    return out


def upper_decile(values: list[float]) -> float:
    """상위 10% 경계값. 과거 시뮬레이션과 같은 정의(정렬 후 int(0.9·n)번째)."""
    s = sorted(values)
    return s[min(int(SURGE_PCTL * len(s)), len(s) - 1)]


def surge_threshold(ratios: list[tuple[str, float]], as_of: str) -> tuple[float | None, int]:
    """as_of 날짜 **이전** 1년치 배율 분포의 상위 10% 경계. (경계, 표본 수)
    ※ 2026-09-14 첫 시뮬레이션은 2년 전체(미래 포함) 분포를 썼다 — 라이브는 과거만 본다."""
    start = (date.fromisoformat(as_of) - timedelta(days=SURGE_LOOKBACK_DAYS)).isoformat()
    hist = [v for d, v in ratios if start <= d < as_of]
    if len(hist) < SURGE_MIN_HIST:
        return None, len(hist)
    return upper_decile(hist), len(hist)


def bg_daily(ohlc_until_yesterday: list[dict], today: str) -> dict:
    """아침 1회 배경 파라미터. 입력은 today 전날까지의 일봉(오름차순)."""
    rows = [r for r in ohlc_until_yesterday if r["date"] < today]
    closes = [float(r["close"]) for r in rows]
    p: dict = {"as_of_date": rows[-1]["date"] if rows else None, "prev_close": closes[-1] if closes else None}

    if len(closes) >= MA_DAYS:
        ma = sum(closes[-MA_DAYS:]) / MA_DAYS
        p["ma20"] = round(ma, 4)
        p["ma_gap_pct"] = round(pct_change(closes[-1], ma), 4)
    if len(closes) >= MA_DAYS + SLOPE_LAG:
        ma_then = sum(closes[-MA_DAYS - SLOPE_LAG:-SLOPE_LAG]) / MA_DAYS
        p["ma_slope_pct"] = round(pct_change(p["ma20"], ma_then), 4)
    if len(closes) > SLOPE_LAG:
        p["chg5_pct"] = round(pct_change(closes[-1], closes[-1 - SLOPE_LAG]), 4)

    ratios = daily_money_ratios(rows)
    thr, n = surge_threshold(ratios, today)
    p["surge_threshold"] = None if thr is None else round(thr, 4)
    p["surge_hist_n"] = n
    vals = [r.get("trade_value") for r in rows[-MONEY_AVG_DAYS:]]
    if len(vals) == MONEY_AVG_DAYS and all(v is not None for v in vals):
        p["money_avg20"] = sum(vals) / MONEY_AVG_DAYS
    return p


def intraday_money_ratio(acc_amount_now: float | None, same_time_history: list[float]) -> tuple[float | None, int]:
    """장중 대금 배율 = 지금까지 누적 대금 ÷ 과거 같은 시각 누적 대금의 평균(최근 20거래일).
    누적 대금은 아침에 몰리므로 하루 전체 평균과 비교하면 오전 내내 '폭증 아님'이 된다 — 같은 시각끼리 비교.
    과거 이력이 INTRADAY_MIN_DAYS일 미만이면 None(불장·투매 판정 보류)."""
    hist = [v for v in same_time_history[-MONEY_AVG_DAYS:] if v]
    if acc_amount_now is None or len(hist) < INTRADAY_MIN_DAYS:
        return None, len(hist)
    return acc_amount_now / (sum(hist) / len(hist)), len(hist)
