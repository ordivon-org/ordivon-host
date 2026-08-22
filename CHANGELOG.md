# Changelog

All user-visible changes to Ordivon Host are recorded here. Release and compatibility rules are defined in `docs/RELEASES.md`.

## Unreleased

No unreleased changes.

## 0.4.1 — 2026-08-22

### Changed

- clarify the Agent-facing Host MCP contract after fresh-Agent consumer testing: `task.*` remains a compatibility surface for Host continuity, while `task.list`/`host.status` explicitly reject interpretation of READY/non-terminal continuity as NOW work, priority, owner standing, or current domain truth;
- keep the six Tool names, input/output schemas, Journal schema, WorkingCheckpoint schema, and durable semantics unchanged; the repair is a consumption-contract correction rather than a Task-v2 or new portfolio authority.

### Compatibility

- Patch-class release: no Host Journal/CAS migration, durable object change, public import removal, Tool name change, input/output schema change, or workload-semantic reinterpretation;
- `serverInterface.schemaDigest` remains `sha256:382abe793d2e41470d91f1efc00d76152ff1fdbae3cac53adcad511d8211780c`; only MCP presentation/instruction text and corresponding documentation/tests change.

## 0.4.0 — 2026-08-18

### Removed

- remove the public `RecoveryAction`, `RecoveryAssessment`, and `assess_recovery` compatibility exports, the `ordivon_host.recovery` module, and CLI `task assess`;
- remove four unreachable historical recovery actions (`advance-read`, `observe-runtime-dispatch`, `cognition-result-required`, `manual-stage`) that survived after their owning engines were deleted.

### Changed

- `task.observe` keeps its existing `recovery` response field for wire-schema stability, but current Host production continuity Tasks continue to project it as `null`; owner-native continuation remains `task.resume` / handoff plus direct owner revalidation;
- boundary tests and documentation checks now fail closed if the removed recovery compatibility surface returns.

### Compatibility

- **Breaking pre-1.0 cleanup:** removing the package-root recovery exports and `task assess` is Major-class under Host policy, so `0.3.x` advances to `0.4.0`;
- retained production state contains 497/497 Tasks under `ordivon.host.external-continuity.v1`, for which MCP `task.observe` already bypassed the generic recovery projection; no durable state migration is required;
- exact scans found no external production import/caller of the removed recovery API or CLI;
- the six-Tool MCP catalog and its schema are intentionally unchanged.

## 0.3.1 — 2026-08-18

### Changed

- canonical README, architecture and quick start were reconstructed around the actually deployed Host boundary: durable semantic Task continuity + Host-owned Journal/CAS authority, not the removed Goal coordination, cognition execution/proposal, Runtime client/read/mutation/code-change, foreign executor, or capability-policy responsibilities;
- `Host` is explicitly the stable product/compatibility proper noun for that narrowed responsibility, not a universal Host ontology or central coordinator;
- documentation validation fails closed if removed C1–C5 ownership claims return;
- Runtime is an external owner and is not a Host installation prerequisite.

### Compatibility

- 0.3.1 changed no product implementation, MCP Tool schema, Host Journal schema, state format, package-root API, CLI, config, or deployment semantics;
- package/service/state identities remain `ordivon-host` / `ordivon_host` / Ordivon Host.

## 0.3.0 — 2026-08-18

### Changed

- Major-class contraction narrowed Host to durable semantic continuity, Host-owned Journal/CAS authority, exact Task revision/lease admission, bounded handoff/inspection, opaque extension durability, the Security-consumed context-selection surface, and local integrity/deployment operations;
- Runtime, Harness, World, Security and other owners are observed directly when their current physical/domain truth matters;
- release acceptance was rebuilt around Host-owned behavior, named real consumers and receipt-bound deployment rather than deleted Host→Runtime workload ownership.

### Removed

- shared Goal coordination;
- caller-neutral ExternalExecutor coordination;
- Host read, guarded-mutation and code-change engines;
- Host cognition execution/proposal/decision orchestration;
- automatic `TaskReconciler` and `RecoveryResult`;
- generic Host capability-authority policy;
- product Runtime client/config/catalog/health integration.

### Compatibility

- the removed package exports/submodules, `[runtime]` config, `task reconcile`, and `doctor --runtime` were intentionally not aliased;
- durable Host Journal/CAS schema remained v5 with no migration;
- World and Security remained named compatibility consumers.

## 0.2.0 — 2026-08-13

### Added

- schema-v5 `task_extension_state` durability: each Task × extension namespace can retain one opaque content-addressed owner state independently of the current Task Event head, with `HostExtensionPort.load_namespace()` preserving the compatibility view and `load_namespace_snapshot(..., expected_revision=...)` exposing revision-coherent Host-owned namespace metadata without importing owner schemas;
- revision-fenced `extensionNamespaces` on external-continuity resume, exposing only which durable extension-owner namespaces had appeared by that exact Task revision; owner fields remain private and namespace presence does not imply availability, currentness, outstanding work, reachability, success, or authority;
- Host MCP can explicitly trust Cloudflare Access assertions behind its loopback-only Tunnel deployment while retaining the independent local Bearer path, matching the proven Runtime remote-auth pattern;
- authenticated loopback `ordivon-host-mcp` using the pinned official MCP Python SDK, with a separate private bearer token, stateless HTTP transport, four-Tool surface for bounded Task discovery plus external-continuity resume/adopt/checkpoint, systemd templates, structured Tool errors, and real response-loss/concurrency acceptance;
- `ordivon.host.external-continuity.v1`, bounded `WorkingCheckpoint` CAS objects, `task.context-checkpointed`, revision-safe adoption/checkpoint/resume APIs, and local CLI commands for cross-session semantic continuity without Runtime or Provider execution;
- caller-neutral `ExternalExecutorAdapter`, durable external request, foreign Run binding, exact response-loss recovery, cancellation/observation reconciliation, and CompletionProposal collection without Task acceptance;
- public Quick Start, status, data/privacy, release, security, contribution, and change-history documents;
- executable documentation ownership and local-link validation;
- pinned CI actions, CodeQL, secret scanning, dependency audit, Dependabot, release acceptance, and read-only live Host→Runtime acceptance;
- explicit modern and legacy Runtime transport profiles.

### Changed

- Host MCP now publishes an exact schema-only interface identity on every Tool result; the digest binds Tool names plus input/output schemas while ignoring presentation text, and deployment receipts retain the independently re-observed wire `tools/list` schema identity so Agents can distinguish server capability from a stale client-loaded Tool schema;
- local deployment now binds current/candidate Journal schema to release activation; forward migrations stop Host MCP, retain a receipt-bound schema-neutral preactivation SQLite snapshot, restore that snapshot before restarting the previous binary on activation failure, verify live schema in deployment status, refuse post-success explicit rollback across the schema boundary, and remove schema-incompatible previous releases from the reversible lifecycle frontier;
- Host MCP delegates every Tool call to a fresh Host storage handle and H-C1 authority; MCP transport/session state never becomes durable Task continuity;
- external continuity keeps its Task permanently at a stable READY/continue frontier; checkpointing advances only revision/time, while Runtime/Git/domain references remain navigation hints rather than copied truth;
- cognition durability is now provider-neutral: one `CognitionWorkRequest` moves the exact Task revision to WAITING and declares only Context plus requested semantic result kind; admission consumes `ActionSelection` or `ActionProposal` plus `CognitionExecutionEvidence`;
- removed Host `ModelInvocation*`, `PreparedInvocation`, `gatewayId`/`adapterId` cognition fields, Provider packages, Codex/Hermes physical execution compatibility modules, and Provider-shaped cognition event kinds;
- removed `[providers]` from Host configuration entirely; Provider/model configuration belongs to the external cognition executor;
- cognition recovery now reports `cognition-result-required` for a `cognition.requested` head and never instructs Host to invoke a Provider.
- the package root now exposes durable Host authority and cross-owner boundary types only; deterministic read, guarded mutation, and code-change workloads remain available explicitly from `ordivon_host.engine`;
- canonical live workload scripts now import their workload implementations from `ordivon_host.engine`, keeping the default Host surface responsibility-oriented without deleting the proven workloads;
- the default Runtime transport now uses the stateless MCP `2026-07-28` `server/discover` lifecycle with per-request metadata, `Mcp-Method`, and `Mcp-Name`;
- the retained MCP `2025-06-18` Session lifecycle is now an explicit compatibility profile rather than the default;
- development documentation now distinguishes immutable online installation from a local sibling Protocol checkout;
- canonical document ownership now includes status, data/privacy, release, and Quick Start responsibilities.

### Fixed

- release candidate construction now runs every dependency-resolving uv operation in explicit offline mode after the lock check; a missing pinned build backend or dependency fails as an unmaterialized local substrate instead of silently turning deployment into a PyPI/network operation;
- schema-changing activation rollback now reconciles migration backup sidecars as part of the same provisional authority boundary: sidecars created only by the failed candidate are removed before the previous release restarts, while exact preactivation sidecars are preserved and any mutation of them fails rollback closed;
- schema migration backups now treat the canonical `pre-schema-vN` path as the current attempt's rollback source: a valid but superseded backup is preserved under a digest-addressed archive before a fresh standalone SQLite snapshot replaces it, while corrupt backups or backups with pending WAL state still fail closed;
- candidate deployment self-check now bootstraps its temporary build-local Host authority with the candidate CLI before invoking `ordivon-host-mcp --check`, so real prepare remains isolated from production without failing on an uninitialized temporary Journal;
- release candidate construction is authority-pure: `prepare` now runs `ordivon-host-mcp --check` against an explicit temporary build-local state root and removes it afterward, preventing candidate validation from opening or migrating the default live Host authority;
- canonical Host architecture and operations describe the post-H3 caller-neutral Harness boundary and derive the current Journal schema version from source rather than freezing an obsolete schema number in the documentation contract;
- Host MCP reverse-proxy deployment now accepts one explicit canonical HTTPS public origin while retaining loopback binding and MCP SDK DNS-rebinding protection, preventing authenticated tunnel traffic from being rejected with HTTP 421;
- concurrent Journal reopen/close now hardens the main database and transient WAL/SHM sidecars through no-follow file descriptors outside active SQLite lock ownership, so legitimate sidecar retirement cannot be misclassified as corruption or disturb process-scoped locking;
- the read-only live Runtime script now constructs the current logical `RepositoryRef` and explicit resolver instead of the removed physical `source_repo` plan field;
- a direct regression test now prevents live-script drift from escaping the deterministic suite.

### Compatibility

- **Breaking pre-1.0 cleanup:** old Provider-shaped cognition events/objects/import paths and `[providers]` config are intentionally not decoded or aliased. New deployments use only the H3 semantic cognition schema.
- `PROTOCOL_VERSION`, `ORDIVON_STATELESS_MCP_PROFILE`, and `ORDIVON_SESSION_MCP_PROFILE` retain their original `2025-06-18` compatibility semantics;
- new code can select `DEFAULT_PROTOCOL_VERSION` or `ORDIVON_MODERN_MCP_PROFILE` for the canonical modern lifecycle;
- existing Task/Event/Effect/Dispatch/verification semantics remain owner-compatible, but opening a schema-v4 authority now performs the explicit v4 → v5 migration with a `host.sqlite3.pre-schema-v5.sqlite3` backup;
- migrated extension namespaces remain readable as legacy owner state, but ordinary mutation fails closed until that owner uses `recover_legacy_namespace()` with the exact legacy state digest and a complete replacement state; Host does not infer or reconstruct owner semantics lost before v5;
- external executor and other extension owners remain schema-independent at the semantic layer: Host stores opaque extension bytes and does not acquire a Harness or domain dependency.

### Removed

- the standalone `observation_export` metadata-only Host→Computing observation bridge and its optional-contract test surface: the module was never wired into the CLI, `pyproject` entry points, the public package surface, or the documented capability matrix, depended on an undeclared optional package (`ordivon-observation-core`), and had no Host-internal consumer. Its three standard-environment-skipped tests and its acceptance receipt are removed with it.

## 0.1.2 — Extracted operational prototype

- independent Host repository extracted with history from the Computing incubator;
- schema-v4 Journal/CAS state, migrations, backup/restore, Doctor, leases, exact event edges, and Task projections;
- deterministic read, guarded mutation, version-bound source change, closed-choice cognition, open ActionProposal, DecisionRequest, Goal coordination, extension admission, and conservative recovery slices;
- Harness implementation removed into the independently versioned `ordivon-harness` repository.
