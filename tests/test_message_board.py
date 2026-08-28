from __future__ import annotations

from pathlib import Path
import sqlite3
import tempfile
import unittest

from ordivon_host.board import HostMessageBoard
from ordivon_host.ops import doctor_state
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
