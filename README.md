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
updated: 2026-08-12
summary: Canonical entry to durable Task continuity, commitments, uncertainty, evidence admission, verification, recovery, and external-continuity handoff without owning physical or domain truth.
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

An Agent works on a long task, reaches a meaningful frontier, and then disappears. Hours later a different Agent continues. The old model session is gone. The original process is gone. Runtime Workspaces may have changed or closed. Domain state may also have moved.

What must survive?

Not the chat transcript as authority. Not the Provider session. Not a copied snapshot of every physical subsystem.

**Ordivon Host preserves the durable semantic work that must outlive replaceable cognition and execution.** It keeps Task identity, current semantic frontier, commitments already made, uncertainty that must not be guessed away, references to evidence, verification admission, and Task-level outcome continuity.

```text
purpose / objective
→ durable Task identity
→ current semantic frontier + bounded Context
→ proposal / decision / commitment
→ physical or domain work happens elsewhere
→ evidence returns
→ verification / unresolved uncertainty
→ next frontier or Task outcome
```

A new Agent can continue because the work has durable semantic identity. It still has to re-observe current Runtime, domain, provider, or repository truth from the owner that can prove it.

## Purpose

Host exists so semantic work can survive replaceable cognition and execution without copying every lower-layer state into a second authority.

## What Host owns

Host owns:

- durable Task identity and revisioned Task state;
- Goal-scoped coordination over Task revisions;
- Journal/CAS durability and exact Event admission;
- bounded semantic Context and cognition requests;
- commitments that must survive process/session replacement;
- Effect/Dispatch identity where a Host workload admits a consequence;
- participant-routed DecisionRequests;
- explicit `UNKNOWN` where outcome cannot safely be inferred;
- references to evidence plus Host-owned verification admission;
- semantic WorkingCheckpoint continuity for external Agents;
- Task-level outcome continuity.

Host does **not** become the owner of a fact merely because it persists a reference or extension object for that fact.

## Responsibility boundary

| Responsibility | Owner |
| --- | --- |
| durable semantic Task/work continuity and Task-level acceptance | Host |
| bounded Agent Run, Provider/Tool cognition, Provider continuation | Harness or another cognition executor |
| Workspace/Job/Attempt/process/Artifact physical execution facts | Runtime |
| provider-native or external-world occurrence/current state | provider/domain owner |
| domain meaning and domain-specific verification | Game, Security, Finance, World, or another domain |
| promoted cross-project contracts | Computing after cross-owner proof |

Persistence is not semantic ownership. Physical execution is not semantic completion. Domain truth does not move into Host because Host carries a durable reference to it.

## One external-continuity journey

The current Host MCP exposes a narrow continuity path for an Agent that must survive conversation/session replacement:

```text
Agent adopts Task + initial WorkingCheckpoint
→ Host commits exact Task revision
→ Agent works outside Host
→ Agent checkpoints current objective/frontier/findings
→ session disappears
→ new Agent lists/resumes the same Task
→ revalidates Runtime/Git/domain navigation hints with their owners
→ continues work
→ later checkpoint or terminal continuity disposition
```

A WorkingCheckpoint is explicitly a **semantic working claim**. Its optional Runtime Workspace/Job/Git fields are navigation hints, not copied physical truth. A resumed Agent must revalidate them before acting.

If a checkpoint response is lost, retry the identical checkpoint against the original `expectedRevision`. If that exact transition already committed, Host converges to the existing revision. A competing or different claim fails closed.

`continuityDisposition=complete|abandon` ends Host continuity tracking only. It does not assert that an external domain succeeded or failed.

## One uncertain-effect journey

Host also demonstrates why durable commitment matters before a consequential call:

```text
Host admits exact Effect + Dispatch identity
→ physical executor is called
→ response path is lost
→ Host records uncertainty rather than inventing success/failure
→ recovery locates the original durable external identity
→ owner-native evidence is observed
→ verification decides what can be concluded
```

Runtime Job success alone is not enough to mark a whole Task complete. The process may have succeeded while an external provider remains uncertain, or the resulting bytes may still fail independent verification, or the domain objective may not be satisfied.

## Status

Host is operational for owner-trusted local engineering work and pre-1.0 as a public interface.

The current product includes:

- immutable TaskDescriptor identity, revisioned projections, lease/revision fencing, and irreversible terminal state;
- schema-v5 SQLite Journal/CAS with migration, backup/restore, Doctor, full-history validation, and opaque per-Task/per-namespace extension-state durability;
- deterministic Runtime read and source-change proof slices;
- guarded mutation with durable Dispatch identity, response-loss `UNKNOWN`, original-Job reconciliation, and no blind redispatch;
- provider-neutral cognition requests plus externally executed ActionSelection/ActionProposal admission;
- participant-aware DecisionRequests for consequences that should not self-authorize;
- external-executor binding without importing Harness;
- authenticated loopback Host MCP with `host.status`, `task.observe`, `task.list`, `task.resume`, `task.adopt`, and `task.checkpoint`;
- revision-coherent WorkingCheckpoint patching and exact response-loss replay;
- receipt-bound local deployment, schema-aware activation rollback, release lifecycle/GC planning, and external substrate retention projections.

Exact support claims and known limits live in [`docs/STATUS.md`](docs/STATUS.md).

## What Host deliberately does not become

Host is not:

- a chat transcript database;
- a Provider/session manager;
- an Agent planner or model runtime;
- a generic multi-Agent scheduler;
- a second Runtime process supervisor;
- a generic domain truth database;
- a universal Effect lifecycle for every backend;
- an authority that infers external success from local execution;
- a machine-level substrate lifecycle owner merely because Host depends on that substrate.

Several earlier candidates were removed or kept workload-local because they duplicated owner responsibility without a second real consumer.

## Runtime transport

Host may call Runtime for workloads that require physical execution, but transport/session mechanics never become Task identity. The canonical Runtime client uses current Runtime discovery and exact Tool identity; retained legacy transport decoding is compatibility only. Host persists semantic commitments and exact external references, not MCP Session state.

## Host MCP surface

The default endpoint is loopback-bound and authenticated. Its six Tools are intentionally small:

| Tool | Purpose |
| --- | --- |
| `host.status` | bounded Host authority/deployment observation |
| `task.observe` | compact revision-fenced Task observation |
| `task.list` | paginated external-continuity discovery |
| `task.resume` | full revision-coherent WorkingCheckpoint recovery |
| `task.adopt` | create/recover one explicit continuity Task |
| `task.checkpoint` | revision-bound semantic checkpoint transition |

The MCP server does not proxy Runtime or Harness, call a Provider, run a scheduler, or create a second Task store. Every Tool response exposes a schema-only `serverInterface` identity so clients can detect stale Tool-schema state after reconnect/refresh.

## Requirements

- Python 3.12;
- Git and SQLite through Python;
- the exact `ordivon-protocol` revision pinned by the repository, plus the exact MCP SDK revision pinned only in the optional `mcp` server extra; core Host/domain consumers do not need the server SDK;
- Linux for the canonical trusted-local path;
- a reachable Runtime only for workloads that actually need Runtime execution.

## Quick start

```bash
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[mcp]'
python -m unittest discover -s tests -v
python scripts/check_docs.py
```

Initialize and inspect one local authority:

```bash
ordivon-host --state-root /var/lib/ordivon/host init
ordivon-host --state-root /var/lib/ordivon/host inspect
ordivon-host --state-root /var/lib/ordivon/host doctor
```

Full setup, Runtime health, continuity examples, and live read-only acceptance are in [`docs/QUICKSTART.md`](docs/QUICKSTART.md).

## Operations

Normal operation uses short Host Journal transactions, immutable CAS objects, revision-fenced checkpoints, explicit Doctor/backup/restore, and conservative recovery. Commands and deployment/reconciliation procedures are owned by [`docs/OPERATIONS.md`](docs/OPERATIONS.md).

## Documentation map

| Need | Read |
| --- | --- |
| understand why Host exists and what survives replacement | this README |
| install, initialize, and perform the first live journey | [`docs/QUICKSTART.md`](docs/QUICKSTART.md) |
| inspect current maturity and known limits | [`docs/STATUS.md`](docs/STATUS.md) |
| understand Task/Event/Context/Effect/recovery ownership | [`ARCHITECTURE.md`](ARCHITECTURE.md) |
| operate state, backup, restore, Doctor, continuity and deployment | [`docs/OPERATIONS.md`](docs/OPERATIONS.md) |
| inspect data sensitivity/retention/deletion | [`docs/DATA_AND_PRIVACY.md`](docs/DATA_AND_PRIVACY.md) |
| inspect release/deprecation contracts | [`docs/RELEASES.md`](docs/RELEASES.md) |
| determine which document/source owns a Host claim | [`docs/authority.md`](docs/authority.md) |

Phase reports, extraction records, and remediation notes live under [`docs/history/`](docs/history/); they and `evidence/` preserve derivation and receipts. They do not override current canonical documents or machine-owned state.

## Security and data

Host state can contain Task text, Context, proposals, decisions, participant/repository references, Effects, observations, verification, outcomes, and opaque extension objects. It is a trusted-local private authority and does not automatically redact sensitive content.

Read [`SECURITY.md`](SECURITY.md) and [`docs/DATA_AND_PRIVACY.md`](docs/DATA_AND_PRIVACY.md) before exposing or sharing it.

## License

Apache License 2.0. See [`LICENSE`](LICENSE).
