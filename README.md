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
- executor-neutral Dispatch / Observation / Verification lifecycle with a Runtime adapter;
- immutable TaskDescriptor identity and Goal-scoped Task revision snapshots;
- idempotent per-Task application of one joint VerificationReceipt with multiple result items;
- recovery across fresh Host processes and local Runtime control-plane restarts;
- Host-local Harness H1 contracts with durable Assignment generation, stale CompletionProposal rejection, required-Artifact and unresolved-UNKNOWN checks, and fresh-process recovery without a second database;
- Harness H2 Runtime correlation with canonical `ordivon.host` Task, Task Attempt, Assignment, and Harness Run references, Assignment-bound request identity, live replay/conflict/terminal-evidence proof, and no new Runtime state owner;
- Codex App Server H3 with a provider-faithful stdio driver, durable Thread/Turn and Tool-lifecycle evidence, Runtime-owned process execution, interrupt support, raw-event digest retention, and a live read-only Harness Run that leaves semantic completion with Host;
- Hermes ACP H4 with a provider-faithful JSON-RPC stdio driver, Session provenance, cancel and fail-closed client-request handling, read-only Tool observation, usage and thought-event digest retention, and a live Runtime-owned Harness Run that leaves semantic completion with Host;
- Harness H5 with both live Codex↔Hermes mid-Task replacement orders, one stable Task Attempt, fresh Assignment generation and Context, Artifact-first completion, stale-generation and missing-Artifact rejection, response-loss recovery without redispatch, and explicit rejection of a shared Provider lifecycle;
- first-party Ordivon Harness OH1–OH5 with a real DeepSeek bare-model loop, Assignment-scoped Runtime ACI, durable Task/Run/Verification contracts, Assignment-bound Tool semantics, unified native Run disposition, fresh-Host completion, conservative Runtime UNKNOWN fencing, and evidence-backed read-only process-loss abandonment;
- schema-v3 operational state, backup/restore, optional full-history Doctor, and measured 100,000-event behavior.

The closed-choice path remains a useful deterministic and closed-domain profile. It is no longer treated as the only possible cognition interface. See `docs/H_SERIES_OPEN_PROPOSAL.md` for the first open-proposal boundary.

See `ARCHITECTURE.md`, `CLOSURE.md`, [`docs/ORDIVON_HARNESS_OH1_OH5_CLOSEOUT.md`](docs/ORDIVON_HARNESS_OH1_OH5_CLOSEOUT.md), `docs/ordivon-harness-v0.md`, `docs/PRE_H7_CLOSURE.md`, `docs/P0_P1_ALIGNMENT.md`, `docs/P2_P3_EXPLORATION.md`, `docs/H_SERIES_OPEN_PROPOSAL.md`, `docs/GAME_WORKLOAD_P0_P2.md`, [`docs/harness-boundary-stage1.md`](docs/harness-boundary-stage1.md), [`docs/harness-boundary-h5-decision.md`](docs/harness-boundary-h5-decision.md), and `evidence/` for exact boundaries, experiment decisions, and receipts.

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
