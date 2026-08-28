SCHEMA_VERSION = 8
LEGACY_UNUSED_TABLES = ("wakeups", "runtime_links", "task_edges", "task_nodes")

SCHEMA = """
CREATE TABLE IF NOT EXISTS host_metadata(
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
INSERT OR IGNORE INTO host_metadata(key, value) VALUES ('schema_version', '8');
INSERT OR IGNORE INTO host_metadata(key, value) VALUES ('event_object_refs_start_sequence', '1');

CREATE TABLE IF NOT EXISTS schema_migrations(
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    from_version INTEGER NOT NULL CHECK(from_version >= 1),
    to_version INTEGER NOT NULL UNIQUE CHECK(to_version > from_version),
    name TEXT NOT NULL,
    backup_path TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS object_refs(
    digest TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    byte_length INTEGER NOT NULL CHECK(byte_length >= 0),
    first_seen_at_ms INTEGER NOT NULL CHECK(first_seen_at_ms >= 0),
    validation_timing TEXT NOT NULL DEFAULT 'startup'
        CHECK(validation_timing IN ('startup', 'on_access'))
);

CREATE INDEX IF NOT EXISTS idx_object_refs_validation_timing_digest
ON object_refs(validation_timing, digest);

CREATE TABLE IF NOT EXISTS object_validation(
    digest TEXT PRIMARY KEY REFERENCES object_refs(digest) ON DELETE CASCADE,
    device INTEGER NOT NULL CHECK(device >= 0),
    inode INTEGER NOT NULL CHECK(inode >= 0),
    byte_length INTEGER NOT NULL CHECK(byte_length >= 0),
    modified_at_ns INTEGER NOT NULL CHECK(modified_at_ns >= 0),
    changed_at_ns INTEGER NOT NULL CHECK(changed_at_ns >= 0),
    mode INTEGER NOT NULL CHECK(mode >= 0)
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

CREATE TABLE IF NOT EXISTS legacy_object_refs(
    digest TEXT PRIMARY KEY REFERENCES object_refs(digest)
);

CREATE TABLE IF NOT EXISTS event_object_refs(
    event_id TEXT NOT NULL REFERENCES events(event_id) ON DELETE CASCADE,
    digest TEXT NOT NULL REFERENCES object_refs(digest),
    role TEXT NOT NULL CHECK(role IN ('payload', 'reference')),
    PRIMARY KEY(event_id, digest)
);
CREATE UNIQUE INDEX IF NOT EXISTS event_object_refs_one_payload
    ON event_object_refs(event_id) WHERE role = 'payload';

CREATE TABLE IF NOT EXISTS task_projection(
    task_id TEXT PRIMARY KEY REFERENCES streams(stream_id),
    goal_id TEXT NOT NULL,
    state TEXT NOT NULL,
    active_node_id TEXT,
    ready_frontier_json TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK(revision >= 1),
    updated_at_ms INTEGER NOT NULL CHECK(updated_at_ms >= 0)
);

CREATE TABLE IF NOT EXISTS task_extension_state(
    task_id TEXT NOT NULL REFERENCES task_projection(task_id) ON DELETE CASCADE,
    namespace TEXT NOT NULL,
    state_digest TEXT NOT NULL REFERENCES object_refs(digest),
    event_id TEXT NOT NULL REFERENCES events(event_id),
    revision INTEGER NOT NULL CHECK(revision >= 1),
    legacy INTEGER NOT NULL CHECK(legacy IN (0, 1)),
    PRIMARY KEY(task_id, namespace)
);

CREATE TABLE IF NOT EXISTS leases(
    task_id TEXT PRIMARY KEY REFERENCES task_projection(task_id) ON DELETE CASCADE,
    owner_id TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK(revision >= 1),
    expires_at_ms INTEGER NOT NULL CHECK(expires_at_ms >= 0)
);

CREATE TABLE IF NOT EXISTS board_messages(
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    client_message_id TEXT NOT NULL UNIQUE,
    author_label TEXT NOT NULL,
    message_kind TEXT NOT NULL CHECK(message_kind IN ('note', 'question', 'proposal', 'warning', 'reply')),
    topic TEXT,
    message_digest TEXT NOT NULL REFERENCES object_refs(digest),
    reply_to_client_message_id TEXT REFERENCES board_messages(client_message_id),
    recorded_at_ms INTEGER NOT NULL CHECK(recorded_at_ms >= 0)
);

CREATE TABLE IF NOT EXISTS news_editions(
    edition_id TEXT PRIMARY KEY,
    edition_date TEXT NOT NULL,
    timezone TEXT NOT NULL,
    current_revision INTEGER NOT NULL CHECK(current_revision >= 1),
    current_digest TEXT NOT NULL REFERENCES object_refs(digest),
    created_at_ms INTEGER NOT NULL CHECK(created_at_ms >= 0),
    updated_at_ms INTEGER NOT NULL CHECK(updated_at_ms >= created_at_ms)
);
CREATE INDEX IF NOT EXISTS news_editions_date_id
    ON news_editions(edition_date DESC, edition_id ASC);

CREATE TABLE IF NOT EXISTS news_publications(
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    client_publish_id TEXT NOT NULL UNIQUE,
    edition_id TEXT NOT NULL REFERENCES news_editions(edition_id) ON DELETE CASCADE,
    edition_date TEXT NOT NULL,
    timezone TEXT NOT NULL,
    expected_revision INTEGER NOT NULL CHECK(expected_revision >= 0),
    revision INTEGER NOT NULL CHECK(revision >= 1),
    edition_digest TEXT NOT NULL REFERENCES object_refs(digest),
    recorded_at_ms INTEGER NOT NULL CHECK(recorded_at_ms >= 0),
    UNIQUE(edition_id, revision)
);
"""
