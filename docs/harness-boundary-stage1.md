# Harness Boundary Stage 1

Status: implementation design for `ordivon-host#14`  
Canonical experiment: `ordivon-computing/research/experiments/harness-boundary-v0/`

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
  adapter.py         Host-local Protocol and event normalization
  host.py            Assignment, Run, proposal, and completion transitions
  runtime_refs.py    Runtime foreign-reference builder
  codex_app.py       Codex App Server direct driver / adapter
  hermes_acp.py      Hermes ACP direct driver / adapter
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

Implement direct driver and adapter with fake-server tests, then one live smoke run.

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
