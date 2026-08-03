# Repository migration

> **Historical repository migration record:** This document preserves stage-specific decisions, measurements, or provenance. It is not a current Host architecture or operations source. Use [`../README.md`](../README.md), [`../ARCHITECTURE.md`](../ARCHITECTURE.md), [`OPERATIONS.md`](OPERATIONS.md), and [`authority.md`](authority.md) for the active boundary.

## Provenance

The initial `ordivon-host/main` revision is the history-preserving subtree extraction of:

```text
repository: zycxfyh/ordivon-computing
source main: b2b9bb0ca0ea9a56ce600014f2b642a55e4f7461
source prefix: incubation/host-v0
extracted head: 0214db4fa1beb2342a4b7a674d7772c727de5e6a
```

The subtree extraction rewrites commit hashes because the path prefix becomes the new repository root, but retains the ordered Host-only commit history and commit messages. The original Computing H5/H6 revisions remain reachable from `ordivon-computing/main` and are bound by `evidence/h6-production-closure-20260727.json`.

## Ownership after extraction

- `ordivon-host` owns Host implementation, Host state, cognition adapters, Runtime coordination, and Host evidence.
- `ordivon-computing` remains authoritative for `ordivon-protocol`, semantic reference behavior, conformance vectors, and cross-project architecture.
- `ordivon-runtime` remains authoritative for physical Workspace, Job, Attempt, process, Artifact, and Runtime recovery state.

The Host dependency is pinned to the exact Computing revision above. Protocol source is not copied into this repository.

## Incubator closure provenance

The pre-extraction H6 closure established guarded mutation, UNKNOWN reconciliation without automatic redispatch, fresh-process Runtime recovery, exact Runtime Job lookup, and the measured subsecond local guarded-mutation path. The retained machine evidence is `evidence/h6-production-closure-20260727.json`. Bound historical revisions include HostKernel `c371da48cea950a75a4f72b4492770979dfcf55f`, Host recovery `e4e8e6b90b39677ec7582e0f66fdcc3789904918`, H6 evidence `e0b91341627b0d7072d6ec6aec009d3f2353a418`, and Runtime production `2d4141b30ebabd9119ed4e9547c36759cb5b7b77`.

The former root `CLOSURE.md` repeated this provenance while describing extraction as future work. Its unique evidence references are preserved here; the obsolete closeout was removed after extraction completed.

## License

The independent repository is licensed under Apache-2.0.
