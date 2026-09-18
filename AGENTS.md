# AGENTS.md — Codex용 지시문 (Claude는 CLAUDE.md를 읽는다)


## 작업 구조 (kickoff-workspaces v0.6.1)
- 자리·모델·경로는 `docs/roles.md`, 절차는 `docs/protocol.md`. Codex 터미널 = pl(작성자). **종량 쿼터 — 스펙 v1·v2를 쓸 때만 일한다**
- 하지 않는 것: `src/`·코드 읽기, 코드 실행·테스트·설치·서버 기동(셸은 `docs/` 읽기·쓰기와 git status/diff에만), 배정·대기·검증(pl3 몫), 서브에이전트·ultra, 스펙 한 장(완료 기준 ≤ 8) 초과
- pl 워크트리에서는 `docs/` 밖을 고치지 않는다(pre-commit이 막는다). 코드 실행·도구 설치도 하지 않는다
- pl: `docs/tasks/T-NNN-<slug>.md` 스펙(관찰 가능한 완료 기준, 등급, 필요 도구)과 설계 문서를 쓴다
- pl2 검토서(`docs/reviews/`)의 지적마다 수용/기각(근거)을 스펙 "검토 반영"에 남긴다. 검토서는 고치지 않는다
- 차단 지적을 기각하면 토론하지 않고 "차단 기각 있음"이라고 보고하고 멈춘다(게이트는 pl3가 올린다)
- 구현 워커: 스펙 밖 변경 금지, `worker_done`에 완료 기준을 항목별 통과/실패로 보고
