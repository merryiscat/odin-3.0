"""
오딘 3.0 data_pipeline — 실시간 1분 시세 수집 → raw_market_snapshot (실시간 판별기의 원료).

매분 :03초에 (정규장 09:00~15:30, 한국시간):
  - 지수 2개(코스피 0001·코스닥 1001) 현재가·등락률·누적 대금  : inquire-index-price (FHPUP02100000) × 2콜
  - 업종 대장주 바스켓(dim_basket leader, 시장별 ~21개) 현재가·등락률 : intstock-multprice (FHKST11300006, 30종목/콜) × 2콜
→ 분당 4콜, 하루 약 1,560콜. 옛 오딘과 같은 앱키로 동시에 돈다(2026-09-14 사용자 결정).

저장 규칙: ts = 수집한 "분"(초 버림, 한국시간), source='kis'. append만.
단위 주의: 지수 acc_amount는 KIS 원값(백만원 단위로 추정 — raw_index_ohlc.trade_value와 같은 단위),
          종목 acc_amount는 원 단위. 첫 분에 전일 대금을 로그로 찍어 일봉과 대조한다.

실행 (프로젝트 루트에서):
  uv run --project data_pipeline python data_pipeline/collect_rt.py              # 장 마감까지 매분
  uv run --project data_pipeline python data_pipeline/collect_rt.py --once --dry-run   # 지금 한 번, 쓰기 없이
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
OPEN, CLOSE = time(9, 0), time(15, 30)
COLLECT_AT_S = 3
INDEX = {"KR-KOSPI": "0001", "KR-KOSDAQ": "1001"}
MULTI_MAX = 30


class Db:
    def __init__(self, write: bool):
        self.url = (os.environ.get("SUPABASE_URL") or "").strip().rstrip("/")
        key = (os.environ.get("SUPABASE_SERVICE_KEY") or "").strip() or (
            "" if write else (os.environ.get("SUPABASE_ANON_KEY") or "").strip())
        if not self.url or not key:
            sys.exit("[오류] SUPABASE_URL / SUPABASE_SERVICE_KEY 가 .env 에 없습니다.")
        self.h = {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    def leaders(self) -> list[tuple[str, str]]:
        r = httpx.get(f"{self.url}/rest/v1/dim_basket", headers=self.h, timeout=20.0, params={
            "select": "market,stock_code", "basket_type": "eq.leader", "order": "market,stock_code"})
        r.raise_for_status()
        return [(x["market"], x["stock_code"]) for x in r.json()]

    def insert(self, rows: list[dict]) -> None:
        r = httpx.post(f"{self.url}/rest/v1/raw_market_snapshot", headers=dict(self.h, Prefer="return=minimal"),
                       content=json.dumps(rows), timeout=20.0)
        if r.status_code not in (200, 201, 204):
            raise RuntimeError(f"raw_market_snapshot 쓰기 실패 {r.status_code}: {r.text[:300]}")


def is_open_day(kis: Kis, today: datetime) -> bool:
    """KIS 휴장일 조회 — 원장 부담 때문에 하루 1회만 부르라는 권고가 있어 시작할 때 한 번만."""
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


def collect_index(kis: Kis, minute: datetime, first: bool) -> list[dict]:
    rows = []
    for market, code in INDEX.items():
        try:
            o = kis.get("/uapi/domestic-stock/v1/quotations/inquire-index-price", "FHPUP02100000",
                        {"FID_COND_MRKT_DIV_CODE": "U", "FID_INPUT_ISCD": code}).get("output") or {}
        except KisError as e:
            print(f"  [{minute:%H:%M}] 지수 {market} 실패: {e}")
            continue
        price = num(o.get("bstp_nmix_prpr"))
        if price is None:
            continue
        if first:
            print(f"  [단위 점검] {market} 전일 거래대금(prdy_tr_pbmn)={o.get('prdy_tr_pbmn')} "
                  f"— raw_index_ohlc 전일 trade_value와 같으면 백만원 단위")
        rows.append({"ts": minute.isoformat(), "market": market, "kind": "index", "code": code, "price": price,
                     "change_pct": num(o.get("bstp_nmix_prdy_ctrt")),
                     "acc_volume": int(num(o.get("acml_vol")) or 0) or None,
                     "acc_amount": int(num(o.get("acml_tr_pbmn")) or 0) or None, "source": "kis"})
    return rows


def collect_stocks(kis: Kis, minute: datetime, basket: list[tuple[str, str]]) -> list[dict]:
    market_of = {code: mk for mk, code in basket}
    codes = list(market_of)
    rows = []
    for i in range(0, len(codes), MULTI_MAX):
        chunk = codes[i:i + MULTI_MAX]
        params = {}
        for n in range(1, len(chunk) + 1):   # 쓰는 칸만 보낸다 — 빈 칸을 빈값으로 보내면
            params[f"FID_COND_MRKT_DIV_CODE_{n}"] = "J"   # OPSQ2002 INVALID INPUT(빈 시장구분 거부)이 난다
            params[f"FID_INPUT_ISCD_{n}"] = chunk[n - 1]  # (2026-09-15 실측 — 30칸 꽉 찬 묶음만 성공했었음)
        try:
            out = kis.get("/uapi/domestic-stock/v1/quotations/intstock-multprice", "FHKST11300006", params).get("output") or []
        except KisError as e:
            print(f"  [{minute:%H:%M}] 대장주 묶음 {i // MULTI_MAX + 1} 실패: {e}")
            continue
        for o in out if isinstance(out, list) else [out]:
            code = (o.get("inter_shrn_iscd") or "").strip()
            price = num(o.get("inter2_prpr"))
            if code not in market_of or price is None:
                continue
            rows.append({"ts": minute.isoformat(), "market": market_of[code], "kind": "stock", "code": code,
                         "price": price, "change_pct": num(o.get("prdy_ctrt")),
                         "acc_volume": int(num(o.get("acml_vol")) or 0) or None,
                         "acc_amount": int(num(o.get("acml_tr_pbmn")) or 0) or None, "source": "kis"})
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description="실시간 1분 시세 수집 (KIS → raw_market_snapshot)")
    ap.add_argument("--once", action="store_true", help="지금 한 번만 수집하고 끝")
    ap.add_argument("--dry-run", action="store_true", help="DB에 쓰지 않고 출력만")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    db, kis = Db(write=not args.dry_run), Kis()
    basket = db.leaders()
    if not basket:
        sys.exit("[중단] dim_basket에 대장주 명부가 없음 — data_pipeline/build_basket.py 먼저 실행")
    now = datetime.now(KST)
    if not args.once and not is_open_day(kis, now):
        print(f"[끝] {now:%Y-%m-%d} 휴장일 — 수집 없음")
        return
    print(f"[시작] 1분 수집 ({'dry-run' if args.dry_run else '기록'}) · 지수 {len(INDEX)} + 대장주 {len(basket)}종목")

    first = True
    while True:
        now = datetime.now(KST)
        minute = now.replace(second=0, microsecond=0)
        if not args.once:
            if now.time() < OPEN:
                systime.sleep(min(30.0, (datetime.combine(now.date(), OPEN, KST) - now).total_seconds() + COLLECT_AT_S))
                continue
            if minute.time() > CLOSE:
                print("[끝] 장 마감 — 수집 종료")
                return
            if now.second < COLLECT_AT_S:
                systime.sleep(COLLECT_AT_S - now.second)
                continue
        try:
            rows = collect_index(kis, minute, first) + collect_stocks(kis, minute, basket)
            first = False
            if args.dry_run:
                for r in rows:
                    print(f"  (dry-run) {r}")
            elif rows:
                db.insert(rows)
            n_ix = sum(1 for r in rows if r["kind"] == "index")
            print(f"  [{minute:%H:%M}] 지수 {n_ix} · 종목 {len(rows) - n_ix}줄")
        except Exception as e:   # 한 분 실패가 루프를 멈추지 않게
            print(f"  [{minute:%H:%M}] 실패: {e}")
        if args.once:
            return
        next_tick = minute + timedelta(minutes=1, seconds=COLLECT_AT_S)
        systime.sleep(max(1.0, (next_tick - datetime.now(KST)).total_seconds()))


if __name__ == "__main__":
    main()
