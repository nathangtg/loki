from __future__ import annotations

import threading
import time
from typing import Any

from src.server import config
from src.server.config import LOGGER


def append_bounded(items: list[Any], item: Any, max_len: int) -> None:
    """Append item, dropping the oldest entries so len(items) <= max_len."""
    items.append(item)
    overflow = len(items) - max_len
    if overflow > 0:
        del items[:overflow]


def _drop(job_id: str) -> None:
    try:
        from src.graph.checkpoint import delete_state

        delete_state(job_id)
    except Exception:
        LOGGER.debug("Could not delete checkpoint for %s", job_id, exc_info=True)
    config.jobs.pop(job_id, None)
    config.job_events.pop(job_id, None)
    config.job_done_events.pop(job_id, None)


def evict_expired_jobs(now: float | None = None) -> int:
    """Remove finished jobs older than the TTL, then trim to the retention cap.

    Running jobs are never evicted. Returns the number of jobs removed.
    """
    current = time.time() if now is None else now
    removed = 0
    with config.jobs_lock:
        for job_id, job in list(config.jobs.items()):
            finished_at = job.get("finished_at")
            if finished_at is not None and current - finished_at > config.JOB_TTL_SECONDS:
                _drop(job_id)
                removed += 1

        excess = len(config.jobs) - config.MAX_RETAINED_JOBS
        if excess > 0:
            finished = sorted(
                (j.get("finished_at"), job_id) for job_id, j in config.jobs.items() if j.get("finished_at") is not None
            )
            for _, job_id in finished[:excess]:
                _drop(job_id)
                removed += 1
    if removed:
        LOGGER.info("Evicted %d finished job(s)", removed)
    return removed


def start_job_sweeper(interval: float | None = None) -> threading.Thread:
    """Start a daemon thread that periodically evicts expired jobs."""
    period = float(interval if interval is not None else config.JOB_SWEEP_INTERVAL_SECONDS)

    def _run() -> None:
        while True:
            time.sleep(period)
            try:
                evict_expired_jobs()
            except Exception:
                LOGGER.exception("Job sweeper failed")

    thread = threading.Thread(target=_run, daemon=True, name="loki-job-sweeper")
    thread.start()
    return thread
