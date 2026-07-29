# H series: open proposal and consequence-aware commitment

## Status

Implemented as the first bounded Host-local slice. It is not yet a promoted cross-project Protocol and does not replace the closed-choice profile for deterministic or closed-domain workloads.

## Why this exists

The earlier cognition path proved persistence and deterministic admission by requiring a model to select one of two to eight exact CandidateActions. That remains useful for fixtures, recovery tests, and domains whose legal action set is already closed.

It is insufficient as the only Host interface because stronger cognition must be able to propose a useful action that the Host did not enumerate in advance.

The H-series boundary is:

```text
Goal + Task + version-bound Context + available resources
→ model ActionProposal
→ deterministic Host validation
→ consequence and ownership classification
→ capability-profile resolution
→ lower into a proven workload, request a participant decision, or reject
→ Effect / Binding / Dispatch remain Host-generated
→ Runtime execution
→ Observation / Verification / TaskOutcome
```

## Stable ownership

### Model or Provider

The model may propose:

- semantic intent;
- target resource and revision;
- expected result;
- rationale and preconditions;
- affected resources and participants;
- reversibility and consequence class;
- a candidate method;
- a verification plan.

The model may not self-assign:

- Effect identity;
- EffectBinding identity;
- Dispatch identity;
- Runtime request identity;
- capability grant;
- completion or verification authority.

### Host

The Host owns:

- durable Task and ModelInvocation identity;
- Context source and revision binding;
- proposal persistence;
- stale-resource and wrong-profile rejection;
- consequence classification at the commitment boundary;
- capability-profile resolution;
- lowering into an exact executable workload;
- response-loss and commit-gap recovery;
- Observation, Verification, and TaskOutcome continuity.

### Domain and Runtime

The domain remains authoritative for world rules and domain-specific verification. Runtime remains authoritative for Workspace, Job, process, file, and physical execution state.

## First lowerer

Only one lowerer is implemented:

```text
private reversible repository-file observation
→ DeterministicReadHost
→ workspace.read
```

This deliberately reuses the existing verified read slice instead of creating a generic Tool-call language.

The lowerer checks:

- exact Context and Task identity;
- selected capability profile;
- available ResourceBinding;
- current repository revision;
- repository-file target shape;
- observation intent;
- private and reversible consequence;
- resource ownership and affected participants;
- supported selector fields;
- capability profile admission.

Unsupported or stale proposals return a stable `ProposalRejection`. Shared, foreign-owned, irreversible, or unknown consequences produce a `DecisionRequest` addressed to the responsible participant rather than a hard-coded human.

## Capability profiles

Two explicit profiles establish the first comparison:

### `profile:owner-trusted-local-v1`

The local owner may use Agent semantic actions on private repository resources. This profile represents physical and semantic reach. It does not bypass consequence admission.

### `profile:public-bounded-v1`

Only repository-file observation is admitted. Source change is denied.

The separation is intentional:

```text
capability profile
≠
permission for every consequence
```

The historical `TrustedLocalAuthorizer` remains limited to the already proven repository read and source-change actions. Existing workloads are not silently broadened; the open path must select `owner_trusted` explicitly after consequence classification.

## Persistence and recovery

The open proposal uses the existing durable cognition boundaries:

```text
persist Context
→ persist ModelInvocationIntent
→ release Task lease
→ call replaceable Provider
→ reacquire Task lease
→ persist ActionProposal and output observation
→ resolve proposal
```

The model call never holds the Task lease.

The first slice proves:

- repeated admission after a lost response returns the retained receipt;
- a child Task committed before the parent resolution can be recovered without creating a second child;
- each Runtime read frontier can be advanced by a fresh HostStorage instance;
- the parent Task completes from the child TaskOutcome;
- Runtime Workspace cleanup is independently checked;
- MCP Session identity remains transport state and is never stored as Task truth.

## Live evidence

`scripts/live_open_read_proposal.py` runs one real Codex turn with:

- ephemeral session;
- read-only sandbox;
- no Host tools exposed to the model;
- structured ActionProposal output;
- current repository ResourceBinding;
- live Ordivon Runtime execution;
- fresh HostStorage open per child frontier;
- independent read verification and Workspace-close check.

The immutable receipt is stored in `evidence/host-live-open-proposal-20260729.json`.

## Non-goals

This slice does not create:

- a universal planning language;
- a generic Tool-call API for models;
- a policy platform;
- a complete participant identity or legal-personhood system;
- DecisionRequest approval semantics;
- a DAG or scheduler;
- automatic lowering for mutation, network, finance, security, or Game actions;
- a promoted Protocol object.

The default repository-read `OpenProposalHost` still stops at a persisted DecisionRequest and does not consume a response. Round 1 adds a Host-local immutable response lifecycle for experiments and products, but integrating it into this read-only path remains unjustified without a consequential second workload.

## Promotion and deletion rule

The open path earns broader use only if additional real workloads show more accepted outcomes or less human interruption without weakening evidence and recovery.

If it merely adds ambiguity, token cost, or duplicated workload code, retain the closed-choice profile and delete the generalization.

## Round 1 research extensions

The core work-system comparison adds three Host-local extensions without
broadening the default open-proposal execution path:

- source-bound Context provenance and explicit invalidation;
- an evidence-rich, expiring, revocable DecisionRequest lifecycle;
- a bounded repository mutation proposal compiler that reuses
  `GuardedMutationHost` but is not registered in `OpenProposalHost`.

A compact operator handoff projection exposes UNKNOWN work as
`reconcile-existing-dispatch`. These extensions remain product and experiment
objects. Promotion requires evidence beyond revision-filtered retrieval, static
consequence policy, idempotency/audit, and durable workflow state.
