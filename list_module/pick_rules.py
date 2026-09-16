"""
오딘 3.0 list_module — 매매 리스트 1호 모델 `rule-pick-v1` 규칙 (순수 계산, DB 모름).

입력: 한 시장·한 기준일의 종목 파라미터 줄들(stock_params.params + dim_stock의 type·status·sector_l1·market_cap).
출력: "다음 거래일에 볼 종목" 목록 — 종목마다 reasons(왜 뽑혔나 태그)·score·warn·metrics.

방법 = **렌즈 합집합**: 렌즈마다 시장별 상위 K를 뽑고, 여러 렌즈에 걸린 종목일수록 점수가 높다. 리스트는 좁히지
않고(상한은 N), 최종 선택은 매매모델이 태그로 한다(스타일 중립). 시장 온도는 조건으로 쓰지 않는다.
유니버스는 빼기 방식(보통주만·거래정지/관리 제외·계좌 매수 불가 제외), 유동성 하한은 없고 렌즈마다 잡음 컷만 둔다.
기준선 3개(무작위·시총 상위·어제 그대로)도 같은 모양으로 낸다.
"""

from __future__ import annotations

import random

MODEL_ID = "rule-pick-v1"
RULE_VER = "pick-rules-v1"

LIST_MAX = 40             # 시장별 아침 확정 리스트 상한(사용자 결정 30~50)
LENS_K = 10               # 렌즈마다 상위 K
UNIVERSE_TYPES = {"보통주"}
UNIVERSE_EXCL_STATUS = {"거래정지", "관리"}
# 렌즈별 잡음 컷(당일 거래대금 하한, 원) — 유니버스 하한이 아니라 렌즈의 것. metrics.lens_cfg로 남긴다
LENS_MIN_MONEY = {"money_surge": 1_000_000_000, "top_gainer": 500_000_000, "top_loser": 500_000_000,
                  "limit_up": 100_000_000, "new_high": 1_000_000_000, "foreign_buy": 0, "inst_buy": 0,
                  "both_buy": 0, "sector_counter": 500_000_000}
SECTOR_DOWN_SHARE = 0.7   # 업종 역행 렌즈: 대장주 중 하락 비율이 이 이상(시장 온도 theme_up과 같은 문턱)
SECTOR_UP_PCT = 1.0       #   … 그 속에서 대장주가 +1% 이상 오른 업종
SECTOR_PER = 5            #   … 그 업종에서 대금 상위 몇 종목
BLOWOFF = {"chg1_pct": 15.0, "ma20_gap_pct": 20.0, "vol_ratio": 5.0}   # 꼭지 추격 경고(한미사이언스 교훈)
LENS_KR = {"money_surge": "대금 급증", "top_gainer": "상승 상위", "top_loser": "하락 상위", "limit_up": "상한가",
           "new_high": "신고가", "foreign_buy": "외인 순매수", "inst_buy": "기관 순매수", "both_buy": "외인·기관 쌍끌이",
           "sector_counter": "업종 역행", "intraday_add": "장중 추가"}


def in_universe(row: dict) -> bool:
    p = row["params"]
    return (row.get("type") in UNIVERSE_TYPES and row.get("status") not in UNIVERSE_EXCL_STATUS
            and bool(p.get("tradable")) and p.get("money") is not None)


def _top(rows: list[dict], key, min_money: int, k: int = LENS_K, reverse: bool = True) -> list[dict]:
    cand = [r for r in rows if key(r) is not None and (r["params"].get("money") or 0) >= min_money]
    cand.sort(key=lambda r: (key(r), r["params"].get("money") or 0), reverse=reverse)
    return cand[:k]


def lenses(rows: list[dict]) -> dict[str, list[dict]]:
    """렌즈 이름 → 그 렌즈에 걸린 종목 줄(순위 순). rows는 유니버스 안 종목만."""
    P = lambda r, k: r["params"].get(k)   # noqa: E731
    out = {
        "money_surge": _top(rows, lambda r: P(r, "money_ratio"), LENS_MIN_MONEY["money_surge"]),
        "top_gainer": _top(rows, lambda r: P(r, "chg1_pct"), LENS_MIN_MONEY["top_gainer"]),
        "top_loser": _top(rows, lambda r: (-P(r, "chg1_pct")) if P(r, "chg1_pct") is not None else None, LENS_MIN_MONEY["top_loser"]),
        "limit_up": [r for r in rows if P(r, "limit_up") and (P(r, "money") or 0) >= LENS_MIN_MONEY["limit_up"]],
        "new_high": _top([r for r in rows if (P(r, "high20_gap_pct") or -1) >= 0], lambda r: P(r, "money"), LENS_MIN_MONEY["new_high"]),
    }
    # 수급 렌즈는 순위 축적이 있는 날만(없으면 키 자체가 None)
    if any(P(r, "foreign_net5") is not None for r in rows):
        out["foreign_buy"] = _top([r for r in rows if (P(r, "foreign_net5") or 0) > 0], lambda r: P(r, "foreign_net5"), 0)
        out["inst_buy"] = _top([r for r in rows if (P(r, "inst_net5") or 0) > 0], lambda r: P(r, "inst_net5"), 0)
        out["both_buy"] = _top([r for r in rows if (P(r, "foreign_net5") or 0) > 0 and (P(r, "inst_net5") or 0) > 0],
                               lambda r: min(P(r, "foreign_net5"), P(r, "inst_net5")), 0)
    out["sector_counter"] = sector_counter(rows)
    return {k: v for k, v in out.items() if v}


def sector_counter(rows: list[dict]) -> list[dict]:
    """업종 역행: 업종 대장주(시총 1위)의 당일 등락으로 '대장주 70%+ 하락 속 +1% 오른 업종'을 찾고,
    그 업종 종목을 대금 순으로 SECTOR_PER개씩. 시장 온도 theme_up과 같은 정의를 종목 파라미터로 재계산(온도 모델과 무관)."""
    leaders: dict[str, dict] = {}
    for r in rows:
        sec, cap = r.get("sector_l1"), r.get("market_cap") or 0
        if sec and (sec not in leaders or cap > (leaders[sec].get("market_cap") or 0)):
            leaders[sec] = r
    chg = {sec: r["params"].get("chg1_pct") for sec, r in leaders.items() if r["params"].get("chg1_pct") is not None}
    if len(chg) < 15:
        return []
    down_share = sum(1 for v in chg.values() if v < 0) / len(chg)
    if down_share < SECTOR_DOWN_SHARE:
        return []
    up_secs = sorted((s for s, v in chg.items() if v >= SECTOR_UP_PCT), key=lambda s: -chg[s])
    out = []
    for s in up_secs:
        members = [r for r in rows if r.get("sector_l1") == s]
        out.extend(_top(members, lambda r: r["params"].get("money"), LENS_MIN_MONEY["sector_counter"], k=SECTOR_PER))
    return out


def warns(p: dict) -> list[str]:
    w = []
    if any((p.get(k) or 0) >= v for k, v in BLOWOFF.items()):
        w.append("blowoff")
    if p.get("thin"):
        w.append("thin")
    if not p.get("tradable"):
        w.append("untradable")
    return w


def pick(rows: list[dict], list_max: int = LIST_MAX) -> list[dict]:
    """rule-pick-v1 아침 리스트. rows = 한 시장 전 종목 파라미터 줄. 반환: 순위 순 [{stock_code, rank_no, score, reasons, warn, metrics, label_kr}]"""
    uni = [r for r in rows if in_universe(r)]
    hits: dict[str, dict] = {}
    for lens, lst in lenses(uni).items():
        for rank, r in enumerate(lst, 1):
            h = hits.setdefault(r["stock_code"], {"row": r, "lens": {}})
            h["lens"][lens] = rank
    out = []
    for code, h in hits.items():
        p = h["row"]["params"]
        k = LENS_K
        score = sum(1.0 + max(0, k - rank + 1) / k for rank in h["lens"].values())   # 렌즈 1개 1위 = 2.0
        reasons = sorted(h["lens"], key=lambda l: h["lens"][l])
        out.append({
            "stock_code": code, "score": round(score, 3), "reasons": reasons, "warn": warns(p),
            "label_kr": "·".join(LENS_KR.get(l, l) for l in reasons),
            "metrics": {"chg1_pct": p.get("chg1_pct"), "money": p.get("money"), "money_ratio": p.get("money_ratio"),
                        "high20_gap_pct": p.get("high20_gap_pct"), "limit_up_streak": p.get("limit_up_streak"),
                        "foreign_net5": p.get("foreign_net5"), "inst_net5": p.get("inst_net5"),
                        "sector_l1": h["row"].get("sector_l1"), "lens_rank": h["lens"],
                        "lens_cfg": {"k": LENS_K, "list_max": list_max, "min_money": LENS_MIN_MONEY}},
        })
    out.sort(key=lambda x: (x["score"], x["metrics"]["money"] or 0), reverse=True)
    out = out[:list_max]
    for i, x in enumerate(out, 1):
        x["rank_no"] = i
    return out


# ── 기준선 모델들 (같은 유니버스, 같은 출력 모양) ─────────────────────────
def baseline_random(rows: list[dict], seed: str, n: int = LIST_MAX) -> list[dict]:
    uni = sorted((r for r in rows if in_universe(r)), key=lambda r: r["stock_code"])
    rng = random.Random(seed)                        # 날짜+시장으로 씨앗 고정 → 재현 가능
    chosen = rng.sample(uni, min(n, len(uni)))
    return [{"stock_code": r["stock_code"], "rank_no": i, "score": None, "reasons": ["random"], "warn": warns(r["params"]),
             "label_kr": "무작위", "metrics": {"money": r["params"].get("money")}} for i, r in enumerate(chosen, 1)]


def baseline_topcap(rows: list[dict], n: int = LIST_MAX) -> list[dict]:
    uni = sorted((r for r in rows if in_universe(r)), key=lambda r: -(r.get("market_cap") or 0))
    return [{"stock_code": r["stock_code"], "rank_no": i, "score": None, "reasons": ["topcap"], "warn": warns(r["params"]),
             "label_kr": "시총 상위", "metrics": {"market_cap": r.get("market_cap"), "money": r["params"].get("money")}}
            for i, r in enumerate(uni[:n], 1)]


def baseline_carry(prev_picks: list[dict]) -> list[dict]:
    """어제의 rule-pick-v1 리스트를 그대로(사유는 carry로 바꿔 표기)."""
    return [{"stock_code": x["stock_code"], "rank_no": x["rank_no"], "score": x.get("score"), "reasons": ["carry"],
             "warn": x.get("warn") or [], "label_kr": "어제 리스트", "metrics": {"from": x.get("reasons")}} for x in prev_picks]
