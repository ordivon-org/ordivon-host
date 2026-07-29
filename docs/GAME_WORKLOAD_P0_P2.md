# Game workload P0–P2 implementation

Date: 2026-07-29
Status: implemented and locally accepted
Protocol source: `ordivon-computing@5c6e225b90f25d4a0e8e0f99bf7590ecbd7ce1a5`
Protocol profile: `host-workload-v1`, version `0.3.0`

## Purpose

This change extends the existing Runtime-oriented Host into an implementation-independent workload control plane that can also coordinate a deterministic Game World executor.

It does not turn Host into a workflow engine, DAG scheduler, organization simulator, mailbox runtime, or Game policy owner.

## P0 — durable Task identity

`TaskDescriptor` binds one Task identity to:

```text
taskId
goalId
workloadId
assigneeRef
providerPolicyRef
domainRef
configurationDigests
```

The semantic protocol digest and the Host CAS object digest are stored separately. A descriptor is referenced by every new executor-neutral lifecycle event while historical Tasks without descriptors remain readable through the existing compatibility inference.

No columns were added to `task_projection` and no Host database migration was required.

## P1 — executor-neutral lifecycle

The new lifecycle is:

```text
TaskDescriptor
→ semantic Effect + executor request
→ DispatchEnvelope persisted
→ Task WAITING
→ executor delivery outside Task lease
→ ObservationEnvelope
→ Task VERIFYING
→ VerificationReceipt
→ TaskOutcome
```

Implemented modules:

```text
src/ordivon_host/effects/
src/ordivon_host/executors/
```

`EffectExecutor` separates:

```text
deliver(dispatch, request)
observe(dispatch, request)
```

A `DeliveryUncertain` result keeps the Task in `WAITING`; reconciliation may only call `observe`. It never blindly redelivers the Effect.

`RuntimeEffectExecutor` adapts the existing Runtime client to the same port. Existing Runtime-specific EventKinds and durable objects remain supported for historical workloads; they were not renamed or rewritten.

New neutral events are:

```text
effect.dispatch-prepared
effect.outcome-unknown
effect.dispatch-observed
verification.recorded
task.outcome-recorded
```

## P2 — minimal Goal-scoped coordination

The Host now provides small Goal coordination primitives:

```text
HostJournal.tasks_for_goal(goalId)
GoalSnapshot
TaskRevisionRef
GoalCoordinatorHost.assert_current()
GoalCoordinatorHost.apply_verification_result()
```

A `TaskRevisionRef` freezes:

```text
taskId
revision
state
head payload digest
descriptor semantic digest
descriptor object digest
```

The Coordinator can therefore bind one domain plan to exact Actor Task heads without introducing Task nodes, Task edges, a scheduler, or a global distributed transaction.

`VerificationReceipt.resultItems` allows one joint Effect to advance multiple Actor Tasks independently. Result application is idempotent by deterministic Event identity. A fresh process may continue after only a subset of Actor Tasks has advanced.

## Ownership

### Host owns

- Task and Goal continuity;
- TaskDescriptor;
- Task revision and short lease;
- Context, Invocation, Decision, and admission contracts;
- Dispatch identity and uncertain-delivery reconciliation;
- Observation and Verification references;
- TaskOutcome;
- Goal-scoped revision snapshots and per-Task result application.

### Domain owns

- World state and transitions;
- Action candidates and proposal contents;
- compatibility and selection policy;
- authority policy;
- Message reachability;
- objective predicates;
- verification policy implementation;
- mission outcome.

The Host stores opaque domain objects and digests but does not interpret Game commands or choose a Team Tick subset.

## Storage compatibility

The active schema remains version 3. The historical tables:

```text
wakeups
runtime_links
task_edges
task_nodes
```

remain explicitly legacy-unused. P0–P2 do not restore or depend on them.

Historical Runtime EventKinds and object decoders remain intact. New executor-neutral Tasks use descriptors and neutral lifecycle objects; old Runtime workloads preserve their exact recovery path.

## Verified behavior

The added tests prove:

- every normative Protocol workload vector is consumed by Host;
- domain ownership is explicit;
- descriptor identity survives fresh-process reopen;
- legacy descriptor-less Tasks remain readable;
- World commit response loss recovers the original Effect without redispatch;
- verification and outcome semantic digests differ correctly from CAS object digests;
- three Actor Tasks plus one Coordinator can share one Goal;
- one VerificationReceipt can be applied independently and idempotently to each Actor Task;
- a fresh Host resumes after only part of the result set was applied;
- existing Runtime, Cognition, Mutation, CodeChange, recovery, backup, schema, and operational tests remain green.

Acceptance:

```text
Host tests:       138/138
Compileall:       PASS
Ruff:             PASS
Diff check:       PASS
Protocol pin:     5c6e225b90f25d4a0e8e0f99bf7590ecbd7ce1a5
Schema migration: none
```

## Deferred

P0–P2 do not add:

- a daemon or wakeup service;
- a general scheduler or DAG;
- multi-machine leases;
- a network Model Gateway;
- a Game-specific Host engine inside this repository;
- a Python/TypeScript sidecar transport;
- automatic migration of historical Runtime lifecycle objects;
- a cross-database transaction between Host and a domain World.

The next deletion or deployment step must be justified by measured removal of duplicate Game Host code or by a second production consumer requiring an independent Host process.
