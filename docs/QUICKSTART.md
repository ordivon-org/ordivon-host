---
schema_version: 1
id: host.quickstart
title: Host Quick Start
type: guide
profile: engineering
lifecycle: active
source_role: canonical
visibility: public
owners:
  - ordivon-host
audience:
  - builder
  - operator
  - agent
updated: 2026-08-18
summary: Minimal path from a clean checkout to deterministic Host checks, a private Host state root, continuity operations, and direct owner revalidation.
evidence_status: verified
readiness: READY
applies_to:
  - ordivon-host
related:
  - host.start
  - host.status
  - host.operations
  - host.data-privacy
  - host.releases
---
# Host Quick Start

## Requirements

- Python 3.12;
- Linux for the canonical trusted-local operational path;
- SQLite supplied by Python;
- Git;
- the exact `ordivon-protocol` revision pinned in `pyproject.toml`;

Host is not an executor. Runtime is not a Host installation prerequisite; a consumer queries Runtime directly only when that consumer's workload needs physical execution truth.

## Install from the immutable dependency pin

```bash
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
python -m pip check
```

This path resolves `ordivon-protocol` from the exact Computing commit in `pyproject.toml`.

## Use a local sibling Protocol checkout

For repository development without reinstalling:

```bash
export PYTHONPATH="$PWD/src:/root/projects/ordivon-computing/packages/ordivon-protocol/src"
git -C /root/projects/ordivon-computing rev-parse HEAD
```

Record the sibling revision in test evidence. A local checkout is not a substitute for the immutable release dependency pin.

## Run portable checks

```bash
python3.12 -m compileall -q src tests scripts
python3.12 -m ruff check src tests scripts
python3.12 -W error::ResourceWarning -m unittest discover -s tests -v
python3.12 scripts/check_docs.py
python3.12 scripts/check_dependencies.py
scripts/local-acceptance check
python3.12 -m pip wheel --no-deps --wheel-dir /tmp/ordivon-host-wheel .
```

The deterministic suite verifies Journal/CAS integrity, migrations, leases, Task revisions, WorkingCheckpoint continuity and response-loss replay, bounded context-selection compatibility, recovery projection, backup/restore, extension-state durability, MCP behavior, and deployment contracts. It does not prove another owner's current state.

## Initialize a state root

```bash
install -d -m 0700 /var/lib/ordivon/host
ordivon-host --state-root /var/lib/ordivon/host init
ordivon-host --state-root /var/lib/ordivon/host inspect
ordivon-host --state-root /var/lib/ordivon/host doctor
```

Host creates `host.sqlite3`, immutable CAS objects, validation metadata, and migration backups inside the state root. Private modes are enforced and checked by Doctor.

## Observe external owners directly

Host does not configure, proxy, or health-check Ordivon Runtime. Runtime Workspace/Job identities that appear in a `WorkingCheckpoint` are navigation hints only and must be revalidated with Runtime's own MCP surface. Runtime credentials remain Runtime/operator configuration rather than Host configuration.

Use Host Doctor only for Host-owned state:

```bash
ordivon-host --state-root /var/lib/ordivon/host doctor
ordivon-host --state-root /var/lib/ordivon/host doctor --history
```

When physical execution state matters, query Ordivon Runtime directly with its native Tools such as `workspace.get`, `workspace.list`, `task.get`, or `task.list`.

## Operate and recover

```bash
ordivon-host --state-root /var/lib/ordivon/host task list
ordivon-host --state-root /var/lib/ordivon/host task handoff TASK_ID --expected-revision REVISION
ordivon-host --state-root /var/lib/ordivon/host doctor --history
```

Back up before migration or administrative repair. See [`OPERATIONS.md`](OPERATIONS.md) and [`DATA_AND_PRIVACY.md`](DATA_AND_PRIVACY.md).
