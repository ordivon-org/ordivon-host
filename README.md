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
updated: 2026-08-18
summary: Canonical entry to Ordivon's durable semantic Task continuity and Host-owned Journal/CAS authority without owning cognition, physical execution, external currentness, or domain truth.
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

An Agent works on a long task, reaches a meaningful frontier, and disappears. Later a different Agent continues. The old model session may be gone, the original process may be gone, Runtime Workspaces may have changed, and domain state may have moved.

What must survive is not a transcript pretending to be truth and not a copied snapshot of every external subsystem. What must survive is the **durable semantic work claim**: Task identity, the current bounded frontier, what was established, what remains unresolved, which routes were rejected, which constraints still apply, and where a new Agent should revalidate owner-current facts.

**Ordivon Host preserves that continuity.**

## Purpose

Host exists so semantic work can survive replaceable cognition and execution without creating a second authority for Runtime, Git, providers, or domain state.

A resumed Agent should be able to answer:

```text
what work identity am I continuing?
what semantic frontier was last committed?
what claims were established or rejected?
what uncertainty must remain explicit?
what owner-native facts must I re-observe before acting?
```

## Identity meaning

**Host is a product name, not a claim that Ordivon has one universal Host ontology or central coordinator.** The Foundations program falsified that broader interpretation. In current engineering use, “Host” names the component that hosts durable semantic Task continuity and the Journal/CAS authority required to preserve it.

This distinction is normative:

```text
Host product identity
!= universal coordinator
!= global truth owner
!= Runtime proxy
!= generic executor
!= governance authority
```

The stable `ordivon-host`, `ordivon_host`, MCP, systemd, state-path, and release-path identities are therefore compatibility names for this narrowed responsibility. Their continued existence does not reopen the rejected ontology.

## What Host owns

The current product owns:

- durable `TaskDescriptor` identity and revisioned `TaskProjection` state;
- one Host Journal plus immutable content-addressed objects;
- exact Event/object admission, stream revision fencing, short leases, and irreversible terminal Task identity;
- semantic `WorkingCheckpoint` continuity for external Agents, including exact response-loss replay;
- revision-coherent handoff and bounded Host-owned inspection;
- opaque per-Task/per-namespace extension-state durability without interpreting owner-specific fields;
- bounded context-selection data structures retained for the real Security consumer, without Host-owned cognition execution;
- Host-local Doctor, full-history validation, backup/restore, deployment receipts, rollback planning, and lifecycle inspection;
- a small set of typed compatibility values retained where current consumers use them.

Compatibility value types do **not** imply generic lifecycle ownership. For example, retaining an `ArtifactRef`, `DispatchEnvelope`, `ObservationEnvelope`, `StateRef`, `VerificationReceipt`, or `TaskOutcome` type does not make Host the universal owner of Runtime execution, external consequence, verification sufficiency, or domain completion.

## Responsibility boundary

| Responsibility | Owner |
| --- | --- |
| durable semantic Task continuity and Host Journal/CAS admission | Host |
| bounded Agent Run, Provider/Tool cognition, Provider continuation | Harness or another cognition executor |
| Workspace/Job/Attempt/process/Artifact physical execution facts | Runtime |
| repository/source currentness | Git/repository owner |
| provider-native or external-world occurrence/current state | provider/domain owner |
| domain meaning and domain-specific verification | Game, Security, Finance, World, or another domain |
| promoted cross-project contracts | Computing after cross-owner proof |

Persistence is not semantic ownership. A Host reference to foreign work is navigation/evidence, not a copied claim that the foreign owner is current, reachable, authorized, healthy, or complete.

## One external-continuity journey

The current Host MCP exposes one narrow continuity path for work that must survive conversation/session replacement:

```text
Agent adopts Task + initial WorkingCheckpoint
→ Host commits exact Task revision
→ Agent works outside Host
→ Agent checkpoints objective/frontier/findings
→ session disappears
→ new Agent lists/resumes the same Task
→ revalidates Runtime/Git/domain hints with their owners
→ continues work
→ later checkpoint or terminal continuity disposition
```

A `WorkingCheckpoint` has `truthRole = semantic-working-claim`. Optional Workspace, Job, or Git references are navigation hints. A resumed Agent must revalidate them before using them as current truth.

If a checkpoint response is lost, retry the identical checkpoint against the original `expectedRevision`. If that exact transition already committed, Host returns the existing revision. A competing or different claim fails closed.

`continuityDisposition=complete|abandon` ends Host continuity tracking only. It does not assert that an external domain succeeded or failed.

## Status

Host 0.3.x is operational for owner-trusted local engineering work and remains pre-1.0 as a public interface.

The current product includes:

- schema-v5 SQLite Journal/CAS with migration, backup/restore, Doctor, and full-history validation;
- semantic external continuity through `WorkingCheckpoint`, exact revision patching, deterministic handoff, and exact response-loss replay;
- opaque extension-state durability with revision-fenced owner metadata;
- the bounded context-selection surface consumed by Security;
- authenticated loopback MCP with exactly `host.status`, `task.observe`, `task.list`, `task.resume`, `task.adopt`, and `task.checkpoint`;
- receipt-bound local deployment with exact release-byte rollback support.

Exact support claims and known limits live in [`docs/STATUS.md`](docs/STATUS.md).

## What Host deliberately does not become

Host is not:

- a chat transcript database or Provider-session manager;
- an Agent planner, model runtime, or cognition execution host;
- a shared Goal coordinator or generic multi-Agent scheduler;
- a Runtime client, Runtime health proxy, or process supervisor;
- a source-read, mutation, or code-change engine;
- a caller-neutral foreign-executor coordinator;
- an automatic cross-owner reconciler;
- a generic capability-policy or validity oracle;
- a generic domain truth database;
- a universal Effect lifecycle for every backend;
- an authority that infers external success from local execution.

Those removals are deliberate engineering consumption of the frozen Host Foundations, not temporary omissions waiting to be reintroduced by default.

## External-owner references

Host may persist opaque references or navigation hints to Runtime, Git, Harness, World, Security, Finance, Game, providers, or another owner. Host does not make those owners current by storing their identities.

When external truth matters, query the owner directly. In particular:

- Runtime Workspace/Job identities in `WorkingCheckpoint.runtime` must be revalidated with Runtime-native Tools;
- Git/source revisions must be revalidated against the relevant repository authority;
- domain state and verification sufficiency remain with the domain owner;
- Harness Run state remains Harness-owned.

Host Doctor and `host.status` therefore report Host-owned health only; an unrelated Runtime or domain outage does not by itself make Host unhealthy.

## Host MCP surface

The default endpoint is loopback-bound and authenticated. Its six Tools are intentionally small:

| Tool | Purpose |
| --- | --- |
| `host.status` | bounded Host authority/deployment observation |
| `task.observe` | compact revision-fenced Host Task observation |
| `task.list` | paginated external-continuity discovery |
| `task.resume` | full revision-coherent WorkingCheckpoint recovery |
| `task.adopt` | create/recover one explicit continuity Task |
| `task.checkpoint` | revision-bound semantic checkpoint transition |

The MCP server does not proxy Runtime or Harness, call a Provider, run a scheduler, or create a second Task store. Every Tool response exposes a schema-only `serverInterface` identity so clients can detect stale Tool-schema state after reconnect/refresh.

## Requirements

- Python 3.12;
- Git and SQLite through Python;
- the exact `ordivon-protocol` revision pinned by the repository;
- the exact MCP SDK revision pinned only in the optional `mcp` server extra;
- Linux for the canonical trusted-local operational path.

Runtime is **not** a Host installation requirement. Consumers query Runtime separately when their own workload requires physical execution truth.

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
ordivon-host --state-root /var/lib/ordivon/host doctor --history
```

Full setup, continuity examples, and owner-revalidation guidance are in [`docs/QUICKSTART.md`](docs/QUICKSTART.md).

## Operations

Normal operation uses short Host Journal transactions, immutable CAS objects, revision-fenced checkpoints, explicit Doctor/backup/restore, and conservative local recovery projection. Deployment is receipt-bound and treats release bytes plus Host schema compatibility as one activation boundary. Procedures are owned by [`docs/OPERATIONS.md`](docs/OPERATIONS.md).

## Documentation map

| Need | Read |
| --- | --- |
| understand why Host exists and what the name now means | this README |
| install, initialize, and perform the first continuity journey | [`docs/QUICKSTART.md`](docs/QUICKSTART.md) |
| inspect current maturity and known limits | [`docs/STATUS.md`](docs/STATUS.md) |
| understand current ownership and durability architecture | [`ARCHITECTURE.md`](ARCHITECTURE.md) |
| operate state, backup, restore, Doctor, continuity and deployment | [`docs/OPERATIONS.md`](docs/OPERATIONS.md) |
| inspect data sensitivity/retention/deletion | [`docs/DATA_AND_PRIVACY.md`](docs/DATA_AND_PRIVACY.md) |
| inspect release/deprecation contracts | [`docs/RELEASES.md`](docs/RELEASES.md) |
| determine which document/source owns a Host claim | [`docs/authority.md`](docs/authority.md) |

Phase reports and evidence under `docs/history/` and `evidence/` preserve derivation. They do not override current canonical documents or machine-owned state.

## Security and data

Host state can contain Task text, semantic working claims, references, owner-opaque extension objects, and other retained compatibility objects. It is a trusted-local private authority and does not automatically redact sensitive content.

Read [`SECURITY.md`](SECURITY.md) and [`docs/DATA_AND_PRIVACY.md`](docs/DATA_AND_PRIVACY.md) before exposing or sharing it.

## License

Apache License 2.0. See [`LICENSE`](LICENSE).
