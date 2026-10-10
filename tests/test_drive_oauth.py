import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from multidownloader import errors, session_store, signins
from multidownloader.platforms import BY_KEY
from multidownloader.sources import gdrive

DRIVE = BY_KEY['gdrive']


class FakeResponse:
    def __init__(self, status=200, payload=None, body=b'', reason='OK'):
        self.status_code, self.payload, self.body, self.reason = status, payload, body, reason

    def json(self):
        if self.payload is None:
            raise ValueError('no json')
        return self.payload

    def iter_content(self, size):
        for i in range(0, len(self.body), 4):
            yield self.body[i:i + 4]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, params=None, **kw):
        self.calls.append((url, dict(params or {}), kw))
        return self.responses.pop(0)


class DriveApiTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)

    def test_metadata_asks_only_for_what_is_recorded(self):
        session = FakeSession([FakeResponse(payload={'id': 'F1', 'name': 'a.pdf'})])
        self.assertEqual('a.pdf', gdrive.DriveApi(session).metadata('F1')['name'])
        url, params, kw = session.calls[0]
        self.assertEqual(f'{gdrive.API}/F1', url)
        self.assertEqual(gdrive.FIELDS, params['fields'])
        self.assertIn('timeout', kw)

    def test_children_follow_every_page(self):
        session = FakeSession([FakeResponse(payload={'files': [{'id': '1'}], 'nextPageToken': 't2'}),
                               FakeResponse(payload={'files': [{'id': '2'}]})])
        self.assertEqual(['1', '2'], [f['id'] for f in gdrive.DriveApi(session).children('D1')])
        self.assertEqual("'D1' in parents and trashed=false", session.calls[0][1]['q'])
        self.assertEqual('t2', session.calls[1][1]['pageToken'])

    def test_download_checks_the_md5_drive_gives(self):
        body = b'drive file bytes'
        target = self.tmp / 'a.bin'
        good = hashlib.md5(body).hexdigest()
        gdrive.DriveApi(FakeSession([FakeResponse(body=body)])).download('F1', str(target), md5=good)
        self.assertEqual(body, target.read_bytes())
        other = self.tmp / 'b.bin'
        with self.assertRaisesRegex(RuntimeError, 'does not match the MD5'):
            gdrive.DriveApi(FakeSession([FakeResponse(body=body)])).download('F1', str(other), md5='0' * 32)
        self.assertEqual(['a.bin'], sorted(p.name for p in self.tmp.iterdir()))  # no partial file left

    def test_refusals_are_plain(self):
        refused = FakeResponse(status=404, payload={'error': {'message': 'File not found: F1.'}}, reason='Not Found')
        with self.assertRaises(gdrive.DriveRefused) as ctx:
            gdrive.DriveApi(FakeSession([refused])).metadata('F1')
        self.assertTrue(str(ctx.exception).startswith(gdrive.NO_ACCESS_MESSAGE))
        self.assertEqual(errors.PRIVATE, errors.classify(ctx.exception).kind)


class CredentialsTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / 'credentials.json'

    def test_google_auth_format_round_trip(self):
        creds = gdrive.Credentials(token='at', refresh_token='rt', token_uri=gdrive.TOKEN_URI, client_id='cid',
                                   client_secret='cs', scopes=gdrive.SCOPES)
        gdrive.save_credentials(creds, self.path)
        loaded = gdrive.load_credentials(self.path)
        self.assertEqual(('rt', 'cid', 'cs'), (loaded.refresh_token, loaded.client_id, loaded.client_secret))

    def test_reads_what_older_versions_saved(self):
        # PyDrive2 wrote oauth2client's format
        self.path.write_text(json.dumps({'_class': 'OAuth2Credentials', 'access_token': 'at', 'refresh_token': 'rt',
                                         'client_id': 'cid', 'client_secret': 'cs',
                                         'token_uri': 'https://oauth2.googleapis.com/token'}), encoding='utf-8')
        loaded = gdrive.load_credentials(self.path)
        self.assertEqual(('at', 'rt'), (loaded.token, loaded.refresh_token))

    def test_without_a_refresh_token(self):
        self.path.write_text('{}', encoding='utf-8')
        with self.assertRaises(ValueError):
            gdrive.load_credentials(self.path)


class ConnectTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        patcher = mock.patch.object(session_store, 'SESSION_ROOT', self.tmp / 'sessions')
        patcher.start()
        self.addCleanup(patcher.stop)
        legacy = mock.patch.object(gdrive, 'LEGACY_CLIENT_SECRETS', self.tmp / 'none.json')
        legacy.start()
        self.addCleanup(legacy.stop)
        self.handler = gdrive.GoogleDriveHandler()
        gdrive.client_secrets_path().write_text(
            json.dumps({'installed': {'client_id': 'cid', 'client_secret': 'cs'}}), encoding='utf-8')

    def test_consent_in_the_browser_read_only_with_a_time_limit(self):
        creds = gdrive.Credentials(token='at', refresh_token='rt', token_uri=gdrive.TOKEN_URI, client_id='cid',
                                   client_secret='cs', scopes=gdrive.SCOPES)
        flow = mock.Mock()
        flow.from_client_secrets_file.return_value.run_local_server.return_value = creds
        with mock.patch.object(gdrive, 'InstalledAppFlow', flow):
            api = self.handler._get_drive_client(interactive=True)
        self.assertIsInstance(api, gdrive.DriveApi)
        self.assertEqual(['https://www.googleapis.com/auth/drive.readonly'],
                         flow.from_client_secrets_file.call_args.args[1])
        kwargs = flow.from_client_secrets_file.return_value.run_local_server.call_args.kwargs
        self.assertEqual((0, True, 600), (kwargs['port'], kwargs['open_browser'], kwargs['timeout_seconds']))
        self.assertEqual('rt', gdrive.load_credentials(gdrive.credentials_path()).refresh_token)

    def test_saved_connection_is_used_without_the_browser(self):
        creds = mock.Mock(valid=True)
        flow = mock.Mock()
        with mock.patch.object(gdrive, 'load_credentials', return_value=creds), \
                mock.patch.object(gdrive, 'InstalledAppFlow', flow):
            gdrive.credentials_path().write_text('{}', encoding='utf-8')
            self.assertIsInstance(self.handler._get_drive_client(), gdrive.DriveApi)
        creds.refresh.assert_not_called()
        flow.from_client_secrets_file.assert_not_called()

    def test_expired_token_is_refreshed_and_saved(self):
        creds = mock.Mock(valid=False)
        creds.to_json.return_value = json.dumps({'refresh_token': 'rt2', 'client_id': 'cid'})
        with mock.patch.object(gdrive, 'load_credentials', return_value=creds), \
                mock.patch.object(gdrive, 'Request', mock.Mock()):
            gdrive.credentials_path().write_text('{}', encoding='utf-8')
            self.handler._get_drive_client()
        creds.refresh.assert_called_once()
        self.assertIn('rt2', gdrive.credentials_path().read_text(encoding='utf-8'))

    def test_status_follows_the_connection(self):
        signins.save_oauth_signin(DRIVE)
        self.assertEqual('expired', signins.status(DRIVE).state)  # no credentials file yet
        gdrive.credentials_path().write_text('{}', encoding='utf-8')
        self.assertEqual('ready', signins.status(DRIVE).state)


class NoPyDrive2Tests(unittest.TestCase):
    def test_gone_from_the_project(self):
        root = Path(__file__).resolve().parents[1]
        for name in ('pyproject.toml', 'requirements.txt', 'requirements-lock.txt', 'scripts/build_exe.py',
                     'scripts/third_party_licenses.py', 'multidownloader/sources/gdrive.py'):
            with self.subTest(file=name):
                self.assertNotIn('pydrive2', (root / name).read_text(encoding='utf-8').lower())


if __name__ == '__main__':
    unittest.main()
