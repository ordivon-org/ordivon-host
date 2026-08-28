---
schema_version: 1
id: host.architecture
title: Ordivon Host architecture boundary
type: architecture
profile: engineering
lifecycle: active
source_role: canonical
visibility: public
owners:
  - ordivon-host
audience:
  - builder
  - operator
  - agent
updated: 2026-08-28
summary: Canonical architecture for durable semantic Task continuity, Host-owned Journal/CAS authority, bounded collaboration-message persistence, handoff/inspection, opaque extension durability, and local operational integrity.
evidence_status: verified
readiness: READY
applies_to:
  - ordivon-host
related:
  - host.start
  - host.quickstart
  - host.status
  - host.operations
  - host.data-privacy
  - host.releases
  - host.authority
---
# Ordivon Host architecture boundary

## Purpose

Preserve semantic work across replaceable Agent sessions and execution processes while refusing to mirror owner-current Runtime, Git, Provider, Harness, or domain state into a second authority.

Host 0.5.x is intentionally smaller than historical Host designs. The current architecture is the engineering result of the Host Deep Foundations and subsequent destructive ownership falsifiers.

## Research authority / navigation

This document remains the current engineering architecture authority. The consolidated research boundary, HDF0–HDF43 provenance, derived cross-owner coordination view, owner bridges, and C1–C9 consumption/falsification history are indexed at [`research/README.md`](research/README.md). Product implementation is downstream evidence and must not redefine the frozen HDF corpus backward.

## Identity and ontology

`Host` is the stable product/compatibility name of this component. It is **not** a primitive universal Host ontology, central coordinator, global scheduler, or cross-owner truth service.

The current identity law is:

```text
Host
= durable semantic Task continuity
+ Host-owned Journal/CAS admission
+ bounded handoff/inspection
+ opaque extension durability
+ local operational integrity
```

and explicitly not:

```text
Host
= universal coordination
+ foreign execution
+ Runtime proxying
+ cognition execution
+ source mutation
+ capability/validity oracle
```

A stable package or service name may survive an ontology contraction. Compatibility is not ontology.

## Responsibility matrix

| Component | Owns | Explicitly does not own |
| --- | --- | --- |
| Host | durable Task identity/projection, Journal/CAS admission, WorkingCheckpoint continuity, bounded collaboration-message persistence/order, Host leases/revisions, opaque extension durability, bounded Host inspection, Host operations | model cognition, Runtime state, source currentness, domain truth, generic coordination/priority adjudication, generic execution, global authority |
| Runtime | Workspace lifecycle, Jobs/Attempts, process trees, Artifacts, cancellation, physical reconciliation | Task semantics, Agent Run policy, domain completion |
| Harness | Agent Runs, Provider adapters/calls, model–Tool execution, Run-local continuity/recovery, Trace/Receipt evidence | Host Task authority, Runtime supervision, domain authority |
| Git/repository owner | repository history and source currentness | Host Task continuity |
| Domain owner | domain state, transition meaning, domain policy, verification sufficiency | Host Journal authority |
| Computing | promoted cross-project contracts and conformance evidence | owner-local runtime/domain truth |

## Current components

The production implementation consists of:

- one schema-v8 SQLite Host Journal, including durable collaboration-message and daily-news publication pointers;
- one materialized `TaskProjection` checked against Event history;
- immutable typed CAS objects;
- a minimal `HostKernel` for lease/revision-fenced local admission;
- `ExternalContinuityHost` plus `WorkingCheckpoint` models;
- generic extension admission with opaque namespace state;
- bounded context-selection structures retained for the Security consumer;
- a small set of typed compatibility value objects retained for real consumers;
- Doctor, full-history validation, backup/restore, GC planning, and receipt-bound deployment;
- one authenticated eleven-Tool MCP projection, with board, daily-news publication, and external-continuity responsibilities kept distinct.

There is no product Runtime client, source-read engine, mutation engine, code-change engine, cognition execution host, foreign executor coordinator, automatic Task reconciler, shared Goal coordinator, or generic capability-policy module.

## External continuity

`ordivon.host.external-continuity.v1` is the current first-class continuity workload. It is designed for work driven by replaceable Agent surfaces such as ChatGPT without turning conversation state into authority.

A `WorkingCheckpoint` stores:

- objective;
- current frontier;
- established claims;
- unresolved claims;
- rejected routes;
- constraints;
- next actions;
- optional Runtime/Git navigation hints.

Its `truthRole = semantic-working-claim`. Navigation hints point a future Agent toward revalidation; they are not copied physical truth.

Each admitted external-continuity checkpoint revision may also retain an optional `writerLabel` in the **Event payload**, separate from WorkingCheckpoint semantic content. The label is self-asserted provenance for who claims to have performed that Host write; it is not authenticated identity, ownership, priority, or execution authority. Keeping it outside WorkingCheckpoint prevents a writer change from changing the semantic checkpoint digest or being accidentally inherited by checkpoint patches. Historical revisions written before this provenance field remain explicitly `unrecorded` rather than being reconstructed from conversation history.

### Adoption and crash safety

New adoption revision 1 atomically references the immutable `TaskDescriptor` and initial checkpoint seed. The normal path then records the same initial semantic checkpoint at revision 2. A crash therefore cannot leave a newly admitted continuity Task with identity but no recoverable semantic seed.

### Exact response-loss replay

If `expectedRevision = r` commits a checkpoint at `r+1` but delivery is lost, replaying the identical checkpoint and continuity disposition against the same `r` returns `admission=existing`. A different claim, different terminal disposition, or later Task revision fails closed.

This is revision coordination, not a distributed lock and not a general Coordination project.

### Resume boundary

`task.resume` returns one revision-coherent projection containing the Task, handoff, WorkingCheckpoint, and revision-bounded extension namespace metadata. It never calls Runtime, Harness, Git, a Provider, or a domain owner.

The consumer loop is:

```text
Host semantic checkpoint
→ owner-native revalidation
→ continued external work
→ new Host semantic checkpoint
```

## Host MCP

The MCP server is loopback-only, bearer-authenticated, stateless at the transport layer, and opens Host authority state per request.

Exactly eleven Tools are public:

```text
host.status
board.list
board.post
news.list
news.read
news.publish
task.observe
task.list
task.resume
task.adopt
task.checkpoint
```

Observation and mutation are separated by responsibility:

- `host.status` reports Host-owned operational/integrity state only;
- `board.list` reads bounded durable collaboration messages; omitted `afterSequence` gives a newest window and an explicit sequence supports incremental polling;
- `board.post` admits one replay-safe collaboration message; author labels remain self-asserted, and persistence/order does not create priority, authority, owner standing, authenticated identity, or domain truth;
- `news.list` / `news.read` expose durable daily-news publication revisions, and `news.publish` admits one revision-fenced external-news edition without making its claims World truth;
- `task.observe` is a bounded Host Task projection and exposes recorded self-asserted checkpoint-writer provenance without exposing raw Event payloads;
- `task.list` discovers external-continuity Tasks with stable pagination; its `READY` projection is Host continuity lifecycle only, not current-work allocation, priority, owner standing, or domain currentness;
- `task.resume` recovers one exact semantic continuation point; its frontier/next actions remain working claims until current owner facts are revalidated;
- `task.adopt` creates/replays one explicit continuity Task and may record a self-asserted writer label for the admitted revision;
- `task.checkpoint` admits one revision-bound semantic checkpoint and may record the same bounded provenance separately from WorkingCheckpoint content.

The server does not proxy Runtime/Harness, invoke Providers, execute model loops, schedule work, or infer foreign currentness.

## Durable state protocol

The Host state root has two durable mechanisms:

```text
objects/       typed immutable content-addressed JSON envelopes
host.sqlite3   Event admission, stream heads, Task projection,
               object validation, schema migrations, short leases,
               opaque extension namespace pointers,
               durable collaboration-message pointers
```

A Host Task transition follows:

```text
1. canonicalize typed payload/object bytes;
2. fsync immutable objects before reference admission;
3. acquire SQLite IMMEDIATE transaction;
4. compare exact stream revision and lease owner/generation/expiry;
5. reject terminal reopening and invalid causal/reference state;
6. append Event + stream head + object refs + resulting projection atomically;
7. consume the exact lease generation and commit.
```

The materialized Task projection is a checked cache, not a second truth store. `object_refs` is the complete CAS **retention inventory**, not a declaration that every retained object belongs on every startup validation path. Schema v7 records `validation_timing = startup|on_access`: Task/Event/extension objects remain startup-critical, while collaboration-message CAS is retained as on-access history. The validation-timing index makes selection proportional to the startup-critical subset rather than the complete retention table. `ContentAddressedStore` likewise does not enumerate the whole CAS directory during ordinary construction: retained object mode/integrity is enforced when that object participates in startup validation or is accessed, while explicit Doctor/backup/GC retain whole-authority scanning responsibilities. Ordinary Host open therefore validates the Task-critical subset and current Task heads without scanning Board history; `board.list` validates each accessed Board reference/object, and explicit Doctor/backup performs whole-authority validation including on-access objects.

Expected invariants include:

- failed SQLite commit cannot create a partial semantic commit;
- two writers on the same stream revision cannot both commit;
- identical Event replay is idempotent;
- Event identity reuse with different bytes fails closed;
- history gaps, projection drift, missing objects, and object corruption fail closed;
- successful non-creation transition consumes the exact lease generation;
- terminal Task identity cannot be reopened;
- Board admission is append-only; its high-water sequence is the cheap current count, while explicit Board Doctor proves sequence continuity, reply integrity, reference classification, and row ↔ immutable-message semantics;
- Board pointers resolve to retained `host-board-message` CAS objects classified `on_access`; an accessed Board page fails closed on missing/corrupt/misclassified message state, while unrelated ordinary Host open remains independent from old collaboration payload bytes;
- full Doctor and backup validate every retained CAS object regardless of validation timing, so deferred startup validation never becomes deferred integrity or retention.

## Minimal HostKernel

`HostKernel` owns only local persistence/admission mechanics:

```text
read current Task projection and Event head
→ acquire short Task lease
→ recheck revision/state/frontier/projection
→ workload builds bounded Host-local semantic objects
→ append at most one Event + resulting projection
→ consume lease on success or release on uncommitted exit
```

It is not a workflow DSL, planner, scheduler, Runtime adapter, Provider router, or policy engine. It never invokes a Provider or Runtime Tool.

## Opaque extension durability

Schema v5 separates current Task Event head from current opaque extension-owner state.

For each Task × namespace, Host may retain one content-addressed owner-state object plus the exact Event/revision metadata that authored it. `HostExtensionPort` preserves and fences those bytes without understanding owner-specific schemas.

The existence of namespace state means only:

```text
Host durably retained owner bytes by this Task revision.
```

It does **not** mean the owner is current, reachable, healthy, authorized, outstanding, or successful.

World is a real direct consumer of the Host extension/value compatibility surface. Harness remains independent and does not require Host as its persistence authority.

## Context-selection compatibility

The historical cognition execution/proposal stack was removed. The surviving `ordivon_host.cognition` surface is bounded context-selection data/compilation semantics consumed by Security.

This retained surface does not select a Provider, execute a model, lower proposals, authorize consequences, or own an Agent Run.

The rule is:

```text
useful context representation may remain
without preserving historical cognition execution ownership.
```

## Compatibility value types

A small set of generic value classes remains exported because real consumers use them, including references/envelopes and verification/outcome records.

These types are data compatibility surfaces, not lifecycle claims. In particular:

```text
ArtifactRef              != Host Artifact authority
DispatchEnvelope         != Host foreign-executor coordinator
ObservationEnvelope      != Host external currentness authority
VerificationReceipt      != universal verification sufficiency
TaskOutcome              != domain completion oracle
```

The owner interpreting a value remains responsible for its domain semantics.

## Failure modes

Host fails closed on:

- stale revisions;
- invalid/expired lease identity;
- terminal reopening;
- missing/corrupt CAS objects;
- Event/history gaps;
- projection drift;
- invalid extension-state fences;
- incompatible retained schema/history;
- attempts to treat foreign navigation hints as Host-owned current truth.

An external owner outage is not automatically a Host health failure.

## Verification

Current behavior is verified by source, deterministic tests, schema/migration tests, full-history Doctor, retained production-state verification, named real-consumer tests, MCP wire acceptance, and receipt-bound deployment evidence.

Historical Runtime/mutation/cognition experiments remain in `docs/history/` as research evidence. They are not alternate current architecture.

## Ownership

- **Host** owns its Journal/CAS, Task revisions/projections, lease admission, external-continuity WorkingCheckpoints, bounded Host handoff/inspection, opaque extension durability, and Host-local operations.
- **Runtime** owns physical execution and current Job/Workspace truth.
- **Harness** owns caller-neutral Agent Run and Provider/model–Tool execution semantics.
- **Git/repository owners** own source history/currentness.
- **Domain owners** own domain state, rules, policy, and verification sufficiency.
- **Computing** owns promoted cross-project contracts after cross-owner proof.
- **Provider/MCP sessions** are replaceable transport state and never own Host Task continuity.

No component becomes authoritative for another owner merely by persisting a reference to it.

## Promotion rule

A Host-local concept moves toward a shared Protocol or shared project only after materially different consumers reproduce the same non-bypassable responsibility and local/source-owner alternatives measurably fail.

The default is therefore:

```text
source owner
or consumer-local adapter
before shared Coordination ownership
```

A mechanism that adds ambiguity or cost without increasing accepted verified outcomes should be contracted rather than promoted.

## Historical ownership removals

The following historical Host responsibilities are intentionally absent from current production:

- shared Goal coordination;
- caller-neutral ExternalExecutor coordination;
- Runtime read/mutation/code-change workloads;
- cognition execution/proposal/decision orchestration;
- automatic `TaskReconciler`;
- generic capability authorization policy;
- product Runtime client/config/catalog/health proxy.

Their evidence remains in `docs/history/`. Reintroducing any of them requires a new concrete ownership falsifier/reopen condition; historical existence alone is not justification.
