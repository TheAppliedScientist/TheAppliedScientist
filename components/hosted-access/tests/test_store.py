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
