# Ordivon Harness extraction

Status: implemented locally; final merge identities are recorded during repository closeout.

Source Host revision: `3f50c676802f1c3653767b200db445d15f2f7930`

Extracted Harness history head: `fbfeff54163e78ca86060b053cb854a94703968c`

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

Host now accepts bounded lowercase dotted extension event kinds. An extension event is stored and reconstructed without requiring Host to enumerate its vocabulary.

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
