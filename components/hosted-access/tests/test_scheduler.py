"""Exercise the real queue scheduler with a controlled, unpaid review backend."""

import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from store import Store

with tempfile.TemporaryDirectory() as import_dir:
    with patch.dict(os.environ, {
        "HOSTED_HASH_SECRET": "a" * 32,
        "HOSTED_DB_PATH": f"{import_dir}/import.db",
        "HOSTED_UPLOAD_DIR": f"{import_dir}/uploads",
    }):
        import app as gateway


class SchedulerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name) / "jobs.db", "a" * 32)
        self.starts = []
        self.active = set()
        self.events = {}
        self.results = {}
        self.polls = 0
        self.peak = 0
        self.auto_complete = False
        self.schedulers = []
        real_client = httpx.AsyncClient
        transport = httpx.MockTransport(self.backend)
        patches = [
            patch.object(gateway, "store", self.store),
            patch.object(gateway, "MAX_CONCURRENT_REVIEWS", 5),
            patch.object(gateway, "ARCHIVE_DIR", ""),
            patch.object(gateway, "INDEX_MARKER", ""),
            patch.object(gateway.httpx, "AsyncClient", side_effect=lambda **kw:
                         real_client(transport=transport, **kw)),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    async def asyncTearDown(self):
        for task in self.schedulers:
            task.cancel()
        await asyncio.gather(*self.schedulers, return_exceptions=True)

    async def backend(self, request):
        if request.method == "POST":
            job_id = json.loads(request.content)["job_id"]
            self.starts.append(job_id)
            self.active.add(job_id)
            self.peak = max(self.peak, len(self.active))
            self.events.setdefault(job_id, asyncio.Event())
            if self.auto_complete:
                self.events[job_id].set()
            return httpx.Response(200, json={"job_id": job_id})
        job_id = request.url.path.rsplit("/", 1)[-1]
        self.polls += 1
        await self.events[job_id].wait()
        self.active.discard(job_id)
        return httpx.Response(200, json={
            "status": self.results.get(job_id, "success"),
            "review_text": "test feedback",
        })

    def enqueue(self, count):
        return [self.store.enqueue(
            f"192.0.2.{i + 1}", "Paper", "", "paper text",
            max_pending=10, max_per_day=3,
        )["job_id"] for i in range(count)]

    def start_scheduler(self):
        task = asyncio.create_task(gateway.review_worker(poll_interval=0.01))
        self.schedulers.append(task)
        return task

    async def eventually(self, condition):
        async def wait():
            while not condition():
                await asyncio.sleep(0.01)
        await asyncio.wait_for(wait(), timeout=5)

    async def test_five_running_sixth_waits_then_all_finish(self):
        jobs = self.enqueue(7)
        self.start_scheduler()
        await self.eventually(lambda: len(self.starts) == 5)
        await asyncio.sleep(0.05)
        self.assertEqual(len(self.starts), 5)
        self.assertEqual(self.store.get(jobs[5])["status"], "pending")
        self.events[self.starts[0]].set()
        await self.eventually(lambda: len(self.starts) == 6)
        self.assertEqual(self.store.get(jobs[6])["status"], "pending")
        self.auto_complete = True
        for event in self.events.values():
            event.set()
        await self.eventually(lambda: all(
            self.store.get(job)["status"] == "success" for job in jobs))
        self.assertEqual(len(self.starts), 7)
        self.assertEqual(len(set(self.starts)), 7)
        self.assertEqual(self.peak, 5)

    async def test_backend_error_releases_a_slot(self):
        jobs = self.enqueue(6)
        self.start_scheduler()
        await self.eventually(lambda: len(self.starts) == 5)
        failed = self.starts[0]
        self.results[failed] = "error"
        self.events[failed].set()
        await self.eventually(lambda: len(self.starts) == 6)
        self.assertEqual(self.store.get(jobs[0])["status"], "error")
        self.assertEqual(self.peak, 5)

    async def test_restart_resumes_running_jobs_without_resubmission(self):
        jobs = self.enqueue(6)
        first = self.start_scheduler()
        await self.eventually(lambda: self.polls == 5)
        first.cancel()
        await asyncio.gather(first, return_exceptions=True)
        gateway.store = Store(self.store.path, "a" * 32)
        self.start_scheduler()
        await self.eventually(lambda: self.polls == 10)
        self.assertEqual(len(self.starts), 5)
        self.assertEqual(self.store.get(jobs[5])["status"], "pending")
        self.events[self.starts[0]].set()
        await self.eventually(lambda: len(self.starts) == 6)
        self.assertEqual(len(set(self.starts)), 6)
        self.assertEqual(self.peak, 5)
