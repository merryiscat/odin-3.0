"""
오딘 3.0 data_pipeline — 주식 로스터+시총 적재 (dim_stock 씨앗 채우기).

공공데이터포털 '금융위 주식시세정보'(GetStockSecuritiesInfoService/getStockPriceInfo)에서
최신 거래일의 전체 주식(보통주+우선주) 목록·시가총액·상장주식수를 받아 dim_stock에 upsert.

- 읽기: data.go.kr API  (환경변수 DATA_GO_KR_KEY)
- 쓰기: Supabase dim_stock (SUPABASE_URL + SUPABASE_SERVICE_KEY, Secret키라 RLS 우회)
- 채우는 칸: stock_code, name, market, market_cap, shares_out
  (ETF·ETN은 별도 API / type·상장일·상폐는 소스 확정 후 — api_spec.md 참고)

실행:  uv run python data_pipeline/ingest_krx_stock.py
"""

from __future__ import annotations

import json
import os
import sys
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from dotenv import load_dotenv

# .env 는 프로젝트 루트(이 파일의 상위 폴더 = odin_3.0)에 있다.
ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

# 주식시세정보(getStockPriceInfo): 보통주+우선주 로스터 + 시가총액 + 상장주식수를 한 번에 준다.
# (KRX상장종목정보/getItemInfo 는 보통주만 → dim_stock 뼈대엔 이 시세정보가 더 완전.
#  ETF·ETN은 또 다른 API라 여기엔 없음 → 나중에 별도 적재)
KRX_URL = "https://apis.data.go.kr/1160100/service/GetStockSecuritiesInfoService/getStockPriceInfo"


def _service_key_qs() -> str:
    """data.go.kr 서비스키를 쿼리스트링용 형태로 만든다.
    '%'가 있으면 이미 URL 인코딩된 'Encoding 키' → 그대로 사용,
    없으면 'Decoding 키' → URL 인코딩. (이중 인코딩으로 인증 실패 방지)"""
    key = (os.environ.get("DATA_GO_KR_KEY") or "").strip()
    if not key:
        sys.exit("[오류] DATA_GO_KR_KEY 가 .env 에 없습니다. scripts\\setup-keys.cmd 를 실행하세요.")
    return key if "%" in key else urllib.parse.quote(key, safe="")


SERVICE_KEY_QS = _service_key_qs()


def _get(params: dict) -> dict:
    """getItemInfo 1콜. serviceKey는 따로 처리(이중 인코딩 방지)하고 나머지만 인코딩해 URL을 만든다."""
    qs = "&".join(f"{k}={urllib.parse.quote(str(v), safe='')}" for k, v in params.items())
    url = f"{KRX_URL}?serviceKey={SERVICE_KEY_QS}&{qs}"
    r = httpx.get(url, timeout=20.0)
    # 키 오류 등은 JSON 대신 XML 에러가 오기도 한다 → 친절히 알린다.
    try:
        data = json.loads(r.text)
    except json.JSONDecodeError:
        sys.exit(f"[오류] 응답이 JSON이 아님(키 문제 가능). status={r.status_code}\n앞부분: {r.text[:300]}")
    resp = data.get("response", {})
    header = resp.get("header", {})
    code = header.get("resultCode")
    if code not in ("00", None):
        sys.exit(f"[오류] API 에러 resultCode={code} {header.get('resultMsg')}")
    return resp.get("body", {}) or {}


def _items(body: dict) -> list[dict]:
    """응답 body에서 종목 리스트 추출. 1건이면 dict로 올 수 있어 리스트로 통일."""
    items = (body.get("items") or {}).get("item") or []
    return [items] if isinstance(items, dict) else items


def find_latest_bas_dt() -> str:
    """최신 거래일(basDt) 찾기 — 오늘부터 뒤로 최대 12일, 데이터가 있는 첫 날."""
    today = datetime.now(timezone.utc).astimezone()  # 로컬(KST) 기준
    for back in range(0, 12):
        d = (today - timedelta(days=back)).strftime("%Y%m%d")
        body = _get({"resultType": "json", "numOfRows": 1, "pageNo": 1, "basDt": d})
        if int(body.get("totalCount", 0) or 0) > 0:
            return d
    sys.exit("[오류] 최근 12일 내 상장종목 데이터를 찾지 못했습니다.")


def fetch_roster(bas_dt: str) -> list[dict]:
    """해당 거래일 전체 종목을 페이지 넘겨가며 수집."""
    rows: list[dict] = []
    page, per = 1, 1000
    while True:
        body = _get({"resultType": "json", "numOfRows": per, "pageNo": page, "basDt": bas_dt})
        total = int(body.get("totalCount", 0) or 0)
        batch = _items(body)
        rows.extend(batch)
        print(f"  page {page}: +{len(batch)}건 (누적 {len(rows)}/{total})")
        if not batch or len(rows) >= total:
            break
        page += 1
    return rows


def _int(v) -> int | None:
    """숫자 문자열 → int. 빈값/비정상은 None(시총·주식수 결측 대비)."""
    s = (v or "").strip()
    return int(s) if s.lstrip("-").isdigit() else None


def to_dim_stock(rows: list[dict]) -> list[dict]:
    """API행 → dim_stock 레코드. srtnCd 앞 'A' 있으면 제거, 6자리 숫자 코드만 남긴다."""
    now_iso = datetime.now(timezone.utc).isoformat()
    out, seen = [], set()
    for it in rows:
        srtn = (it.get("srtnCd") or "").strip()
        code = srtn[1:] if srtn[:1].isalpha() else srtn
        code = code.strip()
        # 주식시세정보는 전부 주식(보통주+우선주) → 6자리 '영숫자' 허용(신형우선주 00088K 등 포함).
        if len(code) != 6 or not code.isalnum() or code in seen:
            continue  # 길이 이상·중복 제외
        seen.add(code)
        out.append({
            "stock_code": code,
            "name": (it.get("itmsNm") or "").strip(),
            "market": (it.get("mrktCtg") or "").strip(),
            "market_cap": _int(it.get("mrktTotAmt")),   # 시가총액(원)
            "shares_out": _int(it.get("lstgStCnt")),    # 상장주식수
            "updated_at": now_iso,
        })
    return out


def upsert_dim_stock(records: list[dict]) -> None:
    """Supabase dim_stock 에 upsert. Secret 키라 RLS를 우회해 쓸 수 있다."""
    url = (os.environ.get("SUPABASE_URL") or "").strip()
    key = (os.environ.get("SUPABASE_SERVICE_KEY") or "").strip()
    if not url or not key:
        sys.exit("[오류] SUPABASE_URL / SUPABASE_SERVICE_KEY 가 .env 에 없습니다.")
    endpoint = f"{url}/rest/v1/dim_stock?on_conflict=stock_code"
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=minimal",
    }
    CHUNK = 500
    for i in range(0, len(records), CHUNK):
        chunk = records[i:i + CHUNK]
        r = httpx.post(endpoint, headers=headers, content=json.dumps(chunk), timeout=60.0)
        if r.status_code not in (200, 201, 204):
            sys.exit(f"[오류] Supabase 쓰기 실패 status={r.status_code}\n{r.text[:400]}")
        print(f"  upsert {i + len(chunk)}/{len(records)}")


def main() -> None:
    print("[1/3] 최신 거래일 찾는 중...")
    bas_dt = find_latest_bas_dt()
    print(f"      최신 거래일: {bas_dt}")

    print("[2/3] 상장종목 로스터 수집 중...")
    rows = fetch_roster(bas_dt)
    records = to_dim_stock(rows)
    print(f"      정상 종목 {len(records)}건 (원본 {len(rows)}행)")

    print("[3/3] dim_stock 에 upsert...")
    upsert_dim_stock(records)
    print(f"완료: dim_stock {len(records)}건 적재 (기준일 {bas_dt}).")


if __name__ == "__main__":
    main()
