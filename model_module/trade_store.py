"""
오딘 3.0 model_module — 실행 기록 저장소 (모델 이름을 딴 SQLite 파일 하나).

기본 위치: model_module/records/rule-trade-v1.sqlite (.gitignore가 *.sqlite를 막아 깃에 안 올라간다).
표:
  run         실행 회차 — 언제 돌았나, 어느 거래일, 결과(ok/holiday/skipped_duplicate/error), 오류 문구
  run_input   그 회차가 본 입력 스냅샷(리스트·시장 상태·시세·계좌·거래일 달력·미체결 의도) — JSON 원문 그대로
  rule_eval   규칙 하나·종목 하나마다 한 줄(걸렸나 + 근거). 안 걸린 이유도 여기 있다
  intent      주문 의도 + 가상 체결 결과(status: pending → filled / unfilled_*)
  account     회차 끝의 계좌(현금·보유). 다음 회차가 여기서 이어간다
  event       사람용 메모(데이터 결손·경고)
규칙: 회차 기록은 지우지 않는다(append). 같은 거래일에 두 번 돌면 두 번째는 skipped_duplicate로 남긴다.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))
DEFAULT_DB = Path(__file__).resolve().parent / "records" / "rule-trade-v1.sqlite"

SCHEMA = """
create table if not exists run (
    run_id      integer primary key autoincrement,
    model_id    text not null,
    rule_ver    text not null,
    trade_date  text not null,          -- 이 회차가 판단한 거래일(YYYY-MM-DD)
    started_at  text not null,          -- 실행 시작(한국시간 ISO)
    finished_at text,
    status      text not null,          -- running / ok / holiday / skipped_duplicate / error / dry_run
    mode        text not null,          -- live(예약 실행) / manual(--date로 손 실행) / dry
    prev_td     text,                   -- 입력으로 쓴 직전 거래일
    error       text,
    summary     text                    -- JSON: 의도 수·체결 수·문 상태 한 줌
);
create table if not exists run_input (
    run_id     integer not null references run(run_id),
    kind       text not null,           -- list / state_rt / state_w / bars / calendar / account / pending_intents / names
    as_of      text,
    row_count  integer,
    payload    text not null,           -- JSON 원문
    primary key (run_id, kind)
);
create table if not exists rule_eval (
    id         integer primary key autoincrement,
    run_id     integer not null references run(run_id),
    stage      text not null,           -- settle / gate / exit / candidate
    market     text,
    stock_code text,
    rule_id    text not null,
    fired      integer not null,        -- 1 = 규칙이 걸렸다(통과/발동), 0 = 안 걸렸다
    detail     text                     -- JSON 근거
);
create table if not exists intent (
    intent_id  integer primary key autoincrement,
    run_id     integer not null references run(run_id),
    trade_date text not null,           -- 체결을 가정하는 날(= 의도를 낸 회차의 거래일, 그날 시가)
    market     text not null,
    stock_code text not null,
    side       text not null,           -- buy / sell
    qty        integer not null,
    ref_price  real,                    -- 수량 산정에 쓴 직전 종가
    rule_id    text not null,
    reason     text,                    -- JSON 근거
    status     text not null,           -- pending / filled / unfilled_no_data / unfilled_no_trade / unfilled_no_cash / unfilled_not_held
    fill_run_id integer,
    fill_price real,
    fill_qty   integer,
    fill_note  text
);
create table if not exists account (
    run_id     integer primary key references run(run_id),
    cash       real not null,
    holdings   text not null            -- JSON 배열
);
create table if not exists event (
    id       integer primary key autoincrement,
    run_id   integer,
    ts       text not null,
    level    text not null,             -- info / warn / error
    msg      text not null
);
"""


def now_kst() -> str:
    return datetime.now(KST).isoformat(timespec="seconds")


def _j(v) -> str:
    return json.dumps(v, ensure_ascii=False, default=str)


class Store:
    def __init__(self, path: Path | str = DEFAULT_DB):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.con = sqlite3.connect(self.path)
        self.con.row_factory = sqlite3.Row
        self.con.executescript(SCHEMA)

    # ── 회차 ────────────────────────────────────────────────────────────
    def start_run(self, model_id: str, rule_ver: str, trade_date: str, mode: str) -> int:
        cur = self.con.execute("insert into run(model_id,rule_ver,trade_date,started_at,status,mode) values(?,?,?,?,?,?)",
                               (model_id, rule_ver, trade_date, now_kst(), "running", mode))
        self.con.commit()
        return cur.lastrowid

    def finish_run(self, run_id: int, status: str, prev_td: str | None = None, error: str | None = None, summary: dict | None = None) -> None:
        self.con.execute("update run set status=?, finished_at=?, prev_td=?, error=?, summary=? where run_id=?",
                         (status, now_kst(), prev_td, error, _j(summary) if summary is not None else None, run_id))
        self.con.commit()

    def has_ok_run(self, trade_date: str) -> bool:
        r = self.con.execute("select 1 from run where trade_date=? and status='ok' limit 1", (trade_date,)).fetchone()
        return r is not None

    def event(self, run_id: int | None, level: str, msg: str) -> None:
        self.con.execute("insert into event(run_id,ts,level,msg) values(?,?,?,?)", (run_id, now_kst(), level, msg))
        self.con.commit()

    # ── 입력·근거·의도 ─────────────────────────────────────────────────
    def save_inputs(self, run_id: int, inputs: dict, as_of: dict | None = None) -> None:
        as_of = as_of or {}
        for kind, payload in inputs.items():
            n = len(payload) if isinstance(payload, (list, dict)) else None
            self.con.execute("insert or replace into run_input(run_id,kind,as_of,row_count,payload) values(?,?,?,?,?)",
                             (run_id, kind, as_of.get(kind), n, _j(payload)))
        self.con.commit()

    def save_evals(self, run_id: int, evals: list[dict]) -> None:
        self.con.executemany("insert into rule_eval(run_id,stage,market,stock_code,rule_id,fired,detail) values(?,?,?,?,?,?,?)",
                             [(run_id, e["stage"], e.get("market"), e.get("stock_code"), e["rule_id"], 1 if e["fired"] else 0, _j(e.get("detail")))
                              for e in evals])
        self.con.commit()

    def save_intents(self, run_id: int, intents: list[dict]) -> list[int]:
        ids = []
        for it in intents:
            cur = self.con.execute(
                "insert into intent(run_id,trade_date,market,stock_code,side,qty,ref_price,rule_id,reason,status) values(?,?,?,?,?,?,?,?,?,'pending')",
                (run_id, it["trade_date"], it["market"], it["stock_code"], it["side"], int(it["qty"]), it.get("ref_price"), it["rule_id"], _j(it.get("reason"))))
            ids.append(cur.lastrowid)
        self.con.commit()
        return ids

    def apply_settlements(self, run_id: int, settlements: list[dict]) -> None:
        for s in settlements:
            self.con.execute("update intent set status=?, fill_run_id=?, fill_price=?, fill_qty=?, fill_note=? where intent_id=?",
                             (s["status"], run_id, s.get("fill_price"), s.get("fill_qty"), s.get("note"), s["intent_id"]))
        self.con.commit()

    def save_account(self, run_id: int, account: dict) -> None:
        self.con.execute("insert or replace into account(run_id,cash,holdings) values(?,?,?)",
                         (run_id, float(account["cash"]), _j(account.get("holdings", []))))
        self.con.commit()

    # ── 다음 회차가 이어받는 상태 ───────────────────────────────────────
    def latest_account(self, initial_cash: float) -> tuple[dict, int | None]:
        r = self.con.execute("select a.run_id, a.cash, a.holdings from account a join run r on r.run_id=a.run_id "
                             "where r.status='ok' order by r.trade_date desc, a.run_id desc limit 1").fetchone()
        if r is None:
            return {"cash": float(initial_cash), "holdings": []}, None
        return {"cash": float(r["cash"]), "holdings": json.loads(r["holdings"])}, r["run_id"]

    def pending_intents(self) -> list[dict]:
        rows = self.con.execute("select intent_id,run_id,trade_date,market,stock_code,side,qty,ref_price,rule_id from intent "
                                "where status='pending' order by trade_date, intent_id").fetchall()
        return [dict(r) for r in rows]

    # ── 검토용 읽기 ────────────────────────────────────────────────────
    def runs_between(self, d_from: str, d_to: str) -> list[dict]:
        return [dict(r) for r in self.con.execute("select * from run where trade_date between ? and ? order by trade_date, run_id", (d_from, d_to))]

    def inputs_of(self, run_id: int) -> dict:
        return {r["kind"]: json.loads(r["payload"]) for r in self.con.execute("select kind,payload from run_input where run_id=?", (run_id,))}

    def intents_of_run(self, run_id: int) -> list[dict]:
        return [dict(r) for r in self.con.execute("select * from intent where run_id=? order by intent_id", (run_id,))]

    def settled_by_run(self, run_id: int) -> list[dict]:
        return [dict(r) for r in self.con.execute("select * from intent where fill_run_id=? order by intent_id", (run_id,))]

    def evals_of_run(self, run_id: int) -> list[dict]:
        return [dict(r) for r in self.con.execute("select * from rule_eval where run_id=? order by id", (run_id,))]

    def account_of_run(self, run_id: int) -> dict | None:
        r = self.con.execute("select cash,holdings from account where run_id=?", (run_id,)).fetchone()
        return {"cash": r["cash"], "holdings": json.loads(r["holdings"])} if r else None

    def events_of_run(self, run_id: int) -> list[dict]:
        return [dict(r) for r in self.con.execute("select * from event where run_id=? order by id", (run_id,))]
