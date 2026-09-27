"""Queue and retention checks; run with python -m unittest discover -s tests."""

from __future__ import annotations

import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

from store import LimitExceeded, Store


class StoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.path = self.root / "jobs.db"
        self.store = Store(self.path, "a" * 32)

    def test_queue_survives_restart_and_limits_one_active_per_ip(self) -> None:
        first = self.store.enqueue("192.0.2.1", "A", "", "paper", max_pending=2, max_per_day=3)
        restarted = Store(self.path, "a" * 32)
        self.assertEqual(restarted.get(first["job_id"])["queue_position"], 1)
        with self.assertRaises(LimitExceeded):
            restarted.enqueue("192.0.2.1", "B", "", "paper", max_pending=2, max_per_day=3)
        second = restarted.enqueue("192.0.2.2", "B", "", "paper", max_pending=2, max_per_day=3)
        self.assertEqual(restarted.get(second["job_id"])["queue_position"], 2)
        with self.assertRaises(LimitExceeded):
            restarted.enqueue("192.0.2.3", "C", "", "paper", max_pending=2, max_per_day=3)
        restarted.set_running(first["job_id"], "abc123")
        self.assertEqual(restarted.next_job()["id"], first["job_id"])
        restarted.finish(first["job_id"], "success", review_text="review")
        self.assertEqual(restarted.next_job()["id"], second["job_id"])
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute("SELECT latex_content FROM reviews WHERE id=?", (first["job_id"],)).fetchone()[0], "")

    def test_usage_and_daily_review_limits(self) -> None:
        self.store.record_usage("192.0.2.1", "search", 1)
        with self.assertRaises(LimitExceeded):
            self.store.record_usage("192.0.2.1", "search", 1)
        self.store.record_usage("192.0.2.2", "search", 1)
        job = self.store.enqueue("192.0.2.1", "A", "", "paper", max_pending=2, max_per_day=1)
        self.store.finish(job["job_id"], "error", error="unavailable")
        with self.assertRaises(LimitExceeded):
            self.store.enqueue("192.0.2.1", "B", "", "paper", max_pending=2, max_per_day=1)

    def test_global_daily_cap_is_atomic(self) -> None:
        job = self.store.enqueue("192.0.2.1", "A", "", "paper", max_pending=2,
                                 max_per_day=3, max_global_per_day=1)
        self.store.finish(job["job_id"], "success")
        with self.assertRaises(LimitExceeded):
            self.store.enqueue("192.0.2.2", "B", "", "paper", max_pending=2,
                               max_per_day=3, max_global_per_day=1)

    def test_upload_reserves_review_slot_until_conversion_finishes(self) -> None:
        upload = self.store.enqueue_upload(
            "192.0.2.1", "Paper", "", str(self.root / "paper.pdf"), "paper.pdf",
            max_pending=2, max_per_day=3,
        )
        self.assertEqual(upload["status"], "preparing")
        self.assertEqual(self.store.next_job()["input_name"], "paper.pdf")
        with self.assertRaises(LimitExceeded):
            self.store.enqueue("192.0.2.1", "Other", "", "text",
                               max_pending=2, max_per_day=3)
        self.store.set_prepared(upload["job_id"], "converted paper", "Paper")
        ready = self.store.next_job()
        self.assertEqual(ready["status"], "pending")
        self.assertEqual(ready["latex_content"], "converted paper")
        self.assertEqual(ready["input_path"], "")

    def test_source_upload_keeps_file_until_backend_accepts_it(self) -> None:
        upload = self.store.enqueue_upload(
            "192.0.2.1", "", "", str(self.root / "source.zip"), "source.zip",
            max_pending=2, max_per_day=3,
        )
        self.store.set_source_ready(upload["job_id"], "source")
        ready = self.store.next_job()
        self.assertEqual(ready["status"], "pending")
        self.assertEqual(ready["input_path"], str(self.root / "source.zip"))
        self.store.set_running(upload["job_id"], "abc123")
        self.assertEqual(self.store.next_job()["input_path"], "")

    def test_mcp_upload_slot_is_private_single_use_and_expires(self) -> None:
        slot = self.store.reserve_upload(
            "192.0.2.1", "Paper", "", "paper.pdf", max_pending=2, max_per_day=3,
        )
        job_id = slot["job_id"]
        self.assertEqual(slot["status"], "awaiting_upload")
        self.assertIsNone(self.store.next_job())
        self.assertEqual(self.store.reserved_upload_name(job_id), "paper.pdf")
        with self.assertRaises(LimitExceeded):
            self.store.reserve_upload("192.0.2.1", "Other", "", "other.pdf",
                                      max_pending=2, max_per_day=3)
        self.store.attach_reserved_upload(job_id, str(self.root / "paper.pdf"))
        self.assertEqual(self.store.next_job()["status"], "preparing")
        self.assertIsNone(self.store.reserved_upload_name(job_id))
        with self.assertRaises(ValueError):
            self.store.attach_reserved_upload(job_id, str(self.root / "other.pdf"))

        stale = self.store.reserve_upload(
            "192.0.2.2", "Stale", "", "stale.zip", max_pending=2, max_per_day=3,
        )
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE reviews SET submitted_at=? WHERE id=?",
                       (int(time.time()) - 601, stale["job_id"]))
        self.assertIsNone(self.store.reserved_upload_name(stale["job_id"]))
        with self.assertRaises(ValueError):
            self.store.attach_reserved_upload(stale["job_id"], str(self.root / "stale.zip"))

    def test_reservation_does_not_spend_daily_review_capacity(self) -> None:
        first = self.store.reserve_upload(
            "192.0.2.1", "A", "", "a.pdf", max_pending=2,
            max_per_day=1, max_global_per_day=1,
        )
        second = self.store.reserve_upload(
            "192.0.2.2", "B", "", "b.pdf", max_pending=2,
            max_per_day=1, max_global_per_day=1,
        )
        self.store.attach_reserved_upload(first["job_id"], str(self.root / "a.pdf"),
                                          max_per_day=1, max_global_per_day=1)
        with self.assertRaises(LimitExceeded):
            self.store.attach_reserved_upload(second["job_id"], str(self.root / "b.pdf"),
                                              max_per_day=1, max_global_per_day=1)

    def test_existing_database_gains_upload_columns(self) -> None:
        with sqlite3.connect(self.path) as db:
            db.execute("ALTER TABLE reviews RENAME TO old_reviews")
            db.execute("""CREATE TABLE reviews (
                id TEXT PRIMARY KEY, owner TEXT NOT NULL, status TEXT NOT NULL,
                title TEXT NOT NULL, abstract TEXT NOT NULL, latex_content TEXT NOT NULL,
                backend_id TEXT, review_text TEXT NOT NULL DEFAULT '',
                error TEXT NOT NULL DEFAULT '', submitted_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL, finished_at INTEGER
            )""")
            db.execute("DROP TABLE old_reviews")
        Store(self.path, "a" * 32)
        with sqlite3.connect(self.path) as db:
            columns = {row[1] for row in db.execute("PRAGMA table_info(reviews)")}
        self.assertTrue({"input_path", "input_name"} <= columns)

    def test_seven_day_cleanup_removes_review_and_archived_trajectory(self) -> None:
        job = self.store.enqueue("192.0.2.1", "A", "", "paper", max_pending=2, max_per_day=3)
        self.store.set_running(job["job_id"], "abc123")
        self.store.finish(job["job_id"], "success", review_text="review")
        archive = self.root / "archive" / "abc123"
        archive.mkdir(parents=True)
        (archive / "trajectory.json").write_text("{}")
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE reviews SET finished_at=? WHERE id=?", (int(time.time()) - 8 * 86400, job["job_id"]))
        self.store.cleanup(7, self.root / "archive")
        self.assertIsNone(self.store.get(job["job_id"]))
        self.assertFalse(archive.exists())


if __name__ == "__main__":
    unittest.main()
