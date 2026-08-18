# Host C7 — Canonical 0.3.0 Activation & Initial Observation — 2026-08-18

> Post-deployment audit evidence. This document is intentionally created **after** the canonical 0.3.0 production cutover. It is not part of the deployed release bytes and must not be used to redefine that release identity.

## 1. Scope and verdict

C6 ended with an exact 0.3.0 release candidate prepared but not activated. C7 took responsibility for the remaining release-governance and production-cutover questions:

1. make the candidate visible to remote GitHub governance;
2. observe exact CI / CodeQL / release acceptance rather than infer remote status from local tests;
3. repair release-infrastructure defects found by those gates;
4. promote the final exact source identity to canonical `main`;
5. recompute deployment eligibility against default `refs/heads/main`;
6. activate the exact release with the previous 0.2.0 bytes retained as rollback peer;
7. validate the deployed Host itself, not merely the candidate source;
8. run named consumers against the installed release bytes;
9. complete a real post-restart continuity checkpoint + exact replay cycle.

Final verdict:

```text
CANONICAL CUTOVER ACTIVATED
INITIAL OBSERVATION PASS
ROLLBACK PEER RETAINED
```

No FoundationReopenCondition fired. HDF0–HDF43 remain frozen. The independent Coordination project remains falsified/closed at the current evidence frontier.

## 2. Candidate lineage and why the final source identity changed

C7 did not treat C6's initial candidate as sacred. Remote release gates were allowed to falsify it.

### 2.1 C6 candidate — e2a85a3

Initial source candidate:

`e2a85a369df2cc8f963ea6bf337701276eb7a931`

Initial prepared release:

`e2a85a369df2cc8f963ea6bf337701276eb7a931-25bd190a3815`

C7 pushed this exact SHA to:

`release/host-0.3.0-candidate-20260818`

and manually dispatched four workflows.

Exact remote results:

```text
CI                  success
CodeQL              success
Release acceptance  failure
System acceptance   queued
```

Release acceptance run:

`32103919123`

The failure was not a product defect. The workflow installed:

```text
pip install -e .
```

but then ran the full suite including `test_mcp_server`, whose production import requires the optional MCP extra. GitHub therefore failed with:

```text
ModuleNotFoundError: No module named 'mcp'
```

The same exact source SHA passed normal CI because CI correctly installed `.[mcp]`.

C7 therefore rejected e2a85a3 as activation source identity even though product tests were locally green. A broken release gate is itself a release defect.

Failure diagnosis Runtime Job:

`job-01a01365-d88c-7723-bcf9-7cd2a04fee20`

The failure diagnosis is retained by exact Runtime Job identity; C7 does not restate a terminal-evidence digest here without re-reading that Job's retained artifact.

### 2.2 Release-acceptance fix — 458c693

C7 changed release acceptance to install:

```text
python -m pip install --disable-pip-version-check -e '.[mcp]'
```

New commit:

`458c693caf3dfb8b1813ddced4f3eef73ca2c70a`

Subject:

`ci(host): install MCP extra in release acceptance`

Exact remote results for 458c693:

```text
CI                  success
CodeQL              success
Release acceptance  success
```

Remote runs:

```text
CI                  32104351929
CodeQL              32104355229
Release acceptance  32104358284
```

This proved the workflow correction was sufficient.

### 2.3 Dead self-hosted system workflow discovered

GitHub repository runner census then returned:

```text
total_count = 0
runners = []
```

Yet `system-acceptance.yml` required:

```text
[self-hosted, linux, x64, ordivon-host-runtime]
```

So that workflow was not merely busy. It had no possible runner and could never produce evidence.

This mattered because current `docs/RELEASES.md` already assigns local systemd/deployment evidence to:

```text
operator-run local acceptance
+ receipt-bound deployment prepare/plan/apply
```

Hosted GitHub cannot prove local systemd deployment truth.

C7 therefore removed:

- `.github/workflows/system-acceptance.yml`;
- `.github/actionlint.yaml`, whose special label configuration existed only for that nonexistent runner;

and recorded the removal in the Changelog.

The queued obsolete system runs were canceled rather than treated as missing evidence.

## 3. Final canonical source identity

Final source commit:

`2979ceac8d596cba7ac8113302a8b46c2a51a737`

Subject:

`ci(host): remove dead self-hosted acceptance workflow`

Relative to 458c693, this commit changes only:

```text
D .github/actionlint.yaml
D .github/workflows/system-acceptance.yml
M CHANGELOG.md
```

No `src/`, package lock, deployment implementation, scripts, or packaging bytes changed between 458c693 and 2979cea.

The canonical release source nevertheless changed because release identity binds the exact Git commit, not only product module bytes.

## 4. Final exact local acceptance — 2979cea

The final clean source `2979cea...` passed:

```text
documentation contract    PASS
dependency contract       PASS
Ruff                      PASS
compile                   PASS
Host tests                157 / 157 PASS
isolated Host Doctor      PASS
isolated history Doctor   PASS
runtimeContacted          false
World                     158 / 158 PASS
Security                  22 / 22 PASS
source gitleaks           no leaks found
```

The local acceptance receipt bound:

```text
hostRevision = 2979ceac8d596cba7ac8113302a8b46c2a51a737
packageVersion = 0.3.0
runtimeContacted = false
```

Runtime Job:

`job-01a0136c-b542-7373-8902-08862b8d6d9f`

Terminal evidence:

`sha256:fdd2f6120bd2f3627d3c44fb531f3e70afbb50adc66231f9391a5bd61f206f9d`

### 4.1 Secret-scan scope correction retained as audit evidence

An earlier 458c693 combined validation created a temporary production-state backup inside the audit workspace and then ran gitleaks before cleanup. That scan reported 20 patterns because it scanned retained production data rather than source.

The temp state was automatically removed on process exit. A clean source-only scan then examined approximately 2.74 MB and reported:

```text
no leaks found
```

C7 therefore distinguishes:

```text
retained production data scan != source secret scan
```

and does not count the scope mistake as a source leak.

## 5. Final production-state compatibility proof before activation

Against final 2979cea candidate, production state was again tested through the safe pattern:

```text
currently deployed 0.2.0 binary
  -> formal production backup
  -> candidate verify
  -> isolated candidate restore
  -> candidate Doctor --history
```

Final preactivation snapshot evidence:

```text
backup schema     5
Doctor healthy    true
journal.history   ok
inspect schema    5
migration table   to 5
Events            2285
Tasks             478
object refs       4551
```

No candidate code migrated or rewrote production authority during this proof.

## 6. Final prepared release — 2979cea

Materialized release:

`2979ceac8d596cba7ac8113302a8b46c2a51a737-6f772f6557ce`

Candidate directory:

`/var/lib/ordivon/host/candidates/2979ceac8d596cba7ac8113302a8b46c2a51a737-6f772f6557ce`

Digests:

```text
effectiveDigest
sha256:6f772f6557ce9c9345917398d3bf2559ff050159f58b30fd3a24b20d65f8ae3a

wheelDigest
sha256:600e87c9da894757816e914d3865a4a5cf989265d2d7aa4fab7d958487dc0272

lockDigest
sha256:67d7c10dbcfd4969b1f29ccb891c60b1a9a25993de3c3720adb8a7a717377d81
```

Initial final-candidate plan before main promotion:

```text
eligible          true
blockers          []
candidate schema  5
live schema       5
migration         false
rollback          true
```

Plan digest at this point:

`sha256:1dd454ca2f004cf490440100c0f85cf7d9f32627fd35ed81e4b4767ac319499b`

## 7. Final release-branch remote gates

The release-candidate branch was fast-forwarded to exact 2979cea and the surviving canonical hosted gates were manually dispatched.

Results:

```text
CI                  32104649594  success
CodeQL              32104652123  success
Release acceptance  32104660139  success
```

Watcher Runtime Job:

`job-01a0136f-2096-7341-807d-a554603f0b6c`

Terminal evidence:

`sha256:f629ba13416dffb26f0c84cbb1c00e3913e9e0fba41dd10e30b326681cb29f2d`

No remote status was inferred; all three exact runs reached terminal `success` for SHA `2979cea...`.

## 8. Canonical main promotion

After release-branch gates passed, `/root/projects/ordivon-host` main was promoted with:

```text
git merge --ff-only 2979ceac8d596cba7ac8113302a8b46c2a51a737
git push origin main
```

No merge commit and no force push were used.

After promotion:

```text
local main   2979ceac8d596cba7ac8113302a8b46c2a51a737
remote main  2979ceac8d596cba7ac8113302a8b46c2a51a737
```

Promotion Runtime Job:

`job-01a01370-157d-78f1-a96b-941c6feaec9d`

Terminal evidence:

`sha256:dbedfb912b71fe54fda0db8c7f25486f334c9ef3933cdf8380e7264edfd8a09d`

## 9. Canonical-main remote gates

Main push automatically triggered CI and CodeQL. Release acceptance was explicitly dispatched against `main` because its workflow has no push trigger.

Exact results:

```text
CI                  32104758985  success
CodeQL              32104759017  success
Release acceptance  32104766604  success
```

All three bound:

`2979ceac8d596cba7ac8113302a8b46c2a51a737`

Watcher Runtime Job:

`job-01a01370-9859-7940-bdce-85b6e78880f8`

Terminal evidence:

`sha256:34a757dfb6648077a6dd12add82a2d8599eddf7df6954e8666c6c3e3c8c3cff5`

Thus the source-governance chain before activation was:

```text
final local source
= release candidate branch
= canonical main
= hosted CI / CodeQL / release acceptance source
= deployment candidate commit
```

## 10. Final default-main deployment plan

After canonical main promotion, C7 recomputed deployment plan using the **default** required ref rather than the temporary release-candidate ref.

Result:

```text
requiredRef        refs/heads/main
requiredRefCommit  2979ceac8d596cba7ac8113302a8b46c2a51a737
eligible           true
blockers           []

candidate schema   5
live schema        5
previous schema    5
migrationRequired  false
rollbackPolicy     release-bytes-only
explicitRollback  true
```

Candidate identities remained exact:

```text
effective  sha256:6f772f6557ce9c9345917398d3bf2559ff050159f58b30fd3a24b20d65f8ae3a
wheel      sha256:600e87c9da894757816e914d3865a4a5cf989265d2d7aa4fab7d958487dc0272
lock       sha256:67d7c10dbcfd4969b1f29ccb891c60b1a9a25993de3c3720adb8a7a717377d81
```

Canonical pre-apply plan digest:

`sha256:2588f3650b716de57324526ff1536fa4f16c0a5689541228e937d1e101341917`

Runtime Job:

`job-01a01371-8de4-7302-acba-e3b1e5f7a89e`

Terminal evidence:

`sha256:de554a706d5c7ac9c7858a23654bd33f72c5cd58f657e26d8bedd13697e696e1`

## 11. Activation contract

Before apply, C7 inspected the exact deployment implementation.

For this no-migration cutover, `apply`:

1. takes the deployment lock;
2. recomputes the plan;
3. requires exact `confirm-release-id` equality;
4. copies candidate release bytes into the release root;
5. atomically switches `current`;
6. restarts `ordivon-host-mcp.service`;
7. waits for active state;
8. runs an authenticated MCP probe;
9. verifies current release content and Python runtime against the candidate;
10. verifies the live Journal schema equals candidate schema;
11. writes a deployment receipt.

If an exception occurs after switching, the same apply implementation restores the exact previous release bytes, restarts the service, re-probes it, and validates previous schema.

This gave C7 an exact automatic rollback path before activation rather than a human promise to recover manually.

## 12. Canonical production activation

C7 then executed apply for:

```text
commit
2979ceac8d596cba7ac8113302a8b46c2a51a737

releaseId
2979ceac8d596cba7ac8113302a8b46c2a51a737-6f772f6557ce
```

Apply result:

```text
status                     deployed
finalJournalSchemaVersion  5
migrationRequired          false
```

The authenticated MCP probe returned:

```text
server name    ordivon-host-mcp
title          Ordivon Host
version        0.3.0
protocol       2026-07-28
Tool count     6
```

Tools:

```text
host.status
task.adopt
task.checkpoint
task.list
task.observe
task.resume
```

Server interface schema digest remained:

`sha256:382abe793d2e41470d91f1efc00d76152ff1fdbae3cac53adcad511d8211780c`

Activation Runtime Job:

`job-01a01371-f831-7423-a7c8-6dbc94175c21`

Terminal evidence:

`sha256:b5c006185bda62ea932de628586f2c06f27b92295d9092e9139ca0d085382017`

Deployment receipt:

`/var/lib/ordivon/host/deployments/20260818T055712Z-2979ceac8d596cba7ac811330-775110418`

Installed release path:

`/usr/local/libexec/ordivon/host/releases/2979ceac8d596cba7ac8113302a8b46c2a51a737-6f772f6557ce`

## 13. Rollback peer retained

Previous release:

`8d7e58a0511734a454805e29d10e7d3bb754d2da-d9c7ebcc5c31`

remains physically present under the release root after activation.

C7 does not run lifecycle GC and does not retire this release.

The activation receipt identifies the previous release bytes and confirms:

```text
previous schema               5
activation rollback policy    release-bytes-only
explicit rollback supported   true
```

The observation window therefore retains a concrete reversible peer.

## 14. Post-activation Host-owned status proof

After service restart, C7 called the deployed `host.status(detail=history)` Tool.

It returned:

```text
deployment releaseId
2979ceac8d596cba7ac8113302a8b46c2a51a737-6f772f6557ce

deployed revision
2979ceac8d596cba7ac8113302a8b46c2a51a737

Journal schema
5

Tool count
6

runtimeProxy
false

Doctor healthy
true

journal.history
ok
```

At that observation the Host authority projection contained:

```text
Events       2302
Tasks        481
object refs  4585
leases       0
```

Full-history Doctor reported 2302 Events validated with all local invariants green.

This is evidence from the newly deployed Host itself, not from the audit workspace.

## 15. Continuity survived service restart

The long-running engineering-consumption Task:

`task:host-foundations-engineering-consumption-20260818`

had revision 11 before activation.

After the new service was deployed and restarted, `task.resume(expectedRevision=11)` succeeded and returned the exact existing checkpoint/handoff.

Therefore the central semantic-continuity object survived the Major-class code contraction and process restart.

## 16. Deployment receipt/current health proof

Post-activation deployment status reports:

```text
status                         healthy
contentMatchesReceipt          true
pythonRuntimeMatchesReceipt    true
authoritySchemaMatchesReceipt  true
observed schema                5
expected schema                5
explicitRollbackSupported      true
```

Current release resolves to:

`/usr/local/libexec/ordivon/host/releases/2979ceac8d596cba7ac8113302a8b46c2a51a737-6f772f6557ce`

Installed package metadata reports:

```text
ordivon-host 0.3.0
```

Canonical source still matches deployment source:

```text
local main   2979ceac8d596cba7ac8113302a8b46c2a51a737
remote main  2979ceac8d596cba7ac8113302a8b46c2a51a737
```

The previous 0.2.0 release directory and new deployment receipt both remain physically present.

## 17. Named consumers against **installed production bytes**

C7 reran the real consumers with `PYTHONPATH` pointed at:

`/usr/local/libexec/ordivon/host/current/venv/lib/python3.12/site-packages`

rather than at the audit source tree.

Results:

```text
World     158 / 158 PASS
Security   22 / 22 PASS
```

Runtime Job:

`job-01a01373-aea0-7ad0-8251-7acb4ffd662c`

Terminal evidence:

`sha256:5ff65bab1d9a650c299e8a68cabf8d6595a2a347a897a8710bde6b2cc07ea6d5`

This is the strongest consumer proof in the sequence because it binds to the bytes actually selected by production `current`.

An earlier post-activation shell attempt failed before any consumer test because `ordivon-host-deploy status` does not accept `--pretty`; this was an audit-command argument error and was replaced by the successful exact-contract run above.

## 18. One complete post-restart continuity-use cycle

C7 uses a real continuity cycle rather than pretending an elapsed wall-clock window alone proves semantic behavior.

Sequence:

```text
rev11 existing checkpoint
  -> resume after service restart
  -> checkpoint expectedRevision=11
  -> Host creates rev12
  -> exact same checkpoint replay with expectedRevision=11
  -> admission=existing
  -> revision remains 12
```

Created rev12 checkpoint digest:

`sha256:f38f38573d6a0f9ae55f113a0fcf25c4f52808e4f37113030b20fedfe09183be`

Checkpoint object:

`sha256:ba6e451fd3b74640413bb00c3078e415e96f40a7fea04baec26ba8f2dfbf5894`

The exact replay produced:

```text
admission = existing
revision  = 12
```

No duplicate continuity history was added.

This directly validates the post-cutover response-loss/idempotency contract:

```text
same expectedRevision + same semantic checkpoint
    -> converge on existing transition
```

Thus C7 satisfies the initial observation criterion using one complete normal continuity-use cycle. C7 does **not** claim that a 30-minute timed observation window elapsed.

## 19. What C7 does not do

C7 deliberately does not:

- reopen HDF0–HDF43;
- reopen the independent Coordination project;
- run another major ownership/deletion search;
- rename Host;
- remove the small conservative `task assess` / recovery projection;
- garbage-collect the previous 0.2.0 release;
- claim that dead self-hosted GitHub infrastructure produced evidence;
- move canonical main beyond the deployed `2979cea...` merely to include this post-deployment report.

## 20. Final architecture now canonical in production

The deployed 0.3.0 Host embodies:

```text
Host
= durable semantic continuity
+ Host Journal/CAS authority
+ exact Task revision / lease admission
+ WorkingCheckpoint adopt/checkpoint/resume
+ bounded handoff / inspection
+ owner-opaque extension durability where real consumers exist
+ bounded context-selection semantics with Security consumer
+ local Doctor / backup / restore / deployment integrity
```

It no longer generically owns:

```text
shared Goal coordination
foreign execution coordination
Runtime workload execution
read / mutation / code-change engines
cognition execution / proposal orchestration
automatic cross-owner reconciliation
generic capability authorization
Runtime client / config / health proxy
```

The key engineering result is not merely fewer lines of code. The current deployed system now corresponds much more closely to the HDF40–HDF43 ownership boundary.

## 21. C7 closeout

```text
final source / canonical main
2979ceac8d596cba7ac8113302a8b46c2a51a737

production release
2979ceac8d596cba7ac8113302a8b46c2a51a737-6f772f6557ce

Host version
0.3.0

Journal schema
5

remote canonical gates
CI                  PASS
CodeQL              PASS
Release acceptance  PASS

postactivation Host history
PASS

installed-byte consumers
World     158/158 PASS
Security   22/22 PASS

continuity cycle
resume               PASS
checkpoint rev11→12  PASS
exact replay         existing / PASS

rollback peer
8d7e58a...-d9c7ebcc5c31 RETAINED
```

Therefore:

```text
C7 = CANONICAL CUTOVER ACTIVATED / INITIAL OBSERVATION PASS
```

The next engineering phase should be post-canonicalization work: identity/naming, the small local `task assess` ergonomics question, and eventually lifecycle cleanup after sufficient confidence. Those should not be allowed to retroactively expand Host ownership without new evidence.
