# /// script
# requires-python = ">=3.11"
# dependencies = ["supabase>=2.0", "python-dotenv"]
# ///
"""옛 오딘 DB → 3.0 DB 밤 복사 스크립트.

무엇을 복사하나 (전부 "읽기만" — 옛 DB는 절대 안 고침):
  1) 지수 일봉:  옛 index_daily_ohlc  → 새 raw_index_ohlc
     (2024-08-22~2026-09-11 치 998줄은 2026-09-13에 이미 이관 완료 — 여기서는 그 뒤 새 날짜만)
  2) 지수 분봉:  옛 index_minute_price → 새 raw_market_snapshot (kind='index')
     (옛 오딘이 2026-09-14부터 쌓기 시작 — 매번 "마지막으로 복사한 시각 이후"만 가져옴)

실행법 (밤에, 장 마감 후):
  1. 이 폴더의 .env 파일에 주소·키 4개를 채운다 (.env.example 참고)
  2. copy_from_old.cmd 더블클릭  (또는 터미널에서: uv run copy_from_old.py)

주의: 서비스 키는 .env에만 둔다 — 절대 커밋 금지(.gitignore 확인).
"""

import os
import sys
from datetime import datetime

from dotenv import load_dotenv
from supabase import create_client

# ── 시장 코드 번역: 옛 표기 → 3.0 표기 ──────────────────────────────────
MARKET_MAP = {"KOSPI": "KR-KOSPI", "KOSDAQ": "KR-KOSDAQ"}
# 지수 코드(KRX 업종코드): raw_market_snapshot의 code 칸에 쓸 값
INDEX_CODE = {"KOSPI": "0001", "KOSDAQ": "1001"}

BATCH = 1000  # 한 번에 넣는 줄 수 (Supabase 안전선)


def get_clients():
    """옛/새 DB 접속. .env에서 주소·키를 읽는다."""
    load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
    need = ["OLD_SUPABASE_URL", "OLD_SUPABASE_SERVICE_KEY",
            "NEW_SUPABASE_URL", "NEW_SUPABASE_SERVICE_KEY"]
    missing = [k for k in need if not os.getenv(k)]
    if missing:
        sys.exit(f"[중단] .env에 값이 비어 있음: {', '.join(missing)} — .env.example 참고")
    old = create_client(os.environ["OLD_SUPABASE_URL"], os.environ["OLD_SUPABASE_SERVICE_KEY"])
    new = create_client(os.environ["NEW_SUPABASE_URL"], os.environ["NEW_SUPABASE_SERVICE_KEY"])
    return old, new


def fetch_all(client, table: str, filters) -> list[dict]:
    """옛 표에서 조건에 맞는 줄을 전부 페이지 넘겨가며 읽기."""
    out, start = [], 0
    while True:
        q = client.table(table).select("*")
        q = filters(q)
        rows = q.range(start, start + BATCH - 1).execute().data or []
        out.extend(rows)
        if len(rows) < BATCH:
            return out
        start += BATCH


def copy_daily(old, new) -> int:
    """일봉 복사 — 새 DB에 없는 날짜만. (date, market) upsert라 재실행 안전."""
    copied = 0
    for old_mkt, new_mkt in MARKET_MAP.items():
        # 새 DB에 이 시장의 마지막 날짜가 언제까지 있나
        last = (new.table("raw_index_ohlc").select("date")
                .eq("market", new_mkt).order("date", desc=True).limit(1).execute().data)
        since = last[0]["date"] if last else "1900-01-01"

        rows = fetch_all(old, "index_daily_ohlc",
                         lambda q: q.eq("market", old_mkt).gt("date", since).order("date"))
        payload = [{
            "date": r["date"], "market": new_mkt,
            "open": r.get("open"), "high": r.get("high"), "low": r.get("low"),
            "close": r["close"], "volume": r.get("volume"), "trade_value": r.get("trade_value"),
            "source": "old_odin",
        } for r in rows]
        for i in range(0, len(payload), BATCH):
            new.table("raw_index_ohlc").upsert(payload[i:i + BATCH],
                                               on_conflict="date,market").execute()
        copied += len(payload)
        print(f"  일봉 {new_mkt}: {since} 이후 {len(payload)}줄")
    return copied


def copy_minute(old, new) -> int:
    """분봉 복사 — 마지막으로 복사한 시각 이후만 (append 표라 중복 방지는 시각 기준)."""
    copied = 0
    for old_mkt, new_mkt in MARKET_MAP.items():
        # 새 DB에서 이 시장의 옛-오딘발 지수 스냅샷이 언제까지 있나
        last = (new.table("raw_market_snapshot").select("ts")
                .eq("market", new_mkt).eq("kind", "index").eq("source", "old_odin")
                .order("ts", desc=True).limit(1).execute().data)
        # ts는 'YYYY-MM-DDTHH:MM:SS+00:00' 형태 → 비교용 date/time으로 분해하는 대신
        # 옛 표가 (date, time) 칼럼이라 날짜 기준으로 넉넉히 자르고, 넣을 때 시각으로 거른다.
        if last:
            last_ts = datetime.fromisoformat(last[0]["ts"])
            since_date = last_ts.strftime("%Y-%m-%d")
        else:
            last_ts, since_date = None, "1900-01-01"

        rows = fetch_all(old, "index_minute_price",
                         lambda q: q.eq("market", old_mkt).gte("date", since_date)
                                    .order("date").order("time"))
        payload = []
        for r in rows:
            # 옛 (date, time) → 3.0 ts (한국 시간). 예: 2026-09-14 + 09:31:00 → 2026-09-14T09:31:00+09:00
            ts = datetime.fromisoformat(f"{r['date']}T{r['time']}+09:00")
            if last_ts and ts <= last_ts:
                continue  # 이미 복사한 분
            payload.append({
                "ts": ts.isoformat(), "market": new_mkt, "kind": "index",
                "code": INDEX_CODE[old_mkt], "price": r["price"],
                "change_pct": r.get("change_pct"), "source": "old_odin",
            })
        for i in range(0, len(payload), BATCH):
            new.table("raw_market_snapshot").insert(payload[i:i + BATCH]).execute()
        copied += len(payload)
        print(f"  분봉 {new_mkt}: {len(payload)}줄")
    return copied


if __name__ == "__main__":
    print(f"[복사 시작] {datetime.now():%Y-%m-%d %H:%M}")
    old, new = get_clients()
    d = copy_daily(old, new)
    m = copy_minute(old, new)
    print(f"[복사 끝] 일봉 {d}줄 + 분봉 {m}줄")
