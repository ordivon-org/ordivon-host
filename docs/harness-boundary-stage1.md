# Harness Boundary Stage 1

Status: H1–H5 implemented; Stage 1 closed
Canonical experiment: `ordivon-computing/research/experiments/harness-boundary-v0/`

## H1 implementation result

H1 now provides strict, content-addressed `TaskAttemptDescriptor`, `HarnessAssignment`, `HarnessRunReceipt`, `CompletionProposal`, capability-manifest, and completion-decision codecs; four Task event kinds; durable Assignment-generation fencing; Host-owned completion adjudication; and version-2 operator handoff projection fields.

The implementation deliberately retains the existing schema-v3 journal. It adds no Assignment table, Run table, scheduler, cross-Provider Harness service, provider Session store, Runtime coupling, or repository extracted from the direct Provider drivers. A successful Harness process remains insufficient for Task completion: stale generation, missing evidence or Artifacts, unresolved Effects, and unresolved `UNKNOWN` state are rejected before the acceptance verifier can commit `TaskOutcome`.

Eight deterministic H1 tests cover strict round trips, capability admission, idempotent replay, fresh Context on replacement, stale CompletionProposal rejection, missing Artifact rejection, unresolved `UNKNOWN`, exactly-once accepted completion, handoff projection, and fresh-process recovery. All H1 objects remain deletion-tested candidates until the live H3–H5 trials establish retained value.

## H2 implementation result

H2 adds one Host-local `runtime_refs.py` module. It produces four sorted opaque `ordivon.host` references for Task, Task Attempt, Assignment, and Harness Run; derives an Assignment- and run-bound `clientRequestId`; and builds the existing Runtime `workspace.exec` request without adding a Dispatch object, service, database table, or Runtime-specific Task state.

The Harness Run reference uses a stable pre-completion binding digest derived from Assignment identity, generation, Harness identity, manifest, Context, Tool catalog, and Harness Run ID. Runtime work can therefore be correlated before the final `HarnessRunReceipt` exists. The final receipt later records the actual Runtime Job and terminal-evidence Artifact.

The live receipt [`../evidence/harness-h2-runtime-r2-live-d50f609-20260731.json`](../evidence/harness-h2-runtime-r2-live-d50f609-20260731.json) proves one real Host request against the active Runtime: exact replay returned the original Job, changed Assignment generation and digest were rejected as idempotency conflicts, terminal evidence retained the exact four Host references, a fresh client recovered the original Job, Runtime made no semantic-completion claim, Host recorded the Runtime Job in `HarnessRunReceipt`, and the Workspace was closed. Three deterministic H2 tests cover canonical ordering, digest and request identity, replacement generation, and invalid request rejection.

## H3 implementation result

H3 adds a provider-faithful Codex App Server v2 stdio driver. It performs `initialize`, `thread/start`, `turn/start`, `turn/interrupt`, bounded line-delimited message ingestion, Thread/Turn identity retention, Tool lifecycle observation, token-usage capture, and raw provider-message canonical hashing. It rejects unexpected server-initiated approval or Tool requests rather than silently granting authority. Codex Thread resume and fork remain advertised provider capabilities; they do not become Host Task continuity because the generated 0.145 protocol explicitly describes resumed Turn history as lossy.

Four deterministic protocol tests use a fake App Server to verify lifecycle ordering, command Tool normalization, usage, structured result round-trip, interrupt behavior, `HarnessRunReceipt` construction, and fail-closed server requests. The driver remains provider-specific. H3 does not add a shared `HarnessAdapter` implementation, provider-independent Session object, event bus, scheduler, Hook framework, or Runtime change.

The live receipt [`../evidence/codex-app-h3-live-64ab44b-20260731.json`](../evidence/codex-app-h3-live-64ab44b-20260731.json) records one Runtime-managed read-only Codex App Server Harness Run from implementation revision `64ab44be667fa172027150a152b2f4660538ef00`. Runtime owned the `uv → worker → codex app-server → bash cat` process tree and retained one Job, Attempt, stdout, stderr, execution result, and terminal-evidence Artifact. Codex 0.145 created a real Thread and Turn with model `gpt-5.6-sol`, executed one successful command reading `runtime_refs.py`, emitted Tool and token-usage events, and completed without file changes. Host admitted the resulting `HarnessRunReceipt`, preserved Runtime and Provider evidence, projected the Run through operator handoff, and left the Task in `waiting` with no `TaskOutcome`.

The existing one-shot `CodexCliModelGateway` remains a distinct baseline: it uses one ephemeral subprocess and retains no Thread identity, interrupt surface, Tool lifecycle, or raw provider-event digest. App Server provides those capabilities at higher protocol, event-volume, and token cost. H3 therefore provisionally retains the direct Codex driver but does not yet justify a shared cross-provider adapter; H4 and H5 must determine whether useful lifecycle code actually repeats across Hermes ACP and replacement trajectories.

## H4 implementation result

H4 adds a provider-faithful Hermes ACP v1 JSON-RPC stdio driver without depending on the ACP Python SDK. It performs `initialize`, `session/new`, `session/prompt`, and `session/cancel`; handles interleaved responses and `session/update` notifications; retains Session identity and Hermes provenance; observes Tool, usage, message, and thought events; and rejects all server-initiated permission, filesystem, terminal, or Tool requests by default. Raw Tool input, output, and file content are represented only by canonical digests. `agent_thought_chunk` text never enters a serialized result, Host object, receipt, or handoff; only event counts and payload digests remain.

Five deterministic protocol tests cover capability projection, full and start-only Tool streams, usage, thought-text exclusion, result round-trip, `HarnessRunReceipt` construction, cancellation, and fail-closed server requests. The start-only fixture was added after the first live Runtime trajectory showed that Hermes 0.18.0 may terminate a successful Prompt after a `tool_call` without emitting the later `tool_call_update`. H4 therefore treats Tool observation plus terminal Prompt response as the reliable Provider contract and preserves richer completion updates when they arrive without inventing them when they do not.

The live receipt [`../evidence/hermes-acp-h4-live-3d9a559-20260731.json`](../evidence/hermes-acp-h4-live-3d9a559-20260731.json) records one Runtime-managed read-only Hermes ACP Harness Run from implementation revision `3d9a55904c735b388d8acf617262f0322174ba9a`. Runtime owned the `uv → worker → hermes acp → read_file` process tree and retained one Job, Attempt, stdout, stderr, execution-result, and terminal-evidence Artifact. Hermes Agent 0.18.0 created Session `eb31426a-d8a8-4814-bf01-38c02f48c8e4`, used `deepseek:deepseek-v4-pro`, observed one read Tool for `runtime_refs.py`, emitted 689 Provider messages, 449 thought chunks, 234 message chunks, two usage updates, and completed with `end_turn`. Host admitted the resulting `HarnessRunReceipt`, preserved Runtime and Provider evidence, projected the Run through operator handoff, and left the Task in `waiting` with no `TaskOutcome`.

The existing one-shot `HermesCliModelGateway` remains the simpler baseline. ACP adds durable Provider Session identity, provenance, cancellation, Tool observations, usage, raw-event hashing, and multi-Prompt process capability, but the bounded inspection still consumed 35,992 total tokens and produced a large event stream. The direct Hermes driver is retained provisionally through H5.

A cross-provider audit found only limited useful repetition between the 841-line Codex driver and 947-line Hermes driver: exact-line Jaccard similarity was approximately 0.275, and most shared lines were subprocess, queue, validation, serialization, and receipt mechanics. Their lifecycle semantics differ materially: Codex uses Thread/Turn/Item notifications and a distinct terminal Turn event; Hermes uses standard JSON-RPC Session/Prompt responses, bidirectional requests, thought streams, and optional Tool completion updates. Extracting a shared adapter now would delete little provider code while obscuring real differences, so no `adapter.py`, common Session format, or shared event runtime is created.

## H5 implementation result

H5 ran the frozen `harness-replacement-repository-repair-v1` workload in both live replacement orders: Codex diagnosis → Hermes repair and Hermes diagnosis → Codex repair. Each trajectory retained one Task Attempt, advanced Assignment generation from 1 to 2, compiled a fresh Context, passed the diagnosis Artifact through Host CAS, started a new Provider Session, ran an independent Runtime acceptance Job, and committed TaskOutcome only after Host adjudication.

The live receipt [`../evidence/harness-replacement-h5-live-76420e4-20260731.json`](../evidence/harness-replacement-h5-live-76420e4-20260731.json) records four Provider Runs, six trajectory Runtime Jobs, one missing-Artifact Runtime probe, exact Host references in terminal evidence, two distinct accepted source implementations, and closed Workspaces. Both old generation-1 completion claims were rejected as `stale_assignment`. A physically successful Runtime process without `completion.json` was rejected as `missing_artifact`. A deliberately dropped repair response was recovered by a fresh Host through one Assignment-bound request identity and one existing Runtime Job, with no redispatch.

H5 also established that Provider final text is not a portable completion contract. Both Codex Runs returned usable structured responses. Both Hermes Runs produced valid Artifacts and passed independent verification while ACP final assistant text was absent. The canonical result therefore comes from verified Artifacts when Provider text is absent or unusable.

The final architecture disposition is recorded in [`harness-boundary-h5-decision.md`](harness-boundary-h5-decision.md): retain the immutable Task Attempt role, Assignment generation, HarnessRunReceipt, CompletionProposal/Decision, Host Runtime references, and provider-specific direct drivers; localize Provider modes and approvals; reject the shared adapter, common Session lifecycle, event runtime, Runtime Task state, new SQL tables, and a repository extracted from the mature Provider drivers. The separate first-party Ordivon Harness question for bare model APIs is tracked by `ordivon-computing#90`.

## Objective

Prove or delete a Host-local Harness boundary by running one durable Task through Codex App Server and Hermes ACP, replacing the Harness mid-Task, and validating completion against Host and Runtime evidence.

The Stage 1 Provider-boundary implementation stays inside `ordivon-host`. It introduces no repository extracted from the direct Provider drivers, provider-independent Session format, scheduler, global Hook system, or second Task database. A first-party Ordivon Harness for bare model APIs is a separate post-Stage-1 construction question.

## Current code to reuse

| Existing Host surface | Stage 1 use |
|---|---|
| `HostStorage` and content-addressed objects | store Task Attempt, Assignment, Run, and CompletionProposal objects |
| Task event stream and `TaskProjection` | authoritative current revision, state, and completion decision |
| `HostKernel.locked_task` | serialize short Host transitions |
| `ContextCompiler` | compile fresh Context for every Assignment |
| `ModelInvocationIntent` and receipts | direct one-shot baseline and identity precedent |
| `OperatorHandoffCapsule` | recovery projection and later operator inspection |
| `TaskOutcome` and verification receipts | final accepted semantic outcome |
| `RuntimeEffectExecutor` and job lookup | response-loss reconciliation and Runtime evidence |

The existing Task lease remains a transition lock. Its revision is not the durable worker fence because releasing the lease deletes the row. Assignment generation owns fencing across Harness processes.

## Package layout

```text
src/ordivon_host/harness/
  __init__.py
  models.py          immutable v0 objects and capability manifest
  host.py            Assignment, Run, proposal, and completion transitions
  runtime_refs.py    Runtime foreign-reference builder
  codex_app.py       provider-faithful Codex App Server direct driver
  hermes_acp.py      provider-faithful Hermes ACP direct driver
  adapter.py         not created; H5 rejected a shared lifecycle implementation
```

The direct drivers expose provider protocols faithfully. The adapter layer maps only the lifecycle required by the experiment.

## Experimental objects

### `TaskAttemptDescriptor`

Immutable semantic path descriptor. It contains no lifecycle state machine.

### `HarnessAssignment`

Immutable Host commitment with:

- Task ID and exact Task revision;
- Task Attempt ID;
- monotonically increasing generation;
- target Harness and manifest digest;
- Context object, acceptance criteria, Tool catalog, source, prior Artifact, budget, and capability bindings.

### `HarnessRunReceipt`

One concrete provider Session/process receipt. Provider Session identity remains local evidence.

### `CompletionProposal`

Harness claim about acceptance completion. Host validates it before producing `TaskOutcome`.

All objects use strict field equality, canonical digest validation, and round-trip tests matching existing Host model conventions.

## Task events

Add four event kinds:

```text
HARNESS_ASSIGNMENT_COMMITTED
HARNESS_RUN_RECORDED
COMPLETION_PROPOSED
COMPLETION_DECIDED
```

Event payloads carry identity and object digests. Objects contain details. No `assignments`, `runs`, or `completion_proposals` SQL tables are added.

## `HarnessHost` transitions

```text
start_attempt(task)
→ create TaskAttemptDescriptor

assign(task, attempt, adapter)
→ compile current Context
→ read adapter manifest
→ generation = previous + 1
→ store HarnessAssignment
→ commit HARNESS_ASSIGNMENT_COMMITTED

record_run(task, assignment, result)
→ verify exact assignment identity
→ store HarnessRunReceipt
→ commit HARNESS_RUN_RECORDED

propose_completion(task, proposal)
→ store proposal even when stale
→ commit COMPLETION_PROPOSED

adjudicate_completion(task, proposal)
→ verify Task revision and Assignment generation
→ verify evidence and required Artifact existence
→ reject unresolved Effect/UNKNOWN state
→ run acceptance verifier
→ commit COMPLETION_DECIDED
→ record TaskOutcome only on acceptance
```

### Fencing rule

A proposal is current only when all match:

```text
taskId
taskRevision
taskAttemptId
assignmentId
assignmentGeneration
harnessRunId
```

A stale proposal becomes retained evidence with a structured rejection reason. It cannot advance Task state or trigger another Effect.

## Adapter surface

```python
class HarnessAdapter(Protocol):
    harness_id: str

    def manifest(self) -> HarnessCapabilityManifest: ...
    def start_assignment(
        self,
        assignment: HarnessAssignment,
        event_sink: HarnessEventSink,
    ) -> HarnessRunHandle: ...
    def wait(self, run: HarnessRunHandle) -> HarnessRunResult: ...
    def interrupt(self, run: HarnessRunHandle, reason: str) -> None: ...
    def close(self, run: HarnessRunHandle) -> None: ...
```

`HarnessEvent` v0 normalizes only:

```text
run_started
message_delta
tool_started
tool_finished
usage_observed
run_stopped
```

Raw provider event bytes or canonical JSON digest remain attached for diagnosis. Provider-specific events are not discarded merely because v0 does not normalize them.

## Capability manifest

The manifest reports support without forcing one common implementation:

```text
protocol and revision
persistent session
session resume
session fork
interrupt
tool events
approval events
usage
images
compaction
checkpoint
local subagents
provider-specific extensions
```

Stage 1 requires only persistent Session, interrupt, final result, and observable Tool events. Unsupported capabilities remain explicit.

## Codex App Server driver

Use generated local protocol schemas from Codex CLI 0.145.

Minimal mapping:

```text
initialize
thread/start
turn/start
notifications for thread / turn / item lifecycle
turn/interrupt
thread/resume as an observed optional capability
```

The driver records thread ID, turn ID, App Server version, model, sandbox/approval configuration, Tool events, usage, and final stop reason.

It does not export hidden reasoning or make a Codex thread authoritative for Task continuation.

## Hermes ACP driver

The installed Hermes Agent 0.18.0 implements ACP v1 over standard JSON-RPC stdio. The direct driver uses the following minimal mapping:

```text
initialize
session/new
session/prompt
session/update
session/cancel
```

Load, resume, fork, image, approval, filesystem, terminal, and MCP capabilities are retained in the Provider manifest when advertised. H4 does not invoke those broader methods or expose client-side filesystem and terminal capabilities. Every server-initiated request fails closed.

The driver records Session ID, protocol and agent revision, model/provider, Session provenance, Tool observations, usage, terminal stop reason, and raw Provider-message digest. Tool completion updates are retained when sent but are not assumed to be mandatory. Thought text and raw Tool content are excluded from serialized evidence.

Hermes persisted history, load, resume, and fork remain Provider-local capabilities. Replacement uses a fresh Host Assignment and Context rather than importing Hermes history into the common contract.

## Direct baselines

Retain existing:

- `CodexCliModelGateway`;
- `HermesCliModelGateway`.

Also keep provider-specific direct drivers callable without `HarnessHost`. The shared adapter is compared with direct drivers on correctness, capability loss, code size, and maintenance.

## Frozen workload

Use `harness-replacement-repository-repair-v1` from the Computing experiment.

Run:

1. Codex → deliberate termination after `diagnosis.json` → Hermes completion;
2. Hermes → deliberate termination after `diagnosis.json` → Codex completion;
3. direct single-Harness baselines;
4. one-shot continuation baselines.

Every path receives the same repository revision, Goal, acceptance criteria, Tool access, and budget.

## First fault slice

Only these faults enter the first implementation:

1. stale generation CompletionProposal;
2. successful process exit with absent or mismatched required Artifact;
3. lost Runtime response after possible execution commitment.

Hook/Event policy, compaction, broader abstention states, source drift, and adversarial injection follow only after this slice retains a mechanism.

## Runtime references

Every Runtime Job created by the Harness path includes existing Runtime `foreignReferences` for:

```text
ordivon.host / task
ordivon.host / task_attempt
ordivon.host / assignment
ordivon.host / harness_run
```

Effect and Dispatch references are added when applicable. A helper constructs and sorts these references; product code does not hand-build dictionaries.

## Tests before live providers

### Model tests

- strict field set and canonical round trip;
- generation must be positive and monotonic;
- referenced digests and identities validated;
- duplicate Artifact/evidence refs rejected.

### Transition tests

- Assignment creation at current Task revision;
- fresh Context per generation;
- stale proposal stored then rejected;
- missing Artifact rejects completion;
- unresolved `UNKNOWN` rejects completion without terminating the Task;
- accepted proposal records exactly one TaskOutcome;
- replay of the same transition is idempotent or conflicts deterministically.

### Adapter contract tests

- provider capability manifest preserved;
- normalized lifecycle order;
- interrupt reaches the provider driver;
- raw unnormalized events remain receipted;
- provider Session state never appears in Task identity.

### Cross-layer test

- Runtime terminal evidence contains exact Host foreign references;
- same `clientRequestId` and references reattach to one Job;
- changed Assignment generation conflicts under the old request identity.

## Implementation batches

### H1 — contracts and transitions

Add models, four event kinds, `HarnessHost`, deterministic tests, and handoff projection fields.

### H2 — Runtime correlation

Add `runtime_refs.py`, consume the Runtime Stage 2 convention, and pass the cross-layer vector.

### H3 — Codex App Server

Completed with a provider-faithful direct driver, fake-server lifecycle tests, interrupt and fail-closed server-request coverage, and one Runtime-managed live read-only Harness Run. Shared adapter extraction remains deferred.

### H4 — Hermes ACP

Completed with a provider-faithful JSON-RPC direct driver, full and sparse Tool-stream fixtures, cancellation and fail-closed server-request coverage, thought-digest-only evidence, and one Runtime-managed live read-only Harness Run. Shared adapter extraction remains deferred.

### H5 — replacement and faults

Completed both replacement orders with one stable Task Attempt, fresh Assignment generation and Context, explicit diagnosis and completion Artifacts, independent Runtime acceptance, and Host-owned TaskOutcome. The live fault slice rejected stale generation and missing Artifact completion, and recovered one response-lost Runtime Job without redispatch.

## Final deletion decisions

- retain `TaskAttemptDescriptor` as one immutable semantic-attempt identity; add no attempt lifecycle or table;
- retain Assignment generation as the durable stale-worker fence;
- retain `HarnessRunReceipt` as the Provider Session, Runtime Job, Artifact, Context, Tool, usage, and stop-evidence link;
- retain `CompletionProposal` and `CompletionDecision`; F1 and F2 directly demonstrate failures they prevent;
- retain Host Runtime foreign references and request identity; F3 directly demonstrates recovery value;
- keep Codex and Hermes direct drivers provider-local and preserve one-shot baselines;
- treat Provider final text as optional observation and verified Artifacts as authoritative;
- do not create a shared `HarnessAdapter`, common mature-Provider Session lifecycle, event runtime, Runtime Task state, additional SQL tables, or repository extracted from the Codex/Hermes drivers; treat first-party Ordivon Harness construction as a separate bare-model problem.

See [`harness-boundary-h5-decision.md`](harness-boundary-h5-decision.md) for the evidence and full retain/localize/shrink/delete rationale.
