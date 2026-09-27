"""Durable, single-process queue and rate accounting for the hosted APIs."""

from __future__ import annotations

import hmac
import hashlib
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path


TERMINAL = ("success", "error", "timeout")


class LimitExceeded(Exception):
    def __init__(self, message: str, retry_after: int = 60):
        super().__init__(message)
        self.retry_after = retry_after


class Store:
    def __init__(self, path: str | Path, secret: str):
        if len(secret) < 32:
            raise ValueError("HOSTED_HASH_SECRET must be at least 32 characters")
        self.path = Path(path)
        self.secret = secret.encode()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS reviews (
                    id TEXT PRIMARY KEY,
                    owner TEXT NOT NULL,
                    status TEXT NOT NULL,
                    title TEXT NOT NULL,
                    abstract TEXT NOT NULL,
                    latex_content TEXT NOT NULL,
                    backend_id TEXT,
                    review_text TEXT NOT NULL DEFAULT '',
                    error TEXT NOT NULL DEFAULT '',
                    submitted_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    finished_at INTEGER
                );
                CREATE INDEX IF NOT EXISTS reviews_owner_status ON reviews(owner, status);
                CREATE INDEX IF NOT EXISTS reviews_submitted_at ON reviews(submitted_at);
                CREATE TABLE IF NOT EXISTS usage (
                    owner TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS usage_lookup ON usage(owner, kind, at);
            """)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=10000")
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def owner(self, ip: str) -> str:
        return hmac.new(self.secret, ip.encode(), hashlib.sha256).hexdigest()

    def record_usage(self, ip: str, kind: str, max_per_hour: int) -> None:
        owner = self.owner(ip)
        now = int(time.time())
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            count = db.execute(
                "SELECT COUNT(*) FROM usage WHERE owner=? AND kind=? AND at>?",
                (owner, kind, now - 3600),
            ).fetchone()[0]
            if count >= max_per_hour:
                raise LimitExceeded(f"{kind} limit reached; try again later", 3600)
            db.execute("INSERT INTO usage(owner,kind,at) VALUES(?,?,?)", (owner, kind, now))

    def enqueue(self, ip: str, title: str, abstract: str, latex_content: str,
                *, max_pending: int, max_per_day: int,
                max_global_per_day: int = 24) -> dict:
        owner = self.owner(ip)
        now = int(time.time())
        job_id = secrets.token_urlsafe(24)
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute(
                "SELECT 1 FROM reviews WHERE owner=? AND status IN ('pending','running') LIMIT 1",
                (owner,),
            ).fetchone():
                raise LimitExceeded("This IP already has a pending or running review", 60)
            count = db.execute(
                "SELECT COUNT(*) FROM reviews WHERE owner=? AND submitted_at>?",
                (owner, now - 86400),
            ).fetchone()[0]
            if count >= max_per_day:
                raise LimitExceeded("Daily review limit reached for this IP", 86400)
            global_count = db.execute(
                "SELECT COUNT(*) FROM reviews WHERE submitted_at>?", (now - 86400,)
            ).fetchone()[0]
            if global_count >= max_global_per_day:
                raise LimitExceeded("Daily review capacity reached; try again tomorrow", 3600)
            waiting = db.execute(
                "SELECT COUNT(*) FROM reviews WHERE status='pending'"
            ).fetchone()[0]
            if waiting >= max_pending:
                raise LimitExceeded("Review queue is full; try again later", 300)
            db.execute(
                """INSERT INTO reviews(id,owner,status,title,abstract,latex_content,
                   submitted_at,updated_at) VALUES(?,?,?,?,?,?,?,?)""",
                (job_id, owner, "pending", title, abstract, latex_content, now, now),
            )
            return {"job_id": job_id, "status": "pending", "queue_position": waiting + 1}

    def next_job(self) -> dict | None:
        with self.connection() as db:
            row = db.execute(
                "SELECT * FROM reviews WHERE status='running' ORDER BY submitted_at LIMIT 1"
            ).fetchone()
            if row:
                return dict(row)
            row = db.execute(
                "SELECT * FROM reviews WHERE status='pending' ORDER BY submitted_at LIMIT 1"
            ).fetchone()
            return dict(row) if row else None

    def set_running(self, job_id: str, backend_id: str) -> None:
        with self.connection() as db:
            db.execute(
                "UPDATE reviews SET status='running', backend_id=?, updated_at=? WHERE id=?",
                (backend_id, int(time.time()), job_id),
            )

    def finish(self, job_id: str, status: str, review_text: str = "", error: str = "") -> None:
        if status not in TERMINAL:
            raise ValueError("invalid terminal status")
        now = int(time.time())
        with self.connection() as db:
            db.execute(
                """UPDATE reviews SET status=?, review_text=?, error=?, latex_content='',
                   updated_at=?, finished_at=? WHERE id=?""",
                (status, review_text, error, now, now, job_id),
            )

    def get(self, job_id: str) -> dict | None:
        with self.connection() as db:
            row = db.execute(
                """SELECT id AS job_id, status, title, review_text, error,
                   submitted_at, updated_at, finished_at, backend_id
                   FROM reviews WHERE id=?""",
                (job_id,),
            ).fetchone()
            if not row:
                return None
            result = dict(row)
            result.pop("backend_id")
            if result["status"] == "pending":
                result["queue_position"] = db.execute(
                    "SELECT COUNT(*) FROM reviews WHERE status='pending' AND submitted_at<=?",
                    (result["submitted_at"],),
                ).fetchone()[0]
            return result

    def cleanup(self, retain_days: int, archive_dir: str | Path | None = None) -> None:
        cutoff = int(time.time()) - retain_days * 86400
        with self.connection() as db:
            old_ids = [row[0] for row in db.execute(
                "SELECT backend_id FROM reviews WHERE finished_at<? AND backend_id IS NOT NULL",
                (cutoff,),
            )]
            db.execute("DELETE FROM reviews WHERE finished_at<?", (cutoff,))
            db.execute("DELETE FROM usage WHERE at<?", (cutoff,))
        if archive_dir:
            import shutil
            archive = Path(archive_dir).resolve()
            for backend_id in old_ids:
                if backend_id.isalnum():
                    target = archive / backend_id
                    if target.is_dir() and target.resolve().parent == archive:
                        shutil.rmtree(target)
