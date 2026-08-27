# Host Historical Approximation Cutover — 2026-08-28

This is the compact recovery surface for the Host slice of the cross-owner historical-approximation audit. It does not reopen HDF0–HDF43. It applies the existing Host owner boundary and compatibility policy to implementation machinery that survived after its consumer disappeared.

## Standing

**CLOSED / 0.5 CUTOVER ADMITTED, PRODUCTION ACTIVATION DEFERRED.** Host 0.5 removes executable recovery of migrated schema-v4 extension state while preserving the current schema-v5 namespace contract. The exact candidate is portable-gate clean, World-consumer clean, retained-production-history clean, and deployment-plan eligible. Activation is intentionally separate because restarting the shared live Host MCP is an operational effect rather than evidence required to establish the code boundary.

## Historical approximation

Schema v5 originally allowed a v4 database to scan historical extension Events, retain the latest Event payload per namespace as `legacy=true`, expose that payload for owner interpretation, block ordinary mutation, and later accept an exact owner-authored `recover_legacy_namespace()` replacement. This was a defensible migration bridge when retained v4 extension state was unknown.

The 2026-08-28 destructive audit found that the bridge has no current retained consumer:

- the live schema-v5 production authority contained **0 `task_extension_state` rows / 0 legacy rows** while full-history Doctor passed;
- the receipt-bound `host.sqlite3.pre-schema-v5.sqlite3` backup contains **141 Events**, all `task.created` or `task.context-checkpointed`, with **0 extension Events**; its SHA-256 is `0939a7046c829582d97469524f78a3046f2d070541e4fc31d498ce5d03225344`;
- the current reversible deployment frontier is schema v5 on both sides; schema-v4 release bytes are no longer a rollback peer;
- no cross-repository consumer calls `recover_legacy_namespace()` or imports `HostExtensionLegacyStateUnknown`;
- World, the named current consumer of `HostExtensionNamespaceSnapshot.legacy`, passed 49 Inspector/Provider/Resource/Message/Entity/temporal/replacement tests against the candidate.

## 0.5 boundary

Host 0.5 therefore keeps the schema-v5 table and public snapshot shape but changes the compatibility law:

- native v5 writes continue to store `host-extension-state` objects and project `legacy=false`;
- the v5 `legacy` column and snapshot field remain compatibility representation, not an executable migration state machine;
- a retained nonzero legacy marker fails closed and requires pre-0.5 owner recovery/export;
- a schema-v4 authority with **no extension Events** still migrates to v5 normally;
- a schema-v4 authority with **any extension Event** fails before migration and explicitly requires a pre-0.5 Host client for owner recovery/export;
- Host no longer infers owner state from old Event payloads and no longer publishes a recovery API for that inferred state.

This is the same general law established independently by the World Minimal 2.0 audit: a historical migration law can remain valid history after its executable compatibility machinery loses every current consumer.

## Source-currentness repair discovered during admission

Deployment planning exposed an independent source-representation debt. The active release was `4d5a04e738d2fbd99fca0ff1af6f8703de0ae8fb`, while GitHub main was still `e1fc92188918d4ecfd357f032430123e31b20596`. The deployed-only lineage contained two real current capabilities: `7d45bba` (Host-owned cold-start/reproducible test environment) and `4d5a04e` (runtime-hint carrier authority repair).

The first 0.5 candidate based only on remote main was correctly **not activated** because doing so would have regressed those deployed capabilities despite an otherwise eligible schema plan. Both deployed commits were then replayed without conflict after the 0.5 product cutover, producing `e3daac89377a4e57ed3c4a76546005b23b9456d6`; cold-start/full Host acceptance and the World named-consumer gate passed again. GitHub main was fast-forwarded to that reconciled lineage.

Hence:

> Git remote currentness is not implementation currentness merely because it is distributed; deployed bytes are not canonical source merely because they are live. A release/admission decision must compare both and reconcile the relation explicitly.

## Deployment admission evidence

The reconciled `e3daac8` candidate materialized as release `e3daac89377a4e57ed3c4a76546005b23b9456d6-901827fc7277`. Provider-independent deployment planning reports:

- `eligible=true`;
- `blockers=[]`;
- live schema = candidate schema = previous-release schema = 5;
- `migrationRequired=false`;
- `activationRollbackPolicy=release-bytes-only`;
- `explicitRollbackSupportedAfterSuccess=true`;
- current deployed commit remains `4d5a04e...`.

No activation claim is made here.

## Reopen conditions

Reopen the retired compatibility machinery only if a concrete retained schema-v4 authority with extension Events must be upgraded and cannot first be recovered/exported with a pre-0.5 client. Do not reopen it because the `legacy` field still exists, because old Changelog/history mentions the bridge, or because compatibility symmetry appears desirable.

Reopen source-distribution handling if deployed/current bytes can again advance without an explicit source-distribution reconciliation surface. That pressure belongs to deployment/currentness infrastructure, not to Host Task semantics.
