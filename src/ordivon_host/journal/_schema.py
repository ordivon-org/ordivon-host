SCHEMA = """
CREATE TABLE IF NOT EXISTS host_metadata(
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
INSERT OR IGNORE INTO host_metadata(key, value) VALUES ('schema_version', '1');

CREATE TABLE IF NOT EXISTS object_refs(
    digest TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    byte_length INTEGER NOT NULL CHECK(byte_length >= 0),
    first_seen_at_ms INTEGER NOT NULL CHECK(first_seen_at_ms >= 0)
);

CREATE TABLE IF NOT EXISTS streams(
    stream_id TEXT PRIMARY KEY,
    stream_kind TEXT NOT NULL CHECK(stream_kind IN ('goal', 'task')),
    revision INTEGER NOT NULL CHECK(revision >= 1),
    created_at_ms INTEGER NOT NULL CHECK(created_at_ms >= 0),
    updated_at_ms INTEGER NOT NULL CHECK(updated_at_ms >= created_at_ms)
);

CREATE TABLE IF NOT EXISTS events(
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    stream_id TEXT NOT NULL REFERENCES streams(stream_id),
    stream_kind TEXT NOT NULL CHECK(stream_kind IN ('goal', 'task')),
    stream_revision INTEGER NOT NULL CHECK(stream_revision >= 1),
    event_kind TEXT NOT NULL,
    payload_digest TEXT NOT NULL REFERENCES object_refs(digest),
    caused_by_event_id TEXT,
    recorded_at_ms INTEGER NOT NULL CHECK(recorded_at_ms >= 0),
    UNIQUE(stream_id, stream_revision)
);

CREATE TABLE IF NOT EXISTS task_projection(
    task_id TEXT PRIMARY KEY REFERENCES streams(stream_id),
    goal_id TEXT NOT NULL,
    state TEXT NOT NULL,
    active_node_id TEXT,
    ready_frontier_json TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK(revision >= 1),
    updated_at_ms INTEGER NOT NULL CHECK(updated_at_ms >= 0)
);

CREATE TABLE IF NOT EXISTS task_nodes(
    node_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES task_projection(task_id) ON DELETE CASCADE,
    node_kind TEXT NOT NULL,
    node_state TEXT NOT NULL,
    payload_digest TEXT REFERENCES object_refs(digest),
    revision INTEGER NOT NULL CHECK(revision >= 1)
);

CREATE TABLE IF NOT EXISTS task_edges(
    task_id TEXT NOT NULL REFERENCES task_projection(task_id) ON DELETE CASCADE,
    from_node_id TEXT NOT NULL REFERENCES task_nodes(node_id) ON DELETE CASCADE,
    to_node_id TEXT NOT NULL REFERENCES task_nodes(node_id) ON DELETE CASCADE,
    edge_kind TEXT NOT NULL,
    PRIMARY KEY(task_id, from_node_id, to_node_id, edge_kind),
    CHECK(from_node_id != to_node_id)
);

CREATE TABLE IF NOT EXISTS runtime_links(
    dispatch_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES task_projection(task_id),
    effect_id TEXT NOT NULL,
    binding_id TEXT NOT NULL,
    workspace_id TEXT,
    runtime_job_id TEXT,
    client_request_id TEXT,
    commit_state TEXT NOT NULL,
    updated_at_ms INTEGER NOT NULL CHECK(updated_at_ms >= 0)
);

CREATE TABLE IF NOT EXISTS wakeups(
    wakeup_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES task_projection(task_id),
    wake_at_ms INTEGER NOT NULL CHECK(wake_at_ms >= 0),
    reason TEXT NOT NULL,
    state TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS leases(
    task_id TEXT PRIMARY KEY REFERENCES task_projection(task_id) ON DELETE CASCADE,
    owner_id TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK(revision >= 1),
    expires_at_ms INTEGER NOT NULL CHECK(expires_at_ms >= 0)
);
"""
