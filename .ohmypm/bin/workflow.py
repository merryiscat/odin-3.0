#!/usr/bin/env python3
"""ohmyPM 1.0 CLI. Standard library only; local state stays in Git's common dir."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from contextlib import nullcontext

from workflow_core import Workflow, WorkflowError


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", default=".", help="project checkout (main for merge)")
    parser.add_argument(
        "--orca", help="exact Orca executable; otherwise resolve session environment"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor")
    sub.add_parser("list")
    create = sub.add_parser("create")
    create.add_argument("--task", required=True)
    create.add_argument("--request-file", required=True)
    create.add_argument("--spec", required=True)
    create.add_argument("--manifest", required=True)
    create.add_argument("--main", required=True, help="main checkout path")
    create.add_argument("--main-address", default="", help="Orca run:<id> or main terminal handle")
    create.add_argument("--pl-address", default="", help="planning conversation location")
    for name in (
        "show",
        "events",
        "question",
        "revise",
        "approve",
        "schedule",
        "prepare",
        "exec",
        "check",
        "submit",
        "verdict",
        "merge",
        "cleanup",
        "retain",
        "launch",
        "deliver",
    ):
        p = sub.add_parser(name)
        p.add_argument("--task", required=True)
        if name in (
            "prepare",
            "exec",
            "check",
            "submit",
            "verdict",
            "merge",
            "cleanup",
            "retain",
            "launch",
        ):
            p.add_argument("--work", required=True)
        if name == "question":
            p.add_argument("--file", required=True)
        if name == "revise":
            p.add_argument("--spec", required=True)
            p.add_argument("--manifest", required=True)
        if name == "approve":
            p.add_argument("--revision", type=int, required=True)
            p.add_argument("--decision-file", required=True)
        if name == "exec":
            p.add_argument("argv", nargs=argparse.REMAINDER)
        if name == "submit":
            p.add_argument("--report", required=True)
        if name == "verdict":
            p.add_argument("--review", required=True)
        if name in ("cleanup", "retain"):
            p.add_argument(
                "--settlement",
                required=True,
                help="Orca accepted worker-release receipt, or terminal-close receipt",
            )
        if name == "deliver":
            p.add_argument("--event", required=True)
    args = vars(parser.parse_args())
    try:
        workflow = Workflow(args.pop("project"), orca_command=args.pop("orca"))
        command = args.pop("command")
        # Atomic state reads and isolated exec logs need no project-wide writer lock.
        # Independent work commands can run concurrently; state transitions/merges serialize.
        with (
            nullcontext()
            if command in ("exec", "show", "list", "events", "doctor")
            else workflow.lock()
        ):
            result = getattr(workflow, command.replace("exec", "execute"))(**args)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if command == "check" and any(not row["passed"] for row in result["results"].values()):
            return 1
    except (WorkflowError, OSError, ValueError, KeyError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    sys.exit(main())
