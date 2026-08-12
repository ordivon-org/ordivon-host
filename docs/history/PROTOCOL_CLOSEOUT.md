# Protocol P0/P1 formal closeout

> **Historical Protocol closeout record:** This document preserves stage-specific decisions, measurements, or provenance. It is not a current Host architecture or operations source. Use [`../README.md`](../../README.md), [`../ARCHITECTURE.md`](../../ARCHITECTURE.md), [`OPERATIONS.md`](../OPERATIONS.md), and [`authority.md`](../authority.md) for the active boundary.

Date: 2026-07-28

H7 remains frozen. This closeout does not add scheduling, daemon wakeups, multi-Agent orchestration, a network Model Gateway, Runtime SecretRef, or a standalone `ordivon-protocol` repository.

## Authoritative boundary

`ordivon-computing/packages/ordivon-protocol` remains the only normative Protocol source. The unified Computing revision is:

```text
fb213ceac5c326e79b53a3122e320b976869e1af
```

It contains:

- one shared `ExecutionKind` and `CompletionKind` implementation;
- one Protocol-package SHA-256 digest validator;
- package-owned normative Schemas and canonical vectors;
- `anc.source.change.v1` and repository-bound `SourceChangeSpec`;
- an Effect schema that admits the SourceChange action;
- Protocol distribution version `0.2.0`;
- `ordivon_semantics` frozen as a reference candidate rather than an expanding production contract.

The Host pins that exact Computing revision. It does not copy Protocol source and does not treat the former SourceChange branch as a second authority.

## Verified compatibility

The existing public import paths remain valid:

```python
from anc_effect_ir import ExecutionKind, CompletionKind
from anc_tool_contract import ExecutionKind, CompletionKind
```

Both modules now re-export the same enum objects. Existing Effect, ToolContract and Binding wire versions remain unchanged. `anc.source.change.v1` is a compatible Protocol 0.2.0 extension, and its normative action enum is present in the packaged Effect schema.

`SourceFileChange` now independently verifies that `resultDigest` matches the canonical UTF-8 content bytes before a SourceChange Effect can be constructed.

## Gates

Computing and Protocol:

```text
Protocol package tests              9/9
External semantic contract tests   41/41
Semantic Core tests               100/100
Task Continuation tests            20/20
Host incubator tests               77/77
Rust canonical vectors              5/5
compileall                           PASS
Ruff                                 PASS
Protocol wheel install              PASS
```

Active Host:

```text
Host tests                         133/133
compileall                           PASS
Ruff                                 PASS
Host wheel metadata                  PASS
Host + Protocol wheel install        PASS
CLI smoke                            PASS
```

## Deliberately deferred

- No independent Protocol Git repository until multiple production consumers or cross-language releases justify it.
- No Host deployment or Runtime deployment.
- No remote push, PR, or merge in this closeout.
- No upgrade of unrelated Ordivon repositories.
- No H7 expansion.
