from __future__ import annotations

import base64
from dataclasses import dataclass
import json
import re
from typing import Any

from anc_canonical import JsonValue

from .domain import EventAdmission
from .journal import EventConflict
from .storage import HostStorage

_NEWS_OBJECT_KIND = "host-news-edition"
_NEWS_SCHEMA_VERSION = 1
_NEWS_TRUTH_ROLE = "external-news-projection-not-world-truth"
_NEWS_SECTIONS = {
    "today",
    "deep_story",
    "radar",
    "research",
    "industry",
    "market",
    "slow_variable",
    "anomaly",
    "unresolved",
    "judgment",
    "catalyst",
}
_NEWS_STATUSES = {"new", "followup", "correction", "closed"}
_NEWS_CONFIDENCE = {"high", "medium", "low"}
_SOURCE_TYPES = {"official", "company", "paper", "regulatory", "news", "dataset", "other"}
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_MAX_EDITION_BYTES = 262_144


@dataclass(frozen=True, slots=True)
class NewsPublication:
    sequence: int
    client_publish_id: str
    edition_id: str
    expected_revision: int
    revision: int
    edition_digest: str
    recorded_at_ms: int

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "sequence": self.sequence,
            "clientPublishId": self.client_publish_id,
            "editionId": self.edition_id,
            "expectedRevision": self.expected_revision,
            "revision": self.revision,
            "editionDigest": self.edition_digest,
            "recordedAtMs": self.recorded_at_ms,
        }


@dataclass(frozen=True, slots=True)
class NewsPublishReceipt:
    admission: EventAdmission
    publication: NewsPublication

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schemaVersion": 1,
            "kind": "ordivon.host-news-publish-receipt",
            "admission": self.admission.value,
            "publication": self.publication.to_dict(),
            "truthBoundary": (
                "Host is authoritative only for publication persistence, revision and exact "
                "edition bytes; external claims remain revalidatable source claims and do not "
                "become World, owner or domain truth merely because Host retained them"
            ),
        }


class HostDailyNews:
    def __init__(self, storage: HostStorage) -> None:
        self.storage = storage

    def publish(
        self,
        *,
        client_publish_id: str,
        edition_id: str,
        expected_revision: int,
        edition: dict[str, Any],
        recorded_at_ms: int,
    ) -> NewsPublishReceipt:
        self._validate_publish_identity(
            client_publish_id=client_publish_id,
            edition_id=edition_id,
            expected_revision=expected_revision,
            recorded_at_ms=recorded_at_ms,
        )
        normalized = self._normalize_edition(edition, edition_id=edition_id)
        encoded = json.dumps(
            normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        if len(encoded) > _MAX_EDITION_BYTES:
            raise ValueError("news edition exceeds 262144 UTF-8 bytes")
        stored = self.storage.put_object(normalized, kind=_NEWS_OBJECT_KIND)
        admission = self.storage.journal.append_news_publication(
            client_publish_id=client_publish_id,
            edition_id=edition_id,
            edition_date=str(normalized["editionDate"]),
            timezone=str(normalized["timezone"]),
            expected_revision=expected_revision,
            edition_object=stored,
            recorded_at_ms=recorded_at_ms,
        )
        publication = self._by_client_publish_id(client_publish_id)
        return NewsPublishReceipt(admission=admission, publication=publication)

    def read(
        self,
        *,
        edition_id: str | None = None,
        revision: int | None = None,
        sections: tuple[str, ...] = (),
        categories: tuple[str, ...] = (),
        thread_keys: tuple[str, ...] = (),
        include_rendered_brief: bool = False,
    ) -> dict[str, JsonValue]:
        if edition_id is not None:
            self._validate_edition_id(edition_id)
        if revision is not None and (type(revision) is not int or revision < 1):
            raise ValueError("news revision must be null or a positive integer")
        for section in sections:
            if section not in _NEWS_SECTIONS:
                raise ValueError("news section filter is invalid")
        self._validate_filter_values(categories, label="category")
        self._validate_filter_values(thread_keys, label="threadKey")
        pointer = self.storage.journal.news_edition_pointer(
            edition_id=edition_id, revision=revision
        )
        if pointer is None:
            raise KeyError("news edition does not exist")
        value = self._read_pointer(pointer)
        items = value["items"]
        assert isinstance(items, list)
        filtered: list[JsonValue] = []
        section_set = set(sections)
        category_set = set(categories)
        thread_set = set(thread_keys)
        for item in items:
            assert isinstance(item, dict)
            if section_set and item["section"] not in section_set:
                continue
            if category_set and item["category"] not in category_set:
                continue
            if thread_set and item["threadKey"] not in thread_set:
                continue
            filtered.append(dict(item))
        result = dict(value)
        result["items"] = filtered
        result["revision"] = pointer.revision
        result["editionDigest"] = pointer.edition_digest
        result["recordedAtMs"] = pointer.recorded_at_ms
        if not include_rendered_brief:
            result["renderedBrief"] = None
        return {
            "schemaVersion": 1,
            "kind": "ordivon.host-news-read",
            "edition": result,
            "filters": {
                "sections": list(sections),
                "categories": list(categories),
                "threadKeys": list(thread_keys),
                "includeRenderedBrief": include_rendered_brief,
            },
            "truthBoundary": (
                "retained external-news projection only; source facts and interpretations must "
                "be revalidated with their owning external evidence"
            ),
        }

    def list(
        self,
        *,
        limit: int = 30,
        cursor: str | None = None,
        from_date: str | None = None,
        to_date: str | None = None,
    ) -> dict[str, JsonValue]:
        if type(limit) is not int or limit < 1 or limit > 100:
            raise ValueError("news list limit must be in [1, 100]")
        if from_date is not None:
            self._validate_date(from_date)
        if to_date is not None:
            self._validate_date(to_date)
        if from_date is not None and to_date is not None and from_date > to_date:
            raise ValueError("news fromDate cannot be after toDate")
        after = self._parse_cursor(cursor, from_date=from_date, to_date=to_date)
        rows = self.storage.journal.news_editions(
            limit=limit + 1,
            after=after,
            from_date=from_date,
            to_date=to_date,
        )
        has_more = len(rows) > limit
        visible = rows[:limit]
        next_cursor = None
        if has_more and visible:
            last = visible[-1]
            next_cursor = self._cursor(
                last.edition_date,
                last.edition_id,
                from_date=from_date,
                to_date=to_date,
            )
        return {
            "schemaVersion": 1,
            "kind": "ordivon.host-news-list",
            "scope": "daily-external-news-editions",
            "editions": [item.to_dict() for item in visible],
            "hasMore": has_more,
            "nextCursor": next_cursor,
            "truthBoundary": "publication inventory only; not external-world truth",
        }

    def validate_integrity(self) -> int:
        validated = 0
        for pointer in self.storage.journal.news_all_publications():
            self._read_pointer(pointer)
            validated += 1
        return validated

    def _by_client_publish_id(self, client_publish_id: str) -> NewsPublication:
        pointer = self.storage.journal.news_publication_by_client_id(client_publish_id)
        if pointer is None:
            raise EventConflict("news publication disappeared after admission")
        return NewsPublication(
            sequence=pointer.sequence,
            client_publish_id=pointer.client_publish_id,
            edition_id=pointer.edition_id,
            expected_revision=pointer.expected_revision,
            revision=pointer.revision,
            edition_digest=pointer.edition_digest,
            recorded_at_ms=pointer.recorded_at_ms,
        )

    def _read_pointer(self, pointer: object) -> dict[str, JsonValue]:
        retained = self.storage.journal.object_ref(pointer.edition_digest)
        if retained is None:
            raise ValueError("Host news edition object reference is missing")
        stored, validation_timing = retained
        if stored.kind != _NEWS_OBJECT_KIND or validation_timing not in {
            "on_access",
            "startup",
        }:
            raise ValueError("Host news edition object reference metadata differs")
        value = self.storage.objects.get(
            pointer.edition_digest, expected_kind=_NEWS_OBJECT_KIND
        )
        if not isinstance(value, dict):
            raise ValueError("Host news edition object must be an object")
        normalized = self._normalize_edition(value, edition_id=pointer.edition_id)
        if normalized != value:
            raise ValueError("Host news edition object is not canonical")
        if (
            value["editionDate"] != pointer.edition_date
            or value["timezone"] != pointer.timezone
        ):
            raise EventConflict("news edition row differs from immutable edition object")
        return dict(value)

    @staticmethod
    def _validate_publish_identity(
        *, client_publish_id: str, edition_id: str, expected_revision: int, recorded_at_ms: int
    ) -> None:
        if (
            not isinstance(client_publish_id, str)
            or not client_publish_id
            or client_publish_id != client_publish_id.strip()
            or len(client_publish_id) > 256
        ):
            raise ValueError("news clientPublishId must be 1-256 trimmed characters")
        HostDailyNews._validate_edition_id(edition_id)
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValueError("news expectedRevision must be a non-negative integer")
        if type(recorded_at_ms) is not int or recorded_at_ms < 0:
            raise ValueError("news recorded time is invalid")

    @staticmethod
    def _validate_edition_id(value: str) -> None:
        if (
            not isinstance(value, str)
            or not value.startswith("news:")
            or value != value.strip()
            or len(value) > 512
        ):
            raise ValueError("news editionId must start with news: and be trimmed")

    @staticmethod
    def _validate_date(value: str) -> None:
        if not isinstance(value, str) or _DATE_RE.fullmatch(value) is None:
            raise ValueError("news date must use YYYY-MM-DD")

    @staticmethod
    def _validate_filter_values(values: tuple[str, ...], *, label: str) -> None:
        if len(values) > 32:
            raise ValueError(f"news {label} filters cannot exceed 32 values")
        for value in values:
            if not isinstance(value, str) or not value or value != value.strip() or len(value) > 256:
                raise ValueError(f"news {label} filter is invalid")

    @classmethod
    def _normalize_edition(
        cls, raw: dict[str, Any], *, edition_id: str
    ) -> dict[str, JsonValue]:
        if not isinstance(raw, dict):
            raise ValueError("news edition must be an object")
        expected = {
            "schemaVersion",
            "kind",
            "truthRole",
            "editionId",
            "editionDate",
            "timezone",
            "generatedAtMs",
            "coverageStartMs",
            "coverageEndMs",
            "marketCutoffMs",
            "producerLabel",
            "renderedBrief",
            "items",
        }
        if set(raw) != expected:
            raise ValueError("news edition fields differ")
        if (
            raw["schemaVersion"] != _NEWS_SCHEMA_VERSION
            or raw["kind"] != "ordivon.host-news-edition"
            or raw["truthRole"] != _NEWS_TRUTH_ROLE
            or raw["editionId"] != edition_id
        ):
            raise ValueError("news edition identity differs")
        edition_date = raw["editionDate"]
        cls._validate_date(edition_date)
        timezone = raw["timezone"]
        if not isinstance(timezone, str) or not timezone or timezone != timezone.strip() or len(timezone) > 128:
            raise ValueError("news timezone is invalid")
        generated_at_ms = cls._optional_time(raw["generatedAtMs"], required=True)
        coverage_start_ms = cls._optional_time(raw["coverageStartMs"])
        coverage_end_ms = cls._optional_time(raw["coverageEndMs"])
        if (coverage_start_ms is None) != (coverage_end_ms is None):
            raise ValueError("news coverage start/end must both be null or timestamps")
        if coverage_start_ms is not None and coverage_end_ms is not None and coverage_start_ms > coverage_end_ms:
            raise ValueError("news coverage start cannot be after coverage end")
        market_cutoff_ms = cls._optional_time(raw["marketCutoffMs"])
        producer = raw["producerLabel"]
        if not isinstance(producer, str) or not producer or producer != producer.strip() or len(producer) > 128:
            raise ValueError("news producerLabel is invalid")
        rendered = raw["renderedBrief"]
        if rendered is not None and (not isinstance(rendered, str) or len(rendered) > 40_000):
            raise ValueError("news renderedBrief must be null or at most 40000 characters")
        items = raw["items"]
        if not isinstance(items, list) or not items or len(items) > 64:
            raise ValueError("news items must contain 1-64 entries")
        normalized_items: list[JsonValue] = []
        ids: set[str] = set()
        for item in items:
            normalized = cls._normalize_item(item)
            item_id = str(normalized["itemId"])
            if item_id in ids:
                raise ValueError("news itemId must be unique within an edition")
            ids.add(item_id)
            normalized_items.append(normalized)
        return {
            "schemaVersion": _NEWS_SCHEMA_VERSION,
            "kind": "ordivon.host-news-edition",
            "truthRole": _NEWS_TRUTH_ROLE,
            "editionId": edition_id,
            "editionDate": edition_date,
            "timezone": timezone,
            "generatedAtMs": generated_at_ms,
            "coverageStartMs": coverage_start_ms,
            "coverageEndMs": coverage_end_ms,
            "marketCutoffMs": market_cutoff_ms,
            "producerLabel": producer,
            "renderedBrief": rendered,
            "items": normalized_items,
        }

    @classmethod
    def _normalize_item(cls, raw: object) -> dict[str, JsonValue]:
        if not isinstance(raw, dict):
            raise ValueError("news item must be an object")
        expected = {
            "itemId", "section", "category", "headline", "summary", "novelty",
            "threadKey", "continuationOf", "status", "importance", "confidence",
            "eventAtMs", "publishedAtMs", "observedAtMs", "evidence"
        }
        if set(raw) != expected:
            raise ValueError("news item fields differ")
        item_id = cls._trimmed(raw["itemId"], "itemId", 256)
        section = raw["section"]
        if section not in _NEWS_SECTIONS:
            raise ValueError("news item section is invalid")
        category = cls._trimmed(raw["category"], "category", 64)
        headline = cls._trimmed(raw["headline"], "headline", 512)
        summary = cls._trimmed(raw["summary"], "summary", 4096)
        novelty = cls._nullable_trimmed(raw["novelty"], "novelty", 2048)
        thread_key = cls._nullable_trimmed(raw["threadKey"], "threadKey", 256)
        continuation = cls._nullable_trimmed(raw["continuationOf"], "continuationOf", 256)
        status = raw["status"]
        if status not in _NEWS_STATUSES:
            raise ValueError("news item status is invalid")
        importance = raw["importance"]
        if type(importance) is not int or importance < 1 or importance > 5:
            raise ValueError("news item importance must be in [1, 5]")
        confidence = raw["confidence"]
        if confidence is not None and confidence not in _NEWS_CONFIDENCE:
            raise ValueError("news item confidence is invalid")
        event_at = cls._optional_time(raw["eventAtMs"])
        published_at = cls._optional_time(raw["publishedAtMs"])
        observed_at = cls._optional_time(raw["observedAtMs"])
        evidence = raw["evidence"]
        if not isinstance(evidence, list) or not evidence or len(evidence) > 16:
            raise ValueError("news item evidence must contain 1-16 entries")
        normalized_evidence = [cls._normalize_evidence(item) for item in evidence]
        return {
            "itemId": item_id,
            "section": section,
            "category": category,
            "headline": headline,
            "summary": summary,
            "novelty": novelty,
            "threadKey": thread_key,
            "continuationOf": continuation,
            "status": status,
            "importance": importance,
            "confidence": confidence,
            "eventAtMs": event_at,
            "publishedAtMs": published_at,
            "observedAtMs": observed_at,
            "evidence": normalized_evidence,
        }

    @classmethod
    def _normalize_evidence(cls, raw: object) -> dict[str, JsonValue]:
        if not isinstance(raw, dict) or set(raw) != {
            "sourceType", "sourceId", "publisher", "title", "publishedAtMs"
        }:
            raise ValueError("news evidence fields differ")
        source_type = raw["sourceType"]
        if source_type not in _SOURCE_TYPES:
            raise ValueError("news evidence sourceType is invalid")
        source_id = cls._trimmed(raw["sourceId"], "sourceId", 2048)
        if re.match(r"^turn\d+(?:search|news|view|fetch|academia|reddit|youtube|image|product|business)", source_id):
            raise ValueError("news sourceId cannot use an ephemeral conversation citation id")
        return {
            "sourceType": source_type,
            "sourceId": source_id,
            "publisher": cls._trimmed(raw["publisher"], "publisher", 256),
            "title": cls._trimmed(raw["title"], "title", 512),
            "publishedAtMs": cls._optional_time(raw["publishedAtMs"]),
        }

    @staticmethod
    def _trimmed(value: object, label: str, maximum: int) -> str:
        if not isinstance(value, str) or not value or value != value.strip() or len(value) > maximum:
            raise ValueError(f"news {label} is invalid")
        return value

    @classmethod
    def _nullable_trimmed(cls, value: object, label: str, maximum: int) -> str | None:
        if value is None:
            return None
        return cls._trimmed(value, label, maximum)

    @staticmethod
    def _optional_time(value: object, *, required: bool = False) -> int | None:
        if value is None:
            if required:
                raise ValueError("news required timestamp is null")
            return None
        if type(value) is not int or value < 0:
            raise ValueError("news timestamp is invalid")
        return value

    @staticmethod
    def _cursor(
        edition_date: str, edition_id: str, *, from_date: str | None, to_date: str | None
    ) -> str:
        payload = json.dumps(
            {"v": 1, "editionDate": edition_date, "editionId": edition_id, "fromDate": from_date, "toDate": to_date},
            sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")

    @staticmethod
    def _parse_cursor(
        value: str | None, *, from_date: str | None, to_date: str | None
    ) -> tuple[str, str] | None:
        if value is None:
            return None
        if not isinstance(value, str) or not value or len(value) > 2048:
            raise ValueError("news cursor is invalid")
        try:
            padding = "=" * (-len(value) % 4)
            payload = json.loads(base64.urlsafe_b64decode((value + padding).encode("ascii")))
        except (ValueError, UnicodeEncodeError, json.JSONDecodeError) as error:
            raise ValueError("news cursor is invalid") from error
        if (
            not isinstance(payload, dict)
            or set(payload) != {"v", "editionDate", "editionId", "fromDate", "toDate"}
            or payload.get("v") != 1
            or payload.get("fromDate") != from_date
            or payload.get("toDate") != to_date
        ):
            raise ValueError("news cursor does not match the current query scope")
        edition_date = payload.get("editionDate")
        edition_id = payload.get("editionId")
        if not isinstance(edition_date, str) or not isinstance(edition_id, str):
            raise ValueError("news cursor is invalid")
        HostDailyNews._validate_date(edition_date)
        HostDailyNews._validate_edition_id(edition_id)
        return edition_date, edition_id
