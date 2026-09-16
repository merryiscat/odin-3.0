"""
오딘 3.0 data_pipeline — 한투 종목마스터 파일로 dim_stock 상태 칸 갱신 (API 호출 0건).

한투가 매일 공개하는 종목마스터(kospi_code.mst / kosdaq_code.mst, 고정폭 텍스트)에 종목마다
증권 그룹(주권/ETF/ETN/리츠…)·우선주 구분·거래정지·관리종목·시장경고·코스피200/코스닥150 편입·상장일이 있다.
이걸로 dim_stock의 type / status / is_kospi200 / is_kosdaq150 / listing_date 를 매일 아침 갱신한다.
(옛 오딘 교훈: 거래정지·관리 종목이 유니버스에 남으면 폭 계산이 왜곡되고, 계좌가 못 사는 자산이 끝까지 통과한다.)

레이아웃은 KIS 공식 예제(kis_kospi_code_mst.py / kis_kosdaq_code_mst.py)의 고정폭 표. 2026-09-17 검증:
삼성전자 ST·우선주 0·코스피200 섹터 5 / 삼성전자우 우선주 1 / 코스피200 편입 201종목 / 거래정지 25·관리 41.
규칙: dim_stock에 **이미 있는 종목만** 갱신(ETF·ETN 등 마스터의 다른 상품은 넣지 않음 — 원하면 나중에 별도).
      마스터에 없는 종목은 상폐 후보로 **개수만 출력**(자동으로 delisted_date를 찍지 않는다 — 사람이 확인).

실행 (프로젝트 루트에서):
  uv run --project data_pipeline python data_pipeline/refresh_dim_stock_master.py [--dry-run]
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
import urllib.request
import zipfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

# (마스터 URL, 고정폭 블록 스펙, 칼럼 이름) — 블록 앞 1바이트는 공백이라 스펙 합 + 1 이 실제 블록 길이
MASTERS = {
    "KOSPI": ("https://new.real.download.dws.co.kr/common/master/kospi_code.mst.zip",
              [2,1,4,4,4, 1,1,1,1,1, 1,1,1,1,1, 1,1,1,1,1, 1,1,1,1,1, 1,1,1,1,1, 1,9,5,5,1, 1,1,2,1,1, 1,2,2,2,3, 1,3,12,12,8, 15,21,2,7,1, 1,1,1,1,9, 9,9,5,9,8, 9,3,1,1,1],
              ['그룹코드','시총규모','업종대','업종중','업종소','제조업','저유동성','지배구조','KOSPI200섹터','KOSPI100','KOSPI50','KRX','ETP','ELW발행','KRX100','KRX자동차','KRX반도체','KRX바이오','KRX은행','SPAC','KRX에너지화학','KRX철강','단기과열','KRX미디어통신','KRX건설','Non1','KRX증권','KRX선박','KRX보험','KRX운송','SRI','기준가','매매수량단위','시간외수량단위','거래정지','정리매매','관리종목','시장경고','경고예고','불성실공시','우회상장','락구분','액면변경','증자구분','증거금비율','신용가능','신용기간','전일거래량','액면가','상장일자','상장주수','자본금','결산월','공모가','우선주','공매도과열','이상급등','KRX300','KOSPI','매출액','영업이익','경상이익','당기순이익','ROE','기준년월','시가총액','그룹사코드','회사신용한도초과','담보대출가능','대주가능']),
    "KOSDAQ": ("https://new.real.download.dws.co.kr/common/master/kosdaq_code.mst.zip",
               [2,1,4,4,4, 1,1,1,1,1, 1,1,1,1,1, 1,1,1,1,1, 1,1,1,1,1, 1,9,5,5,1, 1,1,2,1,1, 1,2,2,2,3, 1,3,12,12,8, 15,21,2,7,1, 1,1,1,9,9, 9,5,9,8,9, 3,1,1,1],
               ['그룹코드','시총규모','업종대','업종중','업종소','벤처','저유동성','KRX','ETP','KRX100','KRX자동차','KRX반도체','KRX바이오','KRX은행','SPAC','KRX에너지화학','KRX철강','단기과열','KRX미디어통신','KRX건설','투자주의환기','KRX증권','KRX선박','KRX보험','KRX운송','KOSDAQ150','기준가','매매수량단위','시간외수량단위','거래정지','정리매매','관리종목','시장경고','경고예고','불성실공시','우회상장','락구분','액면변경','증자구분','증거금비율','신용가능','신용기간','전일거래량','액면가','상장일자','상장주수','자본금','결산월','공모가','우선주','공매도과열','이상급등','KRX300','매출액','영업이익','경상이익','당기순이익','ROE','기준년월','시가총액','그룹사코드','회사신용한도초과','담보대출가능','대주가능']),
}
GROUP_TYPE = {"EF": "ETF", "FE": "ETF", "EN": "ETN", "RT": "리츠", "MF": "펀드", "BC": "펀드", "PF": "펀드", "SC": "펀드",
              "IF": "펀드", "DR": "DR", "FS": "외국주권", "EW": "ELW", "SW": "신주인수권", "SR": "신주인수권"}
WARN_STATUS = {"01": "투자주의", "02": "투자경고", "03": "투자위험"}


def parse_master(market: str) -> dict[str, dict]:
    url, spec, cols = MASTERS[market]
    tail = sum(spec) + 1
    raw = urllib.request.urlopen(url, timeout=30).read()
    zf = zipfile.ZipFile(io.BytesIO(raw))
    data = zf.read(zf.namelist()[0])
    out = {}
    for line in data.splitlines():
        if len(line) <= tail + 21:
            continue
        head, blk = line[:-tail], line[-tail + 1:]
        code = head[0:9].decode("cp949", "ignore").strip()
        rec = {"name": head[21:].decode("cp949", "ignore").strip(), "market": market}
        pos = 0
        for w, c in zip(spec, cols):
            rec[c] = blk[pos:pos + w].decode("cp949", "ignore")
            pos += w
        if code:
            out[code] = rec
    return out


def classify(rec: dict) -> dict:
    """마스터 한 줄 → dim_stock 갱신 값."""
    g = rec["그룹코드"]
    if g == "ST":
        typ = "스팩" if "스팩" in rec["name"] else ("우선주" if rec["우선주"].strip() not in ("", "0") else "보통주")
    else:
        typ = GROUP_TYPE.get(g, g.strip() or "기타")
    if rec["거래정지"] == "Y":
        status = "거래정지"
    elif rec["관리종목"] == "Y":
        status = "관리"
    else:
        status = WARN_STATUS.get(rec["시장경고"], "정상")
    ld = rec["상장일자"].strip()
    listing = f"{ld[:4]}-{ld[4:6]}-{ld[6:]}" if len(ld) == 8 and ld.isdigit() else None
    return {"type": typ, "status": status,
            "is_kospi200": rec["market"] == "KOSPI" and rec["KOSPI200섹터"].strip() not in ("", "0"),
            "is_kosdaq150": rec["market"] == "KOSDAQ" and rec["KOSDAQ150"] == "Y",
            "listing_date": listing}


def main() -> None:
    ap = argparse.ArgumentParser(description="한투 종목마스터 → dim_stock 상태 칸 갱신")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    url = (os.environ.get("SUPABASE_URL") or "").strip().rstrip("/")
    key = (os.environ.get("SUPABASE_SERVICE_KEY") or "").strip()
    if not key and args.dry_run:
        key = (os.environ.get("SUPABASE_ANON_KEY") or "").strip()
    if not url or not key:
        sys.exit("[오류] SUPABASE_URL / SUPABASE_SERVICE_KEY 가 .env 에 없습니다.")
    h = {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    master = {}
    for mk in MASTERS:
        m = parse_master(mk)
        master.update(m)
        print(f"[마스터] {mk} {len(m)}종목 · 그룹 {Counter(r['그룹코드'] for r in m.values()).most_common(4)}")

    # dim_stock 현재 값 (변경된 것만 쓰기 위해)
    rows, offset = [], 0
    while True:
        r = httpx.get(f"{url}/rest/v1/dim_stock", headers=h, timeout=30.0, params={
            "select": "stock_code,name,market,type,status,is_kospi200,is_kosdaq150,listing_date,delisted_date",
            "order": "stock_code", "limit": 1000, "offset": offset})
        r.raise_for_status()
        page = r.json()
        rows.extend(page)
        if len(page) < 1000:
            break
        offset += 1000

    now_iso = datetime.now(timezone.utc).isoformat()
    updates, missing, changes = [], [], Counter()
    for s in rows:
        rec = master.get(s["stock_code"])
        if rec is None:
            if not s.get("delisted_date"):
                missing.append((s["stock_code"], s["name"]))
            continue
        new = classify(rec)
        diff = {k: v for k, v in new.items() if s.get(k) != v}
        if diff:
            for k in diff:
                changes[f"{k}:{s.get(k)}→{diff[k]}" if k in ("type", "status") else k] += 1
            # 바뀐 종목은 5칸 전부 + name·market(not null 칸은 upsert 때 있어야 함)을 보낸다
            # — PostgREST 묶음 upsert는 모든 줄의 칸이 같아야 하고(PGRST102), not null 검사가 충돌 처리보다 먼저 돈다
            updates.append({"stock_code": s["stock_code"], "name": s["name"], "market": s["market"], **new, "updated_at": now_iso})

    print(f"[대조] dim_stock {len(rows)}종목 · 마스터에 없음(상폐 후보, 자동 처리 안 함) {len(missing)}: {missing[:8]}")
    print(f"[변경] {len(updates)}종목 · " + ", ".join(f"{k} {v}" for k, v in changes.most_common(12)))
    if args.dry_run:
        return
    for i in range(0, len(updates), 200):
        chunk = updates[i:i + 200]
        # PostgREST는 행별 patch가 없어 upsert(merge-duplicates)로 — stock_code가 PK라 있는 줄만 바뀐다
        r = httpx.post(f"{url}/rest/v1/dim_stock?on_conflict=stock_code", headers=dict(h, Prefer="resolution=merge-duplicates,return=minimal"),
                       content=json.dumps(chunk, ensure_ascii=False), timeout=60.0)
        if r.status_code not in (200, 201, 204):
            sys.exit(f"[오류] dim_stock 갱신 실패 {r.status_code}: {r.text[:300]}")
    print(f"[끝] dim_stock {len(updates)}종목 갱신")


if __name__ == "__main__":
    main()
