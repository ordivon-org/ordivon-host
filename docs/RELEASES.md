---
schema_version: 1
id: host.releases
title: Host Releases and Versioning
type: policy
profile: engineering
lifecycle: active
source_role: canonical
visibility: public
owners:
  - ordivon-host
audience:
  - maintainer
  - builder
  - operator
  - agent
updated: 2026-08-04
summary: Version identities, change classes, compatibility obligations, release evidence, and deprecation rules for Host.
evidence_status: verified
readiness: READY
applies_to:
  - ordivon-host
related:
  - host.status
  - host.operations
  - host.data-privacy
  - host.authority
---
# Host Releases and Versioning

## Version identities

Host has several independent compatibility identities:

| Identity | Meaning |
| --- | --- |
| Python package version | public source and import change set |
| Git commit | exact repository implementation |
| Host schema and migration history | interpretation of SQLite state |
| durable object `kind` and `schemaVersion` | interpretation of CAS bytes |
| pinned `ordivon-protocol` commit | promoted shared contract dependency |
| Runtime MCP profile | transport lifecycle used for discovery and Tool calls |
| Runtime Tool catalog digest | physical Tool schema bound by a workload |
| workload Effect/Dispatch/Verification identities | semantic commitment and completion evidence |
| live receipt | exact tested revisions, assertions, and environment boundary |

No single SemVer value replaces the stronger identities needed for migration, replay, recovery, and verification.

## Current stage

Host is pre-1.0. Package `0.1.x` identifies an extracted operational prototype, not permanent stability of every import or object field.

Pre-1.0 changes still require:

- a Changelog entry;
- explicit compatibility impact;
- decoder or migration handling for retained state;
- no silent reinterpretation of Events or CAS objects;
- modern and legacy Runtime transport tests when transport changes;
- portable and live evidence appropriate to the affected boundary;
- a deletion trigger for retained compatibility code.

## Change classes

### Patch

Fix behavior without intentionally changing supported durable state, public imports, Runtime lifecycle, or workload semantics. Diagnostics, documentation, tests, and operational safety may improve.

### Minor

Add public APIs, event kinds, object versions, workload capabilities, transport profiles, or additive schema. Existing supported state and callers must remain readable or receive an explicit migration.

### Major

Remove or reinterpret a supported import, event, object, migration path, transport profile, or workload contract. A major cutover must name affected state, export requirements, rollback boundary, acceptance evidence, and the point after which the previous contract is no longer supported.

## Release gates

A releasable commit requires:

1. clean compile and Ruff checks;
2. the complete deterministic suite with `ResourceWarning` treated as error;
3. documentation ownership and link validation;
4. wheel build and metadata inspection;
5. dependency resolution check and vulnerability audit;
6. secret scanning and CodeQL;
7. Changelog entry;
8. exact Protocol dependency pin;
9. read-only live Host→Runtime acceptance when transport, Runtime catalog, read verification, recovery, or configuration changes;
10. additional mutation or source-change receipts when those state machines change.

A hosted CI run cannot prove local Runtime/systemd behavior unless it executes against an operator-owned acceptance environment and retains the receipt.

## Dependency policy

`ordivon-protocol` is pinned to an exact Computing commit. Dependabot may update ordinary packaging or GitHub Action dependencies, but a Protocol revision change is a cross-repository contract update and must be reviewed with Host workload vectors and migration implications.

The Protocol package is first-party and not published through the Python Package Index, so `pip-audit` cannot resolve it as a public advisory target. `scripts/check_dependencies.py` therefore enforces its immutable Git identity, while `requirements-audit.txt` exactly enumerates any third-party Runtime dependencies for independent vulnerability scanning. The current third-party Runtime dependency set is empty.

GitHub Actions are pinned to complete commits. Python remains constrained to `>=3.12,<3.13` until the complete suite and operational path are deliberately qualified on a newer interpreter.

## Deprecation and deletion

A decoder, migration, compatibility profile, import alias, or fallback may be removed only when:

- current writers no longer produce it;
- every production and rollback consumer is named;
- retained databases and CAS objects have been inspected or migrated;
- the observation window has completed;
- live and deterministic evidence remain valid;
- the Changelog records the removal;
- rollback does not depend on the old contract.

The `2025-06-18` Session transport remains a compatibility decoder under this rule. Its existence does not authorize new architecture on the legacy lifecycle.

## Publication

The package is currently distributed from source and repository-built wheels. A public package-index release, hosted service image, signed artifact channel, or automatic deployment pipeline requires a separate publication and provenance contract.
