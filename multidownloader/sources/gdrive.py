"""Google Drive handler (public via gdown, authenticated via PyDrive2)."""
import os
import logging

try:
    import gdown
    GDOWN_AVAILABLE = True
except Exception:
    GDOWN_AVAILABLE = False

try:
    from pydrive2.auth import GoogleAuth
    from pydrive2.drive import GoogleDrive
    PYDRIVE2_AVAILABLE = True
except Exception:
    PYDRIVE2_AVAILABLE = False


def parse_drive_id(url):
    import re
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


class GoogleDriveHandler:
    def __init__(self, logger=None):
        self.logger = logger or logging.getLogger(__name__)

    def download(self, url, out_dir, options):
        if not url or not isinstance(url, str):
            raise ValueError('A valid Google Drive URL must be provided')
        os.makedirs(out_dir, exist_ok=True)
        fid, typ = parse_drive_id(url)
        if not fid:
            raise ValueError('Invalid Google Drive URL')

        if typ == 'file':
            if options.get('method', 'public').startswith('public'):
                if not GDOWN_AVAILABLE:
                    raise RuntimeError('gdown not available')
                self.logger.info(f'Downloading public drive file: {url} into {out_dir}')
                # gdown.download may accept either the share URL or the id-based URL
                try:
                    gdown.download(url, output=os.path.join(out_dir, ''), quiet=False)
                    return True
                except Exception as ex:
                    raise RuntimeError(f'gdown failed: {ex}')
            else:
                if not PYDRIVE2_AVAILABLE:
                    raise RuntimeError('PyDrive2 not available')
                # Authenticated download: run a local webserver auth flow once
                self.logger.info('Starting PyDrive2 authenticated download')
                gauth = GoogleAuth(); gauth.LocalWebserverAuth()
                drive = GoogleDrive(gauth)
                f = drive.CreateFile({'id': fid})
                f.FetchMetadata()
                name = f.get('title') or f.get('originalFilename') or fid
                # attempt download with a retry
                attempts = 2
                for attempt in range(1, attempts + 1):
                    try:
                        f.GetContentFile(os.path.join(out_dir, name))
                        self.logger.info(f'Downloaded (authenticated) {name}')
                        return name
                    except Exception as ex:
                        self.logger.warning(f'Attempt {attempt} failed for authenticated file {fid}: {ex}')
                        if attempt == attempts:
                            raise RuntimeError(f'Authenticated download failed: {ex}')
                        continue
        else:
            if not GDOWN_AVAILABLE:
                raise RuntimeError('gdown not available for folders')
            self.logger.info(f'Downloading Google Drive folder: {fid} into {out_dir}')
            try:
                gdown.download_folder(f'https://drive.google.com/drive/folders/{fid}', output=out_dir, quiet=False)
                return True
            except Exception as ex:
                raise RuntimeError(f'Failed to download folder: {ex}')
