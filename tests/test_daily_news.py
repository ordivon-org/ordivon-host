from __future__ import annotations

from pathlib import Path
import sqlite3
import tempfile
import unittest

from ordivon_host.journal import RevisionConflict
from ordivon_host.mcp_server import _host_status, _list_news, _read_news
from ordivon_host.news import HostDailyNews
from ordivon_host.objects import ObjectMissing
from ordivon_host.ops import doctor_state
from ordivon_host.ops.backup import create_backup
from ordivon_host.storage import HostStorage


def edition(*, edition_id: str = "news:daily:2026-08-28:Asia-Shanghai", headline: str = "Nvidia demand remains strong", source_id: str = "https://example.com/nvidia") -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "kind": "ordivon.host-news-edition",
        "truthRole": "external-news-projection-not-world-truth",
        "editionId": edition_id,
        "editionDate": "2026-08-28",
        "timezone": "Asia/Shanghai",
        "generatedAtMs": 1000,
        "coverageStartMs": 0,
        "coverageEndMs": 1000,
        "marketCutoffMs": 900,
        "producerLabel": "daily-world-model-brief",
        "renderedBrief": "# Daily brief\n\nHuman-readable projection.",
        "items": [
            {
                "itemId": "nvidia-demand",
                "section": "deep_story",
                "category": "ai-compute",
                "headline": headline,
                "summary": "Demand evidence remains strong while system costs rise.",
                "novelty": "New quarterly evidence strengthens infrastructure demand.",
                "threadKey": "ai:nvidia-infrastructure-demand",
                "continuationOf": None,
                "status": "followup",
                "importance": 5,
                "confidence": "high",
                "eventAtMs": 800,
                "publishedAtMs": 850,
                "observedAtMs": 950,
                "evidence": [
                    {
                        "sourceType": "company",
                        "sourceId": source_id,
                        "publisher": "NVIDIA",
                        "title": "Quarterly results",
                        "publishedAtMs": 850,
                    }
                ],
            },
            {
                "itemId": "hormuz-flow",
                "section": "anomaly",
                "category": "geopolitics",
                "headline": "Diplomacy improves while physical traffic stays weak",
                "summary": "Market and physical state remain separated.",
                "novelty": None,
                "threadKey": "geopolitics:hormuz-shipping",
                "continuationOf": "hormuz-flow-2026-08-27",
                "status": "followup",
                "importance": 4,
                "confidence": "medium",
                "eventAtMs": None,
                "publishedAtMs": 900,
                "observedAtMs": 975,
                "evidence": [
                    {
                        "sourceType": "news",
                        "sourceId": "https://example.com/hormuz",
                        "publisher": "Reuters",
                        "title": "Shipping update",
                        "publishedAtMs": 900,
                    }
                ],
            },
        ],
    }


class HostDailyNewsTests(unittest.TestCase):
    def test_publish_exact_replay_revision_read_and_filters(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with HostStorage(directory) as storage:
                news = HostDailyNews(storage)
                created = news.publish(
                    client_publish_id="news-publish:2026-08-28:v1",
                    edition_id="news:daily:2026-08-28:Asia-Shanghai",
                    expected_revision=0,
                    edition=edition(),
                    recorded_at_ms=1100,
                )
                self.assertEqual(created.admission.value, "created")
                self.assertEqual(created.publication.revision, 1)

                replay = news.publish(
                    client_publish_id="news-publish:2026-08-28:v1",
                    edition_id="news:daily:2026-08-28:Asia-Shanghai",
                    expected_revision=0,
                    edition=edition(),
                    recorded_at_ms=9999,
                )
                self.assertEqual(replay.admission.value, "existing")
                self.assertEqual(replay.publication.sequence, created.publication.sequence)
                self.assertEqual(replay.publication.recorded_at_ms, 1100)

                with self.assertRaisesRegex(Exception, "different content"):
                    news.publish(
                        client_publish_id="news-publish:2026-08-28:v1",
                        edition_id="news:daily:2026-08-28:Asia-Shanghai",
                        expected_revision=0,
                        edition=edition(headline="same replay id, changed bytes"),
                        recorded_at_ms=1110,
                    )

                with self.assertRaises(RevisionConflict):
                    news.publish(
                        client_publish_id="news-publish:2026-08-28:stale",
                        edition_id="news:daily:2026-08-28:Asia-Shanghai",
                        expected_revision=0,
                        edition=edition(headline="stale correction"),
                        recorded_at_ms=1200,
                    )

                corrected = news.publish(
                    client_publish_id="news-publish:2026-08-28:v2",
                    edition_id="news:daily:2026-08-28:Asia-Shanghai",
                    expected_revision=1,
                    edition=edition(headline="Nvidia demand remains strong; financing quality matters"),
                    recorded_at_ms=1300,
                )
                self.assertEqual(corrected.publication.revision, 2)

                current = news.read(include_rendered_brief=False)
                self.assertEqual(current["edition"]["revision"], 2)
                self.assertIsNone(current["edition"]["renderedBrief"])
                old = news.read(
                    edition_id="news:daily:2026-08-28:Asia-Shanghai", revision=1, include_rendered_brief=True
                )
                self.assertEqual(old["edition"]["revision"], 1)
                self.assertEqual(old["edition"]["items"][0]["headline"], "Nvidia demand remains strong")
                filtered = news.read(
                    sections=("anomaly",), thread_keys=("geopolitics:hormuz-shipping",)
                )
                self.assertEqual([item["itemId"] for item in filtered["edition"]["items"]], ["hormuz-flow"])

    def test_mcp_news_reads_defer_unrelated_global_cas_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with HostStorage(root) as storage:
                HostDailyNews(storage).publish(
                    client_publish_id="news-publish:operation-local:v1",
                    edition_id="news:daily:2026-08-28:Asia-Shanghai",
                    expected_revision=0,
                    edition=edition(),
                    recorded_at_ms=1100,
                )
                missing_digest = "sha256:" + "f" * 64
                storage.journal.connection.execute(
                    "INSERT INTO object_refs("
                    "digest, kind, byte_length, first_seen_at_ms, validation_timing"
                    ") VALUES (?, ?, ?, ?, ?)",
                    (missing_digest, "test-unrelated", 1, 1200, "startup"),
                )
                storage.journal.connection.commit()

            listing = _list_news(
                root, limit=10, cursor=None, from_date=None, to_date=None
            )
            self.assertEqual(listing["editions"][0]["editionDate"], "2026-08-28")
            current = _read_news(
                root,
                edition_id="news:daily:2026-08-28:Asia-Shanghai",
                revision=None,
                sections=(),
                categories=(),
                thread_keys=(),
                include_rendered_brief=False,
            )
            self.assertEqual(current["edition"]["revision"], 1)
            self.assertIsNone(current["edition"]["renderedBrief"])

            with self.assertRaises(ObjectMissing):
                _host_status(root, detail="summary", recent_limit=0)

    def test_list_is_date_scoped_and_cursor_bound(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with HostStorage(directory) as storage:
                news = HostDailyNews(storage)
                for day in ("26", "27", "28"):
                    edition_id = f"news:daily:2026-08-{day}:Asia-Shanghai"
                    payload = edition(edition_id=edition_id)
                    payload["editionDate"] = f"2026-08-{day}"
                    news.publish(
                        client_publish_id=f"publish:{day}", edition_id=edition_id, expected_revision=0, edition=payload, recorded_at_ms=int(day)
                    )
                first = news.list(limit=2, from_date="2026-08-26", to_date="2026-08-28")
                self.assertEqual([x["editionDate"] for x in first["editions"]], ["2026-08-28", "2026-08-27"])
                self.assertTrue(first["hasMore"])
                second = news.list(limit=2, cursor=first["nextCursor"], from_date="2026-08-26", to_date="2026-08-28")
                self.assertEqual([x["editionDate"] for x in second["editions"]], ["2026-08-26"])
                with self.assertRaisesRegex(ValueError, "query scope"):
                    news.list(limit=2, cursor=first["nextCursor"], from_date="2026-08-27", to_date="2026-08-28")

    def test_news_cas_is_deferred_from_startup_but_not_from_access_doctor_or_backup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "state"
            backup = Path(directory) / "backup"
            with HostStorage(root) as storage:
                receipt = HostDailyNews(storage).publish(
                    client_publish_id="publish:deferred",
                    edition_id="news:daily:2026-08-28:Asia-Shanghai",
                    expected_revision=0,
                    edition=edition(),
                    recorded_at_ms=1,
                )
                retained = storage.journal.object_ref(receipt.publication.edition_digest)
                self.assertIsNotNone(retained)
                self.assertEqual(retained[1], "on_access")

            with HostStorage(root) as storage:
                self.assertEqual(storage.validation_summary.object_refs, 0)
                self.assertEqual(storage.validation_summary.hashed_objects, 0)
                self.assertEqual(storage.validation_summary.cached_objects, 0)
                current = HostDailyNews(storage).read()
                self.assertEqual(current["edition"]["revision"], 1)

            object_path = root / "objects" / f"{receipt.publication.edition_digest[7:]}.json"
            object_path.write_bytes(b"corrupted-news-cas")

            # Ordinary Host open proves only cheap Journal relations for old News history.
            with HostStorage(root) as storage:
                self.assertEqual(storage.validation_summary.object_refs, 0)
                with self.assertRaises(Exception):
                    HostDailyNews(storage).read()

            report = doctor_state(root)
            self.assertFalse(report["healthy"])
            with self.assertRaises(Exception):
                create_backup(root, backup)

    def test_ephemeral_web_citation_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with HostStorage(directory) as storage:
                with self.assertRaisesRegex(ValueError, "ephemeral conversation citation"):
                    HostDailyNews(storage).publish(
                        client_publish_id="bad-source",
                        edition_id="news:daily:2026-08-28:Asia-Shanghai",
                        expected_revision=0,
                        edition=edition(source_id="turn42news3"),
                        recorded_at_ms=1,
                    )

    def test_integrity_detects_head_metadata_tamper(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with HostStorage(root) as storage:
                HostDailyNews(storage).publish(
                    client_publish_id="publish:integrity",
                    edition_id="news:daily:2026-08-28:Asia-Shanghai",
                    expected_revision=0, edition=edition(), recorded_at_ms=1
                )
            connection = sqlite3.connect(root / "host.sqlite3")
            try:
                connection.execute(
                    "UPDATE news_publications SET timezone = 'UTC' WHERE client_publish_id = 'publish:integrity'"
                )
                connection.commit()
            finally:
                connection.close()
            # Cheap Journal relations are startup-critical even though News CAS bytes are not.
            with self.assertRaisesRegex(Exception, "news publication differs"):
                HostStorage(root)
            report = doctor_state(root)
            self.assertFalse(report["healthy"])


if __name__ == "__main__":
    unittest.main()
