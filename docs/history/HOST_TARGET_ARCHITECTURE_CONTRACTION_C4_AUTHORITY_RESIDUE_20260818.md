# Host Target-Architecture Contraction C4 — Authority Residue — 2026-08-18

> Historical engineering evidence. C4 follows C1 GoalCoordinator, C2 ExternalExecutor and C3 Recovery/Engine/Cognition-Execution contraction. It does not change frozen HDF0–HDF43 and does not authorize deployment.

## 1. Question

After C3 removed Host-owned repository read/change engines and cognition proposal lowering, does `src/ordivon_host/authority.py` still represent an independently necessary Host responsibility?

The burden is deliberately narrow:

```text
real current action needing Host capability admission?
real current production consumer?
real retained-state decoder obligation?
real rollback obligation?
real cross-consumer failure if deleted?
```

If all fail, no replacement subsystem is to be invented.

## 2. What `authority.py` contained

The 218 LOC module implemented a small capability-policy layer around `anc_effect_ir.CapabilityRequirement`:

- `CapabilityDecision`;
- `CapabilityAuthorizer` protocol;
- `CapabilityProfile`;
- `CapabilityProfileAuthorizer`;
- `BoundCapabilityAuthorizer`;
- `TrustedLocalAuthorizer`;
- `CapabilityDenied`;
- `OWNER_TRUSTED_PROFILE_ID`;
- `PUBLIC_BOUNDED_PROFILE_ID`;
- default profiles for local repository read/change operations.

The concrete policies were explicitly tied to the historical workload families:

```text
principal:local-owner
anc.source.change.v1
anc.object.read.v1
world_object:repository:*
world_object:repository-file:repository:*
```

`TrustedLocalAuthorizer` even described itself as compatibility policy for the two previously proven repository workloads.

Those workloads were deletion-proven and removed by C3.

## 3. Public-surface audit

Post-C3 `authority.py` had **no `ordivon_host` top-level exports**.

Exact current source scan found no production importer of:

```text
ordivon_host.authority
CapabilityProfileAuthorizer
TrustedLocalAuthorizer
CapabilityDecision
CapabilityDenied
OWNER_TRUSTED_PROFILE_ID
PUBLIC_BOUNDED_PROFILE_ID
```

No external Ordivon production Python repository imported the module or its symbols.

No current Host tests or scripts imported those symbols after C3.

Thus:

```text
current production caller = 0
current test apparatus dependency = 0
current top-level public export = 0
```

Direct module import remains a Python compatibility concern, but no current consumer was found.

## 4. Term separation: Content Authority != capability authority

Host also has `docs/authority.md`, titled **Host Content Authority**.

That document is unrelated to `authority.py` capability admission. It specifies which current documents and machine sources are allowed to define Host behavior, architecture, operations and release truth.

Therefore C4 distinguishes:

```text
Host Content Authority
    = source-of-truth / documentation ownership decision

Host capability authority implementation
    = historical repository action admission policy
```

C4 deletes only the second. `docs/authority.md` remains valid and canonical until separately superseded.

This distinction prevents an accidental semantic overreach caused by the overloaded word “authority”.

## 5. Protocol and owner boundary

`CapabilityRequirement` is defined in the promoted effect protocol under Ordivon Computing / `ordivon-protocol` sources.

That protocol describes a semantic capability requirement; it does **not** imply Host must own a universal authorizer.

Current neighboring systems also have their own local capability/mandate concepts. C4 does not migrate Host policy into them.

The correct ownership conclusion is smaller:

> Current Host performs no action that needs these repository capability profiles, so current Host does not need an authorizer for them.

If a future source-change, effect, governance or authority owner needs constitutive admission again, that owner must prove and own the policy under its own order. HDF43 explicitly forbids treating Host as a universal validity/authority oracle.

## 6. Retained history obligation

Current production CAS census returned zero retained object refs with capability/authority/policy kinds relevant to this module.

More importantly, Host historical Doctor does not import `authority.py` to validate old `CapabilityDecision` bytes.

`ops/history.py` validates an old `capability-decision` object structurally:

- exact field set;
- schemaVersion 1;
- kind `ordivon.capability-decision`;
- `allowed is True`;
- principal/action/object-scope identity agreement with a retained Effect when present.

Therefore:

```text
historical byte validation != need for current policy implementation
```

The historical policy need not be executable merely to preserve and validate immutable evidence.

## 7. Rollback obligation

C3 already established the deployment contract:

```text
activationRollbackPolicy = release-bytes-only
liveSchemaVersion = 5
previousReleaseSchemaVersion = 5
migrationRequired = false
explicitRollbackSupported = true
```

The exact previous release retains its own old `authority.py` together with the historical engines.

Therefore current candidate source does not need to retain capability policy solely for rollback.

## 8. C4 deletion candidate

C4 deletes exactly one source file:

```text
src/ordivon_host/authority.py
```

Diff:

```text
1 file changed
0 insertions
218 deletions
net -218 lines
```

Candidate commit:

`bc5c83f9a1ca50cf0fbff5b529d386f070db373f`

Subject:

`refactor(host): remove unconsumed capability policy`

No replacement policy, adapter, registry or owner database was added.

Post-C4 cumulative candidate Host source is approximately:

```text
8,796 Python LOC
```

## 9. Host validation

Deleting `authority.py` required no test rewrites and exposed no hidden caller.

Complete Host validation:

```text
180 / 180 PASS
```

Also passed:

- documentation contract;
- dependency contract;
- compileall;
- `git diff --check`.

Runtime Job:

`job-01a0133a-17ae-73b1-b5d0-ce3492d6b010`

Terminal evidence:

`sha256:d279194e54941135874e6de5c9bc6f698ed6dfbef6a06a0787f24468dba85e2c`

## 10. Real consumer validation

### World

Current World full suite with the C4 Host candidate:

```text
158 / 158 PASS
```

### Security

Current Security focused dynamic Host/context suites:

```text
22 / 22 PASS
```

Runtime Job:

`job-01a0133a-f2a7-7702-b915-5f7fa055d603`

Terminal evidence:

`sha256:909799695faf515d023e6b24d8fd623ea03cb90a6458a32cb752fc159c108636`

No real consumer required Host capability policy.

## 11. Verdict

C4 establishes:

```text
Host capability-authority implementation
    current action need          absent
    current production consumer  absent
    retained-state decoder need  absent
    rollback need                absent
    World need                   absent
    Security need                absent
    deletion proof               PASS
```

Therefore:

```text
Host capability authorization policy
= DELETION-PROVEN / RELEASE-GATED
```

This does **not** mean authority, authorization or constitutive validity are unreal concepts. It means the current Host is not their generic owner and no current Host workload needs this historical implementation.

## 12. Architecture consequence

C4 strengthens the contraction boundary:

```text
Host owns
    durable semantic continuity
    Host Journal/CAS truth
    exact Task revision/fencing
    continuity checkpointing
    bounded derived inspection/handoff
    explicit extension durability
    operational integrity

Host does not own by default
    generic capability policy
    source-change authorization
    effect authority
    governance
    constitutive validity for another owner
```

This is direct engineering consumption of HDF43:

```text
Valid_O(F)
```

Validity and authority remain owner/order-relative rather than collapsing into Host.

## 13. Release gate

Although no top-level `ordivon_host` export was removed, deleting an importable public module is still a compatibility cutover and belongs in the bundled pre-1.0 Major contraction plan.

Thus:

```text
semantic deletion proof       PASS
consumer deletion proof       PASS
history independence          PASS
rollback independence         PASS
canonical deployment          NO
```

Canonical/deployed Host remains:

`8d7e58a0511734a454805e29d10e7d3bb754d2da`

## 14. Next frontier

C4 leaves one major implementation residual before a bundled cutover decision:

```text
Runtime diagnostic / public compatibility
```

Post-C3/C4 `runtime/*` survives primarily for:

- `doctor --runtime` operator diagnostic;
- direct module/top-level compatibility exports;
- testing helpers.

Unlike Authority, `doctor --runtime` has already demonstrated current utility. The next falsifier therefore must compare that utility against direct Runtime-native observation rather than assuming deletion.

The remaining small `recovery.py` projection should be revisited only after the Runtime/public-compatibility audit and bundled release shape are understood.
