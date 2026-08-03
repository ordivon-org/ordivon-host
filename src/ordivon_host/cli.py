from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Sequence

from .config import HostConfig, load_config, read_token_file
from .domain import StaticRepositoryResolver, TaskState
from .handoff import operator_handoff
from .ops import (
    create_backup,
    doctor_state,
    inspect_state,
    list_tasks,
    plan_gc,
    restore_backup,
    verify_backup,
)
from .recovery import RecoveryResult, TaskReconciler, assess_recovery
from .runtime import McpRuntimeClient
from .storage import HostStorage


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ordivon-host")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--state-root", type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init")
    commands.add_parser("inspect")
    config = commands.add_parser("config")
    config.add_argument("action", choices=("show",))
    task = commands.add_parser("task")
    task_commands = task.add_subparsers(dest="task_command", required=True)
    task_list = task_commands.add_parser("list")
    task_list.add_argument("--state", choices=tuple(state.value for state in TaskState))
    task_list.add_argument("--limit", type=int, default=100)
    task_show = task_commands.add_parser("show")
    task_show.add_argument("task_id")
    task_handoff = task_commands.add_parser("handoff")
    task_handoff.add_argument("task_id")
    task_handoff.add_argument("--expected-revision", type=int)
    task_assess = task_commands.add_parser("assess")
    task_assess.add_argument("task_id")
    task_reconcile = task_commands.add_parser("reconcile")
    task_reconcile.add_argument("task_id")
    task_reconcile.add_argument("--wait-ms", type=int, default=30_000)
    doctor = commands.add_parser("doctor")
    doctor.add_argument("--runtime", action="store_true")
    doctor.add_argument("--history", action="store_true")
    backup = commands.add_parser("backup")
    backup.add_argument("destination", type=Path)
    restore = commands.add_parser("restore")
    restore.add_argument("backup", type=Path)
    restore.add_argument("--replace", action="store_true")
    verify = commands.add_parser("verify-backup")
    verify.add_argument("backup", type=Path)
    gc = commands.add_parser("gc")
    gc.add_argument("action", choices=("plan",))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        config = _config(args)
        result = _dispatch(config, args)
    except (FileNotFoundError, FileExistsError, KeyError, ValueError, RuntimeError) as error:
        print(
            json.dumps(
                {"ok": False, "error": type(error).__name__, "message": str(error)},
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1
    print(json.dumps(result, sort_keys=True, indent=2))
    if args.command == "doctor" and result.get("healthy") is not True:
        return 1
    return 0


def _config(args: argparse.Namespace) -> HostConfig:
    config = load_config(args.config)
    if args.state_root is None:
        return config
    return HostConfig(
        state_root=args.state_root,
        receipt_root=args.state_root / "receipts",
        runtime=config.runtime,
        providers=config.providers,
        repositories=config.repositories,
    )


def _dispatch(config: HostConfig, args: argparse.Namespace) -> dict[str, object]:
    if args.command == "init":
        with HostStorage(config.state_root):
            pass
        return inspect_state(config.state_root)
    if args.command == "inspect":
        return inspect_state(config.state_root)
    if args.command == "config":
        return config.to_dict()
    if args.command == "task":
        return _task(config, args)
    if args.command == "doctor":
        return doctor_state(
            config.state_root,
            config=config,
            check_runtime=args.runtime,
            check_history=args.history,
        )
    if args.command == "backup":
        return create_backup(config.state_root, args.destination)
    if args.command == "restore":
        return restore_backup(args.backup, config.state_root, replace=args.replace)
    if args.command == "verify-backup":
        return verify_backup(args.backup)
    if args.command == "gc":
        return plan_gc(config.state_root)
    raise ValueError("unsupported command")


def _task(config: HostConfig, args: argparse.Namespace) -> dict[str, object]:
    with HostStorage(config.state_root) as storage:
        if args.task_command == "list":
            state = TaskState(args.state) if args.state is not None else None
            tasks = list_tasks(storage, state=state, limit=args.limit)
            return {"tasks": [task.to_dict() for task in tasks]}
        if args.task_command == "show":
            task = storage.journal.get_task(args.task_id)
            if task is None:
                raise KeyError(f"unknown Task: {args.task_id}")
            snapshot = storage.read_task_event(args.task_id)
            return {
                "projection": task.to_dict(),
                "head": {
                    "eventKind": snapshot.event_kind.value,
                    "payloadDigest": snapshot.payload_digest,
                    "data": snapshot.data,
                },
            }
        if args.task_command == "handoff":
            capsule = operator_handoff(
                storage,
                args.task_id,
                expected_revision=args.expected_revision,
            )
            return {
                "capsule": capsule.to_dict(),
                "capsuleDigest": capsule.digest,
            }
        if args.task_command == "assess":
            return assess_recovery(storage, args.task_id).to_dict()
        if args.task_command == "reconcile":
            assessment = assess_recovery(storage, args.task_id)
            if not assessment.automatic:
                return RecoveryResult(assessment, assessment, False).to_dict()
            token = read_token_file(config.runtime.token_file)
            runtime = McpRuntimeClient(
                config.runtime.endpoint,
                token,
                timeout_seconds=config.runtime.timeout_seconds,
                max_response_bytes=config.runtime.max_response_bytes,
                client_version="0.1.0",
            )
            result = TaskReconciler(
                storage,
                runtime,
                clock_ms=_wall_clock_ms,
                repository_resolver=StaticRepositoryResolver(
                    dict(config.repositories)
                ),
            ).reconcile(args.task_id, wait_ms=args.wait_ms)
            return result.to_dict()
    raise ValueError("unsupported Task command")


def _wall_clock_ms() -> int:
    import time

    return time.time_ns() // 1_000_000


def entrypoint() -> None:
    raise SystemExit(main())
