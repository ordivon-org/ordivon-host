# Host → Coordination Transfer Falsifier — Finance × Workstation Currentness — 2026-08-18

> **Historical engineering experiment, not current architecture authority.** This round continues the F1 engineering-consumption line after `HOST_COORDINATION_F1_DERIVED_CROSS_OWNER_VIEW_20260818.md`. It does not reopen HDF0–HDF43, rename Host, create a Coordination repository, add storage, add MCP/API surface, or change Finance/Workstation production code.

## 1. Transfer question

F1 retained one very small candidate grammar:

```text
CaseRef
DependencyRef
OwnerRef
exact owner revision/evidence ref
owner truth/currentness role
derived relation = available | unresolved
no authority
```

The transfer burden was intentionally stricter than “can the same JSON shape describe another case?”

A second materially different real consumer must answer:

1. does the grammar transfer unchanged;
2. do Task-state/revision/stale-checkpoint substitutes fail again;
3. does the relation change a concrete consumer action;
4. does one existing owner or consumer-local boundary already naturally own the relation;
5. can the relation remain derived/no-storage;
6. after deleting a hypothetical generic Coordination read surface, does correctness materially regress?

If an existing consumer-local adapter already owns the relation correctly, that is **negative evidence for Coordination project ownership**, even if the grammar itself transfers.

## 2. Candidate selection

A fresh live-continuity search excluded Computing/FS0 and foundation tasks.

The strongest high-consequence transfer candidate was:

- consumer case: `task:finance:temporal-financial-resource-model-20260815`
- current Task revision: `13`
- checkpoint digest: `sha256:f812fa8e9ca2c2f697b1334be00da8ade1ddd7e3efc91ca8a8c8e2e42fe19afe`
- consumer owner: Finance
- source owner: Workstation
- dependency: current `finance-okx` scoped egress required before FR10 canonical 20/20 primary-source ingestion and later current broad-screen materialization.

This case is materially different from F1 FS0:

- FS0 depends on historical semantic outcomes of frozen research pressures;
- FR10 depends on **ephemeral owner-current capability** that can become available and unavailable without changing the consumer case or even the owner continuity Task revision.

That makes currentness, not merely outcome history, the main falsifier.

## 3. Finance consumer state

Finance FR10 rev13 says:

- FR10 implementation is complete;
- canonical production data remains `PRODUCTION_GRADUATION_BLOCKED`;
- A2 identity intervals/events remain empty;
- current classification remains v1;
- current broad-screen remains v1;
- the only remaining engineering hinge is external current Workstation/OKX connectivity;
- when connectivity is restored, Finance should run canonical 20/20 primary ingest, classification v2, broad-screen v3, then rerun the graduation audit;
- none of this grants capital or execution authority.

The exact Host checkpoint also records the final Workstation observation at FR10 closeout as:

```text
surf-clash = UNAVAILABLE
finance-okx = UNKNOWN / no eligible member / listener absent
```

## 4. Current Finance truth revalidated

Current Finance repository during this transfer:

- HEAD: `58fd41ad7cdac7c7e503bfc4678fa04a5b33a8d0`

No later source commit was found that graduated FR10 production data.

A fresh execution of the owner-native read-only audit at `2026-08-18T03:41:20.027958Z` returned:

```text
verdict                  = PRODUCTION_GRADUATION_BLOCKED
semanticArchitecture     = PASS-FR9
identityIngestion        = BLOCKED
researchUsability        = BLOCKED
productionDataReadiness  = BLOCKED
identity A2              = 0 intervals / 0 events
classification           = schema v1
broad-screen             = schema v1
historical replay        = PASS
capitalAuthority         = false
executionAuthority       = false
financial write          = false
stateVersion             = 898:cf88acfa4d3f29fd
```

Runtime audit Job:

- `job-01a012f5-9426-7ec1-b44f-233291c2b91f`
- stdout digest: `sha256:1702d4e5c2de7111e4b5c8325855a0bf23ad5a6064c5d3b74bd0bf5df4346920`
- terminal evidence: `sha256:e8ceccc70d5ff5265edb75a2fd5e9d3294606d6f1e9cebba746c14e7cdb685ef`

Therefore the transfer consumer is still real and unresolved.

## 5. Workstation durable history versus live currentness

### 5.1 Historical Workstation recovery

Workstation continuity Task:

`task:workstation-nx8-diversity-discovery-flywheel-20260812`

remains:

- state: `ready`
- revision: `16`
- checkpoint digest: `sha256:4a49d694e53c23a40ddaa109e82cfd87397c461bebcc1b052b232489ebee279f`

At rev16, Workstation owner-native historical evidence said:

- scoped-egress supersession bug fixed;
- `finance-okx` profile published on `127.0.0.1:19083`;
- five-cycle 74 s hold remained AVAILABLE;
- every exact OKX public-time probe returned code=0;
- Workstation was no longer a Finance blocker;
- future generation/profile changes must requalify fresh evidence rather than reuse the historical profile indefinitely.

Thus at that historical cut:

```text
DependencyRef(finance-okx current scoped egress)
  -> available
```

### 5.2 Current live owner observation

Fresh `workstation.egress.observe(profile="finance-okx")` on 2026-08-18 returned:

```text
status                     = UNKNOWN
profileDigest              = sha256:3e993ee46cc5f9dbf0457c03464ba18fd5175bbfa27ff38a34d64c85125fb69e
proxy                       = http://127.0.0.1:19083
listenerReachable           = false
serviceActive               = false
parentHealthOk              = false
healthEvidenceSource        = member-pool
watchdogDisposition         = no-eligible-member
watchdogConsecutiveFailures = 1
activeMember                = null
eligibleMembers             = []
```

A fresh active owner probe of `surf-clash` at:

`2026-08-18T03:41:01.076850Z`

returned:

```text
status                 = UNAVAILABLE
observationDigest      = sha256:d3590c6850c3a634ef25cf7379046daf27074c2d1109edfdb45d752cb0441272
generationDigest       = sha256:d7e66dcb53f484bb87bcd7649231d5f205b01dafd49a2f4f9e89be910732e92c
capabilityDigest       = sha256:9358ce15081e89d10bd31356f5779bcb93d09a875f49acb52c1562c70f6dad4c
namespacePresent       = false
resolverHealthy        = false
transportHealthy       = false
requiredTargetsHealthy = false
serviceActive          = false
```

Therefore current owner truth is:

```text
DependencyRef(finance-okx current scoped egress)
  -> unresolved
```

without changing Workstation continuity Task revision 16.

## 6. The unchanged F1 grammar transfers

No new generic field is required.

### CaseRef

```text
task:finance:temporal-financial-resource-model-20260815
revision 13
checkpoint sha256:f812fa8e...
```

### DependencyRef

Case-local identity:

```text
FR10 current Workstation/finance-okx scoped-egress availability
```

This is not promoted into a universal Dependency ontology.

### OwnerRef

```text
ordivon-workstation / finance-okx
```

### Exact owner evidence ref

Historical cut:

```text
Workstation checkpoint rev16
sha256:4a49d694...
```

Current cut:

```text
finance-okx stable profile identity:
sha256:3e993ee4...

fresh surf-clash observation:
sha256:d3590c68...

generation:
sha256:d7e66dcb...
```

Generation/profile details stay **inside owner evidence**. They do not require a new Coordination schema dimension.

### Owner truth/currentness role

Historical rev16 evidence is historical owner evidence.

Fresh `workstation.egress.observe` / `workstation.anchor.observe` is owner-current evidence.

### Derived relation

```text
historical rev16 cut  -> available
current 2026-08-18 cut -> unresolved
```

### Authority

None.

The relation cannot start Workstation, change routes, perform Finance ingestion, read Finance credentials, or authorize capital action.

## 7. Stronger temporal law exposed by transfer

F1 FS0 showed:

```text
Task state / Task revision != pressure outcome identity
```

The Finance transfer adds a stronger temporal case:

```text
same owner Task revision
same stable authority/profile identity
!= same current capability state
```

Specifically:

```text
Workstation Task = ready@rev16

historical owner cut:
  finance-okx AVAILABLE

current owner cut:
  finance-okx UNKNOWN
  surf-clash UNAVAILABLE
```

So even exact Task revision is insufficient for ephemeral capability currentness.

## 8. Deletion baselines

### 8.1 Owner Task terminality

Fails completely as a currentness predicate.

Workstation remains `ready`, both when `finance-okx` was historically AVAILABLE and now when it is UNKNOWN.

```text
TaskState cannot discriminate capability availability.
```

### 8.2 Owner Task revision advancement

Fails more strongly than in F1.

Workstation continuity remains revision 16 while live capability changed from AVAILABLE to UNKNOWN/UNAVAILABLE.

Therefore:

```text
TaskRevision movement is not merely insufficient;
capability can change with zero Task revision movement.
```

### 8.3 Latest durable Workstation checkpoint

Produces a current false positive.

Rev16 says Workstation is no longer a Finance blocker and the profile was AVAILABLE.

Fresh owner observation says the listener is absent, no member is eligible and the parent anchor is unavailable.

Therefore historical durable owner checkpoints cannot substitute for a current observation where the owner contract declares currentness to be ephemeral.

### 8.4 Stable profile identity / binding equality

Also fails.

Current Finance deployment still binds:

`sha256:3e993ee46cc5f9dbf0457c03464ba18fd5175bbfa27ff38a34d64c85125fb69e`

and `finance.executor.status` reports:

```text
bindingsMatch        = true
currentEgressMatches = false
```

Thus:

```text
same authority/profile digest != currently usable capability
```

This is an exact counterexample to treating stable identity as liveness.

## 9. The decisive ownership test

The transfer could have been interpreted as evidence for a generic Coordination currentness view.

Current Finance source falsifies that interpretation.

`mcp/finance-semantic-adapter.mjs` already implements consumer-local currentness semantics.

`executorStatus()`:

- reads Finance venue authority;
- reads Workstation scoped-egress current state;
- exposes `currentEgressMatches` only when status, exact profile digest, proxy and listener all match.

`requireCurrentEgress(expectedProfileDigest)`:

- requires Finance deployment profile digest to equal caller expected digest;
- re-observes Workstation at call time;
- requires `status=AVAILABLE`;
- requires listener reachable;
- requires exact profile digest;
- requires exact proxy;
- requires parent health OK;
- otherwise fails with `EGRESS_NOT_CURRENT`.

This relation is already naturally owned at the **Finance consumer boundary**:

```text
Workstation owns: current scoped-egress state
Finance owns: whether that owner state satisfies Finance's current workflow precondition
```

No new third owner is required.

## 10. Current physical dogfood of the consumer-local relation

Fresh `finance.executor.status` returned:

```text
venue profile            = finance-okx
profile digest            = sha256:3e993ee4...
running executor          = disabled / healthy
bindingsMatch             = true
current egress            = UNKNOWN
listenerReachable         = false
watchdogDisposition       = no-eligible-member
currentEgressMatches      = false
```

Then a real read-only:

`finance.account.observe(expectedEgressProfileDigest=sha256:3e993ee4...)`

returned:

```text
EGRESS_NOT_CURRENT
```

No order/cancel/amend/transfer/withdraw/account mutation surface was available.

This is the correct current consumer behavior.

The relation therefore changes a concrete consumer action:

- when current Workstation evidence satisfies the exact Finance dependency, the existing Finance adapter permits the read-only account observation path;
- when current evidence does not satisfy it, the adapter suppresses that provider action and fails closed.

Historical Finance live-capital evidence confirms the positive side was not hypothetical: after Workstation egress became current, Finance consumed the stable authority, completed authenticated observations, executed bounded live effects under separate Finance authority, and reconciled provider-native truth.

## 11. Current adapter regression

Focused current Finance semantic MCP tests:

- Runtime Job: `job-01a012f7-2d57-7841-9767-92d1824758df`
- stdout digest: `sha256:69506c12e086a0b5766bd740489197ff17cf4305fbadd16b1895a7dc802182fd`
- terminal evidence: `sha256:565caaadb2bd2db626049152f831e13d02ecd878ca24927ba00877471b642baf`

Result:

```text
8/8 PASS
```

including:

- exact current profile required;
- stale/unavailable profile fails `EGRESS_NOT_CURRENT`;
- account observer admits only current exact egress;
- zero-write GET-only provider surface;
- same stable authority with lease drift does not force executor reload;
- startup-window recovery does not issue a second restart.

No Finance or Workstation source mutation was required.

## 12. Transfer deletion test against generic Coordination

The important deletion test is no longer “can the relation be represented without Coordination?”

It is:

> If a hypothetical generic Coordination read surface is deleted, does this consumer become incorrect or materially unable to continue?

For Finance × Workstation, the answer is **no**.

After deleting hypothetical Coordination:

```text
Workstation current observation
  -> FinanceSemanticAdapter.requireCurrentEgress()
  -> Finance owner-specific admission
```

remains complete and correct.

A generic Coordination view would either:

1. duplicate Workstation currentness + Finance qualification policy; or
2. expose only a weaker generic `available/unresolved` relation that Finance must still revalidate before action.

Neither earns an independent shared owner.

Therefore the generic Coordination read surface fails the **consumer-necessity / ownership deletion test** for this transfer case.

## 13. Transfer verdict

### Grammar transfer

**PASS.**

The F1 minimal grammar transfers unchanged from historical research outcome readiness to ephemeral operational capability currentness.

This is evidence that the grammar describes a real general class of relation.

### Generic Coordination ownership transfer

**FAIL.**

The relation is naturally and already owned by the consumer-local Finance/Workstation boundary.

This is evidence against promoting the grammar into a shared Coordination project merely because it generalizes conceptually.

### New durable artifacts

Still **not admitted**:

- Projection
- Dependency
- HyperDependency
- ReconciliationRecord
- AttentionRequest
- LivenessState
- generic currentness ledger
- Coordination database

### Coordination independent-project admission

Becomes **weaker**, not stronger.

Current evidence now says:

```text
Continuity Core                         strongly admitted
Generic relation grammar                empirically transferable
FS0 cross-owner composite relation      real
Finance/Workstation relation            consumer-local ownership sufficient
Generic Coordination read surface       not yet justified
Coordination independent project        not admitted; prior weakened
```

## 14. Important conceptual correction to F1

F1 established that the FS0 relation is not naturally owned by any **source owner**.

The transfer exposes a missing ownership competitor in that earlier reasoning:

```text
source owner
vs
shared Coordination owner
```

was incomplete.

A third option is:

```text
consumer-local relation owner
```

Finance demonstrates this option concretely.

Therefore F1's claim:

> “the relation is not owned by Game/Runtime/etc.”

is not sufficient to infer:

> “Coordination should own the relation.”

The next decisive engineering question is whether FS0 itself can also be implemented cleanly as a Computing-local derived relation over owner-native evidence.

If yes, the strongest remaining reason for an independent Coordination project collapses.

## 15. Next cheapest falsifier

Do **not** search randomly for a third domain yet.

Run a **Consumer-local Ownership Falsifier** against the original F1 FS0 case.

Freeze the same relation semantics, then compare:

### Model A — shared Coordination ownership

```text
owner evidence -> generic Coordination read surface -> Computing/FS0
```

### Model B — consumer-local ownership

```text
owner evidence -> Computing/FS0 local derived adapter -> FS0 evaluation
```

Measure:

1. correctness against the same G-AF3/R-P5/Finance negative controls;
2. amount of case-specific policy that remains in Computing anyway;
3. duplicated generic code, if any;
4. whether another independent consumer actually needs exactly the same reusable mechanism;
5. whether shared extraction reduces friction/errors without centralizing owner semantics;
6. deletion cost of the shared layer.

Admission rule:

- if consumer-local ownership is equally correct and simpler, contract the current Coordination engineering hypothesis to **Continuity Core + owner/consumer-local adapters**;
- if shared extraction uniquely prevents repeated cross-consumer correctness failures, only then reconsider a generic read surface.

This is now more informative than building F2 artifacts.

## 16. Foundation status

No FoundationReopenCondition was triggered.

The transfer reinforces frozen HDF40–HDF43:

- HDF40: shrink product claims to consumer-proven responsibilities;
- HDF41: local compatibility can be evaluated at a consumer boundary without global state;
- HDF42: current viability may require active re-observation, while the maintainer remains owner-specific;
- HDF43: Finance authority remains Finance-owned and Workstation capability truth remains Workstation-owned.

The result is an engineering ownership contraction, not a Foundation change.
