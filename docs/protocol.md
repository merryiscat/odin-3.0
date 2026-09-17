# 작업 프로토콜 — 단일 패스 교차 검토와 등급별 구현

역할·모델·경로는 대상 프로젝트 `docs/roles.md`가 단일 출처. 이 문서는 절차와 종료 조건만 소유한다.
원본은 ohmypm 플러그인 `skills/kickoff-workspaces/PROTOCOL.md`, 프로젝트의 `docs/protocol.md`는 설치 시 복사본이다 — 절차를 고치면 원본을 고치고 다시 복사한다.

## 토큰 규율 — pl(Codex)은 쓰는 순간에만 쓴다

pl 자리의 모델은 종량·소액 쿼터다(2026-09-17 사용자: "함부로 토큰 날리면 치명적"). 그래서 **pl은 두 번만 켠다**:
스펙 v1 작성, 검토 반영 v2 작성. 그 밖의 모든 것 — 검토, 게이트, 워커 배정, 대기, 검증, 머지 — 은 정액제인 pl2(Claude)가 한다.
pl이 하지 않는 것: 코드·`src/` 읽기(워커·검증자 몫), 코드 실행·테스트·설치·서버 기동(셸은 `docs/` 읽고 쓰는 용도로만 — Codex는 파일도 셸로 읽는다), 오케스트레이션 `check --wait`
같은 대기 턴, 서브에이전트·ultra 모드, 스펙 한 장(완료 기준 ≤ 8개)을 넘는 장문, 요청받지 않은 재작성.
pl2도 워커 일을 대신하지 않는다 — 검토·배정·판정만. 구현·문서 작성은 등급 모델 워커가 새 워크트리에서.

## 흐름

```
pl:  스펙 v1 (docs/tasks/T-NNN-<slug>.md)                          ← Codex 켜는 순간 1
  → pl2: 검토서 1회 (docs/reviews/T-NNN.review-v1.md)
  → pl:  지적마다 수용 / 근거 기각 → 스펙 v2                         ← Codex 켜는 순간 2
  → pl2: 차단 지적이 기각됐나?  예 → 게이트(사용자 결정)   아니오 → 게이트(스펙 승인)
  → pl2: 등급에 맞는 워커 배정 (새 워크트리), check --wait
  → 워커: 구현 + worker_done (완료 기준 항목별 통과/실패)
  → pl2: 스펙 기준으로 검증 — 통과/실패만, 고치지 않는다
  → main: 머지·푸시
```

## 1. 스펙 (pl)

`templates/task.md` 형식. 완료 기준은 **관찰 가능한 문장**으로 쓴다 — "저장된다"가 아니라
"`GET /x`가 200과 `{"ok":true}`를 돌려준다". 기준 하나에 확인 방법 하나. 스펙 밖 변경은 워커에게 금지된다.
난이도 등급(S/M/L)과 근거를 적는다 — 등급이 곧 모델이다(roles.md 등급표).
'필요 도구' 항목에 워커가 설치해야 할 스킬·MCP·패키지를 적는다. **스펙에 없는 도구는 설치하지 않는다.**

## 2. 검토 (pl2) — 한 번만

`templates/review.md` 형식. 규칙:
- **재작성 금지.** 대안 문안을 통째로 쓰지 않는다. 지적은 "무엇이 / 왜 문제인가(근거) / 심각도 / 차단 여부".
- 산출물만 본다. 이전 대화·pl의 의도 추정을 비평하지 않는다.
- 진짜 문제가 없으면 "지적 없음"으로 끝낸다 — 억지로 채우지 않는다.
- 같은 스펙에 두 번째 검토는 **내용이 크게 바뀐 새 버전**(v2 이상)에만, 그리고 pl이 요청할 때만.

## 3. 반영 (pl)

지적마다 `수용`(무엇을 고쳤나) 또는 `기각`(근거)을 스펙의 "검토 반영" 절에 남긴다. 검토서는 고치지 않는다.
**차단 지적을 기각했다면 토론하지 않는다** — pl은 "차단 기각 있음"이라고 보고하고 멈추며, pl2가 게이트로 올린다:
```
orca orchestration gate-create --task <taskId> --question "T-NNN: <쟁점 한 줄>" --options '["pl안","pl2안"]' --json
```

## 4. 게이트와 배정 (pl2)

스펙 승인은 사용자 몫이다. 승인 뒤에만 워커를 띄운다. 이 절은 pl2가 실행한다 — pl은 배정·대기 턴을 쓰지 않는다.
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

## 6. 검증 (pl2)

워커 브랜치를 읽고 완료 기준마다 통과/실패를 판정해 스펙의 "검증" 절에 적는다(pl2가 스펙 파일을 쓰는 유일한 경우). **고치지 않는다** —
실패면 사유를 적어 같은 워커에 되돌린다(2회까지). 스펙 자체를 고쳐야 하면 그때만 pl을 켠다. 전부 통과면 main에 머지를 요청한다.

## 7. main

머지·커밋·푸시만. main에서 코드를 고치지 않는다. 머지 후 워커 워크트리는 `orca worktree rm`으로 정리한다.

## 8. 세션 비우기 — 언제 `/clear` 하나

세션은 쌓일수록 비싸고(종량 pl은 매 턴 전체 문맥을 다시 보낸다) 흐려진다. 기억은 세션이 아니라 **파일**(스펙·검토서·검증 표)에 있다.

| 자리 | 비우는 시점 | 비우지 않는 시점 |
|---|---|---|
| pl (Codex) | **태스크마다** — v2 보고 뒤, 또는 "차단 기각 있음" 보고 뒤 | 스펙 v1과 v2 사이 |
| pl2 (Claude) | 태스크가 **main에 머지되고 워커 워크트리를 지운 뒤** | 검토·배정·대기·판정 중(run·task·dispatch id와 핸들을 들고 있다) |
| 워커 | 워크트리 삭제로 끝난다(세션도 함께) | 되돌림 재작업은 같은 세션에서(2회까지) |
| main | 한 바퀴 머지 뒤 권장 | — |

공통: `AGENTS.md`·`CLAUDE.md`·`docs/protocol.md`·`docs/roles.md`가 바뀌면 **즉시** 비운다 — 시작할 때만 읽는다.
Orca를 재시작했으면 터미널 핸들이 바뀐다 — `orca terminal list --worktree name:pl`로 다시 잡는다.
pl2가 대기 중 끊겼으면 비우지 말고 `orca orchestration worker-list --include-remote --json`으로 dispatch·핸들을 되찾아 이어간다.
`/compact`로 버티지 않는다 — pl에선 compact도 턴 비용이고, pl2는 어차피 머지 뒤 비운다.
명령: 두 CLI 모두 `/clear`(새 대화). dispatch 5절이 머지 뒤 pl·pl2에 자동으로 보낸다.

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
