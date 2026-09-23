"""
오딘 3.0 model_module — 매매모델이 DB(Supabase)에서 입력을 읽어오는 함수 모음 (읽기 전용).

읽는 표: pick_list(매매 리스트) · state_market_temp(시장 온도) · raw_stock_ohlc(종목 일봉) · dim_stock(이름만)
쓰는 표: 없음 — 매매모델 1호의 output은 SQLite 기록 파일에만 남긴다(order_intent 표는 아직 없음, README 참조).
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

from supa import Supa
from trade_rules import LIST_MODEL_ID, MARKETS, STATE_RT_MODEL_ID, STATE_W_MODEL_ID

KST = timezone(timedelta(hours=9))
CLOSE_AS_OF = time(15, 30)
BAR_COLS = "date,stock_code,market,open,high,low,close,prev_close,change_pct,volume,trade_value,source"


def close_iso(d: date) -> str:
    """그날 종가 확정 시각(15:30 KST) — 상태 모듈·리스트 모듈이 as_of에 찍는 값과 같은 규약."""
    return datetime.combine(d, CLOSE_AS_OF, KST).isoformat()


def to_kst(ts: str | None) -> str | None:
    if not ts:
        return ts
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(KST).isoformat()
    except ValueError:
        return ts


def load_list(db: Supa, trade_date: date) -> list[dict]:
    """오늘 볼 종목(아침 확정 리스트). 같은 종목이 여러 줄이면 as_of가 늦은 줄만(최신 줄 규칙)."""
    rows = db.select("pick_list", {"select": "ts,as_of,list_date,market,model_id,phase,basis,stock_code,rank_no,score,reasons,warn,label_kr,metrics,rule_ver",
                                   "list_date": f"eq.{trade_date.isoformat()}", "model_id": f"eq.{LIST_MODEL_ID}",
                                   "phase": "eq.open", "order": "market,rank_no"})
    latest: dict[tuple, dict] = {}
    for r in rows:
        k = (r["market"], r["stock_code"])
        if k not in latest or r["as_of"] > latest[k]["as_of"]:
            latest[k] = r
    out = sorted(latest.values(), key=lambda r: (r["market"], r.get("rank_no") or 9999))
    for r in out:
        r["ts"], r["as_of"] = to_kst(r["ts"]), to_kst(r["as_of"])
    return out


def load_state_rt(db: Supa, prev_td: date) -> dict[str, dict | None]:
    """직전 거래일 종가 판정(basis=eod) 한 줄씩. 없으면 None(모델은 보수적으로 매수 금지)."""
    rows = db.select("state_market_temp", {"select": "ts,as_of,market,model_id,scope,basis,state_code,state_sectors,state_conf,label_kr,rule_ver",
                                           "model_id": f"eq.{STATE_RT_MODEL_ID}", "scope": "eq.rt", "basis": "eq.eod",
                                           "as_of": f"eq.{close_iso(prev_td)}", "order": "market,ts.desc"})
    out: dict[str, dict | None] = {mk: None for mk in MARKETS}
    for r in rows:
        if r["market"] in out and out[r["market"]] is None:
            r["ts"], r["as_of"] = to_kst(r["ts"]), to_kst(r["as_of"])
            out[r["market"]] = r
    return out


def load_state_w(db: Supa, prev_td: date) -> dict[str, dict | None]:
    """주간 무드: 직전 거래일 15:30 이전의 가장 최신 줄(종가 판정이든 장중 판정이든). 날짜가 직전 거래일보다 오래됐으면 stale 표시."""
    out: dict[str, dict | None] = {}
    for mk in MARKETS:
        rows = db.select("state_market_temp", {"select": "ts,as_of,market,model_id,scope,basis,state_code,label_kr,rule_ver,metrics",
                                               "model_id": f"eq.{STATE_W_MODEL_ID}", "scope": "eq.w", "market": f"eq.{mk}",
                                               "as_of": f"lte.{close_iso(prev_td)}", "order": "as_of.desc", "limit": 1})
        if rows:
            r = rows[0]
            r["ts"], r["as_of"] = to_kst(r["ts"]), to_kst(r["as_of"])
            r["stale"] = r["as_of"][:10] != prev_td.isoformat()
            out[mk] = r
        else:
            out[mk] = None
    return out


def load_bars(db: Supa, need: dict[str, set[str]]) -> dict[str, dict[str, dict]]:
    """{날짜: {종목코드…}} → {날짜: {종목코드: 일봉}}. 종목은 100개씩 끊어 조회."""
    out: dict[str, dict[str, dict]] = {}
    for d, codes in need.items():
        out[d] = {}
        codes = sorted(codes)
        for i in range(0, len(codes), 100):
            chunk = codes[i:i + 100]
            rows = db.select("raw_stock_ohlc", {"select": BAR_COLS, "date": f"eq.{d}", "stock_code": f"in.({','.join(chunk)})", "order": "stock_code"})
            for r in rows:
                out[d][r["stock_code"]] = r
    return out


def load_names(db: Supa, codes: set[str]) -> dict[str, str]:
    out = {}
    codes = sorted(codes)
    for i in range(0, len(codes), 100):
        chunk = codes[i:i + 100]
        for r in db.select("dim_stock", {"select": "stock_code,name", "stock_code": f"in.({','.join(chunk)})"}):
            out[r["stock_code"]] = r["name"]
    return out
