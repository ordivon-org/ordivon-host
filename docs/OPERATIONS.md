---
schema_version: 1
id: host.operations
title: Host operational contract
type: operations
profile: engineering
lifecycle: active
source_role: canonical
visibility: public
owners:
  - ordivon-host
audience:
  - operator
  - builder
  - agent
updated: 2026-08-08
summary: Canonical operational contract for Host state ownership, external semantic continuity, configuration, validation, backup, restore, Doctor, and conservative reconciliation.
evidence_status: verified
readiness: READY
applies_to:
  - ordivon-host
related:
  - host.quickstart
  - host.status
  - host.architecture
  - host.data-privacy
  - host.releases
  - host.authority
---
# Host operational contract

## Scope

This document owns the trusted-local Host state root, schema migration, configuration, CLI, CAS validation, backup, restore, Doctor, and bounded Task assessment or reconciliation behavior.

Host operations stop at generic Task and storage continuity. They do not interpret Harness Assignment/Run objects, resume a model–Tool loop, cancel an in-memory Provider call, supervise Runtime processes, or decide domain-world truth. Use `ordivon-harness` for Harness semantic Doctor and Run recovery, and `ordivon-runtime` for physical Job, process, Artifact, and Workspace recovery.

## Normal operation

Operate one private Host state root through the installed CLI, short Journal transactions, immutable CAS writes, exact permissions, cached startup validation, explicit configuration, and read-only inspection before any repair or restore action.

## Failure detection

Treat SQLite integrity failure, schema incompatibility, unsafe file modes or symlinks, missing or corrupt CAS objects, Journal or projection drift, causal gaps, orphan evidence, unresolved leases, and Runtime initialization failure as explicit operational findings.

## Recovery

Recover through verified backups, atomic restore, full-history Doctor when required, and conservative Task reconciliation. Reconciliation may replay deterministic read progress or observe an already-persisted keyed Dispatch; it never invents or redispatches an uncertain Effect.

## Verification

Use `ordivon-host doctor`, optional `--history` and `--runtime` checks, backup verification, schema migration tests, deterministic repository tests, and retained live receipts. [`../ARCHITECTURE.md`](../ARCHITECTURE.md) defines semantic ownership; this document defines operational handling.

## State ownership

The deployable Host state root is a trusted-local private boundary. Host enforces `0700` on state and CAS directories and `0600` on SQLite, WAL/SHM, CAS objects, manifests, and restored files. Symlink state roots, Journals, CAS objects, and token files fail closed.

It contains:

```text
host.sqlite3   schema v4 event journal, Task projection, leases, event-object references, and CAS validation cache
objects/       immutable content-addressed objects
receipts/      operational and live-scenario receipts
backups/       optional operator-selected backup destinations
```

Schema evolution is explicit:

- v1 → v2 removes the unowned `task_nodes`, `task_edges`, `runtime_links`, and `wakeups` tables. Migration proceeds only when all four tables are empty; populated legacy tables fail closed. The source database is retained as `host.sqlite3.pre-schema-v2.sqlite3`.
- v2 → v3 adds `object_validation`, which binds a previously SHA-256-verified CAS object to its device, inode, length, modification time, change time, and mode. The source database is retained as `host.sqlite3.pre-schema-v3.sqlite3`.
- v3 → v4 adds `event_object_refs`, a unique payload-reference constraint, a legacy-object reference set, and the sequence boundary from which every newly admitted Event must bind its payload/reference objects explicitly. The source database is retained as `host.sqlite3.pre-schema-v4.sqlite3`.

A v1 database advances through all migrations in order and records every transition in `schema_migrations`.

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

[repositories]
"repository:ordivon-host" = "/root/projects/ordivon-host"
"repository:ordivon-computing" = "/root/projects/ordivon-computing"
```

The `[runtime]` table configures the canonical modern MCP client. Host uses `2026-07-28` `server/discover`, verifies Runtime support, sends per-request client metadata, and binds method or Tool identity through `Mcp-Method` and `Mcp-Name`. The retained `2025-06-18` Session profile is available only when explicitly selected in code; configuration does not silently downgrade the lifecycle.

`[providers]` is not a Host configuration surface. Provider and model configuration belongs to the cognition executor, normally `ordivon-harness`; a `[providers]` table now fails as an unsupported Host config field.

`ORDIVON_HOST_STATE_ROOT`, `ORDIVON_HOST_RECEIPT_ROOT`, `ORDIVON_MCP_ENDPOINT`, and `ORDIVON_BEARER_TOKEN_FILE` may override non-secret configuration. The bearer token itself is read from a regular non-symlink `token_file` with no group/other permission bits; the CLI exposes no token argument.

## CLI

```text
ordivon-host init
ordivon-host inspect
ordivon-host config show
ordivon-host task list [--state STATE] [--limit N]
ordivon-host task show TASK_ID
ordivon-host task handoff TASK_ID [--expected-revision N]
ordivon-host task adopt TASK_ID GOAL_ID --checkpoint-file CHECKPOINT.json
ordivon-host task resume TASK_ID [--expected-revision N]
ordivon-host task checkpoint TASK_ID --expected-revision N --checkpoint-file CHECKPOINT.json
ordivon-host task assess TASK_ID
ordivon-host task reconcile TASK_ID [--wait-ms N]
ordivon-host doctor [--runtime] [--history]
ordivon-host backup DESTINATION
ordivon-host verify-backup BACKUP
ordivon-host restore BACKUP [--replace]
ordivon-host gc plan
```

Only `init` should bootstrap a missing authority root. Run it once before concurrent CLI or future MCP consumers; concurrent first-time SQLite schema creation is not an authority-coordination mechanism. Read and backup commands reject an absent `host.sqlite3`. `gc plan` is read-only and never deletes objects.

### External continuity workflow

`task adopt`, `task resume`, and `task checkpoint` are local semantic-continuity operations. They do not load Runtime credentials, invoke Runtime, invoke Harness, call a Provider, start a scheduler, or infer ChatGPT session state.

The checkpoint file is one exact `ordivon.host-working-checkpoint` JSON object. It is intentionally bounded and self-identifies as `truthRole: semantic-working-claim`; do not put raw conversation transcripts, chain-of-thought, or copied Runtime truth into it. Store references such as `workspaceId`, relevant Job identities, and the last observed Git head only as navigation hints, then revalidate them against the owning authority after `task resume`.

A typical local sequence is:

```bash
ordivon-host --state-root /var/lib/ordivon/host \
  task adopt task:project:work goal:project \
  --checkpoint-file checkpoint.json

ordivon-host --state-root /var/lib/ordivon/host \
  task resume task:project:work

ordivon-host --state-root /var/lib/ordivon/host \
  task checkpoint task:project:work --expected-revision 2 \
  --checkpoint-file checkpoint-next.json
```

On a lost checkpoint response, retry the same checkpoint with the original expected revision. If that exact transition already became the current revision, Host returns `admission: existing`; a different claim fails closed.

### Deployment identity

`ordivon-host deployment` projects the exact installed source revision from the deployment-owned `current` symlink and immutable `releases/<releaseId>/COMMIT` marker. `releaseId` is a physical release identity and is deliberately independent from the Git Commit recorded in `COMMIT`; a source revision alone does not identify its resolved dependency graph, Python/uv build toolchain, or installed bytes. The command does not infer deployment state from a Git checkout, package version, process age, or Host Journal state. Use `--release-root` only when inspecting a nonstandard installation root.

### Receipt-bound local deployment

`scripts/ordivon-host-deploy` owns the canonical local release transition. Deployment state is not stored in the Host Journal: immutable release directories and private deployment receipts are an operational authority separate from Task semantics. The source tree must contain an up-to-date `uv.lock`; the build backend is exactly pinned; `prepare` verifies that lock offline, materializes the requested Git Commit in a clean detached checkout, records exact Python and uv executable digests, builds the Host wheel, and constructs a `uv --relocatable` virtual environment from the frozen dependency graph. It removes transient bytecode and build-path metadata, installs a release-local Python site policy that prevents future bytecode writes, and then hashes the complete release tree. This keeps direct CLI execution and service startup from mutating receipted release bytes.

The physical identity is `releaseId = <sourceCommit>-<effectiveDigestPrefix>`, where `effectiveDigest` commits to both the complete release tree and the complete production Python runtime tree reached by `venv/bin/python`. The release-tree digest binds file bytes, modes, directory modes, and symlink targets. The shared production Python runtime must be one versioned child of `/usr/local/libexec/ordivon/python/`; its executable bytes and entire runtime tree are independently bound in the candidate and receipt. A development interpreter under `/root` is therefore not a valid production dependency even when it has the same Python version string.

A versioned directory is not sufficient by itself. Before deployment schema v3 admits a Python runtime, every `lib/pythonX.Y/**/*.py` source must have exactly one canonical `__pycache__/<stem>.<cacheTag>.pyc`, every bytecode file must use PEP 552 checked-hash invalidation (`flags == 3`), the stored source hash must match the source bytes, the top-level code object's `co_filename` must equal the source's final runtime path, and there must be no additional stale `.pyc` files. The validator runs the candidate interpreter with `-B`, so validation cannot create the cache it is trying to prove. This converts Python's normally lazy bytecode cache into an already-materialized part of the immutable execution substrate. A runtime with missing or timestamp-mode cache files is rejected even if its source, executable, and current tree digest are otherwise readable.

The local stable CPython generation is materialized into a new final versioned path rather than rewriting an in-use runtime. Staging compilation uses checked-hash bytecode with the final path projected into `co_filename`; the directory is then atomically renamed into the shared runtime namespace and exercised without `PYTHONDONTWRITEBYTECODE` before it is admitted. Materialization evidence is kept separately under `/var/lib/ordivon/python/materializations/`; Host consumes and independently validates the resulting runtime but still does not own shared-runtime retirement authority.

`plan` independently re-reads `uv.lock` from the requested Git Commit, requires the configured releasable Git ref to resolve to that Commit, validates the candidate tree, the resolved production Python runtime tree, and its complete checked-hash materialization policy against the manifest, requires the current Host release to provide an exact rollback target, and requires the Host MCP service to be active.

A normal local deployment is:

```bash
repo=/root/projects/ordivon-host
commit=$(git -C "$repo" rev-parse HEAD)
python=/usr/local/libexec/ordivon/python/cpython-3.12.13-ordivon-pyc1-linux-x86_64-gnu/bin/python3.12

prepare=$(scripts/ordivon-host-deploy prepare \
  --source-repo "$repo" \
  --commit "$commit" \
  --python "$python")
release_id=$(printf '%s' "$prepare" | python3 -c 'import json,sys; print(json.load(sys.stdin)["releaseId"])')
candidate_dir=$(printf '%s' "$prepare" | python3 -c 'import json,sys; print(json.load(sys.stdin)["candidateDir"])')

scripts/ordivon-host-deploy plan \
  --source-repo "$repo" \
  --commit "$commit" \
  --candidate-dir "$candidate_dir" \
  --require-ref refs/heads/main \
  --pretty

scripts/ordivon-host-deploy apply \
  --source-repo "$repo" \
  --commit "$commit" \
  --candidate-dir "$candidate_dir" \
  --require-ref refs/heads/main \
  --confirm-release-id "$release_id"

scripts/ordivon-host-deploy status --json
```

`apply` copies the verified candidate into an immutable `releases/<releaseId>` directory, atomically replaces only the `current` symlink, restarts `ordivon-host-mcp.service`, and requires an authenticated modern `2026-07-28` MCP `server/discover` plus the exact six-Tool catalog (`host.status`, `task.observe`, `task.list`, `task.resume`, `task.adopt`, `task.checkpoint`). A failed activation or probe restores the exact previous symlink target, restarts and probes it, and receipts that automatic restoration. The global `/usr/local/bin/ordivon-host*` launchers intentionally resolve through `current/venv/bin/python -m ...`; relocatable virtual environments additionally make their own console scripts safe after staging moves.

Receipts live under `/var/lib/ordivon/host/deployments/`. `status` verifies the current physical tree against the latest successful deployment or rollback event. An older pre-H-A1 release may legitimately appear as `unreceipted`; that is explicit legacy state rather than invented provenance. Explicit rollback requires the original deployment receipt and exact previous `releaseId` confirmation:

```bash
scripts/ordivon-host-deploy rollback \
  --receipt /var/lib/ordivon/host/deployments/<receipt> \
  --confirm-release-id <previousReleaseId> \
  --pretty
```

Rollback verifies the previous tree against the receipt before switching `current`; it then restarts and authenticates the same MCP surface. A rollback failure attempts to recover the displaced release.

### Release lifecycle and garbage collection

Release lifecycle is derived from execution semantics rather than wall-clock age. `scripts/ordivon-host-deploy gc-plan` computes one exact minimal reversible frontier and never deletes anything. Collection is allowed only while deployment status is `healthy`; malformed or unresolved deployment receipts, a missing recovery candidate, a changed rollback peer, or a changed shared Python runtime blocks the plan.

The retained execution frontier is direction-aware:

- after a successful deployment, retain the current release, the previous release needed for one exact rollback, and the current candidate needed to re-apply that release after rollback;
- after an explicit rollback, retain the restored current release, the displaced release, and the displaced release's candidate so the rollback can be reversed;
- after an automatic rollback caused by a failed deployment, do not promote the failed release or candidate into the recovery frontier. When available, recover the last successful deployment authority for the restored current release instead;
- a current-schema candidate that has never reached a terminal deployment receipt is retained as unconsumed prepared work. It is not garbage merely because it is not active;
- obsolete-schema candidates and terminal candidates outside the reversible frontier are collectible;
- non-current releases outside the reversible frontier are collectible even when historical receipts mention them. Receipts preserve evidence; they do not permanently pin executable bytes.

Deployment receipts under `/var/lib/ordivon/host/deployments/` are evidence-retained and are never automatically removed by Host lifecycle GC. Their exact directory trees participate in the lifecycle plan digest, so receipt drift between planning and application invalidates the plan.

The shared Python tree under `/usr/local/libexec/ordivon/python/` is **not Host-owned lifecycle state**. Host records exact retention claims for every production Python runtime required by the current reversible frontier, verifies those runtime trees, and reports `deletionAuthority = not_host`. Host GC never provisions, aliases, retires, or deletes those shared runtimes. A future shared execution-substrate owner must combine retention claims from all consumers before retiring one.

### External substrate retention projection

`scripts/ordivon-host-deploy substrate-claims` is the Host-owned observation surface for that boundary. It derives claims directly from the current receipt-bound lifecycle graph; it does not create a second claim database or persist another source of truth. A healthy projection contains one content-bound claim per distinct external runtime root and preserves every current reason that keeps the runtime reachable, such as `current_release`, `reversible_transition_peer`, or `recovery_candidate`. If lifecycle reconstruction is blocked, the command returns `status = blocked` and no claims rather than publishing a partial retention view.

```bash
scripts/ordivon-host-deploy substrate-claims --pretty
```

The projection is deliberately Host-local. `absence_is_not_deletion_authority`: a generation omitted from Host claims is only proven unnecessary to the current Host reversible frontier. It is not thereby safe to delete because another product, process, operator workflow, or machine-level alias may still depend on it. Likewise, `deletionAuthority = not_host` is a boundary statement, not a request for a new Host subsystem.

H-A3 found no second production consumer of the Ordivon-managed CPython generation: Runtime's target execution plane resolves its explicit `ORDIVON_EXEC_PATH` independently, while Harness and Computing currently use their own uv/mise development environments. Therefore the substrate-claim shape remains an owner-native Host projection rather than a promoted `ordivon-protocol` object, shared registry, daemon, or new repository. Promotion requires a second materially different production consumer whose independent retention semantics demonstrate recurring cross-owner value. Provisioning should prefer mature lower owners such as uv when practical; Ordivon should retain only the materialization proof and consumer-specific retention semantics that those tools do not provide.

A lifecycle plan is deterministic and digest-bound:

```bash
plan=$(scripts/ordivon-host-deploy gc-plan)
plan_digest=$(printf '%s' "$plan" | python3 -c 'import json,sys; print(json.load(sys.stdin)["planDigest"])')
printf '%s\n' "$plan" | python3 -m json.tool

scripts/ordivon-host-deploy gc-apply \
  --confirm-plan-digest "$plan_digest" \
  --pretty
```

`gc-apply` acquires the same deployment lock used by activation and candidate final admission, then recomputes the plan. Any inventory or authority drift changes the digest and fails closed. The apply path first persists the exact plan under `/var/lib/ordivon/host/lifecycle/<planDigest>/`, verifies every target tree, and atomically renames each collectible object out of its canonical namespace into a plan-scoped tombstone. Only after logical retirement is receipted does it remove tombstone bytes. A crash after retirement cannot make an old release active again; a retry with the same plan digest can reconcile an existing tombstone or an already-collected object from `retire-result.json`. A completed `result.json` is replay-idempotent.

Candidate construction itself does not hold the deployment lock while resolving and building dependencies. Only final candidate admission is serialized with deployment/GC, so long builds do not block Host service transitions while a candidate cannot race with lifecycle deletion at publication time.

### Host MCP service

H-C2 projects Host authority over a small Agent-native MCP endpoint with six Tools. Four are read-only: `host.status`, `task.observe`, `task.list`, and `task.resume`. Two mutate only external-continuity state: `task.adopt` and `task.checkpoint`. Each request reopens Host authority rather than persisting transport session state; the MCP server does not own a second Task store, Runtime connection, Harness Run, scheduler, or Provider session.

`host.status` is the compact top-level observation entry. `detail=summary` returns Journal/schema/task counts, continuity counts, bounded recent Task activity, and deployment identity only when the running MCP module is actually loaded from the installed current release. `detail=integrity` adds the existing full local Host Doctor; `detail=history` additionally validates retained Event history. It never proxies Runtime. The result reports MCP surface version/tool names so an Agent can distinguish server capabilities from a stale client-side Tool-schema cache.

`task.observe` is the compact per-Task observation entry for any Host Task. It returns the exact projection, workload identity, current head metadata, bounded recent Event timeline, handoff capsule, and either continuity summary or Host recovery assessment. It omits raw Event payload data and never invokes Runtime/Harness/Provider. Use `task.resume` only when full external-continuity WorkingCheckpoint content is actually required.

`task.list` defaults to active `ordivon.host.external-continuity.v1` Tasks only; unrelated Host workloads cannot consume the external Agent's discovery window. Results are ordered by immutable Task creation identity and paginated with an opaque cursor bound to the exact `goalId` and `includeTerminal` query scope. Reusing a cursor under another scope fails closed. Each row includes the exact current Task projection and a bounded semantic selection summary: objective/frontier previews are independently limited to 512 UTF-8 bytes with explicit truncation flags, while checkpoint revision/digest allow the Agent to open the full revision-coherent checkpoint with `task.resume`. Terminal continuity is hidden by default and can be inspected with `includeTerminal=true`.

The `task.adopt.initialCheckpoint` and `task.checkpoint.checkpoint` Tool inputs publish the complete WorkingCheckpoint JSON shape in MCP discovery, including the distinction between semantic working claim and optional physical navigation hint. That schema guides Agent generation only: the handler still receives raw JSON and the canonical `WorkingCheckpoint.from_dict` decoder remains admission authority. This avoids an MCP-SDK validation path that would otherwise bypass Host's structured error contract. Request errors that Host can attribute to one Tool field report that field together with `INVALID_ARGUMENT`, `retryClass=fix_request`, and `commitState=not_committed`.

`task.checkpoint` accepts either a complete `ordivon.host-working-checkpoint` or a revision-bound patch in the same `checkpoint` argument. Patch keys are limited to `objective`, `frontier`, `established`, `unresolved`, `rejected`, `constraints`, `nextActions`, and `runtime`; omitted fields are inherited from the exact `expectedRevision`, while present fields replace that complete field value. Host reconstructs and validates a complete canonical WorkingCheckpoint before Journal admission, so patching changes transport ergonomics rather than durable semantics. The same call accepts `continuityDisposition=continue|complete|abandon`, defaulting to `continue`. `complete` and `abandon` terminate only Host continuity tracking; they do not assert external domain success or failure. Exact retry after response loss converges for both full and patched checkpoints.

Bootstrap the authority once, then provision an independent Host MCP token:

```bash
ordivon-host --state-root /var/lib/ordivon/host init
install -d -m 0700 /etc/ordivon
python - <<'PY' | install -m 0600 /dev/stdin /etc/ordivon/host-mcp.token
import secrets
print(secrets.token_urlsafe(48))
PY

ORDIVON_HOST_STATE_ROOT=/var/lib/ordivon/host \
ORDIVON_HOST_MCP_TOKEN_FILE=/etc/ordivon/host-mcp.token \
  ordivon-host-mcp --check
```

The default endpoint is `http://127.0.0.1:8898/mcp`. `ordivon-host-mcp` accepts only a literal loopback bind address. The bearer token must be in a regular non-symlink file with no group/other permission bits and at least 32 characters; the token value has no CLI or direct environment-variable form. This token is separate from the Runtime MCP bearer token.

When a reverse proxy or tunnel presents a different public Host header, set one canonical HTTPS origin with `ORDIVON_HOST_MCP_PUBLIC_ORIGIN` (or `--public-origin`), for example `https://host-mcp.ordivon.com`. This does **not** change the listener: Host MCP remains loopback-only. It extends the pinned MCP SDK's DNS-rebinding allowlist with exactly that public Host and Origin while retaining the local Host/Origin allowlist. Do not disable DNS-rebinding protection merely to make a proxy work.

For the canonical Cloudflare deployment, `ORDIVON_HOST_MCP_TRUST_CF_ACCESS=true` may be enabled **only after** the public origin is protected by an operator-owned Cloudflare Access self-hosted application and remains reachable at origin only through the loopback-bound Cloudflare Tunnel path. In that mode Host accepts either its independent static Bearer token or a non-empty `Cf-Access-Jwt-Assertion` injected after Cloudflare Access admission. This mirrors the deployed Runtime remote-auth boundary; Host does not treat arbitrary proxy headers as authority when the flag is false.

The canonical deployment templates are `packaging/systemd/ordivon-host-mcp.service` and `packaging/systemd/ordivon-host-mcp.env.example`. The unit runs `ordivon-host-mcp --check` before starting and gives the service write access only to `/var/lib/ordivon/host` under its filesystem hardening profile.

Transport behavior is deliberately stateless. The preferred MCP lifecycle is `2026-07-28`; the pinned official SDK also accepts the tested `2025-11-25` initialize/initialized/tools lifecycle without turning MCP Session identity into Host state. Request bodies are bounded to 1 MiB by default, including unauthenticated requests.

A dropped HTTP response does not create a second replay protocol. After uncertain `task.checkpoint`, call `task.resume`; if the intended revision committed, replaying the identical checkpoint and continuity disposition with its original `expectedRevision` returns `admission: existing`. This includes a final `complete`/`abandon` transition. A competing different writer receives `TASK_BUSY` or `REVISION_CONFLICT`. `task.resume` binds its checkpoint to the exact returned Task revision, so a concurrent checkpoint cannot produce an impossible mixed-revision recovery view.

Tool failures use MCP `isError=true` plus a structured error object carrying `code`, `field` when attribution is exact, `retryable`, `retryClass`, `commitState`, and `origin=host-mcp`. Python tracebacks are not returned to the Agent.

## Read-only live acceptance

Portable tests use deterministic fake Runtime clients. The explicit live gate proves the current Host client, modern Runtime transport, catalog binding, durable read Task, independent verification, and Workspace closure against a reachable Runtime:

```bash
ORDIVON_BEARER_TOKEN_FILE=/etc/ordivon/runtime-mcp.token \
ORDIVON_MCP_ENDPOINT=http://127.0.0.1:8897/mcp \
  scripts/local-acceptance run
```

The journey reads a tracked file from an exact source revision through a disposable Runtime Workspace and emits a digest-bound JSON receipt. It invokes no Provider and performs no source mutation. A transport, catalog, Runtime-read, verification, or recovery change is not release-ready until this gate passes on the supported local environment.

## Backup and restore

A backup is a directory containing:

- a SQLite online backup;
- every CAS object referenced by that database;
- a manifest with exact file digests, object metadata, schema version, and migration history.

Backup verification is full and read-only: it disables validation-cache writes so repeated verification cannot alter a manifest-covered database. Restore verifies all file digests, opens the restored state under full Host invariants, and then atomically renames the verified temporary state into place. `--replace` preserves the old state root under a timestamped `.previous-*` path.

## Doctor

The local doctor checks SQLite integrity, schema compatibility, exact private state modes, Journal invariants including causal-link integrity, full CAS content integrity, orphan objects, and lease state. `--runtime` additionally loads the token from its file and performs modern MCP discovery and validates Runtime identity and protocol support.

`--history` decodes every historical Event payload and verifies its row identity, projection revision, known CAS references, and retained Effect/Binding/Authority links. It is intentionally explicit: at 100,000 Events it increased measured Doctor latency from 10.3 seconds to 18.5 seconds, while normal startup and normal Doctor remain unchanged.

`task assess` is local and read-only. `task reconcile` performs at most one conservative step: replayable deterministic Read progress or observation of an already-persisted keyed Runtime Dispatch. Package-scoped experimental Effect lifecycles are reported as manual because executor and domain observation authority are not reconstructible from the generic Host alone. It never creates or redispatches an Effect and never invokes a Provider. Non-automatic Tasks return a no-op without loading Runtime credentials.
