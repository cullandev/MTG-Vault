"""Job bookkeeping.

Every scheduled job runs inside :func:`job_run`, which records a ``job_runs`` row and
-- critically -- **never lets an exception escape**. A failing price sync must not take
the scheduler down with it, and the next night's run must be unaffected
(ARCHITECTURE.md section 5).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from app.db import session_scope
from app.models import JobRun, utcnow

log = logging.getLogger("mtgvault.jobs")


@dataclass
class JobContext:
    """Handle a job uses to report what it did."""

    run_id: int
    detail: dict[str, Any] = field(default_factory=dict)
    status: str = "ok"

    def report(self, **values: Any) -> None:
        """Merge values into the run's detail payload."""
        self.detail.update(values)

    def mark_partial(self, reason: str) -> None:
        """Record that some sub-tasks failed but the run as a whole is usable."""
        self.status = "partial"
        self.detail.setdefault("partial_reasons", []).append(reason)


@contextmanager
def job_run(name: str, *, sub_source: str | None = None) -> Iterator[JobContext]:
    """Run a job, recording start, finish, status and timing.

    Args:
        name: Job name, matching the schedule in ARCHITECTURE.md section 5.
        sub_source: For fan-out jobs, which source this run covers.

    Yields:
        A :class:`JobContext` the job reports progress into.
    """
    with session_scope() as db:
        run = JobRun(job_name=name, sub_source=sub_source, status="running")
        db.add(run)
        db.flush()
        run_id = run.id

    context = JobContext(run_id=run_id)
    started = time.perf_counter()
    status = context.status
    error: str | None = None

    try:
        yield context
        status = context.status
    except Exception as exc:
        status = "failed"
        error = f"{type(exc).__name__}: {exc}"
        log.exception("job_failed", extra={"job": name, "sub_source": sub_source})
    finally:
        duration_ms = round((time.perf_counter() - started) * 1000, 1)
        detail = dict(context.detail)
        detail["duration_ms"] = duration_ms
        if error:
            detail["error"] = error
        with session_scope() as db:
            row = db.get(JobRun, run_id)
            if row is not None:
                row.status = status
                row.finished_at = utcnow()
                row.detail_json = detail
        log.info(
            "job_finished",
            extra={
                "job": name,
                "sub_source": sub_source,
                "status": status,
                "duration_ms": duration_ms,
            },
        )
