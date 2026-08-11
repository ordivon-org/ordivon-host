---
schema_version: 1
id: host.status
title: Host Status
type: status
profile: organization
lifecycle: active
source_role: canonical
visibility: public
owners:
  - ordivon-host
audience:
  - builder
  - operator
  - user
  - agent
updated: 2026-08-12
summary: Stable maturity, supported environment, current continuity/commitment capabilities, known limits, and live-state verification route for Ordivon Host.
evidence_status: verified
readiness: READY
applies_to:
  - ordivon-host
related:
  - host.start
  - host.quickstart
  - host.architecture
  - host.operations
  - host.releases
---
# Host Status

## Maturity

Ordivon Host is **operational for owner-trusted local engineering work** and **pre-1.0 as a public product interface**.

Operational means the current repository has durable Journal/CAS state, exact Task revision/lease fencing, conservative recovery, backup/restore/Doctor, Runtime and external-executor boundaries, semantic external continuity, and receipt-bound local deployment. Pre-1.0 means public schemas, Python APIs, packaging, and workload boundaries may still change through explicit migration/cutover evidence; durable history may not be silently reinterpreted.

## Supported environment

The canonical path currently assumes:

- Python 3.12;
- Linux trusted-local operation;
- local SQLite and immutable filesystem CAS;
- a loopback or operator-tunneled Ordivon Runtime where a workload needs Runtime;
- Runtime MCP `2026-07-28` as the default transport lifecycle, with explicit retained legacy compatibility;
- owner-trusted repositories, cognition executors, participants, and state roots.

Host does not currently provide hostile multi-tenancy, distributed consensus, a general scheduler, remote durable state ownership, or automatic recovery of unkeyed effects.

## Current capability matrix

| Area | State | Boundary |
| --- | --- | --- |
| Task/Journal/CAS continuity | operational | Host-owned semantic work and evidence references; not domain or physical truth |
| schema migration, backup/restore, Doctor | operational | exact Host authority/storage integrity |
| deterministic Runtime read | operational proof slice | Runtime proves physical read; Host verifies/adjudicates Task progress |
| guarded mutation and response-loss recovery | operational proof slice | keyed Dispatch + UNKNOWN + original-Job observation; never blind redispatch |
| version-bound source change | operational proof slice | structured evidence + Runtime compare-and-close before Host acceptance |
| closed-choice cognition | retained deterministic profile | external cognition result admitted against exact Context |
| open ActionProposal + DecisionRequest | experimental but verified | Host lowers/adjudicates consequence; no generic planner |
| opaque extension namespace continuity | operational boundary | Host preserves bytes/revision metadata without owning extension semantics |
| external executor binding | operational boundary | foreign Run evidence can be referenced; Task acceptance remains Host-owned |
| external semantic continuity | operational local authority | adopt/resume/checkpoint WorkingCheckpoint across Agent/session replacement |
| Host MCP | operational transport boundary | six narrow Tools; no Runtime proxy, scheduler, Provider or cognition endpoint |
| receipt-bound deployment/lifecycle | operational | physical release/rollback/retention evidence separate from Task semantics |

## External-continuity contract

`ordivon.host.external-continuity.v1` preserves a bounded WorkingCheckpoint across replaceable clients. The checkpoint is a semantic working claim containing objective, frontier, established/unresolved/rejected findings, constraints, next actions, and optional physical navigation hints.

Important limits:

- Runtime Workspace/Job/Git hints in the checkpoint are not current physical truth and must be revalidated with their owners;
- `task.list` exposes bounded previews and revision/digest identity rather than raw checkpoint bodies;
- `task.resume` returns one revision-coherent checkpoint;
- `task.checkpoint` supports complete replacement or exact-revision patching;
- response-loss replay converges only for the exact original claim/revision;
- `complete`/`abandon` end Host tracking only and do not assert external domain outcomes.

## Host MCP contract

The current MCP surface is exactly:

```text
host.status
task.observe
task.list
task.resume
task.adopt
task.checkpoint
```

Every Tool result carries a `serverInterface` identity derived from Tool names and schemas. Production deployment independently receipts the wire Tool catalog. This can prove that a client is using a stale/different schema after re-observation; Host does not claim to know a client's cached schema without that comparison.

The service remains loopback-only. A canonical HTTPS public origin may be admitted behind an operator-owned authenticated tunnel. Optional Cloudflare Access trust is valid only under the configured loopback + Access-protected deployment precondition; arbitrary proxy headers do not become authority.

## Known limits

- Goal coordination is a snapshot over Task revisions rather than a durable Goal event stream.
- Workload-specific state machines remain explicit; Host does not expose a generic executor-neutral Effect lifecycle.
- Runtime Job success or external executor Run completion does not automatically complete a Task.
- Provider execution remains outside Host. Host persists provider-neutral cognition work and returned evidence, not Provider call/session state.
- Domain systems must supply authoritative world-state and domain verification semantics.
- extension namespace identity/state proves retained owner bytes and provenance, not owner availability, external currentness, outstanding work, or authority.
- `WorkingCheckpoint.runtime` is intentionally a navigation hint rather than a copied Runtime state graph.
- concurrent first-time authority initialization is not supported; initialize one Host state root before concurrent consumers.
- the official MCP SDK path does not currently expose explicit output schemas without coupling Host to SDK-private output-model plumbing; the measured limitation is retained rather than creating a second schema system.
- full-history Doctor is explicit and slower than ordinary startup/Doctor.
- live recovery evidence covers the tested local Runtime/filesystem/systemd path, not arbitrary host reboot, database corruption, remote partitions, or distributed scheduling.

## Live state is machine-owned

This page does not own current Task counts, active leases, exact CAS bytes, live Runtime health, deployed release identity, or schema files. Query Host and the relevant external owner:

```bash
ordivon-host --state-root /var/lib/ordivon/host inspect
ordivon-host --state-root /var/lib/ordivon/host doctor
ordivon-host --state-root /var/lib/ordivon/host doctor --history
ordivon-host --config /etc/ordivon/host.toml \
  --state-root /var/lib/ordivon/host doctor --runtime
```

For MCP deployments, `host.status` provides the bounded Host-owned projection. It never proxies Runtime.

## Reopen conditions

Revisit this status when Host gains a durable Goal stream, remote/distributed state ownership, a new shared workload abstraction, a hostile-code boundary, Python support changes, the legacy Runtime decoder is removed, or the public interface reaches a declared 1.0 contract.
