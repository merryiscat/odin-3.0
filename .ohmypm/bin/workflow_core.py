"""Durable, revision-bound workflow mechanics. Agents supply decisions, not invented evidence.

Orca owns terminals/agent lifecycles. This module owns task contracts and Git integration.
No server, external Python packages, shell interpolation, or implicit push is required.
"""

from __future__ import annotations

import contextlib
import copy
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import uuid
from pathlib import Path


class WorkflowError(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise WorkflowError(message)


def digest(value):
    if not isinstance(value, bytes):
        value = json.dumps(value, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    with temp.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)


def command(argv, cwd=None, env=None, timeout=120):
    require(
        isinstance(argv, list) and bool(argv) and all(isinstance(a, str) for a in argv),
        "Commands must be nonempty argument arrays",
    )
    executable = shutil.which(argv[0], path=(env or os.environ).get("PATH"))
    require(executable is not None, f"Executable unavailable: {argv[0]}")
    # Windows cannot directly spawn .cmd/.bat without a shell. Only trusted CLI launchers
    # use that path; arguments are quoted by subprocess, never concatenated by this module.
    return subprocess.run(
        [executable, *argv[1:]],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def git(path, *args, check=True):
    result = command(["git", "-C", str(path), *args])
    if check and result.returncode:
        raise WorkflowError(result.stderr.strip() or result.stdout.strip())
    return result.stdout.strip() if check else result


def safe_id(value):
    require(
        isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", value),
        "IDs must be 1–80 ASCII letters, digits, underscores or hyphens",
    )
    return value


def relative(value):
    require(
        isinstance(value, str) and value and "\\" not in value and ":" not in value,
        f"Use a relative POSIX path: {value}",
    )
    require(
        not value.startswith("/") and ".." not in value.split("/"),
        f"Path escapes workspace: {value}",
    )
    require(not any(c in value for c in "*?[\n\r"), "Use path prefixes, not glob expressions")
    return value.rstrip("/")


def beneath(root, path):
    root, path = Path(root).resolve(), Path(path).resolve()
    require(path != root and path.is_relative_to(root), f"Path escapes its owner: {path}")
    return path


def overlaps(a, b):
    return a == "." or b == "." or a == b or a.startswith(b + "/") or b.startswith(a + "/")


class Orca:
    def __init__(self, executable=None):
        self.executable = (
            executable
            or os.environ.get("ORCA_CLI_COMMAND")
            or (
                "orca-dev"
                if os.environ.get("ORCA_DEV_REPO_ROOT")
                else "orca"
                if os.name == "nt" or os.environ.get("ORCA_TERMINAL_ID")
                else "orca-ide"
            )
        )

    def call(self, *args, cwd=None):
        result = command([self.executable, *args, "--json"], cwd=cwd, timeout=120)
        try:
            receipt = json.loads(result.stdout)
        except ValueError as exc:
            raise WorkflowError(f"Orca returned no JSON receipt: {result.stderr}") from exc
        require(
            result.returncode == 0 and receipt.get("ok") is not False,
            "Orca operation failed; inspect receipt before retry: " + json.dumps(receipt),
        )
        return receipt.get("result", receipt)

    def doctor(self):
        return self.call("status")

    @staticmethod
    def repository(project):
        # Orca's path: repo selector resolves the primary checkout, not a linked worktree.
        primary = git(project, "worktree", "list", "--porcelain", "-z").split("\0", 1)[0]
        require(primary.startswith("worktree "), "Cannot resolve primary repository")
        return primary[len("worktree ") :]

    def find(self, project, name):
        result = self.call(
            "worktree", "list", "--repo", "path:" + self.repository(project), cwd=project
        )
        require(
            isinstance(result, dict)
            and result.get("truncated") is False
            and not result.get("hostScope", {}).get("omittedHostIds"),
            "Worktree inventory is incomplete; inspect Orca before proceeding",
        )
        rows = result.get("worktrees", []) if isinstance(result, dict) else result
        return [row for row in rows if row.get("displayName") == name]

    def create(self, project, name, base):
        result = self.call(
            "worktree",
            "create",
            "--repo",
            "path:" + self.repository(project),
            "--name",
            name,
            "--base-branch",
            base,
            "--setup",
            "skip",
            cwd=project,
        )
        return result["worktree"]

    def remove(self, identity):
        return self.call("worktree", "rm", "--worktree", "id:" + identity)

    def terminals(self, identity):
        result = self.call("terminal", "list", "--worktree", "id:" + identity)
        require(
            isinstance(result, dict)
            and result.get("truncated") is False
            and not result.get("hostScope", {}).get("omittedHostIds"),
            "Terminal inventory is incomplete; preserve the worktree",
        )
        return result["terminals"] if isinstance(result, dict) else result

    def send(self, address, event):
        return self.call(
            "orchestration",
            "send",
            "--to",
            address,
            "--type",
            "status",
            "--subject",
            f"ohmyPM {event['task']} / {event['type']}",
            "--body",
            json.dumps(event, ensure_ascii=False),
        )

    def launch(self, path, spec, profile):
        args = [
            "orchestration",
            "worker-start",
            "--worktree",
            "path:" + str(path),
            "--spec",
            spec,
            "--agent",
            profile["agent"],
        ]
        if profile.get("model"):
            args.extend(["--model", profile["model"]])
        if profile.get("effort"):
            args.extend(["--effort", profile["effort"]])
        return self.call(*args)


class Workflow:
    def __init__(self, project, backend=None, orca_command=None):
        self.project = Path(git(project, "rev-parse", "--show-toplevel")).resolve()
        common = Path(git(self.project, "rev-parse", "--git-common-dir"))
        self.common = (self.project / common).resolve()
        self.root = self.common / "ohmypm-v1"
        self.backend = backend or Orca(orca_command)

    @contextlib.contextmanager
    def lock(self):
        self.root.mkdir(parents=True, exist_ok=True)
        with (self.root / "lock").open("a+b") as stream:
            stream.seek(0)
            stream.write(b"0")
            stream.flush()
            stream.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise WorkflowError(
                    "Another workflow command is active; retry after it completes"
                ) from exc
            try:
                yield
            finally:
                stream.seek(0)
                if os.name == "nt":
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(stream, fcntl.LOCK_UN)

    def location(self, task):
        return self.root / "tasks" / safe_id(task)

    def load(self, task):
        return read_json(self.location(task) / "state.json")

    def save(self, state):
        write_json(self.location(state["id"]) / "state.json", state)

    def event(self, state, kind, artifact):
        item = {
            "id": uuid.uuid4().hex,
            "task": state["id"],
            "revision": state["revision"],
            "type": kind,
            "artifact": str(artifact),
            "delivery": "pending",
        }
        state["events"].append(item)
        self.save(state)
        return item

    def doctor(self):
        return {
            "version": "1.0.0",
            "project": str(self.project),
            "state": str(self.root),
            "orca": self.backend.doctor(),
        }

    def list(self):
        return [read_json(p) for p in sorted((self.root / "tasks").glob("*/state.json"))]

    def show(self, task):
        return self.load(task)

    def events(self, task):
        return self.load(task)["events"]

    def contract(self, spec, manifest):
        text = Path(spec).read_text(encoding="utf-8-sig")
        require(text.strip(), "Spec is empty")
        data = read_json(manifest)
        criteria = data.get("criteria", [])
        require(0 < len(criteria) <= 8, "A contract requires 1–8 observable criteria")
        ids = [safe_id(c["id"]) for c in criteria]
        require(len(set(ids)) == len(ids), "Duplicate criterion ID")
        for c in criteria:
            require(
                c.get("description") and (c.get("command") or c.get("manual")),
                "Every criterion needs a description and command or manual scenario",
            )
            if c.get("command"):
                self.argv(c["command"])
        works = data.get("works", [])
        require(bool(works), "At least one work is required")
        work_ids = [safe_id(w["id"]) for w in works]
        require(len(set(work_ids)) == len(work_ids), "Duplicate work ID")
        covered = set()
        configuration = self.project / "docs/workflow.json"
        default_profile = (
            read_json(configuration)["roles"]["work"]
            if configuration.is_file()
            else {"agent": "claude"}
        )
        for w in works:
            w.setdefault("agent", copy.deepcopy(default_profile))
            require(
                isinstance(w["agent"], dict) and isinstance(w["agent"].get("agent"), str),
                "Work agent must be a launcher profile object",
            )
            require(w.get("paths"), "Each work needs owned path prefixes")
            w["paths"] = [relative(p) for p in w["paths"]]
            require(w.get("criteria") and set(w["criteria"]) <= set(ids), "Invalid work criteria")
            covered.update(w["criteria"])
            require(set(w.get("depends_on", [])) <= set(work_ids) - {w["id"]}, "Invalid dependency")
            if w.get("parallel_safe"):
                require(
                    w.get("independence_reason"), "Parallel work needs pl's independence rationale"
                )
            harness = w.setdefault("harness", {})
            for argv in harness.get("setup", []):
                self.argv(argv)
            for overlay in harness.get("files", []):
                target = relative(overlay["target"])
                require(
                    target in (".mcp.json", ".codex/config.toml", ".claude/settings.local.json")
                    or target.startswith((".agents/skills/", ".claude/skills/")),
                    "Harness files must be agent/MCP configuration or skill files",
                )
                source = Path(overlay["source"]).resolve()
                require(source.is_file(), f"Missing harness source: {source}")
                overlay["source"] = str(source)
                overlay["sha256"] = digest(source.read_bytes())
            for env_name in harness.get("inherit_env", []):
                require(
                    re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", env_name), "Invalid environment name"
                )
        require(covered == set(ids), "Every criterion must belong to a work")
        pending, done = {w["id"]: w for w in works}, set()
        while pending:
            ready = [k for k, w in pending.items() if set(w.get("depends_on", [])) <= done]
            require(ready, "Dependency cycle")
            for k in ready:
                done.add(k)
                del pending[k]
        return {"spec": text, "manifest": data}

    @staticmethod
    def argv(value):
        require(
            isinstance(value, list) and value and all(isinstance(x, str) and x for x in value),
            "Commands must be nonempty argument arrays; shell text is not accepted",
        )

    def snapshot(self, state, contract):
        revision = self.location(state["id"]) / f"r{state['revision']}"
        revision.mkdir(parents=True, exist_ok=True)
        (revision / "spec.md").write_text(contract["spec"], encoding="utf-8")
        write_json(revision / "manifest.json", contract["manifest"])
        state["contract"] = contract
        state["digest"] = digest(contract)
        state["spec_path"] = str(revision / "spec.md")
        state["works"] = {w["id"]: {"status": "pending"} for w in contract["manifest"]["works"]}
        state["approval"] = None
        state["status"] = "designing"
        self.save(state)

    def create(self, task, request_file, spec, manifest, main, main_address="", pl_address=""):
        safe_id(task)
        require(
            not (self.location(task) / "state.json").exists(),
            "Task already exists; use show/revise",
        )
        request = Path(request_file).read_text(encoding="utf-8-sig")
        require(request.strip(), "Original request is empty")
        main = Path(main).resolve()
        require(
            Path(git(main, "rev-parse", "--path-format=absolute", "--git-common-dir")).resolve()
            == self.common,
            "Main belongs to another repository",
        )
        branch = git(main, "symbolic-ref", "--short", "HEAD")
        state = {
            "id": task,
            "revision": 1,
            "request": request,
            "main": str(main),
            "branch": branch,
            "main_address": main_address,
            "pl_address": pl_address,
            "events": [],
            "history": [],
        }
        self.snapshot(state, self.contract(spec, manifest))
        self.event(state, "design_ready", state["spec_path"])
        return state

    def revise(self, task, spec, manifest):
        state = self.load(task)
        require(
            all(w["status"] in ("pending", "cleaned", "retained") for w in state["works"].values()),
            "Preserve/settle existing work before revising; use a new task for changed scope",
        )
        state["history"].append(
            copy.deepcopy(
                {k: state[k] for k in ("revision", "digest", "contract", "approval", "works")}
            )
        )
        state["revision"] += 1
        self.snapshot(state, self.contract(spec, manifest))
        self.event(state, "design_revised", state["spec_path"])
        return state

    def question(self, task, file):
        state = self.load(task)
        question = Path(file).read_text(encoding="utf-8-sig")
        require(question.strip(), "Question is empty")
        target = self.location(task) / "questions" / (uuid.uuid4().hex + ".md")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(question, encoding="utf-8")
        state["status"] = "needs_user"
        state["approval"] = None
        self.event(state, "needs_user", target)
        return {"question": str(target), "pl_address": state["pl_address"], "state": state}

    def approve(self, task, revision, decision_file):
        state = self.load(task)
        require(revision == state["revision"], "Approval names a stale revision")
        decision = Path(decision_file).read_text(encoding="utf-8-sig")
        require(decision.strip(), "Record the user's actual implementation decision")
        state["approval"] = {"revision": revision, "digest": state["digest"], "decision": decision}
        state["status"] = "approved"
        self.event(state, "approved", state["spec_path"])
        return state

    def approved(self, state):
        require(
            state.get("approval") and state["approval"]["digest"] == state["digest"],
            "User approval of this design revision is required",
        )
        require(digest(state["contract"]) == state["digest"], "Contract content changed")
        require(
            Path(state["spec_path"]).read_text(encoding="utf-8") == state["contract"]["spec"],
            "Approved spec snapshot changed; revise and re-approve",
        )
        require(
            read_json(Path(state["spec_path"]).with_name("manifest.json"))
            == state["contract"]["manifest"],
            "Approved manifest snapshot changed",
        )

    def definition(self, state, work):
        require(work in state["works"], "Unknown work ID")
        return next(w for w in state["contract"]["manifest"]["works"] if w["id"] == work)

    def compatible(self, a, b):
        return (
            a.get("parallel_safe") is True
            and b.get("parallel_safe") is True
            and not any(overlaps(x, y) for x in a["paths"] for y in b["paths"])
            and not set(a.get("resources", [])) & set(b.get("resources", []))
        )

    def schedule(self, task):
        state = self.load(task)
        self.approved(state)
        definitions = state["contract"]["manifest"]["works"]
        active = []
        for other in self.list():
            for d in other["contract"]["manifest"]["works"]:
                if other["works"][d["id"]]["status"] not in (
                    "pending",
                    "merged",
                    "cleaned",
                    "retained",
                ):
                    active.append(d)
        selected = []
        for d in definitions:
            if state["works"][d["id"]]["status"] != "pending":
                continue
            if not all(
                state["works"][dep]["status"] in ("merged", "cleaned")
                for dep in d.get("depends_on", [])
            ):
                continue
            if len(active) + len(selected) < 2 and all(
                self.compatible(d, other) for other in active + selected
            ):
                selected.append(d)
        return {"ready": [d["id"] for d in selected], "active": [d["id"] for d in active]}

    def main_head(self, state):
        require(
            git(state["main"], "symbolic-ref", "--short", "HEAD") == state["branch"],
            "Main checkout changed branch",
        )
        return git(state["main"], "rev-parse", "HEAD")

    def validate_worktree(self, state, item):
        path = Path(item["path"]).resolve()
        require(path != Path(state["main"]).resolve(), "Work cannot be the main checkout")
        require(
            Path(git(path, "rev-parse", "--path-format=absolute", "--git-common-dir")).resolve()
            == self.common,
            "Work belongs to another repository",
        )
        require(
            git(path, "symbolic-ref", "--short", "HEAD") == item["branch"], "Work branch changed"
        )
        return path

    def prepare(self, task, work):
        state = self.load(task)
        self.approved(state)
        definition = self.definition(state, work)
        item = state["works"][work]
        if item["status"] == "ready":
            self.isolated(self.validate_worktree(state, item))
            return item
        require(
            item["status"] in ("pending", "creating", "preparing", "preparation_failed"),
            "Work already started; do not create a duplicate",
        )
        if item["status"] == "pending":
            require(
                work in self.schedule(task)["ready"],
                "Dependencies or concurrent ownership block work",
            )
            name = f"{task}-r{state['revision']}-{work}"
            existing = self.backend.find(state["main"], name)
            require(
                not existing, "Worktree name already exists; preserve it and choose another task ID"
            )
            item.update(status="creating", name=name, base=self.main_head(state))
            self.save(state)  # Durable intent BEFORE Orca mutation.
            try:
                row = self.backend.create(state["main"], item["name"], item["base"])
            except Exception:
                self.save(state)  # Next prepare reconciles the exact name, never blindly creates.
                raise
        elif item["status"] == "creating":
            existing = self.backend.find(state["main"], item["name"])
            require(len(existing) == 1, "Creation outcome unknown; inspect Orca before any retry")
            row = existing[0]
        else:
            row = None
        if row:
            item.update(
                path=row["path"],
                orca_id=row["id"],
                branch=git(row["path"], "symbolic-ref", "--short", "HEAD"),
                status="preparing",
            )
            require(
                git(row["path"], "rev-parse", "HEAD") == item["base"], "Unexpected worktree base"
            )
            self.save(state)
        path = self.validate_worktree(state, item)
        try:
            if "copied_files" not in item:
                item["copied_files"] = self.ignored_files(path)
                self.save(state)
            self.prepare_harness(state, work, path, definition["harness"])
        except Exception as exc:
            item["status"], item["error"] = "preparation_failed", str(exc)
            self.save(state)
            raise
        item["status"] = "ready"
        item.pop("error", None)
        self.event(state, "work_ready", path / ".ohmypm-work" / "context.md")
        return item

    def prepare_harness(self, state, work, path, harness):
        item = state["works"][work]
        item["inherit_env"] = harness.get("inherit_env", [])
        self.isolated(path)
        private = beneath(path, path / ".ohmypm-work")
        private.mkdir(exist_ok=True)
        exclude = self.common / "info" / "exclude"
        exclude.parent.mkdir(exist_ok=True)
        existing = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
        if "/.ohmypm-work/" not in existing.splitlines():
            exclude.write_text(existing.rstrip() + "\n/.ohmypm-work/\n", encoding="utf-8")
        for tool in harness.get("tools", []):
            require(shutil.which(tool), f"Required tool missing: {tool}")
        for key in harness.get("inherit_env", []):
            require(key in os.environ, f"Required environment variable missing: {key}")
        item.setdefault("ports", {})
        for key in harness.get("ports", []):
            require(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key), "Invalid port environment name")
            if key not in item["ports"]:
                reserved = {
                    port
                    for t in self.list()
                    for w in t["works"].values()
                    for port in w.get("ports", {}).values()
                    if w["status"] != "cleaned"
                }
                reserved.update(item["ports"].values())
                for _ in range(30):
                    with socket.socket() as sock:
                        sock.bind(("127.0.0.1", 0))
                        port = sock.getsockname()[1]
                    if port not in reserved:
                        item["ports"][key] = port
                        break
                else:
                    raise WorkflowError("Cannot allocate distinct local ports")
            else:
                with socket.socket() as sock:
                    sock.bind(("127.0.0.1", item["ports"][key]))
        item.setdefault("overlays", {})
        for overlay in harness.get("files", []):
            source, target = Path(overlay["source"]), beneath(path, path / overlay["target"])
            require(
                digest(source.read_bytes()) == overlay["sha256"],
                "Harness source changed since approval",
            )
            key = overlay["target"]
            if key not in item["overlays"]:
                backup = (
                    self.location(state["id"]) / f"r{state['revision']}" / work / "backups" / key
                )
                backup.parent.mkdir(parents=True, exist_ok=True)
                if target.exists():
                    backup.write_bytes(target.read_bytes())
                item["overlays"][key] = {
                    "backup": str(backup) if target.exists() else None,
                    "digest": overlay["sha256"],
                }
                self.save(state)
            else:
                current = digest(target.read_bytes()) if target.exists() else None
                backup = item["overlays"][key]["backup"]
                before = digest(Path(backup).read_bytes()) if backup else None
                require(
                    current in (overlay["sha256"], before),
                    "Harness overlay was modified; preserve it",
                )
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        self.save(state)
        # A killed setup command has unknown side effects; do not automatically run it twice.
        for index, argv in enumerate(harness.get("setup", [])):
            key = str(index)
            setup = item.setdefault("setup", {})
            if setup.get(key) == "passed":
                continue
            require(
                key not in setup,
                "Setup outcome failed/unknown; inspect environment before recovery",
            )
            setup[key] = "running"
            self.save(state)
            result = command(argv, cwd=path, env=self.environment(item), timeout=600)
            self.log(state, work, f"setup-{index}", result)
            setup[key] = "passed" if result.returncode == 0 else "failed"
            self.save(state)
            require(result.returncode == 0, f"Setup command {index} failed; see local evidence")
        context = (
            f"# {state['id']} / {work} / revision {state['revision']}\n\n"
            f"Original request:\n{state['request']}\n\nApproved spec: {state['spec_path']}\n"
            f"Contract digest: {state['digest']}\nOwned paths: "
            + ", ".join(self.definition(state, work)["paths"])
            + "\n\nImplement only this work. Preserve acceptance criteria. "
            "Use workflow exec for setup, tools and tests so the task environment is applied. "
            "Commit implementation changes normally; never stage harness overlays. "
            "Submit evidence, then follow the Orca dispatch lifecycle preamble.\n"
        )
        (private / "context.md").write_text(context, encoding="utf-8")
        write_json(private / "harness.json", harness)

    @staticmethod
    def isolated(path):
        for directory in (
            ".venv",
            "node_modules",
            ".ohmypm-work",
            ".ohmypm-work/tmp",
            ".ohmypm-work/data",
            ".ohmypm-work/pycache",
        ):
            candidate = path / directory
            require(
                not candidate.is_symlink() and candidate.resolve().is_relative_to(path),
                f"Shared dependency directory: {candidate}; configure isolated worktrees first",
            )
            if hasattr(candidate, "is_junction"):
                require(not candidate.is_junction(), f"Shared junction: {candidate}")

    def environment(self, item):
        system_names = {
            "PATH",
            "PATHEXT",
            "SYSTEMROOT",
            "WINDIR",
            "COMSPEC",
            "HOME",
            "USERPROFILE",
            "APPDATA",
            "LOCALAPPDATA",
            "PROGRAMDATA",
            "PROGRAMFILES",
            "PROGRAMFILES(X86)",
            "LANG",
            "LC_ALL",
            "USER",
            "USERNAME",
            "NUMBER_OF_PROCESSORS",
        }
        env = {k: v for k, v in os.environ.items() if k.upper() in system_names}
        env.update(
            {key: os.environ[key] for key in item.get("inherit_env", []) if key in os.environ}
        )
        path = Path(item["path"])
        self.isolated(path)
        private = path / ".ohmypm-work"
        for name in ("tmp", "data", "pycache"):
            (private / name).mkdir(parents=True, exist_ok=True)
        env.update(
            {
                "VIRTUAL_ENV": str(path / ".venv"),
                "UV_PROJECT_ENVIRONMENT": str(path / ".venv"),
                "PYTHONPYCACHEPREFIX": str(private / "pycache"),
                "PYTHONNOUSERSITE": "1",
                "OHMYPM_DATA_DIR": str(private / "data"),
                "TMPDIR": str(private / "tmp"),
                "TEMP": str(private / "tmp"),
                "TMP": str(private / "tmp"),
            }
        )
        env.pop("PYTHONPATH", None)
        env["PATH"] = os.pathsep.join(
            [
                str(path / ".venv" / ("Scripts" if os.name == "nt" else "bin")),
                str(path / "node_modules" / ".bin"),
                env.get("PATH", ""),
            ]
        )
        env.update({key: str(port) for key, port in item.get("ports", {}).items()})
        return env

    def log(self, state, work, name, result):
        target = (
            self.location(state["id"])
            / f"r{state['revision']}"
            / work
            / "evidence"
            / (name + "-" + uuid.uuid4().hex + ".json")
        )
        write_json(
            target,
            {"returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr},
        )
        return str(target)

    def execute(self, task, work, argv):
        state = self.load(task)
        self.approved(state)
        item = state["works"][work]
        require(
            item["status"] in ("ready", "running", "submitted", "failed", "verified"),
            "Work is not executable",
        )
        path = self.validate_worktree(state, item)
        if argv and argv[0] == "--":
            argv = argv[1:]
        self.argv(argv)
        result = command(argv, cwd=path, env=self.environment(item), timeout=600)
        evidence = self.log(state, work, "exec-" + uuid.uuid4().hex, result)
        require(result.returncode == 0, f"Command failed: {evidence}")
        return {
            "returncode": result.returncode,
            "evidence": evidence,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }

    def launch(self, task, work):
        state = self.load(task)
        self.approved(state)
        item = state["works"][work]
        if item["status"] == "running":
            return item  # Delivery replay never launches a second editor.
        require(
            item["status"] == "ready",
            "Launch already attempted or work is not ready; inspect Orca lifecycle",
        )
        path = self.validate_worktree(state, item)
        definition = self.definition(state, work)
        self.isolated(path)
        self.clean(path, item)
        profile = definition.get("agent", {"agent": "claude"})
        spec = (
            (path / ".ohmypm-work" / "context.md").read_text(encoding="utf-8")
            + f"\nRuntime CLI: {Path(__file__).with_name('workflow.py').resolve()}\n"
            + "Use the task contract and workflow exec/submit. Report completion with your "
            "injected Orca lifecycle arguments; main coordinates, pl judges quality.\n"
        )
        item["status"] = "starting"
        self.save(state)
        item["launch_receipt"] = self.backend.launch(path, spec, profile)
        item["status"] = "running"
        self.event(state, "work_started", path / ".ohmypm-work" / "context.md")
        return item

    def clean(self, path, item=None, restoring=False):
        # -z avoids Git's quoting of non-ASCII paths and rename ambiguity.
        result = command(
            ["git", "-C", str(path), "status", "--porcelain=v1", "-z", "--untracked-files=all"]
        )
        require(result.returncode == 0, "Cannot inspect worktree changes: " + result.stderr)
        raw = result.stdout
        parts = raw.split("\0")
        overlays = (item or {}).get("overlays", {})
        for name, overlay in overlays.items():
            target = beneath(path, path / name)
            allowed = [overlay["digest"]]
            if restoring:
                allowed.append(
                    digest(Path(overlay["backup"]).read_bytes()) if overlay["backup"] else None
                )
            require(
                (digest(target.read_bytes()) if target.is_file() else None) in allowed,
                "Prepared harness configuration changed",
            )
        for entry in parts:
            if not entry:
                continue
            name = entry[3:]
            if name in overlays:
                require(entry[:2] in ("??", " M", " D"), "Harness overlay must not be staged")
                target = path / name
                allowed = [overlays[name]["digest"]]
                if restoring:
                    backup = overlays[name]["backup"]
                    allowed.append(digest(Path(backup).read_bytes()) if backup else None)
                require(
                    (digest(target.read_bytes()) if target.is_file() else None) in allowed,
                    "Prepared harness configuration changed",
                )
                continue
            raise WorkflowError(f"Uncommitted changes must be preserved: {entry}")

    def candidate(self, state, work):
        item = state["works"][work]
        path = self.validate_worktree(state, item)
        self.clean(path, item)
        head, base = git(path, "rev-parse", "HEAD"), self.main_head(state)
        require(
            git(path, "merge-base", "--is-ancestor", base, head, check=False).returncode == 0,
            "Main advanced: integrate it in work, then rerun checks and pl review",
        )
        changed = git(path, "diff", "--name-only", "--no-renames", "-z", base, head).split("\0")
        owned = self.definition(state, work)["paths"]
        for name in filter(None, changed):
            require(
                any(p == "." or name == p or name.startswith(p + "/") for p in owned),
                f"Change outside owned paths: {name}",
            )
            require(
                name not in item.get("overlays", {}),
                f"Harness overlay must not enter code commit: {name}",
            )
        return {
            "commit": head,
            "base": base,
            "revision": state["revision"],
            "digest": state["digest"],
        }

    def check(self, task, work):
        state = self.load(task)
        self.approved(state)
        item = state["works"][work]
        require(
            item["status"] in ("ready", "running", "submitted", "failed", "verified", "checking"),
            "Work is not ready",
        )
        candidate = self.candidate(state, work)
        item.pop("verdict", None)
        item.pop("checks", None)
        item["status"] = "checking"
        self.save(state)
        results = {}
        for criterion in state["contract"]["manifest"]["criteria"]:
            if criterion["id"] not in self.definition(state, work)["criteria"] or not criterion.get(
                "command"
            ):
                continue
            result = command(
                criterion["command"], cwd=item["path"], env=self.environment(item), timeout=600
            )
            results[criterion["id"]] = {
                "passed": result.returncode == 0,
                "evidence": self.log(state, work, criterion["id"], result),
            }
        require(
            self.candidate(state, work) == candidate,
            "Checks changed the candidate; recommit and rerun",
        )
        item["checks"] = {"candidate": candidate, "results": results}
        item.pop("verdict", None)
        item["status"] = "submitted" if all(r["passed"] for r in results.values()) else "failed"
        self.save(state)
        return item["checks"]

    def submit(self, task, work, report):
        state = self.load(task)
        self.approved(state)
        item = state["works"][work]
        require(
            item["status"] in ("ready", "running", "submitted", "failed", "verified"),
            "Work is not ready",
        )
        candidate = self.candidate(state, work)
        text = Path(report).read_text(encoding="utf-8-sig")
        require(text.strip(), "Implementation evidence is empty")
        target = (
            self.location(task)
            / f"r{state['revision']}"
            / work
            / ("report-" + uuid.uuid4().hex + ".md")
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        item.update(status="submitted", submission=candidate, report=str(target))
        item.pop("verdict", None)
        self.event(state, "review_requested", target)
        return item

    def verdict(self, task, work, review):
        state = self.load(task)
        self.approved(state)
        item = state["works"][work]
        require(item["status"] in ("submitted", "failed"), "Submit implementation evidence first")
        candidate = self.candidate(state, work)
        require(
            item.get("submission") == candidate, "Submission is stale; submit current candidate"
        )
        findings = read_json(review)
        require(findings.get("candidate") == candidate, "pl review names another candidate")
        require(
            findings.get("reviewer") == "pl" and findings.get("code_review"),
            "pl code review is required",
        )
        required = set(self.definition(state, work)["criteria"])
        require(
            set(findings.get("criteria", {})) == required,
            "Review must cover every assigned criterion",
        )
        passed = True
        for key, row in findings["criteria"].items():
            require(
                row.get("status") in ("pass", "fail") and row.get("evidence"),
                "Missing review evidence",
            )
            passed = passed and row["status"] == "pass"
        automatic = {
            c["id"]
            for c in state["contract"]["manifest"]["criteria"]
            if c["id"] in required and c.get("command")
        }
        if passed and automatic:
            checks = item.get("checks", {})
            require(
                checks.get("candidate") == candidate
                and automatic <= set(checks.get("results", {}))
                and all(checks["results"][k]["passed"] for k in automatic),
                "Automated checks are missing, stale or failed",
            )
        target = (
            self.location(task)
            / f"r{state['revision']}"
            / work
            / ("pl-review-" + uuid.uuid4().hex + ".json")
        )
        write_json(target, findings)
        item.update(
            status="verified" if passed else "failed",
            verdict={
                "candidate": candidate,
                "passed": passed,
                "review": str(target),
                "review_digest": digest(findings),
            },
        )
        self.event(state, "quality_passed" if passed else "quality_failed", target)
        return item

    def merge(self, task, work):
        state = self.load(task)
        self.approved(state)
        item = state["works"][work]
        require(
            self.project == Path(state["main"]).resolve(),
            "Merge must run from the registered main checkout",
        )
        if item["status"] in ("merged", "cleaned"):
            require(
                git(
                    state["main"],
                    "merge-base",
                    "--is-ancestor",
                    item["merged_commit"],
                    "HEAD",
                    check=False,
                ).returncode
                == 0,
                "Recorded merge is no longer present in main",
            )
            return item
        if item["status"] == "merging":
            if self.main_head(state) == item["verdict"]["candidate"]["base"]:
                # The fast-forward did not take effect. Normal guards recheck before replay.
                item["status"] = "verified"
                self.save(state)
                return self.merge(task, work)
            require(
                self.main_head(state) == item["verdict"]["candidate"]["commit"],
                "Merge outcome unknown; inspect main without replaying",
            )
        else:
            require(item["status"] == "verified", "pl has not passed this work")
            candidate = self.candidate(state, work)
            require(
                item["verdict"]["passed"] and item["verdict"]["candidate"] == candidate,
                "Quality verdict is stale",
            )
            require(
                digest(read_json(item["verdict"]["review"])) == item["verdict"]["review_digest"],
                "Review evidence changed",
            )
            self.clean(Path(state["main"]))
            item["status"] = "merging"
            self.save(state)
            # No conflicts can be silently resolved and no unreviewed merge commit is introduced.
            git(state["main"], "merge", "--ff-only", candidate["commit"])
        item.update(status="merged", merged_commit=self.main_head(state))
        if all(w["status"] in ("merged", "cleaned") for w in state["works"].values()):
            state["status"] = "done"
        self.event(state, "merged", item["verdict"]["review"])
        return item

    def cleanup(self, task, work, settlement):
        state = self.load(task)
        item = state["works"][work]
        require(item["status"] in ("merged", "cleaning", "cleaned"), "Keep unmerged/failed work")
        if item["status"] == "cleaned":
            return item
        require(
            git(
                state["main"],
                "merge-base",
                "--is-ancestor",
                item["merged_commit"],
                "HEAD",
                check=False,
            ).returncode
            == 0,
            "Main no longer contains the work; preserve it",
        )
        require(
            Path(item["report"]).is_file() and Path(item["verdict"]["review"]).is_file(),
            "Preserve work until report and review are retained",
        )
        receipt = read_json(settlement)
        require(receipt.get("ok") is True, "Cleanup needs a successful Orca settlement receipt")
        # Lifecycle authority remains with Orca. The command never kills an agent on a timer.
        target = self.location(task) / f"r{state['revision']}" / work / "settlement.json"
        write_json(target, receipt)
        if item["status"] == "cleaning" and not Path(item["path"]).exists():
            require(
                not self.backend.find(state["main"], item["name"]), "Orca still owns the worktree"
            )
        else:
            require(
                not self.backend.terminals(item["orca_id"]),
                "Worktree still has terminals; settle workers and preserve unrelated sessions",
            )
            path = self.validate_worktree(state, item)
            self.isolated(path)
            self.clean(path, item, restoring=item["status"] == "cleaning")
            require(
                git(path, "rev-parse", "HEAD") == item["merged_commit"],
                "New work appeared; preserve it",
            )
            for name, value in self.ignored_files(path).items():
                require(
                    name in item.get("overlays", {})
                    or item.get("copied_files", {}).get(name) == value,
                    f"Unowned ignored file must be preserved before cleanup: {name}",
                )
            archive = self.location(task) / f"r{state['revision']}" / work / "environment"
            if (path / ".ohmypm-work").exists():
                shutil.copytree(path / ".ohmypm-work", archive, dirs_exist_ok=True, symlinks=True)
            item["status"] = "cleaning"
            self.save(state)
            for name, overlay in item.get("overlays", {}).items():
                target_file = beneath(path, path / name)
                if overlay["backup"]:
                    shutil.copyfile(overlay["backup"], target_file)
                else:
                    target_file.unlink(missing_ok=True)
            self.backend.remove(item["orca_id"])  # No --force; Orca rechecks dirty/unmerged state.
        item["status"] = "cleaned"
        self.event(state, "environment_reclaimed", item["report"])
        return item

    @staticmethod
    def ignored_files(path):
        files = {}
        raw = git(path, "ls-files", "--others", "--ignored", "--exclude-standard", "-z")
        for name in filter(None, raw.split("\0")):
            if name.startswith((".ohmypm-work/", ".venv/", "node_modules/")):
                continue
            target = beneath(path, path / name)
            require(
                target.is_file() and not (path / name).is_symlink(),
                f"Ignored link cannot be reclaimed automatically: {name}",
            )
            files[name] = digest(target.read_bytes())
        return files

    def retain(self, task, work, settlement):
        state = self.load(task)
        item = state["works"][work]
        require(
            item["status"] not in ("merged", "cleaned", "merging", "cleaning"),
            "Integrated work should use cleanup",
        )
        require(
            read_json(settlement).get("ok") is True and not self.backend.terminals(item["orca_id"]),
            "Settle the worker and close its owned terminals before retaining this revision",
        )
        self.validate_worktree(state, item)
        item["status"] = "retained"
        self.event(state, "revision_retained", item["path"])
        return item

    def deliver(self, task, event):
        state = self.load(task)
        matches = [e for e in state["events"] if e["id"] == event]
        require(len(matches) == 1, "Unknown event")
        item = matches[0]
        if item["delivery"] == "sent":
            return item
        require(
            item["delivery"] == "pending",
            "Delivery outcome unknown; inspect Orca receipt before retry",
        )
        require(state["main_address"], "Set the main inbox address at task creation")
        item["delivery"] = "sending"
        self.save(state)
        receipt = self.backend.send(state["main_address"], item)
        item.update(delivery="sent", receipt=receipt)
        self.save(state)
        return item
