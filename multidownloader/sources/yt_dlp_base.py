"""Shared yt-dlp handler utilities."""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Dict, Optional

try:  # modern yt-dlp versions keep DownloadError in utils but may drop helpers
    import yt_dlp
    from yt_dlp.utils import DownloadError
    try:
        from yt_dlp.utils import save_cookies_to_file  # optional helper (removed in some builds)
    except Exception:  # pragma: no cover - helper is optional
        save_cookies_to_file = None
    YTDLP_AVAILABLE = True
except Exception:  # pragma: no cover - module might be missing in minimal envs
    yt_dlp = None
    DownloadError = Exception
    save_cookies_to_file = None
    YTDLP_AVAILABLE = False

from .. import session_store


class YtDlpHandler:
    """Base handler that wraps yt_dlp with cookie/session persistence support."""

    source_key: str = 'generic'
    logger: logging.Logger

    def __init__(self, source_key: str, *, logger: Optional[logging.Logger] = None, outtmpl: Optional[str] = None):
        self.source_key = source_key
        self.logger = logger or logging.getLogger(f'multidownloader.{source_key.lower()}')
        self.outtmpl = outtmpl or '%(title)s [%(id)s].%(ext)s'
        session_store.ensure_session_dir(self.source_key)

    # --- hooks for subclasses -------------------------------------------------
    def extra_yt_opts(self, options: Dict) -> Dict:
        """Subclasses can override to provide additional yt-dlp options."""
        return {}

    # --- public API ----------------------------------------------------------
    def download(self, url: str, out_dir: str, options: Optional[Dict] = None):
        if not YTDLP_AVAILABLE:
            raise RuntimeError('yt-dlp not available - install it to use this handler')

        options = options or {}
        os.makedirs(out_dir, exist_ok=True)

        cookie_path = self._resolve_cookie_path(options)
        ydl_opts = self._build_opts(out_dir, options, cookie_path)

        self.logger.info('yt-dlp download start: source=%s url=%s', self.source_key, url)

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])
                self.logger.info('yt-dlp download complete: %s', url)
                if cookie_path and save_cookies_to_file:
                    try:
                        save_cookies_to_file(ydl.cookiejar, str(cookie_path), ignore_discard=True, ignore_expires=True)
                        self.logger.debug('Updated cookies saved to %s', cookie_path)
                    except Exception as exc:  # pragma: no cover - best effort
                        self.logger.warning('Failed to persist cookies for %s: %s', self.source_key, exc)
        except DownloadError as exc:
            raise RuntimeError(f'yt-dlp failed for {self.source_key}: {exc}') from exc

        return True

    # --- internal helpers ----------------------------------------------------
    def _build_opts(self, out_dir: str, options: Dict, cookie_path: Optional[Path]) -> Dict:
        base_opts: Dict = {
            'outtmpl': os.path.join(out_dir, self.outtmpl),
            'noplaylist': True,
            'quiet': True,
            'no_warnings': True,
            'retries': 2,
            'concurrent_fragment_downloads': 2,
        }

        if cookie_path:
            base_opts['cookiefile'] = str(cookie_path)

        custom = options.get('ytdlp_opts') or {}
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

        cookie_path = session_store.default_cookie_path(self.source_key)
        cookie_path.parent.mkdir(parents=True, exist_ok=True)
        return cookie_path


__all__ = ['YtDlpHandler', 'YTDLP_AVAILABLE']
