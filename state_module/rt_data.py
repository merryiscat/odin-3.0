"""
오딘 3.0 state_module — 판별기가 DB에서 재료를 읽어오는 함수 모음 (읽기 전용).

읽는 표: dim_basket(대장주 명부) · raw_index_ohlc(지수 일봉) · raw_market_snapshot(1분 시세)
        · state_market_temp(재시작 시 직전 상태 복원) · state_params(오늘 배경 계산 여부)
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone

from rt_rules import MODEL_ID, Judgment
from supa import Supa

KST = timezone(timedelta(hours=9))   # 한국은 서머타임이 없어 고정 +9 (Windows엔 tz DB가 없어 zoneinfo 대신)
MARKETS = {"KR-KOSPI": "0001", "KR-KOSDAQ": "1001"}   # 시장 → 지수코드
SOURCE_RANK = {"kis": 0, "old_odin": 1}                  # 같은 분에 둘 다 있으면 3.0 직접 수집(대금 포함)을 우선


def kst_iso(dt: datetime) -> str:
    return dt.astimezone(KST).isoformat()


def day_start(d: date) -> datetime:
    return datetime.combine(d, time(0, 0), KST)


def load_leaders(db: Supa) -> dict[str, dict[str, str]]:
    """{시장: {종목코드: 업종}} — 업종 대장주 바스켓."""
    rows = db.select("dim_basket", {"select": "market,stock_code,sector_l1",
                                    "basket_type": "eq.leader", "order": "market,stock_code"})
    out: dict[str, dict[str, str]] = defaultdict(dict)
    for r in rows:
        out[r["market"]][r["stock_code"]] = r["sector_l1"]
    return dict(out)


def load_ohlc(db: Supa, market: str, until: date, days: int = 420) -> list[dict]:
    """지수 일봉 (until 포함, 오름차순). 1년 백분위 + 20일 평균을 계산하려면 약 420일치가 필요."""
    return db.select("raw_index_ohlc", {
        "select": "date,close,trade_value", "market": f"eq.{market}",
        "and": f"(date.gte.{(until - timedelta(days=days)).isoformat()},date.lte.{until.isoformat()})",
        "order": "date",
    })


def load_ohlc_hl(db: Supa, market: str, until: date) -> list[dict]:
    """지수 일봉 전체(고가·저가 포함, until 포함, 오름차순) — 주간 판정(w_rules)의 진폭 계산용.
    1년 진폭 분포를 만들려면 판정일보다 1년 이상 앞부터 필요해서 전체를 읽는다."""
    return db.select("raw_index_ohlc", {
        "select": "date,high,low,close", "market": f"eq.{market}",
        "date": f"lte.{until.isoformat()}", "order": "date",
    })


def load_snapshots(db: Supa, start: datetime, end: datetime, kinds=("index", "stock")) -> list[dict]:
    """[start, end) 구간의 1분 시세."""
    return db.select("raw_market_snapshot", {
        "select": "ts,market,kind,code,price,change_pct,acc_amount,source",
        "kind": f"in.({','.join(kinds)})",
        "and": f"(ts.gte.{kst_iso(start)},ts.lt.{kst_iso(end)})",
        "order": "ts,id",
    })


def latest_by_code(rows: list[dict]) -> dict[tuple[str, str, str], dict]:
    """(시장, 종류, 코드)별 가장 최근 줄. 같은 시각이면 kis 출처 우선."""
    best: dict[tuple[str, str, str], dict] = {}
    for r in rows:
        k = (r["market"], r["kind"], r["code"])
        cur = best.get(k)
        if cur is None:
            best[k] = r
            continue
        newer = r["ts"] > cur["ts"]
        same_better = r["ts"] == cur["ts"] and SOURCE_RANK.get(r["source"], 9) < SOURCE_RANK.get(cur["source"], 9)
        if newer or same_better:
            best[k] = r
    return best


def index_amount_history(db: Supa, today: date, lookback_days: int = 45) -> dict[str, dict[str, dict[str, float]]]:
    """{시장: {날짜: {HH:MM: 누적 대금}}} — 과거 거래일의 같은 시각 대금(장중 대금 배율 분모).
    kis 출처만 대금이 있다(옛 오딘 분봉엔 대금 칸이 없음)."""
    rows = db.select("raw_market_snapshot", {
        "select": "ts,market,acc_amount", "kind": "eq.index", "source": "eq.kis",
        "acc_amount": "not.is.null",
        "and": f"(ts.gte.{kst_iso(day_start(today - timedelta(days=lookback_days)))},ts.lt.{kst_iso(day_start(today))})",
        "order": "ts",
    })
    out: dict = defaultdict(lambda: defaultdict(dict))
    for r in rows:
        t = datetime.fromisoformat(r["ts"]).astimezone(KST)
        out[r["market"]][t.date().isoformat()][t.strftime("%H:%M")] = float(r["acc_amount"])
    return out


def restore_hold(db: Supa, market: str, today: date) -> Judgment | None:
    """재시작해도 깜빡임 방지가 끊기지 않게, 오늘 마지막 라이브 판정을 현재 상태로 되살린다."""
    rows = db.client.get(f"{db.url}/rest/v1/state_market_temp", headers=db.headers, params={
        "select": "state_code,state_sectors,state_conf,label_kr,metrics",
        "market": f"eq.{market}", "model_id": f"eq.{MODEL_ID}", "scope": "eq.rt", "basis": "eq.live",
        "as_of": f"gte.{kst_iso(day_start(today))}", "order": "as_of.desc", "limit": 1,
    }).json()
    if not rows or not isinstance(rows, list):
        return None
    r = rows[0]
    return Judgment(r["state_code"], r["state_sectors"], r["state_conf"], r["label_kr"], r["metrics"] or {})


def has_row_since(db: Supa, table: str, filters: dict, since: datetime, ts_col: str = "ts") -> bool:
    params = dict(filters, select=ts_col, limit=1)
    params[ts_col] = f"gte.{kst_iso(since)}"
    r = db.client.get(f"{db.url}/rest/v1/{table}", headers=db.headers, params=params)
    return r.status_code == 200 and bool(r.json())
