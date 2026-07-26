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

## Promotion rule

Code moves from `research/experiments` into `packages` only after an invariant has deterministic conformance coverage. Host code remains under `incubation/` until a real guarded mutation and asynchronous Runtime recovery workload both succeed.
