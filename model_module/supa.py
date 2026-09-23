"""
오딘 3.0 model_module — Supabase REST 얇은 도우미(읽기만 쓴다 — 매매모델은 Supabase에 쓰지 않는다).

모듈끼리 import 금지 규칙 때문에 list_module·state_module의 것을 가져다 쓰지 않고 여기 따로 둔다(내용은 같다).
- 쓰기: SUPABASE_URL + SUPABASE_SERVICE_KEY (프로젝트 루트 .env)
- 읽기만 할 때(--dry-run·검증): SUPABASE_SERVICE_KEY가 없으면 SUPABASE_ANON_KEY(공개 읽기 키)로 읽는다.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

PAGE = 1000   # Supabase 기본 최대 행 수 — 이보다 많으면 끊어서 읽는다


class Supa:
    def __init__(self, write: bool):
        self.url = (os.environ.get("SUPABASE_URL") or "").strip().rstrip("/")
        key = (os.environ.get("SUPABASE_SERVICE_KEY") or "").strip()
        if not key and not write:
            key = (os.environ.get("SUPABASE_ANON_KEY") or "").strip()
        if not self.url or not key:
            need = "SUPABASE_SERVICE_KEY" if write else "SUPABASE_SERVICE_KEY 또는 SUPABASE_ANON_KEY"
            sys.exit(f"[오류] .env에 SUPABASE_URL / {need} 가 없습니다. scripts\\setup-keys.cmd 참고.")
        self.headers = {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        self.client = httpx.Client(timeout=60.0)

    def select(self, table: str, params: dict) -> list[dict]:
        """PostgREST 조회. 1,000행 넘으면 offset으로 끝까지 읽는다(order를 꼭 줄 것)."""
        out, offset = [], 0
        while True:
            q = dict(params, limit=PAGE, offset=offset)
            r = self.client.get(f"{self.url}/rest/v1/{table}", headers=self.headers, params=q)
            if r.status_code != 200:
                raise RuntimeError(f"{table} 조회 실패 {r.status_code}: {r.text[:300]}")
            rows = r.json()
            out.extend(rows)
            if len(rows) < PAGE:
                return out
            offset += PAGE

    def insert(self, table: str, rows: list[dict], chunk: int = 500) -> None:
        """append 전용 표에 추가(덮어쓰기 없음). 큰 묶음은 500줄씩."""
        h = dict(self.headers, Prefer="return=minimal")
        for i in range(0, len(rows), chunk):
            r = self.client.post(f"{self.url}/rest/v1/{table}", headers=h,
                                 content=json.dumps(rows[i:i + chunk], ensure_ascii=False))
            if r.status_code not in (200, 201, 204):
                raise RuntimeError(f"{table} 쓰기 실패 {r.status_code}: {r.text[:300]}")
