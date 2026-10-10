"""Shared yt-dlp handler utilities."""
from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from typing import Dict, Optional

try:  # modern yt-dlp versions keep DownloadError in utils but may drop helpers
    import yt_dlp
    from yt_dlp.utils import DownloadError
    YTDLP_AVAILABLE = True
except Exception:  # pragma: no cover - module might be missing in minimal envs
    yt_dlp = None
    DownloadError = Exception
    YTDLP_AVAILABLE = False

from .. import session_store, signins
from ..platforms import BY_HANDLER


# Output layout (brief T10): <platform>/<account>/<YYYY-MM-DD>_<post id>[_<n>].<ext>
# The date is the post's date, or the download date when the site gives none;
# n numbers the items of a multi-item post. Titles stay out of file names (they
# are long, often non-ASCII and the main cause of Windows path-length
# failures) unless asked for, and then cut to 60 characters. yt-dlp names the
# .info.json and thumbnail after the media, so they sit next to it.
ACCOUNT_FIELD = '%(uploader,channel,uploader_id,creator|unknown)s'
DATE_FIELD = '%(upload_date>%Y-%m-%d,epoch>%Y-%m-%d)s'
TITLE_LENGTH = 60


def layout_template(platform_key: str, include_title: bool = False) -> str:
    name = f'{DATE_FIELD}_%(id)s%(playlist_index&_{{}}|)s'
    if include_title:
        name += f'_%(title).{TITLE_LENGTH}s'
    return f'{platform_key}/{ACCOUNT_FIELD}/{name}.%(ext)s'

NO_FFMPEG_MESSAGE = 'ffmpeg is not installed, so some videos are saved as separate video and audio files.'
_warned = []


def ffmpeg_available(location=None) -> bool:
    if location:
        return os.path.exists(location)
    return shutil.which('ffmpeg') is not None


def _warn_no_ffmpeg(logger) -> None:
    if not _warned:
        _warned.append(True)
        logger.warning(NO_FFMPEG_MESSAGE)


def scrub_info_json(folder) -> int:
    """Remove cookies from yt-dlp .info.json files under folder; returns files changed.

    yt-dlp can attach the cookies it sent to each format ("cookies", or a
    Cookie header). Info files are kept with the media and may be shared, so
    sign-in cookies must never end up in them.
    """
    import json

    changed = 0

    def clean(value):
        if isinstance(value, dict):
            removed = False
            for key in [k for k in value if k.lower() == 'cookies']:
                del value[key]
                removed = True
            headers = value.get('http_headers')
            if isinstance(headers, dict):
                for key in [k for k in headers if k.lower() == 'cookie']:
                    del headers[key]
                    removed = True
            return any([removed] + [clean(v) for v in value.values()])
        if isinstance(value, list):
            return any([clean(v) for v in value])
        return False

    for path in Path(folder).rglob('*.info.json'):
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        if clean(data):
            path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
            changed += 1
    return changed


def progress_hook(cancel_event=None, progress=None):
    """A yt-dlp progress hook that reports bytes and speed and stops on cancel_event.

    progress(downloaded, total, speed) is called while downloading. When
    cancel_event is set, the hook raises yt-dlp's DownloadCancelled, which
    stops the download at its next update.
    """
    if cancel_event is None and progress is None:
        return None

    def hook(d):
        if cancel_event is not None and cancel_event.is_set():
            from yt_dlp.utils import DownloadCancelled
            raise DownloadCancelled('Stopped')
        if progress and d.get('status') == 'downloading':
            progress(d.get('downloaded_bytes') or 0, d.get('total_bytes') or d.get('total_bytes_estimate'),
                     d.get('speed'))

    return hook


YTDLP_MISSING = 'yt-dlp is not installed. Install it with: python -m pip install yt-dlp'


class YtDlpHandler:
    """Base handler that wraps yt_dlp with cookie/session persistence support (a backends.base.Backend)."""

    name = 'yt-dlp'

    def available(self):
        return (True, '') if YTDLP_AVAILABLE else (False, YTDLP_MISSING)

    source_key: str = 'generic'
    logger: logging.Logger

    def __init__(self, source_key: str, *, logger: Optional[logging.Logger] = None, outtmpl: Optional[str] = None):
        self.source_key = source_key
        self.logger = logger or logging.getLogger(f'multidownloader.{source_key.lower()}')
        # None: the shared layout for this platform (layout_template)
        self.outtmpl = outtmpl
        platform = BY_HANDLER.get(source_key)
        self.platform_key = platform.key if platform else source_key.lower()
        session_store.ensure_session_dir(self.source_key)

    # --- hooks for subclasses -------------------------------------------------
    def extra_yt_opts(self, options: Dict) -> Dict:
        """Subclasses can override to provide additional yt-dlp options."""
        return {}

    # --- public API ----------------------------------------------------------
    def download(self, url: str, out_dir: str, options: Optional[Dict] = None):
        if not YTDLP_AVAILABLE:
            raise RuntimeError(YTDLP_MISSING)

        options = options or {}
        os.makedirs(out_dir, exist_ok=True)

        cookie_path = self._resolve_cookie_path(options)
        ydl_opts = self._build_opts(out_dir, options, cookie_path)

        self.logger.info('yt-dlp download start: source=%s url=%s', self.source_key, url)

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])
                self.logger.info('yt-dlp download complete: %s', url)
                if cookie_path and signins.writes_back(self.source_key):
                    try:
                        if signins.save_refreshed(self.source_key, ydl.cookiejar):
                            self.logger.debug('Updated cookies saved to %s', cookie_path)
                    except Exception as exc:  # pragma: no cover - best effort
                        self.logger.warning('Failed to persist cookies for %s: %s', self.source_key, exc)
        except DownloadError as exc:
            raise RuntimeError(f'yt-dlp failed for {self.source_key}: {exc}') from exc
        finally:
            scrub_info_json(out_dir)

        return True

    # --- internal helpers ----------------------------------------------------
    def _build_opts(self, out_dir: str, options: Dict, cookie_path: Optional[Path]) -> Dict:
        template = self.outtmpl or layout_template(self.platform_key, bool(options.get('include_title')))
        base_opts: Dict = {
            # The folder goes in paths, not in the template: trim_file_name
            # shortens the evaluated template, and with the folder inside it a
            # long folder path was cut short and files landed outside out_dir.
            'paths': {'home': str(out_dir), 'temp': str(out_dir)},
            'outtmpl': template,
            # names that are valid on Windows, FAT and exFAT drives too
            'windowsfilenames': True,
            'trim_file_name': 120,
            'noplaylist': True,
            'quiet': True,
            'no_warnings': True,
            'retries': 10,
            'fragment_retries': 10,
            'continuedl': True,
            'socket_timeout': 30,
            'concurrent_fragment_downloads': 2,
        }
        hook = progress_hook(options.get('cancel_event'), options.get('progress'))
        if hook:
            base_opts['progress_hooks'] = [hook]
            if options.get('progress'):
                base_opts['noprogress'] = True  # the app shows progress itself
        if options.get('write_metadata', True):
            # captions, upload time, uploader and counts are often the evidence;
            # thumbnails are written beside the media, never embedded in it
            base_opts['writeinfojson'] = True
            base_opts['writethumbnail'] = True

        if cookie_path:
            base_opts['cookiefile'] = str(cookie_path)
            # some sites tie a session to the browser it was made in, so reuse
            # the User-Agent of the user's own sign-in window when recorded
            user_agent = signins.user_agent_for(self.source_key)
            if user_agent:
                base_opts['http_headers'] = {'User-Agent': user_agent}

        custom = options.get('ytdlp_opts') or {}
        if 'format' not in custom and not ffmpeg_available(custom.get('ffmpeg_location')):
            # yt-dlp's default merges the best video and best audio, which needs
            # ffmpeg. Without it, take a single file with both when the site has
            # one, else save the video and audio streams as two files (unaltered,
            # with the format id in the name so neither overwrites the other).
            base_opts['format'] = 'best/bestvideo*,bestaudio'
            base_opts['outtmpl'] = base_opts['outtmpl'].replace('.%(ext)s', '.f%(format_id)s.%(ext)s')
            _warn_no_ffmpeg(self.logger)
        base_opts.update(custom)
        base_opts.update(self.extra_yt_opts(options))

        verbose = options.get('verbose')
        if verbose:
            base_opts['quiet'] = False
            base_opts['no_warnings'] = False

        return base_opts

    def _resolve_cookie_path(self, options: Dict) -> Optional[Path]:
        cookie_override = options.get('cookiefile') or options.get('cookie_path')
        if isinstance(cookie_override, str) and cookie_override:
            path = Path(cookie_override)
            path.parent.mkdir(parents=True, exist_ok=True)
            return path

        use_session = options.get('use_session', True)
        if not use_session:
            return None

        # Only use the saved cookies file when it holds a sign-in. Pointing
        # yt-dlp at a missing or anonymous file makes it save visitor cookies
        # there afterwards, which then look like a sign-in.
        return signins.cookie_file_for_download(self.source_key)


__all__ = ['YtDlpHandler', 'YTDLP_AVAILABLE', 'NO_FFMPEG_MESSAGE', 'ffmpeg_available', 'layout_template',
           'scrub_info_json']
