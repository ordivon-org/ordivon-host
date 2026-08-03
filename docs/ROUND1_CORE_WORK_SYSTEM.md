# Round 1 core work-system Host surface

> **Historical Round 1 experiment support record:** This document preserves stage-specific decisions, measurements, or provenance. It is not a current Host architecture or operations source. Use [`../README.md`](../README.md), [`../ARCHITECTURE.md`](../ARCHITECTURE.md), [`OPERATIONS.md`](OPERATIONS.md), and [`authority.md`](authority.md) for the active boundary.

## Status

Implemented as Host-local research support for the Computing
`core-work-system-v1` experiment. None of these objects are promoted to
`ordivon-protocol`, and the default `OpenProposalHost` remains limited to the
proven repository-read path.

## Added boundaries

### Source-bound Context

`ContextSourceBinding` records the source identity, source revision, payload
digest, observation time, trust class, Claim status, selection method,
invalidation keys, selector identity, and material omissions. It composes with
an existing `ContextBlock`; it does not change the durable Context envelope.

`evaluate_source` compares the revisions bound when Context was compiled with
the current world revisions. It returns explicit invalidation reasons rather
than silently refreshing or trusting stale content.

### Evidence-rich DecisionRequest lifecycle

`EvidenceRichDecisionRequest` adds alternatives, evidence, unresolved Claims,
consequence, reversibility, authority and budget impact, cost of delay, world
revision, and optional expiry. `DecisionRequestLifecycle` rejects responses
from the wrong participant, stale world revision, expired or revoked requests,
and response objects bound to another request digest.

The lifecycle is an immutable Host-local value object. A product may persist it
through the existing content-addressed store and Task event history. It is not
a second decision database or universal approval queue.

### Bounded mutation proposal lowering

`RepositoryMutationProposalCompiler` lowers exactly one private, reversible,
version-bound root-file change into the existing `GuardedMutationPlan`. Shared,
foreign-owned, or non-reversible changes still produce a `DecisionRequest`.
The compiler is a Round 1 workload adapter and is deliberately not installed as
the default open-proposal lowerer.

### Operator handoff projection

`operator_handoff` derives a compact capsule from the authoritative current Task
snapshot. It exposes current state, frontier, relevant semantic references,
objects that must not be repeated, and the next admissible operation. An
UNKNOWN Runtime outcome projects `reconcile-existing-dispatch`; it never
suggests a new Effect identity.

## Strong-baseline result

The deterministic Computing matrix gives LangGraph checkpoints, Temporal
Workflow state, revision-filtered retrieval, idempotency plus audit, and durable
Activities the same application semantics as the Ordivon variant. Those mature
baselines pass their relevant failure cases. The Host additions therefore remain
local until live workloads demonstrate one of the following:

- source-bound invalidation prevents failures that current-source retrieval does
  not prevent;
- evidence-rich DecisionRequests reduce operator burden relative to a simpler
  static consequence policy;
- the typed handoff projection improves continuation beyond ordinary durable
  workflow state;
- Effect/Binding/Dispatch semantics add value beyond idempotency and Activity
  history on a second materially different backend.

## Non-goals

Round 1 does not add a general Workflow engine, Context Kernel, approval plane,
arbitrary natural-language Tool compiler, Provider router, or Protocol object.
Runtime production code receives no random fault-injection switches; response
loss, stale summaries, revision drift, and process replacement are owned by the
experiment harness.
