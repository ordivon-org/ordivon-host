---
schema_version: 1
id: host.data-privacy
title: Host Data and Privacy
type: policy
profile: organization
lifecycle: active
source_role: canonical
visibility: public
owners:
  - ordivon-host
audience:
  - operator
  - user
  - builder
  - agent
updated: 2026-08-04
summary: Data inventory, sensitivity, retention, export, migration, deletion, and redaction boundaries for Host state.
evidence_status: verified
readiness: READY
applies_to:
  - ordivon-host
related:
  - host.start
  - host.quickstart
  - host.operations
  - host.releases
---
# Host Data and Privacy

## Principle

Host preserves durable work so that model sessions and Runtime processes can be replaced without losing Task continuity. That continuity can contain sensitive user, source, participant, and decision information. Treat the full state root and every backup, evidence file, or receipt as sensitive.

Host does not automatically detect or redact credentials, personal data, proprietary source, model output, participant identities, or external references.

## Data inventory

| Data | Typical location | Purpose | Lifecycle |
| --- | --- | --- | --- |
| `host.sqlite3` | configured state root | Events, projections, leases, object references, migrations, validation metadata | authoritative while the instance is active |
| immutable CAS objects | `objects/` below state root | Task descriptors, Contexts, proposals, decisions, Effects, bindings, observations, verification, outcomes, extension objects | retained while referenced by authoritative history |
| migration backups | state root | recover pre-migration database bytes | retained until migration and rollback policy permits explicit deletion |
| operator receipts | configured receipt root or explicit output | backup, restore, Doctor, acceptance, handoff, and administrative evidence | operator policy; inspect before sharing |
| backups | operator-selected directory | complete recovery snapshot | operator-owned; verification required before reliance |
| repository evidence | `evidence/` | immutable historical engineering receipts | repository history; must contain no live secrets |

A specific deployment's configuration and filesystem are authoritative for exact paths.

## Sensitive content

Host state may include:

- Goal and Task text;
- Context blocks and provenance;
- Provider proposals and decisions;
- participant and ownership references;
- repository identities and revisions;
- source-derived content retained for verification;
- Effect, Dispatch, Runtime Job, Assignment, and external-resource identifiers;
- failure, uncertainty, recovery, and approval records;
- extension-defined objects whose schema Host deliberately does not interpret.

The generic Host Doctor verifies extension bytes and references but cannot classify their privacy sensitivity.

## Access control

The canonical trusted-local path enforces:

- state directories with mode `0700`;
- database, objects, token files, and sensitive receipts with mode `0600` where created by Host;
- regular non-symlink token files with no group or other permission bits;
- no bearer-token CLI argument;
- no durable transport Session identity;
- no automatic remote exposure.

These controls do not protect data from root, the Host service account, filesystem administrators, or code deliberately executed with equivalent authority.

## Retention

Journal history and referenced CAS objects are append-oriented because replay, audit, recovery, idempotency, and migration depend on exact prior bytes. The current `gc plan` command is read-only and does not delete objects.

Do not delete individual Events, projections, leases, object-reference rows, migration metadata, or referenced CAS files manually. Selective erasure would require a new versioned archival or redaction contract that preserves every still-required identity and edge.

## Export and migration

A complete export consists of a verified backup created by the Host CLI. It includes:

- SQLite online backup;
- every referenced CAS object;
- exact digests and object metadata;
- schema version and migration history;
- a manifest covering every exported file.

Verify before transfer or restore:

```bash
ordivon-host --state-root /var/lib/ordivon/host backup DESTINATION
ordivon-host verify-backup DESTINATION
```

Restore verifies bytes and full Host invariants before atomically installing the new state. `--replace` preserves the previous state root under a timestamped name.

Cross-host migration must also preserve or deliberately replace repository mappings, Runtime endpoint, token path, Provider executable configuration, file ownership, and external references. Copying bytes alone does not prove operational continuity.

## Deletion

### Disposable development state

Stop all writers, verify the state is not authoritative, preserve required receipts, and delete the complete state root. Do not partially prune it.

### Backup deletion

Delete only after another verified recovery copy exists or retention policy permits permanent loss. A backup may contain all sensitive Host history.

### Instance retirement

1. stop new Task and extension admission;
2. resolve or explicitly classify active leases and nonterminal Tasks;
3. export required state and evidence;
4. stop Host processes and disable automation;
5. remove the configured state and receipt roots;
6. remove configuration and token files;
7. rotate Runtime and Provider credentials that could still authorize access;
8. review external domain systems for retained references or effects.

Instance retirement cannot retract effects already committed in Runtime or domain systems.

## Sharing diagnostics

Before sharing inspect, Doctor, history, backup, handoff, acceptance, or evidence output:

- remove absolute paths, participant references, repository identities, Task text, proposal content, external IDs, and source-derived bytes unless required;
- never publish bearer tokens, config secrets, raw databases, CAS directories, migration backups, or complete operational backups;
- prefer compact health summaries over raw state;
- preserve cryptographic digests only when they do not reveal a sensitive lookup key.

## Privacy non-goals

Host does not currently provide encryption at rest, per-user tenancy, legal hosted-service privacy terms, automatic PII classification, selective history deletion, content redaction, credential brokering, or protection from a privileged local operator. A hosted product must define those responsibilities above this repository.
