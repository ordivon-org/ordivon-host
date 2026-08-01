# A-Series Host authority remediation

This change closes the source-level findings recorded by Ordivon Computer study `2026-ordivon-host-a-series-source-audit`.

## Closed correctness findings

- Exact lease owner, generation and expiry are checked in the same SQLite transaction as every non-creation Event; successful admission consumes the lease.
- Host-owned Event namespaces fail closed and extension Event values are immutable, interned and thread-stable.
- Failed or rejected generic Effect evidence cannot be overwritten by a caller-provided completed status.
- Joint Verification must be accepted before any per-Task result can advance.
- Terminal Task identities cannot return to a nonterminal state.
- `causedByEventId` must reference an already-admitted Event and startup validation detects dangling legacy rows.
- Code-change evidence is bound to Runtime `sourceStateDigest` and admitted only after exact compare-and-close.
- Host state, CAS, backups and Runtime token files use an explicit private trusted-local permission profile.

## Subtraction

- Removed the unused `PROPOSED` Task state, Goal Stream kind, Wakeup Event, and test-only runtime ownership table.
- Removed generic Effect lifecycle classes from the top-level stable API; they remain package-scoped experimental code pending a second real consumer and net lifecycle deletion.
- Clarified that Host owns Goal-scoped Task coordination, not a durable Goal stream or universal Goal commitment.

## Retained compatibility

`RUNNING` and `activeNodeId` remain readable because they exist in durable schema-v3 history, but current workloads do not use them. `DispatchEnvelope.expectedObservationKind` remains a protocol-v1 compatibility field; it is not represented as an enforced Observation property and is not used as completion evidence. Removing it requires a separately versioned Computing protocol migration rather than a Host-local reinterpretation.

## Non-goals

No scheduler, DAG engine, distributed lease, second database, policy platform, Provider session store, or Host/Harness recoupling was introduced.
