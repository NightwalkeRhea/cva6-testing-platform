"""One queue, a fixed number of slots, and nothing to arbitrate.

A run is one DUT in one process, so the whole core budget belongs to it and the
only question left is how finely to cut it. The measurements answer that: ten
unequal fault-simulation tasks cannot be balanced across sixteen workers, but
sixteen individuals average out. One individual on sixteen cores keeps 41% of
the machine busy; sixteen individuals on one core each keep 94%, for the same
total work. So the slot is small and the queue is deep.

A slot is held while waiting out an infrastructure failure. That is deliberate:
whatever broke is usually shared -- a licence, a disk, a server -- and trying
harder with more workers makes it worse.

The number of slots can change while the queue is being worked through: a
larger one starts workers at once, a smaller one lets the extra workers finish
the job they hold and take no other. Nothing running is ever killed for it.
"""
from __future__ import annotations

import queue
import threading
import time
from typing import Callable

from .model import Job, Status

Work = Callable[[Job], Status]
Done = Callable[[Job], None]
Event = Callable[[str, dict], None]


class Pool:
    def __init__(
        self,
        slots: int,
        cores_each: int,
        work: Work,
        on_done: Done | None = None,
        on_event: Event | None = None,
        retries: int = 2,
        backoff: float = 20.0,
        stall_seconds: float = 1800.0,
    ):
        self.slots = max(1, slots)
        self.cores_each = max(1, cores_each)
        self.work = work
        self.on_done = on_done or (lambda _job: None)
        self.on_event = on_event or (lambda *_: None)
        self.retries = retries
        self.backoff = backoff
        self.stall_seconds = stall_seconds

        self.stopping = threading.Event()
        self._lock = threading.Lock()
        self.running: set[str] = set()
        self.completed = 0
        self.degraded = ""
        self._queue: queue.Queue | None = None
        self._threads: list[threading.Thread] = []
        self._workers = 0

    # ------------------------------------------------------------- lifecycle

    def stop(self) -> None:
        self.stopping.set()

    def resize(self, slots: int) -> None:
        """A new number of slots, for the jobs not started yet."""
        with self._lock:
            self.slots = max(1, slots)
        self._grow()

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "slots": self.slots,
                "cores_each": self.cores_each,
                "running": sorted(self.running),
                "completed": self.completed,
                "degraded": self.degraded,
            }

    def run(self, jobs: list[Job]) -> None:
        """Work through the jobs in the order given and return when all are done."""
        if not jobs:
            return
        q: queue.Queue = queue.Queue()
        for job in jobs:
            job.cores = self.cores_each
            q.put(job)
        with self._lock:
            self._queue, self._threads = q, []
        self._grow()
        # Done when no worker is left: resize() may start one while this waits,
        # and a worker is counted before its thread is running.
        while True:
            with self._lock:
                if self._workers == 0:
                    return
                alive = [t for t in self._threads if t.is_alive()]
            if alive:
                alive[0].join()
            else:
                time.sleep(0.01)

    def _grow(self) -> None:
        # A new worker is handed its first job here, so the workers start on
        # the jobs in queue order.
        while True:
            with self._lock:
                if self._queue is None or self._workers >= self.slots:
                    return
                try:
                    job = self._queue.get_nowait()
                except queue.Empty:
                    return
                t = threading.Thread(target=self._worker, args=(job,),
                                     name=f"slot{len(self._threads)}", daemon=True)
                self._threads.append(t)
                self._workers += 1
            t.start()               # outside the lock, which the job needs at once

    # ---------------------------------------------------------------- worker

    def _take(self) -> Job | None:
        """The next job, or None when this worker should end: nothing is left,
        or there are more workers than slots. Decided and counted under one
        lock, so a shrink never retires more workers than it asked for."""
        with self._lock:
            job = None
            if self._workers <= self.slots:
                try:
                    job = self._queue.get_nowait()
                except queue.Empty:
                    pass
            if job is None:
                self._workers -= 1
            return job

    def _worker(self, job: Job | None) -> None:
        try:
            while job is not None:
                if self.stopping.is_set():
                    job.outcome.status = Status.INFRA
                    job.outcome.detail = "the run was stopped before this one started"
                    self._finish(job)
                else:
                    self._attempt(job)
                job = self._take()
        except BaseException:
            with self._lock:        # _take did not count this one out
                self._workers -= 1
            raise

    def _attempt(self, job: Job) -> None:
        with self._lock:
            self.running.add(job.key)
        failing_since = 0.0
        try:
            while True:
                status = self._once(job)
                if status is not Status.INFRA:
                    with self._lock:
                        self.degraded = ""
                    self._finish(job)
                    return

                job.attempts += 1
                # Patience counts from the first failure, not from submission:
                # a job of the last wave waits as long as one of the first.
                failing_since = failing_since or time.monotonic()
                waited = time.monotonic() - failing_since
                spent = job.attempts > self.retries and waited >= self.stall_seconds
                if spent or self.stopping.is_set():
                    self.on_event("give-up", {"job": job.key, "detail": job.outcome.detail})
                    self._finish(job)
                    return

                with self._lock:
                    self.degraded = f"{job.key}: {job.outcome.detail}"
                self.on_event(
                    "retry",
                    {"job": job.key, "attempt": job.attempts, "detail": job.outcome.detail},
                )
                self.stopping.wait(self.backoff)
        finally:
            with self._lock:
                self.running.discard(job.key)

    def _once(self, job: Job) -> Status:
        t0 = time.monotonic()
        try:
            status = self.work(job)
        except Exception as exc:  # a worker must never take the run down
            status = Status.INFRA
            job.outcome.status = status
            job.outcome.detail = f"{type(exc).__name__}: {exc}"
        job.outcome.seconds += time.monotonic() - t0
        return status

    def _finish(self, job: Job) -> None:
        job.done = True
        with self._lock:
            self.completed += 1
        try:
            self.on_done(job)
        except Exception as exc:  # bookkeeping must not strand the generation
            self.on_event("bookkeeping-failed", {"job": job.key, "error": str(exc)})
        self.on_event(
            "done",
            {
                "job": job.key,
                "status": job.outcome.status.value,
                "coverage": job.outcome.coverage,
                "seconds": round(job.outcome.seconds, 1),
            },
        )
