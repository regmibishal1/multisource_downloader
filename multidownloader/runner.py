"""Run many links at once, politely, with Stop and progress (brief T8, F15, F18).

Runner.run(jobs) downloads through a Run on a small thread pool. A scheduler
hands out only jobs whose site is free right now, so links for one busy site
never hold up the others:

- at most ``max_parallel`` downloads at once (default 3, 1 to 6);
- at most ``per_host`` at once per platform (default 1; Instagram always 1);
- a random pause between two downloads from the same platform (Instagram
  8 to 15 s, others 1 to 3 s);
- when a platform limits requests, it is paused for ``rate_limit_pause``
  seconds (default 10 minutes) and that link is tried once more; other
  platforms keep going. YouTube's bot check pauses YouTube but is not
  retried in the same run.

stop() sets a cancel event: links not started are recorded as stopped, and
yt-dlp downloads in progress stop at their next progress update (their
staging folder is removed). Progress and state changes go to ``on_event``
as small dataclasses, at most 5 progress events per second per link.
"""
from __future__ import annotations

import logging
import random
import threading
import time
from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable, Deque, Dict, List, Optional, Tuple

from . import detect, errors

_log = logging.getLogger(__name__)

DEFAULT_PARALLEL = 3
MAX_PARALLEL = 6
RATE_LIMIT_PAUSE_S = 600
PROGRESS_INTERVAL_S = 0.2
DELAYS: Dict[str, Tuple[float, float]] = {'instagram': (8.0, 15.0)}
DEFAULT_DELAY: Tuple[float, float] = (1.0, 3.0)
SINGLE_LANE = {'instagram'}


@dataclass
class Job:
    url: str
    handler: str
    options: Dict = field(default_factory=dict)
    hint: str = ''
    index: int = 0
    attempts: int = 0

    @property
    def host(self) -> str:
        platform = detect.platform_for_url(self.url)
        return platform.key if platform else self.handler.lower()


# --- events (sent to on_event from worker threads) ---------------------------------

@dataclass
class JobStarted:
    index: int
    url: str


@dataclass
class JobProgress:
    index: int
    url: str
    fraction: Optional[float]   # 0..1, None when the size is unknown
    downloaded: int
    total: Optional[int]
    speed: Optional[float]      # bytes per second


@dataclass
class HostWaiting:
    host: str
    until: float                # time.monotonic() when it resumes
    message: str


@dataclass
class JobFinished:
    index: int
    url: str
    result: object              # runs.JobResult


@dataclass
class RunFinished:
    counts: Dict[str, int]
    stopped: bool


def mmss(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    return f'{seconds // 60:02d}:{seconds % 60:02d}'


class Runner:
    def __init__(self, run, downloader, *, max_parallel: int = DEFAULT_PARALLEL, per_host: int = 1,
                 delays: Optional[Dict[str, Tuple[float, float]]] = None,
                 default_delay: Optional[Tuple[float, float]] = None,
                 rate_limit_pause: Optional[float] = None,
                 on_event: Optional[Callable[[object], None]] = None,
                 clock: Callable[[], float] = time.monotonic, rng: Optional[random.Random] = None):
        self.run_ = run
        self.downloader = downloader
        self.max_parallel = max(1, min(MAX_PARALLEL, int(max_parallel)))
        self.per_host = max(1, int(per_host))
        # module settings are read here, not at import, so callers and tests can change them
        self.delays = dict(DELAYS if delays is None else delays)
        self.default_delay = DEFAULT_DELAY if default_delay is None else default_delay
        self.rate_limit_pause = RATE_LIMIT_PAUSE_S if rate_limit_pause is None else rate_limit_pause
        self.on_event = on_event or (lambda event: None)
        self.clock = clock
        self.rng = rng or random.Random()
        self.cancel = threading.Event()
        self._wake = threading.Event()
        self._last_progress: Dict[int, float] = {}

    # --- control -----------------------------------------------------------------
    def stop(self) -> None:
        """Stop: no new links start, running yt-dlp downloads stop at their next update."""
        self.cancel.set()
        self._wake.set()

    @property
    def stopped(self) -> bool:
        return self.cancel.is_set()

    def _emit(self, event) -> None:
        try:
            self.on_event(event)
        except Exception:  # a broken listener must not break the run
            _log.exception('Event listener failed')

    # --- scheduling --------------------------------------------------------------
    def _limit(self, host: str) -> int:
        return 1 if host in SINGLE_LANE else self.per_host

    def _delay(self, host: str) -> float:
        low, high = self.delays.get(host, self.default_delay)
        return self.rng.uniform(low, high) if high > 0 else 0.0

    def run(self, jobs: List[Job]) -> List[object]:
        """Download every job; returns the recorded results in input order of completion.

        A first Ctrl+C (KeyboardInterrupt in this thread) stops the run after the
        current files; a second one aborts immediately.
        """
        for i, job in enumerate(jobs, 1):
            job.index = job.index or i
        pending: Deque[Job] = deque(jobs)
        running: Dict[Future, Job] = {}
        busy: Dict[str, int] = {}
        ready_at: Dict[str, float] = {}     # earliest next start per host (polite delay or pause)
        no_retry: set = set()                # hosts whose block is not retried this run (bot check)
        results: List[object] = []
        interrupts = 0

        with ThreadPoolExecutor(max_workers=self.max_parallel, thread_name_prefix='download') as pool:
            while pending or running:
                try:
                    # collect finished jobs
                    for future in [f for f in running if f.done()]:
                        job = running.pop(future)
                        busy[job.host] -= 1
                        result = future.result()
                        now = self.clock()
                        ready_at[job.host] = max(ready_at.get(job.host, 0), now + self._delay(job.host))
                        if self._should_retry(job, result, no_retry):
                            pause_until = now + self.rate_limit_pause
                            ready_at[job.host] = max(ready_at[job.host], pause_until)
                            job.attempts += 1
                            pending.appendleft(job)
                            platform = detect.platform_for_url(job.url)
                            message = errors.classify(result.error_detail or '', platform=platform.name if platform
                                                      else '', wait=mmss(self.rate_limit_pause)).message
                            _log.warning('%s', message)
                            self._emit(HostWaiting(job.host, pause_until, message))
                            continue
                        if result.error_kind == errors.BOT_CHECK:
                            no_retry.add(job.host)
                            ready_at[job.host] = max(ready_at[job.host], now + self.rate_limit_pause)
                        self.run_.record(result)
                        results.append(result)
                        self._emit(JobFinished(job.index, job.url, result))

                    if self.cancel.is_set():
                        while pending:
                            job = pending.popleft()
                            result = self.run_.stopped(job.url, handler=job.handler, hint=job.hint)
                            results.append(result)
                            self._emit(JobFinished(job.index, job.url, result))
                        if not running:
                            break

                    # start what can start
                    now = self.clock()
                    for job in list(pending):
                        if len(running) >= self.max_parallel:
                            break
                        host = job.host
                        if busy.get(host, 0) >= self._limit(host) or ready_at.get(host, 0) > now:
                            continue
                        pending.remove(job)
                        busy[host] = busy.get(host, 0) + 1
                        self._emit(JobStarted(job.index, job.url))
                        future = pool.submit(self._download, job)
                        # wake the loop once the result is ready: a wake-up sent from inside the job, just
                        # before it returns, could find the future not done yet and then sleep the full timeout
                        future.add_done_callback(lambda _f: self._wake.set())
                        running[future] = job

                    # wait for a job to finish, a host to become free, or Stop
                    waits = [ready_at[j.host] - now for j in pending if ready_at.get(j.host, 0) > now]
                    timeout = min([0.2] + waits) if (pending or running) else 0
                    self._wake.wait(max(0.01, timeout))
                    self._wake.clear()
                except KeyboardInterrupt:
                    interrupts += 1
                    if interrupts > 1:
                        raise
                    _log.warning('Stopping after the current files. Press Ctrl+C again to quit now.')
                    self.stop()

        self._emit(RunFinished(self.run_.counts(), self.cancel.is_set()))
        return results

    def _should_retry(self, job: Job, result, no_retry: set) -> bool:
        return (result.status == 'failed' and result.error_kind == errors.RATE_LIMITED and job.attempts == 0
                and job.host not in no_retry and not self.cancel.is_set())

    # --- one download (worker thread) --------------------------------------------
    def _download(self, job: Job):
        options = dict(job.options)
        options['cancel_event'] = self.cancel
        options['progress'] = lambda downloaded, total, speed: self._progress(job, downloaded, total, speed)
        result = self.run_.download(self.downloader, job.handler, job.url, options, hint=job.hint, record=False)
        if self.cancel.is_set() and result.status == 'failed':
            # stopped mid-download: staging was removed, nothing half-saved remains
            result.status = 'stopped'
            result.error_kind = None
            result.error_action = None
            result.error_message = 'Stopped before it finished.'
        return result

    def _progress(self, job: Job, downloaded: int, total: Optional[int], speed: Optional[float]) -> None:
        now = self.clock()
        if now - self._last_progress.get(job.index, 0) < PROGRESS_INTERVAL_S and not (total and downloaded >= total):
            return
        self._last_progress[job.index] = now
        fraction = (downloaded / total) if total else None
        self._emit(JobProgress(job.index, job.url, fraction, downloaded, total, speed))


__all__ = ['DEFAULT_PARALLEL', 'HostWaiting', 'Job', 'JobFinished', 'JobProgress', 'JobStarted', 'MAX_PARALLEL',
           'RunFinished', 'Runner', 'mmss']
