from __future__ import annotations

from pathlib import Path
import sqlite3
import stat
import tempfile
import unittest

from ordivon_host.board import HostMessageBoard
from ordivon_host.ops import doctor_state
from ordivon_host.ops.backup import create_backup
from ordivon_host.ops.gc import plan_gc
from ordivon_host.storage import HostStorage


class HostMessageBoardTests(unittest.TestCase):
    def test_post_list_reply_and_exact_replay(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with HostStorage(directory) as storage:
                board = HostMessageBoard(storage)
                first = board.post(
                    client_message_id="msg:test:first",
                    author_label="agent:test-a",
                    message_kind="proposal",
                    message="Host should expose a durable collaboration board.",
                    topic="host-potential",
                    reply_to_client_message_id=None,
                    recorded_at_ms=10,
                )
                self.assertEqual(first.admission.value, "created")
                self.assertEqual(first.message.sequence, 1)
                validation_timing = storage.journal.connection.execute(
                    "SELECT validation_timing FROM object_refs WHERE digest = ?",
                    (first.message.message_digest,),
                ).fetchone()[0]
                self.assertEqual(validation_timing, "on_access")

                replay = board.post(
                    client_message_id="msg:test:first",
                    author_label="agent:test-a",
                    message_kind="proposal",
                    message="Host should expose a durable collaboration board.",
                    topic="host-potential",
                    reply_to_client_message_id=None,
                    recorded_at_ms=999,
                )
                self.assertEqual(replay.admission.value, "existing")
                self.assertEqual(replay.message.sequence, 1)
                self.assertEqual(replay.message.recorded_at_ms, 10)

                second = board.post(
                    client_message_id="msg:test:reply",
                    author_label="agent:test-b",
                    message_kind="reply",
                    message="Observed. I will test it as a coordination surface.",
                    topic="host-potential",
                    reply_to_client_message_id="msg:test:first",
                    recorded_at_ms=20,
                )
                self.assertEqual(second.message.sequence, 2)

                listing = board.list(limit=10)
                self.assertEqual(listing["messageCount"], 2)
                self.assertEqual(listing["lastSequence"], 2)
                self.assertEqual(
                    [item["clientMessageId"] for item in listing["messages"]],
                    ["msg:test:first", "msg:test:reply"],
                )
                incremental = board.list(after_sequence=1, limit=10)
                self.assertEqual(len(incremental["messages"]), 1)
                self.assertEqual(
                    incremental["messages"][0]["replyToClientMessageId"],
                    "msg:test:first",
                )

    def test_exact_topic_filter_uses_global_high_water_without_rescanning_other_topics(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with HostStorage(directory) as storage:
                board = HostMessageBoard(storage)
                for index, topic in enumerate(("a", "b", "a", "a", "b"), start=1):
                    board.post(
                        client_message_id=f"msg:topic:{index}",
                        author_label="agent:test",
                        message_kind="note",
                        message=f"message {index}",
                        topic=topic,
                        reply_to_client_message_id=None,
                        recorded_at_ms=index,
                    )

                latest = board.list(limit=2, topic="a")
                self.assertEqual(
                    [item["sequence"] for item in latest["messages"]], [3, 4]
                )
                self.assertEqual(latest["topic"], "a")
                self.assertFalse(latest["hasMore"])
                self.assertEqual(latest["nextAfterSequence"], 5)

                first = board.list(after_sequence=0, limit=1, topic="a")
                self.assertEqual([item["sequence"] for item in first["messages"]], [1])
                self.assertTrue(first["hasMore"])
                self.assertEqual(first["nextAfterSequence"], 1)

                second = board.list(after_sequence=1, limit=1, topic="a")
                self.assertEqual([item["sequence"] for item in second["messages"]], [3])
                self.assertTrue(second["hasMore"])
                self.assertEqual(second["nextAfterSequence"], 3)

                exhausted = board.list(after_sequence=3, limit=2, topic="a")
                self.assertEqual([item["sequence"] for item in exhausted["messages"]], [4])
                self.assertFalse(exhausted["hasMore"])
                self.assertEqual(exhausted["nextAfterSequence"], 5)

                board.post(
                    client_message_id="msg:topic:6",
                    author_label="agent:test",
                    message_kind="note",
                    message="future matching message",
                    topic="a",
                    reply_to_client_message_id=None,
                    recorded_at_ms=6,
                )
                future = board.list(
                    after_sequence=exhausted["nextAfterSequence"], limit=2, topic="a"
                )
                self.assertEqual([item["sequence"] for item in future["messages"]], [6])
                self.assertFalse(future["hasMore"])
                self.assertEqual(future["nextAfterSequence"], 6)

                empty = board.list(after_sequence=0, limit=2, topic="missing")
                self.assertEqual(empty["messages"], [])
                self.assertFalse(empty["hasMore"])
                self.assertEqual(empty["nextAfterSequence"], 6)

                with self.assertRaisesRegex(ValueError, "topic filter"):
                    board.list(topic=" a")

    def test_topic_filter_does_not_skip_matching_append_after_high_water_capture(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with HostStorage(directory) as storage:
                board = HostMessageBoard(storage)
                board.post(
                    client_message_id="msg:race:unrelated",
                    author_label="agent:test",
                    message_kind="note",
                    message="unrelated",
                    topic="other",
                    reply_to_client_message_id=None,
                    recorded_at_ms=1,
                )
                original = storage.journal.board_messages
                inserted = False

                def racing_board_messages(**kwargs):
                    nonlocal inserted
                    if kwargs.get("topic") == "target" and not inserted:
                        inserted = True
                        board.post(
                            client_message_id="msg:race:matching",
                            author_label="agent:test",
                            message_kind="note",
                            message="arrived after the captured high-water",
                            topic="target",
                            reply_to_client_message_id=None,
                            recorded_at_ms=2,
                        )
                    return original(**kwargs)

                storage.journal.board_messages = racing_board_messages  # type: ignore[method-assign]
                first = board.list(after_sequence=1, limit=10, topic="target")
                self.assertEqual(first["messages"], [])
                self.assertEqual(first["messageCount"], 1)
                self.assertEqual(first["lastSequence"], 1)
                self.assertEqual(first["nextAfterSequence"], 1)
                self.assertFalse(first["hasMore"])

                storage.journal.board_messages = original  # type: ignore[method-assign]
                second = board.list(
                    after_sequence=first["nextAfterSequence"], limit=10, topic="target"
                )
                self.assertEqual(
                    [item["clientMessageId"] for item in second["messages"]],
                    ["msg:race:matching"],
                )
                self.assertEqual(second["nextAfterSequence"], 2)

    def test_conflicting_replay_and_missing_reply_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with HostStorage(directory) as storage:
                board = HostMessageBoard(storage)
                board.post(
                    client_message_id="msg:test:identity",
                    author_label="agent:test-a",
                    message_kind="note",
                    message="original",
                    topic=None,
                    reply_to_client_message_id=None,
                    recorded_at_ms=1,
                )
                self.assertEqual(plan_gc(directory, storage=storage)["orphanedObjects"], [])
                with self.assertRaisesRegex(Exception, "different content"):
                    board.post(
                        client_message_id="msg:test:identity",
                        author_label="agent:test-a",
                        message_kind="note",
                        message="changed",
                        topic=None,
                        reply_to_client_message_id=None,
                        recorded_at_ms=2,
                    )
                with self.assertRaisesRegex(Exception, "reply target"):
                    board.post(
                        client_message_id="msg:test:missing-parent",
                        author_label="agent:test-b",
                        message_kind="reply",
                        message="reply",
                        topic=None,
                        reply_to_client_message_id="msg:does-not-exist",
                        recorded_at_ms=3,
                    )
                self.assertEqual(plan_gc(directory, storage=storage)["orphanedObjects"], [])
                self.assertEqual(storage.journal.object_ref_count(), 1)

    def test_board_cas_is_deferred_from_startup_but_not_from_access_doctor_or_backup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "state"
            backup = Path(directory) / "backup"
            with HostStorage(root) as storage:
                receipt = HostMessageBoard(storage).post(
                    client_message_id="msg:test:deferred-cas",
                    author_label="agent:test-a",
                    message_kind="note",
                    message="Board CAS should not tax unrelated Host startup.",
                    topic="scaling",
                    reply_to_client_message_id=None,
                    recorded_at_ms=10,
                )

            with HostStorage(root) as storage:
                self.assertEqual(storage.validation_summary.object_refs, 0)
                retained = storage.journal.object_ref(receipt.message.message_digest)
                self.assertIsNotNone(retained)
                self.assertEqual(retained[1], "on_access")
                self.assertEqual(storage.validation_summary.hashed_objects, 0)
                self.assertEqual(storage.validation_summary.cached_objects, 0)

            object_path = (
                root
                / "objects"
                / f"{receipt.message.message_digest[7:]}.json"
            )
            object_path.write_bytes(b"corrupted-board-cas")

            # Ordinary Host open is intentionally independent from old board payload bytes.
            with HostStorage(root) as storage:
                self.assertEqual(storage.validation_summary.object_refs, 0)
                with self.assertRaises(Exception):
                    HostMessageBoard(storage).list(limit=10)

            report = doctor_state(root)
            self.assertFalse(report["healthy"])
            with self.assertRaises(Exception):
                create_backup(root, backup)

    def test_doctor_detects_board_sequence_gap_before_high_water_count_is_trusted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with HostStorage(root) as storage:
                board = HostMessageBoard(storage)
                for index in range(3):
                    board.post(
                        client_message_id=f"msg:test:gap:{index}",
                        author_label="agent:test-a",
                        message_kind="note",
                        message=f"message-{index}",
                        topic="integrity",
                        reply_to_client_message_id=None,
                        recorded_at_ms=index + 1,
                    )
                self.assertEqual(storage.journal.board_message_count(), 3)

            connection = sqlite3.connect(root / "host.sqlite3")
            try:
                connection.execute("PRAGMA foreign_keys = OFF")
                connection.execute(
                    "DELETE FROM board_messages WHERE client_message_id = ?",
                    ("msg:test:gap:1",),
                )
                connection.commit()
            finally:
                connection.close()

            # High-water projection stays cheap; explicit Doctor owns historical gap proof.
            with HostStorage(root) as storage:
                self.assertEqual(storage.journal.board_message_count(), 3)
            report = doctor_state(root)
            self.assertFalse(report["healthy"])
            check = next(
                item for item in report["checks"] if item["name"] == "board.integrity"
            )
            self.assertEqual(check["status"], "error")
            self.assertIn("sequence history is not contiguous", check["detail"])

    def test_on_access_board_file_is_not_scanned_on_open_but_is_hardened_on_read(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with HostStorage(root) as storage:
                receipt = HostMessageBoard(storage).post(
                    client_message_id="msg:test:lazy-mode",
                    author_label="agent:test",
                    message_kind="note",
                    message="lazy permission validation",
                    topic="scale",
                    reply_to_client_message_id=None,
                    recorded_at_ms=1,
                )
                digest = receipt.message.message_digest
            path = root / "objects" / f"{digest[7:]}.json"
            path.chmod(0o644)
            with HostStorage(root):
                pass
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o644)
            with HostStorage(root) as storage:
                HostMessageBoard(storage).list(limit=1)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_doctor_detects_board_row_object_semantic_divergence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with HostStorage(root) as storage:
                HostMessageBoard(storage).post(
                    client_message_id="msg:test:doctor-integrity",
                    author_label="agent:test-a",
                    message_kind="warning",
                    message="The immutable message must remain bound to its row metadata.",
                    topic="integrity",
                    reply_to_client_message_id=None,
                    recorded_at_ms=10,
                )

            connection = sqlite3.connect(root / "host.sqlite3")
            try:
                connection.execute(
                    "UPDATE board_messages SET author_label = ? WHERE client_message_id = ?",
                    ("agent:tampered", "msg:test:doctor-integrity"),
                )
                connection.commit()
            finally:
                connection.close()

            report = doctor_state(root)
            self.assertFalse(report["healthy"])
            check = next(
                item for item in report["checks"] if item["name"] == "board.integrity"
            )
            self.assertEqual(check["status"], "error")
            self.assertIn("row differs from immutable message object", check["detail"])


if __name__ == "__main__":
    unittest.main()
