# Contributing to Ordivon Host

Ordivon Host accepts changes that strengthen durable Task continuity, Journal/CAS integrity, commitment identity, uncertainty preservation, participant-routed decisions, verification admission, conservative recovery, or the explicit boundary with Runtime, Harness, Computing, Providers, and domain systems.

Do not add Runtime process supervision, another Task database, Harness Run state, a universal scheduler, a generic policy platform, or a new abstraction without a repeated workload failure that cannot be handled by the current owner.

## Prepare the source

```bash
git status --short --branch
git rev-parse HEAD
python3.12 --version
```

For an online editable installation:

```bash
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
```

For a local Ordivon checkout without reinstalling the pinned Protocol package:

```bash
export PYTHONPATH="$PWD/src:/root/projects/ordivon-computing/packages/ordivon-protocol/src"
```

The local sibling checkout is acceptable for development only when its exact revision is recorded. Release and CI installation use the immutable Protocol revision pinned in `pyproject.toml`.

## Required checks

```bash
python3.12 -m compileall -q src tests scripts
python3.12 -m ruff check src tests scripts
python3.12 -W error::ResourceWarning -m unittest discover -s tests -v
python3.12 scripts/check_docs.py
python3.12 scripts/check_dependencies.py
scripts/local-acceptance check
python3.12 -m pip wheel --no-deps --wheel-dir /tmp/ordivon-host-wheel .
```

Run the read-only live Host→Runtime journey when changing Runtime transport, catalog binding, Workspace lifecycle, read verification, recovery, or configuration:

```bash
ORDIVON_BEARER_TOKEN_FILE=/etc/ordivon/runtime-mcp.token \
  scripts/local-acceptance run
```

The live path creates a disposable Runtime Workspace, reads a tracked file, independently verifies it, closes the Workspace, and emits a JSON receipt. It does not authorize a mutation or Provider call.

## Change standard

A contribution must identify:

1. the observed failure or repeatedly missing operation;
2. the exact owner and boundary affected;
3. the durable identities and uncertainty states involved;
4. the test or live evidence that can falsify the change;
5. schema, migration, recovery, rollback, privacy, and compatibility impact;
6. documents whose canonical claim changes.

Provider and Runtime calls must remain outside Task leases. External delivery must never precede durable Dispatch identity. A lost response must not authorize a new physical effect. Runtime success must not bypass independent Host verification.

## Compatibility

Do not delete old object decoders, migrations, Runtime transport profiles, or replay paths merely because current writes use a newer contract. Deletion requires named consumers, an observation window, retained-state analysis, migration evidence, and an explicit release decision.

## Pull requests

Keep changes bounded and include exact commands, test counts, live receipts when relevant, and known limitations. Do not commit state roots, tokens, backups, private evidence, generated caches, or temporary Runtime Workspaces.

Security reports follow [`SECURITY.md`](SECURITY.md) and must remain private.
