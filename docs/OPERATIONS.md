# Host operational contract

## State ownership

The deployable Host state root is a trusted-local private boundary. Host enforces `0700` on state and CAS directories and `0600` on SQLite, WAL/SHM, CAS objects, manifests, and restored files. Symlink state roots, Journals, CAS objects, and token files fail closed.

It contains:

```text
host.sqlite3   schema v3 event journal, Task projection, leases, and CAS validation cache
objects/       immutable content-addressed objects
receipts/      operational and live-scenario receipts
backups/       optional operator-selected backup destinations
```

Schema evolution is explicit:

- v1 → v2 removes the unowned `task_nodes`, `task_edges`, `runtime_links`, and `wakeups` tables. Migration proceeds only when all four tables are empty; populated legacy tables fail closed. The source database is retained as `host.sqlite3.pre-schema-v2.sqlite3`.
- v2 → v3 adds `object_validation`, which binds a previously SHA-256-verified CAS object to its device, inode, length, modification time, change time, and mode. The source database is retained as `host.sqlite3.pre-schema-v3.sqlite3`.

A v1 database advances through both migrations in order and records both entries in `schema_migrations`.

## CAS validation modes

Normal Host startup uses cached validation:

1. missing objects fail immediately;
2. an unchanged file identity reuses the last successful full decode and SHA-256 result;
3. any ordinary write, replacement, truncation, metadata change, or cache miss forces a full decode and SHA-256 check;
4. every materialized Task head is still read and fully verified while rebuilding its projection.

This cache is scoped to the trusted-local filesystem threat model. `ordivon-host doctor` always performs full byte validation of every referenced CAS object, covering bit rot or offline block-level changes that may not be represented by normal file metadata transitions.

## Configuration

The default config path is `/etc/ordivon/host.toml`:

```toml
[state]
root = "/var/lib/ordivon/host"
receipt_root = "/var/lib/ordivon/host/receipts"

[runtime]
endpoint = "http://127.0.0.1:8897/mcp"
token_file = "/etc/ordivon/runtime-mcp.token"
timeout_seconds = 45.0
max_response_bytes = 2097152

[providers]
codex_executable = "codex"
hermes_executable = "hermes"
timeout_seconds = 180

[repositories]
"repository:ordivon-host" = "/root/projects/ordivon-host"
"repository:ordivon-computing" = "/root/projects/ordivon-computing"
```

`ORDIVON_HOST_STATE_ROOT`, `ORDIVON_HOST_RECEIPT_ROOT`, `ORDIVON_MCP_ENDPOINT`, and `ORDIVON_BEARER_TOKEN_FILE` may override non-secret configuration. The bearer token itself is read from a regular non-symlink `token_file` with no group/other permission bits; the CLI exposes no token argument.

## CLI

```text
ordivon-host init
ordivon-host inspect
ordivon-host config show
ordivon-host task list [--state STATE] [--limit N]
ordivon-host task show TASK_ID
ordivon-host task assess TASK_ID
ordivon-host task reconcile TASK_ID [--wait-ms N]
ordivon-host doctor [--runtime] [--history]
ordivon-host backup DESTINATION
ordivon-host verify-backup BACKUP
ordivon-host restore BACKUP [--replace]
ordivon-host gc plan
```

Only `init` creates a missing state root. Read and backup commands reject an absent `host.sqlite3`. `gc plan` is read-only and never deletes objects.

## Backup and restore

A backup is a directory containing:

- a SQLite online backup;
- every CAS object referenced by that database;
- a manifest with exact file digests, object metadata, schema version, and migration history.

Backup verification is full and read-only: it disables validation-cache writes so repeated verification cannot alter a manifest-covered database. Restore verifies all file digests, opens the restored state under full Host invariants, and then atomically renames the verified temporary state into place. `--replace` preserves the old state root under a timestamped `.previous-*` path.

## Doctor

The local doctor checks SQLite integrity, schema compatibility, exact private state modes, Journal invariants including causal-link integrity, full CAS content integrity, orphan objects, and lease state. `--runtime` additionally loads the token from its file and performs MCP initialization.

`--history` decodes every historical Event payload and verifies its row identity, projection revision, known CAS references, and retained Effect/Binding/Authority links. It is intentionally explicit: at 100,000 Events it increased measured Doctor latency from 10.3 seconds to 18.5 seconds, while normal startup and normal Doctor remain unchanged.

`task assess` is local and read-only. `task reconcile` performs at most one conservative step: replayable deterministic Read progress or observation of an already-persisted keyed Runtime Dispatch. Package-scoped experimental Effect lifecycles are reported as manual because executor and domain observation authority are not reconstructible from the generic Host alone. It never creates or redispatches an Effect and never invokes a Provider. Non-automatic Tasks return a no-op without loading Runtime credentials.
