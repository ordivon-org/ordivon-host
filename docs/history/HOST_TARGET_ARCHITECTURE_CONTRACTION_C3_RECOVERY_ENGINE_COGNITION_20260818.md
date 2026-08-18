# Host Target-Architecture Contraction C3 — Recovery / Engine / Cognition Execution — 2026-08-18

> Historical engineering evidence. This pass continues deletion-first contraction after C1 GoalCoordinator and C2 ExternalExecutor. It does not alter frozen HDF0–HDF43, does not authorize a new Coordination project, and does not deploy the isolated candidate.

## 1. Starting premise from C2

C2 established that current production Host authority contains only external-continuity Tasks and no retained old workload state.

At the start of C3, the current deployed/source Host remained:

`8d7e58a0511734a454805e29d10e7d3bb754d2da`

Fresh `host.status(detail=history)` remained healthy and showed only the six modern MCP Tools with `runtimeProxy=false`.

During C3, other concurrent work advanced the production authority to 440 Tasks, but the architectural population remained external continuity.

C2 had already shown:

```text
old read/mutation/code-change live Task obligation = 0
old external-executor live state obligation        = 0
old cognition execution live Task obligation       = 0
```

C3 therefore asked whether the remaining old execution/reconciliation implementation existed for a real current consumer or only because historical cognition and tests were coupled to it.

## 2. Current consumers before deletion

### 2.1 External production Python consumers

A fresh scan found no non-Host production Python repo importing:

- `TaskReconciler`;
- `assess_recovery`;
- `McpRuntimeClient`;
- `RuntimeCatalog`;
- `ExecutionRuntimeCatalog`;
- `DeterministicReadHost`;
- `GuardedMutationHost`;
- `CodeChangeHost`;
- `ordivon_host.engine`;
- `ordivon_host.recovery`;
- `ordivon_host.runtime`.

### 2.2 Host internal current callers

Before C3, current internal callers were:

- CLI `task assess` -> `assess_recovery()`;
- CLI `task reconcile` -> `TaskReconciler` + Runtime client;
- CLI `doctor --runtime` -> Runtime MCP client through `ops.doctor`;
- MCP `task.observe` -> `assess_recovery()` only for non-external-continuity Tasks.

For external-continuity Tasks, `task.observe` bypasses recovery assessment and returns the continuity summary directly.

Because current production authority contains only external-continuity Tasks, `TaskReconciler` and old engine code are not on the current production MCP state path.

## 3. Operator evidence

Searches across repositories, systemd units, root shell history and recent journal output found no actual operator invocation record for:

```text
ordivon-host task reconcile
ordivon-host task assess
ordivon-host doctor --runtime
```

The commands remain documented in Host `OPERATIONS.md` / `QUICKSTART.md` and were exercised historically by Host-owned tests and live proof scripts.

This does not by itself prove a command should be removed. It does establish that current observed operator demand is documentation/test compatibility rather than an external application dependency.

## 4. Rollback evidence

Current deployment status is healthy and explicitly reports:

```text
activationRollbackPolicy = release-bytes-only
liveSchemaVersion = 5
previousReleaseSchemaVersion = 5
migrationRequired = false
explicitRollbackSupportedAfterSuccess = true
```

The current release is:

`8d7e58a0511734a454805e29d10e7d3bb754d2da-d9c7ebcc5c31`

The current receipt binds previous release:

`ebaf6ef90d87e7bc524e8f30d71521b371d17f2e-5983b8111559`

Every physically retained release inspected contains its own historical:

```text
recovery.py
runtime/
engine/
```

Therefore explicit rollback restores old release bytes; a new release does not need to carry old engine implementations merely so that rollback can recover them.

This is the key rollback law for C3:

```text
rollback compatibility != new-release source compatibility
```

provided Journal schema compatibility and the exact previous release bytes remain preserved.

## 5. Why the first naive engine deletion boundary was too small

The old engine implementation was 3,507 source LOC:

```text
engine/read_task.py
engine/mutation/*
engine/code_change/*
engine/_serde.py
```

However cognition still imported it:

- `cognition.proposal.py` -> `ReadTaskPlan`;
- `cognition.proposal_turn.py` -> `DeterministicReadHost` and Runtime;
- `cognition.mutation_proposal.py` -> `GuardedMutationPlan`.

Thus “engine is still imported” initially looked like evidence for retention.

The next consumer audit showed that this coupling was historical, not production-driven.

## 6. Real cognition consumer

The only current external production consumer of `ordivon_host.cognition` is Security P0-B.

Security dynamically imports:

```text
ordivon_host
ordivon_host.cognition
```

but exact attribute analysis showed it consumes only:

```text
BlockKind
ContextBlock
Freshness
```

It does not consume:

- `CognitionHost`;
- `OpenProposalHost`;
- `RepositoryReadProposalCompiler`;
- `RepositoryMutationProposalCompiler`;
- decision admission lifecycle;
- proposal-turn Runtime execution;
- scripted selector;
- Host cognition execution evidence.

Therefore old cognition execution/proposal modules were not evidence that Host must retain the engine. They were another historical Host-owned workload family.

## 7. C3 target contraction

The C3 candidate preserves the actually consumed cognition context semantics and removes the unconsumed execution/proposal layer.

### Retained cognition

`cognition/context.py` remains, including:

- `ContextBlock`;
- `BlockKind`;
- `Freshness`;
- bounded context compilation helpers and related context data models.

The contracted `ordivon_host.cognition.__all__` is:

```text
BlockKind
CandidateAction
ClosedChoiceContextCompiler
ClosedChoiceContextRequest
CompiledContext
ContextBlock
ContextCompileError
ContextManifest
DecisionKind
Freshness
block_from_payload
estimate_tokens
```

This keeps a useful context-selection tool surface without retaining Host-owned cognition execution.

### Removed cognition execution/proposal modules

C3 removes:

```text
cognition/decision.py
cognition/decision_request.py
cognition/mutation_proposal.py
cognition/proposal.py
cognition/proposal_turn.py
cognition/provenance.py
cognition/scripted.py
cognition/turn.py
```

### Removed engine

C3 removes the entire `src/ordivon_host/engine/` tree.

The removed Host responsibilities include:

- deterministic Runtime read Task progression;
- guarded mutation orchestration;
- code-change orchestration;
- Runtime delivery/reconciliation wrappers for those workloads;
- workload-specific plans/models/codecs/evidence handling.

The underlying capabilities remain owned elsewhere:

```text
physical execution       Runtime
source/domain semantics  actual domain owner
Agent cognition          Harness / caller
Host continuity          Host
```

### Contracted recovery

`recovery.py` contracts from ~260 LOC to 73 LOC.

It retains:

- `RecoveryAction` identifiers;
- `RecoveryAssessment`;
- read-only `assess_recovery()`.

It no longer imports:

- engine;
- Runtime;
- capability authorizer.

It never performs automatic external action.

Its surviving semantics are conservative:

```text
terminal Task     -> none
nonterminal Task  -> unsupported / re-observe owner before acting
automatic         -> always false
```

### Removed automatic recovery

C3 removes:

- `TaskReconciler`;
- `RecoveryResult`;
- CLI `task reconcile`.

CLI `task assess` remains as a cheap read-only Host-local inspection.

## 8. Runtime diagnostic deliberately retained

C3 does **not** remove `runtime/*`.

`doctor --runtime` is kept as a separate operator diagnostic rather than being conflated with old engine ownership.

A real post-contraction CLI proof created a temporary Host state and invoked:

```text
init
doctor --runtime
```

The doctor result was healthy and reported:

```text
runtime status = ok
server name    = ordivon-runtime-mcp
version        = 0.1.0
```

Runtime Job:

`job-01a01332-65bd-7de2-ba3a-745399985c83`

terminal evidence:

`sha256:6180d7fa8576779f2b8d5010a68aeae773d570817a756fa4b4ed849204186996`

This proves the retained Runtime diagnostic remains functional after engine deletion.

## 9. Historical proof apparatus removed

The following scripts existed only to exercise the deleted workload families and were removed from the isolated candidate:

```text
scripts/live_code_change.py
scripts/live_commit_gap_recovery.py
scripts/live_guarded_mutation.py
scripts/live_runtime_read.py
scripts/live_runtime_restart_recovery.py
scripts/round1_host_conformance.py
```

Their historical evidence/documents remain available as history; executable proof code is not architectural ownership.

Dedicated tests for deleted engine/cognition-execution surfaces were also removed.

A new boundary test asserts the contracted ownership shape, and recovery tests now cover only the surviving conservative assessment semantics.

## 10. First full-suite falsifier

The first Host full-suite run after C3 reported exactly two errors:

1. `test_cli` still expected the removed `task reconcile` command;
2. `test_live_scripts` imported the removed historical `live_runtime_read` proof script.

No continuity, Journal, MCP, World-facing, storage, deployment, context-selection, or Runtime diagnostic invariant failed.

These two failures were therefore classified as historical compatibility test apparatus, not hidden production ownership.

After contracting those two tests, the complete Host suite passed.

This failed-first run is retained as useful falsifier evidence rather than hidden.

Runtime Job:

`job-01a0132f-cce3-7ee2-b9c9-88d5f162f0bb`

terminal evidence:

`sha256:556ff417f3f23de3b04b18944912f7fb298a3d060a1b0beead8f2e87f93903e1`

## 11. Final Host validation

Final C3 candidate validation:

```text
180 tests
OK
```

Also passed:

- documentation contract;
- dependency contract;
- compileall;
- `git diff --check`.

Runtime Job:

`job-01a01330-ee93-7cf0-be0f-d6dab0cf7e8b`

terminal evidence:

`sha256:8ae255c3c9e0ccbf158162e56d0e919cfda8394307c1f9013e09c2cd85e4ab2a`

## 12. Real consumer validation

### World

With C3 candidate Host first on `PYTHONPATH`, complete current World tests returned:

```text
158 / 158 PASS
```

### Security

Focused current Security Host/cognition suites returned:

```text
22 / 22 PASS
```

This directly validates the context-only cognition contract used by the actual dynamic consumer.

Combined Runtime Job:

`job-01a01331-dbbc-72c1-88f2-7cf98513b572`

terminal evidence:

`sha256:81f398ea7a9c84222c127c4a1eddbeb208fe8454a15a9113e6a40f4d7d69f8c5`

## 13. Candidate size

C3-only diff:

```text
43 files changed
57 insertions
10,997 deletions
net -10,940 lines
```

Candidate commit:

`7fe7abf2f14bf26621a002c9ced6fc999e0d42ba`

subject:

`refactor(host): contract legacy execution workloads`

After cumulative C1 + C2 + C3 contraction, `src/ordivon_host` is approximately:

```text
9,014 Python LOC
```

C3 itself does not claim that line count is a quality metric. The relevant fact is that the deleted lines represented responsibilities whose live owner/consumer/state obligations failed the deletion falsifier.

## 14. Current post-C3 dependency shape

### Authority

Post-C3 source scan finds **zero production importers** of `authority.py`.

It remains in the candidate only because C3 did not broaden into an Authority contraction.

This makes it a strong next audit target.

### Runtime

Post-C3 Runtime imports remain only in:

- top-level package exports;
- `ops.doctor`;
- testing helpers.

Runtime is no longer imported by recovery, cognition or engine because those execution paths are gone.

Thus Runtime has changed category from “Host workload substrate” to:

```text
operator diagnostic / compatibility surface
```

It needs its own deletion-or-retention falsifier rather than being removed by association.

### Recovery

Recovery is now a 73 LOC conservative read projection consumed by:

- top-level exports;
- CLI `task assess`;
- MCP `task.observe` fallback for non-external Tasks.

It no longer has execution authority.

### Cognition

Cognition is now a context semantics helper with a real Security consumer.

It is no longer a Host-owned model execution / proposal / mutation subsystem.

## 15. Updated target architecture

After C3 the engineering target is substantially narrower:

```text
Host
= Journal/CAS Task continuity
+ exact revision/lease fencing
+ WorkingCheckpoint semantic continuity
+ bounded handoff/inspection
+ opaque extension durability where real owner consumers exist
+ context-selection helper semantics where Security consumes them
+ operational integrity / deployment / backup
+ a small number of explicitly named compatibility/diagnostic surfaces
```

Not:

```text
Host
= Runtime workload orchestrator
+ source mutation engine
+ code-change engine
+ cognition execution host
+ automatic task reconciler
+ foreign executor coordinator
+ shared goal coordinator
```

## 16. Foundations interpretation

C3 is engineering consumption, not a Foundation rewrite.

It reinforces prior HDF conclusions:

- persistence does not imply one owner must perform every recovery action;
- owner-local execution truth remains with Runtime/domain systems;
- Host may preserve semantic continuity while declining automatic recovery authority;
- useful context selection can survive without promoting cognition execution into Host ontology.

No FoundationReopenCondition fired.

## 17. Release gate

C3 removes public Python modules/classes and one CLI command. Under canonical `docs/RELEASES.md`, this is a Major compatibility cutover.

Therefore:

```text
semantic deletion proof          PASS
current live-state deletion      PASS
current consumer deletion        PASS
rollback-byte independence        PASS
Host deterministic validation    PASS
World validation                 PASS
Security validation              PASS
runtime diagnostic preservation  PASS
release-policy deletion          NOT YET ADMITTED
canonical deployment             UNCHANGED
```

Canonical/deployed Host remains:

`8d7e58a0511734a454805e29d10e7d3bb754d2da`

C1/C2/C3 isolated commits remain exact candidate/evidence history only.

## 18. Next frontier

Do not immediately delete more code simply because C3 succeeded.

The next contraction should independently test two residuals:

### A. Authority residue

`authority.py` now has zero production importer.

Question:

> Is any Host-owned capability-authority semantic still real after execution/proposal engines were removed, or is Authority fully owned elsewhere?

### B. Runtime diagnostic / compatibility

`runtime/*` now survives primarily for `doctor --runtime`, tests and public imports.

Question:

> Is Host→Runtime diagnostic a useful Host-local operator tool worth retaining, or should Runtime health be queried only from Runtime itself and Host keep no Runtime client at all?

This should be decided by utility/ownership evidence, not by line-count minimization.

A separate Major-cutover plan is also eventually required to canonicalize C1/C2/C3 together: Changelog, current public compatibility statement, observation window, exact previous-release rollback peer, updated docs/status and release acceptance.

## 19. C3 verdict

```text
Continuity Core                         STRONGLY ADMITTED
GoalCoordinator                         DELETION-PROVEN / RELEASE-GATED
ExternalExecutor                        DELETION-PROVEN / RELEASE-GATED
old read/mutation/code-change engines   DELETION-PROVEN / RELEASE-GATED
automatic TaskReconciler                DELETION-PROVEN / RELEASE-GATED
cognition execution/proposal layer      DELETION-PROVEN / RELEASE-GATED
cognition context semantics             RETAIN — real Security consumer
read-only recovery assessment           RETAIN — small conservative projection
Runtime diagnostic                      RETAIN FOR NOW — independently validated
Authority module                        ZERO-CONSUMER RESIDUE / AUDIT NEXT
World extension/effect compatibility    RETAIN — real World consumer
Host rename                              NOT AUTHORIZED
Foundation reopen                       NO
```
