# Ordivon Harness extraction

> **Historical Harness extraction record:** This document preserves stage-specific decisions, measurements, or provenance. It is not a current Host architecture or operations source. Use [`../README.md`](../../README.md), [`../ARCHITECTURE.md`](../../ARCHITECTURE.md), [`OPERATIONS.md`](../OPERATIONS.md), and [`authority.md`](../authority.md) for the active boundary.

Status: implemented, merged and independently published.

Source Host revision: `3f50c676802f1c3653767b200db445d15f2f7930`

Extracted Harness history head: `fbfeff54163e78ca86060b053cb854a94703968c`

Host extraction implementation commit: `8275a81b3a56834561d2555f276c10e6c62e2735`

Host extraction merge commit: `98852d0a39b6d4c489396bda2fd0c99cc3870e34`

Standalone Harness main commit: `7340005d2bfd1b4ec6b7ca4b842d1cc0cac06888`

Repository: `https://github.com/zycxfyh/ordivon-harness`

## Decision

Agent Harness execution and its Host extension lifecycle are no longer maintained inside `ordivon-host`.

```text
ordivon-harness → ordivon-host → ordivon-protocol
```

The reverse dependency is forbidden. Host remains the durable Task, Journal, CAS, Kernel and Runtime-client substrate. Harness owns Task Attempt, Assignment, Run, Recovery, Completion, Provider adapters, bare-model execution and their semantic audit projections.

## History preservation

`src/ordivon_host/harness` was extracted with `git subtree split`. The new repository retains ten Harness implementation commits covering H1–H5, OH0–OH5 and E1–E2 rather than beginning with one flattened source snapshot.

Harness-specific tests, fixtures, evaluation workloads, scripts, documents and immutable evidence were migrated in the extraction closeout commit.

## Host compatibility boundary

Host accepts bounded lowercase dotted extension event kinds. An extension event is stored and reconstructed without requiring Host to enumerate its vocabulary. The later P0 external-executor boundary builds on this generic port: Host persists an immutable request before delivery, binds only the foreign Run identity and observations, and retains a CompletionProposal without decoding Harness internals or accepting the Task.

Generic Host validation retains these guarantees:

- event row and immutable payload kind agree;
- Task identity, revision and timestamp agree;
- every `*ObjectDigest` or `*ObjectDigests` reference is admitted in Host CAS;
- core Effect, Binding and Authority links remain semantically validated;
- extension bytes survive fresh-process reopen.

Harness-specific Assignment, Tool catalog, Run, Recovery and Completion links are validated by `ordivon-harness doctor`, not by importing Harness into the Host Doctor.

## Handoff boundary

`ordivon-host` exports a generic Task handoff capsule containing current revision, event identity, ready frontier, generic Dispatch/Job/Outcome references and no-repeat Effect evidence.

`ordivon-harness` exports the Harness-aware projection containing Attempt, Assignment, Run and Completion identities plus the native Run disposition-derived next action.

## Removed from Host

- `src/ordivon_host/harness`;
- Harness root exports;
- Harness-specific tests and live scripts;
- Harness fixtures and evaluation workload;
- Harness design and closeout documents;
- Harness evidence receipts;
- Harness semantic branches in Host handoff and history Doctor.

No compatibility package remains. Consumers must import `ordivon_harness` explicitly.
