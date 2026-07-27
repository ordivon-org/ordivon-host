# Pre-H7 foundation closure

This document freezes the independently extracted `ordivon-host` foundation before any H7 design or implementation. It is a closure record and audit starting point, not a claim that the Host is a production-ready general Agent platform.

## Closure decision

Pre-H7 foundation work is complete enough to stop implementation and begin a separate deep audit.

The repository now contains:

- a traceable extraction from `ordivon-computing` under Apache-2.0;
- one durable Host Journal and immutable content-addressed object store;
- explicit schema migrations with pre-migration database backups;
- operational configuration, CLI inspection, Doctor, backup, restore, and read-only GC planning;
- reusable live-scenario and fault-injection support;
- deterministic read, persistent cognition, guarded mutation, and durable source-change vertical slices;
- measured 100,000-event behavior and a bounded process/file-level fault matrix.

No H7 scheduler, DAG engine, daemon, distributed coordination layer, provider router, arbitrary mutation DSL, or multi-Agent mechanism is admitted by this closure.

## Bound revisions

| Revision | Meaning |
|---|---|
| `a536b01c0672e7f74ff5b22829f951e17f58fc3f` | Independent repository extraction merged with history preserved |
| `4f02fed` | Schema-v2 and operational state tooling |
| `d2fb115` | Reusable live scenario harness |
| `5076e6b` | Durable code-change vertical slice |
| `c2933d1` | Live durable code-change scenario and response-loss injection |
| `42d3d6e` | Production Runtime code-change proof bound into the repository |
| `9d10cf7` | Verified CAS file-identity cache and schema v3 |
| `24334a6bbef446910cffef6cc559221cb99438da` | Scale and fault-matrix evidence tools |
| `a6a4d1daa1a405ed318e38a1a2609319adfc226c` | Immutable pre-H7 scale and fault receipts |

The branch is intentionally stopped after a final closure commit. Push, pull request update, merge, deployment, and H7 work are outside this closure.

## Durable state boundary

The state root contains:

```text
host.sqlite3   schema-v3 Journal, stream heads, Task projections, leases,
               migration history, object references, and validation cache
objects/       immutable canonical JSON envelopes addressed by SHA-256
receipts/      optional operational receipts
```

The authoritative semantic sequence remains:

```text
write and fsync immutable object
→ begin SQLite IMMEDIATE transaction
→ compare expected stream revision
→ admit object references, event, stream head, and Task projection
→ commit atomically
```

A failed Journal commit may leave an unreferenced immutable object. It must not leave a partially admitted semantic transition. Such objects are visible to read-only GC planning.

## Schema history

### v1 → v2

Removed four tables that had no production owner:

- `task_nodes`;
- `task_edges`;
- `runtime_links`;
- `wakeups`.

Migration proceeds only when those tables are empty. Populated legacy state fails closed. The source database is retained as `host.sqlite3.pre-schema-v2.sqlite3`.

### v2 → v3

Added `object_validation`, which binds a previously decoded and SHA-256-verified object to its observed file identity:

- device;
- inode;
- byte length;
- modification time;
- change time;
- mode.

The source database is retained as `host.sqlite3.pre-schema-v3.sqlite3`.

Normal startup may reuse a prior full verification only when the complete file identity remains unchanged. A missing object, cache miss, ordinary write, replacement, truncation, or metadata change forces a fresh decode and SHA-256 verification. `ordivon-host doctor` always performs full byte validation and does not rely on the cache.

This optimization is explicitly scoped to a trusted-local filesystem threat model. It does not claim detection of every hypothetical storage-layer mutation that preserves all exposed file metadata.

## Operational surface

The installable command exposes:

```text
ordivon-host init
ordivon-host inspect
ordivon-host config show
ordivon-host task list
ordivon-host task show TASK_ID
ordivon-host doctor [--runtime]
ordivon-host backup DESTINATION
ordivon-host verify-backup BACKUP
ordivon-host restore BACKUP [--replace]
ordivon-host gc plan
```

Only `init` may create a missing state root. Inspection and backup operations fail closed when `host.sqlite3` is absent. GC remains plan-only and cannot delete objects.

Backups contain an online SQLite backup, every referenced CAS object, and a manifest with exact file digests, object metadata, schema version, and migration history. Verification is full and read-only: it disables validation-cache writes so repeated verification cannot mutate the manifest-covered backup database. Restore validates into a temporary root before an atomic rename; replacement preserves the prior state root under a timestamped path.

## Durable code-change boundary

`CodeChangeHost` persists:

- the exact source repository and 40-character source revision;
- one to eight target files;
- each original file digest;
- each complete result content and result digest;
- one to eight structured verification checks;
- a stable Runtime Workspace identity;
- a stable `clientRequestId` and Dispatch identity;
- Runtime observations, independent file verification, Git diff evidence, and terminal outcome.

The changing operation is one durable `workspace.execPlan` Job:

```text
apply exact digest-guarded file replacements
→ run structured checks sequentially
→ observe the keyed Runtime Job
→ independently read and hash every result file
→ independently inspect the Git diff
→ close the disposable Workspace
→ admit TaskOutcome
```

A lost successful response becomes a persistent unknown delivery state. A fresh Host process searches for the original Job by `clientRequestId`; absence never authorizes automatic redispatch. Runtime success alone does not complete the Task. File and diff verification must also succeed.

The production live proof changed `src/ordivon_host/ops/inspect.py` and `tests/test_operations.py`, deliberately discarded the first successful `workspace.execPlan` response, recovered the original Job, completed three structured steps, verified two files, admitted revision 7, and closed the Runtime Workspace. The bound receipt is `evidence/live-p4-code-change-c2933d1-20260727T181620Z.json`.

## Scale evidence

The scale fixture contains:

```text
1,000 Tasks
100 events per Task
100,000 Events
100,000 referenced CAS objects
1,000 terminal Task projections
```

Measured state size was approximately:

```text
SQLite: 42,237,952 bytes
CAS:    37,743,000 bytes
Files:  100,000 CAS files
```

### Baseline

Revision `42d3d6e` performed a full decode and SHA-256 verification of every CAS object on every open.

| Metric | Measurement |
|---|---:|
| Fresh startup 1 | 7,302 ms |
| Fresh startup 2 | 7,514 ms |
| Fresh startup 3 | 7,311 ms |
| Full Doctor | 14,438 ms |
| Task list | 8 ms |
| Journal invariant check | 35 ms |

Receipt:

```text
evidence/p5-scale-baseline-1000x100-42d3d6e-20260727T181208Z.json
sha256:8fad507cc2f5c10200abb32579f01b14ddc3215d822703745a603d163c96e1f2
```

Profiling isolated the dominant cost to opening, decoding, and hashing 100,000 independent small files. SQLite open, object-reference queries, Task-head reconstruction, and normal Task queries were not the dominant cost. Threaded hashing with 2–32 workers was slower than serial hashing on the measured machine and was rejected.

### Schema-v3 cached startup

Revision `24334a6` retained full first-open verification and full Doctor verification while allowing unchanged objects to use the validation cache on subsequent opens.

| Metric | Measurement |
|---|---:|
| First open, hash 100,000 objects and populate cache | 10,244 ms |
| Cached fresh startup 1 | 1,706 ms |
| Cached fresh startup 2 | 1,483 ms |
| In-process open | 1,540 ms |
| Inspect | 1,657 ms |
| Full Doctor | 10,818 ms |
| Task list | 9 ms |
| Journal invariant check | 37 ms |

The repeated-startup median moved from 7,311 ms to approximately 1,595 ms, about a 4.6× improvement and a 78% latency reduction on this fixture. The evidence does not support claiming a repeatable memory reduction.

Receipt:

```text
evidence/p5-scale-1000x100-24334a6-20260727T185853Z.json
sha256:e59482011b577bc55c410131003e3157dcc139e6ac06aa74619168d642c17ab1
```

Pack files, Merkle checkpoints, object compaction, and more complex snapshot structures were not added. At the measured scale, their migration and operational complexity were not justified by the remaining startup cost.

## Fault evidence

The committed fault matrix passed seven destructive local scenarios:

1. Host process killed after an immutable object write: no false Journal reference; the object is reported as an orphan.
2. Process killed with an uncommitted SQLite transaction: transaction rolls back and `quick_check` remains `ok`.
3. Lease owner killed: the lease remains visible and a later owner may take over only after expiry with a higher lease revision.
4. Bounded file-size failure during CAS write: no Event, object reference, or temporary object remains.
5. Referenced CAS object removed: Doctor fails closed.
6. SQLite header corrupted: Doctor detects the database failure.
7. Backup verified repeatedly and restored from a fresh process: verification does not mutate the backup, and restored state matches the manifest.

Receipt:

```text
evidence/p5-fault-matrix-24334a6-20260727T185913Z.json
sha256:d4e25ad96e592d0cb693937a095a0dba47fe951251d61461f23588dddd973a7a
```

## Claims closed by this phase

The evidence supports these claims:

- Host continuity does not depend on a model session or one Python process.
- Journal revision admission and short leases prevent two current writers from silently committing the same Task revision.
- External mutation identity is durable before delivery.
- Unknown delivery is reconciled conservatively without automatic redispatch.
- A real two-file source change can execute through Runtime, structured checks, independent digest verification, diff verification, terminal outcome, and Workspace cleanup.
- The state layout remains operational at 100,000 Events and 100,000 CAS objects on the measured local system.
- Repeated startup cost can be reduced substantially without removing the full Doctor path.
- The tested process-level and file-level failure cases fail closed or recover with explicit evidence.

## Claims not closed

This phase does not prove:

- Provider decision → deterministic admission → `CodeChangePlan` execution as one combined live path;
- arbitrary model-authored patches or a general source-editing DSL;
- multi-Task scheduling, graph execution, multi-Agent coordination, or distributed Host operation;
- a continuously running Host daemon or production service lifecycle;
- whole-WSL restart during a Host transition;
- machine reboot, abrupt power loss, kernel crash, or physical media failure;
- long network partitions or remote Runtime recovery;
- multi-Host concurrent ownership;
- long-duration soak behavior;
- subsecond startup at 100,000 independent CAS files;
- repeatable memory reduction from schema v3.

An untested Cognition-to-CodeChange bridge was started and deliberately deleted before closure. No incomplete API or module is retained in the stable tree.

## Audit entry points

The next activity is review, not feature development. A deep audit should examine at least:

1. Journal/CAS atomicity and every fail-closed path.
2. Whether schema-v3 file-identity caching matches the intended trusted-local threat model.
3. Migration idempotence, backup provenance, and rollback usability.
4. CodeChange request determinism, source preconditions, command bounds, and rollback behavior inside the patch step.
5. Unknown-delivery reconciliation and conflicting Runtime Job identity handling.
6. Verification independence: Runtime result versus Host file reads, digests, diff, and outcome.
7. Test quality, fake Runtime fidelity, live receipt reproducibility, and unsupported claims.
8. Documentation drift between historical `ARCHITECTURE.md`, current schema v3, and operational behavior.
9. Whether any current abstraction exists without two real owners or a demonstrated failure mode.
10. Whether H7 has a sufficiently specific problem statement to justify changing this boundary.

## Freeze rule

Until the audit is complete:

- do not start H7;
- do not reintroduce the deleted Cognition-to-CodeChange bridge;
- do not add a scheduler, DAG abstraction, daemon, provider router, or general mutation language;
- do not optimize beyond measured evidence;
- do not weaken Doctor or backup verification to improve nominal startup latency;
- treat new failures as audit evidence before treating them as implementation tasks.

This closure intentionally leaves a small, evidence-bound Host foundation rather than converting every discovered possibility into another subsystem.
