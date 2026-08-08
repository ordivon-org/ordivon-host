---
schema_version: 1
id: host.start
title: Ordivon Host
type: start
profile: organization
lifecycle: active
source_role: canonical
visibility: public
owners:
  - ordivon-host
audience:
  - user
  - builder
  - operator
  - agent
updated: 2026-08-08
summary: Canonical entry to Host Task continuity, commitment, uncertainty, evidence admission, verification, recovery, and extension boundaries.
evidence_status: verified
readiness: READY
applies_to:
  - ordivon-host
related:
  - host.quickstart
  - host.status
  - host.architecture
  - host.operations
  - host.data-privacy
  - host.releases
  - host.authority
---
# Ordivon Host

Ordivon Host is the persistent coordination and commitment plane of Ordivon. It preserves durable work while model sessions and Runtime processes remain replaceable.

```text
participant or application intent
→ durable Task and bounded Context
→ proposal, decision, or exact candidate admission
→ durable Effect and Dispatch commitment
→ Runtime physical execution or domain action
→ Observation and independent Verification
→ terminal TaskOutcome or explicit UNKNOWN fence
```

## Purpose

Host owns durable Tasks, Goal-scoped Task coordination, Journal/CAS state, Contexts, proposal compilation or admission, Effect commitments, Runtime Dispatch identities, participant-routed DecisionRequests, verification receipts, conservative recovery, and Task outcomes.

Host does **not** own model intelligence, Provider invocation or process continuity, physical process supervision, Workspace or Job truth, domain-world truth, or a permanent hierarchy among participants. Current cognition APIs persist a semantic `CognitionWorkRequest` containing the exact Context and requested result kind, then admit externally produced action selections or proposals with provider-neutral provenance. Host never models or executes a Provider call.

## Responsibility boundary

| Concern | Canonical owner | Not owned here |
| --- | --- | --- |
| durable Task continuity, Journal/CAS, commitments, uncertainty, referenced evidence, verification admission, Task outcomes | `ordivon-host` | physical execution, Agent Run loops, domain truth |
| Workspace, Job, Attempt, process tree, Artifact, physical cancellation and recovery | `ordivon-runtime` | Task meaning or semantic completion |
| caller-neutral Agent Run, Provider adapter, model–Tool loop, Tool-step checkpoint and Run recovery | `ordivon-harness` | caller Task authority, another Task database, or Runtime supervision |
| authoritative world state and domain verification | Game, Security, World, or another domain owner | generic Host inference |
| promoted cross-repository contracts | `ordivon-computing` | Host-local experiments before repeated proof |

## Status

Host is operational for owner-trusted local engineering work and pre-1.0 as a public interface. The independently versioned repository was extracted with history from the Computing incubator after the core architecture, persistence, recovery, and Runtime slices were proven.

See [`docs/STATUS.md`](docs/STATUS.md) for the exact support claim and known limits.

Current proven slices include:

- immutable TaskDescriptor identity, revisioned Task projections, exact lease fencing, and irreversible terminal state;
- schema-v4 SQLite Journal/CAS state, migrations with backups, private modes, backup/restore, Doctor, and optional full-history verification;
- deterministic Runtime repository read with independent digest verification;
- guarded mutation with durable Dispatch identity, explicit UNKNOWN, original-Job reconciliation, and no blind redispatch;
- version-bound two-file source change with structured Runtime checks, exact diff evidence, and compare-and-close;
- closed-choice action selection and open ActionProposal admission with one durable semantic cognition request, provider-neutral execution evidence, and Host-owned lowering or structured rejection;
- participant-aware DecisionRequest lifecycle for shared, foreign-owned, irreversible, or uncertain consequences;
- Goal-scoped Task revision snapshots and idempotent joint VerificationReceipt application;
- generic extension event and CAS admission used by the independently versioned Harness repository;
- recovery across fresh Host processes and local Runtime control-plane restarts;
- modern stateless Runtime MCP transport with explicit retained legacy decoding;
- authenticated loopback Host MCP exposing paginated, semantically bounded external-continuity discovery plus revision-coherent resume/adopt/checkpoint operations, including terminal continuity tracking, without making MCP transport state durable.

These are bounded vertical slices, not a general workflow engine, policy platform, or multi-Agent scheduler.

The package root intentionally exposes durable Host authority and cross-owner boundary types only. Concrete repository-read, mutation, and code-change workloads remain available under `ordivon_host.engine`; they are proven workloads, not permanent Host primitives. New Agent integrations should prefer the generic Task/extension/external-executor boundaries unless they specifically need one of those workload implementations.

## Runtime transport

The default `McpRuntimeClient` uses Runtime's canonical MCP `2026-07-28` lifecycle:

```text
server/discover
→ verify supportedVersions and server identity
→ send per-request client metadata
→ bind method and Tool identity through Mcp-Method / Mcp-Name
→ no transport Session is persisted or created
```

The `2025-06-18` Session lifecycle remains available only through the explicit `ORDIVON_SESSION_MCP_PROFILE` compatibility decoder. Transport state never becomes Goal, Task, Context, Effect, Dispatch, verification, or recovery truth.

## Requirements

- Python 3.12;
- Git;
- SQLite through Python;
- the exact `ordivon-protocol` revision pinned in `pyproject.toml`;
- the exact official `mcp` Python SDK revision pinned in `pyproject.toml` for the Host MCP transport;
- Linux for the canonical trusted-local operational path;
- a reachable Ordivon Runtime for live workloads.

## Quick start

Online editable installation:

```bash
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
python -m unittest discover -s tests -v
```

Local sibling development path:

```bash
export PYTHONPATH="$PWD/src:/root/projects/ordivon-computing/packages/ordivon-protocol/src"
python3.12 -W error::ResourceWarning -m unittest discover -s tests -v
```

Full setup, state initialization, Runtime health, and live read-only acceptance are in [`docs/QUICKSTART.md`](docs/QUICKSTART.md).

## Operations

```bash
ordivon-host --state-root /var/lib/ordivon/host init
ordivon-host --state-root /var/lib/ordivon/host inspect
ordivon-host --state-root /var/lib/ordivon/host doctor
ordivon-host --state-root /var/lib/ordivon/host doctor --history
ordivon-host --state-root /var/lib/ordivon/host task handoff TASK_ID --expected-revision REVISION
ordivon-host --state-root /var/lib/ordivon/host task assess TASK_ID
ordivon-host --state-root /var/lib/ordivon/host task reconcile TASK_ID

# after one-time authority initialization and private token provisioning
ordivon-host-mcp --check
ordivon-host-mcp
```

`task reconcile` performs at most one conservative step. It never invents a new Effect, blindly redispatches uncertain work, or invokes a Provider. `ordivon-host-mcp` is a separate loopback-only transport surface for `task.list`, `task.resume`, `task.adopt`, and `task.checkpoint`; it uses a separate private bearer token and never proxies Runtime or Harness.

## Documentation map

| Need | Start here |
| --- | --- |
| install, test, initialize and verify | [`docs/QUICKSTART.md`](docs/QUICKSTART.md) |
| current maturity and limits | [`docs/STATUS.md`](docs/STATUS.md) |
| architecture and owner boundaries | [`ARCHITECTURE.md`](ARCHITECTURE.md) |
| state, configuration, Doctor, backup and recovery | [`docs/OPERATIONS.md`](docs/OPERATIONS.md) |
| stored data, retention, export and deletion | [`docs/DATA_AND_PRIVACY.md`](docs/DATA_AND_PRIVACY.md) |
| versions, release gates and deprecation | [`docs/RELEASES.md`](docs/RELEASES.md) |
| canonical document ownership | [`docs/authority.md`](docs/authority.md) |
| migration and extraction provenance | [`docs/MIGRATION.md`](docs/MIGRATION.md) |
| repository changes | [`CONTRIBUTING.md`](CONTRIBUTING.md) |
| vulnerability reporting | [`SECURITY.md`](SECURITY.md) |

Phase reports, remediation notes, extraction records, and `evidence/` preserve decisions and receipts. They do not override current canonical documents or machine-owned state.

## Repository layout

```text
src/ordivon_host/   Host implementation
tests/              deterministic contract tests
scripts/            explicit live, scale, and fault scenarios
evidence/           immutable historical engineering receipts
docs/               canonical and historical decisions
```

## Security and data

Host state may contain Task text, Contexts, proposals, decisions, participant and repository references, source-derived content, Effects, observations, verification, and outcomes. It does not automatically redact sensitive content. Read [`SECURITY.md`](SECURITY.md) and [`docs/DATA_AND_PRIVACY.md`](docs/DATA_AND_PRIVACY.md) before operating or sharing state.

## Project family

- [Public project directory](https://ordivon.com/projects)
- [Cross-project map](https://github.com/zycxfyh/ordivon-computing/blob/main/projects/README.md)
- [Ordivon Runtime](https://github.com/zycxfyh/ordivon-runtime)
- [Ordivon Harness](https://github.com/zycxfyh/ordivon-harness)
- [Ordivon Computing](https://github.com/zycxfyh/ordivon-computing)

## License

Apache License 2.0. See [`LICENSE`](LICENSE).
