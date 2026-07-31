# Ordivon Harness v0

Status: OH0–OH3 first live bare-model loop implemented and verified

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

OH3 has passed the first bare-model gate: two real model calls, one production Runtime Tool action, one Observation returned to the second model call, and an independently accepted result. This proves the controlled loop exists; it does not yet prove broad workload value or production maturity.

Promotion beyond OH3 requires a materially different workload, durable Trace/Receipt storage through the Host boundary, fault injection against Provider and Runtime uncertainty, and a cost comparison against one-shot and mature external Harness paths.

The prototype is deleted or narrowed if one-shot gateways or mature external Harnesses provide equal correctness, recovery and portability at lower permanent cost and no bare/local-model consumer remains.
