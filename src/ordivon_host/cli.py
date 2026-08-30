from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Sequence

from .config import HostConfig, load_config
from .continuity import ExternalContinuityHost
from .continuity_lens import build_continuity_lens
from .continuity_models import WorkingCheckpoint
from .domain import TaskState
from .handoff import operator_handoff
from .ops import (
    DEFAULT_HOST_RELEASE_ROOT,
    create_backup,
    doctor_state,
    inspect_deployment,
    inspect_state,
    list_tasks,
    plan_gc,
    restore_backup,
    verify_backup,
)
from .storage import HostStorage


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ordivon-host")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--state-root", type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init")
    commands.add_parser("inspect")
    deployment = commands.add_parser("deployment")
    deployment.add_argument("--release-root", type=Path, default=DEFAULT_HOST_RELEASE_ROOT)
    config = commands.add_parser("config")
    config.add_argument("action", choices=("show",))
    task = commands.add_parser("task")
    task_commands = task.add_subparsers(dest="task_command", required=True)
    task_list = task_commands.add_parser("list")
    task_list.add_argument("--state", choices=tuple(state.value for state in TaskState))
    task_list.add_argument("--limit", type=int, default=100)
    task_lens = task_commands.add_parser("lens")
    task_lens.add_argument("--goal-id")
    task_lens.add_argument("--attention")
    task_lens.add_argument(
        "--carrier",
        choices=("CLOSE_CLEAN", "RETAIN", "DIRTY_HANDOFF", "UNSPECIFIED"),
    )
    task_lens.add_argument("--limit", type=int, default=20)
    task_lens.add_argument("--summary-only", action="store_true")
    task_show = task_commands.add_parser("show")
    task_show.add_argument("task_id")
    task_handoff = task_commands.add_parser("handoff")
    task_handoff.add_argument("task_id")
    task_handoff.add_argument("--expected-revision", type=int)
    task_adopt = task_commands.add_parser("adopt")
    task_adopt.add_argument("task_id")
    task_adopt.add_argument("goal_id")
    task_adopt.add_argument("--checkpoint-file", type=Path, required=True)
    task_resume = task_commands.add_parser("resume")
    task_resume.add_argument("task_id")
    task_resume.add_argument("--expected-revision", type=int)
    task_checkpoint = task_commands.add_parser("checkpoint")
    task_checkpoint.add_argument("task_id")
    task_checkpoint.add_argument("--expected-revision", type=int, required=True)
    task_checkpoint.add_argument("--checkpoint-file", type=Path, required=True)
    doctor = commands.add_parser("doctor")
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
        repositories=config.repositories,
    )


def _dispatch(config: HostConfig, args: argparse.Namespace) -> dict[str, object]:
    if args.command == "init":
        with HostStorage(config.state_root):
            pass
        return inspect_state(config.state_root)
    if args.command == "inspect":
        return inspect_state(config.state_root)
    if args.command == "deployment":
        return inspect_deployment(args.release_root)
    if args.command == "config":
        return config.to_dict()
    if args.command == "task":
        return _task(config, args)
    if args.command == "doctor":
        return doctor_state(
            config.state_root,
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
    observation_only = args.task_command in {
        "list",
        "lens",
        "show",
        "handoff",
        "resume",
    }
    with HostStorage(
        config.state_root, update_validation_cache=not observation_only
    ) as storage:
        if args.task_command == "list":
            state = TaskState(args.state) if args.state is not None else None
            tasks = list_tasks(storage, state=state, limit=args.limit)
            return {"tasks": [task.to_dict() for task in tasks]}
        if args.task_command == "lens":
            return build_continuity_lens(
                storage,
                goal_id=args.goal_id,
                attention=args.attention,
                carrier=args.carrier,
                item_limit=0 if args.summary_only else args.limit,
            )
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
        if args.task_command == "adopt":
            return ExternalContinuityHost(
                storage, clock_ms=_wall_clock_ms
            ).adopt(
                task_id=args.task_id,
                goal_id=args.goal_id,
                initial_checkpoint=_working_checkpoint(args.checkpoint_file),
            ).to_dict()
        if args.task_command == "resume":
            return ExternalContinuityHost(
                storage, clock_ms=_wall_clock_ms
            ).resume(
                args.task_id, expected_revision=args.expected_revision
            ).to_dict()
        if args.task_command == "checkpoint":
            return ExternalContinuityHost(
                storage, clock_ms=_wall_clock_ms
            ).checkpoint(
                task_id=args.task_id,
                expected_revision=args.expected_revision,
                checkpoint=_working_checkpoint(args.checkpoint_file),
            ).to_dict()
    raise ValueError("unsupported Task command")


def _working_checkpoint(path: Path) -> WorkingCheckpoint:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("WorkingCheckpoint file must contain one JSON object")
    return WorkingCheckpoint.from_dict(value)


def _wall_clock_ms() -> int:
    import time

    return time.time_ns() // 1_000_000


def entrypoint() -> None:
    raise SystemExit(main())
