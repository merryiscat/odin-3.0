<!-- kickoff-workspaces v0.5.0 — 원본은 ohmypm 플러그인. 여기서 고치지 말고 플러그인을 고친 뒤 "구조 업데이트하자" -->
# 작업 프로토콜 — 단일 패스 교차 검토와 등급별 구현

역할·모델·경로는 대상 프로젝트 `docs/roles.md`가 단일 출처. 이 문서는 절차와 종료 조건만 소유한다.
원본은 ohmypm 플러그인 `skills/kickoff-workspaces/PROTOCOL.md`, 프로젝트의 `docs/protocol.md`는 설치 시 복사본이다 — 절차를 고치면 원본을 고치고 다시 복사한다.

## 0. 차선 — 요청마다 같은 요금을 내지 않는다

여섯 턴 직렬(스펙·검토·v2·게이트·워커·검증)은 틀리면 비싼 일을 위한 것이다(실측 20~40분). 요청을 받을 때 main이 차선을 정한다. 사용자가 "가볍게"·"제대로"라고 하면 그게 이긴다.

| 차선 | 기준(하나라도 어긋나면 위 차선으로) | 절차 | 목표 |
|---|---|---|---|
| **S 즉시** | 파일 1~2개 · 요청이 곧 완료 기준 · 되돌리기 쉬움(git revert 한 번) · 운영 영향 없음 | **main이 직접** 처리하고 커밋. 스펙·검토·게이트 없음. 커밋 메시지가 기록 | 3~5분 |
| **M 표준** | 여러 파일 · 판단이 조금 필요 · 되돌릴 수 있음 | pl 스펙 한 장(기준 ≤ 5, 30줄) → pl2 검토는 **차단 항목만, 10줄 이내**(차단 없으면 "없음" 한 줄, v2 생략) → 게이트 없음(roles.md가 켜지 않는 한) → pl3가 워커 배정 → pl3 검증은 기준별 한 줄(재조회는 차단 기준만) | 10분 안팎 |
| **L 중대** | 운영 환경 · 되돌리기 어려움(삭제·마이그레이션·외부 발신·구조 변경) · 미지 영역 | 아래 1~7절 전부. 게이트 있음 | 20~30분 |

반복 운영 업무는 첫 회 차선대로, 이후는 스펙 재사용(pl 턴 0). S 판정이 틀려 커지면 그 자리에서 멈추고 M으로 올린다 — 되돌리기 쉬운 일만 S이므로 손해가 없다.

## 토큰 규율 — 비싼 모델은 판단하는 순간에만 쓴다

pl 자리의 모델은 종량·소액 쿼터다(2026-09-17 사용자: "함부로 토큰 날리면 치명적"). 그래서 **pl은 두 번만 켠다**:
스펙 v1 작성, 검토 반영 v2 작성. 검토는 pl2(Claude 최상위 모델)가 하되 **검토서 한 장뿐**이다.
그 밖의 모든 것 — 게이트, 워커 배정, 대기, 검증, compact — 은 배관 작업이라 **pl3(Claude 하위 모델)**가 한다. 정액제도 한도가 있고 최상위 모델이 가장 빨리 깎는다(2026-09-18 실측: pl2에 전부 몰았더니 페이블 사용량이 감당이 안 됐다). 이종 교차 검토의 근거(아래)는 벤더가 다르면 되는 것이지 검토 이후의 배관까지 최상위 모델일 이유가 아니다.
pl이 하지 않는 것: 코드·`src/` 읽기(워커·검증자 몫), 코드 실행·테스트·설치·서버 기동(셸은 `docs/` 읽고 쓰는 용도로만 — Codex는 파일도 셸로 읽는다), 오케스트레이션 `check --wait`
같은 대기 턴, 서브에이전트·ultra 모드, 스펙 한 장(완료 기준 ≤ 8개)을 넘는 장문, 요청받지 않은 재작성.
pl2·pl3도 워커 일을 대신하지 않는다 — pl2는 검토만, pl3는 배정·판정만. 구현·문서 작성은 등급 모델 워커가 새 워크트리에서.


### 모델 모드 — 페이블 한도가 소진된 주

Claude Code는 **쿼터 소진으로 모델을 자동으로 내리지 않는다**. `--fallback-model`과 settings.json `fallbackModel`은
과부하·미사용 가능만 대체하고 rate limit·요금 오류는 대체하지 않는다(공식 문서 model-config). 그래서 스위치를 손으로 넘긴다:
```
"${CLAUDE_PLUGIN_ROOT}"/skills/kickoff-workspaces/scripts/ws-model.sh fable-out <프로젝트>...   # 한도 소진
"${CLAUDE_PLUGIN_ROOT}"/skills/kickoff-workspaces/scripts/ws-model.sh fable-in  <프로젝트>...   # 한도 복귀
```
`fable-out`이면 roles.md의 페이블 자리(pl2·L 워커)가 오퍼스가 되고 **pl3를 열지 않는다** — pl2가 오퍼스라 검토와
코디네이션을 겸한다. 교차 검토의 근거는 벤더 차이이므로 Codex↔Claude 구도는 그대로다.
현재 모드는 roles.md 맨 위 인용 줄에 있다(없으면 `fable-in`). `status`로도 본다.

## 흐름

```
pl:  스펙 v1 (docs/tasks/T-NNN-<slug>.md)                          ← Codex 켜는 순간 1
  → pl2: 검토서 1회 (docs/reviews/T-NNN.review-v1.md)
  → pl:  지적마다 수용 / 근거 기각 → 스펙 v2                         ← Codex 켜는 순간 2
  → pl3: 차단 지적이 기각됐나?  예 → 게이트(사용자 결정)   아니오 → 게이트(스펙 승인)
  → pl3: 등급에 맞는 워커 배정 (새 워크트리), check --wait
  → 워커: 구현 + worker_done (완료 기준 항목별 통과/실패)
  → pl3: 스펙 기준으로 검증 — 통과/실패만, 고치지 않는다
  → main: 머지·푸시
```

## 1. 스펙 (pl)

`templates/task.md` 형식. 완료 기준은 **관찰 가능한 문장**으로 쓴다 — "저장된다"가 아니라
"`GET /x`가 200과 `{"ok":true}`를 돌려준다". 기준 하나에 확인 방법 하나. 스펙 밖 변경은 워커에게 금지된다.
난이도 등급(S/M/L)과 근거를 적는다 — 등급이 곧 모델이다(roles.md 등급표).
'필요 도구' 항목에 워커가 설치해야 할 스킬·MCP·패키지를 적는다. **스펙에 없는 도구는 설치하지 않는다.**

## 2. 검토 (pl2) — 한 번만

`templates/review.md` 형식. **M 차선은 차단 항목만 10줄 이내, 차단이 없으면 "지적 없음" 한 줄로 끝내고 v2를 부르지 않는다.** 규칙:
- **재작성 금지.** 대안 문안을 통째로 쓰지 않는다. 지적은 "무엇이 / 왜 문제인가(근거) / 심각도 / 차단 여부".
- 산출물만 본다. 이전 대화·pl의 의도 추정을 비평하지 않는다.
- 진짜 문제가 없으면 "지적 없음"으로 끝낸다 — 억지로 채우지 않는다.
- 같은 스펙에 두 번째 검토는 **내용이 크게 바뀐 새 버전**(v2 이상)에만, 그리고 pl이 요청할 때만.

## 3. 반영 (pl)

지적마다 `수용`(무엇을 고쳤나) 또는 `기각`(근거)을 스펙의 "검토 반영" 절에 남긴다. 검토서는 고치지 않는다.
**차단 지적을 기각했다면 토론하지 않는다** — pl은 "차단 기각 있음"이라고 보고하고 멈추며, pl3가 게이트로 올린다:
```
orca orchestration gate-create --task <taskId> --question "T-NNN: <쟁점 한 줄>" --options '["pl안","pl2안"]' --json
```

## 4. 게이트와 배정 (pl3)

스펙 승인은 사용자 몫이다 — **L 차선만**. M은 roles.md에 `게이트: M` 이 있을 때만 묻는다. 승인 뒤에만 워커를 띄운다. 이 절은 pl3가 실행한다 — pl은 배정·대기 턴을 쓰지 않고, pl2는 검토서 뒤엔 부르지 않는다.
**배정 전 확인**: 워커 워크트리는 저장소 기본 base(보통 origin/main)에서 뜬다. 승인된 스펙이 그 base에 있어야 하므로, main이 pl 브랜치를 머지·푸시한 뒤 배정한다(2026-09-17 실측 — 원격이 앞서 있어 스펙이 워커 브랜치에 없었다).
```
orca orchestration run-create --objective "<프로젝트> 구현" --json                # 세션당 한 번
orca orchestration task-create --spec "docs/tasks/T-NNN-<slug>.md 를 읽고 완료 기준을 전부 만족시켜라" --json
orca orchestration gate-create --task <taskId> --question "T-NNN 스펙 승인?" --options '["yes","no"]' --json
orca orchestration worker-start --task <taskId> --worktree new-top-level --name T-NNN-<slug> \
  --agent claude --model <등급 모델> --effort <등급 effort> --json
orca orchestration check --wait --types "worker_done,escalation,question" --timeout-ms 900000 --json
```
`launch.effective`로 실제 모델을 확인한다 — 요청값을 믿지 않는다. 질문(`question`)이 오면 스펙으로 답할 수
있는 것만 답하고, 스펙을 바꿔야 하는 질문은 스펙 vN+1로 되돌린다.

## 5. 구현 (워커)

스펙 하나, 워크트리 하나, 브랜치 `T-NNN-<slug>`. 완료 보고(`worker_done`) 본문에 완료 기준을 **항목별로**
`통과/실패 + 확인한 방법`으로 적는다. 실행하지 않은 검증을 실행했다고 쓰지 않는다 — 안 됐으면 `실패`.
스펙 밖이 필요해지면 고치지 말고 `ask`로 올린다.

## 6. 검증 (pl3)

워커 브랜치를 읽고 완료 기준마다 통과/실패를 판정해 스펙의 "검증" 절에 적는다(pl3가 스펙 파일을 쓰는 유일한 경우). **M 차선은 기준별 한 줄, 재조회·재실행은 차단 기준에만** — 전부 재검증하면 검증이 워커만큼 걸린다(실측 5분).
검증은 산출물을 읽는 것이 기본이고, 재현 실행이 필요하면 해도 된다. 단 **오래 걸릴 명령은 백그라운드로 돌리고 진행 로그를 남긴다**(줄 출력·파일 증가). 멈춤은 총 시간이 아니라 **진행 신호가 끊긴 채 오래**(기본 10분, 작업 성격에 따라 늘린다)로 판정한다 — 실측: 신호 없는 재현 명령 하나가 30분 서 있어 바퀴 전체가 섰다. **고치지 않는다** —
실패면 사유를 적어 같은 워커에 되돌린다(2회까지). 스펙 자체를 고쳐야 하면 그때만 pl을 켠다. 전부 통과면 main에 머지를 요청한다.

## 7. main

머지·커밋·푸시만. main에서 코드를 고치지 않는다. 머지 후 워커 워크트리는 `orca worktree rm`으로 정리한다.

## 8. 세션 위생 — `/clear`와 `/compact`

세션은 쌓일수록 비싸고(종량 pl은 매 턴 전체 문맥을 다시 보낸다) 흐려진다. 기억은 세션이 아니라 **파일**(스펙·검토서·검증 표)에 있다.
**`/clear`는 태스크 경계**(잃을 게 없을 때), **`/compact`는 태스크 도중**(상태를 들고 있어 비울 수 없는데 문맥이 커졌을 때). 슬래시 명령은 에이전트가 스스로 못 치므로 main이 터미널에 보낸다(dispatch 4·5절).

| 자리 | `/clear` | `/compact` | 하지 않는 것 |
|---|---|---|---|
| pl (Codex) | **태스크마다** — v2 보고 뒤, 또는 "차단 기각 있음" 보고 뒤 | 안 한다(compact도 턴 비용, v1→v2는 파일로 충분) | v1과 v2 사이 clear |
| pl2 (Claude 검토) | **검토서를 보고한 직후** — 들고 있을 상태가 없다 | 안 한다(검토는 한 턴) | 검토 중 clear |
| pl3 (Claude 코디) | 태스크가 **main에 머지되고 워커 워크트리를 지운 뒤** | ① worker_done 받은 직후, 검증(큰 산출물 읽기) 전 ② 반복 업무를 같은 세션에서 여러 회 돌릴 때 회차 사이 ③ 대기 중 질문 왕복이 길어졌을 때 | 배정·대기·판정 중 clear(run·task·dispatch id·핸들을 잃는다) |
| 워커 | 워크트리 삭제로 끝난다(세션도 함께) | 되돌림 재작업 전(2회까지 같은 세션) | — |
| main | 한 바퀴 머지 뒤 권장 | 세션이 길어졌는데 바퀴가 도는 중이면 | — |

공통: `AGENTS.md`·`CLAUDE.md`·`docs/protocol.md`·`docs/roles.md`가 바뀌면 **즉시** 비운다 — 시작할 때만 읽는다.
Orca를 재시작했으면 터미널 핸들이 바뀐다 — `orca terminal list --worktree name:pl`로 다시 잡는다.
pl3가 대기 중 끊겼으면 비우지 말고 `orca orchestration worker-list --include-remote --json`으로 dispatch·핸들을 되찾아 이어간다.
`/compact`로 버티지 않는다 — pl에선 compact도 턴 비용이고, pl3는 어차피 머지 뒤 비운다.
명령: 두 CLI 모두 `/clear`(새 대화). dispatch 3절이 검토 뒤 pl2에, 5절이 머지 뒤 pl·pl3에 자동으로 보낸다.
**Git Bash에서 보내지 않는다** — MSYS 경로 변환이 `/clear`를 `C:/Program Files/Git/clear`로 바꿔 텍스트로 꽂힌다(2026-09-18 두 번 실측). PowerShell에서 보내거나 `MSYS_NO_PATHCONV=1`을 앞에 붙인다.
**보내기 전 확인**: 화면에 "esc to interrupt / ctrl+b to run / Waiting for"가 있으면 일하는 중이다 — 그때는 아무것도 보내지 않는다(텍스트가 실행 중 명령의 입력으로 들어간다). 멈춘 것 같으면 먼저 `orca terminal send --interrupt`.
**`❯`가 떠 있어도 들고 있는 게 있을 수 있다** — 하단에 "N shell still running / ← N agent"가 보이면 `check --wait` 같은 백그라운드 대기를 쥔 것이다. 그 자리는 그 건이 머지될 때까지 비우지 않는다(2026-09-18 실측: 규칙 갱신 직후 pl2에 `/clear`를 보냈는데 워커 대기를 들고 있었다). **보낸 뒤 확인**: `/clear`는 텍스트로 들어가 한 턴을 태우기도 한다(같은 날 실측 — 세션이 "화면 정리 명령이라 처리할 것 없음"이라고 답했다). 보낸 뒤 화면을 읽어 새 대화 시작 화면인지 본다.

## 근거 (2026-09-17 조사)

- 이종 모델 조합만 일관되게 성능을 올렸다. 같은 모델에 프롬프트만 다른 토론은 다수결보다 못했다 —
  [Revisiting MAD as Test-Time Scaling](https://arxiv.org/abs/2505.22960), [MAD 9 벤치마크 종합](https://beancount.io/bean-labs/research-logs/2026/05/24/multiagent-debate-factuality-reasoning-llms)
- 검토 라운드를 늘리면 진짜 오류가 소진된 뒤 없는 문제를 만들고(정밀도 0.30→0.20) 대화 자체를 비평하는
  쪽으로 표류한다. 단일 패스가 모든 다회차 변형을 이겼다 — [More Rounds, More Noise](https://arxiv.org/abs/2603.16244)
- 아첨성 조기 합의와 동의 표류 — [Peacemaker or Troublemaker](https://arxiv.org/abs/2509.23055),
  [Agreement Drift](https://arxiv.org/abs/2604.11312), [The Cost of Consensus](https://arxiv.org/abs/2605.00914)
- 교차 벤더 코드 리뷰: Claude가 Codex 초안 검토 시 71.6→89.7%, Codex가 Claude 초안 검토 시 91.4→82.8%.
  해로운 검토는 통째 재작성, 도움이 된 검토는 국소 지적 — [Cross-Model LLM Code Review](https://arxiv.org/abs/2607.21656).
  → 검토자 재작성 금지, 작성자=Codex·검토자=Claude 기본 배정
- 스펙 주도 개발: 기획·구현·검증 분리, 검증 가능한 완료 기준, 구현자가 아닌 별도 검증자 —
  [SDD in 2026](https://dev.to/krlz/spec-driven-development-in-2026-what-it-is-the-tooling-and-how-teams-actually-use-it-2fk2)
