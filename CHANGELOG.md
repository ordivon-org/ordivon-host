# Changelog

All user-visible changes to Ordivon Host are recorded here. Release and compatibility rules are defined in `docs/RELEASES.md`.

## Unreleased

### Added

- public Quick Start, status, data/privacy, release, security, contribution, and change-history documents;
- executable documentation ownership and local-link validation;
- pinned CI actions, CodeQL, secret scanning, dependency audit, Dependabot, release acceptance, and read-only live Host→Runtime acceptance;
- explicit modern and legacy Runtime transport profiles.

### Changed

- the default Runtime transport now uses the stateless MCP `2026-07-28` `server/discover` lifecycle with per-request metadata, `Mcp-Method`, and `Mcp-Name`;
- the retained MCP `2025-06-18` Session lifecycle is now an explicit compatibility profile rather than the default;
- development documentation now distinguishes immutable online installation from a local sibling Protocol checkout;
- canonical document ownership now includes status, data/privacy, release, and Quick Start responsibilities.

### Fixed

- the read-only live Runtime script now constructs the current logical `RepositoryRef` and explicit resolver instead of the removed physical `source_repo` plan field;
- a direct regression test now prevents live-script drift from escaping the deterministic suite.

### Compatibility

- `PROTOCOL_VERSION`, `ORDIVON_STATELESS_MCP_PROFILE`, and `ORDIVON_SESSION_MCP_PROFILE` retain their original `2025-06-18` compatibility semantics;
- new code can select `DEFAULT_PROTOCOL_VERSION` or `ORDIVON_MODERN_MCP_PROFILE` for the canonical modern lifecycle;
- existing durable Task, Journal, CAS, schema, Effect, Dispatch, verification, and recovery objects are unchanged.

## 0.1.2 — Extracted operational prototype

- independent Host repository extracted with history from the Computing incubator;
- schema-v4 Journal/CAS state, migrations, backup/restore, Doctor, leases, exact event edges, and Task projections;
- deterministic read, guarded mutation, version-bound source change, closed-choice cognition, open ActionProposal, DecisionRequest, Goal coordination, extension admission, and conservative recovery slices;
- Harness implementation removed into the independently versioned `ordivon-harness` repository.
