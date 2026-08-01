# Ordivon Harness E1–E2 design audit

Status: implemented and verified

Implementation commit: `ace186c712a1678b811a9ec6975f0618f78673dd`

Base Host revision: `fa313039cf2f7c9f8df445a8ccbfed8d9e06f3aa`

Pinned Protocol revision: `ca5af401eda77d1081487c2df07ce9d94003719e`

Parent closeout: [`ORDIVON_HARNESS_OH1_OH5_CLOSEOUT.md`](ORDIVON_HARNESS_OH1_OH5_CLOSEOUT.md)

Tracking issue: `#30`

## Implementation result

E1–E2 were implemented without beginning E3. The resulting production boundary is:

```text
production Runtime descriptors
        + Provider-visible native Tool definitions
        + explicit ToolContract semantics
        + Harness recovery consequence
                         ↓
          NativeToolCatalogSnapshot
                         ↓
      Host CAS + native Assignment generation
                         ↓
 durable facts → pure NativeRunDisposition → Host admission / handoff / audit
```

The implementation added:

```text
src/ordivon_host/harness/tool_semantics.py
src/ordivon_host/harness/disposition.py
tests/test_ordivon_harness_e1_e2.py
```

and integrated them into native Assignment commit/reload, Runtime bridge admission, Recovery, recorded Run projection, Completion admission, operator handoff, and full-history validation.

Verified results:

- every current model-facing Tool has one explicit `ToolContract`, Runtime lowering set, and recovery consequence;
- new native Assignments persist and bind the exact catalog snapshot in Host CAS;
- `NativeHarnessRunContract` v2 binds the catalog object while v1 historical objects remain decodable through a closed seven-Tool compatibility path;
- unknown or incomplete Tool semantics fail closed;
- `native_tool_grant_effect_class()` has been removed from the live path;
- one pure `NativeRunDisposition` drives native replacement, Recovery, Completion admission, handoff, and audit projections;
- external Codex and Hermes Harness receipts retain their existing lifecycle and are not forced into native termination semantics;
- candidate completion carrying UNKNOWN Tool evidence is rejected before persistence;
- the metadata-declared Runtime lowering operation is tested against the actual bridge lowering for all seven Tools;
- exact pinned-Protocol deterministic suite: `255 / 255`;
- production Runtime catalog discovery passed as a read-only inspection without creating a Workspace or Job.

The verified production Runtime snapshot at implementation time was:

```text
catalog object digest:
sha256:0b2dcdb143ac1b961b9b7695f089ef7df84ac3b8e09a23e61bc2b984a7d4dee4

semantic digest:
sha256:41e5b6ca1298b68012a5ae13367a977c6142923800608b225decac14dc4a74b1
```

### Actual deviations from the design draft

The implementation changed three details after code-backed validation:

1. `HarnessAssignment.toolCatalogDigest` binds the canonical digest of the **complete snapshot object**, not only its inner semantic projection. `NativeToolCatalogSnapshot.semanticDigest` remains available as a separate revision identity. This matches the existing Host `_load_object()` invariant and makes CAS reload and historical audit exact.
2. Replacement generations may inherit the previous retained catalog snapshot when the caller omits it; the initial native Assignment must always provide the complete snapshot. This removes repeated caller plumbing without allowing semantic drift.
3. The implementation is larger than the initial estimate because the pinned `ToolContract` package has no decoder and because historical correctness required exact v1/v2 decoding plus event-level Doctor validation. The added structure remains local to Harness semantics and disposition; no service, database, daemon, Provider feature, or workflow language was introduced.

No live model call was required: E1–E2 change Host semantics and auditability rather than model behavior. Existing frozen OH3–OH5 evidence remains the model/Runtime lifecycle evidence.

## Decision

E1 and E2 are justified, but the original shorthand design was incomplete.

The implementation should not add consequence fields directly to `AgentToolDefinition`, should not persist only one aggregate effect flag in `ToolGrant`, and should not create a durable `RunDisposition` object.

The accepted design is:

```text
Provider-visible AgentToolDefinition
                  │
                  │ projection only
                  ▼
Host-owned NativeToolCatalogSnapshot ── persisted and Assignment-bound
                  │
                  ├─ standard anc_tool_contract.ToolContract
                  ├─ exact Runtime lowering operations
                  └─ Harness recovery consequence

Durable Assignment / Run / Recovery facts
                  │
                  ▼
pure NativeRunDisposition derivation ── never a second durable truth
```

E1 establishes the durable semantic catalog. E2 centralizes decisions that consume it. Neither stage implements durable Tool-step continuation.

## Findings from the pre-implementation code

### Provider schema and Host semantics are currently conflated in one catalog digest

`AgentToolDefinition` contains only:

```text
name
description
inputSchema
```

It is sent directly to the model Provider. DeepSeek serializes only those three values into function-calling payloads.

`discover_harness_runtime_catalog()` currently computes one digest from:

```text
selected Runtime operation schemas
+ model-facing Tool definitions
```

The digest is bound to `HarnessAssignment` and `NativeHarnessRunContract`, but the catalog itself is not retained as a Host CAS object.

This is sufficient for live drift detection. It is insufficient for historical consequence interpretation: a future Host binary could change the meaning of a Tool while an old Assignment retains only the previous digest.

### ToolGrant expresses authority, not Tool semantics

`ToolGrant` currently stores:

```text
allowedTools
readPathRules
mutatePathRules
executionChecks
allowOpaqueExec
```

This correctly answers what one Assignment permits. It does not answer what a granted Tool means, how it completes, how delivery is reconciled, or what uncertainty remains after process loss.

Adding one aggregate consequence flag to `ToolGrant` would mix authority with catalog semantics and would not prove where that aggregate came from.

### Recovery previously classified by Tool name

Before E1, `native_tool_grant_effect_class()` contained the complete policy:

```text
run_check / run_in_workspace → process effect possible
mutate_workspace             → Workspace mutation possible
all other names              → read-only
```

This is correct for the seven current Tools but unsafe as an extension rule. A new effectful Tool omitted from the switch would silently become read-only.

### Disposition logic is duplicated across five paths

The same underlying decision is currently expressed in:

1. `HarnessHost.assign()` replacement admission;
2. `HarnessHost.record_native_run_recovery()` UNKNOWN and Task-state derivation;
3. `HarnessHost.record_run()` replacement projection;
4. `HarnessHost._run_from_snapshot()` projection revalidation;
5. `operator_handoff()` next-action selection;
6. historical validation of Recovery and Abandonment projections.

OH5 required synchronized fixes across several of these paths. Effectful continuation would multiply the risk.

### Recorded Run classification cannot yet safely use actual attempted Tools

The Trace records `tool_call_proposed`, `tool_call_dispatched`, and terminal Tool events with Tool names. However, Host `record_run()` currently validates Trace identity and digest but does not decode the complete Trace or prove a one-to-one relationship between proposed Tool Calls and retained Tool Observations.

Therefore E2 must initially preserve the current conservative rule for a recorded Run:

```text
if the Grant is effectful and the Run retained any Tool Observation,
replacement remains blocked
```

It must not reduce conservatism by inspecting attempted Tool names until a separate Trace/Observation consistency gate exists. E3 durable Tool-step work is the natural place to establish that stronger provenance.

## Existing semantic model to reuse

The repository already depends on `anc_tool_contract.ToolContract` through the pinned Ordivon Protocol package.

It defines:

```text
operation
semanticAction
inputSchema / outputSchema
execution
completion
effectClass
idempotencySupport
correlation
cancellation
evidence
capabilityClass
```

Existing Runtime read, execution, and code-change paths already use this Contract. E1 should reuse it rather than create parallel enums for execution, completion, idempotency, correlation, or evidence.

`ToolContract.EffectClass` contains:

```text
observe
change
opaque
```

That is intentionally cross-system and is not precise enough for Harness process-loss recovery. E1 therefore adds one narrow Harness-local dimension rather than replacing the standard Contract.

## E1 data model

### `NativeToolRecoveryConsequence`

Proposed internal enum:

```text
observation-only
workspace-change-possible
process-or-external-effect-possible
unknown
```

Rules:

- `unknown` is the fail-closed default for missing or invalid metadata;
- aggregation takes the most consequential value;
- an unrecorded Run is classified from all granted Tools because any granted Tool may have executed before process loss;
- a recorded Run remains conservatively classified from the Grant until attempted-Tool provenance is strengthened.

This enum describes only the uncertainty retained when the Harness process disappears. It does not replace proposal consequence class, reversibility, authority policy, Effect IR, or ToolContract effect class.

### `NativeToolSpec`

Proposed shared Harness model:

```text
NativeToolSpec
├── description
├── contract: ToolContract
├── runtimeOperations
└── recoveryConsequence
```

The Provider definition is a projection:

```text
AgentToolDefinition(
    name=contract.operation,
    description=description,
    input_schema=contract.input_schema,
)
```

Invariants:

1. Tool names are unique.
2. Every ToolContract input schema exactly matches the Provider-visible input schema after canonical schema normalization.
3. Every referenced Runtime operation exists in the retained Runtime projection.
4. Every Tool has an explicit recovery consequence; missing metadata is invalid.
5. `opaque` ToolContracts must not carry `observation-only` recovery consequence.
6. asynchronous ToolContracts must define non-`none` correlation.
7. keyed idempotency must use stable-key correlation.

### `NativeToolCatalogSnapshot`

The current `HarnessRuntimeCatalog` should evolve into, or contain, a durable snapshot:

```text
NativeToolCatalogSnapshot
├── revision
├── semanticDigest
├── selected Runtime descriptors
└── NativeToolSpec[]
```

The semantic digest must include:

```text
Runtime input/output/execution descriptors
Provider-visible Tool schemas and descriptions
ToolContract semantic fields
Runtime lowering operation identities
recovery consequence
```

The snapshot is stored in Host CAS as:

```text
kind = harness-runtime-catalog
```

The Assignment `toolCatalogDigest` binds the canonical digest of the complete snapshot object. The snapshot separately retains `semanticDigest` and revision identity, and a native Run Contract additionally binds the exact catalog object digest.

### Persistence changes

For new native Assignments:

```text
HarnessAssignment.toolCatalogDigest
NativeHarnessRunContract.toolCatalogDigest
NativeHarnessRunContract.toolCatalogObjectDigest
Assignment event.toolCatalogObjectDigest
```

`CommittedHarnessAssignment` retains the decoded snapshot and StoredObject.

The Runtime bridge rediscovers the live Runtime catalog and compares the canonical digest of the complete snapshot against the committed Assignment before exposing Tools.

Recovery uses the retained snapshot, not the current binary's static Tool-name switch, even when Runtime is unavailable.

### Backward compatibility

Existing OH4/OH5 `NativeHarnessRunContract` objects have no catalog object reference. They cannot be rewritten because they are content-addressed historical evidence.

The implementation therefore needs:

```text
NativeHarnessRunContract schema v1 decoder
NativeHarnessRunContract schema v2 encoder/decoder
```

The v1 compatibility path may use one frozen, closed mapping for exactly the seven OH5 Tool names. It must reject unknown names. This mapping is a legacy codec, not the live extension mechanism.

All new Assignments use v2 and the retained catalog snapshot.

## Proposed Tool semantic table

The table below is the implementation baseline. Exact semantic action strings should be validated against the pinned Protocol before coding, but the remaining fields are determined by current Runtime behavior and Host policy.

| Model Tool | Runtime operation | ToolContract effect | Execution | Completion | Idempotency | Correlation | Recovery consequence | Capability class |
|---|---|---|---|---|---|---|---|---|
| `read_workspace` | `workspace.read` | observe | synchronous | response | natural | receipt | observation-only | workspace-file-read |
| `mutate_workspace` | `workspace.mutate` | change | synchronous | accepted-verification | none | receipt | workspace-change-possible | workspace-source-change |
| `diff_workspace` | `workspace.diff` | observe | synchronous | response | natural | receipt | observation-only | workspace-diff-read |
| `run_check` | `workspace.exec` | change | asynchronous | accepted-verification | keyed | stable-key | process-or-external-effect-possible | workspace-execution-check |
| `run_in_workspace` | `workspace.exec` | opaque | asynchronous | accepted-verification | keyed | stable-key | process-or-external-effect-possible | workspace-opaque-execution |
| `observe_job` | `task.observe` | observe | synchronous | response | natural | receipt | observation-only | runtime-job-observation |
| `read_artifact` | `artifact.read` | observe | synchronous | response | natural | receipt | observation-only | runtime-artifact-read |

Evidence affordances:

```text
read_workspace     observation, version
mutate_workspace   observation, diff, version
run_check          observation, artifact, exit-status
run_in_workspace   observation, artifact
observe_job        observation, artifact, status
read_artifact      artifact, digest
```

`run_check` and `run_in_workspace` share a Runtime operation but must remain different native ToolSpecs because their authority, capability class, Provider schema and effect transparency differ.

## E1 rejected alternatives

### Add consequence fields to `AgentToolDefinition`

Rejected. `AgentToolDefinition` is a Provider-facing projection. Host recovery semantics must not become part of the Provider function schema abstraction.

### Persist only an aggregate consequence in `ToolGrant`

Rejected. ToolGrant is Assignment authority, not the semantic source. An aggregate alone cannot be historically rederived or audited against the catalog.

### Trust MCP annotations

Rejected as the semantic authority. Current Runtime annotations such as `readOnlyHint` and `destructiveHint` are useful presentation hints but do not express completion, correlation, stable delivery identity, verification, or process-loss consequence.

### Reuse `ActionProposal.ConsequenceClass`

Rejected. Proposal consequence describes affected ownership and reversibility before Host lowering. Tool recovery consequence describes uncertainty after possible physical delivery. They answer different questions.

### Reuse only Effect IR

Rejected for E1. Effect IR is the correct future representation for durable effectful steps, but OH1–OH5 Tool Calls are not yet Host-owned Effect/Dispatch lifecycles. E1 must describe the current ACI without pretending E3 is already implemented.

## E2 pure disposition model

### Principle

`NativeRunDisposition` is derived state. It must never become a CAS object, event authority, database table, or replacement for Assignment, Receipt, RecoveryAssessment, or Abandonment.

Persisted projections such as `harnessRunReplacementAllowed` may remain for compact handoff and audit, but must be rederived from durable objects and rejected on mismatch.

### Input facts

Proposed pure input:

```text
NativeRunFacts
├── phase
│   ├── assigned-unrecorded
│   ├── recovery-recorded
│   ├── abandoned
│   └── run-recorded
├── grantedRecoveryConsequence
├── terminationCode?
├── hasToolObservations
├── hasUnknownObservation
├── recoverySafeToAbandon?
└── hasCandidateConclusion
```

`grantedRecoveryConsequence` is aggregated from the Assignment-bound catalog snapshot and ToolGrant.

### Output

```text
NativeRunDisposition
├── unresolvedUnknowns
├── abandonmentAllowed
├── replacementScope
│   ├── forbidden
│   ├── same-workspace
│   └── any-workspace
├── completionRoute
│   ├── unavailable
│   ├── propose-current-run
│   └── reconcile-unknown
└── operatorAction
```

The output is a pure value with no storage, Runtime, Provider, clock, or event dependencies.

## E2 decision table

This table preserves current OH5 behavior.

| Phase / facts | Replacement | Completion | Operator action |
|---|---|---|---|
| Assignment committed; no Receipt or Abandonment | forbidden | unavailable | run current Assignment |
| Recovery safe; not yet abandoned | forbidden | unavailable | abandon current Run |
| Recovery retains UNKNOWN | forbidden | reconcile unknown | reconcile current Run UNKNOWN |
| Run abandoned | any Workspace | unavailable | replace Assignment |
| Recorded `runtime_unknown` or unknown Observation | forbidden | reconcile unknown | reconcile current Run UNKNOWN |
| Recorded `candidate_completed`, read-only or no Tool Observation | same Workspace | propose current Run | replace or propose completion |
| Recorded `candidate_completed`, effectful Grant and Tool Observation | forbidden | propose current Run | propose completion from current Run |
| Recorded non-candidate stop, read-only or no Tool Observation | same Workspace | unavailable | replace Assignment |
| Recorded non-candidate stop, effectful Grant and Tool Observation | forbidden | unavailable | verify current Run before replacement |

Replacement admission then compares a requested Workspace against `replacementScope`:

```text
forbidden       → reject
same-workspace  → require previous Workspace identity
any-workspace   → permit a new Workspace
```

## E2 integration points

### `HarnessHost.assign()`

Replace inline prior-Run branching with one disposition call and one replacement-scope check.

### `HarnessHost.record_native_run_recovery()`

Use catalog-derived granted consequence and a pure recovery derivation to produce UNKNOWN claims, `safeToAbandon`, and the projected Task state.

### `HarnessHost.record_run()`

Derive `harnessRunReplacementAllowed` and completion route from recorded facts. Do not infer directly from Tool names.

### `HarnessHost._run_from_snapshot()`

Decode durable objects, rederive disposition, and compare projected fields. It remains an audit boundary, not a second implementation of policy.

### `operator_handoff()`

Use a pure projection helper from the disposition module. It may consume compact event fields but must not reproduce policy branches.

### `validate_history()`

Recompute catalog binding, Recovery safety and Run projections from historical objects. It should validate, not independently decide.

## E2 rejected alternatives

### Persist `RunDisposition`

Rejected. It would duplicate truth already represented by Assignment, Receipt, Observations, RecoveryAssessment and Abandonment, and would require another supersession lifecycle.

### Use only event kind to determine handoff

Rejected. Event kind alone cannot distinguish Runtime UNKNOWN, effectful observed Runs, candidate completion, or Workspace continuity.

### Immediately classify recorded Runs from Trace Tool names

Deferred. The current Trace is digest-bound but not fully decoded and cross-checked against Tool Observations. Reducing conservatism before that validation would create a new evidence gap.

## Expected implementation sequence

### E1-A — shared semantics model

1. Add `harness/tool_semantics.py`.
2. Define `NativeToolRecoveryConsequence`, `NativeToolSpec`, and catalog snapshot decoding.
3. Reuse `anc_tool_contract.ToolContract` and existing Protocol enums.
4. Build the seven current specs without changing the Provider Tool surface.

### E1-B — durable catalog binding

1. Persist the catalog snapshot during native Assignment commit.
2. Add native Run Contract v2 catalog object binding.
3. Load and validate the snapshot in fresh Hosts and history Doctor.
4. Replace live Tool-name recovery classification with catalog aggregation.
5. Retain a closed v1 compatibility codec.

### E2-A — pure disposition

1. Add `harness/disposition.py` with no storage imports.
2. Encode the OH5 decision table as pure functions and exhaustive tests.
3. Preserve current behavior exactly.

### E2-B — call-site migration

1. Migrate replacement admission.
2. Migrate recovery safety and Task-state projection.
3. Migrate recorded Run projection and reload validation.
4. Migrate operator handoff.
5. Migrate historical audit.
6. Delete `native_tool_grant_effect_class()` from the live path.

## Test plan

### Catalog tests

- all seven ToolSpecs are complete and unique;
- Provider schemas remain byte-for-byte semantically identical after normalization;
- changing consequence, correlation, completion, capability class, lowering operation, or Runtime schema changes the catalog digest;
- unknown or incomplete Tool metadata fails closed;
- `run_check` and `run_in_workspace` remain distinct contracts;
- a new Tool cannot be granted without an explicit ToolSpec;
- v1 native Run Contracts remain readable and reject unknown legacy Tool names;
- v2 Assignments reload the exact retained catalog while the current live catalog may drift.

### Disposition matrix tests

- every row in the decision table;
- Runtime UNKNOWN dominates candidate completion or replacement projections;
- effectful Observation blocks replacement but not completion proposal;
- unrecorded effectful Grant remains unsafe after Workspace closure;
- Abandonment permits a new Workspace;
- recorded read-only replacement requires the same Workspace;
- projected event fields that differ from rederived disposition are journal corruption;
- handoff and Host admission consume the same pure result.

### Regression gates

- OH1–OH5 focused tests remain valid;
- all frozen live evidence remains historically readable;
- exact pinned Protocol suite, compileall, Ruff, boundary tests, handoff tests and history Doctor pass;
- no new Tool, Provider feature, automatic retry, Session, daemon, database or workflow language is added.

## Cost and benefit

E1–E2 did not reduce total line count immediately. The implementation added two focused production modules, strict v1/v2 compatibility and historical validation, plus a dedicated 12-test matrix. This exceeded the early line estimate because historical semantic reconstruction required more code than the live-path refactor itself.

The justification is semantic, not cosmetic:

- a new effectful Tool cannot silently become read-only;
- an old Assignment retains the exact Tool meaning it was authorized under;
- replacement, completion and handoff cannot drift independently;
- E3 can bind one durable Tool step to an already explicit contract instead of adding more name-based exceptions.

If implementation cannot preserve the OH5 matrix or requires a new service/database, stop and redesign.

## Promotion gate to E3

E3 design may begin only when:

1. every current Tool is catalog-bound through a durable semantic snapshot;
2. current and historical Assignments fail closed on missing semantics;
3. one pure disposition result drives Host replacement, Recovery, Handoff and historical validation;
4. all 255 deterministic tests pass under the exact pinned Protocol;
5. no OH1–OH5 live evidence changes meaning.

All five E1–E2 promotion conditions are now satisfied. E3 is eligible for a separate design review, but it has not started. E3 should target only prebound `run_check(checkId)` and establish durable Tool-step intent before Runtime delivery.
