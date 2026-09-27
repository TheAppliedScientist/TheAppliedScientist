"""The internal source endpoint accepts multipart uploads without invoking a model."""

import asyncio
import unittest
from unittest.mock import patch

from aiohttp import FormData, web
from aiohttp.test_utils import TestClient, TestServer

import review_api


class SourceRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_source_file_is_passed_directly_to_review_job(self) -> None:
        received = []

        async def fake_job(*args, **kwargs):
            received.append((args, kwargs))

        app = web.Application(client_max_size=25 * 1024 * 1024)
        app.router.add_post("/review/start_source", review_api.handle_review_start_source)
        server = TestServer(app)
        client = TestClient(server)
        await client.start_server()
        self.addAsyncCleanup(client.close)
        job_id = "a" * 24
        self.addCleanup(review_api._JOBS.pop, job_id, None)
        form = FormData()
        form.add_field("job_id", job_id)
        form.add_field("title", "Source Paper")
        form.add_field("paper", b"\\documentclass{article}" + b" claim" * 80,
                       filename="paper.tex", content_type="text/plain")
        with patch.object(review_api, "_run_review_job", fake_job):
            response = await client.post("/review/start_source", data=form)
            self.assertEqual(response.status, 200)
            self.assertEqual((await response.json())["job_id"], job_id)
            await asyncio.sleep(0)
        self.assertEqual(received[0][0][:4], (job_id, "", "Source Paper", ""))
        self.assertEqual(received[0][1]["source_upload"][0], "paper.tex")


if __name__ == "__main__":
    unittest.main()
