# Security Policy

## Reporting a vulnerability

Do not open a public Issue, Discussion, or pull request for a suspected vulnerability.

Use GitHub private vulnerability reporting:

- `https://github.com/zycxfyh/ordivon-host/security/advisories/new`

Include the affected commit or package version, reproduction steps, expected and observed authority boundaries, persisted-state impact, Runtime or Provider involvement, and whether Tasks, decisions, credentials, source, evidence, or external effects may be exposed.

The maintainer aims to acknowledge a complete report within three business days. Validation, remediation, release, and disclosure timing depend on severity and on preserving migration, recovery, or forensic evidence. Coordinate public disclosure through the private advisory until a fix or explicit disclosure decision is ready.

If private reporting is unavailable, open a public Issue containing no vulnerability details and request a private channel. Do not place secrets, database files, CAS objects, receipts, or exploit material in a public Issue.

## Supported version

Only current `main` and the exact package or deployment revision currently used by the operator are supported. There are no LTS or backport branches. Older schema objects remain security-relevant when they participate in current migration, replay, recovery, verification, or audit.

## Security boundary

Ordivon Host is a trusted-local coordination and commitment plane. It owns durable Task continuity, Journal/CAS state, commitments, uncertainty, evidence references, verification admission, and Task outcomes.

Host does not sandbox Provider processes, Runtime commands, Git repositories, or domain systems. Runtime owns physical execution and process isolation. Harness owns Agent Run and Provider-loop semantics. Domain systems own authoritative world state and domain verification.

A Host decision or Runtime success is not automatically proof that a user's objective is complete. Completion requires the workload-specific evidence and verification boundary encoded by the current Host state machine.

## Network and transport

The default Runtime client uses the stateless MCP `2026-07-28` `server/discover` lifecycle with per-request metadata and method identity. The retained `2025-06-18` Session profile is an explicit compatibility decoder, not the preferred path. Transport Session identity is never durable Task truth.

Keep the Runtime endpoint loopback-bound or behind an operator-owned authenticated tunnel. Bearer tokens must be loaded from regular, non-symlink files with no group or other permission bits. Do not pass tokens on the command line or persist them in Task, Context, Effect, Dispatch, receipt, or CAS payloads.

Host MCP is separately authenticated and loopback-only by construction. Its bearer token is independent from the Runtime token, is loaded only from a private regular file, and must not be reused as Runtime, Provider, Cloudflare, or repository credentials. Do not expose port 8898 directly to an untrusted network; if remote access is required, terminate it through an operator-owned authenticated tunnel and keep the Host listener on loopback. Configure the exact HTTPS `public_origin` so the MCP SDK admits that proxy Host/Origin without disabling DNS-rebinding protection. MCP client/session/HTTP identities are transport metadata and never become Task authority.

## Sensitive data

Host may retain:

- Goals, Task descriptors, Contexts, decisions, proposals, DecisionRequests, Effects, Dispatch identities, observations, verification receipts, and outcomes;
- participant, repository, Runtime Job, Assignment, and external-resource references;
- source-derived content and model-produced text placed in CAS objects;
- migration, backup, recovery, Doctor, handoff, and live-acceptance receipts.

Host does not automatically classify or redact personal data, secrets, proprietary source, or model output. Treat the complete state root, backup, evidence set, and operator receipt directory as sensitive.

See [`docs/DATA_AND_PRIVACY.md`](docs/DATA_AND_PRIVACY.md).

## Security process

Security is enforced through exact Task revisions, lease fencing, append-only events, immutable CAS identities, schema migrations with backups, private filesystem modes, strict object decoding, explicit capability and consequence admission, durable Dispatch identity before external delivery, conservative UNKNOWN handling, independent verification, no blind redispatch, secret scanning, dependency audit, CodeQL, deterministic tests, and read-only live Host→Runtime acceptance.

A passing scan or test is not a security guarantee. Reassess the threat model whenever persistence, authority, transport, Provider, Runtime, domain verification, or external-effect ownership changes.
