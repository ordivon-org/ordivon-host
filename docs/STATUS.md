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
updated: 2026-08-08
summary: Stable maturity claim, support boundary, proven slices, known limits, and live-state verification path for Ordivon Host.
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

Ordivon Host is an **operational engineering prototype for owner-trusted local work** and **pre-1.0 as a public product interface**.

Operational means the repository has a durable Journal/CAS, versioned schema migration, private state modes, exact Task revision and lease fencing, backup/restore, Doctor, conservative reconciliation, modern Runtime transport, deterministic tests, and live evidence for read, mutation, source-change, cognition, and restart scenarios.

Pre-1.0 means public Python imports, object schemas, operational packaging, workload APIs, and deployment conventions may still change. Changes must preserve explicit decoder, migration, recovery, rollback, or major-cutover evidence. Pre-1.0 does not permit silent reinterpretation of durable Task history.

## Supported environment

The canonical path currently supports:

- Python 3.12;
- Linux trusted-local operation;
- local SQLite and filesystem-backed immutable CAS;
- a loopback or operator-tunneled Ordivon Runtime;
- Runtime MCP `2026-07-28` as the default transport lifecycle;
- explicit `2025-06-18` Session compatibility when selected;
- owner-trusted repositories, external cognition executors, and participants.

Python 3.13+, hostile multi-tenancy, remote distributed consensus, a general scheduler, a hosted privacy boundary, and automatic recovery of unkeyed effects are not supported.

## Proven capabilities

| Area | Status |
| --- | --- |
| immutable TaskDescriptor and revisioned Task projection | operational |
| exact lease-fenced event admission and causal edges | operational |
| Journal/CAS integrity, schema migration, backup/restore, Doctor | operational |
| deterministic Runtime repository read and independent verification | operational |
| guarded mutation with UNKNOWN reconciliation and no redispatch | operational proof slice |
| version-bound two-file source change and compare-and-close | operational proof slice |
| closed-choice cognition | retained deterministic profile |
| open ActionProposal with Host-owned lowering and DecisionRequest | experimental but verified |
| Goal-scoped Task revision coordination | operational narrow slice |
| generic extension event and CAS admission for independently versioned components | operational boundary |
| external executor request, foreign Run binding, recovery and completion collection | operational P0 boundary; Task acceptance remains Host-owned |
| external-continuity adoption with crash-safe initial semantic seed, bounded WorkingCheckpoint, revision-coherent discovery/resume, terminal tracking disposition, and exact response-loss replay | operational local authority |
| authenticated loopback Host MCP for paginated external-continuity discovery plus self-describing resume/adopt/checkpoint inputs | operational transport boundary |
| general workflow engine or multi-Agent scheduler | not provided |
| domain-world truth and semantic verification | domain-owned |
| physical execution and process recovery | Runtime-owned |
| Agent Run and Provider-loop recovery | Harness-owned |

## Current transport contract

The default `McpRuntimeClient` uses stateless `server/discover`, verifies that Runtime supports `2026-07-28`, sends required per-request metadata, and binds method and Tool identity through headers. It rejects unexpected Session creation.

The legacy Session decoder remains because retained deployments and historical evidence may still require it. It is not used by default and must not become durable Task state.

## Known limits

- Host has no durable Goal event stream or Goal commitment object; Goal coordination is a snapshot over Task revisions.
- Host MCP intentionally exposes only the H-C1 external-continuity surface plus bounded Task discovery; it is not a general remote Host administration API, Runtime proxy, scheduler, or cognition endpoint.
- `WorkingCheckpoint.runtime` remains a single Runtime Workspace navigation hint rather than a copied physical-state graph. Runtime currently preserves closed-Workspace source identity in its tombstone but `workspace.get` reports a closed identity as not found; durable cross-session navigation after Workspace closure therefore requires a Runtime-owned projection improvement rather than Host shadow state.
- Reverse-proxy deployment keeps the listener on loopback and requires one explicit canonical HTTPS `public_origin`; Host extends only the MCP SDK Host/Origin allowlist and keeps DNS-rebinding protection enabled.
- The canonical remote-auth pattern matches Runtime: static local Bearer remains valid, while optional `trust_cf_access` admits a non-empty Cloudflare Access assertion only under the explicit loopback + Access-protected Tunnel deployment precondition.
- The current official MCP SDK path publishes typed input schemas and structured success/error content, but does not publish explicit output schemas for these `CallToolResult` tools without coupling Host to SDK-private output-model plumbing. H-C2 leaves that as a measured Agent-UX limitation rather than adding a second schema system.
- Host authority creation is a single-operator bootstrap: initialize the state root once before concurrent CLI/MCP consumers. H-C1/H-C2 concurrent adoption/checkpoint guarantees apply to an initialized authority, not simultaneous first-time SQLite schema creation.
- Workload-specific hosts remain explicit state machines rather than a generic Effect lifecycle.
- A successful Runtime Job or foreign executor Run is not Task completion.
- External executor completion is retained only as a proposal until a Host-owned workload verifies and decides the Task outcome.
- Recovery never authorizes blind redispatch after uncertain delivery.
- Provider calls are external to Host. Host persists only a provider-neutral `CognitionWorkRequest` before execution and admits returned semantic results plus `CognitionExecutionEvidence`; it has no Provider-call schema or Provider execution package.
- Domain systems must supply authoritative world-state and verification semantics.
- Full-history Doctor is intentionally slower and explicit.
- Live evidence covers the tested local Runtime and filesystem path, not host reboot, kernel failure, network partition across machines, or distributed scheduling.
- Several workload modules are large, but forced splitting is deferred while each still owns one coherent vertical lifecycle and tests provide stronger safety than cosmetic decomposition.

## Live state is machine-owned

This document does not copy current Task counts, schema files, active leases, object bytes, deployment revisions, or Runtime health. Query them:

```bash
ordivon-host --state-root /var/lib/ordivon/host inspect
ordivon-host --state-root /var/lib/ordivon/host doctor
ordivon-host --state-root /var/lib/ordivon/host doctor --history
ordivon-host --config /etc/ordivon/host.toml \
  --state-root /var/lib/ordivon/host doctor --runtime
```

## Reopen conditions

Revisit this status when Host gains a durable Goal stream, remote/distributed state ownership, a new public workload abstraction, a hostile-code boundary, Python support changes, the legacy Runtime decoder is deleted, or the public interface reaches a declared 1.0 contract.
