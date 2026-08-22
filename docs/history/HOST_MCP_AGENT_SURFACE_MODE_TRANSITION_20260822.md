# Host MCP Agent-Surface Mode-Transition Audit — 2026-08-22

> Historical engineering-consumption record. Current product truth remains `README.md`, `ARCHITECTURE.md`, `docs/OPERATIONS.md`, source/tests, and deployed release identity. This audit does not reopen HDF0–HDF43.

## Question

The six-tool Host MCP surface was proven useful when the dominant use contract was **recover one known durable Task continuity after Agent/session replacement**. Ordivon's current work ecology now separately represents problem/question, owner, result/standing, consumer/decision, consequence and portfolio allocation. The audit asks whether the old Agent-facing `task.*` surface now causes a concrete consumer failure even though the Continuity Core remains sound.

## Existing standing consumed before experimentation

- `task:ordivon-host-semantic-workstate-archaeology-20260821@6`: no Host ontology/schema expansion without a concrete current consumer failure.
- `task:ordivon-decision-bearing-standing-audit-20260821@3`: Host lifecycle/list order is not achievement, value, priority, admission, or owner direction.
- `task:ordivon-agent-conflation-control-risk-audit-20260820@8`: coarse lifecycle/result projections can induce control-relevant semantic elevation; rich continuity packets were adequate for the old known-resume consumer.
- `task:ordivon-research-portfolio-matrix-20260821@12`: activation must not be driven by Task names or READY state.
- `task:stalled-research-revalidation-20260822@5`: real current dispositions include RESUME_NOW, ACQUIRE, WAIT, HOLD and SUPERSEDED outside generic Host READY.

The current source also confirms that generic `TaskState` still has richer states, but `ExternalContinuityHost` deliberately contracts external continuity to READY while open and COMPLETED/CANCELLED when tracking terminates. Therefore the observed READY collapse is an intentional continuity mapping, not a storage deficiency.

## Mode transition

Old consumer contract:

```text
known Task identity -> exact continuity resume -> revalidate owners -> continue
```

Current pressure:

```text
Host continuity inventory --misread as--> current cross-owner work / priority portfolio
```

The correct invariant is:

```text
Continuity != CurrentWork != Priority != Standing != DomainCurrentness
```

`task.*` is therefore retained as a compatibility naming surface over Host Task identity/continuity mechanics, not promoted into a universal work ontology.

## Fresh-Agent experiment

Exact evidence summary: `evidence/host-mcp-agent-surface-mode-transition-20260822.json`. The raw bounded runs remain in Runtime workspace `host-tool-surface-harness-ab-20260822`.

Three consumers were tested: (1) choose a Host operation for cross-owner NOW allocation, (2) discover resumable continuities, and (3) resume one known continuity after session replacement. Initial treatments compared the current surface, an explicitly labelled current surface, and a new continuity-first endpoint candidate. Each cell used three `deepseek-v4-flash` runs plus one `deepseek-v4-pro` run.

Primary result:

- current surface: NOW-portfolio operation selection only 2/4 correct; 2/4 runs falsely granted Host portfolio authority; continuity discovery 4/4 and exact resume 4/4 remained good;
- explicitly labelled existing surface: NOW selection 4/4, false Host portfolio authority 0, discovery 4/4, exact resume 4/4, READY-to-actionable false positives 0;
- new `continuity.list` candidate removed portfolio false authority but added surface complexity, retained two READY-actionability false positives across the matrix, and one exact-resume run produced no valid result.

A second ablation separated description/instruction changes from response-field changes. Description-only passed the decision-critical scoring 9/9 across two Flash plus one Pro run per consumer. Response-fields-only passed 7/9. Thus the smallest demonstrated repair is Agent-facing contract clarification, not a new endpoint or wire object.

## Engineering decision

Admit only the following change:

1. state in MCP server instructions that `task.*` is compatibility naming for Host continuity primitives;
2. state mechanically that `host.status` continuity counts are tracking counts, not active-work or priority counts;
3. state mechanically that `task.list` is continuity inventory/discovery and READY means only open Host continuity;
4. state that `task.observe` TaskProjection is Host lifecycle mechanics;
5. state that `task.resume` recovers semantic continuity but does not re-admit frontier/nextActions as current work;
6. state that adopt/checkpoint mutate continuity only and do not establish priority, owner standing or external domain outcome.

Do **not** add WAIT/HOLD/BLOCKED to Host continuity, do not add `continuity.list`, do not add response fields, and do not build Task v2 or a Host portfolio authority.

## Compatibility and validation

The candidate changes descriptions/instructions, current documentation, one regression test and changelog only. Tool names, input/output schemas, WorkingCheckpoint, Journal schema and persistent state are unchanged. Because Host intentionally excludes titles/descriptions from `serverInterface` schema identity, candidate schema digest remains exactly `sha256:382abe793d2e41470d91f1efc00d76152ff1fdbae3cac53adcad511d8211780c`.

Candidate validation: 156/156 unit tests, Ruff, documentation contract, dependency contract and Host local acceptance all pass.

## Reopen condition

Reopen tool-shape work only if a real current consumer still makes a control-relevant continuity/current-work conflation after this description correction, or if repeated discovery friction demonstrates that naming/response shape itself—not missing semantics—remains deletion-essential. Such a failure may justify a continuity-first alias or a separate derived work projection, but no such stronger change is established here.
