# Repository migration

## Provenance

The initial `ordivon-host/main` revision is the history-preserving subtree extraction of:

```text
repository: zycxfyh/ordivon-computing
source main: b2b9bb0ca0ea9a56ce600014f2b642a55e4f7461
source prefix: incubation/host-v0
extracted head: 0214db4fa1beb2342a4b7a674d7772c727de5e6a
```

The subtree extraction rewrites commit hashes because the path prefix becomes the new repository root, but retains the ordered Host-only commit history and commit messages. The original Computing H5/H6 revisions remain reachable from `ordivon-computing/main` and are bound by `evidence/h6-production-closure-20260727.json`.

## Ownership after extraction

- `ordivon-host` owns Host implementation, Host state, cognition adapters, Runtime coordination, and Host evidence.
- `ordivon-computing` remains authoritative for `ordivon-protocol`, semantic reference behavior, conformance vectors, and cross-project architecture.
- `ordivon-runtime` remains authoritative for physical Workspace, Job, Attempt, process, Artifact, and Runtime recovery state.

The Host dependency is pinned to the exact Computing revision above. Protocol source is not copied into this repository.

## License

The independent repository is licensed under Apache-2.0.
