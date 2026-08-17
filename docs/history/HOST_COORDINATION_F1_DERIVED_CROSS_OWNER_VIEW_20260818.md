# Host → Coordination F1 — Derived Cross-owner Coordination View Falsifier — 2026-08-18

> **Historical engineering experiment, not current architecture authority.** This record consumes frozen HDF0–HDF43 into one bounded F1 engineering falsifier. It does not rename Host, create a Coordination database, add a public artifact, migrate state, change MCP/API behavior, or authorize Coordination as an independent project. Current Host authority remains `README.md`, `ARCHITECTURE.md`, `docs/STATUS.md`, `docs/authority.md`, source/tests, deployed state, and owner-native systems.

## 1. Question

F1 asks whether there is any real operational value in a read-only **Derived Cross-owner Coordination View** after Stage 0 already rejected a generic Coordination database / persisted Projection store as the first engineering move.

The view is admitted only if all of the following survive:

1. one real consumer case depends on facts owned by multiple independent owners;
2. no single owner fact naturally owns the composite relation;
3. direct cheap substitutes such as latest Task state, Task revision movement, or one stale case checkpoint are insufficient or unsafe;
4. the view preserves each source's truth role rather than choosing truth;
5. the view creates no execution, scheduling, delegation, authority, consent, validity, or scoring power;
6. removing the relation reintroduces a reproduced false-negative, false-positive, unsafe retry, or material manual stitching failure;
7. no new durable storage/schema is needed before this value is proven.

Hard constraints remain:

- `Projection != SourceTruth`.
- `Coordination != Runtime != Harness != Network != World truth != Institution/Governance`.
- `Handoff != Delegation`.
- `AttentionRouting != Authority`.
- HDF41 permits dependency-relative local compatibility without global canonical coordination state.
- HDF42 permits many maintenance mechanisms; Continuity Core is not universal maintenance ownership.
- HDF43 keeps validity/authority order-relative and owner-native.

## 2. Baseline

F1 continued from engineering continuity Task:

- `task:host-foundations-engineering-consumption-20260818`
- starting Task revision: `2`
- audit Runtime workspace: `host-foundations-engineering-consumption-audit-20260818`
- Stage-0 isolated audit commit: `bc9da5984d22b8fcb4720a730000cf2724fef742`
- deployed Host remained `8d7e58a0511734a454805e29d10e7d3bb754d2da`

F1 created no Host production state except the ordinary semantic continuity checkpoint that will record this experiment after closeout.

## 3. Pilot A — Harness × Runtime late convergence

### 3.1 Why this was tested first

Current Harness already has a real independent-owner bridge to Runtime through `SQLiteHarnessRuntimeBridge`. This is stronger evidence than inventing a synthetic World/Runtime bridge.

Historical Harness evidence also contains a real orphan gap where:

- Harness durably prepared a Tool Step;
- one physical Runtime call occurred;
- Harness did not yet have a terminal Tool receipt.

Harness later repaired that historical race owner-natively with revision/lease fencing. Therefore the historical gap proves the relational phenomenon exists but does **not** prove Coordination ownership.

### 3.2 Current Harness semantics attacked

Current Harness source at the F1 target was:

`7277e1074be83fa38c28cd8170c28d6f4223146e`

Relevant current semantics:

- `HarnessToolStepStatus.UNKNOWN` is terminal;
- terminal Tool Step Receipt cannot be superseded;
- `reconcile_current_tool_step()` returns the retained terminal observation when present, or asks for caller-authorized content rehydration when privacy omitted it;
- it does not re-query Runtime after terminal `UNKNOWN` and convert history to a later Runtime outcome.

This preserves immutable Harness history but opens a possible late-convergence relation: Runtime can later have stronger physical evidence while Harness still correctly retains an earlier terminal `UNKNOWN`.

### 3.3 Prospective real Runtime probe

A separate clean Runtime target workspace was opened from current Harness source:

- workspace: `host-f1-harness-runtime-target-20260818`
- source revision: `7277e1074be83fa38c28cd8170c28d6f4223146e`
- source state digest: `sha256:06af31a1b7feb0c62bca50d09ec76ab57f1c1d4fba7521f6b7819f63a37d3bbd`

The experiment used current Harness and the real local Runtime. A thin test proxy:

1. forwarded exactly one real `workspace.exec` admission;
2. then hid the Runtime response from Harness to simulate response loss;
3. hid Harness's immediate `task.list` reconciliation to simulate temporary reconciliation visibility loss;
4. let Harness durably record its own outcome;
5. bypassed the fault proxy and re-observed Runtime owner truth directly by exact Harness `clientRequestId`.

No external effect beyond an observation-only workspace search was requested.

### 3.4 Successful probe

F1 probe runner:

- Runtime Job: `job-01a01106-3e49-7090-b0f7-42ebd3735654`
- stdout digest: `sha256:a22fac66b18bed7f8abc1a1bbc4c730961cd6dd87f2c6d56cfb4ba1905fa47df`
- terminal evidence: `sha256:a8e1feeff0342afdb7eb7ae4eddd406aabe4bef105dd9e57388ffa9a58c781a5`

Real inner Runtime Job:

- Job: `job-01a01106-40cf-7160-afbc-e2aabb49002a`
- Attempt: `attempt-01a01106-40cf-7160-afbc-e2b81abf4bc5`
- exact `clientRequestId`: `request:harness:g1:d18ccdcb219e36caaf802492fd4494ab`
- physical dispatches: `1`
- duplicate dispatches: `0`
- resolution: `succeeded`
- mechanically converged: `true`
- semantic completion evaluated: `false`

Harness retained:

- Run: `harness-run:p0-runtime-f1-live-1786992017395848799`
- Run revision: `3`
- Tool receipt status: `unknown`
- terminal: `true`
- receipt digest: `sha256:ba29ebea54261fb803bb70585c3be8335b9dca4679b26104d778a727f50f03cf`

Fresh Runtime owner observation for the same exact request reported:

- execution terminal: `true`
- execution disposition: `succeeded`
- delivery disposition: `committed`
- recovery required: `false`
- result available: `true`
- semantic completion evaluated: `false`

An ephemeral derived relation correctly classified:

`runtime-ahead-of-harness`

with the constraints:

- Runtime physical success does not rewrite Harness historical `UNKNOWN`;
- Harness historical `UNKNOWN` does not erase Runtime completion;
- no Harness semantic completion may be inferred;
- no Runtime redispatch is authorized;
- any repair belongs to Harness.

### 3.5 Privacy alternative explanation falsified

A second real Runtime probe enabled Harness Tool content retention:

`HarnessPrivacyPolicy(content_policy="bounded-private-content", allow_tool_content=True)`

Probe runner:

- Job: `job-01a01107-437e-7d00-850d-0a04f7bfdf4c`
- stdout digest: `sha256:651ec92b0c03227852b622693b960521dd95446c84085ba319aa69f7bb6fbcca`
- terminal evidence: `sha256:e0afface6181434d01cf969e820c84b050151a774f1fac312c30428d23dea924`

Inner Runtime Job:

- `job-01a01107-4602-7af3-bbc5-a61f45f216d2`
- exact client request: `request:harness:g1:6f33115b4125a8c0e89184b37c8e3f1b`

Results:

- one physical Runtime dispatch;
- Runtime terminal + committed;
- initial Harness status `unknown`;
- native late reconciliation still returned `unknown`;
- Harness receipt digest unchanged;
- Harness receipt status unchanged.

Therefore late divergence is structural, not merely caused by privacy omitting Tool content.

### 3.6 Pilot A ownership verdict

The relation is real, but this case does **not** admit Coordination ownership.

Why:

- the Tool Step is Harness-owned;
- Harness initiated the Runtime request;
- exact repair naturally belongs to Harness late-reconciliation / recovery policy;
- Runtime already owns the physical result;
- a Coordination layer should not rewrite either owner.

Classification:

- relational state `runtime-ahead-of-harness`: **real**;
- read-only cross-owner observation: **potentially useful**;
- repair capability: `owned-elsewhere` → **Harness**;
- new `ReconciliationRecord`: **not admitted**;
- new Coordination storage: **rejected**.

This pilot prevents a false F1 success based merely on the existence of cross-owner divergence.

The clean target workspace was removed after exact digest verification.

## 4. Search for a relation with no single natural source owner

F1 then searched the live external-continuity corpus for active non-foundation cases containing multiple owner references plus waiting/dependency/reconciliation semantics.

The strongest case was:

`task:computing:fs0-shadow-portfolio-calibration-20260811`

Current FS0 checkpoint:

- Task revision: `4`
- checkpoint digest: `sha256:0f7047089139b263cdf8b452bc130bb689e6f876437bc3113a482ce8ce1b4f7a`
- frozen prediction receipt identity: `sha256:bb406bf0907e9bb23fa41c9306c876b5f44b0dfe388008b9eb5c1deb776f89fc`
- current raw prediction JSON byte hash observed by F1: `sha256:ad9701e724d042b2103d7927d24df979faf8a053c8845bc0967b8dcabb4e9071`

The two digests have different roles. `bb406…` is the receipt's declared semantic/receipt identity embedded in `fs0-predictions-v1.json`; it is not the raw file byte SHA.

## 5. Pilot B — FS0 pressure-identity composite readiness

### 5.1 Why this case is materially different

FS0 is not an owner execution workflow. It is a Computing-owned research consumer that intentionally observes several independent owner pressures without controlling them.

Its frozen comparison depends principally on outcomes from:

- `G-AF3` — Game AF003;
- `R-P5` — Runtime P5.

Auxiliary candidates include:

- `H-P6` — Harness P6;
- `HOST-PKG` — Host packaging pressure;
- `F-C2-BLOCKED` — Finance negative control.

FS0 rev4 explicitly states that **Task progress is not the outcome identity**. Finance had advanced on a different Venue World/OKX frontier while the frozen C2 independent-host pressure remained blocked.

Therefore the coordination question is not:

`Did each Task advance or terminate?`

It is:

`Did the exact pressure/discriminator that the frozen FS0 case depends upon acquire an owner-native outcome?`

That relation is not owned by Game, Runtime, Harness, Host, or Finance individually. Computing owns the eventual FS0 scoring/interpretation, but it does not own the source outcomes.

### 5.2 Exact Game G-AF3 outcome

Frozen FS0 observed Game at revision 12.

Game rev12 checkpoint:

- AF002 complete;
- exact next frontier = AF003;
- seed strings explicitly shown not to create independent World variation;
- AF003 required 2–3 materially different Scenario/Genesis variants;
- robustness/generalization was explicitly not yet earned.

Game rev13 then produced the exact pressure outcome:

- Task: `task:game:station-zero-v3-playtest-validation-20260809`
- owner checkpoint revision: `13`
- digest: `sha256:fc1299e6be77a99d91fbe954109cf158cd0e431f31a1a5ef34e17fb46c22b553`
- AF003 complete;
- four exact content-addressed evaluation Worlds replaced seed pseudo-variation;
- P2 negative transfer was found;
- one-strike and two-strike memory were rejected as universal laws;
- fixed matched states displaced low-information whole-run repetition;
- the next frontier moved to AF004.

The same Game Task later continued to revision 20 and remains `ready`, because later GX work continues in the same broad continuity case.

Thus:

`Game Task terminality != G-AF3 pressure outcome availability`.

### 5.3 Exact Runtime R-P5 outcome

Frozen FS0 had observed Runtime P5 as partial and reopened by a hidden launcher/provider coupling.

Runtime later produced a final owner outcome:

- Task: `task:runtime:rsi-p5-foundation-closeout-20260811`
- checkpoint revision: `9`
- digest: `sha256:e283d718daa44b571d63d7347f392251e9f1699d11df50b8d4da67f287dfc529`
- Task state: completed
- final source/deployment convergence: `68275eb1af61b9bb837f09a8058d49fc01b36080`
- provider-release discontinuity closed;
- structured self-release succeeded;
- exact replay produced no second physical deployment;
- fresh production `windows_native` immutable-input execution graduated successfully;
- final Runtime health was clean.

This is an owner-native pressure outcome. Coordination does not decide whether RFM was “right”; Computing/FS0 owns that evaluation.

### 5.4 Auxiliary owner outcomes

Harness P6:

- Task: `task:harness-host:rsi-p6-20260811`
- outcome revision: `5`
- checkpoint digest: `sha256:4f380cf9005dc0ea9553a5b1e7bc2801f01caf9b74cb72fe3251b48468db668b`
- owner outcome available.

Host packaging:

- Task: `task:host:world-core-dependency-surface-20260810`
- outcome revision: `3`
- checkpoint digest: `sha256:a696b0b53ead5713d37421f54552a1b4bc99c08752ccef4ed280321cdcf26346`
- packaging pressure resolved owner-natively by moving MCP server dependency into an explicit server extra while preserving a narrow base install.

Finance negative control:

- FS0 rev4 says later Finance Task activity belonged to a different Venue World/OKX frontier;
- frozen `F-C2-BLOCKED` independent-host pressure remained unresolved;
- F1 did not invent an owner-task mapping merely to complete the view.

This unresolved relation is intentionally preserved rather than guessed away.

## 6. Ephemeral Derived Cross-owner View

F1 built the view **in memory only**. No source type, table, event, MCP tool, schema migration, or persistent Coordination object was created.

The minimal relation was:

```text
CaseRef
  = FS0 Task @ exact revision/checkpoint

PressureDependency
  = pressure identity
  + owner
  + owner Task ref
  + frozen observed owner revision
  + exact owner outcome checkpoint revision/digest
  + required/auxiliary role

Derived relation
  = whether the exact pressure-bound owner outcome is now available
```

The resulting principal relation was:

```text
G-AF3  -> owner outcome available at Game rev13
R-P5   -> owner outcome available at Runtime rev9

principal composite state
  -> ready-for-fs0-owner-evaluation
```

The view explicitly **does not**:

- score raw vs RFM;
- choose a research winner;
- scalarize incompatible value vectors;
- schedule Game/Runtime/Harness/Finance/Host;
- grant action authority;
- replace source truth;
- rewrite FS0's frozen checkpoint;
- create a global coordination state.

Its only derived ownership candidate is:

> **pressure-identity-bound composite outcome availability**

and the next operation is routed back to `Computing/FS0` for owner-native evaluation.

Primary ephemeral-view probe:

- Runtime Job: `job-01a0110b-ce39-77e0-a81d-c08ba28a9432`
- stdout digest: `sha256:542ecc3983f67d71b440fce61e29b761e53f0daba4dfe0ab63120e98e05d3019`
- terminal evidence: `sha256:87da9705ff26e0851788590aa50efd048c16274848ccbcdce116d64789617631`

## 7. Deletion test

The key F1 burden was whether cheaper representations could replace pressure identity.

Deletion-test probe:

- Runtime Job: `job-01a0110c-bc8e-7322-91d1-19a7e68abce9`
- stdout digest: `sha256:8991fcb1991db84a60a93dfa9d52df1c0e53ecdb5819728cbb1a1271eadd2021`
- terminal evidence: `sha256:24f9afd89eb5b50e0f7fd603b435f7dd2afd9a38d510cf708171ca71ef04104d`

### 7.1 Baseline A — Task terminality

Result: **fails with false negative**.

Game Task is still `ready` at revision 20 even though the exact G-AF3 dependency acquired its outcome at Game rev13.

Therefore:

`Task terminality` cannot substitute for pressure outcome availability.

### 7.2 Baseline B — Task revision advancement

Result: **known unsound / false-positive capable**.

Game, Runtime, Harness and Host Tasks all advanced beyond the revisions observed at prediction freeze. But FS0 rev4 already supplies a negative control: Finance Task progress occurred on a different frontier while frozen `F-C2-BLOCKED` remained unresolved.

Therefore:

`Task revision advanced` cannot substitute for pressure outcome availability.

### 7.3 Baseline C — case checkpoint only

Result: **fails with stale false negative**.

FS0 rev4 still states:

- G-AF3 pending;
- R-P5 partial/reopened;
- principal discriminator not identifiable.

Owner history later changed the relational readiness without changing the frozen FS0 checkpoint.

Therefore a stale case checkpoint cannot alone determine current cross-owner readiness.

### 7.4 Deletion conclusion

All deletion gates passed:

- frozen receipt identity matched;
- exact Game pressure outcome exists;
- exact Runtime pressure outcome exists;
- principal composite is now ready;
- terminality baseline fails;
- revision-advancement baseline is known unsound;
- stale case checkpoint baseline fails;
- pressure identity is required for correctness;
- Coordination does not own scoring.

This is the first F1 case in which the derived relation cannot be reduced to one source owner's internal recovery semantics.

## 8. Failure modes established by F1

F1 now has direct engineering evidence for two coordination-shaped failure modes.

### 8.1 Cross-owner relational divergence

Example:

`Runtime mechanically converged` while `Harness durably retains historical UNKNOWN`.

This relation is real but its repair is Harness-owned in the tested case.

### 8.2 Composite-readiness staleness / false convergence

A consumer case depends on exact owner pressure outcomes.

Two symmetric failures exist:

- **stale false negative**: the case checkpoint says “not ready” after exact owner outcomes have become available;
- **false convergence**: Task activity/revision movement is mistaken for satisfaction of the exact dependency even though the owner advanced on another frontier.

The FS0 Finance negative control proves the latter is not hypothetical.

These failures are relational: they arise from relations between a frozen case dependency and later independently evolving owner facts.

## 9. Minimal invariant surviving F1

F1 does **not** justify a universal `Projection` artifact.

The smaller surviving invariant is:

```text
DerivedCoordinationRelation(case, dependency, owner-observation)

must bind:
- exact case identity/revision or case contract identity;
- exact dependency identity meaningful to that case;
- source owner identity;
- exact source revision / immutable evidence reference;
- source truth role/currentness claim;
- derived relation status;
- unresolved/missing state without guessing;
- no action/authority implication.
```

For F1 the only admitted derived status is effectively:

`dependency-outcome-available | dependency-outcome-unresolved`

under an exact pressure identity.

This is deliberately weaker than:

- generic Dependency object;
- global graph edge;
- persisted Projection;
- ReconciliationRecord;
- AttentionRequest;
- LivenessState;
- scheduler readiness;
- truth convergence.

## 10. Six-question artifact gate

### Is a new durable `Projection` admitted?

1. Facts it would own: source-qualified observation copy plus derived relation.
2. Why not owner-native: source observation is owner-native; only relation is cross-owner.
3. Consumer: FS0 demonstrates a consumer for relation availability.
4. Failure without it: stale false-negative / false-convergence risk.
5. Mirror risk: **high** if source payloads are copied.
6. Minimal invariant: exact source references + relation status only.

Verdict: **do not add durable Projection**. Use a derived read model first.

### Is a generic `Dependency` / graph admitted?

FS0 demonstrates one exact pressure-dependency relation, but not a reusable taxonomy sufficient for generic `requires/blocks/waits-for/...` storage.

Verdict: **not admitted**.

### Is `ReconciliationRecord` admitted?

Harness/Runtime pilot shows divergence but repair is Harness-owned. FS0 is readiness observation, not reconciliation.

Verdict: **not admitted**.

### Is `AttentionRequest` admitted?

No prospective attention-starvation failure was demonstrated.

Verdict: **not admitted**.

### Is `LivenessState` admitted?

F1 does not need a timer/scheduler.

Verdict: **not admitted**.

### Is a read-only Derived Coordination View admitted as an engineering hypothesis?

Yes, narrowly.

Owned relation:

> exact case-dependency × owner-outcome availability/current relation

Consumer value:

> detects current composite readiness while avoiding Task-terminal false negatives and unrelated-revision false positives.

Verdict: **narrowly admitted for transfer testing; not yet a durable product artifact**.

## 11. Ownership disposition

| F1 result | Owner / disposition |
| --- | --- |
| Runtime physical outcome | Runtime |
| Harness Tool Step history | Harness |
| Harness late-reconciliation repair | Harness |
| Game AF003 outcome | Game |
| Runtime P5 outcome | Runtime |
| Harness P6 outcome | Harness |
| Host packaging outcome | Host |
| FS0 selection/regret interpretation | Computing/FS0 |
| pressure-identity-bound composite availability | candidate Derived Coordination relation |
| source truth arbitration | nobody in Coordination |
| scheduling/prioritization | not granted |
| generic persistence | Continuity Core only where already proven |

## 12. F1 verdict

### Rejected interpretations

F1 does **not** prove:

- Coordination should become a new repository now;
- Host should be renamed now;
- Coordination needs a database;
- Coordination needs a graph;
- all cross-owner divergence is Coordination-owned;
- all dependencies should become first-class artifacts;
- Coordination may decide which source is true;
- Coordination may schedule owners;
- Coordination may score FS0.

### Accepted result

F1 establishes a narrower engineering proposition:

> There exist real Ordivon consumer cases whose actionable continuation depends on a relation between an exact case-specific dependency identity and independently evolving owner-native outcomes. That relation cannot safely be replaced by Task terminality, Task revision movement, or the case's own stale checkpoint. A read-only source-qualified derived view can expose the relation without becoming a truth, execution, scheduling, or authority owner.

This is the first engineering evidence that something **beyond Continuity Core** may deserve a Coordination-layer capability.

But one successful FS0 consumer is insufficient for Harness/Runtime-level independent-project admission.

Current project verdict:

```text
Continuity Core                         ADMITTED
Derived cross-owner relation/view      NARROW F1 PASS / TRANSFER CANDIDATE
Durable Projection                     NOT ADMITTED
Generic Dependency Graph               NOT ADMITTED
Reconciliation artifact                NOT ADMITTED
Attention/Liveness artifacts           NOT ADMITTED
Coordination independent project       NOT YET ADMITTED
Host rename/migration                   NOT AUTHORIZED
```

## 13. Next cheapest falsifier

The next experiment should be a **transfer falsifier**, not artifact expansion.

Take the exact minimal F1 relation grammar:

- case ref;
- case-specific dependency identity;
- owner ref;
- owner revision/evidence ref;
- derived availability/unresolved relation;
- no authority.

Apply it to a second materially different real consumer whose composite readiness is not naturally owned by Computing/FS0.

The transfer must answer:

1. Can the same relation grammar work without adding case-specific schema fields?
2. Does it again outperform Task-state/revision/checkpoint-only baselines?
3. Is there a genuine consumer action unlocked by the relation?
4. Does one existing owner already naturally own the relation?
5. Can the view remain fully derived/no-storage?

If the second consumer requires an unrelated custom ontology, F1 does not justify a generic Coordination surface and the hypothesis must contract.

If the same small relation transfers, only then consider whether a generic **read surface** is justified. Durable artifacts remain a later burden.

## 14. Foundation status

No F1 observation triggers a FoundationReopenCondition.

HDF40–HDF43 are reinforced rather than challenged:

- HDF40: Host product mechanics can survive while primitive Host ontology remains rejected.
- HDF41: FS0 readiness is exactly dependency-relative local compatibility; no global coordination state is needed.
- HDF42: continuity/history plus fresh owner re-observation jointly maintain viability; no universal maintainer exists.
- HDF43: no owner validity/authority is absorbed into the derived view.

F1 is therefore an engineering consumption result, not a new Foundation round.
