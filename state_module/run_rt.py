"""
오딘 3.0 state_module — 실시간 판별기 장중 루프 (마트 1호 예측모델 rule-temp-rt-v1, basis=live).

매분 하는 일 (정규장 09:00~15:30, 한국시간):
  1) raw_market_snapshot에서 이번 분 지수·대장주 시세를 읽는다 (data_pipeline/collect_rt.py가 채움)
  2) 장중 파라미터 계산 → state_params(scope=intraday)에 append
  3) 규칙 판정(rt_rules) + 3분 연속 전환(깜빡임 방지) → state_market_temp(basis=live)에 append
아침 첫 실행 때 배경 파라미터(bg_daily)를 하루 한 번 state_params에 남긴다.

실행 (프로젝트 루트에서):
  uv run --project state_module python state_module/run_rt.py              # 장 마감까지 매분
  uv run --project state_module python state_module/run_rt.py --once --dry-run   # 지금 한 분만, 쓰기 없이
  uv run --project state_module python state_module/run_rt.py --replay 2026-09-15 # 그날 쌓인 시세로 재생(기본 쓰기 없음)
"""

from __future__ import annotations

import argparse
import json
import sys
import time as systime
from datetime import date, datetime, time, timedelta

import w_rules   # 주간 판정도 장중 실시간으로 같이 돌린다(README "갱신 = 실시간" 확정, 2026-09-15 연결)
from rt_data import (KST, MARKETS, day_start, has_row_since, index_amount_history, kst_iso, latest_by_code,
                     load_leaders, load_ohlc, load_ohlc_hl, load_snapshots, restore_hold)
from rt_params import CALC_VER, bg_daily, intraday_money_ratio
from rt_rules import MODEL_ID, RULE_VER, StateHold, breadth_from_changes, classify
from supa import Supa

OPEN, CLOSE = time(9, 0), time(15, 30)
PROCESS_DELAY_S = 25     # 수집기가 매분 :03초에 받아 쓰므로, 여유를 두고 :25초에 판정
STALE_MINUTES = 3        # 지수 시세가 이보다 오래됐으면 그 분은 판정하지 않는다(낡은 값으로 '살아있음' 위장 금지)


def weekly_bg(ohlc: list[dict], today: date) -> dict | None:
    """어제까지의 일봉으로 주간 판정 배경(하루 불변)을 만든다. 일봉이 5일 미만이면 주간 판정 불가(None)."""
    if len(ohlc) < w_rules.WINDOW_DAYS:
        return None
    ranges = w_rules.day_ranges(ohlc)
    thr, hist_n = w_rules.vol_threshold(w_rules.avg_ranges(ranges), today.isoformat())
    return {
        "prev_close": float(ohlc[-1]["close"]),                      # 어제 종가
        "prev5_close": float(ohlc[-w_rules.WINDOW_DAYS]["close"]),   # 5거래일 전 종가(오늘 포함 창의 시작점)
        "last4_ranges": [v for _, v in ranges[-(w_rules.WINDOW_DAYS - 1):]],   # 직전 4일 하루 진폭
        "rng_threshold": thr, "rng_hist_n": hist_n,
    }


class Ctx:
    """하루 동안 바뀌지 않는 재료(명부·배경·과거 같은 시각 대금) + 시장별 깜빡임 방지 상태."""

    def __init__(self, db: Supa, today: date, write: bool, restore: bool = True):
        self.db, self.today, self.write = db, today, write
        self.leaders = load_leaders(db)
        missing = [m for m in MARKETS if not self.leaders.get(m)]
        if missing:
            sys.exit(f"[중단] dim_basket에 대장주 명부가 없음: {missing} — data_pipeline/build_basket.py 먼저 실행")
        self.bg = {m: bg_daily(load_ohlc(db, m, today - timedelta(days=1)), today.isoformat()) for m in MARKETS}
        self.amount_hist = index_amount_history(db, today)
        self.hold = {m: StateHold(current=restore_hold(db, m, today) if restore else None) for m in MARKETS}
        # 주간 판정 배경(하루 동안 불변): 전일 종가·5거래일 전 종가·직전 4일 진폭·1년 진폭 기준
        self.wbg = {m: weekly_bg(load_ohlc_hl(db, m, today - timedelta(days=1)), today) for m in MARKETS}
        # 오늘 장중 지수 고가·저가 추적(1분 가격 기준). 라이브 재시작이면 오늘 쌓인 시세로 복원,
        # 재생(replay)이면 빈 채로 시작해 분 순서대로 채운다(미래 참조 금지).
        self.day_hilo: dict[str, list[float]] = {}
        if restore:
            snaps = load_snapshots(db, datetime.combine(today, OPEN, KST),
                                   datetime.now(KST) + timedelta(minutes=1), kinds=("index",))
            for s in snaps:
                if s.get("price") is not None:
                    hl = self.day_hilo.setdefault(s["market"], [float(s["price"]), float(s["price"])])
                    hl[0] = max(hl[0], float(s["price"]))
                    hl[1] = min(hl[1], float(s["price"]))

    def same_time_amounts(self, market: str, hhmm: str) -> list[float]:
        days = self.amount_hist.get(market, {})
        return [days[d][hhmm] for d in sorted(days) if hhmm in days[d]]


def write_bg_once(ctx: Ctx) -> None:
    """배경 파라미터를 오늘 아직 안 남겼으면 남긴다(시장별 하루 1줄)."""
    rows = []
    for m, p in ctx.bg.items():
        if ctx.write and has_row_since(ctx.db, "state_params", {"market": f"eq.{m}", "scope": "eq.bg_daily"},
                                       day_start(ctx.today)):
            continue
        rows.append({"ts": kst_iso(datetime.now(KST)), "market": m, "scope": "bg_daily", "params": p,
                     "calc_ver": CALC_VER})
    emit(ctx, "state_params", rows)


def emit(ctx: Ctx, table: str, rows: list[dict]) -> None:
    if not rows:
        return
    if ctx.write:
        ctx.db.insert(table, rows)
    else:
        for r in rows:
            print(f"  (dry-run) {table}: {json.dumps(r, ensure_ascii=False)[:400]}")


def judge_minute(ctx: Ctx, minute: datetime, snaps: list[dict]) -> None:
    """minute(분 단위, KST)에 대해 두 시장을 판정해 기록한다. snaps = 그 분 근처의 시세 줄."""
    latest = latest_by_code(snaps)
    hhmm = minute.strftime("%H:%M")
    params_rows, temp_rows = [], []
    for market, index_code in MARKETS.items():
        ix = latest.get((market, "index", index_code))
        if ix is None:
            print(f"  [{hhmm}] {market}: 지수 시세 없음 — 판정 건너뜀")
            continue
        ix_ts = datetime.fromisoformat(ix["ts"]).astimezone(KST)
        if minute - ix_ts > timedelta(minutes=STALE_MINUTES) or ix["change_pct"] is None:
            print(f"  [{hhmm}] {market}: 지수 시세가 낡음({ix_ts:%H:%M}) — 판정 건너뜀")
            continue

        sector_chg, basket = {}, {}
        for code, sector in ctx.leaders[market].items():
            row = latest.get((market, "stock", code))
            if row is None or row["change_pct"] is None:
                continue
            if minute - datetime.fromisoformat(row["ts"]).astimezone(KST) > timedelta(minutes=STALE_MINUTES):
                continue
            sector_chg[sector] = float(row["change_pct"])
            basket[sector] = code
        b = breadth_from_changes(sector_chg)

        bg = ctx.bg[market]
        money_ratio, hist_days = intraday_money_ratio(
            float(ix["acc_amount"]) if ix.get("acc_amount") is not None else None,
            ctx.same_time_amounts(market, hhmm))
        chg = float(ix["change_pct"])

        raw = classify(chg, money_ratio, bg.get("surge_threshold"), b)
        out = ctx.hold[market].update(raw)

        params_rows.append({"ts": kst_iso(minute), "market": market, "scope": "intraday", "calc_ver": CALC_VER,
                            "params": {"index_price": float(ix["price"]), "index_chg": chg,
                                       "acc_amount": ix.get("acc_amount"), "money_ratio": money_ratio,
                                       "money_hist_days": hist_days, "surge_threshold": bg.get("surge_threshold"),
                                       "breadth_n": b.n, "breadth_up": b.up, "breadth_down": b.down,
                                       "breadth_u05": b.u05, "breadth_d05": b.d05, "sector_chg": sector_chg}})
        temp_rows.append({
            "ts": kst_iso(datetime.now(KST)), "as_of": kst_iso(ix_ts), "market": market, "model_id": MODEL_ID,
            "scope": "rt", "horizon": "now", "basis": "live",
            "state_code": out.state_code, "state_sectors": out.state_sectors, "state_conf": out.state_conf,
            "breadth_up": b.up, "breadth_down": b.down, "breadth_total": b.n, "label_kr": out.label_kr,
            "metrics": dict(out.metrics, basket=basket, money_hist_days=hist_days), "rule_ver": RULE_VER,
        })
        # ── 주간 판정(초안 모델)도 매분 같이 — 재료는 1분 지수 가격으로 만든 오늘 고저·5일 누적·진폭 ──
        weekly_note = ""
        wb = ctx.wbg.get(market)
        if wb is not None:
            px = float(ix["price"])
            hl = ctx.day_hilo.setdefault(market, [px, px])   # [고가, 저가]
            hl[0] = max(hl[0], px)
            hl[1] = min(hl[1], px)
            low_pct = w_rules.pct_change(hl[1], wb["prev_close"])
            chg5 = w_rules.pct_change(px, wb["prev5_close"])
            rng_today = (hl[0] - hl[1]) / wb["prev_close"] * 100.0
            r4 = wb["last4_ranges"]
            rng5 = (sum(r4) + rng_today) / w_rules.WINDOW_DAYS if len(r4) == w_rules.WINDOW_DAYS - 1 else None
            wj = w_rules.classify(low_pct, chg5, rng5, wb["rng_threshold"])
            wj.metrics["chg_today"] = round(w_rules.pct_change(px, wb["prev_close"]), 4)
            wj.metrics["rng_hist_n"] = wb["rng_hist_n"]
            temp_rows.append({
                "ts": kst_iso(datetime.now(KST)), "as_of": kst_iso(ix_ts), "market": market,
                "model_id": w_rules.MODEL_ID, "scope": "w", "horizon": "now", "basis": "live",
                "state_code": wj.state_code, "state_sectors": None, "state_conf": None,
                "breadth_up": None, "breadth_down": None, "breadth_total": None,
                "label_kr": wj.label_kr, "metrics": wj.metrics, "rule_ver": w_rules.RULE_VER,
            })
            weekly_note = f" · 주간 {wj.label_kr}"

        held = f" (원판정 {raw.state_code}, {out.metrics.get('pending_minutes')}분째)" if raw.state_code != out.state_code else ""
        mr = "-" if money_ratio is None else f"{money_ratio:.2f}"
        print(f"  [{hhmm}] {market}: {out.label_kr}{held} · 지수 {chg:+.2f}% · 폭 {b.down}↓/{b.up}↑/{b.n} · 대금배율 {mr}{weekly_note}")
    emit(ctx, "state_params", params_rows)
    emit(ctx, "state_market_temp", temp_rows)


def floor_minute(dt: datetime) -> datetime:
    return dt.replace(second=0, microsecond=0)


def run_live(ctx: Ctx, once: bool) -> None:
    write_bg_once(ctx)
    while True:
        now = datetime.now(KST)
        minute = floor_minute(now)
        if not once and now.time() < OPEN:
            systime.sleep(min(60.0, (datetime.combine(ctx.today, OPEN, KST) - now).total_seconds() + 1))
            continue
        if not once and minute.time() > CLOSE:
            print("[끝] 장 마감 — 루프 종료")
            return
        if not once and now.second < PROCESS_DELAY_S:
            systime.sleep(PROCESS_DELAY_S - now.second)
            continue
        try:
            snaps = load_snapshots(ctx.db, minute - timedelta(minutes=STALE_MINUTES), minute + timedelta(minutes=1))
            judge_minute(ctx, minute, snaps)
        except Exception as e:   # 한 분 실패가 루프를 멈추지 않게(다음 분에 다시)
            print(f"  [{minute:%H:%M}] 실패: {e}")
        if once:
            return
        next_tick = minute + timedelta(minutes=1, seconds=PROCESS_DELAY_S)
        systime.sleep(max(1.0, (next_tick - datetime.now(KST)).total_seconds()))


def run_replay(ctx: Ctx, d: date) -> None:
    """그날 쌓인 시세로 분 단위 판정을 처음부터 다시 돌린다(규칙 점검용)."""
    snaps = load_snapshots(ctx.db, datetime.combine(d, OPEN, KST) - timedelta(minutes=STALE_MINUTES),
                           datetime.combine(d, CLOSE, KST) + timedelta(minutes=1))
    print(f"[재생] {d} 시세 {len(snaps)}줄")
    parsed = [(datetime.fromisoformat(s["ts"]).astimezone(KST), s) for s in snaps]
    minute = datetime.combine(d, OPEN, KST)
    while minute.time() <= CLOSE:
        lo = minute - timedelta(minutes=STALE_MINUTES)
        window = [s for t, s in parsed if lo <= t < minute + timedelta(minutes=1)]
        if window:
            judge_minute(ctx, minute, window)
        minute += timedelta(minutes=1)


def main() -> None:
    ap = argparse.ArgumentParser(description="실시간 판별기 장중 루프")
    ap.add_argument("--once", action="store_true", help="지금 한 분만 판정하고 끝")
    ap.add_argument("--dry-run", action="store_true", help="DB에 쓰지 않고 화면에만 출력")
    ap.add_argument("--replay", metavar="YYYY-MM-DD", help="그날 쌓인 시세로 재생 (기본 쓰기 없음)")
    ap.add_argument("--write", action="store_true", help="--replay 결과를 DB에 기록(주의: append라 중복 기록됨)")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")   # Windows 콘솔(cp949)에서 '—' 등 출력 오류 방지

    if args.replay:
        d = date.fromisoformat(args.replay)
        write = args.write
        ctx = Ctx(Supa(write=write), d, write, restore=False)
        run_replay(ctx, d)
        return

    today = datetime.now(KST).date()
    if today.weekday() >= 5 and not args.once:
        print("[끝] 주말 — 판정 없음")
        return
    write = not args.dry_run
    ctx = Ctx(Supa(write=write), today, write)
    print(f"[시작] {today} 실시간 판별기 ({'기록' if write else 'dry-run'}) · 대장주 "
          + ", ".join(f"{m} {len(v)}개" for m, v in ctx.leaders.items()))
    run_live(ctx, args.once)


if __name__ == "__main__":
    main()
