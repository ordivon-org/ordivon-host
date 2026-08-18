# Host C8 — Post-canonicalization Identity / Naming Audit — 2026-08-18

> Historical engineering evidence. C8 begins after the C1–C7 Host contraction and canonical 0.3.0 cutover. It does not reopen HDF0–HDF43 and does not reopen generic Coordination.

## 1. Question

After Primitive Host ontology was falsified and the surviving engineering responsibility was contracted to durable semantic Task continuity plus Host-owned Journal/CAS authority, should the project still be called **Ordivon Host**?

C8 also asks a prerequisite question: do the canonical documents actually describe the already-deployed contracted product, or does the old ontology survive in prose even after its implementation was removed?

## 2. Starting production state

C8 starts from the completed C7 production state:

```text
canonical source
2979ceac8d596cba7ac8113302a8b46c2a51a737

production releaseId
2979ceac8d596cba7ac8113302a8b46c2a51a737-6f772f6557ce

package
ordivon-host 0.3.0

Journal schema
5

MCP
6 Tools
runtimeProxy=false
```

C1–C5 removals are already canonical production reality. C8 does not use naming as a reason to reintroduce any removed responsibility.

## 3. Naming surface census

The string `Host` is not merely a README title. Current stable identity spans:

```text
repository             ordivon-host
Python package          ordivon_host
CLI                     ordivon-host
MCP CLI                 ordivon-host-mcp
MCP server name         ordivon-host-mcp
MCP title               Ordivon Host
systemd unit            ordivon-host-mcp.service
state root              /var/lib/ordivon/host
release root            /usr/local/libexec/ordivon/host
```

Current direct/indirect external references also exist in:

- Computing consumer manifests and conformance scripts;
- World direct Python imports and Host dependency declarations;
- Security dynamic `ordivon_host` imports and component identity;
- Runtime watchdog/service references and public ownership documentation;
- Game and Harness documentation/history;
- Web legacy redirects.

Runtime Job:

`job-01a01378-9912-7ed2-a1e9-ba7e85a32e67`

Terminal evidence:

`sha256:3f49fcf44eb8d2547f433aded3a6e9e9c0e17bb930936aedd21dda55792e382d`

Therefore a rename is a cross-repository public-compatibility migration, not a cosmetic edit.

## 4. Candidate name comparison

C8 compares the current name with the strongest obvious replacements.

### Model A — keep `Host`

Advantages:

- stable public/product identity already consumed by multiple projects and operational tooling;
- semantically compatible with “the component that hosts durable continuity” once the term is explicitly narrowed;
- does not require a new ontology or new owner;
- can preserve the already-proven C1–C7 product boundary without migration.

Risk:

- readers may incorrectly import the historical meaning “central/global Host” unless current docs explicitly reject it.

### Model B — `Continuity`

This captures the strongest surviving purpose but is too narrow as a project identity. Current Host also owns:

- Journal/CAS admission and integrity;
- Task revision and lease fencing;
- opaque extension durability;
- bounded Host inspection/handoff;
- backup/restore/Doctor/deployment authority;
- several real compatibility value surfaces.

A pure `Continuity` name would hide these responsibilities rather than clarify them.

### Model C — `Ledger`

`Ledger` over-centers append-only history. Current Host also has:

- materialized Task projection;
- short leases;
- mutable current WorkingCheckpoint progression through revisioned Events;
- extension namespace pointers;
- operational inspection and deployment.

The name would be mechanically suggestive but architecturally incomplete.

### Model D — `Authority`

`Authority` captures Host ownership over its own Journal/CAS, but it is dangerously broad. HDF43 established owner/order-relative validity rather than a universal authority oracle. Naming the whole project `Authority` would invite exactly the cross-owner overclaim the Foundations work rejected.

### Model E — `Coordination` / `Coordinator`

Rejected most strongly. Generic shared Coordination ownership was directly falsified by F1/F2 and transfer falsifiers. Consumer-local or source-owner relation ownership remains the default.

## 5. Naming verdict

C8 selects **Model A: retain Ordivon Host**.

The decisive reason is not migration cost alone. The alternatives are semantically inferior:

```text
Continuity   omits real authority/integrity responsibilities
Ledger       overfits storage history
Authority    overclaims cross-owner authority
Coordination contradicts the falsification result
```

The correct move is therefore to narrow the meaning of the existing proper noun.

Normative identity law:

```text
Host product name
!= primitive universal Host ontology
!= central coordinator
!= global truth owner
!= Runtime proxy
!= generic executor
!= governance/validity oracle
```

Current positive meaning:

```text
Host
= durable semantic Task continuity
+ Host-owned Journal/CAS admission
+ exact Task revision/lease fencing
+ bounded handoff/inspection
+ opaque extension durability
+ local operational integrity
```

A product name may survive the falsification of an earlier ontology if its current contract is explicitly narrower.

Key rule:

> Compatibility is not ontology.

## 6. Canonical-document falsifier

The naming audit exposed a more urgent defect: canonical 0.3.0 prose still described responsibilities deleted by C1–C5.

Examples in the deployed-source README/ARCHITECTURE included:

- “Goal-scoped coordination over Task revisions”;
- cognition requests and participant-routed `DecisionRequest`;
- `CognitionWorkRequest`;
- proposal compilation/lowering;
- `DeterministicReadHost`;
- a “Deterministic Runtime read slice”;
- a “Guarded mutation and uncertain delivery” implementation slice;
- “Runtime clients” as current Host components;
- README `Runtime transport` claiming a canonical Host Runtime client;
- Quick Start claiming live Task progress requires Runtime.

Yet current product source contains none of the historical execution classes those passages described.

Source-existence falsifier Runtime Job:

`job-01a0137a-0850-7f21-a003-58d174b7c569`

Terminal evidence:

`sha256:11935c05e7a83f4636dc7babbd0cf23c5102efd6b1b697b4c5b743dd72c22f61`

The same ARCHITECTURE document later correctly stated that Host does **not** own Runtime clients, source-read/mutation/code-change engines, foreign executors, or automatic cross-owner reconciliation. The canonical document was therefore internally contradictory.

## 7. Regression origin

C8 compared the C5/C6/C7 source history.

The stale identity prose was **not** reintroduced by C7. The final C7 candidate changes between `e2a85a3...` and `2979...` touched CI workflows only.

The problem was incomplete canonical-doc contraction from the earlier engineering-consumption rounds: later boundary sections were corrected while older architecture sections survived above them.

Runtime Job:

`job-01a0137a-5c50-7060-8272-768e7d45026e`

Terminal evidence:

`sha256:5e6981f75056edc020dae4a7413841769dd389e0c9c09cb684649c4cf565bf62`

This matters because C8 treats the issue as a documentation-contract defect, not as evidence that 0.3.0 implementation secretly retained the deleted ontology.

## 8. Managed-document census

`.ordivon/project.yaml` marks these as canonical managed documents:

```text
README.md
ARCHITECTURE.md
docs/QUICKSTART.md
docs/STATUS.md
docs/OPERATIONS.md
docs/DATA_AND_PRIVACY.md
docs/RELEASES.md
docs/authority.md
```

The stale ownership claims were concentrated in README, ARCHITECTURE and QUICKSTART. STATUS and the later architecture boundary sections already reflected the contracted model.

Runtime Job:

`job-01a0137b-2ebc-7451-9fbc-88bb116df84b`

Terminal evidence:

`sha256:51f38fe8ef117f1843cc35cbf319c5abc2639f0b31231b933bb482d36ce3ec56`

## 9. C8 canonical identity contract

C8 rewrote README, ARCHITECTURE and QUICKSTART around the actually deployed responsibility.

The new README explicitly states:

> Host is a product name, not a claim that Ordivon has one universal Host ontology or central coordinator.

The architecture now defines the positive and negative identity boundaries directly and treats surviving compatibility value types as values rather than lifecycle ownership.

Important separations include:

```text
ArtifactRef          != Host Artifact authority
DispatchEnvelope     != Host foreign-executor coordinator
ObservationEnvelope  != Host external-currentness authority
VerificationReceipt  != universal verification sufficiency
TaskOutcome          != domain-completion oracle
```

The surviving `ordivon_host.cognition` surface is documented as context-selection compatibility consumed by Security, not cognition execution ownership.

`assess_recovery` is documented as small conservative read-only operator projection, not automatic reconciliation.

Runtime is explicitly not a Host installation prerequisite.

## 10. Machine-readable documentation guard

C8 changes `scripts/check_docs.py` so the semantic contraction cannot silently regress again.

It removes the old requirement that README contain a `Runtime transport` heading and requires identity/revalidation headings instead.

Canonical README/ARCHITECTURE/QUICKSTART now fail validation if they reintroduce current claims containing historical markers such as:

```text
Goal-scoped coordination over Task revisions
CognitionWorkRequest
DecisionRequest
ActionProposal
DeterministicReadHost
Runtime clients
Host may call Runtime for workloads
live Task progress requires Runtime
Deterministic Runtime read slice
Semantic cognition request and admission
Guarded mutation and uncertain delivery
```

The checker also requires contraction identity markers including:

```text
Host is a product name, not a claim that Ordivon has one universal Host ontology
Compatibility is not ontology
There is no product Runtime client
```

After rewriting the canonical docs, the stale-claim scan returned zero matches.

Patch creation Runtime Job:

`job-01a0137c-b80b-7591-9fa0-c799d8dd8f43`

Terminal evidence:

`sha256:6a85cfa07e6def1aefd5ae6c7b176fc8902bc3ba2c9f38b22a2e87afa0d1645e`

## 11. No implementation change

Before assigning a release version, C8 ran the complete Host validation suite.

Results:

```text
documentation contract PASS
dependency contract    PASS
Ruff                   PASS
compileall             PASS
Host tests             157 / 157 PASS
src/ordivon_host diff  none
tests diff             none
```

Runtime Job:

`job-01a0137c-f172-76f3-a09f-a0b2e5d39f75`

Terminal evidence:

`sha256:36df1b303f1f52efc2e92ce44ee1490a3ef7770cc0006aee537fb4094dc83af7`

Therefore C8 is an identity/documentation contract correction, not a second architecture contraction.

## 12. 0.3.1 patch release

To preserve the established rule that canonical source and deployed source identity remain aligned, C8 does not leave corrected main docs ahead of production indefinitely.

The identity correction is packaged as:

```text
ordivon-host 0.3.1
```

Candidate source commit:

`70f22431ba43b81294b8395ed1b8d23ec83fde61`

Subject:

`docs(host): canonicalize 0.3 identity boundary`

The release changes docs, the documentation contract checker, Changelog/version metadata and lock metadata only. No product Python implementation, MCP schema, Journal schema, CLI, config, or state format changes.

Commit Runtime Job:

`job-01a0137d-fe5b-7ce1-a92e-c4440611fe5e`

Terminal evidence:

`sha256:25227c8d0170b57be4ee81dc10c858ecb1166f55d24a9c80fe6b28886e120ca9`

## 13. Exact 0.3.1 local and consumer acceptance

Against clean exact commit `70f22431...`:

```text
Host                 157 / 157 PASS
World                 158 / 158 PASS
Security               22 / 22 PASS
isolated Host Doctor            PASS
isolated history Doctor         PASS
runtimeContacted                false
pip-audit                       no known vulnerabilities
gitleaks                        no leaks found
```

Runtime Job:

`job-01a0137e-33f1-7373-a1b7-7705ff847cbe`

Terminal evidence:

`sha256:956016469c6b5204c6dc259695d5ded84c466bc4b066ee605fd0fb3a24ecc899`

## 14. Release-branch remote governance

Exact source was pushed to:

```text
release/host-0.3.1-identity-20260818
→ 70f22431ba43b81294b8395ed1b8d23ec83fde61
```

Release-branch remote runs, all exact same head SHA:

```text
CI                  32105962365  SUCCESS
CodeQL              32105966038  SUCCESS
Release acceptance  32105970146  SUCCESS
```

Watcher Runtime Job:

`job-01a01380-9b9a-7c60-9ef1-a0b84bcd2407`

Terminal evidence:

`sha256:c4e83494f622f48b87f72fc49c7bf63870267e61b64ee7b00b00a9e1d54071a3`

## 15. Canonical source promotion

Remote main was still exactly C7 source:

`2979ceac8d596cba7ac8113302a8b46c2a51a737`

C8 used that exact value as a force-with-lease guard and fast-forwarded main to:

`70f22431ba43b81294b8395ed1b8d23ec83fde61`

Main then ran its own gates:

```text
CI push             32106091883  SUCCESS
CodeQL push         32106091874  SUCCESS
Release acceptance  32106111938  SUCCESS
```

All bind head SHA `70f22431...`.

Main-gate watcher Runtime Job:

`job-01a01382-6136-7883-83a1-0dc51fa15e48`

Terminal evidence:

`sha256:5b8d3b247e7b44964ad2f355e063a3b74a2bd5fd4a96f4c305d9e073f14468d9`

The local canonical repository was then fast-forwarded normally to the same remote main. An initial attempt to use `git branch -f main` was correctly rejected because main was checked out in `/root/projects/ordivon-host`; C8 changed no source in that failed attempt and then used `merge --ff-only origin/main`.

## 16. 0.3.1 candidate preparation and deployment plan

Prepared release:

```text
releaseId
70f22431ba43b81294b8395ed1b8d23ec83fde61-13325be2e39d

effectiveDigest
sha256:13325be2e39d45e0a24f71487d0b06e73f2eaef7e1c6b25069daa4f9f5993348

wheelDigest
sha256:9d020c495a9bb9fbff4d4eaa6a38ff8d8ea425ac8c7e23a555d271bdf15529e9

lockDigest
sha256:e18cbf9e020b4a4e9b62e5d9ba91734f36ef69b5da8658accf7ba0b0397d7ddd
```

Plan against default `refs/heads/main`:

```text
eligible                             true
blockers                             []
requiredRefCommit                    70f22431...
candidateSchemaVersion               5
liveSchemaVersion                    5
previousReleaseSchemaVersion         5
migrationRequired                    false
explicitRollbackSupportedAfterSuccess true
activationRollbackPolicy             release-bytes-only
```

Previous production rollback peer at plan time:

`2979ceac8d596cba7ac8113302a8b46c2a51a737-6f772f6557ce`

Runtime Job:

`job-01a01384-d86f-7481-8f98-eea4eb9b265d`

Terminal evidence:

`sha256:45fb9aee1bc91a8b034200b3273fb25ee1d44b6cece308567937f390d678fbf2`

## 17. Production activation

Receipt-bound deployment apply succeeded.

Production release:

```text
releaseId
70f22431ba43b81294b8395ed1b8d23ec83fde61-13325be2e39d

package version
0.3.1

Journal schema
5
```

Authenticated MCP activation probe returned the same six-Tool schema and `Ordivon Host` title/version 0.3.1.

Activation receipt:

`/var/lib/ordivon/host/deployments/20260818T061805Z-70f22431ba43b81294b8395ed-701809292`

Apply Runtime Job:

`job-01a01385-16d4-75b2-8493-d1a322f7d581`

Terminal evidence:

`sha256:f0294ecec4ce44165f6c836fa3aeeecd7b8be28f11468a17b7aa2f718237dbd1`

No durable migration occurred.

## 18. Post-activation Host truth

`host.status(detail=history)` after restart reports:

```text
deployedRevision   70f22431ba43b81294b8395ed1b8d23ec83fde61
releaseId          70f22431...-13325be2e39d
Journal schema      5
MCP Tools           6
runtimeProxy        false
Doctor healthy      true
journal.history     ok
leases              0
```

At observation there were 2344 retained Events, 489 Tasks and 4669 validated object refs; full-history validation was healthy.

This is owner-native Host evidence, not Runtime-proxied health.

## 19. Installed-byte consumer acceptance

Deployment status after activation reports:

```text
status                        healthy
contentMatchesReceipt         true
pythonRuntimeMatchesReceipt   true
authoritySchemaMatchesReceipt true
explicitRollbackSupported     true
```

Installed package resolves to the current release tree and reports version 0.3.1.

Against the installed `/usr/local/libexec/ordivon/host/current` package bytes:

```text
World      158 / 158 PASS
Security    22 / 22 PASS
```

Runtime Job:

`job-01a01385-ba73-7ad3-be90-a515ff9b2ea1`

Terminal evidence:

`sha256:da10faadd058fc2ace978b101dd51b6a2cf638f1e59ee9df4c1478b9f837b087`

An earlier combined post-deploy command failed before validation because `status` does not accept `--pretty`; it made no product claim and was replaced by the successful exact command above.

## 20. Rollback retention

C8 performs no lifecycle GC.

Both earlier exact releases remain physically retained:

```text
0.3.0
2979ceac8d596cba7ac8113302a8b46c2a51a737-6f772f6557ce

0.2.0
8d7e58a0511734a454805e29d10e7d3bb754d2da-d9c7ebcc5c31
```

This preserves a conservative rollback window even though 0.3.1 changes no product code or Host schema.

## 21. Continuity across the 0.3.1 restart

The long-running engineering Task:

`task:host-foundations-engineering-consumption-20260818`

resumed successfully at exact revision 13 after the 0.3.1 service restart. Its C7 semantic checkpoint and handoff remained intact.

Therefore the identity/documentation patch did not disturb the very continuity responsibility whose meaning it clarifies.

## 22. Final C8 verdict

```text
Rename Ordivon Host                     REJECTED
Keep Host as stable product proper noun ADMITTED
Primitive/universal Host ontology       STILL FALSIFIED
Generic Coordination project            STILL CLOSED
Canonical identity prose                RECONSTRUCTED
Machine stale-ontology guard             ADMITTED
0.3.1 patch                              CANONICAL + DEPLOYED
Product implementation change            NONE
Journal migration                        NONE
```

The semantic interpretation of the name is now explicit:

> Ordivon Host is the component that hosts durable semantic Task continuity and owns the Journal/CAS authority required for that continuity. The word “Host” does not grant it universal coordination, execution, truth, governance, or validity ownership.

This resolves the post-contraction naming question without using naming as an excuse to expand the project again.

## 23. Next engineering frontier

With identity settled, the next bounded question is no longer naming.

The remaining explicitly open Host-local ergonomics item is the small read-only `task assess` / recovery projection (~73 LOC). It should be judged by practical operator value, not by ontology minimalism:

```text
retain if it materially improves safe operator continuation
contract/remove if direct Task inspection fully substitutes it
```

Lifecycle cleanup of old candidate/release material remains separate and should preserve rollback bytes until explicitly released.
