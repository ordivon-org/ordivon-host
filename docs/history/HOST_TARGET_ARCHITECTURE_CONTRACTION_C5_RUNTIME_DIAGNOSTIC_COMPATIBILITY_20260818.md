# Host Target-Architecture Contraction C5 — Runtime Diagnostic / Public Compatibility — 2026-08-18

> Historical engineering evidence. C5 follows C1 GoalCoordinator, C2 ExternalExecutor, C3 Recovery/Engine/Cognition-Execution, and C4 capability-authority contraction. It does not change frozen HDF0–HDF43 and does not authorize production deployment.

## 1. Question

After C3 removed all Host-owned Runtime workloads and C4 removed the remaining capability policy, does Host still need to own a Runtime client/configuration/diagnostic surface?

Three models were compared:

```text
A. retain current Host Runtime integration
   runtime/* + RuntimeSettings + doctor --runtime

B. retain only a thin Host Runtime diagnostic adapter
   enough to check configured Runtime identity/connectivity

C. remove Host Runtime integration entirely
   Runtime is observed directly through Runtime-native Tools
```

The admission criterion is not whether Host *can* connect to Runtime. It is whether Host gains a non-bypassable correctness or ownership signal by doing so.

## 2. Starting surface

Post-C4 candidate Host still contained 881 LOC under:

```text
src/ordivon_host/runtime/
    __init__.py
    catalog.py
    client.py
    errors.py
    jobs.py
    mcp.py
    semantics.py
    workspaces.py
```

`ordivon_host.runtime` exported:

- `RuntimeClient`;
- `McpRuntimeClient`;
- modern and legacy MCP transport profiles;
- `RuntimeCatalog` / `ExecutionRuntimeCatalog`;
- Runtime Tool discovery helpers;
- Runtime Job/workspace helpers;
- Runtime transport/protocol/tool errors;
- Runtime observation classification.

Top-level `ordivon_host` also re-exported:

- `McpRuntimeClient`;
- `RuntimeCatalog`;
- `ExecutionRuntimeCatalog`;
- Runtime discovery helpers;
- `RuntimeSettings`.

HostConfig still carried Runtime endpoint, token file, timeout and response bounds.

The only remaining production-side call path was:

```text
ordivon-host doctor --runtime
  -> HostConfig.runtime
  -> Runtime token file
  -> McpRuntimeClient.initialize()
  -> server/discover
  -> return serverInfo
```

No current Host workload, MCP Tool, continuity operation, storage operation, deployment transition, World consumer, or Security consumer used Runtime through this package.

## 3. Exact consumer audit

A precise import scan found **zero external production Python imports** of:

```text
ordivon_host.runtime
McpRuntimeClient
RuntimeCatalog
ExecutionRuntimeCatalog
discover_runtime_catalog
discover_execution_runtime_catalog
```

Earlier loose scans had matched generic class names such as `RuntimeClient` inside Finance and Harness. Exact import-line inspection proved those are their own Runtime abstractions, not Host Runtime consumers.

Within Host production source, Runtime package use after C3/C4 reduced to:

- `ops/doctor.py`;
- top-level compatibility exports.

All other references were tests, packaged testing helpers, or one historical Runtime restart proof script.

## 4. Host service dependency audit

The deployed Host MCP service has no systemd dependency on Runtime:

```text
Requires=Runtime     absent
Wants=Runtime        absent
After=Runtime        absent
```

Host service only depends on ordinary network-online ordering.

No non-doctor production source uses `config.runtime`.

The current `/etc/ordivon/host.toml` does not define a Runtime table. `doctor --runtime` was therefore primarily exercising default endpoint `127.0.0.1:8897` plus the independent Runtime token file.

This is significant: the wiring existed for optional diagnosis, not because Host operation required Runtime.

## 5. What `doctor --runtime` actually proved

The implementation did not inspect Runtime Job state, Workspace state, durability, recovery health, Tool catalog semantics, or a Host-specific dependency contract.

It only:

1. loaded the configured Runtime token;
2. constructed `McpRuntimeClient`;
3. performed modern MCP `server/discover`;
4. returned Runtime `serverInfo`;
5. marked Host Doctor unhealthy if that connection failed.

Thus its positive signal was approximately:

```text
Host-configured endpoint/token can perform Runtime MCP discovery now
```

That is an integration connectivity signal, not Host state correctness.

## 6. Runtime-native observation is stronger

Direct Runtime-native `task.list` during C5 returned current durable Runtime facts including:

- Job identity;
- Workspace identity;
- Attempt identity/state;
- execution disposition;
- delivery disposition;
- recoveryRequired;
- resultAvailable;
- source revision;
- exact timing/status.

Runtime source also exposes native `workspace.list`, `workspace.get`, `task.get`, `task.observe`, `task.list`, and related Tools whose durable truth is explicitly owned by Runtime Core.

Therefore:

```text
Host doctor Runtime signal < Runtime-native owner observation
```

for physical Runtime truth.

The only unique Host-side fact was whether **Host's own obsolete Runtime wiring** worked.

## 7. False-dependency falsifier

C5 intentionally created an isolated Host config with an unreachable Runtime endpoint:

```text
http://127.0.0.1:1/mcp
```

Results:

```text
Host init                    PASS
Host-only doctor             healthy / exit 0
Host doctor --runtime        unhealthy / exit 1
```

The Runtime diagnostic failed with a transport timeout while every Host-owned Doctor invariant remained green.

Immediately after that experiment, direct Runtime-native `task.list` remained healthy and returned the exact C5 Job itself.

Runtime Job for the falsifier:

`job-01a01341-3197-7133-8c7c-956b5e25ab1f`

Terminal evidence:

`sha256:8eac1276b5d110f37c95e85340a9bdadbb1fab35329bdc10e49a5bc14575c220`

This proves:

```text
Host↔Runtime wiring failure != Host failure
```

and also:

```text
Host↔Runtime wiring failure != Runtime failure
```

Once Host no longer has a Runtime workload consumer, treating that wiring as Host health creates a false dependency.

## 8. Model comparison

### Model A — retain full Runtime integration

Benefits:

- backwards-compatible Host Python imports;
- convenience `doctor --runtime`;
- legacy Host-local Runtime helper/test apparatus.

Costs:

- 881 LOC product Runtime package;
- duplicated MCP client/transport compatibility logic;
- Runtime endpoint/token fields in HostConfig;
- direct Host dependency on Runtime protocol evolution;
- legacy modern/legacy transport profiles;
- public compatibility burden with no production consumer;
- a diagnostic that can label healthy Host state unhealthy due to an external system Host no longer needs.

Unique correctness signal: **none demonstrated**.

### Model B — thin diagnostic adapter

A thinner adapter could preserve only Runtime discovery and Host-side credential validation.

But the false-dependency experiment shows that this would still answer only:

```text
can an unused Host Runtime credential reach Runtime?
```

It would not establish Host correctness and would remain weaker than Runtime-native state observation.

Unique correctness signal: **none demonstrated**.

### Model C — no Host Runtime integration

Host retains only semantic navigation references such as `WorkingCheckpoint.runtime`.

When physical Runtime truth matters, the consumer queries Runtime directly.

Advantages:

- ownership matches actual truth boundary;
- no duplicate Runtime protocol/client implementation;
- no Runtime credential in Host configuration;
- no false Host health dependency;
- Runtime protocol evolution is owned by Runtime;
- continuity can still preserve opaque Runtime navigation hints without mirroring Runtime state.

Model C survives all current workloads and wins C5.

## 9. Test equipment separation

One important subtlety surfaced during deletion.

`tests/test_mcp_server.py` used `McpRuntimeClient` as a convenient generic HTTP/MCP wire client to test the **Host MCP server itself**.

That is test equipment, not evidence that Host production owns Runtime integration.

C5 therefore moved the bounded MCP client/error machinery into:

```text
src/ordivon_host/testing/mcp_client.py
src/ordivon_host/testing/mcp_errors.py
```

The test-only class is named `McpTestClient`.

Product `ordivon_host.runtime` is absent.

This enforces the distinction:

```text
needed to test Host MCP wire behavior
!=
needed as Host product Runtime API
```

## 10. Product/config/CLI contraction

C5 removes:

- entire product `src/ordivon_host/runtime/` package;
- Runtime top-level Host exports;
- `RuntimeSettings`;
- Runtime endpoint/token/timeout/response fields from HostConfig;
- `[runtime]` Host configuration support;
- Runtime-specific token alias `read_token_file`;
- `doctor --runtime`;
- Runtime check from Host Doctor;
- Runtime testing/fault helper modules;
- historical `scripts/live_active_job_restart.py`;
- dedicated Runtime client/catalog/semantics/workspace tests.

The generic private-token validation primitive remains because Host MCP itself still requires private credential handling.

`WorkingCheckpointRuntime` remains because it is a semantic navigation hint, not a Runtime client or copied Runtime state graph.

## 11. Active documentation contraction

The first deletion run produced:

```text
157 unit tests PASS
scripts/check_docs.py FAIL
```

The failure was exact and informative: `check_docs.py` still required `src/ordivon_host/runtime/mcp.py` and checked modern/legacy Runtime transport markers.

That was not a hidden product consumer. It proved active docs still treated the old Host Runtime client as current architecture.

C5 therefore updated the isolated candidate's active:

- `README.md`;
- `ARCHITECTURE.md`;
- `docs/QUICKSTART.md`;
- `docs/STATUS.md`;
- `docs/OPERATIONS.md`;
- `docs/RELEASES.md`;
- `scripts/check_docs.py`.

The updated documents state:

```text
Runtime is an external owner.
Host does not configure/proxy/health-check Runtime.
WorkingCheckpoint.runtime is navigation only.
Runtime truth is queried from Runtime-native Tools.
Runtime availability is not Host health.
```

This also consumed stale C3 documentation that still described deleted read/mutation/code-change/reconcile workloads as current product features.

First deletion/full-suite Job:

`job-01a01343-118f-7b93-9a29-bf9b224dcf79`

Terminal evidence:

`sha256:a311ad9af1e4242d470defe9691c98fce543df164c3146d8c5f63122a5d58f26`

The failure was docs-contract-only after all 157 unit tests passed.

## 12. Final Host validation

After contracting the active docs contract:

```text
157 / 157 PASS
```

Also passed:

- documentation contract;
- dependency contract;
- compileall;
- `git diff --check`.

Runtime Job:

`job-01a01345-3b52-7101-85b3-4f00c859f706`

Terminal evidence:

`sha256:2fb0d5c949c9012a1a3801eeac352afe3e1cbd90f237cfee99cb924c03e2c575`

The test-count reduction from 180 to 157 is explained by removal of dedicated product Runtime client/catalog/semantics/workspace tests. Host MCP wire behavior remains covered by the test-only MCP client.

## 13. Real consumer / boundary validation

### World

Current World complete suite with C5 candidate Host:

```text
158 / 158 PASS
```

### Security

Current focused Security Host/context suites:

```text
22 / 22 PASS
```

### Host Runtime surface absence

Candidate boundary proof confirmed:

```text
ordivon_host.runtime                 absent
Host top-level Runtime exports       absent
legacy [runtime] HostConfig table    rejected
ordivon-host doctor --runtime        rejected by CLI
WorkingCheckpointRuntime             retained as navigation hint
```

Combined Runtime Job:

`job-01a01345-fb99-7950-9776-cd99670bb766`

Terminal evidence:

`sha256:8d4184084aaa6678533e91dda63905c04f32f632fddb5fab940eb8cb472a4051`

Direct Runtime-native `task.list` remained operational after candidate contraction.

## 14. Candidate commit and size

C5 candidate commit:

`b7815de9ff8ee7cee6990f7852a39f956adfa22a`

Subject:

`refactor(host): remove Runtime integration residue`

Git commit summary:

```text
32 files changed
99 insertions
1,795 deletions
```

Git recognized `runtime/mcp.py` and `runtime/errors.py` as renames into test-only MCP equipment; logically, the product Runtime package is completely absent.

Current cumulative candidate Host source:

```text
~7,982 Python LOC
```

## 15. Verdict

```text
Host Runtime workload ownership        already falsified by C3
Host Runtime production caller         absent
external production consumer           absent
systemd dependency                     absent
HostConfig Runtime necessity            absent
Host health correctness signal          absent
Runtime-native alternative              stronger
thin adapter unique value               absent
product Runtime package deletion        PASS
World validation                        PASS
Security validation                     PASS
Host validation                         PASS
active docs contraction                 PASS
release-policy admission                NOT YET
```

Therefore:

```text
Host Runtime integration
= DELETION-PROVEN / RELEASE-GATED
```

## 16. Updated target architecture

After C1–C5, current engineering evidence supports:

```text
Host
= durable semantic continuity
+ Journal/CAS authority for Host facts
+ exact Task revision/lease admission
+ WorkingCheckpoint adopt/checkpoint/resume
+ bounded handoff/inspection
+ owner-opaque extension durability where real consumers exist
+ bounded context semantics where Security consumes them
+ local Doctor/backup/restore/deployment integrity
```

Host no longer needs to own:

```text
shared Goal coordination
foreign executor coordination
Runtime workload engines
source mutation/code-change engines
cognition execution/proposal orchestration
automatic cross-owner reconciliation
generic capability authorization policy
Runtime client/configuration/health proxy
```

The ownership law is now explicit:

```text
semantic continuity reference != foreign state ownership
navigation hint != copied owner state
ability to query another owner != responsibility to proxy that owner
external owner outage != Host authority failure
```

## 17. Recovery projection after C5

The surviving `recovery.py` remains a small (~73 LOC) Host-local read projection:

```text
terminal -> none
nonterminal -> unsupported / re-observe owner
automatic -> false
```

It does not import Runtime, invoke another owner, or create a shared recovery mechanism.

C5 therefore does not treat it as another major cross-owner implementation residual. Whether `task assess` is worth retaining as a small operator convenience can be decided during bundled cutover acceptance without reopening the architecture.

## 18. Release gate and next phase

Canonical/deployed Host remains:

`8d7e58a0511734a454805e29d10e7d3bb754d2da`

C1–C5 remain isolated, deletion-proven candidates/evidence.

C5 closes the last major foreign-owner implementation residual identified by C1–C4.

The next canonical phase should **not** be another opportunistic deletion round. It should prepare one bundled pre-1.0 Major contraction cutover covering:

- C1 GoalCoordinator;
- C2 ExternalExecutor;
- C3 old engines / cognition execution / automatic reconcile;
- C4 capability policy;
- C5 Runtime integration;
- active documentation/interface cleanup already begun in C5;
- exact compatibility impact;
- Changelog;
- retained-state/history validation;
- current World/Security consumer validation;
- deployment candidate build;
- exact previous-release rollback peer;
- observation window and acceptance criteria.

Host naming still remains last. The contraction evidence should be canonicalized before deciding whether the repository/product name itself should change.
