# Changelog

All user-visible changes to Ordivon Host are recorded here. Release and compatibility rules are defined in `docs/RELEASES.md`.

## Unreleased

### Added

- schema-v5 `task_extension_state` durability: each Task × extension namespace can retain one opaque content-addressed owner state independently of the current Task Event head, with `HostExtensionPort.load_namespace()` preserving the current Task projection without importing owner schemas;
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

## 0.1.2 — Extracted operational prototype

- independent Host repository extracted with history from the Computing incubator;
- schema-v4 Journal/CAS state, migrations, backup/restore, Doctor, leases, exact event edges, and Task projections;
- deterministic read, guarded mutation, version-bound source change, closed-choice cognition, open ActionProposal, DecisionRequest, Goal coordination, extension admission, and conservative recovery slices;
- Harness implementation removed into the independently versioned `ordivon-harness` repository.
