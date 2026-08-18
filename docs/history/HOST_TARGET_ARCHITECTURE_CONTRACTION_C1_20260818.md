# Host Target-Architecture Contraction C1 — 2026-08-18

> Historical engineering closeout for the first deletion-first contraction pass after HDF0–HDF43 and Coordination F2. This document is evidence, not deployment authority. Canonical/deployed Host remains the owner of its actual release state.

## 1. Starting engineering facts

Frozen Foundation status remains unchanged:

- HDF0–HDF43 frozen;
- Primitive central Host ontology falsified;
- HDF44 not admitted;
- no FoundationReopenCondition triggered during this pass.

F2 closed the current independent Coordination-project hypothesis:

```text
coordination relations                real
generic relation grammar              transferable
consumer-local relation ownership     proven
generic Coordination read surface     not admitted
independent Coordination project      falsified at current engineering evidence
```

The contraction question is therefore no longer “what Coordination capability should Host add?” It is:

> Given an admitted Continuity Core and consumer-local relation ownership, which current Host surfaces are still owned, which are compatibility surfaces, which are experimental/historical, and which are deletion-proven residue?

## 2. Revalidated current production boundary

Fresh `host.status(detail=history)` on 2026-08-18 reported:

- deployed revision `8d7e58a0511734a454805e29d10e7d3bb754d2da`;
- MCP surface version 2;
- exactly 6 Tools:
  - `host.status`
  - `task.observe`
  - `task.list`
  - `task.resume`
  - `task.adopt`
  - `task.checkpoint`
- `runtimeProxy=false`;
- Journal schema 5;
- 430 Tasks at observation, 375 terminal / 55 active at the later history observation;
- 0 leases;
- SQLite, permissions, Journal invariants, CAS references/orphans, leases and full retained Event history all healthy.

Current source `/root/projects/ordivon-host` was also exact clean `8d7e58a...`, matching deployment before this isolated candidate was created.

Therefore the contraction baseline is a healthy current production system, not an emergency repair.

## 3. Actual external consumers

A local source-wide scan across `/root/projects/ordivon-*` distinguished exact package imports from name/document references.

### 3.1 Direct production Python package consumer

Current direct `ordivon_host` Python imports were found only in **Ordivon World** production source.

World production consumes:

- `EventKind`;
- `HostExtensionPort`;
- `TaskRevisionMismatch`;
- `ArtifactRef`;
- `DispatchEnvelope`;
- `ObservationEnvelope`;
- `StateRef`.

World tests additionally exercise `HostKernel`, `HostStorage`, `ExternalContinuityHost`, `WorkingCheckpoint` and related integration types.

World does **not** consume:

- `GoalCoordinatorHost`;
- `GoalSnapshot`;
- `TaskRevisionRef`;
- `CoordinationError`;
- `CoordinationSuperseded`.

### 3.2 Harness

Current Harness base remains Host-package-free.

Harness has a real `OrdivonHarnessExternalExecutorAdapter`, but it is deliberately duck-typed against the historical Host foreign-Run protocol rather than importing `ordivon_host`. This preserves independent Harness authority.

Implication:

- Host `external_executor.py` is not Continuity Core;
- it is also not yet zero-evidence residue, because a corresponding current Harness adapter/wire contract still exists;
- classify it as **compatibility-only / contraction candidate requiring coordinated consumer retirement**, not immediate deletion.

### 3.3 Security

Security evaded a simple static import scan because it dynamically imports:

```text
ordivon_host
ordivon_host.cognition
```

inside the P0-B Host-assigned experimental actor path.

It then uses HostStorage / HostKernel / EventKind plus cognition surface semantics.

Therefore `ordivon_host.cognition` is **not** safe to delete in C1 even though it is not reachable from the modern Host MCP or normal CLI entrypoint.

It is best classified as **experimental-consumer-bound / non-core**.

### 3.4 Finance and Runtime

No current direct production Python imports of `ordivon_host` were found in Finance or Runtime.

Their historical/evidence documents may name Host contracts, but current ownership remains independent.

## 4. Current Host surface map

### 4.1 Still-core — admitted Continuity Core

These surfaces remain supported by current production responsibilities and/or live MCP/operations:

#### Durable identity and authority substrate

- `journal/*`
- `objects/*`
- `storage.py`
- `kernel.py`
- domain Task/Event/Descriptor primitives needed by the durable stream

They own Host Journal/CAS truth, exact Task revisions, optimistic/lease fencing, immutable referenced objects and state transitions.

#### External semantic continuity

- `continuity.py`
- `continuity_models.py`
- `ExternalContinuityHost`
- `WorkingCheckpoint*`

They back the admitted `task.adopt/checkpoint/resume/list/observe` model without claiming owner-current Runtime/domain truth.

#### Derived handoff/observation

- `handoff.py`
- `OperatorHandoffCapsule`

This remains a derived continuity view, not a new durable authority.

#### Modern MCP

- `mcp_server.py`

Current exposed surface is exactly the 6-tool continuity/inspection contract, with no Runtime proxy.

#### Operational integrity

- backup / restore / doctor / history / GC-plan / deployment inspection

These remain evidence and operability responsibilities around the admitted durable substrate.

### 4.2 Compatibility-only — retained, not architectural center

#### Recovery + Runtime client + old workload engines

Current CLI still exposes:

```text
task assess
task reconcile
doctor --runtime
```

`recovery.py` in turn retains logic for historical read/mutation/code-change/cognition workload heads and uses Runtime/repository adapters where automatic reconciliation is explicitly permitted.

Therefore:

- `recovery.py`
- `runtime/*`
- `engine/read_task.py`
- `engine/mutation/*`
- `engine/code_change/*`
- `authority.py`

must not be deleted merely because they are absent from the 6-tool MCP surface.

Current classification: **compatibility/operator recovery surface**.

A later contraction must inspect retained Task populations and rollback/CLI consumers before deleting any workload decoder/reconciler, per `docs/RELEASES.md`.

#### World-facing extension boundary

- `extensions.py`
- `HostExtensionPort`

This has a strong real World consumer and preserves owner-opaque extension state without Host absorbing owner semantics.

It is compatible with HDF41–43 and remains retained.

#### Host effect-envelope model exports

- `effects/*`

These are not the current Host center, but World production still imports several envelope/ref types. They remain a **real compatibility contract** until the World owner migrates or another contract becomes authoritative.

#### External executor compatibility

- `external_executor.py`

No direct Host-side production consumer was found, but current Harness still contains a corresponding duck-typed adapter designed for this foreign-Run port.

This is therefore **compatibility-only, not deletion-proven** in C1.

### 4.3 Experimental-consumer-bound / non-core

#### Cognition

- `cognition/*`

Not part of the modern MCP and not needed for ordinary Host continuity, but Security P0-B dynamically consumes it.

It cannot be called zero-consumer residue.

Before deletion, Security must either:

- retire the experiment;
- move the semantic boundary to its actual owner;
- or reproduce a durable Host responsibility that survives the Continuity-Core model.

### 4.4 Test equipment

- `testing/*`

This is test/evaluation support, not a product ontology claim. It may remain as cheap engineering equipment and should not be counted as Host architectural responsibility.

### 4.5 Deletion-proven residue

Before C1 the clearest member was:

- `coordination.py`
- `GoalCoordinatorHost`
- `GoalSnapshot`
- `TaskRevisionRef`
- `CoordinationError`
- `CoordinationSuperseded`

No live MCP/CLI path used it, no current external production consumer used it, and F2 independently falsified the central/shared Coordination ownership model it embodied.

## 5. First deletion candidate — GoalCoordinatorHost

The removed module implemented:

- Goal-wide Task snapshots;
- exact TaskRevisionRefs;
- assertion that the whole Goal snapshot remained current;
- coordinated Task transition;
- application of a joint VerificationReceipt to actor Tasks.

This was explicitly “not a scheduler or DAG”, but it still instantiated a shared Host-owned coordination surface over multiple Tasks.

F2 showed that the engineering responsibility does not survive consumer-local ownership/deletion tests.

### Deletion actions in isolated candidate

The candidate removes:

- `src/ordivon_host/coordination.py` — 252 LOC;
- `tests/test_goal_coordination.py` — 151 LOC;
- five top-level public exports;
- coordination-specific remediation test apparatus.

It preserves the generic invariant “terminal Task cannot reopen” by rewriting that remediation test directly against `HostKernel`, where the invariant is actually owned.

Candidate diff:

```text
4 files changed
29 insertions
487 deletions
```

Isolated candidate commit:

`6b3ce2c38e24a6fa8b1c229ae1184ec3efe4d24a`

Commit subject:

`refactor(host): contract unconsumed goal coordinator`

## 6. Candidate validation

### 6.1 Full Host deterministic validation

Against the candidate source:

```text
258 tests
OK
```

Also passed:

- documentation contract;
- dependency contract;
- compileall;
- `git diff --check`.

Runtime Job:

`job-01a0131d-20aa-7bf2-ac2e-2ab503d37ed9`

terminal evidence:

`sha256:360561c9bbfa10c5684a682f80df00a19941db1a6f604c7d98d7fc29901bcac0`

### 6.2 Full current World regression against candidate Host

Current World revision observed:

`6f98e381f6c58c0ff1a56cf7036c607d9ac0d4c6`

With candidate Host `src` placed first on `PYTHONPATH`, complete World unittest discovery returned:

```text
158 tests
OK
```

Runtime Job:

`job-01a0131e-348c-78a2-9fe7-724b0ea143ba`

terminal evidence:

`sha256:4b1b88a613b350b5eeb4a25c4069eaf720f6dfcb6642ec97fea168006d6eabd5`

Thus the only direct production package consumer survives the deletion candidate completely.

## 7. Why the candidate is not deployed yet

`docs/RELEASES.md` is canonical and explicitly states:

- removal of a supported public import is a **Major** change;
- even pre-1.0 changes require explicit compatibility impact;
- deletion of import aliases/compatibility surfaces requires named production/rollback consumers, retained-state inspection/migration where applicable, an observation window, live/deterministic evidence, Changelog entry and rollback independence.

`GoalCoordinatorHost` and related types were top-level `ordivon_host` public exports.

Therefore C1 establishes:

```text
semantic deletion proof     PASS
current consumer deletion   PASS
release-policy deletion     NOT YET ADMITTED
```

The correct action is to preserve the isolated candidate as an exact future cutover patch rather than bypass Host's own release discipline.

Canonical source and deployment remain at `8d7e58a...` until the Major-cutover obligations are explicitly satisfied.

## 8. Important contraction law exposed by C1

C1 distinguishes three questions that must not be conflated:

```text
Is this responsibility real?
Is the current implementation consumed?
Is removal release-admissible today?
```

For GoalCoordinatorHost:

```text
shared coordination owner responsibility   falsified
current implementation consumer need        absent
immediate public-import deletion release    gated
```

This prevents both opposite errors:

- keeping dead architecture forever because it once had tests;
- deleting public compatibility immediately merely because current local consumers do not use it.

## 9. Current target architecture

The best current Host target is:

```text
Host
= Continuity Core
+ bounded derived continuity views
+ explicitly named compatibility surfaces
+ operational integrity/recovery equipment
```

Not:

```text
Host
= coordination plane
+ scheduler
+ universal effect owner
+ cognition owner
+ Runtime proxy
+ global truth graph
```

More concretely:

```text
Source/domain truth           actual source/domain owner
Consumer-specific relation    named consumer
Durable semantic continuity   Host
Physical execution             Runtime
Agent cognition                Harness / actual cognition owner
Network capability             Workstation/Network owner
Governance/authority           actual constitutive owner
```

Host may carry compatibility equipment while it is still required, but compatibility is not ontology.

## 10. Naming verdict

Do not rename `ordivon-host` yet.

Reasons:

1. `Host` remains a useful deployed identity with substantial Journal/CAS/continuity/operations evidence;
2. a rename would create compatibility/migration work without deleting an unowned responsibility;
3. the more important task is first to contract responsibilities and clearly label compatibility surfaces;
4. after contraction, the repository may naturally read as a Continuity Host/Core even if the short operational name remains Host.

Naming is therefore a final ergonomic decision, not an architecture driver.

## 11. Next contraction order

Do not delete broad surfaces in bulk.

The next evidence order should be:

1. **ExternalExecutor compatibility audit**
   - inspect whether any current Host-side production path actually instantiates it;
   - bind the current Harness adapter as the named compatibility consumer;
   - test whether Harness can own the relation entirely without Host coordinator machinery;
   - only then determine deprecation/deletion trigger.

2. **Historical workload recovery census**
   - count retained Tasks/Event heads by workload/event family;
   - identify whether read/mutation/code-change/cognition recovery decoders still have retained-state obligations;
   - distinguish rollback/history decode from active execution support.

3. **Cognition consumer audit**
   - Security P0-B is a real dynamic consumer;
   - determine whether it is a current experiment worth preserving, or historical apparatus whose owner should migrate/retire it.

4. **Top-level public API contraction plan**
   - separate modern stable imports from compatibility imports;
   - avoid another Major removal before exact consumers and observation windows are named.

Only after these should a second code deletion be attempted.

## 12. Current verdict

```text
Continuity Core                         STRONGLY ADMITTED
GoalCoordinator/shared coordination     DELETION-PROVEN
GoalCoordinator candidate               258/258 Host + 158/158 World PASS
GoalCoordinator canonical removal       RELEASE-GATED / NOT DEPLOYED
HostExtensionPort                       REAL WORLD CONSUMER / RETAIN
Effects envelopes                       WORLD COMPATIBILITY / RETAIN
ExternalExecutor                        COMPATIBILITY-ONLY / AUDIT NEXT
Recovery + Runtime client + engines     OPERATOR/RETAINED-STATE COMPATIBILITY
Cognition                               SECURITY EXPERIMENT CONSUMER / NON-CORE
Independent Coordination project        FALSIFIED AT CURRENT EVIDENCE
Host rename                              NOT AUTHORIZED
Foundation reopen                       NO
```
