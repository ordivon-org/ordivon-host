# Ordivon Host

Persistent coordination and commitment plane for Ordivon.

Ordivon Host owns durable Goals, Tasks, Host events and projections, bounded cognition Contexts, proposal compilation or admission, Effect commitments, Runtime Dispatch identities, verification receipts, participant-routed decisions, and Task outcomes. It treats model sessions and Runtime processes as replaceable dependencies rather than owners of work continuity.

The Host controls durable work and external commitment lifecycles. It does not own model intelligence, domain-world truth, physical execution, or a permanent hierarchy among participants.

## Status

This repository was extracted with Git history from `ordivon-computing/incubation/host-v0` after the H2-H6 architectural gates passed. It is an independently versioned engineering prototype, not yet a general production workflow engine, policy platform, or multi-Agent scheduler.

Current proven vertical slices:

- logical RepositoryRef-based Runtime read with Authority and independent digest verification;
- closed-choice deterministic cognition with two to eight exact CandidateActions;
- open ActionProposal cognition with no prebuilt action menu, durable Model Invocation, Host-owned lowering, and structured rejection;
- explicit `owner_trusted` and `public_bounded` capability profiles, with consequence admission remaining separate from physical reach;
- participant-aware DecisionRequest generation for shared, foreign-owned, or non-reversible proposals;
- a live Codex proposal lowered into one verified Runtime repository read across fresh Host state opens;
- guarded mutation with durable Dispatch identity, conservative UNKNOWN reconciliation, and persisted terminal failure;
- logical RepositoryRef → Computing SourceChange Effect → CapabilityDecision → EffectBinding → Runtime Dispatch;
- durable two-file source change through structured Runtime checks and exact structured diff verification;
- conservative one-shot recovery assessment that never redispatches an uncertain Effect;
- MCP Session lifecycle support without persisting transport sessions as Task truth;
- recovery across fresh Host processes and local Runtime control-plane restarts;
- schema-v3 operational state, backup/restore, optional full-history Doctor, and measured 100,000-event behavior.

The closed-choice path remains a useful deterministic and closed-domain profile. It is no longer treated as the only possible cognition interface. See `docs/H_SERIES_OPEN_PROPOSAL.md` for the first open-proposal boundary.

See `ARCHITECTURE.md`, `CLOSURE.md`, `docs/PRE_H7_CLOSURE.md`, `docs/P0_P1_ALIGNMENT.md`, `docs/P2_P3_EXPLORATION.md`, and `evidence/` for exact boundaries and receipts.

## Development

Python 3.12 is required. The authoritative `ordivon-protocol` 0.2.0 package remains in `ordivon-computing` and is pinned to the exact unified Protocol revision by `pyproject.toml`.

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
ordivon-host --state-root /var/lib/ordivon/host doctor --history
ordivon-host --state-root /var/lib/ordivon/host task assess TASK_ID
ordivon-host --state-root /var/lib/ordivon/host task reconcile TASK_ID
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
