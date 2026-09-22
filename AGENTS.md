# AGENTS.md — Codex용 지시문 (Claude는 CLAUDE.md를 읽는다)


## 작업 구조 (kickoff-workspaces v1.0.0)
- 역할은 `docs/roles.md`, 모델은 `docs/workflow.json`, 절차는 `docs/protocol.md`, 명령은 `docs/workflow-guide.md`.
- main은 접수·알림·환경 준비·로컬 머지, pl은 설계·품질 책임, work는 승인된 범위의 구현을 맡는다.
- pl은 사용자와 필요한 만큼 논의하고 기준·검증 방법을 함께 정한다. 질문은 main에 알려 사용자와 직접 대화한다.
- pl은 코드 열람·work 환경 검증이 가능하지만 구현 수정은 work에 돌린다. 최고 성능 모델을 pl에 둔다.
- 특정 설계 버전의 사용자 구현 요청 후에만 work를 시작한다. 기준 변경은 재합의하고 재검증한다.
- 독립성이 확인된 work만 최대 두 개 병렬. 단순하고 가역적인 요청은 main이 직접 처리한다.
- pl 판정은 스펙·결과·통합 기준에 연결한다. main은 검증된 결과만 로컬 머지하며 푸시·배포는 별도다.
- 상태·증거는 Git 공통 디렉터리에 보존한다. 중복 실행·타임아웃에 의한 무단 종료·미반영 변경 삭제를 금지한다.
