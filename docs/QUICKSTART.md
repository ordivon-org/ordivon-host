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
updated: 2026-08-04
summary: Minimal path from a clean checkout to deterministic checks, a private Host state root, Runtime health, and read-only live acceptance.
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
- a reachable Ordivon Runtime for live journeys.

Host is not a standalone executor. Portable tests can use fake Runtime clients; live Task progress requires Runtime.

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

The deterministic suite verifies Journal/CAS integrity, migrations, leases, Task revisions, Context and decision admission, Runtime catalog binding, modern and legacy transport, recovery, backup/restore, extension events, and the proven workload slices. It does not prove a live Runtime path.

## Initialize a state root

```bash
install -d -m 0700 /var/lib/ordivon/host
ordivon-host --state-root /var/lib/ordivon/host init
ordivon-host --state-root /var/lib/ordivon/host inspect
ordivon-host --state-root /var/lib/ordivon/host doctor
```

Host creates `host.sqlite3`, immutable CAS objects, validation metadata, and migration backups inside the state root. Private modes are enforced and checked by Doctor.

## Configure Runtime access

Create `/etc/ordivon/host.toml` and a private Runtime token file as described in [`OPERATIONS.md`](OPERATIONS.md). The token file must be regular, non-symlink, and inaccessible to group and other users.

The default client uses:

```text
MCP 2026-07-28
server/discover
stateless per-request metadata
Mcp-Method and Mcp-Name identity
```

The legacy `2025-06-18` Session profile remains available only when explicitly selected by code.

Verify connectivity:

```bash
ordivon-host --config /etc/ordivon/host.toml \
  --state-root /var/lib/ordivon/host doctor --runtime
```

## Run read-only live acceptance

```bash
ORDIVON_BEARER_TOKEN_FILE=/etc/ordivon/runtime-mcp.token \
ORDIVON_MCP_ENDPOINT=http://127.0.0.1:8897/mcp \
  scripts/local-acceptance run
```

The live path:

1. binds the exact Host source revision;
2. creates a disposable Runtime Workspace;
3. persists a Host read Task;
4. discovers the modern Runtime lifecycle and Tool catalog;
5. reads `README.md`;
6. independently verifies content and digest;
7. closes the Runtime Workspace;
8. emits a JSON receipt with integrity metadata.

It does not invoke a Provider, modify the source repository, or claim general mutation recovery.

## Operate and recover

```bash
ordivon-host --state-root /var/lib/ordivon/host task list
ordivon-host --state-root /var/lib/ordivon/host task handoff TASK_ID --expected-revision REVISION
ordivon-host --state-root /var/lib/ordivon/host task assess TASK_ID
ordivon-host --state-root /var/lib/ordivon/host task reconcile TASK_ID
ordivon-host --state-root /var/lib/ordivon/host doctor --history
```

`task reconcile` performs at most one conservative step and never creates a new Effect or invokes a Provider. Back up before migration or administrative repair. See [`OPERATIONS.md`](OPERATIONS.md) and [`DATA_AND_PRIVACY.md`](DATA_AND_PRIVACY.md).
