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
  - builder
  - operator
  - agent
updated: 2026-08-04
summary: Canonical entry to Host Task continuity, commitment, uncertainty, evidence admission, verification, recovery, and extension boundaries.
evidence_status: verified
readiness: READY
applies_to:
  - ordivon-host
related:
  - host.architecture
  - host.operations
  - host.authority
---
# Ordivon Host

## Purpose

Persistent coordination and commitment plane for Ordivon.

Ordivon Host owns durable Tasks, Goal-scoped Task coordination, Host events and projections, bounded cognition Contexts, proposal compilation or admission, Effect commitments, Runtime Dispatch identities, verification receipts, participant-routed decisions, and Task outcomes. It treats model sessions and Runtime processes as replaceable dependencies rather than owners of work continuity.

The Host controls durable work and external commitment lifecycles. It does not own model intelligence, domain-world truth, physical execution, or a permanent hierarchy among participants.

## Current boundary

Host owns durable work coordination and commitment records. Runtime owns physical execution, domain systems own world truth and domain verification, Harness owns replaceable Agent execution lifecycles, Computing owns promoted shared contracts, and Providers remain replaceable cognition dependencies.

## Repository selection

| Change concerns | Use | Do not put here |
| --- | --- | --- |
| Workspace, Job, Attempt, process tree, Artifact, physical cancellation, or execution recovery | `ordivon-runtime` | Task meaning, Agent Run policy, or domain completion |
| durable Task continuity, Journal/CAS, commitment admission, verification records, or Task outcomes | `ordivon-host` | Provider loops, Harness Run semantics, or physical process truth |
| Assignment, Agent Run, Provider adapter, model–Tool loop, Tool-step checkpoint, or Run recovery | `ordivon-harness` | a second Task database, Runtime supervision, or domain-world authority |

## Status

This repository was extracted with Git history from `ordivon-computing/incubation/host-v0` after six architectural proof stages passed. It is an independently versioned engineering prototype, not yet a general production workflow engine, policy platform, or multi-Agent scheduler.

Current proven vertical slices:

- logical RepositoryRef-based Runtime read with Authority and independent digest verification;
- closed-choice deterministic cognition with two to eight exact CandidateActions;
- open ActionProposal cognition with no prebuilt action menu, durable Model Invocation, Host-owned lowering, and structured rejection;
- explicit `owner_trusted` and `public_bounded` capability profiles, with consequence admission remaining separate from physical reach;
- participant-aware DecisionRequest generation for shared, foreign-owned, or non-reversible proposals;
- a live Codex proposal lowered into one verified Runtime repository read across fresh Host state opens;
- guarded mutation with durable Dispatch identity, conservative UNKNOWN reconciliation, and persisted terminal failure;
- logical RepositoryRef → Computing SourceChange Effect → CapabilityDecision → EffectBinding → Runtime Dispatch;
- durable two-file source change through structured Runtime checks and exact structured diff verification;
- conservative one-shot recovery assessment that never redispatches an uncertain Effect;
- MCP Session lifecycle support without persisting transport sessions as Task truth;
- immutable TaskDescriptor identity and Goal-scoped Task revision snapshots;
- idempotent per-Task application of one joint VerificationReceipt with multiple result items;
- recovery across fresh Host processes and local Runtime control-plane restarts;
- extension-safe immutable Task events with reserved Host namespaces, thread-stable extension identity, and no dynamic Enum mutation;
- generic Host history and operator-handoff surfaces that preserve extension bytes, references, Task revision, UNKNOWN fences and ready frontiers without interpreting extension semantics;
- a public `HostExtensionPort` for CAS-backed extension objects and revision/state/frontier-fenced preserving Journal appends, without adding Harness-specific tables or state machines;
- a one-way integration boundary for the independently versioned [`ordivon-harness`](https://github.com/zycxfyh/ordivon-harness) repository, which now owns Agent Assignment, Run, Recovery, Completion, Provider adapters and bare-model execution;
- schema-v3 operational state with private 0700/0600 modes, backup/restore, optional full-history Doctor, and measured 100,000-event behavior;
- exact lease-fenced event admission, irreversible terminal Tasks, causal-link validation, and version-bound code-change completion through Runtime compare-and-close.

The closed-choice path remains a useful deterministic and closed-domain profile. It is no longer treated as the only possible cognition interface. Agent Harness implementation and its historical evidence now live in the independent `ordivon-harness` repository.

## Start here

- [`ARCHITECTURE.md`](ARCHITECTURE.md) defines the current Host architecture and responsibility boundary.
- [`docs/OPERATIONS.md`](docs/OPERATIONS.md) defines the operational state, configuration, backup, restore, Doctor, and conservative reconciliation contract.
- [`docs/authority.md`](docs/authority.md) identifies which records may define current Host behavior.
- [`docs/MIGRATION.md`](docs/MIGRATION.md), phase reports, extraction records, and `evidence/` preserve decisions and receipts but do not replace the current architecture.

## Development

Python 3.12 is required. The authoritative `ordivon-protocol` 0.5.0 package remains in `ordivon-computing` and is pinned to the exact unified Protocol revision by `pyproject.toml`.

```bash
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
python -m unittest discover -s tests
```

Live scripts require a reachable Ordivon Runtime and are not part of default CI.

## Operations

After installation, the `ordivon-host` command provides state initialization, inspection, Task queries, Doctor checks, backup verification and restore, and read-only CAS garbage-collection planning:

```bash
ordivon-host --state-root /var/lib/ordivon/host init
ordivon-host --state-root /var/lib/ordivon/host doctor
ordivon-host --state-root /var/lib/ordivon/host doctor --history
ordivon-host --state-root /var/lib/ordivon/host task handoff TASK_ID --expected-revision REVISION
ordivon-host --state-root /var/lib/ordivon/host task assess TASK_ID
ordivon-host --state-root /var/lib/ordivon/host task reconcile TASK_ID
ordivon-host --state-root /var/lib/ordivon/host inspect
```

See `docs/OPERATIONS.md` for the schema migration, configuration, secret-loading, backup, and restore contracts.

## Repository layout

```text
src/ordivon_host/   Host implementation
tests/              deterministic contract tests
scripts/            explicit live and fault-injection scenarios
evidence/           immutable historical receipts
docs/               migration and structure decisions
```

## Project family

- [Public project directory](https://ordivon.com/projects) — reader-facing role, maturity, and next steps.
- [Cross-project map](https://github.com/zycxfyh/ordivon-computing/blob/main/projects/README.md) — stable roles, repository links, and authority entry points for all nine repositories.
- Related owners: [Ordivon Harness](https://github.com/zycxfyh/ordivon-harness) owns Assignment-scoped Agent Runs; [Ordivon Runtime](https://github.com/zycxfyh/ordivon-runtime) owns physical execution; [Ordivon Computing](https://github.com/zycxfyh/ordivon-computing) owns promoted shared contracts.

## License

Apache License 2.0. See `LICENSE`.
