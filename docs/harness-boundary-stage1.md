# Harness Boundary Stage 1

Status: H1–H3 implemented; H4–H5 remain experimental work
Canonical experiment: `ordivon-computing/research/experiments/harness-boundary-v0/`

## H1 implementation result

H1 now provides strict, content-addressed `TaskAttemptDescriptor`, `HarnessAssignment`, `HarnessRunReceipt`, `CompletionProposal`, capability-manifest, and completion-decision codecs; four Task event kinds; durable Assignment-generation fencing; Host-owned completion adjudication; and version-2 operator handoff projection fields.

The implementation deliberately retains the existing schema-v3 journal. It adds no Assignment table, Run table, scheduler, Harness service, provider Session store, Runtime coupling, or `ordivon-harness` repository. A successful Harness process remains insufficient for Task completion: stale generation, missing evidence or Artifacts, unresolved Effects, and unresolved `UNKNOWN` state are rejected before the acceptance verifier can commit `TaskOutcome`.

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

## Objective

Prove or delete a Host-local Harness boundary by running one durable Task through Codex App Server and Hermes ACP, replacing the Harness mid-Task, and validating completion against Host and Runtime evidence.

The implementation stays inside `ordivon-host`. It introduces no `ordivon-harness` repository, provider-independent Session format, scheduler, global Hook system, or second Task database.

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
  adapter.py         candidate common lifecycle only after H4/H5 evidence
  host.py            Assignment, Run, proposal, and completion transitions
  runtime_refs.py    Runtime foreign-reference builder
  codex_app.py       provider-faithful Codex App Server direct driver
  hermes_acp.py      planned provider-faithful Hermes ACP direct driver
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

Use the installed Hermes ACP adapter, which currently advertises load, resume, fork, prompt, cancel, Tool-call updates, usage, and MCP support.

Minimal mapping:

```text
initialize
session/new
session/prompt
session/update
session/cancel
session/load or session/resume as observed optional capabilities
```

The driver records Session ID, ACP/version capability response, model/provider, MCP catalog, Tool-call updates, usage, and stop reason.

Hermes history and checkpoints remain provider-local evidence. Replacement uses fresh Host Context rather than importing those internals into the common contract.

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

Implement direct driver and adapter with protocol fixtures, then one live smoke run.

### H5 — replacement and faults

Run both replacement orders and the three faults, preserving receipts and equal-budget measurements.

## Deletion decisions

- remove Task Attempt if Task + Assignment fully express every tested path;
- remove Assignment generation if Task revision alone rejects every stale worker;
- merge Harness Run into existing invocation receipts if it contributes no recovery or diagnosis;
- reuse existing TaskOutcome directly if CompletionProposal prevents no failure;
- keep adapters provider-local if the shared boundary loses capability or saves negligible code;
- do not propose `ordivon-harness` until the repository promotion gate in Computing #83 is independently satisfied.
