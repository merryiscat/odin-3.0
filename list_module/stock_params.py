"""
오딘 3.0 list_module — 종목 파라미터 계산 (순수 계산, DB 모름).

한 종목의 일봉(날짜 오름차순, 마지막 줄 = 기준일)과 순위 축적(외인·기관)을 받아 "그날 볼 종목을 고르는 재료"를
dict 하나로 만든다. 결과가 stock_params.params(bg_daily)에 그대로 저장된다 — 키 사전은 db/stock_params.sql.
원칙: 기준일 **이후** 데이터는 절대 안 본다(미래 참조 금지). 재료가 모자라면 그 키는 None(추정해 채우지 않는다).
"""

from __future__ import annotations

CALC_VER = "params-v1"

AVG_DAYS = 20              # 평균 대금·거래량·이평·20일 고저의 창(거래일 한 달 관례)
HIGH_252 = 252             # 52주 신고가 창
CHG5, CHG20 = 5, 20        # 누적 등락 창
THIN_MONEY = 5_000_000_000 # 20일 평균 대금 50억 미만 = 얇음(옛 오딘 검증 하한을 경고선으로 재활용)
LIMIT_UP_PCT = 29.5        # 상한가 값이 없는 줄(krx 백필)에서 상한가로 볼 등락률
NET_DAYS = 5               # 외인·기관 순매수 합산 창(거래일)
UNTRADABLE_TYPES = {"DR", "외국주권", "ELW", "신주인수권", "펀드"}   # 계좌로 못 사거나 살 이유가 없는 상품


def pct(now: float | None, base: float | None) -> float | None:
    if now is None or not base:
        return None
    return round((now / base - 1.0) * 100.0, 4)


def _mean(vals: list) -> float | None:
    v = [x for x in vals if x is not None]
    return (sum(v) / len(v)) if v else None


def is_limit_up(row: dict) -> bool:
    """상한가 마감: kis 줄은 상한가 값으로 정확히, krx 줄은 등락률 29.5% 이상으로."""
    ul, close = row.get("upper_limit"), row.get("close")
    if ul and close is not None:
        return float(close) >= float(ul)
    chg = row.get("change_pct")
    return chg is not None and float(chg) >= LIMIT_UP_PCT


def bg_daily(ohlc: list[dict], rank_days: list[dict] | None, stock: dict) -> dict:
    """기준일 파라미터. ohlc = 그 종목 일봉(오름차순, 마지막 = 기준일, 최대 253줄이면 충분).
    rank_days = 최근 NET_DAYS 거래일의 [{date, foreign_net, inst_net, foreign_buy(bool), inst_buy(bool)} …] (오래된 순),
                순위 축적이 아예 없는 날은 목록에서 빠짐. None이면 수급 키 전부 None.
    stock = dim_stock 줄(type·status)."""
    today = ohlc[-1]
    prev = ohlc[:-1]
    close = float(today["close"])
    prev_close = float(today["prev_close"]) if today.get("prev_close") else (float(prev[-1]["close"]) if prev else None)
    p: dict = {
        "prev_close": prev_close,
        "chg1_pct": float(today["change_pct"]) if today.get("change_pct") is not None else pct(close, prev_close),
        "chg5_pct": pct(close, float(prev[-CHG5]["close"])) if len(prev) >= CHG5 else None,
        "chg20_pct": pct(close, float(prev[-CHG20]["close"])) if len(prev) >= CHG20 else None,
        "money": today.get("trade_value"),
        "volume": today.get("volume"),
        "gap_open_pct": pct(float(today["open"]), prev_close) if today.get("open") else None,
        "range_pct": (round((float(today["high"]) - float(today["low"])) / prev_close * 100, 4)
                      if today.get("high") and today.get("low") and prev_close else None),
    }
    # 평균 대금·거래량은 기준일을 뺀 직전 20일(오늘 값과 비교하려고)
    win = prev[-AVG_DAYS:]
    money_avg = _mean([r.get("trade_value") for r in win]) if len(win) == AVG_DAYS else None
    vol_avg = _mean([r.get("volume") for r in win]) if len(win) == AVG_DAYS else None
    p["money_avg20"] = round(money_avg, 0) if money_avg else None
    p["money_ratio"] = round(p["money"] / money_avg, 4) if money_avg and p["money"] is not None else None
    p["vol_ratio"] = round(p["volume"] / vol_avg, 4) if vol_avg and p["volume"] is not None else None
    # 고저 대비 위치(직전 창의 고가·저가 대비 오늘 종가 — 0 이상이면 신고가 돌파)
    highs = [float(r["high"]) for r in win if r.get("high")]
    lows = [float(r["low"]) for r in win if r.get("low")]
    p["high20_gap_pct"] = pct(close, max(highs)) if len(win) == AVG_DAYS and highs else None
    p["low20_gap_pct"] = pct(close, min(lows)) if len(win) == AVG_DAYS and lows else None
    win252 = prev[-HIGH_252:]
    highs252 = [float(r["high"]) for r in win252 if r.get("high")]
    p["high252_gap_pct"] = pct(close, max(highs252)) if len(win252) == HIGH_252 and highs252 else None
    # 20일 이평 이격(오늘 포함)
    closes = [float(r["close"]) for r in ohlc[-AVG_DAYS:]]
    p["ma20_gap_pct"] = pct(close, sum(closes) / AVG_DAYS) if len(closes) == AVG_DAYS else None
    # 상한가
    p["limit_up"] = is_limit_up(today)
    streak = 0
    for r in reversed(ohlc):
        if is_limit_up(r):
            streak += 1
        else:
            break
    p["limit_up_streak"] = streak
    # 외인·기관 수급(순위 축적으로 본 것 — 순위 밖이면 0, 축적 자체가 없으면 None)
    if rank_days:
        p["foreign_net5"] = sum(d.get("foreign_net") or 0 for d in rank_days[-NET_DAYS:])
        p["inst_net5"] = sum(d.get("inst_net") or 0 for d in rank_days[-NET_DAYS:])
        p["foreign_streak"] = _streak(rank_days, "foreign_buy")
        p["inst_streak"] = _streak(rank_days, "inst_buy")
    else:
        p["foreign_net5"] = p["inst_net5"] = p["foreign_streak"] = p["inst_streak"] = None
    # 표지
    p["thin"] = (money_avg is not None and money_avg < THIN_MONEY)
    p["tradable"] = stock.get("status") != "거래정지" and stock.get("type") not in UNTRADABLE_TYPES
    p["type"], p["status"] = stock.get("type"), stock.get("status")
    return p


def _streak(rank_days: list[dict], key: str) -> int:
    n = 0
    for d in reversed(rank_days):
        if d.get(key):
            n += 1
        else:
            break
    return n
