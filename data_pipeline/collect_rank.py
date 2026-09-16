"""
오딘 3.0 data_pipeline — KIS 순위 조회 축적 → raw_rank_snapshot (매매 리스트의 장중 재료 + 수급 이력).

무엇을: 전체 시장 상위 30 (⚠️ 시장별 조회 코드 0001/1001은 세 TR 모두 빈 결과 — 2026-09-17 실측. 전체(0000)만 되므로
  코스피·코스닥이 섞인 상위 30이고, market 칸은 dim_stock으로 붙인다. 시장별 상위 K는 밤 일봉(raw_stock_ohlc)이 맡는다.)
  - 거래량 순위 TR(FHPST01710000) 정렬 3가지: volume(거래량) · vol_inc(거래 증가율) · amount(거래대금)
  - 등락률 순위 TR(FHPST01700000): gainer(상승) · loser(하락) — 전일 종가 대비(FID_PRC_CLS_CODE=1,
    저가 대비로 두면 상한가 종목이 30위 밖으로 밀리는 옛 오딘 교훈)
  - 외인/기관 순매수·순매도 순위 TR(FHPTJ04400000), 금액 기준: foreign_buy/sell · inst_buy/sell
    (새벽엔 빈 결과 — 장중·장 마감 직후에만 값이 오는 것으로 추정, 첫 장중 실측으로 확인)
→ 한 바퀴 = 3 + 2 + 4 = 9콜. 10분마다 + 장 마감 뒤 15:35에 한 번 더(그날 확정 순위 = 수급 이력).

규칙: append만, ts = 조회한 분. 휴장일엔 KIS가 전 거래일 순위를 그대로 주므로 저장하지 않는다.
      원천이라 ETF·스팩 등을 거르지 않는다(리스트 유니버스 단계의 몫). dim_stock에 없는 코드(ETF 등)는 market='KR-ETC'.

실행 (프로젝트 루트에서):
  uv run --project data_pipeline python data_pipeline/collect_rank.py                 # 09:00~15:35 10분 루프
  uv run --project data_pipeline python data_pipeline/collect_rank.py --once --dry-run  # 지금 한 바퀴, 쓰기 없이
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time as systime
from datetime import datetime, time, timedelta, timezone

import httpx

from kis import Kis, KisError, num

KST = timezone(timedelta(hours=9))
OPEN, LAST = time(9, 0), time(15, 35)
STEP_MIN = 10
TOP_N = 30
ALL_MARKETS = "0000"                                  # KIS 순위 TR의 시장 코드 — 전체만 동작(0001/1001은 빈 결과)
MARKET_CODE = {"KOSPI": "KR-KOSPI", "KOSDAQ": "KR-KOSDAQ"}

# 거래량 순위 TR의 정렬 기준(FID_BLNG_CLS_CODE): 0 평균거래량 · 1 거래증가율 · 2 평균거래회전율 · 3 거래금액순
VOLUME_KINDS = {"volume": "0", "vol_inc": "1", "amount": "3"}
INVESTOR_KINDS = {  # (투자자 1=외인 2=기관, 정렬 0=순매수 1=순매도, 응답 필드 접두)
    "foreign_buy": ("1", "0", "frgn"), "foreign_sell": ("1", "1", "frgn"),
    "inst_buy": ("2", "0", "orgn"), "inst_sell": ("2", "1", "orgn"),
}


class Db:
    def __init__(self, write: bool):
        self.url = (os.environ.get("SUPABASE_URL") or "").strip().rstrip("/")
        key = (os.environ.get("SUPABASE_SERVICE_KEY") or "").strip() or (
            "" if write else (os.environ.get("SUPABASE_ANON_KEY") or "").strip())
        if not self.url or not key:
            sys.exit("[오류] SUPABASE_URL / SUPABASE_SERVICE_KEY 가 .env 에 없습니다.")
        self.h = {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    def market_map(self) -> dict[str, str]:
        """dim_stock 종목코드 → 3.0 시장코드 (순위 줄에 시장을 붙이는 용도)."""
        out, offset = {}, 0
        while True:
            r = httpx.get(f"{self.url}/rest/v1/dim_stock", headers=self.h, timeout=30.0, params={
                "select": "stock_code,market", "market": "in.(KOSPI,KOSDAQ)", "order": "stock_code", "limit": 1000, "offset": offset})
            r.raise_for_status()
            rows = r.json()
            out.update({x["stock_code"]: MARKET_CODE[x["market"]] for x in rows})
            if len(rows) < 1000:
                return out
            offset += 1000

    def insert(self, rows: list[dict]) -> None:
        r = httpx.post(f"{self.url}/rest/v1/raw_rank_snapshot", headers=dict(self.h, Prefer="return=minimal"),
                       content=json.dumps(rows, ensure_ascii=False), timeout=30.0)
        if r.status_code not in (200, 201, 204):
            raise RuntimeError(f"raw_rank_snapshot 쓰기 실패 {r.status_code}: {r.text[:300]}")


def is_open_day(kis: Kis, today: datetime) -> bool:
    if today.weekday() >= 5:
        return False
    ymd = today.strftime("%Y%m%d")
    try:
        body = kis.get("/uapi/domestic-stock/v1/quotations/chk-holiday", "CTCA0903R",
                       {"BASS_DT": ymd, "CTX_AREA_NK": "", "CTX_AREA_FK": ""})
        for item in body.get("output") or []:
            if item.get("bass_dt") == ymd:
                return item.get("opnd_yn") == "Y"
    except KisError as e:
        print(f"[경고] 휴장일 조회 실패({e}) — 평일이라 개장으로 보고 진행")
    return True


def _base(ts: datetime, kind: str, rank: int, o: dict, code_key: str) -> dict:
    return {"ts": ts.isoformat(), "market": None, "kind": kind, "rank_no": rank,   # market은 one_round에서 붙인다
            "stock_code": (o.get(code_key) or "").strip(), "name": (o.get("hts_kor_isnm") or "").strip() or None,
            "price": num(o.get("stck_prpr")), "change_pct": num(o.get("prdy_ctrt")),
            "acc_volume": int(num(o.get("acml_vol")) or 0) or None,
            "acc_amount": int(num(o.get("acml_tr_pbmn")) or 0) or None, "source": "kis"}


def volume_rank(kis: Kis, ts: datetime, kind: str, blng: str) -> list[dict]:
    out = kis.get("/uapi/domestic-stock/v1/quotations/volume-rank", "FHPST01710000", {
        "FID_COND_MRKT_DIV_CODE": "J", "FID_COND_SCR_DIV_CODE": "20171",   # 화면코드 20171이어야 데이터가 온다(옛 오딘 실측)
        "FID_INPUT_ISCD": ALL_MARKETS, "FID_DIV_CLS_CODE": "0", "FID_BLNG_CLS_CODE": blng,
        "FID_TRGT_CLS_CODE": "111111111", "FID_TRGT_EXLS_CLS_CODE": "000000",
        "FID_INPUT_PRICE_1": "0", "FID_INPUT_PRICE_2": "0", "FID_VOL_CNT": "0", "FID_INPUT_DATE_1": ""}).get("output") or []
    return [_base(ts, kind, i, o, "mksc_shrn_iscd") for i, o in enumerate(out[:TOP_N], 1)]


def fluct_rank(kis: Kis, ts: datetime, kind: str) -> list[dict]:
    out = kis.get("/uapi/domestic-stock/v1/ranking/fluctuation", "FHPST01700000", {
        "FID_COND_MRKT_DIV_CODE": "J", "FID_COND_SCR_DIV_CODE": "20170", "FID_INPUT_ISCD": ALL_MARKETS,
        "FID_RANK_SORT_CLS_CODE": "0" if kind == "gainer" else "1", "FID_INPUT_CNT_1": "0",
        "FID_PRC_CLS_CODE": "1",   # 전일 종가 대비
        "FID_INPUT_PRICE_1": "0", "FID_INPUT_PRICE_2": "0", "FID_VOL_CNT": "0",
        "FID_TRGT_CLS_CODE": "0", "FID_TRGT_EXLS_CLS_CODE": "0", "FID_DIV_CLS_CODE": "0",
        "FID_RSFL_RATE1": "", "FID_RSFL_RATE2": ""}).get("output") or []
    return [_base(ts, kind, i, o, "stck_shrn_iscd") for i, o in enumerate(out[:TOP_N], 1)]


def investor_rank(kis: Kis, ts: datetime, kind: str) -> list[dict]:
    etc, sort_cls, pre = INVESTOR_KINDS[kind]
    out = kis.get("/uapi/domestic-stock/v1/quotations/foreign-institution-total", "FHPTJ04400000", {
        "FID_COND_MRKT_DIV_CODE": "V", "FID_COND_SCR_DIV_CODE": "16449", "FID_INPUT_ISCD": ALL_MARKETS,
        "FID_DIV_CLS_CODE": "1",   # 금액 기준
        "FID_RANK_SORT_CLS_CODE": sort_cls, "FID_ETC_CLS_CODE": etc}).get("output") or []
    rows = []
    for i, o in enumerate(out[:TOP_N], 1):
        r = _base(ts, kind, i, o, "mksc_shrn_iscd")
        r["net_volume"] = int(num(o.get(f"{pre}_ntby_qty")) or 0)
        r["net_amount"] = int((num(o.get(f"{pre}_ntby_tr_pbmn")) or 0) * 1_000_000)   # 백만원 → 원
        rows.append(r)
    return rows


def one_round(kis: Kis, ts: datetime, market_of: dict[str, str]) -> list[dict]:
    rows = []
    jobs = [(k, lambda k=k, b=b: volume_rank(kis, ts, k, b)) for k, b in VOLUME_KINDS.items()]
    jobs += [(k, lambda k=k: fluct_rank(kis, ts, k)) for k in ("gainer", "loser")]
    jobs += [(k, lambda k=k: investor_rank(kis, ts, k)) for k in INVESTOR_KINDS]
    for kind, fn in jobs:
        try:
            got = fn()
        except KisError as e:
            print(f"  [{ts:%H:%M}] {kind} 실패: {e}")
            continue
        for r in got:
            if r["stock_code"]:
                r["market"] = market_of.get(r["stock_code"], "KR-ETC")   # dim_stock에 없는 코드(ETF 등)
                rows.append(r)
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description="KIS 순위 조회 축적 → raw_rank_snapshot")
    ap.add_argument("--once", action="store_true", help="지금 한 바퀴만")
    ap.add_argument("--dry-run", action="store_true", help="DB에 쓰지 않고 요약만")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    db, kis = Db(write=not args.dry_run), Kis()
    now = datetime.now(KST)
    if not args.once and not is_open_day(kis, now):
        print(f"[끝] {now:%Y-%m-%d} 휴장일 — 순위 저장 없음")
        return
    market_of = db.market_map()
    print(f"[시작] 순위 축적 ({'dry-run' if args.dry_run else '기록'}) · 한 바퀴 {len(VOLUME_KINDS) + 2 + len(INVESTOR_KINDS)}콜 · 시장 대조 {len(market_of)}종목")

    while True:
        now = datetime.now(KST)
        minute = now.replace(second=0, microsecond=0)
        if not args.once:
            if now.time() < OPEN:
                systime.sleep(min(60.0, (datetime.combine(now.date(), OPEN, KST) - now).total_seconds() + 5))
                continue
            if minute.time() > LAST:
                print("[끝] 15:35 지나 종료")
                return
        try:
            rows = one_round(kis, minute, market_of)
            if args.dry_run:
                by_kind = {}
                for r in rows:
                    by_kind.setdefault(r["kind"], []).append(r)
                for k, v in sorted(by_kind.items()):
                    top = v[0]
                    mk = {m: sum(1 for r in v if r["market"] == m) for m in ("KR-KOSPI", "KR-KOSDAQ", "KR-ETC")}
                    print(f"  {k:<12} {len(v):>2}건 {mk} · 1위 {top['name']}({top['stock_code']}) {top['change_pct']}% "
                          f"대금 {top['acc_amount']} 순매수 {top.get('net_amount')}")
            elif rows:
                db.insert(rows)
            print(f"  [{minute:%H:%M}] {len(rows)}줄")
        except Exception as e:   # 한 바퀴 실패가 루프를 멈추지 않게
            print(f"  [{minute:%H:%M}] 실패: {e}")
        if args.once:
            return
        # 다음 10분 경계(09:00, 09:10, …) 또는 15:35 마감 1회
        nxt = minute + timedelta(minutes=STEP_MIN - (minute.minute % STEP_MIN))
        if minute.time() < LAST < nxt.time():
            nxt = datetime.combine(minute.date(), LAST, KST)
        systime.sleep(max(1.0, (nxt + timedelta(seconds=5) - datetime.now(KST)).total_seconds()))


if __name__ == "__main__":
    main()
