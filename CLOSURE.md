# Host v0 closeout

This directory records the final Computing-incubator state of Ordivon Host v0 before extraction into the independent `ordivon-host` repository.

## Closure decision

The original incubation gates are satisfied:

- a real guarded mutation succeeds through a durable Dispatch;
- uncertain delivery is reconciled without automatic redispatch;
- asynchronous Runtime recovery succeeds across fresh Host processes and Runtime control-plane restarts;
- the exact Runtime Job lookup optimization is deployed to production;
- production guarded mutation latency is subsecond on the measured local path.

The Host is therefore no longer blocked on architectural proof. Remaining work concerns repository extraction, installable packaging, operational surfaces, structure reduction, and real engineering workloads.

## Bound revisions

- HostKernel: `c371da48cea950a75a4f72b4492770979dfcf55f`
- Host recovery implementation: `e4e8e6b90b39677ec7582e0f66fdcc3789904918`
- H6 recovery evidence: `e0b91341627b0d7072d6ec6aec009d3f2353a418`
- Runtime production revision: `2d4141b30ebabd9119ed4e9547c36759cb5b7b77`
- Runtime CI run: `30267867183`

The machine-readable closure is `evidence/h6-production-closure-20260727.json`.

## Extraction contract

1. Merge this complete Host history into `ordivon-computing/main` without rewriting the H2-H6 commit identities.
2. Extract `incubation/host-v0` with path history preserved.
3. Create `zycxfyh/ordivon-host` under Apache-2.0.
4. Keep `ordivon-protocol` authoritative in Computing; the Host repository must pin an exact Computing revision instead of copying protocol source.
5. Perform repository-layout and module-boundary cleanup as separate commits after extraction so provenance remains inspectable.

## Boundary

This closeout does not claim a production-ready general Agent Host. It closes the architectural incubator and establishes a traceable starting point for the independent repository.

## Subsequent closure records

The first-party bare-model Harness read-only lifecycle was later closed through OH1–OH5. See [`docs/ORDIVON_HARNESS_OH1_OH5_CLOSEOUT.md`](docs/ORDIVON_HARNESS_OH1_OH5_CLOSEOUT.md) for the final capability matrix, verified invariants, retained structural debt and gated effectful continuation route.
