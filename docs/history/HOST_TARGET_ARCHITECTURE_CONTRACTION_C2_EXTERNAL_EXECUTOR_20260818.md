# Host Target-Architecture Contraction C2 — ExternalExecutor — 2026-08-18

> Historical engineering evidence. This document records a deletion-first audit after C1. It does not authorize canonical deployment, alter the frozen HDF0–HDF43 foundations, or create a new Coordination project.

## 1. Question

Does Host still own a non-bypassable foreign-executor coordination responsibility that justifies `external_executor.py`, or has that surface become compatibility-only residue after Harness became independently authoritative and Host contracted to Continuity Core?

C2 used four gates:

1. current product reachability;
2. retained durable-state obligation;
3. current external consumer necessity;
4. deletion falsifier across Host + World + Harness + Security.

## 2. Current Host ExternalExecutor surface

Before the candidate deletion, `src/ordivon_host/external_executor.py` was 870 LOC and exposed:

- `ExternalExecutionRequest`;
- `ExternalRunObservation`;
- `ExternalCompletionProposal`;
- `ExternalRunBinding`;
- `ExternalExecutionSnapshot`;
- `ExternalExecutorAdapter`;
- `ExternalExecutorCoordinator`;
- associated conflict/error/status types.

The coordinator stored opaque state through `HostExtensionPort` under namespace `external` and emitted extension events:

```text
external.execution-requested
external.run-bound
external.run-observed
external.cancel-requested
external.run-recovered
external.completion-collected
```

Its intended boundary was conservative: Host persisted immutable request/binding/observation/proposal references while the foreign executor retained execution authority.

## 3. Product reachability audit

Current modern Host MCP does not import or expose `ExternalExecutorCoordinator`.

Current Host CLI does not instantiate it.

Source references outside the module itself, top-level package exports and tests were absent.

Therefore current Host application reachability is:

```text
MCP path        none
CLI path        none
internal caller none
```

The surface remained public primarily through `ordivon_host.__init__` and its own deterministic tests.

## 4. Current Harness relationship

Current Harness revision observed:

`7277e1074be83fa38c28cd8170c28d6f4223146e`

Harness still contains:

`ordivon_harness.host_external_adapter.OrdivonHarnessExternalExecutorAdapter`

but that adapter is explicitly Host-free and duck-typed.

It owns Harness-side behavior:

- resolving an immutable Harness Run contract from a foreign request view;
- creating/reopening the independent Harness Run;
- observing/cancelling/recovering that Run;
- returning Harness-native observations;
- returning an independent completion proposal.

Exact reference scan found no current application/production Python caller of this adapter. Current references are:

- adapter module itself;
- Harness docs;
- Harness tests/public API test;
- wheel-content check.

Therefore the Harness helper is itself an optional integration compatibility surface, not evidence that current Host must own a coordinator.

The important ownership split is:

```text
Harness Run truth       Harness
Harness execution       Harness
foreign request meaning caller/consumer
Host continuity         Host
```

A Host-side shared foreign-run coordinator is not independently required by this split.

## 5. Retained production-state census

During C2 the production Host Journal advanced from 436 to 437 Tasks because other work was ongoing.

At the final census:

```text
Task count                         437
workload identities                100% ordivon.host.external-continuity.v1
current Task heads                 100% task.context-checkpointed
task_extension_state rows          0
external.* retained Event kinds    0
runtime.* retained Event kinds     0
cognition.* retained Event kinds   0
effect.* retained Event kinds      0
```

Current CAS also contains zero objects with these ExternalExecutor kinds:

```text
external-execution-request
external-run-binding
external-completion-proposal
```

Thus current production durable authority has no ExternalExecutor state requiring decoding or recovery.

This is stronger than an import-graph result:

```text
current caller obligation       = 0
current retained-state obligation = 0
```

## 6. Old workload recovery census

The same production census showed that all 437 Tasks are external-continuity Tasks.

Running `assess_recovery()` over every current Task produced:

```text
terminal Tasks -> none          383
active external continuity -> unsupported 54
errors                          0
```

No current Task required automatic Runtime/mutation/code-change/cognition recovery.

This does not yet authorize deletion of recovery/Runtime/engine compatibility code, because current CLI/public API and release/rollback contracts still exist. It does establish that **live-state recovery obligation is zero** for those old workloads.

That becomes the next contraction premise.

## 7. ExternalExecutor deletion candidate

C2 removed from the isolated audit branch:

- `src/ordivon_host/external_executor.py` — 870 LOC;
- `tests/test_external_executor.py` — 414 LOC;
- 13 top-level `ordivon_host` public exports;
- two boundary assertions that required the old public surface.

C2-only diff:

```text
4 files changed
1314 deletions
0 insertions
```

Candidate commit:

`7c9192ea7863aa26c34d95800a961d1b579c52d5`

Subject:

`refactor(host): contract unconsumed external executor`

This candidate is cumulative with the earlier C1 GoalCoordinator contraction on the isolated branch, but C2 itself contains only the ExternalExecutor removal above.

## 8. Host validation

Against the C2 candidate:

```text
252 tests
OK
```

Also passed:

- documentation contract;
- dependency contract;
- compileall;
- `git diff --check`.

Runtime Job:

`job-01a01325-5b34-7ff3-90ba-5f6b006aedfc`

terminal evidence:

`sha256:b973d2a9cc49e644b582de14f733320f3bac208598df7921bc87b3b7e62cb15a`

The test-count decrease is exactly explained by deletion of the dedicated ExternalExecutor tests; unrelated Host tests remain green.

## 9. World validation

Current World remains the only direct production Python package consumer of `ordivon_host`.

With the C2 candidate Host source first on `PYTHONPATH`:

```text
158 tests
OK
```

Therefore World has no dependency on the removed ExternalExecutor surface.

## 10. Harness validation

With C2 candidate Host source present in the environment, complete current Harness unittest discovery returned:

```text
424 tests
OK
3 skipped
```

This includes Harness's own optional Host-external-adapter tests.

The result is important:

> Harness can retain and validate its independent optional adapter even when current Host no longer exposes or implements the corresponding coordinator.

That proves the Harness helper is not a runtime dependency on Host ExternalExecutor ownership.

Combined World + Harness Runtime Job:

`job-01a01326-2cdf-7582-95e8-d15f83dea8c6`

terminal evidence:

`sha256:7d2558a6ce3291080dbfb1ec95865d2572a7247f51b81dc3905060fea8953dc5`

## 11. Security non-regression

Security dynamically imports `ordivon_host` and `ordivon_host.cognition` in the P0-B experimental actor path.

After C2 removal, focused Security suites passed:

```text
test_host_assigned_actor       9/9
test_runtime_assigned_actor    7/7
test_surface                   6/6
```

Total focused checks:

```text
22/22 PASS
```

Thus top-level Host package contraction did not break the known dynamic cognition consumer.

Runtime Job:

`job-01a01328-2523-7f71-83f4-8c1d28a3b7f1`

terminal evidence:

`sha256:200a038c73d90bd1943076270ad1adc4a6d18810caea15a6a3ad8da998a2ca4e`

## 12. Deletion verdict

C2 produces the following engineering result:

```text
Host ExternalExecutor responsibility     not core
current Host caller need                 absent
current retained-state need              absent
current World need                       absent
current Harness runtime need             absent
current Security need                    absent
semantic deletion proof                  PASS
current-consumer deletion proof          PASS
```

Therefore:

```text
ExternalExecutorCoordinator
ExternalExecutionRequest
ExternalRunBinding
ExternalRunObservation
ExternalCompletionProposal
and associated Host foreign-executor machinery
```

are **deletion-proven at current engineering evidence**.

## 13. Why it is still not canonical/deployed

As in C1, these classes are supported top-level public imports in package `0.2.0`.

Canonical `docs/RELEASES.md` says removing supported public imports is a Major change and requires explicit cutover evidence including:

- compatibility impact;
- named production/rollback consumers;
- observation window;
- live/deterministic evidence;
- Changelog entry;
- rollback independence.

Therefore the final status is:

```text
semantic deletion proof       PASS
live durable-state deletion    PASS
current consumer deletion      PASS
release-policy deletion        NOT YET ADMITTED
canonical deployment           UNCHANGED
```

Canonical/deployed Host remains `8d7e58a0511734a454805e29d10e7d3bb754d2da`.

## 14. Updated target architecture

After C1 + C2, two historically broad Host responsibilities have now failed deletion tests in favor of a smaller core:

```text
Goal-scoped shared coordinator    deletion-proven
foreign executor coordinator      deletion-proven
```

The surviving engineering center is increasingly:

```text
Host
= durable semantic continuity
+ exact Journal/CAS identity and fencing
+ bounded derived handoff/inspection
+ owner-opaque extension durability where real consumers exist
+ operational integrity
+ explicitly temporary compatibility surfaces
```

Host does not need to own foreign execution merely to retain a reference to foreign work.

This sharpens HDF41/HDF42 consumption:

- cross-owner compatibility can be local and reference-based;
- persistent viability does not require one central coordinator;
- foreign owner observations do not transfer execution authority to Host.

## 15. New recovery contraction premise

C2's retained-workload census changes the next-round burden substantially.

Previously recovery/Runtime/engine code was retained because the current CLI still exposed it and historical Tasks might require it.

Now we know:

```text
live old-workload Tasks             0
live old-workload current heads     0
live old-workload extension state   0
live old-workload CAS kinds sampled 0
```

Thus the next question is no longer “does production state need these decoders?” It does not.

The next question is:

> Are `task assess`, `task reconcile`, `doctor --runtime`, `recovery.py`, `runtime/*`, and old read/mutation/code-change engines still justified by current operator/public/rollback compatibility, or are they another release-gated historical surface?

That requires an exact CLI/public/rollback deletion audit before code removal.

## 16. Harness-side handoff

C2 does **not** mutate Harness.

Harness still owns an optional `host_external_adapter` public helper. Since no current application caller was found, Harness may later audit that helper under its own release policy.

Host should not delete another owner's compatibility surface simply because Host no longer consumes it.

## 17. C2 verdict

```text
Continuity Core                       STRONGLY ADMITTED
GoalCoordinator                       DELETION-PROVEN / RELEASE-GATED
Host ExternalExecutor                 DELETION-PROVEN / RELEASE-GATED
HostExtensionPort                     RETAIN — real World consumer
World effect/ref compatibility        RETAIN — real World consumer
Harness host_external_adapter         HARNESS-OWNED OPTIONAL COMPATIBILITY
Cognition                             RETAIN FOR NOW — Security P0-B consumer
old workload live-state recovery      ZERO CURRENT OBLIGATION
recovery/runtime/engine code          AUDIT NEXT — public/operator/rollback only
Host rename                           NOT AUTHORIZED
Foundation reopen                     NO
```

## 18. Next canonical experiment

Run **Recovery / Runtime / Engine Compatibility Contraction** before any further deletion.

Required sequence:

1. enumerate exact current CLI/API consumers of `task assess`, `task reconcile`, `doctor --runtime`, `McpRuntimeClient`, `TaskReconciler`, read/mutation/code-change engine classes;
2. inspect deployment rollback releases/backups and determine whether any rollback contract requires the old Python imports or only the current Journal bytes;
3. prove whether current MCP `task.observe` can project an honest recovery field for external-continuity Tasks without importing legacy workload engines;
4. construct a minimal compatibility alternative if needed;
5. only then attempt a deletion candidate;
6. run Host + World + Security and any named operator consumer tests;
7. keep canonical deployment unchanged until Major-cutover gates are satisfied.
