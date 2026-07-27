# Host operational contract

## State ownership

The deployable Host state root contains:

```text
host.sqlite3   schema v2 event journal, Task projection, and leases
objects/       immutable content-addressed objects
receipts/      operational and live-scenario receipts
backups/       optional operator-selected backup destinations
```

Schema v2 removes the unowned `task_nodes`, `task_edges`, `runtime_links`, and `wakeups` tables. Opening a v1 database creates `host.sqlite3.pre-schema-v2.sqlite3` and migrates only when all four legacy tables are empty. Populated legacy tables fail closed.

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
```

`ORDIVON_HOST_STATE_ROOT`, `ORDIVON_HOST_RECEIPT_ROOT`, `ORDIVON_MCP_ENDPOINT`, and `ORDIVON_BEARER_TOKEN_FILE` may override non-secret configuration. The bearer token itself is read from `token_file`; the CLI exposes no token argument.

## CLI

```text
ordivon-host init
ordivon-host inspect
ordivon-host config show
ordivon-host task list [--state STATE] [--limit N]
ordivon-host task show TASK_ID
ordivon-host doctor [--runtime]
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

Restore verifies all file digests, opens the restored state under full Host invariants, and then atomically renames the verified temporary state into place. `--replace` preserves the old state root under a timestamped `.previous-*` path.

## Doctor

The local doctor checks SQLite integrity, schema compatibility, Journal invariants, CAS references, orphan objects, and lease state. `--runtime` additionally loads the token from its file and performs MCP initialization.
