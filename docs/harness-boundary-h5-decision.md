# Harness Boundary H5 Decision

Status: accepted architecture; Stage 1 closed

Evidence: [`../evidence/harness-replacement-h5-live-76420e4-20260731.json`](../evidence/harness-replacement-h5-live-76420e4-20260731.json)

Implementation revision exercised by the live experiment: `76420e4f1ab2d20799b09aad6497f195bd951aa7`

## Decision

Ordivon retains a Host-local Harness boundary expressed through durable Host objects and provider-specific direct drivers.

The boundary is:

```text
Host
  Task / Task Attempt
  Assignment generation and fresh Context
  Harness Run receipt
  CompletionProposal / CompletionDecision / TaskOutcome
  recovery and semantic completion

Provider Harness
  Session / Thread / Turn / Prompt
  model and Tool loop
  provider-local history, resume, fork, compaction, modes

Runtime
  Workspace / Job / Runtime Attempt
  process tree / physical execution / terminal evidence / Artifacts
```

No shared `HarnessAdapter` implementation, provider-independent Session format, common event runtime, or repository extracted from the Codex/Hermes direct drivers is created.

**Scope clarification:** H5 closes the shared cross-Provider lifecycle hypothesis. It does not reject a first-party **Ordivon Harness** for bare model APIs or local inference systems that provide model intelligence without a mature Agent Loop. That separate construction question is tracked by `ordivon-computing#90` (`ANC-HARNESS-002`).

## Experiment

The frozen workload `harness-replacement-repository-repair-v1` contains a deterministic largest-remainder allocation defect, versioned specification, frozen acceptance tests, and two required Artifacts:

```text
artifacts/diagnosis.json
artifacts/completion.json
```

H5 ran four real Provider invocations in two replacement orders:

```text
Codex diagnosis → Host replacement → Hermes repair
Hermes diagnosis → Host replacement → Codex repair
```

Each trajectory used:

- one durable Task;
- one stable Task Attempt;
- Assignment generation 1 for diagnosis;
- one diagnosis Artifact retained in Host CAS;
- a newly compiled Context;
- Assignment generation 2 for repair;
- one independent Runtime acceptance Job;
- one Host-adjudicated CompletionProposal and TaskOutcome.

The Provider Session from generation 1 was not resumed or transferred. Generation 2 continued from Host state, source state, and explicit Artifact evidence.

## Results

Both replacement orders completed the same semantic workload while producing different valid source implementations:

| Order | Final source digest | Result |
|---|---|---|
| Codex → Hermes | `sha256:1fb2d560fa109be2673b3d1834df8a441b6c88541b47fdd3c214a3bfd6095ebd` | accepted |
| Hermes → Codex | `sha256:d751a39f77c35bdb0f23cf91cc4da0410be54a740a53a4c5ecb60e9d689136e7` | accepted |

This proves that semantic completion should bind acceptance evidence, diagnosis, final source digest, and Artifacts. It should not require byte-identical implementation output across Harnesses.

Every Provider Job retained exact `ordivon.host` references for Task, Task Attempt, Assignment, and Harness Run in Runtime Terminal Evidence. Runtime owned every physical process and never claimed semantic Task completion.

## Fault results

### F1 — stale Assignment completion

The generation-1 diagnosis Harness proposed Task completion after generation 2 had already been committed.

Both old proposals were retained as evidence and rejected with:

```text
reasonCode = stale_assignment
```

The acceptance verifier was not invoked, no TaskOutcome was created, and stale adjudication dispatched no Runtime work.

**Decision:** retain Assignment generation. Task revision alone is not the tested worker fence.

### F2 — process success without required Artifact

A real Runtime process exited successfully and produced terminal physical evidence, but the required completion Artifact did not exist.

Host rejected the proposal with:

```text
reasonCode = missing_artifact
```

The Task remained `waiting`, the acceptance verifier was skipped, and Runtime success did not become Task completion.

**Decision:** retain CompletionProposal and CompletionDecision as a semantic admission boundary distinct from process success and TaskOutcome.

### F3 — ambiguous Runtime response

The successful response to the Codex→Hermes repair dispatch was deliberately dropped.

A fresh Host recovered the original Runtime Job from the Assignment-bound `clientRequestId` and exact foreign references:

```text
dispatchCalls = 1
matchingJobs = 1
responseLost = true
```

No repair was blindly redispatched.

**Decision:** retain the Host Runtime-reference convention and Assignment/run-bound request identity. No Runtime Task table or query service is required.

## Provider final text

Both Codex Runs returned usable structured final responses.

Both Hermes Runs completed their Tools and produced valid, independently verified Artifacts, but ACP final assistant text was absent. Host completion still succeeded because the Artifact, source digest, Runtime acceptance, and CompletionProposal were valid.

**Decision:** Provider final text is optional observation. It is not completion evidence and cannot be a required cross-provider field.

The canonical H5 result is reconstructed from verified Artifacts when Provider text is absent or structurally unusable.

## Provider event semantics

H4 established that Hermes may omit `tool_call_update` after emitting `tool_call`. H5 additionally showed that:

- ACP thought-event count is not interchangeable with provider thought-token accounting;
- a Provider may complete useful work without a final assistant message;
- Codex and Hermes expose different terminal and Tool semantics;
- normalizing these differences into one lifecycle would either lose evidence or invent events.

**Decision:** preserve provider-specific event models and normalize only durable Host facts.

## Cost

The four Provider invocations consumed approximately:

| Provider | Total tokens |
|---|---:|
| Codex | 114,111 |
| Hermes | 379,183 |
| Combined | 493,294 |

Hermes emitted substantially more messages, thought chunks, and Tool observations. The richer direct-driver path is therefore an explicit high-capability mode, not the universal default for bounded cognition.

The existing one-shot Codex and Hermes gateways remain lower-cost baselines.

## Retain

### `TaskAttemptDescriptor`

Retain one immutable semantic-attempt identity across Harness replacement. H5 used one Task Attempt for both generations and both Providers.

Its scope stays narrow:

- stable attempt ID;
- Task binding;
- objective digest;
- acceptance digest;
- starting Task revision.

It gains no mutable lifecycle, scheduler state, lease state, or independent table.

### `HarnessAssignment` and generation

Retain. Generation is the durable stale-worker fence. Every replacement compiles a fresh Context and can carry explicit prior Artifact references.

### `HarnessRunReceipt`

Retain. H5 required a durable link among:

- Assignment generation;
- Provider Session identity;
- Provider revision and event digest;
- Context and Tool catalog;
- Runtime Job identities;
- diagnosis, completion, source, and terminal Artifacts;
- usage and stop evidence.

Existing one-shot Model Invocation receipts do not express this cross-layer replacement evidence without losing distinctions.

### `CompletionProposal` and `CompletionDecision`

Retain. F1 and F2 directly demonstrate failures prevented by this boundary.

`TaskOutcome` remains the accepted terminal fact; it is not reused as an unverified proposal.

### Runtime foreign references and request identity

Retain the current Host-local builder and Runtime opaque-reference contract. F3 demonstrates recovery value without Runtime production expansion.

### Provider-specific direct drivers

Retain Codex App Server and Hermes ACP direct drivers as explicit capabilities. Keep them inside `ordivon-host` until another real consumer proves independent release value.

## Localize and shrink

### Provider modes and approvals

Hermes `session/set_mode("accept_edits")` remains a provider-local explicit operation. It does not become a generic approval policy abstraction.

The H4 default remains fail-closed for server-initiated permissions, filesystem, terminal, and Tool requests.

### Final response

Shrink to optional Provider observation. Verified Artifact evidence is authoritative.

### Task Attempt

Keep the immutable descriptor, but do not create a second Task lifecycle or attempt scheduler.

### Shared mechanics

Subprocess, queue, JSON, and receipt boilerplate may be deduplicated only when doing so deletes substantial code without flattening Provider semantics. H3–H5 do not meet that threshold.

## Delete or do not create

The following candidates are rejected by H5:

- shared `HarnessAdapter` implementation;
- provider-independent Session object;
- common Thread/Turn/Prompt lifecycle;
- synthesized universal Tool completion events;
- shared event bus or Harness runtime;
- automatic Provider resume/fork as Task continuity;
- Runtime-owned Task, Task Attempt, Assignment, or semantic completion state;
- Assignment, Run, or CompletionProposal SQL tables;
- Runtime foreign-reference query index;
- global Hook or policy framework for this boundary;
- a separate repository that packages the Codex/Hermes drivers behind one common internal lifecycle.

## Repository gate for the tested hypothesis

A repository extracted from the mature Provider direct drivers does not pass its promotion gate:

- two Providers exist, but no second independent consumer exists;
- lifecycle contracts are intentionally provider-specific;
- duplicate-code reduction is not large enough;
- direct drivers still depend on Host Assignment and receipt semantics;
- independent release value has not been demonstrated.

The Provider-specific code remains Host-local. A future first-party Ordivon Harness has a different gate: it must implement a real bare-model Agent Loop, support at least two bare-model adapters and two independent consumers, preserve Host/Runtime authority, and demonstrate independent release value before repository extraction.

## Operational consequence

For future replacement work:

```text
1. Commit a new Host Assignment generation.
2. Compile fresh Context from current Host state.
3. Pass explicit prior Artifacts.
4. Give the replacement Harness a new Provider Session.
5. Bind Runtime dispatch to the new Assignment and Harness Run.
6. Treat Provider text and Runtime exit as observations.
7. Admit completion only through Host verification.
```

This is the smallest architecture that survived both Provider orders and all three first-fault families.

## Next frontier

Host Harness Stage 1 is closed. Further work on the retained cross-Provider boundary should come from downstream evidence:

- Game may ablate retained mechanisms against real playable workloads;
- Security may attack generation fencing, Artifact provenance, response-loss recovery, and completion admission;
- Computing may compare the retained boundary against future external Harnesses or consumers.

A separate construction frontier now exists: `ANC-HARNESS-002` may implement a thin first-party Ordivon Harness for bare model APIs. It must consume the retained Host boundary rather than generalize it, and it must not normalize mature Provider Harness lifecycles.

No additional Host durable-state generalization is authorized before either downstream work or the Ordivon Harness v0 experiment exposes a concrete failure.
