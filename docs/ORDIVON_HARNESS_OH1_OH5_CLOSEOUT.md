# Ordivon Harness OH1–OH5 closeout

Status: closed as the verified **read-only native Harness v0 boundary**

Bound Host revision: `2d340021887cae1d4053a3cf33d28093d344bc78`

Pinned Protocol revision: `ca5af401eda77d1081487c2df07ce9d94003719e`

Deterministic suite at closure: `243 / 243`

## Closure decision

OH1–OH5 establish a first-party Harness for bare model APIs that can execute one bounded multi-turn read-only workload through Ordivon Runtime, retain its authority and evidence in Ordivon Host, recover from a lost read-only Harness process, and refuse to erase unresolved effect uncertainty.

The completed boundary is:

```text
OH1–OH3  native execution plane
OH4–OH5  Host control, evidence and recovery plane
```

This closes issue #21 as implemented. It does not claim a general effectful Agent Harness or production maturity.

No additional OH stage should begin merely to extend the sequence. Further implementation requires a new evidence-backed gate.

## Why the five stages belong together

```text
OH1  a model call is not an Agent Run
OH2  a Tool Call is not physical execution truth
OH3  a model conclusion is not Task completion
OH4  a Context is not Task authority
OH5  an absent receipt does not prove that nothing happened
```

Together, these statements define the native Harness boundary more accurately than a feature list.

## Evolution map

```text
TaskContract / TaskAttempt / Assignment                 Host authority
                         ↓
CompiledContext → Provider messages                     cognition projection
                         ↓
AgentTurnAdapter → sequential model–Tool loop           Harness execution
                         ↓
Assignment-scoped ToolGrant → RuntimeToolBridge         admitted physical action
                         ↓
Runtime Workspace / Job / Artifact                      physical truth
                         ↓
Trace / Observations / HarnessRunReceipt                 retained Run evidence
                         ↓
CompletionVerification / Decision / TaskOutcome          Host adjudication
                         ↓
RecoveryAssessment / RunAbandonment                     lost-process control
```

## Stage summary

| Stage | Question closed | Principal implementation | Evidence |
|---|---|---|---|
| OH1 | Can a bare model call become a bounded Agent Run without redefining Host Tasks? | Provider-neutral Turn types, sequential loop, budgets, cancellation, immutable Trace and explicit stop codes | deterministic Tool→Observation→conclusion loop |
| OH2 | Can model Tool Calls lower to production Runtime without blind redispatch? | Runtime catalog binding, ACI, stable request identity, Job/Artifact correlation and UNKNOWN handling | live Runtime read and exec probe; response-loss reconciliation tests |
| OH3 | Can the loop consume Host Context and a real bare model API? | Harness Context compiler, semantic input projection and DeepSeek Flash adapter | two real model calls, one Runtime read and independent acceptance |
| OH4 | Can a fresh Host reconstruct and adjudicate the complete native Run? | TaskContract, ToolGrant, NativeHarnessRunContract, durable Trace/Observations/Receipt and CompletionVerification | three fresh-Host reopen boundaries and accepted TaskOutcome |
| OH5 | Can the Host distinguish safe replacement from unresolved uncertainty after process loss? | RecoveryAssessment, RunAbandonment, Provider fault taxonomy, stale-result fencing and replacement admission | lost read-only process, proven Workspace closure, stale result rejection and successful generation-two Run |

## Capability matrix

| Capability | Closure status | Exact boundary |
|---|---|---|
| Sequential bare-model Agent loop | verified | one Run-local message history; no parallel Turns or Tools |
| Production Runtime Tool path | verified | current catalog-backed ACI only |
| Real model Provider | verified | `deepseek-v4-flash`, non-streaming, thinking disabled |
| Host-owned Task authority | verified | Harness cannot directly commit TaskOutcome |
| Durable Task Contract | verified | objective, acceptance criteria, constraints and resource refs are independent from Context projection |
| Assignment-scoped Tool authority | verified | explicit ToolGrant and path/check constraints |
| Durable native Run identity | verified | one Assignment generation authorizes one Run ID before Provider or Runtime activity |
| Stable Runtime request identity | verified | assignment/run/step-bound client request identity; no uncertain redispatch |
| Durable Trace and Tool Observations | verified | Host CAS objects with historical validation |
| Runtime Job and Artifact provenance | verified | receipt references must derive from retained Observations |
| Independent completion verification | verified | full CompletionVerification object retained before TaskOutcome |
| Fresh-Host normal lifecycle recovery | verified | Assignment, Run, Proposal, Verification and Outcome reload across processes |
| Read-only lost-process recovery | verified | automatic abandonment only after proven Workspace closure/absence |
| Provider failure taxonomy | verified | timeout, transport failure, rejection, unavailability and generic failure remain distinct |
| Runtime UNKNOWN fencing | verified | no replacement while Tool delivery or result remains unresolved |
| Stale late-result rejection | verified | superseded before Trace, Observation, conclusion or receipt CAS writes |
| Historical semantic audit | verified | deep history Doctor validates native Contract, Run, Recovery and Completion references |
| Effectful process-loss continuation | not implemented | mutation/process-capable uncertainty remains BLOCKED |
| Durable individual Tool-step identity | not implemented | Runtime request identity exists, but no Host-owned resumable Tool-step lifecycle |
| Recorded Runtime UNKNOWN reconciliation lifecycle | not implemented | fenced and surfaced to the operator, not automatically resolved |
| Durable Workspace release disposition | not implemented | recorded Run replacements retain the same Workspace unless abandonment already proved cleanup |
| Provider Session continuation | intentionally absent | Provider sessions are not Task truth |
| Parallel Tools, subagents, router, Skills/Plugins | intentionally absent | no evidence that these improve the current bounded workload |
| Harness daemon or separate database | intentionally absent | Host CAS and journal remain the only durable owner |

## Code ownership at closure

The repository contains three different Harness concerns that must not be conflated.

### External Harness boundary

```text
harness/codex_app.py
harness/hermes_acp.py
```

These preserve Provider-faithful mature Harness lifecycles. They are not implementations of the first-party native loop.

### Host Harness control plane

```text
harness/models.py
harness/contracts.py
harness/host.py
harness/recovery.py
harness/recovery_controller.py
harness/runtime_refs.py
```

This layer owns durable Attempt, Assignment, Contract, Run, Recovery, Completion and Runtime-reference semantics.

### Native Ordivon Harness execution plane

```text
harness/ordivon/model.py
harness/ordivon/input.py
harness/ordivon/loop.py
harness/ordivon/tools.py
harness/ordivon/deepseek.py
harness/ordivon/events.py
harness/ordivon/result.py
harness/ordivon/manifest.py
```

This layer owns only the live model–Tool–Observation loop and its Provider/Runtime adapters.

At closure, production Harness code totals approximately 8,367 lines. The total includes the external Codex and Hermes drivers; it is not the size of the native loop alone.

## Verified invariants

1. A model Turn, Harness Run, Host Task and Runtime Job have different identities.
2. The Harness cannot create or revise authoritative Task semantics.
3. CompiledContext is a bounded projection of a durable TaskContract, not the Contract itself.
4. Runtime physical capability and Assignment-granted model capability are separate.
5. One native Assignment generation authorizes exactly one native Run ID.
6. Uncertain Runtime delivery is never automatically redispatched.
7. A model-declared Artifact or evidence reference is advisory until derived from Host-retained evidence.
8. Completion requires independent Host verification.
9. A missing Run receipt is not interpreted as proof of no action.
10. Workspace cleanup does not prove that a prior mutation or process effect never happened.
11. Runtime UNKNOWN cannot be cleared by replacement.
12. A stale process cannot regain authority after Recovery or Abandonment advances the Task.

## Frozen live evidence

```text
evidence/ordivon-harness-oh3-deepseek-live-20260801.json
evidence/ordivon-harness-oh4-deepseek-live-20260801.json
evidence/ordivon-harness-oh5-recovery-live-20260801.json
```

The chronological experiment record remains in `docs/ordivon-harness-v0.md`.

The latest OH5 live path proved:

```text
generation 1: one production Runtime read, no persisted Run receipt
fresh Host: Workspace closure → RecoveryAssessment → safe RunAbandonment
late generation-1 result: rejected before CAS writes
generation 2: two DeepSeek calls + one production Runtime read
final state: independently verified TaskOutcome at Task revision 8
```

## Structural debt retained deliberately

### Large HarnessHost

`harness/host.py` is approximately 1,953 lines. The largest methods combine durable object validation, event admission and snapshot reconstruction.

This is real structure debt, but it is not yet evidence for a new service, database or workflow framework. A future internal split may separate Assignment, Run, Recovery, Completion and snapshot-loading modules while preserving one Host authority.

### Tool consequence inference by Tool name

`native_tool_grant_effect_class()` currently infers read, mutation and process consequences from known Tool names. This is correct for the current catalog but is not a safe long-term extension mechanism.

A new effectful Tool must not become implicitly read-only because a recovery switch was not updated.

### Concentrated RuntimeToolBridge lowering

`RuntimeToolBridge._lower()` combines Tool argument validation, Grant admission, Runtime request construction, identity binding and error classification. It should not be split before Tool consequence metadata is explicit, or the same switch will be reorganized twice.

### Repeated live-script mechanics

OH4 and OH5 live scripts repeat Runtime setup, Context compilation, Workspace lifecycle, Provider configuration and evidence writing. Before a third large live scenario is added, mechanical support may be extracted without abstracting the distinct experiment semantics.

## Promotion rule

The read-only v0 boundary is promoted as complete. The next implementation boundary must address effect semantics before effect continuation. The code-backed E1–E2 design and rejected alternatives are recorded in [`ORDIVON_HARNESS_E1_E2_DESIGN.md`](ORDIVON_HARNESS_E1_E2_DESIGN.md).

The ordered gates are:

```text
E1  Tool consequence metadata
E2  unified internal RunDisposition derivation
E3  one durable prebound run_check Tool step
E4  effectful fault matrix
E5  mutation lifecycle
```

### E1 — Tool consequence metadata

Replace Tool-name-based recovery inference with explicit metadata such as:

```text
effectClass
executionMode
deliverySemantics
reconciliationMode
resourceScope
verificationMode
```

E1 does not implement continuation.

### E2 — Unified RunDisposition

Derive replacement, completion, Workspace-continuity and operator-action decisions from one internal pure model rather than reproducing them across Host admission, handoff, recovery and history validation.

E2 is an internal Host refactor, not a public workflow protocol.

### E3 — Durable prebound `run_check`

The first effectful continuation slice should use a fixed `checkId`, not arbitrary mutation or opaque execution. The Host must persist a Tool-step intent before Runtime delivery and reconcile the original Job after process loss.

### E4 — Effectful fault matrix

Exercise failure before dispatch, response loss, Job creation, Job completion, Observation persistence and stale duplicate results without redispatching the physical operation.

### E5 — Mutation lifecycle

Only after E3–E4 should the system attempt a durable read → mutate → diff → check → verify → complete workload.

## Deferred directions

The following are not next-stage requirements:

- Provider Session persistence;
- streaming;
- parallel Tool Calls;
- subagents;
- model routing;
- Context compaction infrastructure;
- Skills, Plugins or Hook platforms;
- Harness daemon;
- standalone Harness database;
- automatic retries after uncertain delivery.

They require separate evidence and must not be bundled into effect continuation.

## Delete or narrow condition

Narrow or delete the native Harness if one-shot gateways or mature Provider Harnesses provide equal correctness, recovery and portability for all retained bare/local-model workloads at lower permanent cost.

The current implementation remains justified because it demonstrates a first-party bare-model path with Host-owned authority and conservative recovery semantics that external Harness sessions do not define for Ordivon.
