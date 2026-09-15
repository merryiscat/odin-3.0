"""
오딘 3.0 state_module — 주간 시장 온도 판별 규칙 (초안 모델 rule-temp-w-v0).

주간 = 최근 일주일(5거래일)의 시장 성격 → 매매로직을 고르는 이름표 하나.
DB·네트워크를 모르는 **순수 계산**만 둔다. 종가 판정·과거 백필(run_w.py)과 나중의 장중 실시간 판정이
전부 이 파일 하나를 같이 쓴다(실시간 판별기 rt_rules.py와 같은 원칙).

규칙 원문: state_module/README.md "주간 판정 설계" 절. **조건 숫자는 초안**(2026-09-15 사용자가 "이 초안으로
시작" 승인 — 확정 아님). 숫자를 바꾸면 RULE_VER를 올린다.

확정된 원칙(README):
- 모델은 이름표 하나만 읽는다 → state_code 한 칸, 계산값은 metrics에만.
- 상황값을 기간 고정하지 않는다 → 매 판정마다 그 시점까지의 사실로 다시 계산(유지·확인 규칙 없음).
- 긴급은 크기 기준(원인 무관, 서킷브레이커급 폭락).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

MODEL_ID = "rule-temp-w-v0"
RULE_VER = "w-rules-draft1"

# ── 초안 숫자 (README와 1:1) ────────────────────────────────────────────────
WINDOW_DAYS = 5              # 판정 창: 오늘 포함 최근 5거래일
EMERGENCY_LOW_PCT = -8.0     # 긴급: 오늘 장중 최저가가 전일 종가 대비 −8% 이하(KRX 서킷브레이커 1단계 수준)
TREND_PCT = 3.0              # 상승장/하락장: 최근 5거래일 누적 등락률 ±3%
VOL_PCTL = 0.8               # 변동장: 5일 평균 진폭이 최근 1년 분포 상위 20% 이상
VOL_LOOKBACK_DAYS = 365      # "최근 1년"(달력 기준, 판정일 제외)
VOL_MIN_HIST = 120           # 분포 표본이 이보다 적으면 변동장 기준을 만들지 않는다(→ 박스권으로 떨어짐)

# 위에서부터 먼저 맞는 것 하나 (2026-09-15 사용자 승인 순서)
PRIORITY = ["emergency", "downtrend", "uptrend", "volatile", "box"]

LABEL_KR = {
    "emergency": "긴급", "downtrend": "하락장", "uptrend": "상승장", "volatile": "변동장", "box": "박스권",
}


def pct_change(now: float, base: float) -> float:
    return (now / base - 1.0) * 100.0


@dataclass
class Judgment:
    state_code: str
    label_kr: str
    metrics: dict


# ── 파라미터 계산 ───────────────────────────────────────────────────────────
def day_ranges(ohlc: list[dict]) -> list[tuple[str, float]]:
    """일봉(날짜 오름차순, high·low·close) → [(날짜, 하루 진폭% = (고가−저가) ÷ 전일 종가)]. 첫날은 전일이 없어 빠진다."""
    out = []
    for i in range(1, len(ohlc)):
        prev = float(ohlc[i - 1]["close"])
        hi, lo = ohlc[i].get("high"), ohlc[i].get("low")
        if hi is None or lo is None or prev <= 0:
            continue
        out.append((ohlc[i]["date"], (float(hi) - float(lo)) / prev * 100.0))
    return out


def avg_ranges(ranges: list[tuple[str, float]]) -> list[tuple[str, float]]:
    """[(날짜, 하루 진폭)] → [(날짜, 그날 포함 최근 5거래일 평균 진폭)]."""
    return [(ranges[i][0], sum(v for _, v in ranges[i - WINDOW_DAYS + 1:i + 1]) / WINDOW_DAYS)
            for i in range(WINDOW_DAYS - 1, len(ranges))]


def upper_pctl(values: list[float], pctl: float = VOL_PCTL) -> float:
    """상위 (1−pctl) 경계값. 정렬 후 int(pctl·n)번째(실시간 대금 기준 upper_decile과 같은 정의)."""
    s = sorted(values)
    return s[min(int(pctl * len(s)), len(s) - 1)]


def vol_threshold(avg_rng: list[tuple[str, float]], as_of: str) -> tuple[float | None, int]:
    """as_of 날짜 **이전** 1년치 5일 평균 진폭 분포의 상위 20% 경계. (경계, 표본 수) — 미래 참조 없음."""
    start = (date.fromisoformat(as_of) - timedelta(days=VOL_LOOKBACK_DAYS)).isoformat()
    hist = [v for d, v in avg_rng if start <= d < as_of]
    if len(hist) < VOL_MIN_HIST:
        return None, len(hist)
    return upper_pctl(hist), len(hist)


# ── 판정 ────────────────────────────────────────────────────────────────────
def classify(low_pct_today: float | None, chg5: float, rng5: float | None, rng_threshold: float | None) -> Judgment:
    """재료 → 이름표 하나(PRIORITY 순서로 먼저 맞는 것).
    low_pct_today : 오늘 장중 최저가 ÷ 전일 종가 −1 (%)
    chg5          : 오늘(또는 지금) 가격 ÷ 5거래일 전 종가 −1 (%)
    rng5          : 오늘 포함 최근 5거래일 평균 진폭 (%)
    rng_threshold : 그 시장 최근 1년 5일 평균 진폭의 상위 20% 경계 (없으면 변동장 판정 불가)"""
    metrics = {
        "low_pct_today": None if low_pct_today is None else round(low_pct_today, 4),
        "chg5": round(chg5, 4),
        "rng5": None if rng5 is None else round(rng5, 4),
        "rng_threshold": None if rng_threshold is None else round(rng_threshold, 4),
    }
    if low_pct_today is not None and low_pct_today <= EMERGENCY_LOW_PCT:
        code = "emergency"
    elif chg5 <= -TREND_PCT:
        code = "downtrend"
    elif chg5 >= TREND_PCT:
        code = "uptrend"
    elif rng5 is not None and rng_threshold is not None and rng5 >= rng_threshold:
        code = "volatile"
    else:
        code = "box"
    return Judgment(code, LABEL_KR[code], metrics)


def judge_series(ohlc: list[dict]) -> list[tuple[str, Judgment]]:
    """일봉 전체(오름차순) → 날짜마다 종가 기준 판정. 각 날짜는 그날까지의 데이터만 쓴다(미래 참조 없음).
    과거 백필과 종가 판정이 같은 경로를 탄다."""
    closes = {r["date"]: float(r["close"]) for r in ohlc}
    dates = [r["date"] for r in ohlc]
    avg_rng = avg_ranges(day_ranges(ohlc))
    avg_by_date = dict(avg_rng)
    out = []
    for i in range(WINDOW_DAYS, len(ohlc)):
        d = dates[i]
        prev_close = closes[dates[i - 1]]
        low = ohlc[i].get("low")
        low_pct = None if low is None else pct_change(float(low), prev_close)
        chg5 = pct_change(closes[d], closes[dates[i - WINDOW_DAYS]])
        thr, n = vol_threshold(avg_rng, d)
        j = classify(low_pct, chg5, avg_by_date.get(d), thr)
        j.metrics["chg_today"] = round(pct_change(closes[d], prev_close), 4)
        j.metrics["rng_hist_n"] = n
        out.append((d, j))
    return out
