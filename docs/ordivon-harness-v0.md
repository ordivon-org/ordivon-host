# Ordivon Harness v0

Status: OH0–OH2 skeleton implemented and locally verified

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

The skeleton advances to a real bare-model Adapter only if OH0–OH2 remain smaller than the duplicated semantics they prevent. The first live Run must include at least two model calls and one Runtime Tool action, bind all identities to a receipt, and leave semantic completion to Host verification.

The prototype is deleted if one-shot gateways or mature external Harnesses provide equal correctness, recovery and portability at lower permanent cost and no bare/local-model consumer exists.
