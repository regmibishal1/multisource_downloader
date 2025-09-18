"""Google Drive handler (public via gdown, authenticated via PyDrive2)."""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Optional

try:
    import gdown
    GDOWN_AVAILABLE = True
except Exception:  # pragma: no cover - optional dependency
    gdown = None
    GDOWN_AVAILABLE = False

try:
    from pydrive2.auth import GoogleAuth
    from pydrive2.drive import GoogleDrive
    PYDRIVE2_AVAILABLE = True
except Exception:  # pragma: no cover - optional dependency
    GoogleAuth = None
    GoogleDrive = None
    PYDRIVE2_AVAILABLE = False

from .. import session_store

SESSION_NAMESPACE = 'GoogleDrive'
CREDENTIAL_FILENAME = 'credentials.json'
CLIENT_SECRET_FILENAME = 'client_secrets.json'
PROJECT_ROOT = Path(__file__).resolve().parents[2]
CLIENT_SECRET_CANDIDATES = (
    session_store.path_for(SESSION_NAMESPACE, CLIENT_SECRET_FILENAME),
    PROJECT_ROOT / CLIENT_SECRET_FILENAME,
)


def parse_drive_id(url):
    m = re.search(r"(?:/d/|id=)([\w-]+)", url)
    if m:
        return m.group(1), 'file'
    m = re.search(r"(?:folders/|folderview\?id=)([\w-]+)", url)
    if m:
        return m.group(1), 'folder'
    m = re.search(r"uc\?id=([\w-]+)", url)
    if m:
        return m.group(1), 'file'
    return None, None


def _find_client_secrets() -> Optional[Path]:
    for candidate in CLIENT_SECRET_CANDIDATES:
        if candidate.exists():
            return candidate
    return None


class GoogleDriveHandler:
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

        if typ == 'file':
            if options.get('method', 'public').startswith('public'):
                return self._download_public_file(url, out_dir)
            return self._download_authenticated_file(fid, out_dir)

        return self._download_folder(fid, out_dir)

    # ------------------------------------------------------------------
    def _download_public_file(self, url: str, out_dir: str):
        if not GDOWN_AVAILABLE:
            raise RuntimeError('gdown not available')
        self.logger.info('Downloading public drive file: %s -> %s', url, out_dir)
        try:
            gdown.download(url, output=os.path.join(out_dir, ''), quiet=False)
            return True
        except Exception as exc:  # pragma: no cover - network call
            raise RuntimeError(f'gdown failed: {exc}')

    def _download_authenticated_file(self, file_id: str, out_dir: str):
        if not PYDRIVE2_AVAILABLE:
            raise RuntimeError('PyDrive2 not available')
        drive = self._get_drive_client()
        file_obj = drive.CreateFile({'id': file_id})
        file_obj.FetchMetadata()
        name = file_obj.get('title') or file_obj.get('originalFilename') or file_id
        target_path = os.path.join(out_dir, name)
        attempts = 2
        for attempt in range(1, attempts + 1):
            try:
                file_obj.GetContentFile(target_path)
                self.logger.info('Downloaded (authenticated) %s', target_path)
                return target_path
            except Exception as exc:  # pragma: no cover - network call
                self.logger.warning('Attempt %s failed for %s: %s', attempt, file_id, exc)
                if attempt == attempts:
                    raise RuntimeError(f'Authenticated download failed: {exc}')
        return None

    def _download_folder(self, folder_id: str, out_dir: str):
        if not GDOWN_AVAILABLE:
            raise RuntimeError('gdown not available for folders')
        url = f'https://drive.google.com/drive/folders/{folder_id}'
        self.logger.info('Downloading Google Drive folder: %s -> %s', folder_id, out_dir)
        try:
            gdown.download_folder(url, output=out_dir, quiet=False)
            return True
        except Exception as exc:  # pragma: no cover - network call
            raise RuntimeError(f'Failed to download folder: {exc}')

    def _get_drive_client(self):
        if not PYDRIVE2_AVAILABLE:
            raise RuntimeError('PyDrive2 not available')

        client_secrets = _find_client_secrets()
        if not client_secrets:
            raise RuntimeError(
                'Google Drive client_secrets.json not found. Place it at one of: '
                f'{CLIENT_SECRET_CANDIDATES[0]} or {CLIENT_SECRET_CANDIDATES[1]}'
            )

        gauth = GoogleAuth()
        try:
            gauth.LoadClientConfigFile(str(client_secrets))
            self.logger.debug('Loaded Google Drive client config from %s', client_secrets)
        except Exception as exc:
            raise RuntimeError(f'Failed to load Google Drive client_secrets.json: {exc}') from exc

        credentials_path = session_store.path_for(SESSION_NAMESPACE, CREDENTIAL_FILENAME)
        if credentials_path.exists():
            try:
                gauth.LoadCredentialsFile(str(credentials_path))
            except Exception as exc:  # pragma: no cover - corrupted file
                self.logger.warning('Failed to load saved Google Drive credentials: %s', exc)

        if getattr(gauth, 'credentials', None):
            try:
                if gauth.access_token_expired:
                    gauth.Refresh()
                    self.logger.info('Refreshed Google Drive token')
                else:
                    gauth.Authorize()
                return GoogleDrive(gauth)
            except Exception as exc:  # pragma: no cover - expired refresh token
                self.logger.warning('Stored Google Drive credentials invalid: %s', exc)

        self.logger.info('Launching Google Drive authentication flow')
        gauth.LocalWebserverAuth()
        try:
            credentials_path.parent.mkdir(parents=True, exist_ok=True)
            gauth.SaveCredentialsFile(str(credentials_path))
            self.logger.info('Saved Google Drive credentials to %s', credentials_path)
        except Exception as exc:  # pragma: no cover - disk issues
            self.logger.warning('Failed to persist Google Drive credentials: %s', exc)
        return GoogleDrive(gauth)


__all__ = ['GoogleDriveHandler', 'parse_drive_id']
