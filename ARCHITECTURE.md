# Ordivon Host v0 boundary

The incubator exists to falsify and reduce the Host architecture before an independent repository is created.

## Ownership

- **Host** owns goals, task nodes, Host events and projections, context compilation, model invocation, candidate decisions, Effect proposals, Tool bindings, verification receipts, and task outcomes.
- **Runtime** owns workspaces, committed physical jobs, Runtime attempts, process state, retained output, artifacts, cancellation, and physical recovery.
- **Computing** owns protocol definitions, reference behavior, conformance vectors, experiments, and evidence.
- **Provider sessions** are replaceable cognition transports and never own task continuity.
- **Git** owns repository history and source revisions.

## v0 constraints

- one SQLite Host journal;
- one materialized task projection derived in the same transaction;
- immutable objects written before journal references are committed;
- at most one active writer per task;
- graph-shaped task data with a single running node;
- deterministic progress before model invocation;
- no automatic redispatch after an uncertain external effect;
- TaskCapsule is an export/checkpoint, not the primary database;
- no independent Semantic Journal;
- no multi-Agent, distributed scheduler, provider router, Web UI, or general workflow DSL.

## Durable state protocol

The Host state root contains exactly two durable mechanisms:

```text
objects/       typed, immutable, content-addressed JSON envelopes
host.sqlite3   event admission, stream heads, materialized projections,
               graph indexes, Runtime links, wakeups, and leases
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

This slice deliberately does not use the research Authority/Attestation chain or admit a reusable Fact. A verified read completes with a `VerificationReceipt` and `TaskOutcome`; Fact promotion remains a separate cross-Task decision.

## Persistent multi-candidate cognition

Cognition is split across two durable boundaries:

```text
compile bounded Context
→ persist Context and release the Task lease
→ invoke any replaceable Provider outside the lease
→ reacquire the Task lease
→ reread current world, completed Effects, and unresolved Dispatches
→ deterministically admit or reject the exact ModelDecision
→ persist the decision, admission, and selected frontier together
```

A `ContextBlock` binds its typed payload to a source digest, priority, freshness class, and required/optional status. The compiler always includes required blocks or fails; optional blocks are selected deterministically under a token budget. The resulting `CompiledContext` carries two to eight exact candidate actions, forbidden completed Effects, unresolved Dispatches, and a content digest.

The Cognition boundary preserves these invariants:

- Provider sessions, transcripts, tools, and hidden reasoning are not Task state;
- the Provider receives one immutable `CompiledContext` and returns one structured `ModelDecision`;
- a Context can be recovered by a fresh Host process before Provider invocation;
- the Task lease is not held while an external model call runs;
- a decision for another Context or an invented action is rejected;
- action, Effect, Binding, Dispatch, and world identities must be copied exactly;
- current world drift and newly completed Effects are rechecked at admission time;
- an unresolved Dispatch blocks another Effect or premature completion;
- observing a Dispatch must target the exact unresolved Dispatch;
- if another entry point advances the Task during model execution, the old decision is superseded;
- Provider failure leaves the prepared Context as the durable Task head;
- Codex runs ephemerally and read-only; Hermes runs in an isolated HOME with no Host tools, MCP servers, memory, or persistent session snapshots.

The Host may compare multiple Provider decisions against the same persistent Context before admitting one. Provider agreement is evidence about replaceability, not authority to bypass admission.

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

The v0 workload deliberately allows only one root-level file created with `O_EXCL`. This is a falsifiable recovery slice, not a general mutation DSL or shell workflow engine.

## Promotion rule

Code moves from `research/experiments` into `packages` only after an invariant has deterministic conformance coverage. Host code remains under `incubation/` until a real guarded mutation and asynchronous Runtime recovery workload both succeed.
