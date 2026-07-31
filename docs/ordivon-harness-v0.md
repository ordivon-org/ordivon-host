# Ordivon Harness v0

Status: OH0–OH5 native Run recovery and safe abandonment implemented and verified

## Purpose

Ordivon Harness is Ordivon's first-party Agent Harness backend for bare model APIs and local inference endpoints. It consumes one Host-owned `HarnessAssignment`, runs a bounded model–Tool–Observation loop, lowers Assignment-scoped Tool Calls through Ordivon Runtime, and returns Run evidence for Host adjudication.

```text
Host Task / Attempt / Assignment
                ↓
        Ordivon Harness Run
 Model Adapter / sequential Loop / ACI
                ↓
        Ordivon Runtime Job
                ↓
 Artifact / physical evidence
                ↓
 HarnessRunReceipt / CompletionProposal
                ↓
       Host verification / Outcome
```

## Ownership

### Host

- Goal, Task, Task Attempt, Assignment and generation;
- durable Context selection and acceptance criteria;
- capability and consequence admission;
- CompletionDecision and TaskOutcome.

### Ordivon Harness

- one Run-local model message state;
- model Turn invocation through an `AgentTurnAdapter`;
- sequential Tool Call interpretation;
- Assignment-scoped Tool projection;
- Run budgets, cancellation and stop classification;
- Run events, observations and receipt inputs.

### Runtime

- Workspace, Job, Runtime Attempt and process truth;
- physical execution, retained output and Artifacts;
- request admission, process ownership and reconciliation evidence.

## OH0 baseline

The construction baseline is `ordivon-host` revision `4ac03b52322cb76ade8f2902ab5b08113141c3e7`, including H1–H5. The pinned `ordivon-protocol` revision is `ca5af401eda77d1081487c2df07ce9d94003719e`.

The pre-construction deterministic baseline contained 199 passing Host tests. OH0–OH2 add 14 tests, producing a 213-test passing suite. Static compilation, Ruff, and Git whitespace validation also pass.

No Host Task, Runtime Job, Provider Session or completion object is redefined by Ordivon Harness.

## OH1 retained skeleton

- `ordivon_harness_manifest()` declares one stable conservative first-party identity;
- `AgentTurnRequest` and `AgentTurnResult` represent one model invocation, not one Task;
- Adapter model identity and Model Call identity are checked explicitly;
- `AgentToolCall` and `ToolObservation` retain separate identities;
- `OrdivonAgentLoop` is sequential and Run-local;
- `RunBudget` bounds model calls, Tool calls, observation bytes and wall time;
- `CancellationToken` stops before additional model or Tool work;
- `HarnessTrace` is an immutable digestible Run event sequence;
- `ScriptedTurnAdapter` is deterministic test infrastructure, not a model Provider.

## OH2 Runtime ACI

The first model-facing ACI exposes only operations available in the current production Runtime catalog:

```text
read_workspace
mutate_workspace
diff_workspace
run_in_workspace
observe_job
read_artifact
```

`workspace.patch` is deliberately not required by OH2 because the current production MCP catalog does not expose it. The Harness does not manufacture capabilities from a newer client surface or a research expectation.

Workspace creation and closure remain Host responsibilities. `task.list` is retained below the ACI only for identity-preserving reconciliation. `artifact.read` requires both the producing Runtime Job identity and Artifact identity.

The Runtime catalog digest binds both the selected Runtime Tool schemas and the model-facing ACI. A drifted catalog cannot execute an existing Assignment.

For `run_in_workspace`, one Tool Call derives one stable `clientRequestId` from:

```text
Assignment generation
+ Harness Run identity
+ stable step identity
```

Transport loss never causes automatic redispatch. The bridge searches for the original Job by request identity and observes that Job. No match remains `unknown`; multiple matches remain a conflict. A structured `not_committed` rejection is returned as `rejected`, not as a Dispatch or unknown delivery.

A `tool_call_dispatched` event is recorded only after lowering and pre-admission validation have succeeded and the result is not a proven `not_committed` rejection.

## OH2 live evidence

A sanitized live probe against the production Runtime retained:

```text
catalog digest  sha256:22ae1290ee472e9ffd6d9af67a2e5ced1f007e7d58f01f7c6a9ccfee5405a3f8
read path       Host-opened Workspace → read_workspace → observed README
exec path       run_in_workspace(/usr/bin/true) → one observed Runtime Job
cleanup         temporary Workspace closed in a finally path
```

A deliberately undersized `FULL` read was returned as a structured `not_committed` rejection rather than `unknown`; increasing the bound produced an observed reading. This exercises both the rejection and success paths without creating a second physical execution.

## OH3 shared Context profile

OH3 does not introduce another Context container. It reuses the existing shared types:

```text
ContextBlock
ContextManifest
CompiledContext
```

The three current compilation profiles are distinct producers of the same durable envelope:

```text
ContextCompiler         → ordivon.compiled-context
OpenContextCompiler     → ordivon.open-compiled-context
HarnessContextCompiler  → ordivon.harness-compiled-context
```

The closed profile retains an exact action menu for bounded deterministic decisions. The open profile requests one `ActionProposal` without `allowedActions`. The Harness profile freezes structured objective and acceptance objects, constraints, selected Context blocks, and unresolved Dispatch identities for a multi-turn model–Tool–Observation Run. It contains no prebuilt action menu.

The construction order is deliberately acyclic:

```text
TaskAttemptDescriptor + HarnessContextRequest
→ HarnessContextCompiler
→ CompiledContext
→ Host CAS object digest
→ HarnessAssignment
→ OrdivonInputCompiler
→ provider-neutral system/user messages
```

The Input Compiler verifies Task, Task Attempt, objective digest, acceptance digest, and the exact CAS object digest before presenting the Assignment to a model.

## OH3 DeepSeek Turn Adapter

The first live Adapter uses the official DeepSeek Chat Completions surface with the stable `deepseek-v4-flash` model identifier.

The retained v0 request profile is intentionally narrow:

```text
thinking      disabled
streaming     disabled
tool_choice   required
session       none
auto retry    none
```

Runtime Tools are translated to Provider function definitions. A Harness-private `submit_run_conclusion` function ends a Run without granting Task completion authority. The Adapter fails closed on malformed JSON, unavailable Tool names, mixed Runtime/conclusion calls, duplicate conclusions, inconsistent finish reasons, oversized responses, and Provider transport failures.

The API key is loaded only from a mode-`0600` non-symlink secret file, is excluded from dataclass representation, and is sent only in the Authorization header. It is not included in prompts, request bodies, traces, or evidence files.

## OH3 live evidence

The frozen read-only dogfood at `evidence/ordivon-harness-oh3-deepseek-live-20260801.json` exercised:

```text
Host-compiled structured Context
→ DeepSeek model call 1
→ read_workspace Tool Call
→ production Runtime Observation
→ DeepSeek model call 2
→ submit_run_conclusion
→ independent heading extraction and acceptance
```

The accepted run retained:

```text
source revision        79507fb6a000d241df19947de550610ebef6b8b1
Runtime catalog        sha256:22ae1290ee472e9ffd6d9af67a2e5ced1f007e7d58f01f7c6a9ccfee5405a3f8
model calls            2
Tool calls             1
Observation status     observed
model conclusion       candidate_completed
independent heading    # Ordivon Host
independent acceptance true
```

The model conclusion was not used as the acceptance oracle. The script reread the Runtime Observation, extracted the first Markdown heading, and compared it with the frozen expected value. The temporary Runtime Workspace was closed in a `finally` path.

After rebasing onto Host `main` revision `79507fb6a000d241df19947de550610ebef6b8b1`, the deterministic suite contains 226 passing tests, including nine focused OH3 tests.

## OH4 native Run Contract closure

OH4 closes the Host control-plane gap left by OH3. A native Run is no longer assembled in memory after the fact. Four Host-owned CAS objects are committed around the existing H1–H5 boundary:

```text
TaskContract
ToolGrant
NativeHarnessRunContract
CompletionVerification
```

The authoritative flow is now:

```text
TaskContract
→ TaskAttemptDescriptor
→ HarnessContextCompiler
→ CompiledContext CAS
→ HarnessAssignment + ToolGrant + native Harness Run identity
→ fresh Host reload
→ model–Tool–Observation loop
→ Trace / ToolObservations / conclusion / HarnessRunReceipt CAS
→ fresh Host reload
→ Host-derived CompletionProposal
→ persisted CompletionVerification
→ CompletionDecision / TaskOutcome
→ final fresh Host reload
```

One Assignment generation authorizes exactly one native `harnessRunId`. The Run identity exists before any Provider or Runtime activity and is included in Runtime foreign references together with the Task Contract, Tool Grant, Assignment, Task Attempt and native Run Contract digests. Replacing a Run requires a new Assignment generation.

### Task Contract and Context separation

`TaskContract` owns the durable objective, acceptance criteria, constraints, resource bindings and consequence policy. `CompiledContext` remains a bounded cognition snapshot derived from that Contract. Provider prompts receive a semantic projection rather than the complete CAS envelope.

This removes the previous ambiguity where Context simultaneously acted as both the authoritative Task definition and the model input representation.

### Assignment-scoped Tool Grant

The Runtime catalog still proves what the connected Runtime can execute. `ToolGrant` separately proves what the current Assignment exposes to the model. Native Assignments default to an explicit subset and resource scope.

The OH4 ACI adds:

```text
run_check(checkId)
```

A check binds an identity to an exact executable, arguments, working directory, environment and resource bounds before the model call. Generic `run_in_workspace` remains available only when `allowOpaqueExec` is explicitly granted. Read and mutation paths are checked before the Runtime boundary. `observe_job` and `read_artifact` can target only identities already observed in the current Run.

The historical H1–H5 direct-driver profile remains compatible and retains its original receipt boundary. Strict Trace-derived Job and Artifact provenance is mandatory only for native Ordivon Runs.

### Durable Run provenance

The Host now persists:

```text
HarnessTrace
each ToolObservation
AgentRunConclusion
HarnessRunReceipt schema v2
CompletionVerification
```

`HarnessRunReceipt` v2 permits a native Run with no Provider Session, retains an exact `terminationCode`, and keeps an optional continuation reference. The decoder continues to accept v1 receipts from existing Codex and Hermes lifecycle records.

Runtime Job and Artifact references in a native receipt must be exactly derivable from persisted Tool Observations. A model-provided evidence string does not become authoritative provenance. `propose_native_completion()` derives Host object evidence from the retained Trace and Observations.

The independent verifier result is stored as a complete `CompletionVerification` CAS object. `CompletionDecision.verificationDigest` binds that object rather than an unrecoverable transient JSON value.

### Capability precision

The first-party manifest no longer overclaims `interrupt=true`. It declares `ordivon.interrupt-between-turns.v0`, which matches the actual cancellation boundary: the Loop checks cancellation between model and Tool operations but does not promise in-flight HTTP interruption.

## OH4 live evidence

The frozen live evidence at `evidence/ordivon-harness-oh4-deepseek-live-20260801.json` exercised the production DeepSeek and Runtime path with three fresh-Host reopen boundaries. The legacy OH3 live launcher was removed because it manually assembled committed objects and no longer represented the authoritative architecture. The historical OH3 evidence remains retained.

```text
source revision              fb10eae6c178eb76dda51ab1baeb258da4682c38
model calls                  2
Tool calls                   1
termination code             candidate_completed
observed heading             # Ordivon Host
final Task revision          5
CompletionVerification       persisted and reloadable
final fresh-Host state       completed
independent acceptance       true
```

The live Provider usage was approximately 4,643 total tokens across two calls, compared with 6,434 in OH3. The reduced prompt projection lowered this simple workload by about 28% while adding stronger Host bindings.

The live proof closes durable Contract, Assignment, Run receipt, Trace, Observation, Verification and Outcome recovery for the tested read-only workload. It does not claim in-flight model-session continuation, recovery of an unrecorded provisional Runtime Job, arbitrary reboot recovery, or effectful external-action safety.

After OH4 implementation, the deterministic Host suite contains 231 passing tests, including four focused native Contract, ToolGrant and fresh-Host lifecycle tests.

## OH5 fault and abandonment semantics

OH5 closes the ambiguity between an absent Run receipt and a safely replaceable Run. A process can disappear after Assignment commit but before `record_run()` at any point in the model–Tool–Observation loop. The Host now retains two separate objects:

```text
NativeRunRecoveryAssessment
NativeRunAbandonment
```

`NativeRunRecoveryAssessment` records the exact Assignment and Run identity, recovery trigger, current Runtime catalog status, ToolGrant effect class, Workspace cleanup status and all unresolved UNKNOWN claims. It does not itself authorize replacement.

`NativeRunAbandonment` can be committed only from an assessment whose derived `safeToAbandon` value is true. The derived value is validated on decode and cannot be edited independently.

The automatic rule is deliberately narrow:

```text
read-only ToolGrant
+ Workspace closed / already absent / not applicable
+ no unresolved UNKNOWN
= safe abandonment
```

A Grant with `mutate_workspace` retains `workspace_mutation_possible`. A Grant with `run_check` or opaque `run_in_workspace` retains `process_effect_possible`. Closing the Workspace after process loss does not prove those prior effects did not occur. Such Tasks remain `BLOCKED`, expose `reconcile-current-harness-run-unknown`, and cannot receive a replacement Assignment generation.

Cleanup failure is retryable as evidence collection rather than execution retry. A first assessment may retain `workspaceStatus=unknown`; a later fresh Host may close the same Workspace, record assessment sequence 2 and only then abandon a read-only Run. Runtime catalog drift is preserved but does not manufacture effect uncertainty when the committed Grant is read-only and cleanup is proven.

A durable Run receipt changes the path. Exact Provider terminal codes are now retained:

```text
provider_failed
provider_timeout
provider_transport_failed
provider_rejected
provider_unavailable
```

These failures do not auto-retry and may be replaced with a new Assignment generation on the same Workspace when the retained Run is read-only or has no Tool Observation. Switching a recorded Run to another Workspace requires a future durable cleanup or release disposition; without one, the old Workspace would become an untracked resource. A recorded Run with observed mutation or process-capable Tools must be verified, completed, or continued through a future explicit continuation protocol before replacement. `runtime_unknown` remains non-replaceable because a Tool delivery or outcome is unresolved. Invalid model output, invalid Tool calls, budget exhaustion and cancellation remain distinct stop codes.

Before persisting a native Run result, the Host now rechecks the current Task revision, Assignment and recovery disposition. A late process result arriving after recovery or abandonment is rejected before Trace, ToolObservation, conclusion or receipt CAS writes.

### OH5 live recovery evidence

The frozen evidence at `evidence/ordivon-harness-oh5-recovery-live-20260801.json` exercises both the lost-process path and a successful replacement against production Runtime and DeepSeek:

```text
generation 1 Assignment     committed
generation 1 Runtime Tool   read_workspace observed
generation 1 receipt        deliberately absent
fresh Host cleanup          Workspace closed
RecoveryAssessment          persisted, safeToAbandon=true
RunAbandonment               persisted
late generation 1 result    rejected before CAS writes
generation 2 Assignment     committed
generation 2 model calls    2
generation 2 Tool calls     1
observed heading             # Ordivon Host
final Task revision          8
final Task state             completed
independent acceptance       true
```

The live source revision is `264d96cb2325e4a418c98459e05c74d482001fd8`. The first generation executed one real production Runtime read before its in-memory result was discarded. Recovery closed that Workspace and committed abandonment. The retained CAS object count did not change when the stale result was submitted later. The second generation then completed through the real DeepSeek Flash adapter and production Runtime.

The focused OH5 matrix covers Provider fault taxonomy, cancellation, budget exhaustion, invalid model output, safe read-only abandonment, effectful process-loss blocking, cleanup reassessment, catalog drift, recorded Runtime UNKNOWN, stale result rejection, object round trips and DeepSeek transport classification. The full deterministic Host suite contains 243 passing tests.

## Non-goals for v0

- persistent Provider Session;
- context compaction or reset policy;
- parallel Tools or subagents;
- model routing;
- Skills, Plugins or Hook platforms;
- Harness database, daemon or network service;
- Provider-independent Codex/Hermes/Claude lifecycle;
- direct Task completion by the Harness;
- automatic retry after uncertain delivery.

## Promotion and deletion

OH5 has passed the read-only native fault gate: the Host can distinguish a persisted terminal Run, a safely abandoned lost read-only Run and an unresolved effectful Run; reject stale late results; and replace only the cases whose uncertainty has been removed. This proves conservative read-only process-loss recovery, not general effect recovery or production maturity.

Promotion beyond OH5 requires a mutation workload whose Effect and Dispatch intent are durable before Runtime delivery, reconciliation of a lost process without relying on in-memory Tool history, explicit lifecycle for recorded Runtime UNKNOWN, and a cost comparison against one-shot and mature external Harness paths.

The prototype is deleted or narrowed if one-shot gateways or mature external Harnesses provide equal correctness, recovery and portability at lower permanent cost and no bare/local-model consumer remains.
