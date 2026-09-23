"""
오딘 3.0 model_module — 한국 증시 휴장일 달력 (정적 표).

실행기가 "오늘 장이 열리는 날인가"를 판단하고, 주간 검토가 "안 돈 날"과 "휴장일"을 구분하는 데 쓴다.
정적 표라 해가 바뀌면 손으로 추가해야 한다(KRX 휴장일 공고 기준). 임시공휴일이 새로 지정되면 여기 넣는다.
2026-09-28(월)은 대체공휴일이 아니다(추석 연휴 09-24~26, 토요일 겹침은 대체공휴일 대상이 아님 — 2026-09-23 확인).
"""

from __future__ import annotations

from datetime import date, timedelta

KR_HOLIDAYS = {
    # 2026
    "2026-01-01": "신정",
    "2026-02-16": "설 연휴", "2026-02-17": "설날", "2026-02-18": "설 연휴",
    "2026-03-02": "삼일절 대체공휴일",
    "2026-05-01": "근로자의 날", "2026-05-05": "어린이날", "2026-05-25": "부처님오신날 대체공휴일",
    "2026-06-03": "지방선거", "2026-08-17": "광복절 대체공휴일",
    "2026-09-24": "추석 연휴", "2026-09-25": "추석", 
    "2026-10-05": "개천절 대체공휴일", "2026-10-09": "한글날",
    "2026-12-25": "성탄절", "2026-12-31": "연말 휴장",
}


def is_trading_day(d: date) -> bool:
    """주말·휴장일이 아니면 거래일로 본다(정적 표 기준 — 실제 시세 유무는 실행기가 따로 확인)."""
    return d.weekday() < 5 and d.isoformat() not in KR_HOLIDAYS


def closed_reason(d: date) -> str | None:
    if d.weekday() >= 5:
        return "주말"
    return KR_HOLIDAYS.get(d.isoformat())


def weekdays_between(d_from: date, d_to: date) -> list[date]:
    out, d = [], d_from
    while d <= d_to:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out
