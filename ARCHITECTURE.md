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

## Promotion rule

Code moves from `research/experiments` into `packages` only after an invariant has deterministic conformance coverage. Host code remains under `incubation/` until a real guarded mutation and asynchronous Runtime recovery workload both succeed.
