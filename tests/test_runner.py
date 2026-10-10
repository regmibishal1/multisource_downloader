import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from multidownloader import errors, runs
from multidownloader.runner import HostWaiting, Job, JobFinished, JobProgress, JobStarted, RunFinished, Runner
from multidownloader.runs import Run

from gui_gate import HAVE_DISPLAY, REASON as GUI_REASON, hidden_root



class TimedDownloader:
    """Each download takes `seconds`, writes a file, and records how many ran at once."""

    def __init__(self, seconds=0.15, fail=None):
        self.seconds = seconds
        self.fail = fail or {}  # url -> list of exceptions to raise on successive attempts
        self.lock = threading.Lock()
        self.active = {}
        self.max_active = {}
        self.max_total = 0
        self.starts = []

    def download(self, source, url, options, out_dir=None):
        host = url.split('/')[2]
        with self.lock:
            self.active[host] = self.active.get(host, 0) + 1
            self.max_active[host] = max(self.max_active.get(host, 0), self.active[host])
            self.max_total = max(self.max_total, sum(self.active.values()))
            self.starts.append((host, time.monotonic(), url))
        try:
            cancel = options.get('cancel_event')
            end = time.monotonic() + self.seconds
            while time.monotonic() < end:
                if cancel is not None and cancel.is_set():
                    raise RuntimeError('Stopped')  # what yt-dlp's hook does
                if options.get('progress'):
                    options['progress'](1, 10, 100.0)
                time.sleep(0.005)
            queued = self.fail.get(url)
            if queued:
                raise queued.pop(0)
            (Path(out_dir) / 'f.jpg').write_bytes(url.encode())
            return True
        finally:
            with self.lock:
                self.active[host] -= 1


class RunnerTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.run = Run(Path(tmp.name) / 'out')
        self.addCleanup(self.run.finish)  # closes the manifest before the folder is removed (Windows)
        self.events = []

    def runner(self, downloader, **kw):
        kw.setdefault('delays', {})
        kw.setdefault('default_delay', (0, 0))
        return Runner(self.run, downloader, on_event=self.events.append, **kw)

    def jobs(self, urls):
        return [Job(u, 'Twitter' if 'x.com' in u else 'Instagram' if 'instagram' in u else 'YouTube') for u in urls]

    def test_parallel_across_platforms_one_per_platform(self):
        urls = [f'https://x.com/a/status/{i}' for i in range(3)] + \
               [f'https://www.instagram.com/p/C{i}/' for i in range(3)] + \
               [f'https://www.youtube.com/watch?v=v{i:010d}' for i in range(3)]
        fake = TimedDownloader(0.3)  # long enough to overlap on a slow CI machine
        results = self.runner(fake, max_parallel=3).run(self.jobs(urls))
        self.assertEqual(9, len(results))
        self.assertTrue(all(r.status == 'saved' for r in results))
        # parallelism is checked by what ran at once, not by the clock: wall-clock comparisons were flaky on
        # shared CI machines whose disks make every custody-log flush slow
        self.assertEqual(3, fake.max_total)
        self.assertEqual({1}, set(fake.max_active.values()))  # never two at once on one site
        self.assertEqual(9, len(runs.read_manifest(self.run.dir / 'manifest.jsonl')))

    def test_instagram_is_always_one_at_a_time(self):
        fake = TimedDownloader(0.4)  # long enough for the x.com links to overlap on a slow CI machine
        self.runner(fake, max_parallel=4, per_host=3).run(
            self.jobs([f'https://www.instagram.com/p/C{i}/' for i in range(3)] +
                      [f'https://x.com/a/status/{i}' for i in range(3)]))
        self.assertEqual(1, fake.max_active['www.instagram.com'])
        self.assertEqual(3, fake.max_active['x.com'])

    def test_polite_pause_between_links_from_one_platform(self):
        fake = TimedDownloader(0.01)
        self.runner(fake, delays={'x': (0.25, 0.25)}).run(self.jobs([f'https://x.com/a/status/{i}' for i in range(3)]))
        times = [t for host, t, _u in fake.starts]
        gaps = [b - a for a, b in zip(times, times[1:])]
        self.assertTrue(all(gap >= 0.25 for gap in gaps), gaps)

    def test_default_pauses_are_polite(self):
        runner = Runner(self.run, TimedDownloader())
        self.assertEqual((8.0, 15.0), runner.delays['instagram'])
        self.assertEqual((1.0, 3.0), runner.default_delay)
        self.assertEqual(600, runner.rate_limit_pause)
        self.assertEqual(3, runner.max_parallel)
        self.assertEqual(6, Runner(self.run, TimedDownloader(), max_parallel=50).max_parallel)

    def test_rate_limit_pauses_that_platform_and_retries_once(self):
        x1 = 'https://x.com/a/status/1'
        fake = TimedDownloader(0.02, fail={x1: [RuntimeError('ERROR: [twitter] 1: HTTP Error 429: Too Many Requests')]})
        urls = [x1, 'https://www.youtube.com/watch?v=aaaaaaaaaaa', 'https://www.youtube.com/watch?v=bbbbbbbbbbb']
        results = self.runner(fake, rate_limit_pause=0.4).run(self.jobs(urls))
        by_url = {r.input_url: r for r in results}
        self.assertEqual('saved', by_url[x1].status)  # the retry worked
        self.assertEqual(1, sum(1 for r in results if r.input_url == x1))  # recorded once
        x_starts = [t for host, t, _u in fake.starts if host == 'x.com']
        self.assertGreaterEqual(x_starts[1] - x_starts[0], 0.4)
        yt_done = [t for host, t, _u in fake.starts if host == 'www.youtube.com']
        self.assertLess(max(yt_done), x_starts[1])  # YouTube went on during the pause
        waiting = [e for e in self.events if isinstance(e, HostWaiting)]
        self.assertEqual(1, len(waiting))
        self.assertEqual('X (Twitter) is limiting requests.', waiting[0].message.split(' Waiting')[0])

    def test_rate_limited_twice_fails_once(self):
        x1 = 'https://x.com/a/status/1'
        limit = 'HTTP Error 429: Too Many Requests'
        fake = TimedDownloader(0.01, fail={x1: [RuntimeError(limit), RuntimeError(limit)]})
        results = self.runner(fake, rate_limit_pause=0.05).run(self.jobs([x1]))
        self.assertEqual(['failed'], [r.status for r in results])
        self.assertEqual(errors.RATE_LIMITED, results[0].error_kind)
        self.assertEqual(1, len(runs.read_manifest(self.run.dir / 'manifest.jsonl')))

    def test_bot_check_is_not_retried(self):
        y1 = 'https://www.youtube.com/watch?v=aaaaaaaaaaa'
        fake = TimedDownloader(0.01, fail={y1: [RuntimeError("Sign in to confirm you're not a bot")]})
        results = self.runner(fake, rate_limit_pause=5).run(self.jobs([y1]))
        self.assertEqual((errors.BOT_CHECK, 1), (results[0].error_kind, len(fake.starts)))

    def test_stop_within_five_seconds(self):
        urls = [f'https://x.com/a/status/{i}' for i in range(2)] + \
               [f'https://www.youtube.com/watch?v=v{i:010d}' for i in range(2)]
        fake = TimedDownloader(30)  # would take a minute without Stop
        runner = self.runner(fake, max_parallel=2)
        threading.Timer(0.3, runner.stop).start()
        started = time.monotonic()
        results = runner.run(self.jobs(urls))
        self.assertLess(time.monotonic() - started, 5)
        self.assertEqual(['stopped'] * 4, [r.status for r in results])
        messages = sorted({r.error_message for r in results})
        self.assertEqual(['Stopped before it finished.', 'Stopped before it started.'], messages)
        self.assertFalse(any((self.run.root / '_staging').iterdir()))  # nothing half-saved
        self.assertTrue(isinstance(self.events[-1], RunFinished) and self.events[-1].stopped)

    def test_events_and_progress_throttling(self):
        fake = TimedDownloader(0.5)
        self.runner(fake).run(self.jobs(['https://x.com/a/status/1']))
        kinds = [type(e) for e in self.events]
        self.assertEqual(JobStarted, kinds[0])
        self.assertEqual([JobFinished, RunFinished], kinds[-2:])
        progress = [e for e in self.events if isinstance(e, JobProgress)]
        self.assertGreater(len(progress), 0)
        self.assertLessEqual(len(progress), 5)  # ~100 updates in 0.5 s, at most 5 per second
        self.assertAlmostEqual(0.1, progress[0].fraction)


@unittest.skipUnless(__import__('importlib').util.find_spec('yt_dlp'), 'yt-dlp not installed')
class ProgressHookTests(unittest.TestCase):
    def test_hook_reports_and_stops(self):
        from multidownloader.sources.yt_dlp_base import progress_hook
        self.assertIsNone(progress_hook())
        calls = []
        cancel = threading.Event()
        hook = progress_hook(cancel, lambda *a: calls.append(a))
        hook({'status': 'downloading', 'downloaded_bytes': 5, 'total_bytes_estimate': 10, 'speed': 2.0})
        self.assertEqual([(5, 10, 2.0)], calls)
        cancel.set()
        from yt_dlp.utils import DownloadCancelled
        with self.assertRaises(DownloadCancelled):
            hook({'status': 'downloading'})

    def test_hook_is_installed(self):
        from multidownloader import session_store
        from multidownloader.sources.yt_dlp_base import YtDlpHandler
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(session_store, 'SESSION_ROOT', Path(tmp) / '.sessions'):
            opts = YtDlpHandler('YouTube')._build_opts(tmp, {'cancel_event': threading.Event()}, None)
            self.assertEqual(1, len(opts['progress_hooks']))
            self.assertNotIn('progress_hooks', YtDlpHandler('YouTube')._build_opts(tmp, {}, None))


@unittest.skipUnless(HAVE_DISPLAY, GUI_REASON)
class WindowProgressTests(unittest.TestCase):
    def setUp(self):
        self.root = hidden_root(self)
        import multisource_downloader_ui as ui
        with mock.patch.object(ui.signins, 'summary', return_value=''):
            self.app = ui.DownloaderUI(self.root, offer_resume=False)

    def test_status_progress_and_waiting(self):
        from multidownloader.runs import JobResult
        app = self.app
        app._counts = {'total': 2}
        app.progress.configure(maximum=2)
        app._handle_event(JobStarted(1, 'https://x.com/a/status/1'))
        self.assertEqual('Saving 1 of 2. 0 saved, 0 failed.', app.status_var.get())
        done = JobResult(run_id='r', input_url='u', normalized_url='u', platform='x', backend='yt-dlp',
                         status='saved')
        app._handle_event(JobFinished(1, 'https://x.com/a/status/1', done))
        self.assertEqual(1, app.progress['value'])
        self.assertIn('1 saved', app.status_var.get())
        app._handle_event(HostWaiting('x', time.monotonic() + 65,
                                      'X (Twitter) is limiting requests. Waiting 10:00 before trying again.'))
        app._show_waiting()
        self.assertRegex(app.notice_var.get(),
                         r'^! X \(Twitter\) is limiting requests\. Waiting 01:0[45] before trying again\.$')

    def test_stop_asks_first_and_keep_is_the_default(self):
        import multisource_downloader_ui as ui
        runner = mock.Mock(stopped=False)
        self.app._runner = runner
        self.app._counts = {'total': 5, 'done': 2}
        with mock.patch.object(ui, 'confirm_stop', return_value=False) as ask:
            self.app.ask_stop()
        ask.assert_called_once_with(self.root, 3)
        runner.stop.assert_not_called()
        with mock.patch.object(ui, 'confirm_stop', return_value=True):
            self.app.ask_stop()
        runner.stop.assert_called_once()


if __name__ == '__main__':
    unittest.main()
