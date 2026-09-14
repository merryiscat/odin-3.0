"""
오딘 3.0 data_pipeline — 한국투자증권(KIS) OpenAPI 시세 조회 도우미 (주문 기능 없음, 조회 전용).

- 키: 프로젝트 루트 .env 의 KIS_APP_KEY / KIS_APP_SECRET (3.0 오픈 전까지는 옛 오딘과 같은 실전 앱키를 공유 —
  2026-09-14 사용자 결정: 호출량이 작아 지장 없다고 보고, 문제가 생기면 그때 분리)
- 토큰: 파일에 저장해 만료 전까지 재사용한다. KIS 규칙상 6시간 안에 다시 발급받으면 같은 토큰이 오므로
  옛 오딘이 쓰는 토큰을 끊지 않는다(공식 예제 kis_auth.py 주석).
- 호출 간격: 최소 0.1초(실전 한도 초당 ~20건, 옛 오딘과 나눠 쓰니 보수적으로). 초과 오류(EGW00201)면 1초 쉬고 1회 재시도.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

BASE_URL = (os.environ.get("KIS_BASE_URL") or "https://openapi.koreainvestment.com:9443").rstrip("/")
TOKEN_CACHE = Path(__file__).resolve().parent / "kis_token_cache.json"   # .gitignore(kis_*.json)로 차단됨
MIN_INTERVAL_S = 0.1


class KisError(RuntimeError):
    pass


class Kis:
    def __init__(self):
        self.app_key = (os.environ.get("KIS_APP_KEY") or "").strip()
        self.app_secret = (os.environ.get("KIS_APP_SECRET") or "").strip()
        if not self.app_key or not self.app_secret:
            sys.exit("[오류] KIS_APP_KEY / KIS_APP_SECRET 가 .env 에 없습니다 (옛 오딘과 같은 실전 앱키).")
        self.client = httpx.Client(base_url=BASE_URL, timeout=10.0)
        self._lock = threading.Lock()
        self._last_call = 0.0
        self._token: str | None = None

    # ── 토큰 ────────────────────────────────────────────────────────────
    def token(self) -> str:
        if self._token:
            return self._token
        cached = self._read_cache()
        if cached:
            self._token = cached
            return cached
        r = self.client.post("/oauth2/tokenP", json={
            "grant_type": "client_credentials", "appkey": self.app_key, "appsecret": self.app_secret})
        body = r.json()
        if r.status_code != 200 or "access_token" not in body:
            raise KisError(f"토큰 발급 실패 {r.status_code}: {str(body)[:200]}")
        TOKEN_CACHE.write_text(json.dumps({
            "app_key_tail": self.app_key[-4:], "access_token": body["access_token"],
            "expires": body.get("access_token_token_expired")}), encoding="utf-8")
        self._token = body["access_token"]
        return self._token

    def _read_cache(self) -> str | None:
        try:
            c = json.loads(TOKEN_CACHE.read_text(encoding="utf-8"))
            exp = datetime.strptime(c["expires"], "%Y-%m-%d %H:%M:%S")
            if c.get("app_key_tail") == self.app_key[-4:] and (exp - datetime.now()).total_seconds() > 600:
                return c["access_token"]
        except (OSError, KeyError, ValueError, TypeError):
            pass
        return None

    # ── 조회 ────────────────────────────────────────────────────────────
    def get(self, path: str, tr_id: str, params: dict, retry: bool = True) -> dict:
        with self._lock:
            wait = MIN_INTERVAL_S - (time.monotonic() - self._last_call)
            if wait > 0:
                time.sleep(wait)
            self._last_call = time.monotonic()
        headers = {"content-type": "application/json; charset=utf-8", "authorization": f"Bearer {self.token()}",
                   "appkey": self.app_key, "appsecret": self.app_secret, "tr_id": tr_id, "custtype": "P"}
        r = self.client.get(path, headers=headers, params=params)
        try:
            body = r.json()
        except ValueError:
            raise KisError(f"{tr_id} 응답이 JSON 아님 {r.status_code}: {r.text[:200]}")
        if body.get("rt_cd") != "0":
            if retry and body.get("msg_cd") == "EGW00201":   # 초당 호출 초과
                time.sleep(1.0)
                return self.get(path, tr_id, params, retry=False)
            if retry and body.get("msg_cd") in ("EGW00123", "EGW00121"):   # 토큰 만료·무효 → 새로 발급
                self._token = None
                TOKEN_CACHE.unlink(missing_ok=True)
                return self.get(path, tr_id, params, retry=False)
            raise KisError(f"{tr_id} 실패 {body.get('msg_cd')}: {body.get('msg1')}")
        return body


def num(v) -> float | None:
    """KIS 숫자 문자열 → float. 빈값은 None."""
    try:
        s = str(v).strip()
        return float(s) if s else None
    except (TypeError, ValueError):
        return None
