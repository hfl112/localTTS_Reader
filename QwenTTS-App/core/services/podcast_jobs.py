from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from typing import Any, Iterator


@contextmanager
def _file_lock(path: str) -> Iterator[None]:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    lock_path = path + ".lock"
    with open(lock_path, "a", encoding="utf-8") as lock_file:
        try:
            import fcntl

            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            yield
        finally:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass


class PodcastJobStore:
    def __init__(self, path: str, max_jobs: int = 100) -> None:
        self.path = path
        self.max_jobs = max_jobs

    def list(self) -> list[dict[str, Any]]:
        with _file_lock(self.path):
            return self._load_unlocked()

    def create(
        self,
        *,
        job_id: str,
        kind: str,
        md5: str,
        title: str,
        source: str,
        output_path: str | None = None,
    ) -> dict[str, Any]:
        now = time.time()
        job = {
            "job_id": job_id,
            "kind": kind,
            "md5": md5,
            "title": title,
            "source": source,
            "status": "queued",
            "created_at": now,
            "updated_at": now,
            "pid": None,
            "output_path": output_path,
            "error": None,
        }
        with _file_lock(self.path):
            jobs = [item for item in self._load_unlocked() if item.get("job_id") != job_id]
            jobs.insert(0, job)
            self._write_unlocked(jobs[: self.max_jobs])
        return job

    def update(self, job_id: str | None, **fields: Any) -> None:
        if not job_id:
            return
        with _file_lock(self.path):
            jobs = self._load_unlocked()
            for job in jobs:
                if job.get("job_id") == job_id:
                    job.update(fields)
                    job["updated_at"] = time.time()
                    break
            self._write_unlocked(jobs[: self.max_jobs])

    def active_for_md5(self, md5: str) -> bool:
        active_statuses = {"queued", "running", "paused"}
        return any(
            job.get("md5") == md5 and job.get("status") in active_statuses
            for job in self.list()
        )

    def cancel_active(self) -> None:
        with _file_lock(self.path):
            jobs = self._load_unlocked()
            now = time.time()
            for job in jobs:
                if job.get("status") in {"queued", "running", "paused"}:
                    job["status"] = "canceled"
                    job["updated_at"] = now
            self._write_unlocked(jobs[: self.max_jobs])

    def mark_unfinished_failed(self, reason: str) -> None:
        with _file_lock(self.path):
            jobs = self._load_unlocked()
            now = time.time()
            for job in jobs:
                if job.get("status") in {"queued", "running", "paused"}:
                    job["status"] = "failed"
                    job["error"] = reason
                    job["updated_at"] = now
            self._write_unlocked(jobs[: self.max_jobs])

    def _load_unlocked(self) -> list[dict[str, Any]]:
        if not os.path.exists(self.path):
            return []
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, list) else []
        except Exception:
            return []

    def _write_unlocked(self, jobs: list[dict[str, Any]]) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp_path = self.path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(jobs, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, self.path)
