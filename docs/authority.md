---
schema_version: 1
id: host.authority
title: Host Content Authority
type: decision
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
summary: Decision identifying the documents and machine sources allowed to define current Host behavior, ownership, and operational state.
evidence_status: not_applicable
readiness: READY
applies_to:
  - ordivon-host
related:
  - host.start
  - host.quickstart
  - host.status
  - host.architecture
  - host.operations
  - host.data-privacy
  - host.releases
---
# Host Content Authority

## Context

Host contains current architecture, operational guidance, extraction records, closeouts, alignment reports, exploratory phases, structure audits, and immutable evidence. Recency, phase number, or document size cannot determine which record defines current behavior.

## Decision

| Responsibility | Canonical source |
| --- | --- |
| public repository identity, boundary summary, and navigation | [`../README.md`](../README.md) |
| installation, deterministic checks, state initialization, and first live journey | [`QUICKSTART.md`](QUICKSTART.md) |
| stable maturity, support environment, proven slices, and known limits | [`STATUS.md`](STATUS.md) |
| current Host architecture and component ownership | [`../ARCHITECTURE.md`](../ARCHITECTURE.md) |
| state root, configuration, migration, Doctor, backup, restore, and bounded reconciliation | [`OPERATIONS.md`](OPERATIONS.md) |
| stored data, sensitivity, retention, export, migration, and deletion | [`DATA_AND_PRIVACY.md`](DATA_AND_PRIVACY.md) |
| versions, release gates, dependency policy, compatibility, and deprecation | [`RELEASES.md`](RELEASES.md) |

Source code, schema migrations, deterministic tests, exact dependency pins, live service inspection, and immutable receipts remain stronger owners for fields, transitions, compatibility, performance measurements, and deployed state. `CHANGELOG.md` records user-visible change but does not override current contracts. The former root closeout, extraction records, P/H/Round reports, remediation notes, and evidence files explain how the current boundary was reached; they do not silently redefine it.

## Consequences

The repository entry, Quick Start, status, architecture, operations, data/privacy, release policy, and this decision enter strict content management. Historical and phase-oriented texts remain available with explicit non-authoritative markers. A later human-centered rewrite may reorganize and simplify them, but it must preserve evidence references and use explicit supersession rather than creating another current Host truth.

## Status

Accepted and active. Reopen when Host ownership changes, a machine-generated contract replaces prose, or two managed sources claim the same current responsibility.
