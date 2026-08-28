# Host Daily News

Host Daily News is a durable publication surface for structured external-world daily briefs. It is deliberately not a world-truth authority, a research engine, a scheduler, or a Task factory.

## Authority boundary

`news.*` uses the truth role `external-news-projection-not-world-truth`. Host is authoritative only that an exact edition revision was published, retained, and can be replayed. The facts and interpretations inside that edition remain source claims and must be revalidated against their external evidence when current truth matters.

Board remains the collaboration surface. Task remains continuity for work. News does not silently create either.

## Persistence model

One complete `NewsEdition` is one immutable CAS object per revision. Individual news items are embedded inside the edition rather than stored as one CAS object each. The Journal stores only edition heads and publication history. Edition objects are classified `on_access`, so ordinary Host startup validates cheap Journal relations without enumerating historical News CAS. This keeps long-run object-ref growth proportional to edition revisions instead of item count and keeps News history off the startup-critical payload-validation path.

An edition contains 1–64 structured items. Each item carries a section, category, headline, summary, novelty, optional thread/continuation identity, status, importance, confidence, distinct event/publication/observation timestamps, and 1–16 durable evidence references. Ephemeral conversation citations such as `turn...news...` are rejected as source identities.

## MCP surface

- `news.publish`: exact-replay publication with `clientPublishId` and `expectedRevision`.
- `news.read`: read latest or exact revision; optionally filter by section/category/thread; long rendered prose is omitted by default.
- `news.list`: bounded date-scoped edition discovery with query-bound cursor paging.

Corrections append a new immutable revision. Old revisions remain readable.

## Daily brief integration

The preferred producer flow is:

`external research -> structured NewsEdition -> Host.news.publish + human rendered brief`

The structured edition is the intermediate representation; the human 3,500–5,000-character brief is one projection. Host should not parse the final prose back into structure, and Host does not run a second research pipeline. A failed Host sync must be replayed with the same `clientPublishId` rather than generating a second edition identity.

## Scale result (schema v8 integration, 2026-08-28)

A conservative local benchmark published one durable edition per day with SQLite `synchronous=FULL` and News CAS classified `on_access`. Ordinary Host open median was ~0.81 ms at 1 edition, ~4.64 ms at 365 editions, and ~41.29 ms at 3,650 editions. Ordinary open validated/hash-read **zero** News CAS objects in all three cases; it still checked cheap Journal revision/head relations. Full validation at 3,650 editions hashed all 3,650 retained edition objects in ~663 ms. Object refs were exactly equal to edition count, all `on_access`.

This supports edition-granularity persistence plus deferred payload validation for v1. Cross-day semantic indexes, per-item CAS objects, or additional startup caches should be added only if real retrieval/currentness pressure appears.
