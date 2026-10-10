import logging
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from multidownloader import errors, session_store
from multidownloader.sources import instagram

needs_instaloader = unittest.skipUnless(instagram.INSTALOADER_AVAILABLE, 'Instaloader is not installed')


@needs_instaloader
class RateControllerTests(unittest.TestCase):
    def controller(self, cancel=None):
        loader = instagram.new_loader()
        self.addCleanup(loader.close)
        return instagram.AppRateController(loader.context, cancel, logging.getLogger('test'))

    def test_short_waits_are_logged_and_kept(self):
        with self.assertLogs('test', level='INFO') as logs:
            started = time.monotonic()
            self.controller().sleep(1.2)
        self.assertGreaterEqual(time.monotonic() - started, 1.1)
        self.assertIn('slow down: waiting 1 s', logs.output[0])

    def test_stop_ends_a_wait_within_a_second(self):
        cancel = threading.Event()
        threading.Timer(0.3, cancel.set).start()
        started = time.monotonic()
        with self.assertRaisesRegex(RuntimeError, 'Stopped'):
            self.controller(cancel).sleep(30)
        self.assertLess(time.monotonic() - started, 1.5)

    def test_long_waits_become_a_rate_limit(self):
        with self.assertRaises(instagram.TooManyRequestsException) as ctx:
            self.controller().sleep(1800)
        info = errors.classify(RuntimeError(str(ctx.exception)), platform='Instagram')
        self.assertEqual(errors.RATE_LIMITED, info.kind)

    def test_loader_settings(self):
        loader = instagram.new_loader()
        self.addCleanup(loader.close)
        self.assertEqual(instagram.REQUEST_TIMEOUT_S, loader.context.request_timeout)
        log = logging.getLogger('test.tune')
        instagram.tune(loader, None, log)
        self.assertIsInstance(loader.context._rate_controller, instagram.AppRateController)
        self.assertTrue(loader.context.quiet)
        with self.assertLogs('test.tune', level='WARNING') as logs:
            loader.context.error('Too many queries in the last time.')
        self.assertIn('Instaloader: Too many queries', logs.output[0])


@needs_instaloader
class DownloadRateLimitTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        patcher = mock.patch.object(session_store, 'SESSION_ROOT', self.tmp / 'sessions')
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_rate_limit_is_not_retried_and_reads_as_one(self):
        handler = instagram.InstagramHandler()
        loader = instagram.new_loader()
        self.addCleanup(loader.close)
        calls = []

        def limited(context, shortcode):
            calls.append(shortcode)
            raise instagram.TooManyRequestsException(instagram.RATE_LIMITED_MESSAGE.format(seconds=1800))

        with mock.patch.object(instagram.Post, 'from_shortcode', side_effect=limited):
            with self.assertRaises(RuntimeError) as ctx:
                handler.download('https://www.instagram.com/p/AbC123/', str(self.tmp / 'out'),
                                 {'instaloader': loader})
        self.assertEqual(['AbC123'], calls)  # no second attempt
        self.assertEqual(errors.RATE_LIMITED, errors.classify(ctx.exception, platform='Instagram').kind)

    def test_stop_during_a_failure_is_not_retried(self):
        handler = instagram.InstagramHandler()
        loader = instagram.new_loader()
        self.addCleanup(loader.close)
        cancel = threading.Event()

        def stopped(context, shortcode):
            cancel.set()
            raise ConnectionError('connection closed')

        with mock.patch.object(instagram.Post, 'from_shortcode', side_effect=stopped) as fetch:
            with self.assertRaisesRegex(RuntimeError, 'Stopped'):
                handler.download('https://www.instagram.com/p/AbC123/', str(self.tmp / 'out'),
                                 {'instaloader': loader, 'cancel_event': cancel})
        self.assertEqual(1, fetch.call_count)


if __name__ == '__main__':
    unittest.main()
