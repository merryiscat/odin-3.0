# ohmyPM 1.0 실행 가이드

Python 3.11+ 표준 라이브러리만 사용한다. 설치 후 `python .ohmypm/bin/workflow.py --help`.
`--project`는 명령 앞의 공통 옵션이다. 어느 워크트리에서든 같은 Git 공통 상태를 읽는다.
상태는 `.git/ohmypm-v1/`에 있어 원문·질문·모델 결과·로컬 경로를 커밋하지 않는다.
운영 정보는 인증·보안 경계가 아니다. CLI의 승인·reviewer 입력은 실제 사용자 결정과 pl의 관찰을 기록하는 계약이다.

## 설계와 입력

스펙은 `docs/tasks/T-NNN-*.md`, manifest는 같은 작업의 JSON이다. 스펙 최대 8개 기준을 다음 형태로 대응시킨다.
모델 기본값은 `docs/workflow.json`의 roles.work, 작업별 override는 works[].agent다.

```json
{
  "criteria": [
    {"id": "C1", "description": "example.txt에 hello가 저장된다",
     "command": ["python", "-c", "from pathlib import Path; assert Path('example.txt').read_text().strip() == 'hello'"]},
    {"id": "C2", "description": "사용자 흐름이 합의한 설계와 일치한다", "manual": "pl이 실제 동작을 확인하고 근거를 기록"}
  ],
  "works": [
    {"id": "greeting", "paths": ["example.txt"], "criteria": ["C1", "C2"], "depends_on": [],
     "parallel_safe": false, "resources": [],
     "harness": {"tools": ["python", "git"], "setup": [], "files": [], "inherit_env": [], "ports": []}}
  ]
}
```

- `paths`: 수정할 파일·디렉터리 prefix. glob은 받지 않으며 `.`은 저장소 전체로 취급한다.
- `depends_on`: 같은 manifest의 work ID. 의존 work가 main에 머지된 뒤 시작한다. 순환은 거부한다.
- 병렬은 `parallel_safe: true`와 `independence_reason`이 필요하다. 경로·resources가 겹치면 순차다.
  DB·공유 서비스 등 격리하지 못하는 자원은 같은 이름으로 resources에 적는다. 프로젝트 전체 활성 work 상한은 두 개다.
- `harness.setup`: argv 배열의 배열. 승인된 의존성 설치 등 준비 명령이다. 셸 문자열을 합치지 않는다.
- `harness.files`: `source`(로컬 파일), `target`(작업 상대 경로). 스킬 파일과 `.mcp.json`, `.codex/config.toml`,
  `.claude/settings.local.json`만 허용한다. 소스 해시가 승인 후 바뀌면 중단한다. 원본 설정은 로컬 백업한다.
  스킬 폴더는 파일별로 열거한다. MCP의 실행 파일·설정 요구사항도 tools/setup/검증 기준에 명시한다.
- `inherit_env`: 작업에 필요한 환경변수 이름만 지정한다. 값은 계약에 저장하지 않는다.
- `ports`: 예를 들어 `["APP_PORT"]`. 작업별 사용 가능한 로컬 포트를 배정한다. 외부 프로세스와 경쟁하면
  실제 서비스의 bind 실패를 보고하며 다른 작업 포트를 빼앗지 않는다.
- `agent`: 선택적 `{ "agent": "claude", "model": "sonnet" }`. pl과 합의된 경우만 변경한다.

## main의 순서

아래 경로·T-번호는 실제 작업으로 바꾼다. 사용자 결정 파일에는 실제 구현 요청을 그대로 기록한다.
원문·결정·manifest 입력 파일을 비공개 위치에 두어도 된다. create가 불변 버전 사본을 보존한다.

```text
python .ohmypm/bin/workflow.py doctor
python .ohmypm/bin/workflow.py create --task T-006 --request-file request.txt --spec docs/tasks/T-006.md --manifest task.json --main <main-path> --main-address <main-inbox> --pl-address <pl-conversation>
python .ohmypm/bin/workflow.py question --task T-006 --file question.md
python .ohmypm/bin/workflow.py events --task T-006
python .ohmypm/bin/workflow.py deliver --task T-006 --event <event-id>
python .ohmypm/bin/workflow.py revise --task T-006 --spec docs/tasks/T-006.md --manifest task.json
python .ohmypm/bin/workflow.py approve --task T-006 --revision 2 --decision-file user-decision.txt
python .ohmypm/bin/workflow.py schedule --task T-006
python .ohmypm/bin/workflow.py prepare --task T-006 --work greeting
```

`doctor`는 실행 중인 Orca 확인까지 한다. Orca 실행 파일은 `--orca`로 지정할 수 있다.
Orca skills 안내로 현재 명령을 확인하고 main이 Run을 준비한 뒤 `launch`한다:

```text
orca skills get orchestration
orca orchestration run-create --objective "T-006 구현" --json
python .ohmypm/bin/workflow.py launch --task T-006 --work greeting
```

`launch`는 준비된 워크트리에 `worker-start --spec`을 사용한다. main이 유일한 coordinator다.
Orca 메시지 소비·질문 답변·worker_done 확인·worker-release는 orchestration 스킬을 따른다.
pl은 사용자 대화와 품질 판단을 맡는다. main이 pl 대화 위치를 사용자에게 안내하며 pl을 코디네이터로 중복 등록하지 않는다.

## work와 pl

work는 받은 context의 실제 CLI 절대 경로를 사용한다. 도구는 `exec` 안에서 실행해야 작업 환경이 적용된다.
임시 파일·데이터·Python cache와 venv 경로는 work에 묶이며 의존성 symlink/junction은 거부한다.
워크트리의 지침·합의한 설정을 읽고 승인 범위만 커밋한다. 하네스 overlay는 커밋하지 않는다.

```text
python <workflow.py> --project <work-path> exec --task T-006 --work greeting -- python <tool.py>
python <workflow.py> --project <work-path> submit --task T-006 --work greeting --report <report.md>
python <workflow.py> --project <work-path> check --task T-006 --work greeting
python <workflow.py> --project <work-path> show --task T-006
```

pl은 work의 코드와 실제 동작을 확인하고 show의 submission과 같은 candidate를 사용해 review를 쓴다.
자동 검증만 통과했다고 수동 기준의 증거를 만들어내지 않는다.

```json
{
  "reviewer": "pl",
  "candidate": {"commit": "실제 결과 SHA", "base": "실제 main SHA", "revision": 2, "digest": "실제 스펙 digest"},
  "code_review": "읽은 변경과 확인한 동작을 구체적으로 기록",
  "criteria": {
    "C1": {"status": "pass", "evidence": "실행 도구가 남긴 실제 검증 기록 위치"},
    "C2": {"status": "fail", "evidence": "관찰한 실패 입력과 실제 결과"}
  }
}
```

```text
python <workflow.py> verdict --task T-006 --work greeting --review <review.json>
python <workflow.py> --project <main-path> merge --task T-006 --work greeting
```

실패한 작업은 동일한 work에서 고친다. 이전 Dispatch 정산 후 main이 Orca의 새 Task로 수정 요청을 전달한다.
수정한 커밋은 다시 submit→check→verdict한다. main이 바뀌었으면 먼저 work에서 main을 통합한다.
merge는 dirty main·stale verdict·범위 밖 수정·하네스 설정 커밋을 거부하고 fast-forward만 수행한다.

## 중단·보존·회수

- 상태 쓰기는 OS lock과 atomic replace를 사용한다. 충돌한 상태 변경 명령은 완료 후 다시 호출한다.
  상태 조회와 독립 work의 exec는 전역 쓰기 lock을 잡지 않으므로 작업 명령은 병렬로 실행할 수 있다.
- prepare가 create 도중 끊기면 같은 이름의 Orca 워크트리를 조회한다. 없거나 여러 개면 생성 여부를 단정하지 않는다.
- setup의 실패·실행 여부 불명은 자동 재실행하지 않는다. 종료를 확인하고 기존 work를 retain한 뒤 새 버전으로 준비한다.
- launch·deliver의 receipt가 불명확하면 상태는 starting/sending으로 남는다. Orca의 request-show·worker-list로 확인하고
  해당 receipt의 recovery 명령을 따른다. 주기적인 재전송으로 복구하지 않는다. deliver 실패여도 events에 질문은 남는다.
- 기획 변경 전 Orca로 이전 worker를 정산하고 소유 터미널을 닫는다. `retain --settlement <receipt.json>`으로 변경을
  보존한 뒤 revise한다. 이전 work의 위치와 근거는 history에 남아 자동 삭제되지 않는다.
- merge가 끊겼으면 main HEAD를 대조해 이미 적용된 정확한 커밋만 정산한다. main이 원래 기준이면
  정상 검증 조건을 다시 확인하고 재시도한다. 그 외 판단 불명 상태는 유지한다.
- 결과 보고와 pl 판정은 Git 공통 디렉터리에 보존된다. main은 worker-release 뒤 확인된 빈 셸만 닫는다.
  사용자·무관한 터미널은 닫지 않는다. cleanup은 성공 receipt와 실제 터미널 부재를 모두 확인한다.

```text
python <workflow.py> cleanup --task T-006 --work greeting --settlement <orca-receipt.json>
```

cleanup은 미반영 변경·새 커밋·남은 터미널·새로 생기거나 수정된 개인 ignore 파일을 만나면 멈춘다.
처음 복사된 ignore 파일은 해시가 같은 경우에만 회수 대상이다. 작업 데이터는 먼저 로컬 상태 폴더에 복사하고
Orca에 force 없이 회수를 요청한다. 작업 상태 done과 환경 상태 cleaned는 별개다. 결과에 둘을 구분해 보고한다.
Git 공통 디렉터리의 운영 데이터는 자동 백업되지 않는다. 사용자가 정한 로컬 백업 대상에 포함한다.
