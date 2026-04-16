from __future__ import annotations

import logging
import signal
import time
from collections.abc import Callable

from .models import JobConfig

LOGGER = logging.getLogger(__name__)


class JobScheduler:
    def __init__(self, jobs: list[JobConfig], runner: Callable[[JobConfig], None]):
        self.jobs = jobs
        self.runner = runner
        self._stop_requested = False

    def install_signal_handlers(self) -> None:
        signal.signal(signal.SIGINT, self._request_stop)
        signal.signal(signal.SIGTERM, self._request_stop)

    def run_once(self) -> None:
        for job in self.jobs:
            self.runner(job)

    def run_forever(self) -> None:
        self.install_signal_handlers()
        next_run_at = {job.name: 0.0 for job in self.jobs}

        while not self._stop_requested:
            current_time = time.monotonic()
            due_jobs = [job for job in self.jobs if current_time >= next_run_at[job.name]]

            if not due_jobs:
                next_due = min(next_run_at.values())
                sleep_for = max(0.1, min(1.0, next_due - current_time))
                time.sleep(sleep_for)
                continue

            for job in due_jobs:
                if self._stop_requested:
                    break
                start = time.monotonic()
                self.runner(job)
                elapsed = time.monotonic() - start
                next_run_at[job.name] = time.monotonic() + job.interval_seconds
                LOGGER.info(
                    "job_completed",
                    extra={
                        "job_name": job.name,
                        "interval_seconds": job.interval_seconds,
                        "elapsed_seconds": round(elapsed, 3),
                    },
                )

    def _request_stop(self, signum: int, _frame: object) -> None:
        LOGGER.info("shutdown_requested", extra={"signal": signum})
        self._stop_requested = True
