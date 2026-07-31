# Ordivon Host architecture boundary

This architecture was falsified and reduced in the Computing incubator before the independent repository was created. The extracted repository retains the proven v0 boundary while product and operational work proceed as separately reviewable changes.

`docs/P0_P1_ALIGNMENT.md` records the post-audit correctness and stack-alignment changes. `docs/H_SERIES_OPEN_PROPOSAL.md` records the first open-cognition extension. Where this historical derivation conflicts with either later record, the later evidence-backed record is authoritative. A universal H7 scheduler remains frozen.

## Ownership

- **Host** owns Goals, Tasks, Task Contracts, Host events and projections, Context compilation, ModelInvocation identity, Harness Assignments and native Run intent, Assignment-scoped Tool Grants, proposal compilation or closed-choice admission, Effect commitments, Tool bindings, retained Run evidence, verification receipts, participant-routed decisions, and Task outcomes.
- **Runtime** owns Workspaces, committed physical Jobs, Runtime Attempts, process state, retained output, Artifacts, cancellation, and physical recovery.
- **Domain systems** own authoritative world state, transition rules, domain coordination policy, and domain-specific verification sufficiency.
- **Computing** owns promoted protocol definitions, reference behavior, conformance vectors, experiments, and evidence.
- **Provider and MCP sessions** are replaceable transport state and never own Task continuity.
- **Git** owns repository history and source revisions.

The Host controls durable work and external commitments. It does not own model intelligence, domain truth, physical execution, or a permanent hierarchy among participants.

## v0 constraints retained

- one SQLite Host journal;
- one materialized Task projection derived in the same transaction;
- immutable objects written before journal references are committed;
- at most one active writer per Task;
- Task-local frontier state with a single running node;
- deterministic progress before model invocation;
- no automatic redispatch after an uncertain external Effect;
- TaskCapsule is an export/checkpoint, not the primary database;
- no independent Semantic Journal;
- no distributed scheduler, provider router, Web UI, or general workflow DSL.

## Durable state protocol

The Host state root contains exactly two durable mechanisms:

```text
objects/       typed, immutable, content-addressed JSON envelopes
host.sqlite3   event admission, stream heads, materialized projections,
               object validation, schema migrations, and short leases
```

A Task state change follows this order:

```text
1. canonicalize the typed event payload;
2. fsync the immutable object and its directory entry;
3. acquire a SQLite IMMEDIATE transaction;
4. compare the expected stream revision;
5. admit the object reference, event, stream head, and Task projection;
6. commit them together.
```

The event payload binds the complete resulting `TaskProjection`. A new Host process validates every referenced CAS object and rebuilds each Task from its event head before accepting work. The materialized projection is therefore a checked cache, not an independent source of truth.

Expected consequences:

- a failed SQLite commit may leave an unreferenced immutable object, but cannot leave a partial semantic commit;
- two writers racing the same stream revision cannot both commit;
- a repeated identical event identity is idempotent;
- a repeated event identity with different payload or resulting projection fails closed;
- event-history gaps, stream-kind drift, projection drift, missing objects, and object corruption prevent startup;
- a lease coordinates the active writer but does not replace stream revision CAS.

## Minimal HostKernel

Read, cognition, guarded mutation, and open proposal workloads share one mechanical transition kernel. The Kernel owns only the invariants independently repeated by those workloads:

```text
read current Task projection and event head
→ acquire the short Task lease
→ recheck revision, state, frontier, and projection equality
→ let the workload build semantic objects and the next event payload
→ append at most one event and resulting projection with revision CAS
→ release the lease on success or failure
```

`HostKernel` and `LockedTask` therefore own lease lifetime, monotonic timestamps, current-state admission, one-transition-per-lock enforcement, and atomic event/projection commit. Workload code still owns Context compilation, proposal lowering or candidate admission, Effect and Binding construction, Runtime calls, delivery classification, reconciliation, independent verification, and TaskOutcome semantics.

The Kernel preserves these boundaries:

- it is not a workflow DSL, scheduler, DAG engine, provider router, Runtime adapter, or policy engine;
- it never invokes a Provider or Runtime Tool;
- workload-specific public exceptions are preserved through explicit error mapping;
- external execution and Provider invocation remain outside the Task lease;
- deterministic coordination and verification operations may remain inside a short transition until real latency data proves a need to split them;
- one locked transition may append at most one Host event;
- existing workloads remain regression specifications for Kernel behavior.

## Deterministic Runtime read slice

The first vertical slice contains no model call. It advances one durable frontier per Host step:

```text
open-or-reconcile Workspace
→ bind and execute workspace.read
→ verify returned content independently
→ close-or-reconcile Workspace
→ completed TaskOutcome
```

The lifecycle Tools (`workspace.get`, `workspace.open`, and `workspace.close`) are deterministic coordination operations. `workspace.read` is the only semantic Effect in this slice and produces separate immutable objects for the Effect, current Tool contract snapshot, EffectBinding, Observation, VerificationReceipt, and terminal TaskOutcome.

The slice preserves these invariants:

- every frontier transition is committed before the next Runtime action;
- a new Host process can advance the next frontier without provider or process memory;
- a stable Workspace identity reconciles a crash after `workspace.open` but before Host commit;
- the Runtime Tool catalog is rediscovered and must match the bound catalog before the read;
- returned content is hashed independently and must match the Runtime digest;
- a failed read or failed verification leaves the Task at its prior revision;
- closing an already absent Workspace is reconciled as complete;
- presentation-only Tool metadata does not change catalog identity, while schemas and execution metadata do;
- semantic objects are admitted into the Host Journal transaction as references, not copied into the projection.

This slice deliberately does not admit a reusable Fact. A verified read completes with a `VerificationReceipt` and `TaskOutcome`; Fact promotion remains a separate cross-Task decision.

## Closed-choice deterministic cognition profile

The first cognition profile is split across two durable boundaries:

```text
compile bounded Context with two to eight exact CandidateActions
→ persist Context and release the Task lease
→ invoke any replaceable Provider outside the lease
→ reacquire the Task lease
→ reread current world, completed Effects, and unresolved Dispatches
→ deterministically admit or reject the exact ModelDecision
→ persist the decision, admission, and selected frontier together
```

This profile remains appropriate when the legal action set is already closed, for deterministic fixtures, and for recovery tests. It is no longer treated as the only general cognition interface.

The profile preserves these invariants:

- Provider sessions, transcripts, tools, and hidden reasoning are not Task state;
- a Context can be recovered by a fresh Host process before Provider invocation;
- the Task lease is not held while an external model call runs;
- a decision for another Context or an invented action is rejected;
- action, Effect, Binding, Dispatch, and world identities must be copied exactly;
- current world drift and newly completed Effects are rechecked at admission time;
- an unresolved Dispatch blocks another Effect or premature completion;
- if another entry point advances the Task during model execution, the old decision is superseded;
- model invocation intent is durable before the external Gateway call;
- Provider failure leaves the prepared Invocation as the durable WAITING Task head.

## Open proposal cognition profile

The first open profile removes `allowedActions` from Context:

```text
Goal + Context + ResourceBindings + capability profile
→ ActionProposal from a replaceable model
→ Host checks identity, revision, ownership, reversibility, and consequence
→ lower, create DecisionRequest, or reject
```

The model may state semantic intent, target, rationale, preconditions, affected participants, expected result, candidate method, and verification plan. It may not assign Effect, Binding, Dispatch, Runtime request, capability grant, or completion identities.

Only one lowerer is currently proven:

```text
private reversible repository-file observation
→ existing DeterministicReadHost
→ verified workspace.read
```

Shared, foreign-owned, irreversible, and unknown-consequence proposals do not self-authorize. They create a `DecisionRequest` addressed to the responsible participant. A human is one possible participant, not a hard-coded universal recipient.

The profile proves:

- Context contains resources and constraints but no prebuilt action menu;
- ModelInvocationIntent is durable before the model call;
- the Provider runs outside the Task lease and retains no Task continuity;
- stale resource revisions and wrong profiles are rejected structurally;
- a child Task committed before the parent resolution is reused after recovery;
- repeated admission after response loss returns the retained receipt;
- parent completion follows the verified child TaskOutcome;
- MCP Session identity remains disposable transport state.

The profile is Host-local and experimental. No universal planning language or promoted Protocol object is implied.

## Native Harness Run contract

The first-party bare-model Harness retains the H1–H5 Host boundary but closes its native control plane before execution:

```text
TaskContract + TaskAttemptDescriptor
→ CompiledContext CAS
→ HarnessAssignment + ToolGrant + NativeHarnessRunContract
→ Provider / Runtime activity
→ persisted Trace + ToolObservations + conclusion + HarnessRunReceipt
→ Host-derived CompletionProposal
→ persisted CompletionVerification
→ CompletionDecision / TaskOutcome
```

One native Assignment generation authorizes one durable Harness Run identity. The Assignment event commits the Task Contract, Tool Grant and Run Contract object references before any Provider or Runtime call. A fresh Host can therefore reconstruct the Run identity and authority without Provider process state.

The Runtime catalog expresses connected physical capability; `ToolGrant` expresses the smaller Assignment-authorized model surface. Native Runs expose only granted Tools and paths. Prebound `run_check(checkId)` is preferred to opaque execution. Generic `run_in_workspace` requires an explicit opaque-exec grant. Observation Jobs and Artifacts remain accessible only after their identities have appeared in the current Run.

The Host persists the complete native Trace, each Tool Observation, the model Run conclusion and the v2 Run receipt. Job and Artifact references must be derivable from those Observations. Model-declared evidence is advisory; the Host compiles the CompletionProposal from retained Run objects. Independent verifier output is retained as a `CompletionVerification` object before TaskOutcome.

The first-party capability manifest declares cancellation between Turns rather than claiming in-flight Provider interruption. Provider Session continuation, parallel Tools, compaction, subagents and effectful external actions remain outside this verified slice.

### Native Run fault and abandonment semantics

A committed native Run Contract is not automatically replaceable merely because no `HarnessRunReceipt` exists. Absence of a receipt may mean the process died before any work, after a read, after a Workspace mutation, or after starting a process whose response was lost. OH5 therefore separates recovery evidence from abandonment authority:

```text
NativeHarnessRunContract with no receipt
→ NativeRunRecoveryAssessment
→ safe read-only cleanup?
   ├─ yes → NativeRunAbandonment → replacement generation allowed
   └─ no  → Task BLOCKED with explicit UNKNOWN → replacement forbidden
```

The recovery controller re-discovers the Runtime catalog and attempts idempotent forced Workspace closure. Catalog drift is retained as evidence but is not itself an Effect UNKNOWN. Cleanup transport failure remains Workspace UNKNOWN and may be reassessed later.

Automatic abandonment is limited to a read-only `ToolGrant` and a Workspace proven closed, already absent, or not applicable. A Grant containing `mutate_workspace`, `run_check`, or opaque `run_in_workspace` retains UNKNOWN after process loss even if the Workspace is later closed, because an unrecorded mutation or process effect may already have occurred. No missing Job inference converts that uncertainty into safety.

A recorded `runtime_unknown` receipt likewise blocks replacement. Other recorded terminal stops, including exact Provider timeout, transport failure, rejection and unavailability, may be replaced by a new Assignment generation only when the Run is read-only or has no Tool Observation, and must retain the same Workspace until a durable cleanup or release disposition exists. A recorded mutation/process-capable Observation requires verification, completion or a future explicit continuation protocol before replacement. Provider faults remain separately classified and are never automatically retried.

Recovery and abandonment are Host CAS objects and Host events. Once recovery or abandonment advances the Task, a late result from the old process is superseded before Trace, Observation or receipt objects are written. Handoff exposes `reconcile-current-harness-run-unknown`, `abandon-current-harness-run`, or `replace-harness-assignment` according to the retained evidence.

## Capability and consequence separation

Capability profiles express the semantic and resource reach available to a participant. Consequence admission decides whether that reach may be committed for the current proposal.

```text
physical credentials
+ capability profile
≠ automatic permission for every external consequence
```

`owner_trusted` admits Agent semantic actions on private repository resources. `public_bounded` admits repository-file observation only. Both still pass through proposal consequence classification and domain verification.

## Guarded mutation and uncertain delivery

The first changing vertical slice uses one keyed `workspace.exec` Dispatch to create one exact proof file in a disposable Runtime Workspace. The Host persists the Effect, current `workspace.exec` Tool contract, EffectBinding, stable `clientRequestId`, and Dispatch intent before crossing the Runtime boundary.

```text
open-or-reconcile Workspace
→ persist Effect + Binding + Dispatch intent
→ release the Task lease
→ deliver workspace.exec once
→ classify a lost response as UNKNOWN
→ find the original Job by clientRequestId
→ observe that exact Job to terminal state
→ verify the changed file independently with workspace.read
→ force-close the dirty disposable Workspace
→ completed TaskOutcome
```

The mutation boundary preserves these invariants:

- the external call never occurs before its exact Dispatch identity and request digest are durable;
- the Task lease is not held during `workspace.exec` or asynchronous Job observation;
- a transport or protocol failure with uncertain commit state becomes UNKNOWN;
- an explicit `not_committed` Tool rejection remains distinguishable from UNKNOWN;
- a fresh Host process only searches for and observes the original keyed Runtime Job;
- failure to find that Job never authorizes automatic redispatch;
- one `clientRequestId` resolving to conflicting Job identities fails closed;
- Runtime success alone does not complete the Task;
- exact content and digest verification must succeed before TaskOutcome;
- the dirty test Workspace is force-closed only after evidence is retained.

The workload deliberately allows only one root-level file created with `O_EXCL`. This is a falsifiable recovery slice, not a general mutation DSL or shell workflow engine.

## MCP transport lifecycle

The production Runtime uses the standard MCP Session lifecycle:

```text
initialize
→ retain Mcp-Session-Id as in-memory transport state
→ notifications/initialized
→ bounded request/response operations
```

Empty SSE heartbeat data frames are ignored; multiple non-empty JSON-RPC messages remain outside the supported profile. MCP Session identity is never persisted in Goal, Task, Context, Effect, or recovery state. A new Host process establishes a new transport session and continues from durable Host and Runtime identities.

## Empirical recovery boundary

Repeated live work exposed mechanisms not visible from unit tests alone:

1. Runtime Job reconstruction should use an exact durable `clientRequestId` filter when published by the Tool contract rather than scanning all history.
2. `systemctl is-active` is not a readiness proof; recovery waits for both service activity and a successful MCP initialization.
3. Millisecond timestamps are ordering hints, not unique identities; live workload identities include an independent nonce.
4. Production MCP may emit an empty SSE heartbeat before the JSON-RPC response; ignoring the empty frame preserves strict single-response semantics.

These results prove the tested local systemd/SQLite/Workspace path only. They do not establish arbitrary host reboot recovery, remote Runtime recovery, database corruption recovery, distributed scheduling, or automatic redispatch safety for unkeyed Effects.

## Promotion rule

A Host-local concept moves toward shared Protocol only after at least two materially different workloads demonstrate the same non-bypassable invariant. Open ActionProposal, capability profiles, and participant DecisionRequest remain Host-local until that threshold is met.

A mechanism that adds ambiguity or cost without increasing accepted verified outcomes should be deleted rather than promoted.
