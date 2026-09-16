# 매매 리스트 — 입출력 규격 v1

> 마켓에 올릴 **매매 리스트(그날 볼 종목) 모델**이 지켜야 하는 계약. 2026-09-17 기준, **실제로 돌고 있는 표·값**으로 작성.
> 규칙 숫자(어떻게 고르는지)는 `list_module/README.md`, 표 정본은 `db/*.sql`. 시장 상태 규격은 `state_module/SPEC_market_state_v1.md`.

## 한 줄 계약
```
[읽는다]  공용 종목 파라미터(stock_params) (+ 원한다면 원천: 전 종목 일봉·순위 축적·종목 마스터)
[낸다]   pick_list 에 종목마다 한 줄 — 왜 뽑혔나(reasons 태그) + 점수 + 경고 + 근거(metrics)
```
- 모델은 **DB 표로만** 주고받는다. 외부 API·증권 키·주문에 접근하지 않는다.
- 같은 날 여러 모델이 각자 리스트를 낼 수 있다(`model_id`로 구분). 서로의 줄을 고치지 않는다.
- **append만.** 이미 쓴 줄은 고치거나 지우지 않는다.
- **시장 온도를 조건으로 종목을 고르지 않는다.** 온도로 리스트를 거르는 건 매매모델의 몫이다.

---

## 1. 입력 (읽기 전용)

### 1-1. `stock_params` — 공용 종목 파라미터 (모든 리스트 모델이 같은 입력을 읽는다)
밤 배치가 그날 확정 일봉으로 종목마다 한 줄(`scope=bg_daily`). 실제 줄: 2026-09-16 빛샘전자(072950).

| 키 | 뜻 | 실제 값 |
|---|---|---|
| `prev_close` | 전일 종가(원) | 10700 |
| `chg1_pct` | 당일 등락률 % | 30.0 |
| `chg5_pct` / `chg20_pct` | 5·20거래일 누적 등락률 % | |
| `money` | 당일 거래대금(원) | 195459058900 |
| `money_avg20` | 직전 20거래일 평균 대금(원, 당일 제외) | |
| `money_ratio` | 당일 ÷ 평균 | 26.5045 |
| `volume` / `vol_ratio` | 거래량 / 직전 20일 평균 대비 | |
| `high20_gap_pct` | 직전 20일 고가 대비 종가 %(0 이상 = 신고가 돌파) | 20.5373 |
| `high252_gap_pct` | 52주 고가 대비(52주 이력이 없으면 null) | |
| `low20_gap_pct` / `ma20_gap_pct` | 20일 저가 대비 / 20일 이평 이격 | |
| `range_pct` | 당일 진폭(고−저)÷전일 종가 % | |
| `gap_open_pct` | 시가 갭 % | |
| `limit_up` / `limit_up_streak` | 상한가 마감 여부(상한가 값 기준) / 연속 일수 | true / 1 |
| `foreign_net5` / `inst_net5` | 순위 축적으로 본 최근 5일 외인·기관 순매수 합(원). 순위 밖 0, **축적 없으면 null** | null |
| `foreign_streak` / `inst_streak` | 순매수 순위 연속 일수 | null |
| `thin` | 20일 평균 대금 < 50억 | |
| `tradable` | 종류·상태로 계좌 매수 가능(DR·외국주권·ELW·신주인수권·펀드·거래정지 제외) | true |
| `type` / `status` | 종목 마스터 값(보통주… / 정상·관리·거래정지·투자주의…) | 보통주 / 정상 |
- 공통 칸: `ts` · `as_of_date`(계산에 쓴 마지막 거래일) · `market` · `stock_code` · `scope` · `calc_ver`(현재 `params-v1`).
- 없는 값은 null이다(추정해 채우지 않는다). 모델도 null이면 그 조건을 건너뛴다.

### 1-2. 원천(선택) — 직접 계산하고 싶은 모델용
`raw_stock_ohlc`(전 종목 일봉, trade_value 원) · `raw_rank_snapshot`(KIS 순위 상위 30 축적, 10분+15:35) · `dim_stock`(종목 마스터).

---

## 2. 출력 — `pick_list` 한 종목 한 줄

| 칸 | 필수 | 뜻 · 허용값 |
|---|---|---|
| `ts` | ✔ | 쓴 시각 |
| `as_of` | ✔ | 판정에 쓴 **데이터의 시각**(밤 리스트 = 기준일 15:30). 내일 날짜를 찍지 않는다 |
| `list_date` | ✔ | 이 줄을 "볼 날"(거래일) |
| `market` | ✔ | `KR-KOSPI` / `KR-KOSDAQ` |
| `model_id` | ✔ | `dim_model`(kind=`pick`)에 등록된 값 |
| `phase` | ✔ | `open`(아침 확정) / `intraday`(장중 추가) |
| `basis` | ✔ | `eod`(확정 일봉) / `live`(장중 순위) |
| `stock_code` | ✔ | 종목코드 |
| `rank_no` | | 아침 확정 순위(장중 추가는 아침 뒤 번호) |
| `score` | | 점수(모델이 정의, 근거는 metrics에) |
| `reasons` | ✔ | **왜 뽑혔나 태그 배열**(아래 어휘). 장중 줄은 아침 사유를 포함해 다시 쓴다 |
| `warn` | | 경고 태그 배열: `blowoff` / `thin` / `untradable` |
| `label_kr` | | 사람용 한 줄. 기계는 매칭 금지 |
| `metrics` | | 근거 숫자(쓴 파라미터 값·렌즈 순위 `lens_rank`·렌즈 설정 `lens_cfg`) |
| `rule_ver` | ✔ | 규칙 버전 |

**실제 줄 (2026-09-17 아침 리스트, 코스닥 1위)**
```json
{"ts":"2026-09-17T05:36:07+09:00","as_of":"2026-09-16T15:30:00+09:00","list_date":"2026-09-17","market":"KR-KOSDAQ",
 "model_id":"rule-pick-v1","phase":"open","basis":"eod","stock_code":"072950","rank_no":1,"score":6.9,
 "reasons":["top_gainer","limit_up","new_high","money_surge"],"warn":["blowoff"],
 "label_kr":"상승 상위·상한가·신고가·대금 급증",
 "metrics":{"chg1_pct":30,"money":195459058900,"money_ratio":26.5045,"high20_gap_pct":20.5373,"limit_up_streak":1,
            "foreign_net5":null,"inst_net5":null,"sector_l1":"반도체·전자",
            "lens_rank":{"top_gainer":1,"limit_up":2,"new_high":2,"money_surge":10},
            "lens_cfg":{"k":10,"list_max":40,"min_money":{"…":"…"}}},
 "rule_ver":"pick-rules-v1"}
```

### 최신 줄 규칙 (읽는 쪽 계약)
같은 (`list_date`, `market`, `model_id`, `stock_code`)에서 **`as_of`가 가장 늦은 줄이 현재값.** 삭제는 없다 —
아침에 뽑힌 종목은 하루 종일 리스트에 있다. 장중 추가 줄은 `reasons`에 아침 사유를 포함하므로 줄을 합칠 필요가 없다.

---

## 3. 태그 어휘 (reasons)
| 태그 | 뜻 |
|---|---|
| `money_surge` | 거래대금이 평소(20일 평균)보다 크게 늘었다 |
| `top_gainer` / `top_loser` | 당일 등락률 상위 / 하위 |
| `limit_up` | 상한가 마감 |
| `new_high` | 직전 20일 고가 위에서 마감 |
| `foreign_buy` / `inst_buy` / `both_buy` | 외인 / 기관 / 둘 다 순매수 상위(순위 축적 5일) |
| `sector_counter` | 시장이 대체로 내리는데 오른 업종(대장주 기준)의 대금 상위 |
| `intraday_add` | 장중 순위로 덧붙인 종목 |
| `random` / `topcap` / `carry` | 기준선 모델의 사유 |
새 태그를 쓰려면 먼저 합의한다 — 읽는 쪽이 모르는 태그는 무시된다. **`warn`은 배제가 아니라 경고**다(옛 오딘: 블로우오프 전면 배제는 틀렸고 극단 꼬리만 문제).

---

## 4. 지켜야 할 것 (검증 자동 검사 항목)
1. **미래를 보지 않는다.** `as_of` 이전 데이터만. 평균·분포도 그 시점까지로 자른다.
2. **증권 키·주문에 접근하지 않는다.** 외부로 데이터를 보내지 않는다.
3. **append만.** 남의 줄·원천 표를 고치지 않는다.
4. 밤 리스트는 **다음 거래일 08:50 전**에 끝낸다. 장중 추가는 10분 안에.
5. 재료가 없으면 **그 조건을 건너뛰고 metrics에 남긴다**(예: `foreign_net5: null`).
6. `model_id`는 `dim_model`에 등록된 값만. 규칙 숫자를 바꾸면 `rule_ver`를 올린다.
7. **시장 온도를 조건으로 쓰지 않는다.** (온도 재료를 파라미터로 다시 계산해 쓰는 건 허용 — 예: 업종 대장주 등락)

---

## 5. 지금 실제 상태 (2026-09-17 05:40)
| 표 | 줄 수 | 비고 |
|---|---|---|
| `raw_stock_ohlc` | 1,384,077 | 2024-08-22~2026-09-15 공공데이터 백필(krx) + 09-16 KIS 스냅샷(kis) |
| `raw_rank_snapshot` | 0 | 수집기 준비됨, 첫 장중 가동 전 |
| `stock_params` | 2,761 | 2026-09-16 bg_daily |
| `pick_list` | 219 | 2026-09-17 아침 리스트: rule-pick-v1 코스피 25·코스닥 34 + 기준선 |
| `dim_model` kind=pick | 4 | rule-pick-v1, 기준선 3 |
- 수급 태그(`foreign_buy` 등)는 순위 축적 5거래일 뒤부터. 장중 추가(`phase=intraday`)는 미구현.

## 6. 바뀔 때
표 칸이 바뀌면 `db/*.sql`과 이 문서를 같이 고친다. 파라미터 계산식 변경 → `calc_ver`, 규칙 변경 → `rule_ver`, 모델 자체 변경 → `model_id` 버전.
