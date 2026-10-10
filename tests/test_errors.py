import errno
import logging
import unittest

from multidownloader import errors
from multidownloader.batch import next_step, report_failures
from multidownloader.errors import classify

# Real error texts from yt-dlp, Instaloader and gdown, with the platform the
# link was for, whether a sign-in was used, and the kind expected.
REAL_ERRORS = [
    ("ERROR: [Instagram] C1abc: Requested content is not available, rate-limit reached or login required. Use "
     "--cookies, --cookies-from-browser, --username and --password, --netrc-cmd, or --netrc (instagram) to provide "
     "account credentials", 'Instagram', False, 'auth_required'),
    ("ERROR: [Instagram] C1abc: Requested content is not available, rate-limit reached or login required.",
     'Instagram', True, 'rate_limited'),
    ("ERROR: [youtube] dQw4w9WgXcQ: Sign in to confirm you're not a bot. Use --cookies-from-browser or --cookies "
     "for the authentication.", 'YouTube', True, 'bot_check'),
    ("ERROR: [youtube] dQw4w9WgXcQ: Sign in to confirm your age. This video may be inappropriate for some users.",
     'YouTube', False, 'auth_required'),
    ("ERROR: [youtube] dQw4w9WgXcQ: Private video. Sign in if you've been granted access to this video",
     'YouTube', False, 'private'),
    ('ERROR: [youtube] dQw4w9WgXcQ: Video unavailable', 'YouTube', False, 'not_found'),
    ('ERROR: [youtube] dQw4w9WgXcQ: Video unavailable. This video has been removed by the uploader',
     'YouTube', False, 'not_found'),
    ('ERROR: [youtube] dQw4w9WgXcQ: The uploader has not made this video available in your country',
     'YouTube', False, 'geo_blocked'),
    ('ERROR: [twitter] 1890: No video could be found in this tweet', 'X (Twitter)', False, 'no_media'),
    ('ERROR: [twitter] 1890: NSFW tweet requires authentication. Use --cookies', 'X (Twitter)', False,
     'auth_required'),
    ('ERROR: [twitter] 1890: NSFW tweet requires authentication.', 'X (Twitter)', True, 'signin_rejected'),
    ('ERROR: [TikTok] 7300: Unable to extract universal data for rehydration', 'TikTok', False, 'outdated'),
    ('ERROR: Unsupported URL: https://www.facebook.com/some/new/path', 'Facebook', False, 'outdated'),
    ('ERROR: Unsupported URL: https://example.org/video', '', False, 'unsupported'),
    ('ERROR: [generic] Unable to download webpage: <urlopen error [Errno 11001] getaddrinfo failed>',
     'Reddit', False, 'network'),
    ('ERROR: [Reddit] abc12: HTTP Error 429: Too Many Requests', 'Reddit', False, 'rate_limited'),
    ('ERROR: [facebook] 123: HTTP Error 404: Not Found', 'Facebook', False, 'not_found'),
    ('ERROR: [reddit] abc12: HTTP Error 403: Blocked', 'Reddit', False, 'rate_limited'),
    ('Login required.', 'Instagram', False, 'auth_required'),
    ('Login: Checkpoint required. Point your browser to https://www.instagram.com/challenge/', 'Instagram', True,
     'checkpoint'),
    ('Your account has been locked', 'Instagram', True, 'account_locked'),
    ('Too many users have viewed or downloaded this file recently.', 'Google Drive', False, 'rate_limited'),
    ("invalid_grant: Token has been expired or revoked.", 'Google Drive', True, 'oauth_expired'),
    ('ERROR: You have requested merging of multiple formats but ffmpeg is not installed.', 'YouTube', False,
     'missing_component'),
    ('WARNING: No supported JavaScript runtime could be found', 'YouTube', False, 'missing_component'),
    ('ERROR: something nobody expected', 'YouTube', False, 'unknown'),
]


class ClassifyTests(unittest.TestCase):
    def test_real_errors(self):
        for text, platform, signed_in, kind in REAL_ERRORS:
            with self.subTest(text=text[:60]):
                info = classify(RuntimeError(text), platform=platform, signed_in=signed_in)
                self.assertEqual(kind, info.kind)
                self.assertEqual(text, info.detail)
                self.assertTrue(info.message)
                self.assertNotIn('ERROR:', info.message)  # never the raw library text

    def test_exact_messages(self):
        cases = {
            ('Login required.', 'Instagram', False): 'This post needs you to be signed in to Instagram.',
            ('Login required.', 'Instagram', True): 'Instagram did not accept your sign-in. Sign in again.',
            ('HTTP Error 404: Not Found', 'X (Twitter)', False): 'This post was deleted or the link is wrong.',
            ('Private video', 'YouTube', False): 'This post is private. Sign in with an account that can see it.',
            ('not available in your country', 'YouTube', False): 'This post is blocked in your region.',
            ('getaddrinfo failed', 'YouTube', False): 'Could not connect. Check your internet connection.',
            ('Unable to extract data', 'TikTok', False): 'TikTok changed something and the downloader needs an update.',
            ('Unsupported URL: https://example.org', '', False): 'This site is not supported yet.',
            ('ffmpeg is not installed', 'YouTube', False): 'ffmpeg is not installed. Some downloads need it.',
            ("Sign in to confirm you're not a bot", 'YouTube', True):
                'YouTube is limiting this computer. Wait and try later with slower settings.',
            ('HTTP Error 429', 'Reddit', False): 'Reddit is limiting requests. Try again later.',
            ('weird', '', False): 'Something went wrong. Details are in the log.',
        }
        for (text, platform, signed_in), message in cases.items():
            with self.subTest(text=text):
                self.assertEqual(message, classify(text, platform=platform, signed_in=signed_in).message)
        self.assertEqual('Instagram is limiting requests. Waiting 09:30 before trying again.',
                         classify('HTTP Error 429', platform='Instagram', wait='09:30').message)

    def test_blocks_never_suggest_signing_in(self):
        for text in ("Sign in to confirm you're not a bot. Use --cookies", 'HTTP Error 429: Too Many Requests',
                     'rate-limit reached'):
            with self.subTest(text=text):
                info = classify(text, platform='YouTube', signed_in=True)
                self.assertFalse(info.needs_signin)
                self.assertNotIn('sign in', info.message.lower())

    def test_wrapped_library_errors(self):
        # handlers wrap errors; yt-dlp keeps the original in DownloadError.exc_info
        class GeoRestrictedError(Exception):
            pass

        class DownloadError(Exception):
            def __init__(self, msg, exc_info=None):
                super().__init__(msg)
                self.exc_info = exc_info

        inner = GeoRestrictedError('geo')
        download_error = DownloadError('ERROR: [youtube] x: oops', exc_info=(GeoRestrictedError, inner, None))
        try:
            try:
                raise download_error
            except DownloadError as exc:
                raise RuntimeError('yt-dlp failed for YouTube: oops') from exc
        except RuntimeError as wrapped:
            self.assertEqual('geo_blocked', classify(wrapped, platform='YouTube').kind)

        class TooManyRequestsException(Exception):
            pass

        self.assertEqual('rate_limited', classify(TooManyRequestsException('x'), platform='Instagram').kind)
        self.assertEqual('network', classify(ConnectionResetError('peer'), platform='X').kind)

    def test_disk_errors(self):
        cases = {
            errno.ENOSPC: 'Could not save the file: the drive is full.',
            errno.EACCES: 'Could not save the file: no permission to write there.',
            errno.ENAMETOOLONG: 'Could not save the file: the path is too long.',
        }
        for code, message in cases.items():
            with self.subTest(code=code):
                try:
                    try:
                        raise OSError(code, 'x')
                    except OSError as exc:
                        raise RuntimeError('yt-dlp failed') from exc
                except RuntimeError as wrapped:
                    info = classify(wrapped)
                self.assertEqual(('disk', message, errors.CHANGE_FOLDER), (info.kind, info.message, info.action))

    def test_own_plain_messages_are_kept(self):
        from multidownloader.sources import gdrive, instagram
        for message in (gdrive.PRIVATE_MESSAGE, gdrive.NO_ACCESS_MESSAGE, gdrive.OAUTH_EXPIRED_MESSAGE,
                        instagram.CHECKPOINT_MESSAGE, 'Instagram did not accept your sign-in. Sign in again.'):
            with self.subTest(message=message[:40]):
                self.assertEqual(message, classify(RuntimeError(message), platform='Google Drive').message)
        self.assertTrue(classify(RuntimeError(gdrive.PRIVATE_MESSAGE)).needs_signin)
        self.assertFalse(classify(RuntimeError(gdrive.NO_ACCESS_MESSAGE)).needs_signin)

    def test_every_kind_has_an_action_or_none_on_purpose(self):
        for text, platform, signed_in, _kind in REAL_ERRORS:
            info = classify(text, platform=platform, signed_in=signed_in)
            self.assertIn(info.action, list(errors.ACTION_LABELS) + [None])

    def test_group_largest_first(self):
        a = classify('HTTP Error 404', platform='X')
        b = classify('Login required.', platform='Instagram')
        grouped = errors.group([('u1', a), ('u2', b), ('u3', b)])
        self.assertEqual([(b, ['u2', 'u3']), (a, ['u1'])], grouped)


class CliReportTests(unittest.TestCase):
    def test_next_steps(self):
        self.assertEqual('Run: multidownloader-signin import facebook --browser firefox',
                         next_step(classify('Login required.', platform='Facebook'), 'facebook'))
        self.assertIn('winget install DenoLand.Deno',
                      next_step(classify('No supported JavaScript runtime could be found', platform='YouTube')))
        self.assertIn('pip install -U yt-dlp', next_step(classify('Unable to extract x', platform='TikTok')))

    def test_report_groups_failures_largest_last(self):
        from multidownloader.runs import JobResult

        def failed(url, text, platform):
            info = classify(text, platform={'facebook': 'Facebook', 'x': 'X (Twitter)'}[platform])
            return JobResult(run_id='r', input_url=url, normalized_url=url, platform=platform, backend='yt-dlp',
                             status='failed', error_kind=info.kind, error_message=info.message,
                             error_action=info.action, error_detail=text)

        results = [failed('f1', 'Login required.', 'facebook'), failed('f2', 'Login required.', 'facebook'),
                   failed('x1', 'HTTP Error 404', 'x')]
        logger = logging.getLogger('test.report')
        with self.assertLogs(logger, level='ERROR') as logs:
            report_failures(results, logger)
        self.assertEqual(['1 link: This post was deleted or the link is wrong.',
                          '2 links: This post needs you to be signed in to Facebook. Run: multidownloader-signin '
                          'import facebook --browser firefox'],
                         [r.getMessage() for r in logs.records])


if __name__ == '__main__':
    unittest.main()
