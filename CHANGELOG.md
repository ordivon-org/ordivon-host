# Changelog

All user-visible changes to Ordivon Host are recorded here. Release and compatibility rules are defined in `docs/RELEASES.md`.

## Unreleased

### Added

- caller-neutral `ExternalExecutorAdapter`, durable external request, foreign Run binding, exact response-loss recovery, cancellation/observation reconciliation, and CompletionProposal collection without Task acceptance;
- public Quick Start, status, data/privacy, release, security, contribution, and change-history documents;
- executable documentation ownership and local-link validation;
- pinned CI actions, CodeQL, secret scanning, dependency audit, Dependabot, release acceptance, and read-only live Host→Runtime acceptance;
- explicit modern and legacy Runtime transport profiles.

### Changed

- current cognition writers no longer invoke Providers: `CognitionTurnHost` admits externally executed decisions only after a durable `PreparedInvocation`, and `OpenProposalHost` exposes explicit prepare/admit boundaries instead of `propose(gateway)`;
- Codex/Hermes Host-local physical execution moved behind the explicit `ordivon_host.legacy_provider_execution` compatibility namespace; current `ordivon_host.cognition` and `ordivon_host.providers` no longer advertise it;
- `ProviderSettings` was removed from current `HostConfig` and package-root discovery. Retained `[providers]` config is validated then ignored until compatibility cleanup;
- cognition recovery now reports `external-cognition-required` rather than instructing Host to `invoke-provider`.
- the package root now exposes durable Host authority and cross-owner boundary types only; deterministic read, guarded mutation, and code-change workloads remain available explicitly from `ordivon_host.engine`;
- canonical live workload scripts now import their workload implementations from `ordivon_host.engine`, keeping the default Host surface responsibility-oriented without deleting the proven workloads;
- the default Runtime transport now uses the stateless MCP `2026-07-28` `server/discover` lifecycle with per-request metadata, `Mcp-Method`, and `Mcp-Name`;
- the retained MCP `2025-06-18` Session lifecycle is now an explicit compatibility profile rather than the default;
- development documentation now distinguishes immutable online installation from a local sibling Protocol checkout;
- canonical document ownership now includes status, data/privacy, release, and Quick Start responsibilities.

### Fixed

- the read-only live Runtime script now constructs the current logical `RepositoryRef` and explicit resolver instead of the removed physical `source_repo` plan field;
- a direct regression test now prevents live-script drift from escaping the deterministic suite.

### Compatibility

- retained cognition event kinds, `ModelInvocationIntent`/Observation/Receipt codecs, compiled Context objects, decisions, proposals, and historical CAS edges are unchanged and remain reopenable;
- deprecated module paths `ordivon_host.providers.gateway`, `ordivon_host.cognition.adapters`, and `ordivon_host.cognition.proposal_adapters` remain import shims to the explicit legacy Provider execution namespace for the pre-1.0 observation window.
- `PROTOCOL_VERSION`, `ORDIVON_STATELESS_MCP_PROFILE`, and `ORDIVON_SESSION_MCP_PROFILE` retain their original `2025-06-18` compatibility semantics;
- new code can select `DEFAULT_PROTOCOL_VERSION` or `ORDIVON_MODERN_MCP_PROFILE` for the canonical modern lifecycle;
- existing durable Task, Journal, CAS, schema, Effect, Dispatch, verification, and recovery objects are unchanged;
- external executor state uses extension Events and immutable CAS objects, requiring no Host Journal schema migration and no Harness dependency.

## 0.1.2 — Extracted operational prototype

- independent Host repository extracted with history from the Computing incubator;
- schema-v4 Journal/CAS state, migrations, backup/restore, Doctor, leases, exact event edges, and Task projections;
- deterministic read, guarded mutation, version-bound source change, closed-choice cognition, open ActionProposal, DecisionRequest, Goal coordination, extension admission, and conservative recovery slices;
- Harness implementation removed into the independently versioned `ordivon-harness` repository.
