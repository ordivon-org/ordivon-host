# Ordivon Host

Persistent coordination and commitment plane for Ordivon.

Ordivon Host owns durable Tasks, Goal-scoped Task coordination, Host events and projections, bounded cognition Contexts, proposal compilation or admission, Effect commitments, Runtime Dispatch identities, verification receipts, participant-routed decisions, and Task outcomes. It treats model sessions and Runtime processes as replaceable dependencies rather than owners of work continuity.

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
- an experimental package-scoped executor-neutral Dispatch / Observation / Verification lifecycle candidate;
- immutable TaskDescriptor identity and Goal-scoped Task revision snapshots;
- idempotent per-Task application of one joint VerificationReceipt with multiple result items;
- recovery across fresh Host processes and local Runtime control-plane restarts;
- extension-safe immutable Task events with reserved Host namespaces, thread-stable extension identity, and no dynamic Enum mutation;
- generic Host history and operator-handoff surfaces that preserve extension bytes, references, Task revision, UNKNOWN fences and ready frontiers without interpreting extension semantics;
- a one-way integration boundary for the independently versioned [`ordivon-harness`](https://github.com/zycxfyh/ordivon-harness) repository, which now owns Agent Assignment, Run, Recovery, Completion, Provider adapters and bare-model execution;
- schema-v3 operational state with private 0700/0600 modes, backup/restore, optional full-history Doctor, and measured 100,000-event behavior;
- exact lease-fenced event admission, irreversible terminal Tasks, causal-link validation, and version-bound code-change completion through Runtime compare-and-close.

The closed-choice path remains a useful deterministic and closed-domain profile. It is no longer treated as the only possible cognition interface. Agent Harness implementation and its historical evidence now live in the independent `ordivon-harness` repository.

See `ARCHITECTURE.md`, `CLOSURE.md`, `docs/HARNESS_EXTRACTION.md`, `docs/PRE_H7_CLOSURE.md`, `docs/P0_P1_ALIGNMENT.md`, `docs/P2_P3_EXPLORATION.md`, `docs/GAME_WORKLOAD_P0_P2.md`, and `evidence/` for the retained Host boundary and receipts.

## Development

Python 3.12 is required. The authoritative `ordivon-protocol` 0.3.0 package remains in `ordivon-computing` and is pinned to the exact unified Protocol revision by `pyproject.toml`.

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
