"""
오딘 3.0 state_module — 실시간 판별기 규칙 (마트 1호 예측모델 rule-temp-rt-v1).

DB·네트워크를 모르는 **순수 계산**만 둔다: 재료(지수 등락률·대금 배율·대장주 폭)를 받아
상태 하나를 돌려준다. 장중 1분 루프(run_rt.py)·종가 판정(run_eod.py)·과거 대조(verify_sim.py)가
전부 이 파일 하나를 같이 쓴다 → "시뮬레이션과 라이브가 다른 규칙으로 도는" 사고를 막는다.

규칙 원문: state_module/README.md "실시간 어휘 v3" 표 (2026-09-13~14 사용자 확정).
숫자를 바꾸면 RULE_VER를 올린다(과거 판정 줄과 섞이지 않게).
"""

from __future__ import annotations

from dataclasses import dataclass, field

MODEL_ID = "rule-temp-rt-v1"
RULE_VER = "rt-rules-v1"

# ── 확정 숫자 (README 표와 1:1) ─────────────────────────────────────────────
BAND_PCT = 0.3            # 상승/하락/횡보 경계: 지수 당일 등락률 ±0.3%
EACH_PCT = 0.5            # 급락/급등: 대장주 "각자" ±0.5% 이상
EXTREME_RATIO = 0.9       # 급락/급등: 대장주의 90% 이상
MIN_SAMPLE = 15           # 폭 판정 최소 표본(집계된 대장주 수)
THEME_DOWN_RATIO = 0.7    # 테마주상승: 하락 대장주 비율 70% 이상(급락 미달)
THEME_SECTOR_PCT = 1.0    # 테마주상승: +1% 이상 역행 상승한 업종
HOLD_MINUTES = 3          # 깜빡임 방지: 새 상태가 3분 연속이어야 전환

# 우선순위 = 이 순서 (위가 이긴다). 급락과 테마주상승은 공존하지 않는다.
PRIORITY = ["crash", "surge", "theme_up", "selloff", "fire", "up", "down", "flat"]

LABEL_KR = {
    "crash": "급락", "surge": "급등", "theme_up": "테마주상승", "selloff": "투매",
    "fire": "불장", "up": "상승", "down": "하락", "flat": "횡보",
}

# 사람용 문구에만 쓰는 업종 줄임말. 기계는 state_sectors(sector_l1 원문)만 매칭한다.
SECTOR_SHORT = {
    "건설·엔지니어링": "건설", "금융·지주": "금융", "기계·장비": "기계", "기타·서비스": "서비스",
    "미디어·엔터·레저": "엔터", "반도체·전자": "반도체", "섬유·의류·종이": "의류",
    "소프트웨어·IT": "IT", "운송·물류": "운송", "유통·도소매": "유통", "유틸리티·환경": "유틸",
    "음식료·담배": "음식료", "의료기기·정밀": "의료기기", "자동차·부품": "자동차",
    "전기·2차전지": "2차전지", "제약·바이오": "바이오", "조선·방산·중공업": "방산",
    "증권·보험": "증권보험", "철강·금속": "철강",   # 줄임말엔 '·'를 안 쓴다(업종 구분자와 헷갈림) "통신": "통신", "화학": "화학",
}


@dataclass
class Breadth:
    """업종 대장주 바스켓 집계. 비율은 개수가 아니라 n(실제 집계된 수) 기준 — 거래정지로 분모가 변한다."""
    n: int = 0          # 등락률을 받은 대장주 수(분모)
    up: int = 0         # 등락률 > 0
    down: int = 0       # 등락률 < 0
    u05: int = 0        # 등락률 ≥ +0.5%
    d05: int = 0        # 등락률 ≤ −0.5%
    rising_sectors: list[str] = field(default_factory=list)  # +1% 이상 업종, 상승률 높은 순


def breadth_from_changes(sector_chg: dict[str, float]) -> Breadth:
    """{업종(sector_l1): 그 업종 대장주 등락률%} → Breadth. 등락률 없는 종목은 호출 전에 빼고 넘긴다."""
    vals = list(sector_chg.values())
    rising = sorted((s for s, c in sector_chg.items() if c >= THEME_SECTOR_PCT),
                    key=lambda s: sector_chg[s], reverse=True)
    return Breadth(
        n=len(vals),
        up=sum(1 for c in vals if c > 0),
        down=sum(1 for c in vals if c < 0),
        u05=sum(1 for c in vals if c >= EACH_PCT),
        d05=sum(1 for c in vals if c <= -EACH_PCT),
        rising_sectors=rising,
    )


@dataclass
class Judgment:
    state_code: str
    state_sectors: list[str] | None
    state_conf: float | None
    label_kr: str
    metrics: dict


def money_surge(money_ratio: float | None, threshold: float | None) -> bool:
    """대금 폭증: (당일 대금 ÷ 직전 20일 평균)이 그 시장 최근 1년 분포 상위 10% 이상.
    재료가 없으면(이력 부족·대금 미수신) 폭증 아님으로 본다 → 불장·투매 대신 상승·하락으로 떨어진다."""
    return money_ratio is not None and threshold is not None and money_ratio >= threshold


def classify(index_chg: float, money_ratio: float | None, surge_threshold: float | None,
             b: Breadth) -> Judgment:
    """재료 → 단일 상태(우선순위 높은 것 하나). 확신도는 규칙이 아직 정의되지 않아 None
    (예외: 급락인데 대장주 전원 하락 = 확신도 최고 + 외생 충격 표지, README 확정 사항)."""
    metrics = {
        "chg": round(index_chg, 4),
        "money_ratio": None if money_ratio is None else round(money_ratio, 4),
        "surge_threshold": None if surge_threshold is None else round(surge_threshold, 4),
        "n": b.n, "up": b.up, "down": b.down, "u05": b.u05, "d05": b.d05,
    }
    sampled = b.n >= MIN_SAMPLE
    conf, sectors = None, None

    if sampled and b.d05 / b.n >= EXTREME_RATIO:
        code = "crash"
        if b.down == b.n:
            conf = 1.0
            metrics["exogenous_shock"] = True
    elif sampled and b.u05 / b.n >= EXTREME_RATIO:
        code = "surge"
    elif sampled and b.down / b.n >= THEME_DOWN_RATIO and b.rising_sectors:
        code, sectors = "theme_up", list(b.rising_sectors)
    elif index_chg > BAND_PCT:
        code = "fire" if money_surge(money_ratio, surge_threshold) else "up"
    elif index_chg < -BAND_PCT:
        code = "selloff" if money_surge(money_ratio, surge_threshold) else "down"
    else:
        code = "flat"

    label = LABEL_KR[code]
    if sectors:
        label += " — " + "·".join(SECTOR_SHORT.get(s, s) for s in sectors)
    return Judgment(code, sectors, conf, label, metrics)


class StateHold:
    """깜빡임 방지(장중 전용). 새 상태가 HOLD_MINUTES분 연속으로 나와야 전환한다.
    전환 전에는 기존 상태를 계속 내보내되, 이번 분의 재료·원판정은 metrics에 남긴다(근거 보존).
    하루 첫 판정은 비교 대상이 없으니 바로 채택(잠정 — README "구현 메모" 참고)."""

    def __init__(self, minutes: int = HOLD_MINUTES, current: Judgment | None = None):
        self.minutes = minutes
        self.current = current
        self.pending: str | None = None
        self.count = 0

    def update(self, raw: Judgment) -> Judgment:
        if self.current is None or raw.state_code == self.current.state_code:
            self.current, self.pending, self.count = raw, None, 0
            return raw
        if raw.state_code == self.pending:
            self.count += 1
        else:
            self.pending, self.count = raw.state_code, 1
        if self.count >= self.minutes:
            self.current, self.pending, self.count = raw, None, 0
            return raw
        held = self.current
        metrics = dict(raw.metrics, raw_state=raw.state_code, pending_minutes=self.count)
        return Judgment(held.state_code, held.state_sectors, held.state_conf, held.label_kr, metrics)
