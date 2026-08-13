# Provider-First Host — PF2

Status: current-boundary audit — 2026-08-14

## Disposition

**NO STRUCTURAL CHANGE EARNED.**

Host should continue inheriting mature protocol, database, process, and transport mechanisms while owning the durable semantics that make Ordivon work explicit and recoverable.

Provider-First does not imply replacing Host with a workflow engine.

## What mature workflow engines already own

Current durable-workflow systems such as Restate and DBOS can own generic mechanics including durable step replay, retries/backoff, timers, scheduling, workflow state, cancellation, event waiting, queues, and workflow observability.

Those are strong comparative falsifiers for any future Host mechanism that starts to resemble a general workflow system.

They do **not** by themselves eliminate Host's current semantic responsibilities:

- exact Task and Goal identity;
- revision-fenced semantic transitions;
- Event admission and projection laws;
- semantic commitment identity;
- uncertainty and `UNKNOWN` preservation;
- WorkingCheckpoint / external continuity semantics;
- Proposal / Decision / cognition authority;
- owner-scoped extension state;
- Runtime effect lowering plus post-effect verification;
- exact cross-owner handoff evidence.

## Current ownership is already narrow

Current implementation hotspots include the SQLite journal, content-addressed objects/storage, kernel transition law, external continuity, and Runtime/MCP adapters.

The journal and storage implementations are physical realizations of Host semantics. SQLite remains the database owner; the MCP SDK remains the protocol owner; Runtime remains the execution owner. Host should not reimplement those lower mechanisms.

Likewise, `mcp_server.py` being large does not make Host an MCP transport implementation: the public protocol lifecycle remains delegated to the installed MCP SDK while Host projects its own semantic surface.

## Why Restate/DBOS adoption is not currently earned

A replacement would have to delete more Host-owned mechanism than it introduces.

At present, generic workflow durability would still require Host to encode Task revision, semantic Event types, commitment identity, extension ownership, cognition/proposal semantics, external continuity and Runtime evidence. In addition, the candidate would introduce a new durable-workflow runtime and/or database/control-plane dependency.

Therefore the current comparison is:

```text
Host semantic kernel + SQLite + official MCP SDK + Runtime
```

versus

```text
Host semantic kernel + workflow provider + provider state model + provider operations
```

No current workload proves the second has lower total ownership.

This is a **retention result produced by Provider-First**, not an exception to it.

## What would reopen the decision

Re-evaluate a durable-workflow provider when a real workload produces at least one of these pressures:

- multi-node Host execution where current owner-local journaling becomes the bottleneck;
- large durable timer/event workloads that Host would otherwise implement itself;
- generic queue/scheduling mechanics begin accumulating in Host;
- operator recovery requires a distributed workflow control plane;
- a provider can demonstrably replace a substantial amount of physical journal/recovery machinery while preserving exact Host semantics.

Until then, workflow engines remain external benchmark equipment, not Host dependencies.

## Freeze

Continue the existing freezes against a universal scheduler, generic DAG/workflow DSL, provider router, or connector registry. New Host mechanism must first prove that a mature owner cannot provide the mechanic beneath Host semantics.
