"""Background job runner for long-running pipeline operations, so the
Flask server never blocks a request on a multi-minute Archidekt pull or
a full Stage 3 re-analysis.

Deliberately NOT a task queue (Celery/Redis/etc): this is a single-user,
local tool per the project's own ground rules ("everything runs locally,
no cloud services, no database server beyond SQLite"), and a thread plus
an in-memory dict is the whole problem statement -- one person, one
browser tab, one job at a time is the actual usage pattern. Job history is
lost on server restart; that's an acceptable trade for the simplicity, not
an oversight.
"""
from __future__ import annotations

import contextlib
import io
import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field
from typing import Callable

_jobs: dict[str, "Job"] = {}
_lock = threading.Lock()


@dataclass
class Job:
    id: str
    name: str
    status: str = "running"  # running | completed | failed
    log: list[str] = field(default_factory=list)
    progress_line: str = ""
    result: object = None
    error: str | None = None
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id, "name": self.name, "status": self.status,
            "log": self.log, "progress_line": self.progress_line,
            "result": self.result, "error": self.error,
            "started_at": self.started_at, "finished_at": self.finished_at,
        }


class _JobStream(io.TextIOBase):
    """A file-like object that mirrors writes to the real stream (so
    server-side console logs are untouched) while also feeding the job's
    own log, so the frontend can poll and show progress. Both '\\n' and
    '\\r' commit the current partial line -- existing scripts print
    progress bars with '\\r...end=""', and treating that the same as a
    newline means those updates show up as log entries instead of vanishing.
    """

    def __init__(self, job: Job, mirror):
        self.job = job
        self.mirror = mirror
        self._partial = ""

    def write(self, s: str) -> int:
        self.mirror.write(s)
        for ch in s:
            if ch in ("\n", "\r"):
                if self._partial:
                    self.job.log.append(self._partial)
                    self._partial = ""
            else:
                self._partial += ch
        self.job.progress_line = self._partial
        return len(s)

    def flush(self) -> None:
        self.mirror.flush()


def start_job(name: str, target: Callable[[], object]) -> str:
    """Run target() in a background thread, capturing everything it
    prints. target takes no arguments -- callers close over whatever
    parameters they need (see webapp/actions.py)."""
    job = Job(id=uuid.uuid4().hex[:12], name=name)
    with _lock:
        _jobs[job.id] = job

    def runner() -> None:
        import sys
        stream = _JobStream(job, sys.stdout)
        try:
            with contextlib.redirect_stdout(stream):
                job.result = target()
            job.status = "completed"
        except Exception as e:
            job.status = "failed"
            job.error = f"{e!r}\n{traceback.format_exc()}"
        finally:
            if stream._partial:
                job.log.append(stream._partial)
                stream._partial = ""
            job.finished_at = time.time()

    threading.Thread(target=runner, daemon=True).start()
    return job.id


def get_job(job_id: str) -> Job | None:
    return _jobs.get(job_id)


def list_jobs() -> list[Job]:
    return sorted(_jobs.values(), key=lambda j: -j.started_at)


def any_job_running() -> bool:
    return any(j.status == "running" for j in _jobs.values())
