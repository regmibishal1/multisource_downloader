"""Google Drive handler.

Public links download with gdown and need nothing. When Drive refuses a
link, the stored sign-in is used: Google cookies (from Firefox or a cookies
file, passed to gdown) or the user's own Google Cloud OAuth client, through
Google's own libraries (google-auth, google-auth-oauthlib) and the Drive v3
REST API.
"""
from __future__ import annotations

import contextlib
import hashlib
import inspect
import json
import logging
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Dict, Iterator, Optional

try:
    import gdown
    GDOWN_AVAILABLE = True
except Exception:  # pragma: no cover - optional dependency
    gdown = None
    GDOWN_AVAILABLE = False

try:
    from google.auth.exceptions import RefreshError
    from google.auth.transport.requests import AuthorizedSession, Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    OAUTH_AVAILABLE = True
except Exception:  # pragma: no cover - optional dependency
    RefreshError = AuthorizedSession = Request = Credentials = InstalledAppFlow = None
    OAUTH_AVAILABLE = False

from .. import session_store, signins
from ..paths import path_inside
from ..platforms import BY_KEY
from ..signins import SignInError

SESSION_NAMESPACE = 'GoogleDrive'
CREDENTIAL_FILENAME = 'credentials.json'
CLIENT_SECRET_FILENAME = 'client_secrets.json'
# Older versions also looked for client_secrets.json in the project root.
LEGACY_CLIENT_SECRETS = Path(__file__).resolve().parents[2] / CLIENT_SECRET_FILENAME
FOLDER_MIME = 'application/vnd.google-apps.folder'
# read-only: the app only downloads. Connections made by older versions (full Drive scope) keep working.
SCOPES = ['https://www.googleapis.com/auth/drive.readonly']
TOKEN_URI = 'https://oauth2.googleapis.com/token'
API = 'https://www.googleapis.com/drive/v3/files'
FIELDS = 'id,name,mimeType,modifiedTime,createdTime,md5Checksum,size,owners(displayName)'
CONSENT_TIMEOUT_S = 600  # the same 10 minutes as every other sign-in window (brief A15)
CHUNK = 1024 * 1024
OAUTH_MISSING = 'Google Drive sign-in needs the drive extra: pip install ".[drive]"'

PRIVATE_MESSAGE = ("This file is private. Ask the owner to set sharing to 'Anyone with the link', "
                   'or sign in with a Google account that has access.')
NO_ACCESS_MESSAGE = 'Your Google account does not have access to this file. Ask the owner to share it with you.'
OAUTH_EXPIRED_MESSAGE = ('Your Google connection expired. This happens every 7 days while your Google Cloud '
                         'project is in Testing status. Reconnect it in Sign-ins.')
NOT_CONNECTED_MESSAGE = 'Connect Google Drive in Sign-ins first.'
GDOWN6_NEEDED = 'Google Drive sign-in needs gdown 6 or newer: pip install -U gdown'

_PRIVATE_HINTS = ('permission', 'cannot retrieve the public link', 'access denied', 'sign in', '401', '403')


def parse_drive_id(url):
    """Return (id, kind) where kind is 'file', 'folder' or 'unknown'.

    Folder patterns are checked first: a generic "id=" match would otherwise
    claim folderview?id= links. open?id= links can point at either, so they
    come back as 'unknown'.
    """
    m = re.search(r"(?:/folders/|folderview\?id=)([\w-]+)", url)
    if m:
        return m.group(1), 'folder'
    m = re.search(r"/d/([\w-]+)", url)
    if m:
        return m.group(1), 'file'
    m = re.search(r"(?:uc|download)\?(?:[^#]*&)?id=([\w-]+)", url)
    if m:
        return m.group(1), 'file'
    m = re.search(r"[?&]id=([\w-]+)", url)
    if m:
        return m.group(1), 'unknown'
    return None, None


def _drive_id(url: str) -> Optional[str]:
    return parse_drive_id(url)[0]


def _write_drive_json(media_path: str, details: dict) -> None:
    """Write <file>.drive.json with what Drive said about the file (brief F13)."""
    path = media_path + '.drive.json'
    with open(path, 'w', encoding='utf-8') as fp:
        json.dump({k: v for k, v in details.items() if v not in (None, [])}, fp, ensure_ascii=False, indent=2)


def _looks_private(message: str) -> bool:
    message = (message or '').lower()
    return any(hint in message for hint in _PRIVATE_HINTS)


def _supports(func, name: str) -> bool:
    try:
        return name in inspect.signature(func).parameters
    except (TypeError, ValueError):
        return False


def client_secrets_path() -> Path:
    return session_store.path_for(SESSION_NAMESPACE, CLIENT_SECRET_FILENAME)


def credentials_path() -> Path:
    return session_store.path_for(SESSION_NAMESPACE, CREDENTIAL_FILENAME)


def _find_client_secrets() -> Optional[Path]:
    path = client_secrets_path()
    if path.exists():
        return path
    if LEGACY_CLIENT_SECRETS.exists():
        # copy (never move) the old location into the credentials folder
        shutil.copy2(LEGACY_CLIENT_SECRETS, path)
        logging.getLogger(__name__).info('Copied %s to %s', LEGACY_CLIENT_SECRETS, path)
        return path
    return None


def oauth_connected() -> bool:
    return signins.read_meta(BY_KEY['gdrive']).get('method') == 'oauth' and credentials_path().exists()


def validate_client_secrets(text: str) -> None:
    """Check a downloaded OAuth client file. Raises SignInError with a message to show."""
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise SignInError('This is not the JSON file Google Cloud downloads for an OAuth client.') from exc
    if 'web' in data:
        raise SignInError('This client is a Web application. Create a Desktop app OAuth client instead.')
    installed = data.get('installed') if isinstance(data, dict) else None
    if not isinstance(installed, dict) or not installed.get('client_id') or not installed.get('client_secret'):
        raise SignInError('This file has no Desktop app OAuth client in it. Download the JSON for a Desktop '
                          'app client from Google Cloud, Credentials.')


GDOWN_MISSING = 'Google Drive downloads need gdown. Install it with: python -m pip install ".[drive]"'


class GoogleDriveHandler:
    """Google Drive files and folders (a backends.base.Backend)."""

    name = 'gdown'

    def available(self):
        # gdown covers public links; the Google libraries are only needed for the OAuth route
        return (True, '') if GDOWN_AVAILABLE or OAUTH_AVAILABLE else (False, GDOWN_MISSING)

    def __init__(self, logger=None):
        self.logger = logger or logging.getLogger(__name__)
        session_store.ensure_session_dir(SESSION_NAMESPACE)

    def download(self, url, out_dir, options):
        if not url or not isinstance(url, str):
            raise ValueError('A valid Google Drive URL must be provided')
        os.makedirs(out_dir, exist_ok=True)
        fid, typ = parse_drive_id(url)
        if not fid:
            raise ValueError('Invalid Google Drive URL')

        # 'public' or 'authenticated' force one path (older callers); the
        # default tries public first and signs in only when Drive refuses
        method = options.get('method')
        if method and method.startswith('public'):
            method = 'public'
        self._flat = options.get('layout') == 'flat'

        if typ == 'file':
            return self._file(url, fid, out_dir, method)
        if typ == 'unknown':
            # open?id= does not say whether it is a file or a folder: try the
            # file path first, then the folder path.
            try:
                return self._file(url, fid, out_dir, method)
            except Exception as file_exc:
                self.logger.info('Not a downloadable file, trying as a folder: %s (%s)', fid, file_exc)
                try:
                    return self._folder(fid, out_dir, method)
                except Exception as folder_exc:
                    raise RuntimeError(
                        f'Could not download {url} as a file ({file_exc}) or a folder ({folder_exc})'
                    ) from folder_exc
        return self._folder(fid, out_dir, method)

    # --- choosing a path ---------------------------------------------------
    def _file(self, url, fid, out_dir, method):
        # gdrive/<file id>/<original name>: Drive names are kept (they are often
        # the only description of a document), under the id so they cannot clash
        if not getattr(self, '_flat', False):  # gdrive/<id>/<name>, or the file itself for "flat"
            out_dir = os.path.join(out_dir, 'gdrive', fid)
        os.makedirs(out_dir, exist_ok=True)
        if method == 'authenticated':
            return self._download_authenticated_file(fid, out_dir)
        try:
            with self._cookie_copy(method) as cookies:
                return self._download_public_file(url, out_dir, cookies)
        except RuntimeError as exc:
            return self._after_refusal(exc, method, lambda: self._download_authenticated_file(fid, out_dir))

    def _folder(self, fid, out_dir, method):
        if not getattr(self, '_flat', False):  # gdrive/<id>/<name>, or the file itself for "flat"
            out_dir = os.path.join(out_dir, 'gdrive', fid)
        os.makedirs(out_dir, exist_ok=True)
        if method == 'authenticated':
            return self._download_authenticated_folder(fid, out_dir)
        try:
            with self._cookie_copy(method) as cookies:
                return self._download_folder(fid, out_dir, cookies)
        except RuntimeError as exc:
            return self._after_refusal(exc, method, lambda: self._download_authenticated_folder(fid, out_dir))

    def _after_refusal(self, exc, method, with_oauth):
        if method == 'public' or not _looks_private(str(exc)):
            raise exc
        if oauth_connected():
            self.logger.info('Drive refused the public download; using the Google Cloud connection')
            return with_oauth()
        if signins.cookie_file_for_download('Google Drive'):
            raise RuntimeError(NO_ACCESS_MESSAGE) from exc
        raise RuntimeError(PRIVATE_MESSAGE) from exc

    @contextlib.contextmanager
    def _cookie_copy(self, method):
        """Yield a throwaway copy of the Google cookies, or None without a cookie sign-in.

        gdown writes cookies back to the file it is given; a copy keeps the
        stored sign-in limited to what the user imported.
        """
        path = None if method == 'public' else signins.cookie_file_for_download('Google Drive')
        if not path:
            yield None
            return
        fd, tmp = tempfile.mkstemp(prefix='.gdown-', suffix='.txt', dir=path.parent)
        os.close(fd)
        try:
            shutil.copyfile(path, tmp)
            yield tmp
        finally:
            with contextlib.suppress(OSError):
                os.unlink(tmp)

    # --- gdown ---------------------------------------------------------------
    def _gdown_kwargs(self, func, cookies_file):
        kwargs = {'quiet': False}
        if _supports(func, 'fuzzy'):
            # gdown 5 needs fuzzy to read /file/d/<id>/view share links;
            # gdown 6 reads them by itself and dropped the argument
            kwargs['fuzzy'] = True
        if cookies_file:
            if not _supports(func, 'cookies_file'):
                raise RuntimeError(GDOWN6_NEEDED)
            kwargs['cookies_file'] = cookies_file
        return kwargs

    def _download_public_file(self, url: str, out_dir: str, cookies_file: Optional[str] = None):
        if not GDOWN_AVAILABLE:
            raise RuntimeError('gdown not available')
        kwargs = self._gdown_kwargs(gdown.download, cookies_file)
        self.logger.info('Downloading Drive file%s: %s -> %s', ' (signed in)' if cookies_file else '', url, out_dir)
        try:
            result = gdown.download(url, output=os.path.join(out_dir, ''), **kwargs)
        except Exception as exc:  # pragma: no cover - network call
            raise RuntimeError(f'gdown failed: {exc}') from exc
        if not result:
            raise RuntimeError('gdown did not download a file')
        if isinstance(result, str) and os.path.isfile(result):
            _write_drive_json(result, {'id': _drive_id(url), 'name': os.path.basename(result), 'source_url': url,
                                       'downloaded_with': 'gdown'})
        return True

    def _download_folder(self, folder_id: str, out_dir: str, cookies_file: Optional[str] = None):
        if not GDOWN_AVAILABLE:
            raise RuntimeError('gdown not available for folders')
        kwargs = self._gdown_kwargs(gdown.download_folder, cookies_file)
        kwargs.pop('fuzzy', None)
        url = f'https://drive.google.com/drive/folders/{folder_id}'
        self.logger.info('Downloading Google Drive folder: %s -> %s', folder_id, out_dir)
        try:
            result = gdown.download_folder(url, output=out_dir, **kwargs)
        except Exception as exc:  # pragma: no cover - network call
            raise RuntimeError(f'Failed to download folder: {exc}') from exc
        if result is None:
            raise RuntimeError('gdown did not download the folder')
        return True

    # --- the user's own Google Cloud OAuth client, through the Drive v3 API ----------
    def _download_authenticated_file(self, file_id: str, out_dir: str):
        drive = self._get_drive_client()
        meta = drive.metadata(file_id)
        if meta.get('mimeType') == FOLDER_MIME:
            return self._download_authenticated_folder(file_id, out_dir, drive=drive)
        return self._save_drive_file(drive, meta, out_dir)

    def _save_drive_file(self, drive, meta, out_dir):
        file_id = meta.get('id') or 'file'
        name = meta.get('name') or file_id
        # Drive names can contain "/" or "../"; never trust them as a path.
        target_path = str(path_inside(out_dir, name, fallback=file_id))
        details = {
            'id': file_id, 'name': name, 'mime_type': meta.get('mimeType'),
            'modified': meta.get('modifiedTime'), 'created': meta.get('createdTime'),
            'owners': [o.get('displayName') for o in (meta.get('owners') or []) if o.get('displayName')],
            'md5': meta.get('md5Checksum'), 'downloaded_with': 'google-auth (Drive API v3)',
        }
        attempts = 2
        for attempt in range(1, attempts + 1):
            try:
                drive.download(file_id, target_path, md5=meta.get('md5Checksum'))
                self.logger.info('Downloaded (authenticated) %s', target_path)
                _write_drive_json(target_path, details)
                return target_path
            except DriveRefused:
                raise
            except Exception as exc:  # pragma: no cover - network call
                self.logger.warning('Attempt %s failed for %s: %s', attempt, file_id, exc)
                if attempt == attempts:
                    raise RuntimeError(f'Authenticated download failed: {exc}') from exc
        return None

    def _download_authenticated_folder(self, folder_id: str, out_dir: str, drive=None):
        drive = drive or self._get_drive_client()
        saved = 0
        for item in drive.children(folder_id):
            mime = item.get('mimeType') or ''
            if mime == FOLDER_MIME:
                sub = path_inside(out_dir, item.get('name') or item['id'], fallback=item['id'])
                os.makedirs(sub, exist_ok=True)
                saved += self._download_authenticated_folder(item['id'], str(sub), drive=drive)
            elif mime.startswith('application/vnd.google-apps.'):
                # Docs, Sheets and Slides have no file to download as-is
                self.logger.warning('Skipping Google %s "%s": export it from Drive instead',
                                    mime.rsplit('.', 1)[-1], item.get('name'))
            else:
                self._save_drive_file(drive, item, out_dir)
                saved += 1
        return saved

    def _get_drive_client(self, interactive: bool = False):
        """Return a DriveApi from the saved OAuth connection.

        interactive=True (the Sign-ins wizard only) opens the system browser
        when there is no connection; a download never does.
        """
        if not OAUTH_AVAILABLE:
            raise RuntimeError(OAUTH_MISSING)
        client_secrets = _find_client_secrets()
        if not client_secrets:
            raise RuntimeError(NOT_CONNECTED_MESSAGE)

        creds_file = credentials_path()
        creds = None
        if creds_file.exists():
            try:
                creds = load_credentials(creds_file)
            except Exception as exc:  # a damaged file: connect again
                self.logger.warning('Could not read the saved Google Drive connection: %s', exc)

        if creds is not None:
            try:
                if not creds.valid:
                    creds.refresh(Request())
                    save_credentials(creds, creds_file)
                    self.logger.info('Refreshed Google Drive token')
                return DriveApi(AuthorizedSession(creds))
            except Exception as exc:
                if 'invalid_grant' in str(exc):
                    signins.mark_rejected(BY_KEY['gdrive'])
                    raise RuntimeError(OAUTH_EXPIRED_MESSAGE) from exc
                if not interactive:
                    raise RuntimeError(f'Google Drive connection failed: {exc}') from exc

        if not interactive:
            raise RuntimeError(NOT_CONNECTED_MESSAGE)
        self.logger.info('Opening the browser for Google Drive consent')
        flow = InstalledAppFlow.from_client_secrets_file(str(client_secrets), SCOPES)
        # Google's consent page in the user's own browser, as Google requires; a local
        # address on this computer receives the answer
        creds = flow.run_local_server(port=0, open_browser=True, timeout_seconds=CONSENT_TIMEOUT_S,
                                      success_message='Google Drive is connected. You can close this tab.')
        save_credentials(creds, creds_file)
        return DriveApi(AuthorizedSession(creds))

    def connect_oauth(self, client_file) -> None:
        """Wizard step: store the user's OAuth client and run Google's consent in the browser."""
        text = Path(client_file).read_text(encoding='utf-8', errors='replace')
        validate_client_secrets(text)
        session_store._atomic_write(client_secrets_path(), text.encode('utf-8'))
        if credentials_path().exists():
            credentials_path().unlink()  # connect fresh, possibly as another account
        try:
            self._get_drive_client(interactive=True)
        except Exception as exc:
            raise SignInError(f'Google sign-in did not finish: {exc}') from exc
        signins.save_oauth_signin(BY_KEY['gdrive'])


class DriveRefused(RuntimeError):
    """Drive answered with an error status; the message says why in plain words."""


class DriveApi:
    """The few Drive v3 REST calls the app needs, over an authorized requests session."""

    def __init__(self, session):
        self.session = session

    def _check(self, response):
        if response.status_code < 400:
            return response
        try:
            reason = response.json().get('error', {}).get('message') or response.reason
        except ValueError:
            reason = response.reason
        if response.status_code in (403, 404):
            raise DriveRefused(f'{NO_ACCESS_MESSAGE} (Drive said: {reason})')
        if response.status_code == 401:
            raise DriveRefused(f'Google Drive did not accept the connection (401): {reason}')
        raise DriveRefused(f'Google Drive error {response.status_code}: {reason}')

    def metadata(self, file_id: str) -> Dict:
        response = self.session.get(f'{API}/{file_id}', params={'fields': FIELDS, 'supportsAllDrives': 'true'},
                                    timeout=60)
        return self._check(response).json()

    def children(self, folder_id: str) -> Iterator[Dict]:
        params = {'q': f"'{folder_id}' in parents and trashed=false", 'fields': f'nextPageToken,files({FIELDS})',
                  'pageSize': 1000, 'supportsAllDrives': 'true', 'includeItemsFromAllDrives': 'true'}
        while True:
            data = self._check(self.session.get(API, params=params, timeout=60)).json()
            yield from data.get('files') or []
            token = data.get('nextPageToken')
            if not token:
                return
            params = dict(params, pageToken=token)

    def download(self, file_id: str, target: str, md5: Optional[str] = None) -> None:
        """Stream the file to target (via a .partial name) and check Drive's MD5 when it gives one."""
        partial = target + '.partial'
        digest = hashlib.md5()  # noqa: S324 (Drive publishes MD5; it is a transfer check here, not security)
        try:
            with self.session.get(f'{API}/{file_id}', params={'alt': 'media', 'supportsAllDrives': 'true'},
                                  stream=True, timeout=60) as response:
                self._check(response)
                with open(partial, 'wb') as fp:
                    for chunk in response.iter_content(CHUNK):
                        fp.write(chunk)
                        digest.update(chunk)
            if md5 and digest.hexdigest() != md5.lower():
                raise RuntimeError('the downloaded file does not match the MD5 Google Drive gave for it')
            os.replace(partial, target)
        finally:
            with contextlib.suppress(OSError):
                os.unlink(partial)


def load_credentials(path: Path):
    """The saved connection: google-auth's format, or oauth2client's, which older versions wrote."""
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    if not data.get('refresh_token') or not data.get('client_id'):
        raise ValueError('no refresh token in the saved connection')
    return Credentials(
        token=data.get('token') or data.get('access_token'),
        refresh_token=data['refresh_token'],
        token_uri=data.get('token_uri') or TOKEN_URI,
        client_id=data['client_id'],
        client_secret=data.get('client_secret'),
        scopes=data.get('scopes') or None,
    )


def save_credentials(creds, path: Path) -> None:
    session_store._atomic_write(Path(path), creds.to_json().encode('utf-8'))  # readable only by this user


__all__ = ['DriveApi', 'DriveRefused', 'GoogleDriveHandler', 'load_credentials', 'oauth_connected',
           'parse_drive_id', 'save_credentials', 'validate_client_secrets', 'PRIVATE_MESSAGE',
           'OAUTH_EXPIRED_MESSAGE']
