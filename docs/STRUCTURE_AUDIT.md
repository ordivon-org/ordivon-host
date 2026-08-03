# Post-extraction structure audit

> **Historical post-extraction structure audit record:** This document preserves stage-specific decisions, measurements, or provenance. It is not a current Host architecture or operations source. Use [`../README.md`](../README.md), [`../ARCHITECTURE.md`](../ARCHITECTURE.md), [`OPERATIONS.md`](OPERATIONS.md), and [`authority.md`](authority.md) for the active boundary.

## Scope

This audit was performed after the history-preserving extraction of `ordivon-computing/incubation/host-v0` into the independent `ordivon-host` repository. The objective was to identify structure that had become duplicated or overloaded during H2-H6 without introducing a scheduler, workflow DSL, generic Effect framework, or other speculative abstraction.

## Baseline

Before the migration refactor:

| Area | Size |
|---|---:|
| Host source | 4,995 lines |
| deterministic tests | 2,735 lines |
| live scripts | 1,672 lines |
| `engine/mutation_task.py` | 1,183 lines |
| `engine/read_task.py` | 584 lines |

The large mutation module owned data models, Effect construction, Runtime delivery, UNKNOWN reconciliation, Workspace lifecycle, verification, event projection, strict decoding, and public exceptions. Read and mutation workloads also carried separate copies of the same Runtime client Protocol, missing-Workspace classification, digest utilities, task-token derivation, and strict JSON extraction.

## Changes

The refactor preserved the public `ordivon_host.engine` imports and split only responsibilities already proven to be independently repeated:

```text
runtime/client.py            shared RuntimeClient Protocol
runtime/workspaces.py        shared missing-Workspace classification
engine/_serde.py             shared strict digest and JSON mechanics
engine/mutation/models.py    mutation plans, observations, receipts, steps
engine/mutation/effect.py    exact v0 mutation Effect construction
engine/mutation/host.py      mutation state machine and recovery orchestration
```

The former `engine/mutation_task.py` was removed. `engine/read_task.py` now consumes the shared Runtime and serialization mechanics but remains a single vertical workload because its remaining logic is still cohesive.

## Result

| Area | Before | After |
|---|---:|---:|
| Host source | 4,995 | 5,040 lines |
| deterministic tests | 2,735 | 2,752 lines |
| live scripts | 1,672 | 1,672 lines |
| mutation model layer | embedded | 288 lines |
| mutation Effect layer | embedded | 68 lines |
| mutation Host orchestration | embedded | 819 lines |
| read workload | 584 | 538 lines |
| shared serialization mechanics | duplicated | 47 lines |
| shared Runtime client Protocol | duplicated | 11 lines |
| shared Workspace error classification | duplicated | 12 lines |
| internal Python modules | — | 30 |
| internal import cycles | — | 0 |

The source total increased by 45 lines. This is accepted because the change makes dependencies explicit, removes duplicated definitions, and separates stable data contracts from orchestration. The objective was responsibility clarity, not line-count reduction.

## Preserved contracts

- `ordivon_host.engine.GuardedMutationHost` remains the public workload class.
- `ordivon_host.engine.GuardedMutationPlan` and existing mutation types remain available.
- `ordivon_host.engine.DeterministicReadHost` remains available.
- `ordivon_host.runtime.RuntimeClient` is now the one shared structural Protocol.
- all 77 pre-refactor deterministic tests pass after extraction and module separation, plus two migration structure assertions for 79 total tests;
- the separate-process recovery test now uses the installed or inherited protocol dependency instead of assuming the old Computing directory layout.

## Deliberately deferred

The following are real remaining concerns but were not mixed into this migration:

- `mutation/host.py` is still 819 lines and may need further decomposition after a second real mutation workload reveals a stable boundary;
- the six live scripts still duplicate scenario setup, readiness, identity, fault injection, cleanup, and receipt writing;
- no formal CLI, configuration contract, service, state-root layout, inspect/doctor, backup/restore, or deploy/rollback surface exists for Host;
- Host Journal cold-start and invariant-validation cost has not been measured at long history scale;
- whole-WSL restart, machine reboot, abrupt power loss, and remote Runtime partition recovery remain unproven;
- the current guarded mutation remains an exact proof-file workload, not general software-engineering execution.

These concerns should be addressed through separate, evidence-driven changes after the migration is merged.
