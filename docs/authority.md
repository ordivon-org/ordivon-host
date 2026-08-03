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
  - host.architecture
  - host.operations
---
# Host Content Authority

## Context

Host contains current architecture, operational guidance, extraction records, closeouts, alignment reports, exploratory phases, structure audits, and immutable evidence. Recency, phase number, or document size cannot determine which record defines current behavior.

## Decision

[`../README.md`](../README.md) is the canonical repository entry. [`../ARCHITECTURE.md`](../ARCHITECTURE.md) owns the current Host architecture and responsibility boundary. [`OPERATIONS.md`](OPERATIONS.md) owns the operational state, configuration, migration, backup, restore, Doctor, and conservative reconciliation contract.

Source code, schema migrations, deterministic tests, exact dependency pins, live service inspection, and immutable receipts remain stronger owners for fields, transitions, compatibility, performance measurements, and deployed state. The former root closeout, extraction records, P/H/Round reports, remediation notes, and evidence files explain how the current boundary was reached; they do not silently redefine it.

## Consequences

Only the repository entry, architecture, operations contract, and this decision enter strict content management in this adoption step. Historical and phase-oriented texts remain available with explicit non-authoritative markers. A later human-centered rewrite may reorganize and simplify them, but it must preserve evidence references and use explicit supersession rather than creating another current Host truth.

## Status

Accepted and active. Reopen when Host ownership changes, a machine-generated contract replaces prose, or two managed sources claim the same current responsibility.
