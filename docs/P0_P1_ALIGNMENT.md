# P0/P1 Host stack alignment

This document records the first implementation pass after the pre-H7 first-principles audit. It supersedes conflicting architectural descriptions in historical closure documents while preserving those documents as evidence of the earlier boundary.

H7 remains frozen. This change hardens the existing Host and aligns its real source-change and cognition paths with Ordivon Computing, Runtime, Git, Provider, and Authority ownership.

## Bound local revisions

- Host base: `930374efb48622bcb67c7aa4b552dfc7a1ed4b31`
- Computing source-change protocol: `fb213ceac5c326e79b53a3122e320b976869e1af`
- Runtime base: `2d4141b30ebabd9119ed4e9547c36759cb5b7b77`

The final Host and Runtime revisions are supplied by their local commits. No remote push, merge, deployment, or production Runtime replacement is part of this pass.

## P0 correctness changes

### Journal and schema

- Every Task stream must have one materialized Task projection. A Task stream without a projection now prevents Journal open.
- A non-empty SQLite database without `host_metadata` is rejected instead of being silently initialized as a Host Journal.
- State inspection uses SQL aggregate counts and no longer truncates Task totals at the CLI list bound.
- Schema migration backups are compared with a fresh online backup of the current source database. An existing valid-version but stale backup is rejected.

### Lease identity

Default workload owners are process-instance identities containing the component, PID, and a UUID. Component labels are no longer reused as lease owners across processes. Stream revision comparison remains the final commit authority; leases remain short coordination records rather than distributed locks.

### Terminal mutation failure

A guarded mutation Runtime Job ending in `failed`, `timed_out`, or `cancelled` is now persisted as an observed terminal failure. The Task enters `BLOCKED`, closes its disposable Workspace, writes a failed TaskOutcome, and reaches `FAILED`. It no longer remains indefinitely in `WAITING` after an exception escapes.

### Durable object versions

Durable semantic decoders now dispatch explicitly by `(kind, schemaVersion)`. Unknown versions raise `UnsupportedObjectVersion`. Read plans, guarded mutation plans, code-change plans, and compiled contexts use this codec boundary.

This is a framework for decoder/upcaster evolution, not a claim that every existing CAS kind already has multiple versions.

### Source-change verification

Runtime `workspace.diff` now returns structured Git facts:

```text
changedPaths
addedPaths
modifiedPaths
deletedPaths
renamedPaths
untrackedPaths
truncated
```

The current CodeChange workload replaces existing files only. Verification therefore requires the exact planned path set to equal both `changedPaths` and `modifiedPaths`, with no additions, deletions, renames, untracked files, or truncation. Raw diff text remains evidence, but path admission no longer depends on substring matching.

`CodeChangeVerificationReceipt.accepted` now includes both file digest verification and structured diff acceptance.

### Durable secret boundary

`ExecutionCheck.env` rejects secret-like variable names. Secrets must not be serialized into Host CAS as ordinary environment values. Runtime SecretRef resolution is deliberately not invented in this phase; workloads needing secrets remain unsupported until Runtime and Authority expose a reviewed SecretRef contract.

## P1 stack alignment

### Logical repository identity

New code-change plans use:

```text
RepositoryRef
  repositoryId: repository:...
  revision: 40-character Git object ID
```

A Host repository resolver maps logical identities to trusted-local absolute paths only at the Runtime Workspace boundary. The semantic plan no longer publishes the physical path.

CodeChangePlan schema v2 contains `repository`. Schema v1 plans can be decoded for historical recovery and receive a deterministic legacy repository identity while retaining the old path only as a non-serialized local locator.

### Computing SourceChange Effect

Ordivon Computing now defines:

```text
anc.source.change.v1
SourceChangeSpec
SourceFileChange
source-files-structured-diff-and-checks.v1
```

The Host source-change path is now:

```text
CodeChangePlan
→ SourceChangeSpec
→ EffectEnvelope
→ CapabilityDecision
→ current Runtime ToolContract
→ EffectBinding
→ durable Dispatch
→ Runtime workspace.execPlan
→ structured observation and verification
```

Effect, Binding, Authority decision, Runtime request, and Dispatch are separate immutable Host objects with cross-identity validation during recovery.

### Explicit trusted-local Authority

`TrustedLocalAuthorizer` is intentionally broad but explicit. Its default policy permits only:

- `principal:local-owner`;
- action `anc.source.change.v1`;
- repository-scoped world objects.

The resulting `CapabilityDecision` is persisted and bound into the Dispatch. Another principal or action fails before Dispatch preparation.

This is a real admission decision under the current single-owner threat model, not a multi-user authorization service.

### Provider Gateway and durable invocation

Physical Codex and Hermes invocation moved behind `providers.gateway`. Cognition no longer owns subprocess, HOME, credential, or provider-specific mechanics directly. Existing Adapter names remain compatibility aliases.

The durable cognition path is now:

```text
CompiledContext
→ ModelInvocationIntent persisted
→ Task WAITING
→ Gateway invocation outside Task lease
→ ModelDecision + gateway evidence
→ ModelInvocationObservation
→ deterministic DecisionAdmission
→ ModelInvocationReceipt
→ selected READY frontier
```

A gateway failure leaves the prepared Invocation intent as the Task head. A fresh process may inspect and retry that exact semantic invocation. Provider session state remains disposable.

This phase introduces an internal Gateway port, not a networked Provider service. A separate Model Runtime should be created only after a second physical transport or deployment boundary proves that process separation is necessary.

### Explicit Runtime MCP profile

The current HTTP client is declared as:

```text
ordivon.mcp-stateless-http.v1
protocol: 2025-06-18
stateful sessions: false
server-initiated requests: false
multi-message SSE: false
resumable SSE: false
```

The client validates the negotiated protocol version, caches initialization, and rejects multiple SSE data messages. This removes the prior ambiguity between a deliberately stateless Ordivon Runtime profile and complete stateful MCP support.

> **H-series successor note:** production Runtime later adopted the standard MCP Session lifecycle. `docs/H_SERIES_OPEN_PROPOSAL.md` and the current `ARCHITECTURE.md` are authoritative for Session identity, `notifications/initialized`, and empty SSE heartbeat handling. This paragraph remains the historical P1 boundary.

## Compatibility boundary

- Existing terminal Host history remains readable.
- CodeChangePlan v1 is upcast to the logical repository model.
- A pre-P1 **prepared CodeChange Dispatch** lacks a real Computing SourceChange Effect, EffectBinding, and CapabilityDecision. These records cannot be honestly fabricated after the fact. Such an in-flight Task must either finish under the old binary or be explicitly abandoned and recreated under the new plan.
- No automatic database rewrite manufactures Authority evidence.
- GuardedMutation and Read plans remain legacy physical-path workloads. CodeChange is the aligned source-change main path; migration of Read belongs to a later reconciler pass, not P1 expansion.

## Deliberately deferred

- Runtime SecretRef resolution;
- networked Model Gateway;
- generic Task reconciler conversion;
- migration of deterministic Read to RepositoryRef;
- full history semantic Doctor;
- daemon, wakeup service, scheduler, DAG, H7, multi-Agent, or distributed Host;
- production deployment and new live receipts against the updated Runtime binary.

## Acceptance

The implementation is accepted locally only when:

- Computing protocol tests pass;
- Runtime format, Clippy, and full workspace tests pass;
- Host compileall, Ruff, complete unittest suite, wheel build, and `git diff --check` pass;
- all three workspaces are clean at explicit local commits;
- no push, merge, deploy, or H7 implementation occurs.

## P2 successor note

`docs/P2_P3_EXPLORATION.md` records the subsequent implementation. P2 migrated deterministic Read to `RepositoryRef` and trusted-local Authority, added a conservative one-shot Task Reconciler, and added explicit full-history Doctor validation. The P1 deferred list above remains the historical decision at revision `29b409f`; the P2 document is authoritative for these completed items.
