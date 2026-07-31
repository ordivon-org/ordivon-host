# HARNESS-REPO-REPAIR-001

Status: frozen Track R R2 workload

## Purpose

This Task turns the existing `harness-replacement-repository-repair-v1` fixture into a quality-controlled evaluation workload for one-shot, Ordivon Harness, and mature Provider Harness comparisons.

The model-visible workspace contains only:

```text
SPEC.md
allocation.py
test_allocation.py
artifacts/
```

The oracle, hidden verifier, known-invalid candidates, and QA script remain outside that workspace.

## Task identity

- Task: `HARNESS-REPO-REPAIR-001`
- Version: `1`
- Family: `repository_repair`
- Source fixture: `fixtures/harness-replacement-repository-repair-v1`
- Formal definition: [`task.json`](task.json)

## QA gate

The Task is admitted only when all checks hold across three clean rebuilds:

1. the untouched baseline fails the visible and hidden verifier;
2. the oracle passes both suites;
3. the floor-only candidate is rejected;
4. a visible-suite-overfit candidate passes the visible suite but is rejected by the hidden verifier;
5. protected specification and visible tests remain unchanged;
6. the hidden verifier and oracle are outside the model-visible fixture;
7. the Task and environment digests validate.

Run:

```bash
python3 evals/harness-repository-repair-001/qa.py
python3 -m unittest -v tests/test_eval_harness_repository_repair_001.py
```

Use `qa.py --write-digests` only before the first commit or when intentionally creating a new Task version. The immutable R2 admission receipt is [`evidence/r2-task-qa.json`](evidence/r2-task-qa.json).

## Verifier boundary

The visible tests communicate the required behavior. The hidden verifier checks broader largest-remainder behavior and input preservation without requiring one implementation strategy. It does not inspect model reasoning or demand a specific algorithmic syntax.

## Known limitation

This is a small deterministic repair task. It establishes task QA and common outcome verification; it does not represent long-horizon repository engineering, Context selection, or model quality in general.
