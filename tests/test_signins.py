import http.cookiejar
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from multidownloader import session_store, signins
from multidownloader.core import Downloader
from multidownloader.platforms import BY_KEY, PLATFORMS
from multidownloader.signins import SignInError

try:
    import instaloader  # noqa: F401
    HAVE_INSTALOADER = True
except ImportError:
    HAVE_INSTALOADER = False


class RequestsCookieJar(http.cookiejar.CookieJar):
    """Stand-in for requests' jar: Instaloader's session cookies have .set()."""

    def set(self, name, value, domain='', path='/'):
        self.set_cookie(http.cookiejar.Cookie(
            0, name, value, None, False, domain, True, domain.startswith('.'), path, True, False,
            None, False, None, None, {}))


try:
    import yt_dlp.cookies  # noqa: F401
    HAVE_YTDLP = True
except ImportError:
    HAVE_YTDLP = False

NOW = 1_800_000_000  # fixed clock for status tests
DAY = 86400
IG = BY_KEY['instagram']
X = BY_KEY['x']
FB = BY_KEY['facebook']
YT = BY_KEY['youtube']


def line(domain, name, value='v', expires=NOW + 365 * DAY, http_only=False):
    prefix = '#HttpOnly_' if http_only else ''
    return f'{prefix}{domain}\tTRUE\t/\tTRUE\t{expires}\t{name}\t{value}'


def cookie_file(*lines, header=True, newline='\n'):
    rows = (['# Netscape HTTP Cookie File'] if header else []) + list(lines)
    return newline.join(rows) + newline


def big_mixed_file():
    """A 400-cookie export across many sites, 14 of them for instagram.com."""
    rows = [line('.instagram.com', 'sessionid', 'S', http_only=True),
            line('.instagram.com', 'ds_user_id', '1234567'),
            line('.instagram.com', 'csrftoken', 'C')]
    rows += [line('www.instagram.com', f'ig_{i}') for i in range(11)]
    rows += [line(f'.site{i % 37}.example', f'c{i}') for i in range(386)]
    rows += [line('.notinstagram.com', 'sessionid', 'fake')]  # lookalike domain
    return cookie_file(*rows)


class TempStore(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        patcher = mock.patch.object(session_store, 'SESSION_ROOT', self.tmp / '.sessions')
        patcher.start()
        self.addCleanup(patcher.stop)

    def save(self, platform, text, **kw):
        terms = platform.terms.version if platform.terms else None
        return signins.import_cookies_text(platform, text, terms_version=terms, **kw)


class ParseTests(unittest.TestCase):
    def test_lf_crlf_and_cr_endings_parse_the_same(self):
        rows = [line('.x.com', 'auth_token', 'A'), line('.x.com', 'twid', 'u%3D99')]
        parsed = [signins.parse_cookies_txt(cookie_file(*rows, newline=nl)) for nl in ('\n', '\r\n', '\r')]
        self.assertEqual(parsed[0], parsed[1])
        self.assertEqual(parsed[0], parsed[2])
        self.assertEqual(2, len(parsed[0]))

    def test_missing_header_is_accepted_when_lines_look_right(self):
        cookies = signins.parse_cookies_txt(cookie_file(line('.x.com', 'auth_token'), header=False))
        self.assertEqual('auth_token', cookies[0].name)

    def test_not_a_cookie_file(self):
        with self.assertRaises(SignInError) as ctx:
            signins.parse_cookies_txt('{"cookies": []}')
        self.assertIn('not in cookies.txt format', str(ctx.exception))

    def test_http_only_and_bom(self):
        text = '﻿' + cookie_file(line('.instagram.com', 'sessionid', http_only=True))
        cookie = signins.parse_cookies_txt(text)[0]
        self.assertTrue(cookie.http_only)
        self.assertEqual('.instagram.com', cookie.domain)
        self.assertTrue(cookie.to_line().startswith('#HttpOnly_.instagram.com'))

    def test_expiry_formats(self):
        unix = NOW
        cases = {str(unix): unix, f'{unix}.5': unix, str(unix * 1000): unix,
                 str((unix + 11644473600) * 10**6): unix, '0': 0, 'junk': 0}
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                text = cookie_file(f'.x.com\tTRUE\t/\tTRUE\t{raw}\tauth_token\tA')
                self.assertEqual(expected, signins.parse_cookies_txt(text)[0].expires)

    def test_round_trip(self):
        original = signins.parse_cookies_txt(cookie_file(line('.x.com', 'auth_token', 'A=B; c')))
        again = signins.parse_cookies_txt(signins.format_cookies_txt(original))
        self.assertEqual(original, again)


class MarkerTests(unittest.TestCase):
    def cookies(self, *rows):
        return signins.parse_cookies_txt(cookie_file(*rows))

    def test_all_groups_needed(self):
        self.assertFalse(signins.find_markers(FB, self.cookies(line('.facebook.com', 'c_user')), NOW)[0])
        found, _exp, expired = signins.find_markers(
            FB, self.cookies(line('.facebook.com', 'c_user'), line('.facebook.com', 'xs')), NOW)
        self.assertTrue(found)
        self.assertFalse(expired)

    def test_any_of_a_group(self):
        rows = (line('.youtube.com', '__Secure-3PAPISID'), line('.youtube.com', 'LOGIN_INFO'))
        self.assertTrue(signins.find_markers(YT, self.cookies(*rows), NOW)[0])

    def test_expired_and_earliest_expiry(self):
        found, expires, expired = signins.find_markers(
            FB, self.cookies(line('.facebook.com', 'c_user', expires=NOW + 10 * DAY),
                             line('.facebook.com', 'xs', expires=NOW + 2 * DAY)), NOW)
        self.assertEqual(NOW + 2 * DAY, expires)
        self.assertFalse(expired)
        self.assertTrue(signins.find_markers(X, self.cookies(line('.x.com', 'auth_token', expires=NOW - 1)), NOW)[2])

    def test_account_ids(self):
        self.assertEqual('1890', signins.account_id(X, self.cookies(line('.x.com', 'twid', '"u%3D1890"'))))
        self.assertEqual('42', signins.account_id(IG, self.cookies(line('.instagram.com', 'ds_user_id', '42'))))
        self.assertIsNone(signins.account_id(YT, self.cookies(line('.youtube.com', 'SID'))))

    def test_every_platform_has_markers_and_cookie_domains(self):
        for platform in [p for p in PLATFORMS if p.signin_need != 'none']:
            with self.subTest(platform=platform.key):
                self.assertTrue(platform.markers)
                self.assertTrue(platform.cookie_domains)


class ImportTests(TempStore):
    def test_mixed_400_cookie_file_keeps_only_the_platform(self):
        result = self.save(IG, big_mixed_file())
        self.assertEqual(14, result.kept)
        self.assertEqual(387, result.removed)
        self.assertEqual('1234567', result.account_id)
        self.assertIn('Kept 14 Instagram cookies. Removed 387 cookies for other sites', result.message())
        stored = signins.read_cookies(IG)
        self.assertEqual(14, len(stored))
        self.assertTrue(all(c.matches(IG.cookie_domains) for c in stored))
        self.assertNotIn('notinstagram', signins.cookie_path(IG).read_text(encoding='utf-8'))

    def test_signin_json_shape(self):
        self.save(IG, big_mixed_file(), label='Archive account')
        meta = json.loads((signins.folder(IG) / 'signin.json').read_text(encoding='utf-8'))
        for key in ('method', 'browser', 'profile', 'container', 'user_agent', 'account_id', 'label',
                    'imported_at', 'marker_expires_at', 'last_ok_at', 'last_rejected_at',
                    'terms_version_seen', 'acknowledged_at'):
            self.assertIn(key, meta)
        self.assertEqual('file', meta['method'])
        self.assertEqual(IG.terms.version, meta['terms_version_seen'])
        self.assertTrue(meta['imported_at'].endswith('Z'))

    def test_terms_must_be_acknowledged(self):
        text = cookie_file(line('.x.com', 'auth_token'))
        for version in (None, 'old-version'):
            with self.subTest(version=version), self.assertRaises(SignInError):
                signins.import_cookies_text(X, text, terms_version=version)
        self.assertFalse(signins.cookie_path(X).exists())
        self.assertTrue(signins.needs_terms_ack(X))
        self.save(X, text)
        self.assertFalse(signins.needs_terms_ack(X))

    def test_new_terms_version_asks_again(self):
        self.save(X, cookie_file(line('.x.com', 'auth_token')))
        meta = signins.read_meta(X)
        meta['terms_version_seen'] = '2000-01-01'
        session_store.write_json('Twitter', 'signin.json', meta)
        self.assertTrue(signins.needs_terms_ack(X))

    def test_messages(self):
        with self.assertRaises(SignInError) as ctx:
            self.save(IG, cookie_file(line('.instagram.com', 'csrftoken')))
        self.assertEqual('This file does not contain an Instagram sign-in. Sign in on instagram.com before exporting.',
                         str(ctx.exception))
        with self.assertRaises(SignInError) as ctx:
            self.save(IG, cookie_file(line('.instagram.com', 'sessionid', expires=int(time.time()) - 60)))
        self.assertEqual('This sign-in has expired. Sign in again and export a new file.', str(ctx.exception))

    def test_original_file_is_not_copied(self):
        source = self.tmp / 'export.txt'
        source.write_text(big_mixed_file(), encoding='utf-8')
        signins.import_cookies_file(IG, source, terms_version=IG.terms.version)
        self.assertEqual(big_mixed_file(), source.read_text(encoding='utf-8'))
        files = {p.name for p in signins.folder(IG).iterdir()}
        self.assertEqual({'cookies.txt', 'signin.json'}, files)

    @unittest.skipUnless(os.name == 'posix', 'POSIX permissions')
    def test_permissions(self):
        self.save(IG, big_mixed_file())
        self.assertEqual(0o600, signins.cookie_path(IG).stat().st_mode & 0o777)
        self.assertEqual(0o700, signins.folder(IG).stat().st_mode & 0o777)


class StatusTests(TempStore):
    def write(self, platform, *rows, meta=None):
        signins.folder(platform)
        signins.cookie_path(platform).write_text(cookie_file(*rows), encoding='utf-8')
        if meta is not None:
            session_store.write_json(signins._folder_name(platform), 'signin.json', meta)

    def test_nothing_stored(self):
        self.assertEqual('not_set_up', signins.status(IG, NOW).state)
        self.assertEqual('Not set up', signins.status(IG, NOW).text)
        st = signins.status(YT, NOW)
        self.assertEqual(('not_needed', 'Not needed for public posts'), (st.state, st.text))

    def test_ready_text(self):
        self.write(IG, line('.instagram.com', 'sessionid'), line('.instagram.com', 'ds_user_id', '1234567'),
                   meta={'method': 'browser', 'browser': 'firefox', 'account_id': '1234567'})
        st = signins.status(IG, NOW)
        self.assertEqual('ready', st.state)
        self.assertEqual('Signed in (account 1234...) via Firefox', st.text)
        self.assertEqual(('✓', 'success'), (st.glyph, st.tone))

    def test_label(self):
        self.write(X, line('.x.com', 'auth_token'), meta={'method': 'file', 'label': 'Archive account'})
        self.assertEqual('Signed in as Archive account via cookies file', signins.status(X, NOW).text)

    def test_expires_soon_expired_rejected_unknown(self):
        self.write(X, line('.x.com', 'auth_token', expires=NOW + 2 * DAY), meta={'method': 'file'})
        st = signins.status(X, NOW)
        self.assertEqual(('expires_soon', 'Expires in 2 days. Refresh', '!'), (st.state, st.text, st.glyph))

        self.write(X, line('.x.com', 'auth_token', expires=NOW - 1), meta={'method': 'file'})
        self.assertEqual(('expired', 'Signed out. Sign in again'), (signins.status(X, NOW).state,
                                                                    signins.status(X, NOW).text))

        self.write(X, line('.x.com', 'auth_token'), meta={'method': 'file', 'last_ok_at': '2026-01-01T00:00:00Z',
                                                           'last_rejected_at': '2026-02-01T00:00:00Z'})
        self.assertEqual('rejected', signins.status(X, NOW).state)
        signins.record_result('Twitter', True)
        self.assertEqual('ready', signins.status(X, NOW).state)
        signins.record_result('Twitter', False, 'HTTP Error 401: login required')
        self.assertEqual('rejected', signins.status(X, NOW).state)

        self.write(X, line('.x.com', 'guest_id'), meta={'method': 'file'})
        self.assertEqual('unknown', signins.status(X, NOW).state)

    def test_visitor_cookies_without_signin_json_are_not_a_signin(self):
        # B19 for installs that already have an auto-created cookies.txt
        self.write(X, line('.x.com', 'guest_id'))
        self.assertEqual('not_set_up', signins.status(X, NOW).state)
        self.assertIsNone(signins.cookie_file_for_download('Twitter'))

    def test_legacy_imported_file_with_a_signin_still_works(self):
        self.write(X, line('.x.com', 'auth_token'))
        self.assertEqual('ready', signins.status(X, NOW).state)
        self.assertEqual(signins.cookie_path(X), signins.cookie_file_for_download('Twitter'))

    def test_legacy_instagram_session(self):
        session_store.write_json('Instagram', 'meta.json', {'username': 'archiver', 'filename': 'archiver.session'})
        (signins.folder(IG) / 'archiver.session').write_bytes(b'x')
        st = signins.status(IG, NOW)
        self.assertEqual(('ready', 'Signed in as archiver via saved session'), (st.state, st.text))

    def test_summary(self):
        self.assertEqual('', signins.summary())
        self.write(X, line('.x.com', 'auth_token'), meta={'method': 'file'})
        self.write(IG, line('.instagram.com', 'sessionid'), meta={'method': 'file'})
        self.assertEqual('2 ready', signins.summary())
        self.write(FB, line('.facebook.com', 'c_user', expires=int(time.time()) - 5),
                   line('.facebook.com', 'xs'), meta={'method': 'file'})
        self.assertEqual('1 needs attention', signins.summary())

    def test_preflight(self):
        urls = ['https://www.instagram.com/p/a/', 'https://www.instagram.com/p/b/', 'https://youtu.be/abc123']
        self.assertEqual(['Instagram may refuse some of these links without a sign-in.'],
                         signins.preflight_warnings(urls))
        self.write(IG, line('.instagram.com', 'sessionid'), meta={'method': 'file'})
        self.assertEqual([], signins.preflight_warnings(urls))


class RemoveTests(TempStore):
    def test_remove_deletes_files_but_keeps_client_secrets(self):
        self.save(IG, big_mixed_file())
        (signins.folder(IG) / 'archiver.session').write_bytes(b'x')
        drive = BY_KEY['gdrive']
        (signins.folder(drive) / 'credentials.json').write_text('{}', encoding='utf-8')
        (signins.folder(drive) / 'client_secrets.json').write_text('{}', encoding='utf-8')
        signins.remove_all()
        self.assertEqual([], list(signins.folder(IG).iterdir()))
        self.assertEqual(['client_secrets.json'], [p.name for p in signins.folder(drive).iterdir()])

    def test_rename(self):
        self.save(X, cookie_file(line('.x.com', 'auth_token')))
        signins.rename(X, 'Archive account')
        self.assertEqual('Archive account', signins.read_meta(X)['label'])


def fake_jar(*cookies):
    jar = http.cookiejar.CookieJar()
    for domain, name, value in cookies:
        jar.set_cookie(http.cookiejar.Cookie(
            0, name, value, None, False, domain, True, domain.startswith('.'), '/', True, True,
            NOW + 365 * DAY, False, None, None, {}))
    return jar


@unittest.skipUnless(HAVE_YTDLP, 'yt-dlp not installed')
class BrowserTests(TempStore):
    def patch_extract(self, jar=None, error=None):
        target = 'yt_dlp.cookies.extract_cookies_from_browser'
        if error:
            return mock.patch(target, side_effect=error)
        return mock.patch(target, return_value=jar)

    def test_import_from_firefox_filters_and_records_method(self):
        jar = fake_jar(('.x.com', 'auth_token', 'A'), ('.x.com', 'twid', 'u%3D77'), ('.google.com', 'SID', 'G'))
        with self.patch_extract(jar) as extract:
            result = signins.import_from_browser(X, 'firefox', None, terms_version=X.terms.version)
        extract.assert_called_once()
        self.assertEqual(('firefox', None), extract.call_args.args[:2])
        self.assertEqual((2, 1, '77'), (result.kept, result.removed, result.account_id))
        meta = signins.read_meta(X)
        self.assertEqual(('browser', 'firefox'), (meta['method'], meta['browser']))
        self.assertFalse(signins.writes_back('Twitter'))
        self.assertIn('via Firefox', signins.status(X).text)

    def test_no_signin_in_browser(self):
        with self.patch_extract(fake_jar(('.x.com', 'guest_id', 'g'))), self.assertRaises(SignInError) as ctx:
            signins.import_from_browser(X, 'firefox', None, terms_version=X.terms.version)
        self.assertEqual('No X (Twitter) sign-in found in Firefox. Sign in to X (Twitter) there first.',
                         str(ctx.exception))

    def test_locked_database_message(self):
        with self.patch_extract(error=RuntimeError('database is locked')), self.assertRaises(SignInError) as ctx:
            signins.read_browser_cookies('firefox')
        self.assertEqual('Close Firefox and try again.', str(ctx.exception))

    @unittest.skipUnless(sys.platform == 'win32', 'Windows message')
    def test_chromium_on_windows_message(self):
        with self.patch_extract(error=RuntimeError('Failed to decrypt')), self.assertRaises(SignInError) as ctx:
            signins.read_browser_cookies('chrome')
        self.assertIn('locks its sign-ins on Windows', str(ctx.exception))

    def test_refresh_rereads_the_browser(self):
        with self.patch_extract(fake_jar(('.x.com', 'auth_token', 'old'))):
            signins.import_from_browser(X, 'firefox', None, terms_version=X.terms.version)
        with self.patch_extract(fake_jar(('.x.com', 'auth_token', 'new'))):
            self.assertTrue(signins.refresh_from_browser(X))
        self.assertEqual('new', signins.read_cookies(X)[0].value)
        # a failed read keeps the stored copy
        with self.patch_extract(error=RuntimeError('database is locked')):
            self.assertFalse(signins.refresh_from_browser(X))
        self.assertEqual('new', signins.read_cookies(X)[0].value)

    def test_firefox_profiles_and_detection(self):
        root = self.tmp / 'Profiles'
        for name in ('abcd.default-release', 'efgh.work', 'empty.none'):
            (root / name).mkdir(parents=True)
        (root / 'abcd.default-release' / 'cookies.sqlite').write_bytes(b'')
        (root / 'efgh.work' / 'cookies.sqlite').write_bytes(b'')
        with mock.patch('yt_dlp.cookies._firefox_browser_dirs', return_value=[str(root)]):
            profiles = signins.firefox_profiles()
            self.assertEqual({'default-release', 'work'}, {p.name for p in profiles})

            def by_profile(browser, profile, logger):
                if profile.endswith('work'):
                    return fake_jar(('.instagram.com', 'sessionid', 's'), ('.instagram.com', 'ds_user_id', '9'))
                return fake_jar(('.instagram.com', 'csrftoken', 'c'))

            with mock.patch('yt_dlp.cookies.extract_cookies_from_browser', side_effect=by_profile):
                found = signins.find_firefox_signins(IG)
        self.assertEqual([('work', '9')], [(f.profile.name, f.account_id) for f in found])


class DownloaderWiringTests(TempStore):
    def test_results_are_recorded_and_browser_refreshed_once(self):
        self.save(X, cookie_file(line('.x.com', 'auth_token')))
        downloader = Downloader(str(self.tmp))
        handler = mock.Mock()
        handler.download.side_effect = [True, RuntimeError('ERROR: login required'), RuntimeError('HTTP 500')]
        downloader.handlers['Twitter'] = handler
        with mock.patch.object(signins, 'refresh_from_browser') as refresh:
            downloader.download('Twitter', 'https://x.com/a/status/1')
            self.assertIsNotNone(signins.read_meta(X)['last_ok_at'])
            with self.assertRaises(RuntimeError):
                downloader.download('Twitter', 'https://x.com/a/status/2')
            self.assertEqual('rejected', signins.status(X).state)
            with self.assertRaises(RuntimeError):
                downloader.download('Twitter', 'https://x.com/a/status/3')
        refresh.assert_called_once()

    def test_youtube_bot_check_is_not_a_rejected_signin(self):
        # yt-dlp's real message mentions signing in and cookies, but it is a
        # block: never mark the sign-in rejected or suggest signing in
        message = ("ERROR: [youtube] dQw4w9WgXcQ: Sign in to confirm you're not a bot. Use --cookies-from-browser "
                   'or --cookies for the authentication. See https://github.com/yt-dlp/yt-dlp/wiki/FAQ')
        self.assertFalse(signins.looks_like_auth_error(message))
        self.assertTrue(signins.looks_like_block(message))
        self.assertFalse(signins.looks_like_auth_error('HTTP Error 429: Too Many Requests'))
        self.assertTrue(signins.looks_like_auth_error('ERROR: [Instagram] abc: login required'))

        yt = BY_KEY['youtube']
        self.save(yt, cookie_file(line('.youtube.com', 'SAPISID'), line('.youtube.com', 'LOGIN_INFO')))
        before = signins.read_meta(yt)
        signins.record_result('YouTube', False, message)
        self.assertEqual(before, signins.read_meta(yt))
        self.assertEqual('ready', signins.status(yt).state)

    def test_refreshed_cookies_are_filtered_on_write_back(self):
        self.save(X, cookie_file(line('.x.com', 'auth_token', 'old')))
        jar = fake_jar(('.x.com', 'auth_token', 'new'), ('.twimg.com', 'cdn', '1'), ('.doubleclick.net', 'ad', '2'))
        self.assertTrue(signins.save_refreshed('Twitter', jar))
        stored = signins.read_cookies(X)
        self.assertEqual([('auth_token', 'new')], [(c.name, c.value) for c in stored])
        # a jar that lost the sign-in is not written
        self.assertFalse(signins.save_refreshed('Twitter', fake_jar(('.x.com', 'guest_id', 'g'))))
        self.assertEqual('new', signins.read_cookies(X)[0].value)

    def test_no_signin_no_record(self):
        downloader = Downloader(str(self.tmp))
        handler = mock.Mock()
        handler.download.side_effect = RuntimeError('login required')
        downloader.handlers['Twitter'] = handler
        with self.assertRaises(RuntimeError):
            downloader.download('Twitter', 'https://x.com/a/status/1')
        self.assertEqual({}, signins.read_meta(X))


class InstagramSigninTests(TempStore):
    def test_loader_built_from_cookies(self):
        from multidownloader.sources import instagram
        self.save(IG, big_mixed_file())
        made = []

        class FakeContext:
            def __init__(self):
                self._session = mock.Mock()
                self._session.cookies = RequestsCookieJar()
                self.username = None

        class FakeLoader:
            def __init__(self, *a, **kw):
                self.context = FakeContext()
                made.append(self)

            def test_login(self):
                names = {c.name for c in self.context._session.cookies}
                return 'archiver' if 'sessionid' in names else None

        with mock.patch.object(instagram, 'Instaloader', FakeLoader):
            handler = instagram.InstagramHandler()
            loader = handler._ensure_loader(None, 'auto')
            self.assertIs(loader, handler._ensure_loader(None, 'auto'))  # checked once
        self.assertEqual('archiver', loader.context.username)
        self.assertEqual(1, len(made))

    def test_rejected_cookies(self):
        from multidownloader.sources import instagram
        self.save(IG, big_mixed_file())

        class FakeLoader:
            def __init__(self, *a, **kw):
                self.context = mock.Mock()
                self.context._session.cookies = RequestsCookieJar()

            def test_login(self):
                return None

        with mock.patch.object(instagram, 'Instaloader', FakeLoader), self.assertRaises(RuntimeError) as ctx:
            instagram.InstagramHandler()._ensure_loader(None, 'auto')
        self.assertEqual('Instagram did not accept your sign-in. Sign in again.', str(ctx.exception))
        self.assertEqual('rejected', signins.status(IG).state)

    @unittest.skipUnless(HAVE_INSTALOADER, 'instaloader not installed')
    def test_password_errors(self):
        from instaloader import exceptions as ex

        from multidownloader.sources import instagram
        cases = [
            (ex.BadCredentialsException('bad'), 'Wrong username or password.'),
            (ex.ConnectionException('Login: Checkpoint required'), instagram.CHECKPOINT_MESSAGE),
            (ex.LoginException('Your account has been locked'),
             'This account is locked by Instagram. Use it in your browser to unlock it.'),
        ]
        for error, message in cases:
            fake = mock.Mock()
            fake.return_value.login.side_effect = error
            with self.subTest(error=error), mock.patch.object(instagram, 'Instaloader', fake), \
                    self.assertRaises(SignInError) as ctx:
                instagram.InstagramHandler().password_login('u', 'p', lambda: None)
            self.assertEqual(message, str(ctx.exception))

    @unittest.skipUnless(HAVE_INSTALOADER, 'instaloader not installed')
    def test_two_factor(self):
        from instaloader import exceptions as ex

        from multidownloader.sources import instagram
        fake = mock.Mock()
        fake.return_value.login.side_effect = ex.TwoFactorAuthRequiredException('2fa')
        with mock.patch.object(instagram, 'Instaloader', fake):
            instagram.InstagramHandler().password_login('u', 'p', lambda: '123456')
            fake.return_value.two_factor_login.assert_called_once_with('123456')
            with self.assertRaises(SignInError) as ctx:
                instagram.InstagramHandler().password_login('u', 'p', lambda: None)
        self.assertEqual('Sign-in cancelled.', str(ctx.exception))


class NoUiImportTests(unittest.TestCase):
    def test_signins_module_does_not_need_tk(self):
        import subprocess
        code = "import sys; sys.modules['tkinter'] = None; import multidownloader.signins, multidownloader.core"
        root = Path(__file__).resolve().parents[1]
        proc = subprocess.run([sys.executable, '-c', code], cwd=root, capture_output=True, text=True)
        self.assertEqual(0, proc.returncode, proc.stderr)


if __name__ == '__main__':
    unittest.main()
