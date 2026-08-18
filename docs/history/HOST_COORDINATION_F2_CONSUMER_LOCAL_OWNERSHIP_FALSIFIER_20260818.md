# Host → Coordination F2 — Consumer-local Ownership Falsifier — 2026-08-18

> **Historical engineering experiment, not current architecture authority.** This round continues the frozen-HDF engineering-consumption line after F1 Derived Cross-owner View and the Finance × Workstation transfer falsifier. It does not reopen HDF0–HDF43, rename Host, create a Coordination repository, add a Coordination database, add a scheduler, or mutate Computing/Finance/Workstation owner code.

## 1. Question

F1 proved that an exact case-specific dependency can become satisfiable in owner histories while a stale consumer checkpoint still says it is waiting.

The transfer round then proved two separate facts:

1. the minimal relation grammar transfers beyond FS0;
2. conceptual transfer does not imply shared engineering ownership, because Finance already correctly owns its Workstation-currentness qualification at the consumer boundary.

That exposed a stronger competing model for the original FS0 case.

### Model A — shared Coordination ownership

```text
owner-native evidence
        ↓
generic Coordination relation/read view
        ↓
Computing / FS0 interpretation
```

### Model B — consumer-local ownership

```text
owner-native evidence
        ↓
Computing / FS0 pressure-bound adapter
        ↓
FS0 interpretation
```

F2 asks whether Model A owns any non-bypassable correctness responsibility that Model B cannot preserve more simply.

The burden is not “can a generic envelope be written?” The burden is:

- same exact owner evidence;
- same frozen pressure identities;
- same deletion controls;
- same concrete FS0 next action;
- no storage;
- no authority;
- no hidden scheduler;
- shared layer must add correctness or repeated cross-consumer value, not only another representation.

## 2. Frozen consumer case

Consumer:

`task:computing:fs0-shadow-portfolio-calibration-20260811`

Frozen consumer revision:

`4`

Checkpoint digest:

`sha256:0f7047089139b263cdf8b452bc130bb689e6f876437bc3113a482ce8ce1b4f7a`

FS0 is explicitly shadow-only:

- owner tasks continue independently;
- FS0 does not start/stop/slow/prioritize them;
- predictions are frozen before outcomes;
- no universal tractability scalar exists;
- later owner-native outcomes are interpreted as FS0-owned frontier/cost/prediction-error/partial-order-regret vectors.

Current Computing repository observed during F2:

`/root/projects/ordivon-computing`

The existing FS0 artifact set remains intentionally local:

```text
README.md
FS0-PREDICTION-REPORT.md
cohort-v1.json
baselines-v1.json
apparatus-archive-v1.json
post-freeze-observation-001.json
statistical-calibration-v1.json
evidence/fs0-predictions-v1.json
```

There is no generic dependency graph, shared projection service, or Coordination consumer in this experiment.

## 3. Existing FS0 representation already looks like Model B

`cohort-v1.json` freezes, per candidate:

- candidate id;
- owner;
- exact owner Task id;
- frozen owner Task revision;
- frozen checkpoint digest;
- exact pressure text;
- current boundary;
- owner next actions.

`post-freeze-observation-001.json` then stores, per candidate:

- candidate id;
- frozen Task revision;
- observed Task revision;
- exact observed checkpoint digest when present;
- directional outcome;
- `terminalForFS0`.

The consumer therefore already has a natural local join:

```text
candidate id
    -> frozen pressure identity
    -> owner/task identity
    -> exact observed checkpoint
    -> FS0-local outcome judgment
```

The current app-local artifact is not an accidental workaround. It matches FS0's research semantics.

## 4. Owner outcomes re-observed from exact Host history

F2 did not use current broad Task state as outcome identity. It searched owner checkpoint history *after each frozen revision* for the first checkpoint that actually adjudicated the frozen pressure discriminator.

### 4.1 G-AF3 — Game generalization pressure

Frozen:

- Task: `task:game:station-zero-v3-playtest-validation-20260809`
- frozen revision: `12`
- frozen checkpoint: `sha256:6b4d96286f75b121d262b40b40c56952683478f78979fd6bd088f70f1b90b960`

First qualifying pressure outcome:

- revision: `13`
- checkpoint: `sha256:fc1299e6be77a99d91fbe954109cf158cd0e431f31a1a5ef34e17fb46c22b553`

Exact owner semantics:

- Agent-first Creative Loop 003 complete;
- seed pseudo-variation replaced with four exact content-addressed evaluation Worlds;
- real P2 negative transfer found;
- one-strike/two-strike capacity-memory universality rejected;
- policy-governance Goodhart defect found;
- next frontier moved to AF004.

Consumer outcome kind:

`generalization-falsifier-complete`

Important:

Game's broad Task remains nonterminal and later advances through AF004/GX work. Therefore Task terminality is not the pressure outcome.

### 4.2 R-P5 — Runtime mechanism closeout

Frozen:

- Task: `task:runtime:rsi-p5-foundation-closeout-20260811`
- frozen revision: `4`
- frozen checkpoint: `sha256:9ad2e933ccf92eb9cfea3c52fb0f9bbf1abd9d40978d959305026387e0515082`

First F2 qualifying owner outcome:

- revision: `9`
- checkpoint: `sha256:e283d718daa44b571d63d7347f392251e9f1699d11df50b8d4da67f287dfc529`

Exact owner semantics:

- P5/P-series physically complete;
- provider-release discontinuity closed;
- structured self-release succeeded;
- exact replay produced no duplicate deployment;
- production `windows_native` immutable-input graduation passed.

Consumer outcome kind:

`mechanism-closeout-complete`

### 4.3 H-P6 — Harness utilization pressure

Frozen:

- Task: `task:harness-host:rsi-p6-20260811`
- frozen revision: `4`

First qualifying owner outcome:

- revision: `5`
- checkpoint: `sha256:4f380cf9005dc0ea9553a5b1e7bc2801f01caf9b74cb72fe3251b48468db668b`

Exact owner semantics:

- P6 complete;
- six replay/incremental/batch trajectories executed;
- replay repetition, compact-state causal collapse, and batch false-finalization tradeoffs measured;
- experiment-only apparatus deleted before closeout.

Consumer outcome kind:

`utilization-experiment-complete`

### 4.4 HOST-PKG — Host package-surface pressure

Frozen:

- Task: `task:host:world-core-dependency-surface-20260810`
- frozen revision: `2`

First qualifying owner outcome:

- revision: `3`
- checkpoint: `sha256:a696b0b53ead5713d37421f54552a1b4bc99c08752ccef4ed280321cdcf26346`

Exact owner semantics:

- packaging pressure resolved owner-natively;
- `mcp==2.0.0` moved from Host base dependency to explicit server extra;
- base/server clean-environment comparison proved the split;
- World semantic compatibility was already good;
- GitHub reachability remained a separate distribution-currentness issue.

Consumer outcome kind:

`packaging-pressure-resolved`

### 4.5 F-C2-BLOCKED — Finance negative control

Frozen:

- Task: `task:finance:l0-live-capital-20260810`
- frozen revision: `9`
- frozen pressure: remote/high-assurance effect principal blocked by absence of independently administered Linux/systemd host.

This case is decisive because its outcome is not “Task later completed” and not “independent host eventually appeared.”

The first exact pressure adjudication appears at Finance rev19:

- revision: `19`
- checkpoint: `sha256:ac1dd6331553fbbccb4b1bfe9d1d521e95b0413b0e8904c0b08526fd2e67e7aa`

Owner conclusion:

```text
Remote topology is no longer treated as a universal effect-correctness law.
```

The same checkpoint explicitly rejects:

```text
Remote-independent-principal as a universal prerequisite for effect correctness.
```

Finance rev20 then freezes the distinction more strongly:

```text
remote/independent principal
= optional stronger high-assurance deployment profile
≠ universal prerequisite for effect correctness
```

Later Finance achieved real live effects and reconciliation on the contracted same-machine correctness profile under its own later authority/evidence chain.

The correct FS0 interpretation of the frozen negative control is therefore not:

`completed because the missing host arrived`

but:

`the frozen pressure premise itself was falsified/contracted by owner research`.

Consumer outcome kind:

`pressure-premise-falsified-contracted`

This is highly case-specific research meaning.

## 5. First F2 test failure was itself informative

The first F2 consumer-local matcher incorrectly required a Game phrase that was not exact rev13 owner text.

It therefore produced:

```text
G-AF3 = unresolved
```

while the known exact outcome existed.

The experiment was stopped and the owner rev13 checkpoint was inspected exactly.

The matcher was repaired to the actual pressure discriminator:

```text
Agent-first Creative Loop 003 is complete
+
four exact content-addressed evaluation Worlds
```

No owner product was changed.

This test-fixture failure matters because it demonstrates that “pressure outcome matching” is semantic work. A generic relation envelope does not make that interpretation disappear.

## 6. Model B — Computing-local pressure interpretation

The no-storage Model B experiment did exactly this:

```text
for each frozen FS0 candidate:
    read exact owner Task history after frozen revision
    apply the frozen candidate's FS0-specific discriminator
    find first qualifying checkpoint
    bind exact checkpoint digest
    classify FS0-specific outcome kind
```

It produced:

| Candidate | First qualifying owner rev | Exact checkpoint | FS0-local outcome kind |
|---|---:|---|---|
| H-P6 | 5 | `sha256:4f380cf9...` | utilization-experiment-complete |
| R-P5 | 9 | `sha256:e283d718...` | mechanism-closeout-complete |
| G-AF3 | 13 | `sha256:fc1299e6...` | generalization-falsifier-complete |
| HOST-PKG | 3 | `sha256:a696b0b5...` | packaging-pressure-resolved |
| F-C2-BLOCKED | 19 | `sha256:ac1dd633...` | pressure-premise-falsified-contracted |

All five are now pressure-outcome **available** for FS0 evaluation.

Runtime Job:

`job-01a01315-5963-7ec2-baf1-cdce2bf59920`

stdout digest:

`sha256:aab682d47d3c33108b7958d0703b43b30a8f7b42b8ffdcf72a14511a0be0a3f0`

terminal evidence:

`sha256:beaad0fa6bede51c3f5f53520db8ffaf910d1b95a68662e9315b1a595587bfa5`

## 7. Model A — generic shared relation envelope

The same outcomes were wrapped in the F1 minimal generic envelope:

```text
CaseRef
DependencyRef
OwnerRef
EvidenceRef
Truth/currentness role
available | unresolved
no authority
```

The shared envelope reproduced exactly the same binary availability vector:

```text
H-P6          available
R-P5          available
G-AF3         available
HOST-PKG      available
F-C2-BLOCKED  available
```

Therefore grammar transfer remains real.

But the generic layer could only do this *after* Computing-local logic had already:

- bound the frozen pressure discriminator;
- found the first semantically qualifying owner revision;
- distinguished unrelated Task advancement from exact pressure outcome;
- classified the outcome kind;
- preserved the Finance negative-control meaning.

The shared layer contributed only:

- relation-envelope validation;
- owner/evidence identity carriage;
- truth-role carriage;
- `available|unresolved` carriage;
- no-authority marker.

It did not discover or interpret the outcome.

## 8. Generic baselines still fail

### 8.1 Task terminality

At Game rev13:

```text
exact G-AF3 pressure outcome = available
broad Game Task              = nonterminal
```

Terminality therefore false-negatives the exact pressure outcome.

At Finance rev19:

```text
exact F-C2 pressure premise adjudicated = available
broad Finance Task                       = nonterminal
```

Again, terminality false-negatives the exact pressure outcome.

### 8.2 Revision advance

At Finance rev10:

```text
Task revision advanced 9 -> 10
exact frozen F-C2 pressure still unresolved
```

Revision advancement therefore false-positives.

At Finance rev19:

revision advancement happens to agree with availability, but only because the consumer knows rev19 contains the exact pressure adjudication.

Thus:

```text
TaskRevisionAdvance
!=
PressureOutcomeAvailable
```

### 8.3 Broad Task completion

Finance later reaches rev36 and the first live-capital objective is complete.

That still does not mean the original F-C2 hypothesis “won.” Its specific high-assurance-topology premise had already been contracted/falsified at rev19–20.

Broad Task success can therefore erase exactly the research meaning FS0 needs.

## 9. Shared layer deletion test

F2 then deleted the hypothetical generic Coordination envelope and asked whether FS0 loses any action-relevant capability.

The current local FS0 artifacts already recover every generic field:

| Generic field | Existing FS0-local source |
|---|---|
| CaseRef | FS0 Task/revision is consumer context |
| DependencyRef | frozen cohort candidate id + pressure text |
| OwnerRef | cohort owner + exact taskId |
| EvidenceRef | observed checkpoint digest |
| truthRole | explicit owner-native outcome rule + Host checkpoint role |
| status | FS0-local `terminalForFS0` / pressure evaluation |
| authority=false | shadow-only / no owner-prioritization constraint |

Deletion probe result:

```text
deleteGenericCoordinationEnvelope          = true
FS0PressureOutcomeCorrectnessPreserved     = true
FS0RegretEvaluationPathPreserved           = true
OwnerAuthorityPreserved                    = true
LostCapability                             = none demonstrated
```

Runtime Job:

`job-01a01316-08a2-78e2-ac9b-3e8861b240e0`

stdout digest:

`sha256:5e6e404c1fa0bcce4170fd48e5dd1de49f7f2f6029272cdd47f0da642eb859c4`

terminal evidence:

`sha256:c6648ddfa4d8ad78efe54ec22e9defe3302d39d1d670c7b1f67ba382f9035238`

## 10. Does shared discovery/notification rescue Coordination?

A possible objection is:

> A shared layer might discover owner changes earlier than FS0-local observation and therefore still own useful coordination.

F2 tested that claim against the actual consumer contract.

FS0 is explicitly:

- shadow-only;
- non-authoritative;
- not a scheduler;
- not allowed to start/stop/prioritize owner work;
- not deadline-bound in the frozen experiment;
- only required to re-observe owner-native outcomes before later regret evaluation.

While FS0 is dormant, a stale FS0 checkpoint cannot itself cause an external effect because FS0 owns no automatic action.

Therefore the delay between owner outcome occurrence and later FS0 resumption is not, in this consumer, a reproduced correctness failure.

A notification/watch layer might improve convenience or latency, but F2 found no consumer consequence that currently makes it non-bypassable.

Thus:

```text
notification convenience
!=
shared-owner necessity
```

AttentionRequest / watcher / scheduler remains unearned.

## 11. Computing's own current admission laws agree

Current Computing research contains two directly relevant cross-project laws.

### C9 — Consumer Pressure Determines Layer Admission

A shared/lower layer should change only when reproduced consumer failure proves owner-local treatment insufficient; stop at the first truthful successful treatment.

### C12 — Shared Semantics Do Not Imply Shared Mechanism

The same semantic distinction recurring across owners may justify a shared claim, but does not by itself justify a central implementation/schema/service. A central mechanism is earned only when a truly unowned non-bypassable responsibility recurs and deletion breaks multiple workloads.

F2 matches these laws exactly:

- the semantic relation recurs;
- consumer-local treatment succeeds in Finance;
- consumer-local treatment succeeds in FS0;
- shared-layer deletion breaks neither;
- no unowned action authority remains.

## 12. Stronger engineering conclusion

Before F2, the status was:

```text
Generic relation grammar          real / transferable
Independent Coordination project  weakened, not admitted
```

After F2, the stronger current-evidence conclusion is justified:

```text
Generic relation grammar          REAL
Derived coordination relations    REAL
Coordination as architectural lens USEFUL
Generic shared read surface        NOT ADMITTED
Independent Coordination project  FALSIFIED AT CURRENT ENGINEERING EVIDENCE
```

This does **not** mean coordination is unreal.

It means current evidence does not justify making “Coordination” a new system owner.

The relation can exist without a central owner:

```text
source owner owns relata truth
consumer owns dependency interpretation
Host owns durable continuity only
```

A new independent Coordination project would currently duplicate those responsibilities.

## 13. What would reopen project admission

Independent Coordination project admission may be reopened only by a concrete engineering counterexample, such as:

1. at least two materially different production consumers reproduce the same shared relation responsibility;
2. owner-local / consumer-local adapters both produce measurable duplicated correctness failures or unacceptable repeated semantic drift;
3. deleting the shared layer breaks multiple workloads in the same non-domain-specific way;
4. the surviving responsibility is not source truth, consumer policy, scheduling authority, governance, or domain-specific currentness;
5. the responsibility remains non-bypassable after a pure library/local adapter alternative is tested;
6. a shared layer can own it without becoming a global truth graph, scheduler, authority oracle or mirrored database.

Until such evidence exists:

```text
CoordinationProjectAdmission = closed
```

not merely pending.

## 14. Candidate artifact disposition after F2

| Candidate | F2 disposition |
|---|---|
| Continuity Core | ADMITTED |
| local derived relation | ADMITTED where consumer needs it |
| generic relation grammar | useful conceptual pattern, not product owner |
| generic Coordination read surface | REJECT / not admitted |
| persisted Projection | REJECT |
| generic Dependency / HyperDependency | REJECT |
| ReconciliationRecord | REJECT |
| AttentionRequest | REJECT |
| LivenessState | REJECT |
| Coordination DB | REJECT |
| global coordination graph | REJECT |
| scheduler | REJECT |
| Coordination independent project | FALSIFIED at current evidence |
| Host rename/migration | still not authorized |

## 15. What remains admitted in Host

F2 does not weaken the existing Continuity Core.

Still strongly admitted:

- Journal/CAS;
- exact Task/revision fencing;
- atomic checkpoints;
- external-continuity adopt/checkpoint/resume/list/observe;
- response-loss reconciliation;
- immutable history;
- bounded semantic working claims;
- opaque extension-owner references;
- doctor/integrity/backup/restore/deploy evidence;
- Task continuity without claiming Runtime/domain/provider truth.

This is the current durable Host engineering center.

## 16. Host / Coordination target architecture after F2

The best current architecture is no longer:

```text
Owners
  ↓
Coordination Plane
  ↓
Consumers
  ↓
Host Continuity
```

It is closer to:

```text
Owner A truth ─┐
Owner B truth ─┼─> consumer-local derived relations / adapters
Owner C truth ─┘                 │
                                 │ semantic working continuity only
                                 v
                         Host Continuity Core
```

Or more precisely:

```text
Truth ownership       = source/domain owners
Relation interpretation = named consumer
Persistent semantic continuity = Host
Execution             = Runtime
Agent cognition       = Harness
Transport/capability  = Workstation/Network owners
Authority/governance  = their actual owners
```

No new middle sovereign is required.

## 17. Concrete FS0 action now unlocked

F2 discovered that all five frozen pressures now have a qualifying owner-native outcome.

Therefore FS0 itself can, when resumed by its owner, advance beyond `post-freeze-observation-001.json` and perform its own next intended work:

- record second exact pressure-bound owner observation;
- construct frontier/cost/prediction-error vectors;
- preserve F-C2 as a premise-falsification/contracted-pressure outcome rather than broad Task success;
- compare raw/RFM/simple baselines using partial-order regret;
- admit incomparability where value vectors trade off.

This action belongs Computing/FS0.

F2 does not mutate Computing to perform that unrelated owner task.

## 18. F2 verdict

```text
F1 grammar transfer                       PASS
FS0 consumer-local ownership              PASS
Finance consumer-local ownership          PASS
Shared ownership necessity                FAIL
Generic Coordination read surface         NOT ADMITTED
Coordination independent project          FALSIFIED AT CURRENT ENGINEERING EVIDENCE
Continuity Core                           REMAINS STRONGLY ADMITTED
Foundation reopen                         NO
```

## 19. Next engineering round

The Coordination project hypothesis is sufficiently falsified to stop creating more transfer cases merely for coverage.

The next highest-value engineering work is **Host target-architecture contraction / closeout**, not F3 feature expansion.

That round should:

1. freeze the F2 project-admission rejection and its reopen condition;
2. map current Host source/API/tests/history surfaces against the admitted Continuity Core;
3. classify historical proof/compatibility surfaces as:
   - still-core;
   - compatibility-only;
   - historical research apparatus;
   - zero-consumer residue;
   - externally owned;
4. identify deletion/deprecation candidates without renaming first;
5. preserve World current compatibility and Host operational integrity;
6. only mutate code where a contraction has a clear deletion proof and real consumer safety;
7. decide last, after contraction evidence, whether the repository/product name `Host` remains pragmatically useful or should eventually be narrowed/renamed.

Do not reopen Coordination merely because the word describes a real relational phenomenon.

## 20. Foundation status

No FoundationReopenCondition was triggered.

F2 reinforces the frozen conclusions:

- **HDF40:** Primitive central Host claims should shrink when responsibilities are already owned elsewhere.
- **HDF41:** dependency-relative local compatibility is sufficient; no global coordination state is required.
- **HDF42:** maintained viability may be achieved by owner/consumer-local re-observation and repair; there need not be one universal maintainer.
- **HDF43:** validity/authority remains owner-relative; derived relation convenience cannot become a constitutive oracle.

The result is an engineering contraction, not a new Foundation.
