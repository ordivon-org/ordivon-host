from __future__ import annotations

from dataclasses import dataclass

from anc_canonical import JsonValue

from .domain import EventAdmission
from .journal import EventConflict
from .storage import HostStorage

_BOARD_OBJECT_KIND = "host-board-message"
_BOARD_SCHEMA_VERSION = 1
_BOARD_KINDS = {"note", "question", "proposal", "warning", "reply"}


@dataclass(frozen=True, slots=True)
class BoardMessage:
    sequence: int
    client_message_id: str
    author_label: str
    message_kind: str
    topic: str | None
    message: str
    reply_to_client_message_id: str | None
    recorded_at_ms: int
    message_digest: str

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "sequence": self.sequence,
            "clientMessageId": self.client_message_id,
            "authorLabel": self.author_label,
            "authorIdentityRole": "self-asserted-label",
            "messageKind": self.message_kind,
            "topic": self.topic,
            "message": self.message,
            "replyToClientMessageId": self.reply_to_client_message_id,
            "recordedAtMs": self.recorded_at_ms,
            "messageDigest": self.message_digest,
            "truthRole": "coordination-message-not-domain-truth",
        }


@dataclass(frozen=True, slots=True)
class BoardPostReceipt:
    admission: EventAdmission
    message: BoardMessage

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schemaVersion": 1,
            "kind": "ordivon.host-board-post-receipt",
            "admission": self.admission.value,
            "message": self.message.to_dict(),
            "truthBoundary": (
                "message persistence only; author label is self-asserted and content does not "
                "become Task priority, execution authority, owner standing, or domain truth"
            ),
        }


class HostMessageBoard:
    def __init__(self, storage: HostStorage) -> None:
        self.storage = storage

    def post(
        self,
        *,
        client_message_id: str,
        author_label: str,
        message_kind: str,
        message: str,
        topic: str | None,
        reply_to_client_message_id: str | None,
        recorded_at_ms: int,
    ) -> BoardPostReceipt:
        self._validate_input(
            client_message_id=client_message_id,
            author_label=author_label,
            message_kind=message_kind,
            message=message,
            topic=topic,
            reply_to_client_message_id=reply_to_client_message_id,
        )
        existing = self._existing_by_client_message_id(client_message_id)
        if existing is not None:
            expected = (
                author_label,
                message_kind,
                topic,
                message,
                reply_to_client_message_id,
            )
            actual = (
                existing.author_label,
                existing.message_kind,
                existing.topic,
                existing.message,
                existing.reply_to_client_message_id,
            )
            if actual != expected:
                raise EventConflict(
                    "board client message identity is already bound to different content"
                )
            return BoardPostReceipt(
                admission=EventAdmission.EXISTING,
                message=existing,
            )
        if (
            reply_to_client_message_id is not None
            and not self._client_message_id_exists(reply_to_client_message_id)
        ):
            raise EventConflict("board reply target does not exist")

        value: JsonValue = {
            "schemaVersion": _BOARD_SCHEMA_VERSION,
            "kind": "ordivon.host-board-message",
            "clientMessageId": client_message_id,
            "authorLabel": author_label,
            "messageKind": message_kind,
            "topic": topic,
            "message": message,
            "replyToClientMessageId": reply_to_client_message_id,
            "truthRole": "coordination-message-not-domain-truth",
        }
        stored = self.storage.put_object(value, kind=_BOARD_OBJECT_KIND)
        admission = self.storage.journal.append_board_message(
            client_message_id=client_message_id,
            author_label=author_label,
            message_kind=message_kind,
            topic=topic,
            message_object=stored,
            reply_to_client_message_id=reply_to_client_message_id,
            recorded_at_ms=recorded_at_ms,
        )
        row = self._by_client_message_id(client_message_id)
        return BoardPostReceipt(admission=admission, message=row)

    def list(
        self, *, after_sequence: int | None = None, limit: int = 50,
        topic: str | None = None, reply_to_client_message_id: str | None = None,
    ) -> dict[str, JsonValue]:
        if after_sequence is not None and (
            type(after_sequence) is not int or after_sequence < 0
        ):
            raise ValueError("board afterSequence must be null or non-negative")
        if type(limit) is not int or limit < 1 or limit > 100:
            raise ValueError("board limit must be in [1, 100]")
        if topic is not None and (
            not isinstance(topic, str)
            or not topic
            or topic != topic.strip()
            or len(topic) > 256
        ):
            raise ValueError("board topic filter must be null or 1-256 trimmed characters")
        if reply_to_client_message_id is not None and (
            not isinstance(reply_to_client_message_id, str)
            or not reply_to_client_message_id
            or reply_to_client_message_id != reply_to_client_message_id.strip()
            or len(reply_to_client_message_id) > 256
        ):
            raise ValueError(
                "board reply target filter must be null or 1-256 trimmed characters"
            )

        if topic is None and reply_to_client_message_id is None:
            pointers = self.storage.journal.board_messages(
                after_sequence=after_sequence,
                limit=limit,
            )
            messages = [self._read(pointer) for pointer in pointers]
            last_sequence = self.storage.journal.board_last_sequence()
            next_after = after_sequence or 0
            if messages:
                next_after = messages[-1].sequence
            has_more = next_after < last_sequence
        else:
            # Freeze a global high-water before the filtered read. Once every matching
            # row through this cut is consumed, advancing to the high-water avoids
            # rescanning unrelated traffic without skipping a concurrent later append.
            last_sequence = self.storage.journal.board_last_sequence()
            requested_limit = limit if after_sequence is None else limit + 1
            pointers = self.storage.journal.board_messages(
                after_sequence=after_sequence,
                limit=requested_limit,
                topic=topic,
                reply_to_client_message_id=reply_to_client_message_id,
                through_sequence=last_sequence,
            )
            if after_sequence is None:
                visible = pointers
                has_more = False
                next_after = last_sequence
            else:
                has_more = len(pointers) > limit
                visible = pointers[:limit]
                if has_more and visible:
                    next_after = visible[-1].sequence
                else:
                    next_after = max(after_sequence, last_sequence)
            messages = [self._read(pointer) for pointer in visible]

        result: dict[str, JsonValue] = {
            "schemaVersion": 1,
            "kind": "ordivon.host-board-list",
            "scope": "host-global-coordination-messages",
            "messages": [item.to_dict() for item in messages],
            "messageCount": (
                last_sequence
                if topic is not None
                else self.storage.journal.board_message_count()
            ),
            "lastSequence": last_sequence,
            "nextAfterSequence": next_after,
            "hasMore": has_more,
            "truthBoundary": (
                "board messages are durable collaboration records only; they are not Tasks, "
                "priority, execution authority, owner standing, or domain truth"
            ),
        }
        if topic is not None:
            result["topic"] = topic
        if reply_to_client_message_id is not None:
            result["replyToClientMessageId"] = reply_to_client_message_id
        return result

    def validate_integrity(self) -> int:
        """Decode and cross-check one stable prefix of current board state.

        Normal Host startup does not scan board history. Message CAS is retained as
        on-access data, so unrelated Host operations do not pay O(board-size) history
        validation cost. Board reads validate accessed row/reference/CAS state; Doctor
        calls this explicit full structural + semantic pass. Messages appended after the
        captured boundary belong to a later validation round.
        """
        self.storage.journal.validate_board_invariants()
        boundary = self.storage.journal.board_last_sequence()
        after = 0
        validated = 0
        while after < boundary:
            pointers = self.storage.journal.board_messages(
                after_sequence=after,
                limit=256,
            )
            if not pointers:
                break
            progressed = False
            for pointer in pointers:
                if pointer.sequence > boundary:
                    break
                self._read(pointer)
                after = pointer.sequence
                validated += 1
                progressed = True
            if not progressed:
                break
        return validated

    def _client_message_id_exists(self, client_message_id: str) -> bool:
        row = self.storage.journal.connection.execute(
            "SELECT 1 FROM board_messages WHERE client_message_id = ?",
            (client_message_id,),
        ).fetchone()
        return row is not None

    def _existing_by_client_message_id(
        self, client_message_id: str
    ) -> BoardMessage | None:
        if not self._client_message_id_exists(client_message_id):
            return None
        return self._by_client_message_id(client_message_id)

    def _by_client_message_id(self, client_message_id: str) -> BoardMessage:
        row = self.storage.journal.connection.execute(
            "SELECT sequence, client_message_id, author_label, message_kind, topic, "
            "message_digest, reply_to_client_message_id, recorded_at_ms "
            "FROM board_messages WHERE client_message_id = ?",
            (client_message_id,),
        ).fetchone()
        if row is None:
            raise EventConflict("board message disappeared after admission")
        pointers = self.storage.journal.board_messages(
            after_sequence=max(0, int(row["sequence"]) - 1),
            limit=1,
        )
        if not pointers or pointers[0].client_message_id != client_message_id:
            raise EventConflict("board message lookup differs after admission")
        return self._read(pointers[0])

    def _read(self, pointer: object) -> BoardMessage:
        retained = self.storage.journal.object_ref(pointer.message_digest)
        if retained is None:
            raise ValueError("Host board message object reference is missing")
        stored, validation_timing = retained
        if stored.kind != _BOARD_OBJECT_KIND or validation_timing not in {
            "on_access",
            "startup",
        }:
            raise ValueError("Host board message object reference metadata differs")
        value = self.storage.objects.get(
            pointer.message_digest, expected_kind=_BOARD_OBJECT_KIND
        )
        if not isinstance(value, dict):
            raise ValueError("Host board message object must be an object")
        expected_keys = {
            "schemaVersion",
            "kind",
            "clientMessageId",
            "authorLabel",
            "messageKind",
            "topic",
            "message",
            "replyToClientMessageId",
            "truthRole",
        }
        if set(value) != expected_keys:
            raise ValueError("Host board message object fields differ")
        if (
            value["schemaVersion"] != _BOARD_SCHEMA_VERSION
            or value["kind"] != "ordivon.host-board-message"
            or value["truthRole"] != "coordination-message-not-domain-truth"
        ):
            raise ValueError("Host board message object identity differs")
        if (
            value["clientMessageId"] != pointer.client_message_id
            or value["authorLabel"] != pointer.author_label
            or value["messageKind"] != pointer.message_kind
            or value["topic"] != pointer.topic
            or value["replyToClientMessageId"] != pointer.reply_to_client_message_id
        ):
            raise ValueError("Host board row differs from immutable message object")
        message = value["message"]
        if not isinstance(message, str):
            raise ValueError("Host board message text is invalid")
        return BoardMessage(
            sequence=pointer.sequence,
            client_message_id=pointer.client_message_id,
            author_label=pointer.author_label,
            message_kind=pointer.message_kind,
            topic=pointer.topic,
            message=message,
            reply_to_client_message_id=pointer.reply_to_client_message_id,
            recorded_at_ms=pointer.recorded_at_ms,
            message_digest=pointer.message_digest,
        )

    @staticmethod
    def _validate_input(
        *,
        client_message_id: str,
        author_label: str,
        message_kind: str,
        message: str,
        topic: str | None,
        reply_to_client_message_id: str | None,
    ) -> None:
        if (
            not isinstance(client_message_id, str)
            or not client_message_id
            or client_message_id != client_message_id.strip()
            or len(client_message_id) > 256
        ):
            raise ValueError("board clientMessageId must be 1-256 trimmed characters")
        if (
            not isinstance(author_label, str)
            or not author_label
            or author_label != author_label.strip()
            or len(author_label) > 128
        ):
            raise ValueError("board authorLabel must be 1-128 trimmed characters")
        if message_kind not in _BOARD_KINDS:
            raise ValueError("board messageKind is invalid")
        if not isinstance(message, str) or not message.strip() or len(message) > 4096:
            raise ValueError("board message must contain 1-4096 characters")
        if topic is not None and (
            not isinstance(topic, str)
            or not topic
            or topic != topic.strip()
            or len(topic) > 256
        ):
            raise ValueError("board topic must be null or 1-256 trimmed characters")
        if reply_to_client_message_id is not None and (
            not isinstance(reply_to_client_message_id, str)
            or not reply_to_client_message_id
            or reply_to_client_message_id != reply_to_client_message_id.strip()
            or len(reply_to_client_message_id) > 256
        ):
            raise ValueError(
                "board replyToClientMessageId must be null or 1-256 trimmed characters"
            )
