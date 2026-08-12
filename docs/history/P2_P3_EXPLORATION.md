# P2 recovery closure and P3 cost-benefit exploration

> **Historical P2/P3 exploration record:** This document preserves stage-specific decisions, measurements, or provenance. It is not a current Host architecture or operations source. Use [`../README.md`](../../README.md), [`../ARCHITECTURE.md`](../../ARCHITECTURE.md), [`OPERATIONS.md`](../OPERATIONS.md), and [`authority.md`](../authority.md) for the active boundary.

This phase begins at Host revision `29b409f73cfc5e601fe3fc1204a888431cba71ff`. The retained P2 implementation is revision `34c8ca72144248be6b2c5dc957e84b67d61b88b8`.

The phase does not start H7. It completes the remaining repository-read alignment, adds a conservative one-shot recovery surface, and adds an optional historical semantic Doctor. P3 candidates were explored only far enough to measure whether their expected benefit justified another durable subsystem.

## P2 retained implementation

### Repository-aligned deterministic read

`ReadTaskPlan` schema v2 replaces the durable absolute `sourceRepo` with:

```text
RepositoryRef
  repositoryId
  revision
```

The Host resolves the logical repository only when opening the Runtime Workspace. Historical v1 plans remain decodable through a deterministic legacy repository identity; their physical path is retained only as a non-serialized local recovery locator.

The read Effect now targets a repository-file world object, carries repository revision and path in canonical input, and receives a persisted trusted-local `CapabilityDecision`. The read path is therefore aligned with the P1 source-change boundary:

```text
logical repository identity
→ Computing object-read Effect
→ CapabilityDecision
→ ToolContract and EffectBinding
→ Runtime workspace.read
→ independent digest verification
```

### Conservative one-shot Task recovery

P2 adds `assess_recovery()` and `TaskReconciler`. This is not a scheduler or daemon. It classifies one durable Task head and may perform at most one already-authorized recovery step.

Automatically allowed:

- advance a deterministic Read Task at a valid READY open/read/close frontier;
- observe the original keyed Runtime Job for a Mutation or CodeChange Task already waiting at its reconcile frontier.

Never automatic:

- create a Task or Effect;
- prepare or redispatch a Runtime request;
- invoke a Provider;
- admit a model decision;
- advance an unrecognized or malformed frontier;
- infer success from absence of a Runtime Job.

Two uncertain-delivery windows have separate tests:

```text
Dispatch prepared, no Runtime Job exists
→ no-op; zero physical deliveries

Runtime Job admitted, response lost
→ observe the original keyed Job; one physical delivery
```

CLI surfaces:

```text
ordivon-host task assess TASK_ID
ordivon-host task reconcile TASK_ID [--wait-ms N]
```

`task reconcile` performs the local assessment before loading Runtime credentials. A non-automatic Task therefore returns an explicit no-op without requiring a reachable Runtime.

### Optional complete-history Doctor

Normal startup and normal Doctor validate the current projection, all referenced CAS bytes, event continuity, and each current Task head. They deliberately do not decode every historical event payload.

`ordivon-host doctor --history` additionally validates every Event row against its immutable payload:

- event kind;
- Task identity;
- stream revision;
- recorded timestamp and projected timestamp;
- known CAS references;
- Effect/Binding identity;
- Authority/Effect capability identity.

The deep check detected a corruption class that normal Doctor did not: an old Event row rebound to a newer, still valid and still admitted Event payload. This preserves all SQLite and CAS byte invariants while falsifying historical semantics.

The history check is explicit. It adds no work to normal startup or normal Doctor.

## Measured cost

The retained implementation changed 12 files with 973 insertions and 42 deletions, including tests. This is material rather than trivial, so retention requires concrete failure coverage.

At 1,000 Tasks × 100 Events:

| Metric | Measurement |
|---|---:|
| Events / CAS files | 100,000 |
| Normal full Doctor | 10,328 ms |
| Historical semantic scan alone | 8,578 ms |
| Doctor with `--history` | 18,520 ms |
| Added deep-check latency | 8,192 ms |
| Total latency ratio | 1.793× |

The additional cost is dominated by reading and decoding 100,000 independent CAS files. It is not dominated by the SQLite Event query.

Decision: retain the explicit deep check, but do not add a history index, snapshot table, pack file, Merkle checkpoint, or schema v4. An approximately 18.5-second manual integrity operation at this scale is acceptable; another migration and storage format is not justified.

Evidence:

```text
evidence/p2-p3-cost-benefit-34c8ca7-20260728.json
sha256:9d863c6cfcee00b83e2915554bb16af309c0476fe5f0a8c327bcf1d8e195ab89
```

## P3 explorations stopped

### Host daemon and durable wakeups

A one-pass assessment of 1,000 terminal Tasks took 79.572 ms. Scanning performance is therefore not the blocker.

The blocker is missing semantics: there is no retained wakeup owner, deadline contract, recurrence contract, or demonstrated population of Tasks requiring automatic action. Reintroducing a `wakeups` table and a long-running process would add service lifecycle, polling, duplicate-work, shutdown, upgrade, and recovery states without a real workload.

Decision: stop. Keep the one-shot CLI and gather actual recurring recovery demand before considering a daemon.

### Networked Model Gateway

A temporary loopback benchmark of 1,000 small JSON invocations measured:

| Path | Total | Per call |
|---|---:|---:|
| Direct in-process decoding | 1.743 ms | 0.0017 ms |
| Loopback HTTP | 1,064.204 ms | 1.0642 ms |
| Added HTTP overhead | 1,062.461 ms | 1.0625 ms |

The latency is negligible relative to a real model invocation. Performance therefore neither requires nor forbids a network service. The decisive fact is ownership: Host currently has one physical Provider transport family, local CLI subprocesses. A network service would add authentication, deployment, availability, retry, version negotiation, and service recovery without enabling a second real transport or independent deployment requirement.

Decision: stop. Retain the internal `ModelGateway` port; split a process only when a second physical transport or deployment owner exists.

### Runtime SecretRef

Runtime currently accepts durable plain-string environment maps. It has no SecretRef schema, credential store, systemd credential injection path, Authority rule, redaction contract, or replay semantics.

A correct implementation would require coordinated Computing, Host, Runtime, operational configuration, log-redaction, backup, and fault-injection changes. No retained Host source-change workload currently requires a secret-bearing execution check.

Decision: stop. Continue rejecting secret-like durable environment names. Reopen only for a concrete workload and design the cross-stack contract first.

### Historical index or snapshot

The 100,000-Event measurement shows the deep-check bottleneck is independent CAS file reads, not Event lookup. A SQL index would not materially reduce the measured cost. Snapshotting or packing would change the storage format and recovery model for an operation that already completes in under 20 seconds.

Decision: stop. Reconsider only if deep Doctor becomes a frequent operational action or history reaches a scale where the measured latency materially blocks recovery.

## Net assessment

P2 is retained because each added mechanism closes an observed boundary without adding a permanent service or database schema:

- Read no longer leaks a physical repository path into new durable plans.
- Authority now covers both retained repository workloads.
- Recovery can be asked and executed uniformly without permitting redispatch.
- Historical semantic corruption has an explicit detection path.

P3 is paused because none of the explored candidates has a current benefit clearly greater than its lifecycle and migration cost.

No P3 prototype code, daemon, Runtime credential mechanism, network service, history index, or schema migration remains in the tree.
