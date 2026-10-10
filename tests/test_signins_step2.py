"""Phase 2 step 2: "Sign in here" (in-app window) and Google Drive sign-in."""
import io
import itertools
import json
import os
import tempfile
import unittest
from http.cookies import SimpleCookie
from pathlib import Path
from unittest import mock

from multidownloader import session_store, signins
from multidownloader.gui import signin_window
from multidownloader.platforms import BY_KEY
from multidownloader.signins import SignInError
from multidownloader.sources import gdrive
from multidownloader.sources.gdrive import GoogleDriveHandler
from multidownloader.sources.yt_dlp_base import YtDlpHandler

IG = BY_KEY['instagram']
X = BY_KEY['x']
DRIVE = BY_KEY['gdrive']
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Edg/141.0'


def morsel(name, value, domain, expires='', httponly=False):
    c = SimpleCookie()
    c[name] = value
    c[name]['domain'] = domain
    c[name]['path'] = '/'
    c[name]['expires'] = expires
    c[name]['secure'] = True
    c[name]['httponly'] = httponly
    return c


class TempStore(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        patcher = mock.patch.object(session_store, 'SESSION_ROOT', self.tmp / '.sessions')
        patcher.start()
        self.addCleanup(patcher.stop)


class PlatformTableTests(unittest.TestCase):
    def test_google_sites_never_offer_sign_in_here(self):
        # Google blocks sign-in inside embedded windows (disallowed_useragent)
        self.assertIsNone(BY_KEY['youtube'].web_signin_url)
        self.assertIsNone(DRIVE.web_signin_url)
        for key in ('instagram', 'x', 'facebook', 'tiktok', 'threads', 'reddit'):
            with self.subTest(key=key):
                self.assertTrue(BY_KEY[key].web_signin_url.startswith('https://'))

    def test_logged_out_cookies_are_not_a_signin(self):
        # cookie names the live sites set for a logged-out visitor, recorded
        # on 2026-10-09 by loading each sign-in or home page without signing in
        anonymous = {
            'reddit': ['edgebucket'],
            'tiktok': ['msToken', 'tiktok_webapp_theme', 'tiktok_webapp_theme_source', 'tt_chain_token',
                       'tt_csrf_token', 'ttwid'],
            'instagram': ['csrftoken', 'datr', 'dpr', 'ig_did', 'mid', 'wd'],
            'x': ['__cf_bm', '__cuid', 'g_state', 'gt', 'guest_id', 'guest_id_ads', 'guest_id_marketing',
                  'personalization_id'],
            'facebook': ['datr', 'dpr', 'fr', 'ps_l', 'ps_n', 'sb', 'wd'],
            'threads': ['csrftoken', 'dpr', 'ig_did'],
        }
        for key, names in anonymous.items():
            platform = BY_KEY[key]
            items = [{'name': n, 'value': 'v', 'domain': '.' + platform.cookie_domains[0]} for n in names]
            with self.subTest(key=key):
                self.assertFalse(signins.find_markers(platform, signins.cookies_from_webview(items))[0])

    def test_expected_hosts(self):
        self.assertTrue(IG.expected_signin_host('www.instagram.com'))
        self.assertTrue(BY_KEY['threads'].expected_signin_host('www.instagram.com'))
        self.assertFalse(IG.expected_signin_host('instagram.com.evil.example'))
        self.assertFalse(X.expected_signin_host('notx.com'))


class WebviewCookieTests(TempStore):
    def test_morsels_and_expiry(self):
        items = signin_window.morsels_to_dicts([
            morsel('sessionid', 'S', '.instagram.com', 'Sat, 09 Oct 2027 05:31:14 GMT', httponly=True),
            morsel('csrftoken', 'C', '.instagram.com', 'Thu, 01 Jan 1970 00:00:00 GMT'),
            morsel('ds_user_id', '1234567', '.instagram.com'),
        ])
        self.assertEqual(3, len(items))
        cookies = signins.cookies_from_webview(items)
        by_name = {c.name: c for c in cookies}
        self.assertTrue(by_name['sessionid'].http_only)
        self.assertEqual(1823059874, by_name['sessionid'].expires)
        self.assertEqual(0, by_name['csrftoken'].expires)  # session cookie
        self.assertEqual(0, by_name['ds_user_id'].expires)

    def test_save_webview_signin_records_user_agent(self):
        items = [{'name': 'sessionid', 'value': 'S', 'domain': '.instagram.com', 'expires': ''},
                 {'name': 'ds_user_id', 'value': '987654', 'domain': '.instagram.com'},
                 {'name': 'other', 'value': 'x', 'domain': '.facebook.com'}]
        result = signins.save_webview_signin(IG, items, user_agent=UA, terms_version=IG.terms.version)
        self.assertEqual((2, 1, '987654'), (result.kept, result.removed, result.account_id))
        meta = signins.read_meta(IG)
        self.assertEqual(('webview', UA), (meta['method'], meta['user_agent']))
        self.assertEqual('Signed in (account 9876...) via Sign in here', signins.status(IG).text)

    def test_terms_still_required(self):
        with self.assertRaises(SignInError):
            signins.save_webview_signin(IG, [{'name': 'sessionid', 'value': 'S', 'domain': '.instagram.com'}],
                                        user_agent=None, terms_version=None)

    def test_no_signin_cookie_message(self):
        with self.assertRaises(SignInError) as ctx:
            signins.save_webview_signin(X, [{'name': 'guest_id', 'value': 'g', 'domain': '.x.com'}],
                                        user_agent=None, terms_version=X.terms.version)
        self.assertEqual('X (Twitter) did not finish signing you in. Try again.', str(ctx.exception))

    def test_user_agent_goes_to_ytdlp(self):
        signins.save_webview_signin(X, [{'name': 'auth_token', 'value': 'A', 'domain': '.x.com'}],
                                    user_agent=UA, terms_version=X.terms.version)
        handler = YtDlpHandler('Twitter')
        opts = handler._build_opts(str(self.tmp), {}, handler._resolve_cookie_path({}))
        self.assertEqual({'User-Agent': UA}, opts['http_headers'])
        signins.remove(X)
        self.assertIsNone(signins.user_agent_for('Twitter'))


class WebviewAvailabilityTests(unittest.TestCase):
    def test_missing_pywebview(self):
        with mock.patch('importlib.util.find_spec', return_value=None):
            self.assertEqual((False, signins.PYWEBVIEW_MESSAGE), signins.webview_available())

    def test_missing_webview2_runtime_on_windows(self):
        with mock.patch('importlib.util.find_spec', return_value=object()), \
                mock.patch.object(signins.sys, 'platform', 'win32'), \
                mock.patch.object(signins, '_webview2_installed', return_value=False):
            self.assertEqual((False, signins.WEBVIEW2_MESSAGE), signins.webview_available())

    def test_available(self):
        with mock.patch('importlib.util.find_spec', return_value=object()), \
                mock.patch.object(signins, '_webview2_installed', return_value=True):
            self.assertEqual((True, ''), signins.webview_available())


class FakeWindow:
    """Stands in for a pywebview window: cookies appear after a few polls."""

    def __init__(self, polls_before_signin=2):
        self.polls = 0
        self.polls_before_signin = polls_before_signin
        self.destroyed = False

    def get_current_url(self):
        return 'https://www.instagram.com/accounts/login/' if self.polls < 2 else 'https://www.instagram.com/'

    def get_cookies(self):
        self.polls += 1
        found = [morsel('csrftoken', 'C', '.instagram.com')]
        if self.polls > self.polls_before_signin:
            found += [morsel('sessionid', 'S', '.instagram.com'), morsel('ds_user_id', '42', '.instagram.com')]
        return found

    def evaluate_js(self, script):
        return UA

    def destroy(self):
        self.destroyed = True


class SigninWindowTests(TempStore):
    def run_watch(self, window, timeout):
        out = self.tmp / 'out.json'
        out.write_text('', encoding='utf-8')
        state = {}
        stdout = io.StringIO()
        with mock.patch.object(signin_window, 'POLL_SECONDS', 0), \
                mock.patch.object(signin_window, 'SETTLE_SECONDS', 0), \
                mock.patch('sys.stdout', stdout):
            signin_window.watch(window, IG, str(out), timeout, state)
        events = [json.loads(line) for line in stdout.getvalue().splitlines()]
        return out, state, events

    def test_writes_cookies_and_user_agent_once_signed_in(self):
        window = FakeWindow()
        out, state, events = self.run_watch(window, timeout=5)
        self.assertTrue(state['done'])
        self.assertTrue(window.destroyed)
        self.assertEqual('done', events[-1]['event'])
        self.assertIn({'event': 'url', 'url': 'https://www.instagram.com/accounts/login/'}, events)
        data = json.loads(out.read_text(encoding='utf-8'))
        self.assertEqual(UA, data['user_agent'])
        self.assertEqual({'csrftoken', 'sessionid', 'ds_user_id'}, {c['name'] for c in data['cookies']})

    def test_times_out_without_a_signin(self):
        window = FakeWindow(polls_before_signin=10**9)
        with mock.patch.object(signin_window.time, 'time', side_effect=itertools.chain([0, 0, 0], itertools.repeat(1000))):
            out, state, events = self.run_watch(window, timeout=10)
        self.assertTrue(state['timed_out'])
        self.assertEqual('timeout', events[-1]['event'])
        self.assertEqual('', out.read_text(encoding='utf-8'))


class DriveDownloadTests(TempStore):
    def handler(self):
        return GoogleDriveHandler()

    def test_public_link_needs_nothing(self):
        h = self.handler()
        with mock.patch.object(h, '_download_public_file', return_value=True) as public:
            self.assertTrue(h.download('https://drive.google.com/file/d/F1/view', str(self.tmp), {}))
        public.assert_called_once_with('https://drive.google.com/file/d/F1/view',
                                       os.path.join(str(self.tmp), 'gdrive', 'F1'), None)

    def test_private_link_without_signin(self):
        h = self.handler()
        refused = RuntimeError("gdown failed: Cannot retrieve the public link of the file. You may need to change "
                               "the permission to 'Anyone with the link'")
        with mock.patch.object(h, '_download_public_file', side_effect=refused):
            with self.assertRaises(RuntimeError) as ctx:
                h.download('https://drive.google.com/file/d/F1/view', str(self.tmp), {})
        self.assertEqual(gdrive.PRIVATE_MESSAGE, str(ctx.exception))
        self.assertTrue(signins.looks_like_auth_error(str(ctx.exception)))  # offers sign-in

    def test_other_errors_are_not_turned_into_sign_in_errors(self):
        h = self.handler()
        with mock.patch.object(h, '_download_public_file', side_effect=RuntimeError('gdown failed: timed out')):
            with self.assertRaises(RuntimeError) as ctx:
                h.download('https://drive.google.com/file/d/F1/view', str(self.tmp), {})
        self.assertIn('timed out', str(ctx.exception))

    def test_cookie_signin_is_passed_as_a_throwaway_copy(self):
        signins.import_cookies_text(DRIVE, '.google.com\tTRUE\t/\tTRUE\t0\tSID\tS\n', terms_version=None)
        seen = {}

        def fake_public(url, out_dir, cookies_file):
            seen['path'] = cookies_file
            seen['text'] = Path(cookies_file).read_text(encoding='utf-8')
            return True

        h = self.handler()
        with mock.patch.object(h, '_download_public_file', side_effect=fake_public):
            h.download('https://drive.google.com/file/d/F1/view', str(self.tmp), {})
        self.assertNotEqual(signins.cookie_path(DRIVE), Path(seen['path']))
        self.assertIn('SID', seen['text'])
        self.assertFalse(os.path.exists(seen['path']))  # deleted afterwards

    def test_signed_in_but_no_access(self):
        signins.import_cookies_text(DRIVE, '.google.com\tTRUE\t/\tTRUE\t0\tSID\tS\n', terms_version=None)
        h = self.handler()
        with mock.patch.object(h, '_download_public_file', side_effect=RuntimeError('HTTP 403 permission denied')):
            with self.assertRaises(RuntimeError) as ctx:
                h.download('https://drive.google.com/file/d/F1/view', str(self.tmp), {})
        self.assertEqual(gdrive.NO_ACCESS_MESSAGE, str(ctx.exception))
        self.assertFalse(signins.looks_like_auth_error(str(ctx.exception)))  # not a rejected sign-in

    def test_oauth_used_when_drive_refuses(self):
        signins.save_oauth_signin(DRIVE)
        gdrive.credentials_path().write_text('{}', encoding='utf-8')
        h = self.handler()
        with mock.patch.object(h, '_download_public_file', side_effect=RuntimeError('permission denied')), \
                mock.patch.object(h, '_download_authenticated_file', return_value='saved') as oauth:
            self.assertEqual('saved', h.download('https://drive.google.com/file/d/F1/view', str(self.tmp), {}))
        oauth.assert_called_once_with('F1', os.path.join(str(self.tmp), 'gdrive', 'F1'))

    def test_explicit_public_never_signs_in(self):
        signins.save_oauth_signin(DRIVE)
        gdrive.credentials_path().write_text('{}', encoding='utf-8')
        h = self.handler()
        with mock.patch.object(h, '_download_public_file', side_effect=RuntimeError('permission denied')), \
                mock.patch.object(h, '_download_authenticated_file') as oauth, self.assertRaises(RuntimeError):
            h.download('https://drive.google.com/file/d/F1/view', str(self.tmp), {'method': 'public'})
        oauth.assert_not_called()

    def test_gdown_5_and_6_arguments(self):
        h = self.handler()

        def gdown5(url=None, output=None, quiet=False, fuzzy=False, use_cookies=True):
            pass

        def gdown6(url=None, output=None, quiet=False, use_cookies=True, cookies_file=None):
            pass

        self.assertEqual({'quiet': False, 'fuzzy': True}, h._gdown_kwargs(gdown5, None))
        self.assertEqual({'quiet': False}, h._gdown_kwargs(gdown6, None))
        self.assertEqual({'quiet': False, 'cookies_file': 'c.txt'}, h._gdown_kwargs(gdown6, 'c.txt'))
        with self.assertRaises(RuntimeError) as ctx:
            h._gdown_kwargs(gdown5, 'c.txt')
        self.assertEqual(gdrive.GDOWN6_NEEDED, str(ctx.exception))

    def test_downloads_never_open_the_browser(self):
        gdrive.client_secrets_path().write_text('{"installed": {}}', encoding='utf-8')
        fake_auth = mock.Mock()
        fake_auth.return_value.credentials = None
        with mock.patch.object(gdrive, 'PYDRIVE2_AVAILABLE', True), mock.patch.object(gdrive, 'GoogleAuth', fake_auth), \
                mock.patch.object(gdrive, 'LEGACY_CLIENT_SECRETS', self.tmp / 'none.json'):
            with self.assertRaises(RuntimeError) as ctx:
                self.handler()._get_drive_client()
        self.assertEqual(gdrive.NOT_CONNECTED_MESSAGE, str(ctx.exception))
        fake_auth.return_value.LocalWebserverAuth.assert_not_called()

    def test_expired_testing_token(self):
        signins.save_oauth_signin(DRIVE)
        gdrive.client_secrets_path().write_text('{"installed": {}}', encoding='utf-8')
        gdrive.credentials_path().write_text('{}', encoding='utf-8')
        fake_auth = mock.Mock()
        fake_auth.return_value.credentials = object()
        fake_auth.return_value.access_token_expired = True
        fake_auth.return_value.Refresh.side_effect = RuntimeError("invalid_grant: Token has been expired or revoked.")
        with mock.patch.object(gdrive, 'PYDRIVE2_AVAILABLE', True), mock.patch.object(gdrive, 'GoogleAuth', fake_auth):
            with self.assertRaises(RuntimeError) as ctx:
                self.handler()._get_drive_client()
        self.assertEqual(gdrive.OAUTH_EXPIRED_MESSAGE, str(ctx.exception))
        self.assertEqual(('rejected', 'Your Google connection expired. Reconnect'),
                         (signins.status(DRIVE).state, signins.status(DRIVE).text))

    def test_legacy_client_secrets_is_copied_not_moved(self):
        legacy = self.tmp / 'client_secrets.json'
        legacy.write_text('{"installed": {"client_id": "a", "client_secret": "b"}}', encoding='utf-8')
        with mock.patch.object(gdrive, 'LEGACY_CLIENT_SECRETS', legacy):
            found = gdrive._find_client_secrets()
        self.assertEqual(gdrive.client_secrets_path(), found)
        self.assertTrue(legacy.exists())


class DriveOauthTests(TempStore):
    def test_validate_client_secrets(self):
        good = json.dumps({'installed': {'client_id': 'id', 'client_secret': 's'}})
        gdrive.validate_client_secrets(good)
        cases = {
            'not json': 'not the JSON file',
            json.dumps({'web': {'client_id': 'x'}}): 'Desktop app OAuth client instead',
            json.dumps({'installed': {'client_id': 'x'}}): 'no Desktop app OAuth client',
        }
        for text, expected in cases.items():
            with self.subTest(text=text), self.assertRaises(SignInError) as ctx:
                gdrive.validate_client_secrets(text)
            self.assertIn(expected, str(ctx.exception))

    def test_connect_stores_client_and_records_oauth(self):
        client = self.tmp / 'client.json'
        client.write_text(json.dumps({'installed': {'client_id': 'id', 'client_secret': 's'}}), encoding='utf-8')
        h = GoogleDriveHandler()
        with mock.patch.object(h, '_get_drive_client') as get:
            h.connect_oauth(client)
        get.assert_called_once_with(interactive=True)
        self.assertEqual(client.read_text(encoding='utf-8'), gdrive.client_secrets_path().read_text(encoding='utf-8'))
        self.assertEqual('oauth', signins.read_meta(DRIVE)['method'])

    def test_oauth_status(self):
        signins.save_oauth_signin(DRIVE)
        self.assertEqual('expired', signins.status(DRIVE).state)  # no token saved
        gdrive.credentials_path().write_text('{}', encoding='utf-8')
        st = signins.status(DRIVE)
        self.assertEqual(('ready', 'Connected with your Google Cloud project'), (st.state, st.text))
        signins.remove(DRIVE)
        self.assertEqual('not_needed', signins.status(DRIVE).state)


if __name__ == '__main__':
    unittest.main()
