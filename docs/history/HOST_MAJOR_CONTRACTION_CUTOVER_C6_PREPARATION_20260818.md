# Host C6 — Bundled Pre-1.0 Major Contraction Cutover Preparation — 2026-08-18

> Historical engineering evidence. This round canonicalizes the release shape proved by C1–C5. It does not reopen HDF0–HDF43, does not reopen the independent Coordination project, and does **not** activate the candidate in production.

## 1. Scope

C1–C5 established deletion proofs for historical Host responsibilities that no longer have an independent owner/consumer justification:

- C1 — shared Goal coordination;
- C2 — caller-neutral ExternalExecutor coordination;
- C3 — Host read/mutation/code-change engines, cognition execution/proposal orchestration, automatic TaskReconciler;
- C4 — generic Host capability-authority policy;
- C5 — product Runtime client/config/catalog/health-proxy integration.

C6 therefore changes mode. It does **not** ask what else can be deleted. It asks whether the cumulative contraction can be expressed as one auditable pre-1.0 Major-class release candidate with exact compatibility impact, retained-state proof, named-consumer proof, rollback peer, and non-activating deployment eligibility.

C6 verdict:

```text
CUTOVER PREPARED
DEPLOYMENT PLAN ELIGIBLE
PRODUCTION NOT ACTIVATED
```

## 2. Production baseline remains unchanged

Observed production remains:

```text
commit
8d7e58a0511734a454805e29d10e7d3bb754d2da

releaseId
8d7e58a0511734a454805e29d10e7d3bb754d2da-d9c7ebcc5c31

Host Journal schema
5

Host MCP
6 Tools
runtimeProxy=false
```

The current release symlink still resolves to:

`/usr/local/libexec/ordivon/host/releases/8d7e58a0511734a454805e29d10e7d3bb754d2da-d9c7ebcc5c31`

The production Git `refs/heads/main` still resolves to the same deployed commit.

No `apply`, symlink switch, service restart, production migration, or production candidate activation occurred in C6.

## 3. Cumulative compatibility census

The exact deployed-base → C5 candidate comparison found 72 package-root exports before contraction and 46 after contraction.

Twenty-six package-root exports are intentionally removed:

```text
CoordinationError
CoordinationSuperseded
ExecutionRuntimeCatalog
ExternalCompletionConflict
ExternalCompletionProposal
ExternalExecutionMissing
ExternalExecutionRequest
ExternalExecutionSnapshot
ExternalExecutorAdapter
ExternalExecutorCoordinator
ExternalExecutorError
ExternalObservationConflict
ExternalRequestConflict
ExternalRunBinding
ExternalRunObservation
ExternalRunStatus
GoalCoordinatorHost
GoalSnapshot
McpRuntimeClient
RecoveryResult
RuntimeCatalog
RuntimeSettings
TaskReconciler
TaskRevisionRef
discover_execution_runtime_catalog
discover_runtime_catalog
```

No new package-root compatibility aliases are added.

CLI breakage is explicit:

```text
removed: task reconcile
removed: doctor --runtime
```

Configuration breakage is explicit:

```text
removed: [runtime] Host config table
removed: RuntimeSettings
```

The candidate rejects these old surfaces rather than silently accepting/ignoring them.

## 4. Durable-state compatibility is not a migration cutover

The cumulative candidate introduces no changes to the Host-owned durable state implementation under:

```text
src/ordivon_host/journal/
src/ordivon_host/objects.py
src/ordivon_host/storage.py
```

The production and candidate Host Journal schema are both v5.

Therefore C6 classifies the release correctly:

```text
public Python/API/config/CLI compatibility
    Major-class breaking cutover

durable Journal/CAS interpretation
    no schema migration
```

Historical Event/CAS validation remains independent of deleted historical execution engines and policies.

## 5. Version policy: 0.3.0

The currently deployed compatibility line is 0.2.x.

Host remains pre-1.0, so the Major-class contraction uses a minor-line cutover:

```text
0.2.x -> 0.3.0
```

This is not a claim that 0.x breaking changes are implicit. `docs/RELEASES.md` now explicitly requires Major obligations for pre-1.0 removals/reinterpretations and states that a Major-class cutover advances the minor version until 1.0.

`pyproject.toml` candidate version:

```text
0.3.0
```

The package description is also narrowed from a coordination/commitment-plane ontology to:

```text
Durable semantic continuity and Host-owned state authority for Ordivon.
```

## 6. Changelog and active release contract

`CHANGELOG.md` now records the 0.3.0 contraction under Unreleased with explicit Changed / Removed / Compatibility sections.

It names:

- C1–C5 responsibility removals;
- 26 package-root export removals;
- deleted Runtime config/CLI surfaces;
- retained schema v5;
- World and Security as named compatibility consumers;
- exact deployed commit as rollback peer.

C6 also corrected release infrastructure that still encoded the old ontology.

### Old invalid gate

Before C6, release policy and CI still required a read-only Host→Runtime workload journey, `live_runtime_read.py`, product `runtime/mcp.py`, Runtime credentials, and Runtime transport compatibility.

C3/C5 had already falsified those as current Host product responsibilities.

Keeping that release gate would have created the pathological rule:

```text
Host cannot release unless it reintroduces a responsibility already proved not to belong to Host.
```

### New owner-correct gate

`scripts/local-acceptance` now verifies Host-owned behavior only:

- docs contract;
- dependency contract;
- compileall;
- Ruff;
- deterministic full suite;
- isolated Host state init;
- local Doctor;
- full-history Doctor.

It emits:

```text
kind = ordivon.host-local-acceptance
runtimeContacted = false
```

Cross-repository correctness is proven by named real consumers rather than a Host proxy.

Major-class local activation additionally requires retained production state/history proof and receipt-bound deployment prepare/plan.

`.github/workflows/system-acceptance.yml` is correspondingly changed from Host Runtime acceptance to Host-local system acceptance. The pre-existing self-hosted runner label is retained only as infrastructure naming and no longer implies a Runtime dependency.

## 7. Protocol lock metadata correction

Running `uv lock --offline` changed two lock metadata versions:

```text
ordivon-host       0.2.0 -> 0.3.0
ordivon-protocol   0.3.0 -> 0.4.0.dev0
```

The second line is **not** a Protocol commit upgrade.

The dependency remains pinned to the exact same commit:

`5f9b418f1c366befa780f0ace1c6c8e64c721a3e`

Inspection of that exact commit's `packages/ordivon-protocol/pyproject.toml` proves its real package metadata is already:

```text
version = 0.4.0.dev0
```

Thus the old Host lock carried stale version metadata for the same immutable dependency commit. C6 corrects that metadata without changing dependency identity.

## 8. 0.3.0 release contract commit

The release-contract source commit is:

`e2a85a369df2cc8f963ea6bf337701276eb7a931`

Subject:

`release(host): prepare 0.3 contraction cutover`

This commit contains:

- cumulative C1–C5 contracted implementation;
- active docs aligned to the contracted architecture;
- 0.3.0 version/description;
- Changelog compatibility notice;
- owner-correct release policy;
- rewritten local/system acceptance;
- corrected lock metadata.

The present C6 report is intentionally committed **after** that release-contract commit and is not part of the already materialized 0.3.0 candidate bytes.

## 9. Exact 0.3.0 Host acceptance

The exact clean commit `e2a85a3...` passed the rewritten Host acceptance contract.

Results:

```text
documentation contract   PASS
dependency contract      PASS
Ruff                      PASS
compileall                PASS
157 / 157 tests           PASS
isolated state Doctor     PASS
isolated history Doctor   PASS
runtimeContacted          false
gitleaks                  no leaks found
```

Exact Runtime Job:

`job-01a0135a-6316-7f43-ba89-41270cbad068`

Terminal evidence:

`sha256:6e233376a81a5e202633b9daa70c0054bed1fd8b7c4f17cb59b7b256a138ac9e`

The acceptance receipt binds:

```text
hostRevision = e2a85a369df2cc8f963ea6bf337701276eb7a931
packageVersion = 0.3.0
runtimeContacted = false
```

An earlier acceptance run before committing the release-contract edits bound the prior source revision while testing the dirty candidate. C6 deliberately reran acceptance after commit so final evidence binds the exact candidate Git identity.

## 10. Named real-consumer acceptance

Against exact commit `e2a85a3...`:

### World

```text
158 / 158 PASS
```

### Security

```text
22 / 22 PASS
```

Runtime Job:

`job-01a01356-8a0e-7613-a49c-b159606ee43a`

Terminal evidence:

`sha256:78177c723d34b8d5c705ee477b7f438909c01a7907427aaf36cd681ccc7374f9`

This confirms that C6 release-contract/version changes did not invalidate the real consumers preserved through C1–C5.

## 11. Production retained-state snapshot proof

C6 did not allow candidate code to open/migrate production state directly before activation.

Instead:

1. the **currently deployed 0.2.0 release binary** created an official backup of `/var/lib/ordivon/host`;
2. the 0.3.0 candidate verified that backup;
3. candidate code restored it to an isolated temporary state root;
4. candidate Doctor ran full retained-history validation against the restored copy;
5. the temporary state was removed afterward.

Final successful proof:

```text
backup Host Journal schema = 5
candidate Doctor healthy    = true
journal.history             = ok
candidate inspect schema    = 5
schema_migrations to        = 5
Events                      = 2210
Tasks                       = 461
object_refs                 = 4401
```

Runtime Job:

`job-01a01355-cb0a-74d3-af1a-5611e5de6dad`

Terminal evidence:

`sha256:48b08dae6154efb20d7f75129db1686da14676603885bae336e8c5b8323c2010`

### Audit-script failures preserved rather than hidden

Four earlier retained-state attempts did not justify rejecting the candidate:

1. attempted to import nonexistent `SCHEMA_VERSION` from `journal.migrations`;
2. attempted to call nonexistent `HostJournal.list_tasks()`;
3. queried nonexistent SQLite table `tasks` instead of `task_projection`;
4. incorrectly treated SQLite `PRAGMA user_version` as Host schema authority and accidentally printed the full `objectRefs` list.

Each error occurred in audit-only summary code, not in candidate backup verification / state decoding semantics. The final proof removed all guessed APIs/predicates and used Host-owned Doctor/history plus the actual schema migration table.

These failures are retained as evidence that the release gate was not waved through on an ambiguous result.

## 12. Candidate materialization

Deployment `prepare` first rejected an incorrect `--python-runtime-root` argument because the flag denotes the parent authority containing immutable runtime children, not the child runtime itself.

After using the exact validator contract, candidate preparation succeeded.

Candidate:

```text
releaseId
 e2a85a369df2cc8f963ea6bf337701276eb7a931-25bd190a3815

candidateDir
 /var/lib/ordivon/host/candidates/e2a85a369df2cc8f963ea6bf337701276eb7a931-25bd190a3815

effectiveDigest
 sha256:25bd190a38156a5049bd32f78af5a8efc21b5d1624d01e5e533f6d25302ebc0c

wheelDigest
 sha256:be01cf1c53f2784f7e0ff4647b8f4c4d4c476517d15f13fdda9c090d1875b65b

lockDigest
 sha256:67d7c10dbcfd4969b1f29ccb891c60b1a9a25993de3c3720adb8a7a717377d81
```

The candidate uses the same immutable shared CPython 3.12.13 runtime tree as the current release:

```text
runtime content digest
sha256:201f932d1a141f696c145df375c652fbed7a673389cb17a8d556ae55403b28a9
```

Prepare Runtime Job:

`job-01a01357-aa7d-7492-ae42-c0d2e533e3ab`

Terminal evidence:

`sha256:795542e3f01b5d5a76f3bc8ea000e7fc7f8608a615c84955f2cb010da84b8251`

No release symlink was changed by prepare.

## 13. Git source-governance gate

The first deployment plan correctly failed closed because the default required ref is:

```text
refs/heads/main
```

and main still points at deployed `8d7e58a...`, not `e2a85a3...`.

C6 deliberately did **not** fast-forward main merely to make the plan green.

Instead it created one explicit local release-candidate ref:

```text
refs/heads/release/host-0.3.0-candidate-20260818
    -> e2a85a369df2cc8f963ea6bf337701276eb7a931
```

This permits non-activating plan evaluation while preserving the distinction:

```text
main                    still production line
release-candidate ref   exact candidate governance handle
```

The ref is local preparation evidence; promotion/push policy is a later activation/release decision.

## 14. Deployment plan

With the explicit release-candidate ref, deployment plan returns:

```text
eligible = true
blockers = []

candidateSchemaVersion = 5
liveSchemaVersion = 5
migrationRequired = false
previousReleaseSchemaVersion = 5
explicitRollbackSupportedAfterSuccess = true
activationRollbackPolicy = release-bytes-only
```

Current rollback peer:

`8d7e58a0511734a454805e29d10e7d3bb754d2da-d9c7ebcc5c31`

Candidate release:

`e2a85a369df2cc8f963ea6bf337701276eb7a931-25bd190a3815`

Canonical plan digest:

`sha256:05f96807f3c58523ee473ecd4778cc9e70bdc149afd5df14f63e987df4ee5714`

Eligible-plan Runtime Job:

`job-01a01358-7775-7e70-8568-6169980ff68f`

Terminal evidence:

`sha256:4fbb1dfd4a16f4da9e40454a3c079be8e6f2124ce2a41deabbd537655b4b5dc4`

No `apply` command was executed.

## 15. Release hygiene

C6 also ran:

```text
pip-audit --strict
    No known vulnerabilities found

gitleaks
    no leaks found
```

The final plan digest / hygiene Runtime Job:

`job-01a01359-0be1-7391-8d8e-a27eb57e0a90`

Terminal evidence:

`sha256:3a247e12780cb82e90ac08c931348a80eab416b01a1f2dc4ebd80df723ed7d8f`

Hosted GitHub workflow/CodeQL status was not observed from this local preparation round, so C6 does not claim those remote CI executions have passed for the release-candidate ref.

## 16. Final target architecture represented by 0.3.0

The release candidate now expresses the evidence-derived Host boundary:

```text
Host
= durable semantic continuity
+ Host Journal/CAS authority
+ exact Task revision/lease admission
+ WorkingCheckpoint adopt/checkpoint/resume
+ bounded handoff/inspection
+ owner-opaque extension durability with real consumers
+ bounded context-selection semantics with Security consumer
+ local Doctor/backup/restore/deployment integrity
```

It does not implement as generic Host responsibility:

```text
shared Goal coordination
foreign executor coordination
Runtime workload execution
read/mutation/code-change engines
cognition execution/proposal orchestration
automatic cross-owner reconciliation
generic capability authorization
Runtime client/config/health proxy
```

This is engineering consumption of the frozen HDF40–HDF43 boundary rather than a new Foundation claim.

## 17. Activation conditions for the next phase

C6 intentionally stops before activation.

A later activation round should require all of the following against the **same exact candidate bytes** or a deliberately rebuilt candidate if source changes:

1. source-governance promotion is explicit — e.g. candidate commit is promoted to the intended canonical release/main ref rather than relying only on the local candidate branch;
2. required remote CI/release workflows for that exact source identity are green or explicitly replaced by equivalent retained evidence;
3. deployment plan is recomputed after any ref/source change and remains `eligible=true`, `blockers=[]`;
4. candidate effective/wheel/lock digests match the prepared release or are deliberately replaced by a newly prepared release identity;
5. current production release remains the named rollback peer until successful observation completes.

No activation should infer these conditions from C6 alone.

## 18. Post-activation observation window

For the eventual activation, use one bounded observation window rather than treating process restart as success.

Recommended initial window:

```text
30 minutes or one complete normal continuity-use cycle,
whichever provides stronger evidence before lifecycle GC/retirement.
```

During that window verify:

- deployment status resolves current to the 0.3.0 releaseId;
- `host.status` summary/integrity/history remains healthy;
- MCP surface remains exactly six Tools and `runtimeProxy=false`;
- current external-continuity Tasks can still `resume` at exact revisions;
- one exact checkpoint replay/continuation behaves correctly;
- World named consumer remains green;
- Security context consumer remains green;
- no schema migration occurred;
- previous 0.2.0 release remains physically retained and rollback-eligible.

This is an activation procedure definition, not background work performed by C6.

## 19. Immediate rollback triggers

Rollback should be triggered rather than repaired in place if activation produces any of:

```text
Host MCP service not healthy after candidate activation
current release identity differs from the planned releaseId
Host Journal schema != 5
Doctor / full-history validation fails
MCP Tool catalog differs from the intended six-Tool contract
external-continuity resume/checkpoint regression
World supported-consumer regression
Security context-consumer regression
unexpected durable migration or reinterpretation
candidate content/runtime identity differs from prepared receipt
```

Rollback authority is the exact retained 0.2.0 release bytes, not reconstruction from current source.

## 20. C6 conclusion

C6 establishes:

```text
0.3.0 compatibility statement       READY
Changelog                           READY
active docs/release policy          READY
portable/local Host acceptance      PASS
exact 0.3.0 acceptance              PASS
World consumer                      PASS 158/158
Security consumer                   PASS 22/22
production retained-state snapshot  PASS
schema migration                    NONE
candidate materialization           PASS
candidate Git ref                   PRESENT
non-activating deployment plan      ELIGIBLE
pip-audit                            PASS
secret scan                          PASS
production activation               NOT PERFORMED
main branch promotion               NOT PERFORMED
remote CI status                     NOT CLAIMED
```

Therefore the engineering state is:

```text
C1–C5 contraction has moved from deletion-proven research evidence
into an exact, materialized, rollback-planned 0.3.0 release candidate.

C6 = CUTOVER PREPARED / NOT ACTIVATED.
```

The next phase, if continued, should be **activation readiness / canonical cutover**, not another architecture-deletion search. Host naming should remain a separate post-canonicalization decision.
