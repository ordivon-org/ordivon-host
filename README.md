# Ordivon Host

Persistent Agent Host control plane for Ordivon.

Ordivon Host owns durable goals, tasks, Host events and projections, bounded cognition contexts, candidate admission, Effect proposals, Runtime Dispatch identities, verification receipts, and task outcomes. It treats model sessions and Runtime processes as replaceable execution dependencies rather than owners of task continuity.

## Status

This repository was extracted with Git history from `ordivon-computing/incubation/host-v0` after the H2-H6 architectural gates passed. It is an independently versioned engineering prototype, not yet a general production workflow engine or multi-Agent scheduler.

Current proven vertical slices:

- deterministic Runtime read with independent digest verification;
- persistent multi-candidate cognition with deterministic admission;
- guarded mutation with durable Dispatch identity and conservative UNKNOWN reconciliation;
- durable two-file source change through structured Runtime checks and independent diff verification;
- recovery across fresh Host processes and local Runtime control-plane restarts;
- schema-v3 operational state, backup/restore, full Doctor validation, and measured 100,000-event behavior.

See `ARCHITECTURE.md`, `CLOSURE.md`, `docs/PRE_H7_CLOSURE.md`, and `evidence/` for exact boundaries and receipts.

## Development

Python 3.12 is required. The authoritative `ordivon-protocol` package remains in `ordivon-computing` and is pinned to an exact Git revision by `pyproject.toml`.

```bash
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
python -m unittest discover -s tests
```

Live scripts require a reachable Ordivon Runtime and are not part of default CI.

## Operations

After installation, the `ordivon-host` command provides state initialization, inspection, Task queries, Doctor checks, backup verification and restore, and read-only CAS garbage-collection planning:

```bash
ordivon-host --state-root /var/lib/ordivon/host init
ordivon-host --state-root /var/lib/ordivon/host doctor
ordivon-host --state-root /var/lib/ordivon/host inspect
```

See `docs/OPERATIONS.md` for the schema migration, configuration, secret-loading, backup, and restore contracts.

## Repository layout

```text
src/ordivon_host/   Host implementation
tests/              deterministic contract tests
scripts/            explicit live and fault-injection scenarios
evidence/           immutable historical receipts
docs/               migration and structure decisions
```

## License

Apache License 2.0. See `LICENSE`.
