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
updated: 2026-08-08
summary: Canonical Host architecture for durable Task state, commitment, verification, uncertainty, extension admission, and recovery above Runtime.
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

Preserve durable work, commitments, uncertainty, evidence, and terminal outcomes while treating cognition sessions and physical Runtime processes as replaceable dependencies. The architecture was falsified and reduced in the Computing incubator before extraction into this repository.

## Boundaries

Host owns Task continuity and external commitment admission. Runtime owns physical execution, domain systems own authoritative world state and domain verification, Harness owns caller-neutral Agent Run semantics, Computing owns promoted contracts, and Git owns source history. A universal scheduler remains deliberately frozen.

### Component responsibility matrix

| Component | Owns | Explicitly does not own |
| --- | --- | --- |
| Runtime | Workspace lifecycle, physical Jobs and Attempts, process trees, bounded Artifacts, cancellation, and physical reconciliation | Task semantics, Assignment/Run policy, Provider behavior, or domain completion |
| Host | durable Task continuity, Journal/CAS, generic extension admission, commitment identities, verification records, and Task outcomes | Harness-specific schemas, model–Tool execution, physical process truth, or domain-world truth |
| Harness | caller-neutral Agent Runs, Provider adapters and calls, model–Tool execution, Tool-step checkpoints, Run-local recovery, Trace/Receipt evidence, and completion proposals | caller Task authority, generic Host persistence, Runtime supervision, promoted protocol, or final domain authority |

## Components

The current system consists of one Host Journal and materialized Task projection, immutable CAS objects, a minimal transition kernel, bounded cognition profiles, workload-specific Effect lifecycles, generic extension admission, Runtime clients, verification receipts, and operational recovery surfaces.

## External continuity

`ordivon.host.external-continuity.v1` is a narrow Host workload for work driven by an external Agent surface such as ChatGPT. It does not execute cognition, mirror conversation state, or proxy Runtime. While tracking remains active, the Task stays `READY` at one stable `continue` frontier and Host records bounded `WorkingCheckpoint` objects as semantic working claims. A final checkpoint may set the **continuity-tracking** disposition to `complete` or `abandon`, which atomically moves the Host Task to `COMPLETED` or `CANCELLED` with an empty frontier. Those terminal states describe only whether Host should keep presenting the work as active continuity; they do not assert success or failure in an external domain.

A `WorkingCheckpoint` records the current objective/frontier, established and unresolved claims, rejected routes, constraints, next actions, and optional Runtime navigation references. It explicitly has `truthRole = semantic-working-claim`: the checkpoint tells a future Agent where to revalidate truth; Runtime, Git, and domain authorities remain the source of current physical/domain facts. The Runtime reference is a navigation hint rather than copied Runtime truth.

Checkpoint bytes live in immutable CAS. For new adoption, revision 1 atomically references both the immutable `TaskDescriptor` and the initial checkpoint seed; the normal adoption path then records the same initial checkpoint as `task.context-checkpointed` at revision 2. This preserves the established revision cadence while eliminating the crash window in which a Task identity could survive without any semantic working claim. Historical descriptor-only revision-1 Tasks remain readable and can still be completed by exact adopt replay. Full-history Doctor validates a seeded creation checkpoint with the same semantic identity checks as later checkpoint events.

Checkpoint mutation is revision-safe. If `expectedRevision = r` commits at `r+1` and the response is lost, retrying the same checkpoint **and continuity disposition** returns `existing`; a different checkpoint, a different terminal disposition, or a Task that advanced beyond that exact transition fails with a revision conflict. Competing external sessions therefore coordinate through Task revision rather than conversation ownership or a distributed lock.

`task.resume` combines one revision-coherent `TaskProjection`, `OperatorHandoffCapsule`, checkpoint, and `extensionNamespaces` projection from that exact Task revision. `extensionNamespaces` means only that, by this revision, those extension namespaces had durable owner state retained; it does **not** mean an owner is available, current, outstanding, authorized, reachable, or successful, and it never embeds owner state fields. The routing projection is revision-fenced against first appearance and later owner updates, so a concurrent future namespace cannot leak backward into an older resume. `task.resume` never invokes Runtime, Harness, an owner inspector, or a Provider. READY external-continuity handoff projects the semantic action `continue-external-work` rather than leaking the internal `node:...:continue` frontier identity; terminal continuity has no next Host action. The intended recovery loop is: Host semantic checkpoint → intersect durable owner namespaces with currently available owner-specific inspection capabilities → Runtime/Git/domain revalidation → continued work → new semantic checkpoint.

H-C2 exposes this authority through a compact Host MCP surface. The MCP server is loopback-only, bearer-authenticated, stateless at the transport layer, and opens fresh Host authority state per request. Its six Tools separate observation from mutation: `host.status` and `task.observe` expose Host-owned operational/task state; `task.list` and `task.resume` expose external-continuity discovery and full semantic recovery; only `task.adopt` and `task.checkpoint` write continuity state. `host.status` deliberately does not proxy Runtime and binds installed deployment identity only when the running MCP module actually comes from the current release tree. `task.observe` exposes bounded head/timeline metadata rather than raw Event payloads. `task.list` remains an external-continuity discovery projection rather than a generic Host Task dump: active tracking is the default, terminal history is opt-in, immutable creation order drives query-bound cursor pagination, and each row carries only a bounded semantic preview plus the exact checkpoint digest/revision needed to decide whether to call `task.resume`. `task.checkpoint` accepts either a complete WorkingCheckpoint or a patch inherited from one exact expected revision; durable storage still contains a complete canonical checkpoint. Transport sessions, HTTP connections, client identity, and MCP request state are never persisted into Task continuity. The official MCP SDK owns protocol lifecycle while Host owns the semantic/read projections and exact revision admission.

```text
external Agent / ChatGPT
        │
        ▼
Host MCP  (auth + protocol only)
        │
        ▼
ExternalContinuityHost
        │
        ▼
Journal / CAS
        │
        └── navigation refs ──> Runtime / Git / domain truth
```

## Data flow

Participant or application intent becomes a durable Task and Context; replaceable cognition proposes or selects work; Host validates and commits Effect identity before delivery; Runtime executes; observations are independently verified; Host admits a terminal Task outcome or retains explicit uncertainty for reconciliation.

## Failure modes

Host fails closed on stale revisions, invalid leases, terminal reopening, missing or corrupt CAS objects, causal gaps, ambiguous external delivery, unsupported extension semantics, insufficient verification, and attempts to make transport or Provider state the owner of Task continuity. Deployment also treats executable bytes and durable authority schema as one compatibility boundary: a forward Journal migration has an exact preactivation SQLite snapshot for activation-only recovery, while a successfully accepted schema migration cannot be reversed by merely switching to an older release because that would either strand the newer authority or discard post-deployment facts.

## Verification

Exact behavior is verified by source, schema migrations, deterministic tests, live Runtime scenarios, fault injection, full-history Doctor, immutable evidence receipts, and version-bound source-change acceptance. The operational procedures are in [`docs/OPERATIONS.md`](docs/OPERATIONS.md), and the authority boundary is recorded in [`docs/authority.md`](docs/authority.md). Phase and closeout documents preserve the evidence behind this boundary but are not alternate current architectures.

## Ownership

- **Host** owns Tasks, Goal-scoped Task coordination, generic Host events and projections, Context compilation, semantic `CognitionWorkRequest` identity, proposal compilation or closed-choice admission, Effect commitments, Tool bindings, verification receipts, participant-routed decisions, and Task outcomes. It does not yet own a durable Goal stream or Goal commitment object. It admits immutable references and extension event kinds outside reserved Host namespaces without importing extension-specific schemas.
- **Runtime** owns Workspaces, committed physical Jobs, Runtime Attempts, process state, retained output, Artifacts, cancellation, and physical recovery.
- **Harness** owns caller-neutral Agent Runs, Provider adapters and calls, model–Tool execution, Run-local continuity and recovery, Trace/Receipt evidence, and completion proposals. Host is an optional caller, not Harness persistence authority.
- **Domain systems** own authoritative world state, transition rules, domain coordination policy, and domain-specific verification sufficiency.
- **Computing** owns promoted protocol definitions, reference behavior, conformance vectors, experiments, and evidence.
- **Provider and MCP sessions** are replaceable transport state and never own Task continuity.
- **Git** owns repository history and source revisions.

The Host controls durable work and external commitments. It does not own model intelligence, domain truth, physical execution, or a permanent hierarchy among participants.

## v0 constraints retained

- one SQLite Host journal;
- one materialized Task projection derived in the same transaction;
- immutable objects written before journal references are committed;
- every non-creation Task transition is fenced by one exact live lease generation and stream revision;
- terminal Task identities are irreversible;
- Task-local frontier state; `RUNNING` and `activeNodeId` remain legacy-readable but are not a current workload lifecycle;
- deterministic progress before external cognition execution;
- no automatic redispatch after an uncertain external Effect;
- TaskCapsule is an export/checkpoint, not the primary database;
- no independent Semantic Journal;
- no distributed scheduler, provider router, Web UI, or general workflow DSL.

## Durable state protocol

The Host state root contains exactly two durable mechanisms:

```text
objects/       typed, immutable, content-addressed JSON envelopes
host.sqlite3   event admission, stream heads, materialized projections,
               object validation, schema migrations, and short leases
```

A Task state change follows this order:

```text
1. canonicalize the typed event payload;
2. fsync the immutable object and its directory entry;
3. acquire a SQLite IMMEDIATE transaction;
4. compare the expected stream revision and exact lease owner/generation/expiry;
5. reject terminal reopening and dangling causal references;
6. admit the object reference, event, stream head, and Task projection;
7. consume the lease and commit them together.
```

The event payload binds the complete resulting `TaskProjection`. A new Host process validates every referenced CAS object and rebuilds each Task from its event head before accepting work. The materialized projection is therefore a checked cache, not an independent source of truth.

Expected consequences:

- a failed SQLite commit may leave an unreferenced immutable object, but cannot leave a partial semantic commit;
- two writers racing the same stream revision cannot both commit;
- a repeated identical event identity is idempotent;
- a repeated event identity with different payload or resulting projection fails closed;
- event-history gaps, stream-kind drift, projection drift, missing objects, and object corruption prevent startup;
- a lease and stream revision CAS are complementary admission predicates; neither can be bypassed for a non-creation transition;
- successful admission consumes the exact lease generation, while a lost or expired lease cannot write;
- terminal Tasks cannot be reopened under the same identity.

## Minimal HostKernel

Read, cognition, guarded mutation, and open proposal workloads share one mechanical transition kernel. The Kernel owns only the invariants independently repeated by those workloads:

```text
read current Task projection and event head
→ acquire the short Task lease
→ recheck revision, state, frontier, and projection equality
→ let the workload build semantic objects and the next event payload
→ append at most one event and resulting projection with lease + revision CAS
→ atomically consume the lease on success, or release it on an uncommitted exit
```

`HostKernel` and `LockedTask` therefore own lease lifetime, monotonic timestamps, current-state admission, one-transition-per-lock enforcement, and atomic event/projection commit. Workload code still owns Context compilation, proposal lowering or candidate admission, Effect and Binding construction, Runtime calls, delivery classification, reconciliation, independent verification, and TaskOutcome semantics.

The Kernel preserves these boundaries:

- it is not a workflow DSL, scheduler, DAG engine, provider router, Runtime adapter, or policy engine;
- it never invokes a Provider or Runtime Tool;
- workload-specific public exceptions are preserved through explicit error mapping;
- Provider invocation and effectful Runtime delivery remain outside the Task lease;
- external observations are collected outside the lease and must carry an independently recheckable version when the observed world can change before admission;
- only bounded deterministic state readers may run inside a short transition;
- one locked transition may append at most one Host event;
- existing workloads remain regression specifications for Kernel behavior.

## Deterministic Runtime read slice

The first vertical slice contains no model call. It advances one durable frontier per Host step:

```text
open-or-reconcile Workspace
→ bind and execute workspace.read
→ verify returned content independently
→ close-or-reconcile Workspace
→ completed TaskOutcome
```

The lifecycle Tools (`workspace.get`, `workspace.open`, and `workspace.close`) are deterministic coordination operations. `workspace.read` is the only semantic Effect in this slice and produces separate immutable objects for the Effect, current Tool contract snapshot, EffectBinding, Observation, VerificationReceipt, and terminal TaskOutcome.

The slice preserves these invariants:

- every frontier transition is committed before the next Runtime action;
- a new Host process can advance the next frontier without provider or process memory;
- a stable Workspace identity reconciles a crash after `workspace.open` but before Host commit;
- the Runtime Tool catalog is rediscovered and must match the bound catalog before the read;
- returned content is hashed independently and must match the Runtime digest;
- a failed read or failed verification leaves the Task at its prior revision;
- closing an already absent Workspace is reconciled as complete;
- presentation-only Tool metadata does not change catalog identity, while schemas and execution metadata do;
- semantic objects are admitted into the Host Journal transaction as references, not copied into the projection.

This slice deliberately does not admit a reusable Fact. A verified read completes with a `VerificationReceipt` and `TaskOutcome`; Fact promotion remains a separate cross-Task decision.

## Semantic cognition request and admission

Host has one durable pre-execution cognition boundary:

```text
READY Task + exact frontier
→ compile bounded semantic Context
→ persist CognitionWorkRequest(resultKind, Context, exact resulting Task revision)
→ move Task to WAITING
→ execute cognition outside the Host lease
→ return semantic result + CognitionExecutionEvidence
→ reacquire the exact Task revision
→ re-read current authority/world state
→ admit, lower, route to DecisionRequest, or reject
```

`CognitionWorkRequest` deliberately contains no Provider, gateway, Adapter, session, model, process, retry, or transport identity. Those are execution facts owned by the executor, normally Harness. Host needs only enough durable state to answer **what semantic result is currently authorized for this Task revision?**

For closed-choice work, `resultKind=action-selection`. Context contains two to eight exact `CandidateAction` values. The executor returns an `ActionSelection`; Host rechecks Context identity, current world digest, completed Effects, unresolved Dispatches, and the exact allowed action before advancing the frontier. Invented actions, stale world requirements, duplicate Effects, and stale Task revisions fail closed.

For open work, `resultKind=action-proposal`. Context contains resources, constraints, capability profile, participant responsibility, and a proposal contract, but no prebuilt action menu. The executor returns an `ActionProposal`; Host checks identity, revision, ownership, reversibility, consequence, and authority before lowering it, creating a `DecisionRequest`, or rejecting it.

Only one lowerer is currently proven:

```text
private reversible repository-file observation
→ existing DeterministicReadHost
→ verified workspace.read
```

Shared, foreign-owned, irreversible, and unknown-consequence proposals do not self-authorize. They create a `DecisionRequest` addressed to the responsible participant. A human is one possible participant, not a universal hard-coded recipient.

The durability invariant is intentionally smaller than the H2 design: if execution fails, the Task remains on exactly one `cognition.requested` head. Host does not persist a second model-invocation intent, does not select a Provider, and does not perform Provider retry. Harness Run/Provider continuity supplies execution-side crash recovery without duplicating that authority in Host.

## Independent Harness extension boundary

Agent Harness implementation no longer lives in this repository. It was extracted with source history into the independently versioned `ordivon-harness` repository.

The dependency direction is deliberately one-way:

```text
ordivon-harness / another external cognition executor
  Agent Run / Provider execution / model–Tool lifecycle
             ↓ semantic result + evidence
ordivon-host
  Task / Context / CognitionWorkRequest / admission / commitment / verification
             ↓
ordivon-runtime / ordivon-protocol as required by their own boundaries
```

Host does not import `ordivon-harness`. The Host kernel accepts immutable lowercase dotted event kinds outside reserved Host namespaces, stores them exactly in the Journal and event payload, and reconstructs one thread-stable interned `EventKind` value after process restart. Misspellings under `task.*`, `cognition.*`, `effect.*`, `verification.*`, `runtime.*`, or `wakeup.*` fail closed. Generic Host Doctor validates event continuity, payload bytes, Task projections and every referenced CAS object. It deliberately does not decode Harness Assignment, Run or Recovery semantics.

Schema v5 also separates the **current Task Event head** from **current opaque extension-owner state**. Each Task × extension namespace may retain one content-addressed state object and the exact Event/revision that authored it. `HostExtensionPort.load_namespace(taskId, namespace)` preserves the compatibility view of owner state plus current `TaskProjection`; `load_namespace_snapshot(taskId, namespace, expected_revision=...)` is the stronger read boundary for owner inspection, returning the same opaque state together with Host-owned namespace metadata (`owner_event_id`, Event kind, state digest, owner revision, legacy bit) under one optimistic Task-revision fence. A later Host core Event or a write from another namespace cannot erase that retained state or be silently mixed into an older inspection snapshot. Host preserves and fences these bytes without understanding their field names. Namespace state and metadata are durability/routing evidence only: their existence does not imply that an owner is currently available, that its state is externally current, that work is outstanding, or that any authority has been granted.

The Harness extension owns:

- its event-kind constants;
- Task Contract, Attempt, Assignment, Tool Grant and Run Contract models;
- Provider adapters and the first-party bare-model loop;
- Harness-aware operator handoff;
- Harness semantic history validation and its own Doctor command;
- Harness-specific tests, fixtures, live scripts, documents and evidence.

Retained legacy Harness extension objects may still exist in Host CAS and remain readable through generic extension admission. New independent Harness Runs own their own Journal/CAS and appear to Host only through external-executor request, binding, observation, and completion-proposal references. Host Task continuity therefore does not imply ownership of Harness Run bytes.

This split does not add a second Host Task projection or require Host to import Harness.

## Capability and consequence separation

Capability profiles express the semantic and resource reach available to a participant. Consequence admission decides whether that reach may be committed for the current proposal.

```text
physical credentials
+ capability profile
≠ automatic permission for every external consequence
```

`owner_trusted` admits Agent semantic actions on private repository resources. `public_bounded` admits repository-file observation only. Both still pass through proposal consequence classification and domain verification.

## Guarded mutation and uncertain delivery

The first changing vertical slice uses one keyed `workspace.exec` Dispatch to create one exact proof file in a disposable Runtime Workspace. The Host persists the Effect, current `workspace.exec` Tool contract, EffectBinding, stable `clientRequestId`, and Dispatch intent before crossing the Runtime boundary.

```text
open-or-reconcile Workspace
→ persist Effect + Binding + Dispatch intent
→ release the Task lease
→ deliver workspace.exec once
→ classify a lost response as UNKNOWN
→ find the original Job by clientRequestId
→ observe that exact Job to terminal state
→ verify the changed file independently with workspace.read
→ force-close the dirty disposable Workspace
→ completed TaskOutcome
```

The mutation boundary preserves these invariants:

- the external call never occurs before its exact Dispatch identity and request digest are durable;
- the Task lease is not held during `workspace.exec` or asynchronous Job observation;
- a transport or protocol failure with uncertain commit state becomes UNKNOWN;
- an explicit `not_committed` Tool rejection remains distinguishable from UNKNOWN;
- a fresh Host process only searches for and observes the original keyed Runtime Job;
- failure to find that Job never authorizes automatic redispatch;
- one `clientRequestId` resolving to conflicting Job identities fails closed;
- Runtime success alone does not complete the Task;
- exact content and digest verification must succeed before TaskOutcome;
- the dirty test Workspace is force-closed only after evidence is retained.

The workload deliberately allows only one root-level file created with `O_EXCL`. This is a falsifiable recovery slice, not a general mutation DSL or shell workflow engine.

## Version-bound source-change completion

Code-change completion uses a two-event verification boundary rather than treating a successful check process as final truth:

```text
Runtime Job succeeds
→ Host collects exact file and structured diff evidence
→ workspace.get sourceStateDigest is stable before and after collection
→ Host persists VERIFICATION_RECORDED while Task remains VERIFYING
→ workspace.close(expectedSourceStateDigest) compare-and-closes under Runtime lifecycle lock
→ Runtime tombstone retains the exact digest for response-loss replay
→ Host admits VERIFICATION_ACCEPTED
→ terminal TaskOutcome is recorded without another mutable Workspace read
```

The Runtime digest commits HEAD, Git index state, tracked and untracked source bytes, file modes, symlinks, and nested worktrees. Runtime owns the physical compare-and-close; Host owns whether the retained evidence is semantically sufficient. A mismatch preserves the Workspace and leaves the Task at the prepared verification frontier so evidence can be recollected. Extending a Task lease across Runtime calls is deliberately not used as a world-version substitute.

## Effect lifecycle ownership

Host does not expose a generic executor-neutral Effect lifecycle. The former candidate duplicated the specialized read, mutation, code-change, and Harness paths without an external consumer, so it was removed. Shared Host responsibility stops at durable Task state, explicit commitment and uncertainty, referenced evidence, and terminal admission; each workload owns the narrow lifecycle required to produce those facts.

## MCP transport lifecycle

The default Host `McpRuntimeClient` uses Runtime's canonical stateless MCP `2026-07-28` lifecycle:

```text
server/discover
→ verify supportedVersions and server identity
→ send protocol, clientInfo, and clientCapabilities metadata on every request
→ bind method identity with Mcp-Method and Tool identity with Mcp-Name
→ bounded request/response operations without a transport Session
```

Modern responses that attempt to create an MCP Session fail closed. Empty SSE heartbeat data frames are ignored, while multiple non-empty JSON-RPC messages remain outside the bounded Host profile. The retained `2025-06-18` `initialize` / `Mcp-Session-Id` path is available only through an explicit legacy compatibility profile for retained deployments and evidence. Neither modern request metadata nor legacy Session identity is persisted in Goal, Task, Context, Effect, Dispatch, verification, or recovery state. A fresh Host process rediscovers Runtime and continues from durable Host and Runtime identities.

## Empirical recovery boundary

Repeated live work exposed mechanisms not visible from unit tests alone:

1. Runtime Job reconstruction should use an exact durable `clientRequestId` filter when published by the Tool contract rather than scanning all history.
2. `systemctl is-active` is not a readiness proof; recovery waits for both service activity and successful modern MCP discovery.
3. Millisecond timestamps are ordering hints, not unique identities; live workload identities include an independent nonce.
4. Production MCP may emit an empty SSE heartbeat before the JSON-RPC response; ignoring the empty frame preserves strict single-response semantics.

These results prove the tested local systemd/SQLite/Workspace path only. They do not establish arbitrary host reboot recovery, remote Runtime recovery, database corruption recovery, distributed scheduling, or automatic redispatch safety for unkeyed Effects.

## Promotion rule

A Host-local concept moves toward shared Protocol only after at least two materially different workloads demonstrate the same non-bypassable invariant. Open ActionProposal, capability profiles, and participant DecisionRequest remain Host-local until that threshold is met.

A mechanism that adds ambiguity or cost without increasing accepted verified outcomes should be deleted rather than promoted.
