# Ordivon Host

Persistent Agent Host control plane for Ordivon.

Ordivon Host owns durable goals, tasks, Host events and projections, bounded cognition contexts, candidate admission, Effect proposals, Runtime Dispatch identities, verification receipts, and task outcomes. It treats model sessions and Runtime processes as replaceable execution dependencies rather than owners of task continuity.

## Status

This repository was extracted with Git history from `ordivon-computing/incubation/host-v0` after the H2-H6 architectural gates passed. It is an independently versioned engineering prototype, not yet a general production workflow engine or multi-Agent scheduler.

Current proven vertical slices:

- deterministic Runtime read with independent digest verification;
- persistent multi-candidate cognition with deterministic admission;
- guarded mutation with durable Dispatch identity and conservative UNKNOWN reconciliation;
- recovery across fresh Host processes and local Runtime control-plane restarts.

See `ARCHITECTURE.md`, `CLOSURE.md`, and `evidence/` for exact boundaries and receipts.

## Development

Python 3.12 is required. The authoritative `ordivon-protocol` package remains in `ordivon-computing` and is pinned to an exact Git revision by `pyproject.toml`.

```bash
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
python -m unittest discover -s tests
```

Live scripts require a reachable Ordivon Runtime and are not part of default CI.

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
