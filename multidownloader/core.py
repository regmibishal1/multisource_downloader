"""Core dispatcher for the multidownloader package.

Provides a Downloader class that delegates to source-specific handlers.
"""
import logging
import shutil
from pathlib import Path

from .backends import gallerydl
from .sources.bluesky import BlueskyHandler
from .sources.facebook import FacebookHandler
from .sources.gdrive import GoogleDriveHandler
from .sources.instagram import InstagramHandler
from .sources.reddit import RedditHandler
from .sources.threads import ThreadsHandler
from .sources.tiktok import TikTokHandler
from .sources.twitter import TwitterHandler
from .sources.youtube import YouTubeHandler
from . import errors, platforms, signins
from .backends.base import require

THREADS_IMAGES = 'Image posts from Threads are not supported yet.'
# Instaloader failures that another program would hit too, or that need the user: no second try
NO_SECOND_BACKEND = {errors.AUTH_REQUIRED, errors.SIGNIN_REJECTED, errors.CHECKPOINT, errors.ACCOUNT_LOCKED,
                     errors.PRIVATE, errors.NOT_FOUND, errors.RATE_LIMITED, errors.BOT_CHECK, errors.NETWORK,
                     errors.DISK, errors.GEO_BLOCKED}


class Downloader:
    def __init__(self, out_dir, logger=None):
        self.out_dir = out_dir
        self.logger = logger
        self._refreshed = set()
        self.handlers = {
            'Google Drive': GoogleDriveHandler(logger=logger),
            'Instagram': InstagramHandler(logger=logger),
            'TikTok': TikTokHandler(logger=logger),
            'Threads': ThreadsHandler(logger=logger),
            'Twitter': TwitterHandler(logger=logger),
            'Reddit': RedditHandler(logger=logger),
            'Facebook': FacebookHandler(logger=logger),
            'YouTube': YouTubeHandler(logger=logger),
            'Bluesky': BlueskyHandler(logger=logger),
        }

    def list_sources(self):
        """Return the list of supported source names."""
        return list(self.handlers.keys())

    def backend_status(self):
        """[(source, backend name, available, reason)] for diagnostics."""
        return [(source, h.name, *h.available()) for source, h in self.handlers.items()]

    def download(self, source_name, url, options=None, out_dir=None):
        """Download url with the named handler into out_dir (default: the Downloader's folder)."""
        if source_name not in self.handlers:
            raise ValueError(f'Unknown source: {source_name}')
        handler = self.handlers[source_name]
        require(handler)  # a missing library gets its own "install ..." message
        if source_name not in self._refreshed:
            # browser sign-ins are re-read once per run, so the browser's
            # current cookies are used without exporting anything again
            self._refreshed.add(source_name)
            signins.refresh_from_browser(platforms.BY_HANDLER[source_name])
        options = options or {}
        target = out_dir or self.out_dir
        try:
            try:
                result = handler.download(url, target, options)
            except Exception as exc:
                if source_name == 'Instagram':
                    result = self._instagram_with_ytdlp(url, target, options, exc)
                else:
                    result = self._images_instead(source_name, url, target, options, exc)
        except Exception as exc:
            signins.record_result(source_name, False, str(exc))
            raise
        signins.record_result(source_name, True)
        return result

    def _instagram_with_ytdlp(self, url, out_dir, options, exc):
        """When Instaloader fails for a reason other than the sign-in or the post itself, try yt-dlp with the
        same stored sign-in, and gallery-dl after it for photo posts (brief F5). Instagram changes its API
        often, and the two libraries rarely break at the same time."""
        from .sources.yt_dlp_base import YTDLP_AVAILABLE, YtDlpHandler

        kind = errors.classify(exc, platform='Instagram').kind
        if kind in NO_SECOND_BACKEND or not YTDLP_AVAILABLE or (options.get('cancel_event') is not None
                                                                and options['cancel_event'].is_set()):
            raise exc
        log = self.logger or logging.getLogger(__name__)
        log.info('Instaloader could not get %s (%s). Trying yt-dlp...', url, exc)
        _clear_staging(out_dir)  # nothing Instaloader half-saved is mixed with the second try
        handler = getattr(self, '_ytdlp_instagram', None) or YtDlpHandler('Instagram', logger=self.logger)
        self._ytdlp_instagram = handler
        second = {k: v for k, v in options.items() if k != 'auth'}
        second['use_session'] = True
        try:
            outcome = handler.download(url, out_dir, second)
        except Exception as ytdlp_exc:
            log.info('yt-dlp could not get %s either: %s', url, ytdlp_exc)
            if errors.classify(ytdlp_exc).kind == errors.NO_MEDIA:
                _clear_staging(out_dir)
                return self._images_instead('Instagram', url, out_dir, options, ytdlp_exc)
            raise exc from ytdlp_exc  # the first error says best what went wrong
        outcome = dict(outcome) if isinstance(outcome, dict) else {}
        outcome['backend'] = handler.name
        return outcome

    def _images_instead(self, source_name, url, out_dir, options, exc):
        """When yt-dlp finds no video in a post, get its images with gallery-dl (brief F5).

        Re-raises exc when the post is not a no-video case or the platform has
        no image backend.
        """
        platform = platforms.BY_HANDLER.get(source_name)
        if not platform or errors.classify(exc).kind != errors.NO_MEDIA:
            raise exc
        if platform.key == 'threads':
            raise RuntimeError(THREADS_IMAGES) from exc
        if gallerydl.NAME not in platform.backends or platform.backends[0] == gallerydl.NAME:
            raise exc
        log = self.logger or logging.getLogger(__name__)
        log.info('No video found in %s. Trying image download...', url)
        files = gallerydl.download(url, out_dir, platform_key=platform.key,
                                   cookie_file=signins.cookie_file_for_download(source_name),
                                   user_agent=signins.user_agent_for(source_name),
                                   cancel_event=options.get('cancel_event'),
                                   layout=options.get('layout', 'account'),
                                   write_metadata=options.get('write_metadata', True))
        if not files:
            raise exc
        return {'backend': gallerydl.NAME, 'files': files}

    def authenticate(self, source_name, root=None):
        """Proxy to handler interactive authentication if available.
        Returns whatever the handler's auth method returns (e.g., Instaloader instance) or None.
        """
        if source_name not in self.handlers:
            raise ValueError(f'Unknown source: {source_name}')
        handler = self.handlers[source_name]
        # Prefer an interactive_auth method if present
        if hasattr(handler, 'interactive_auth'):
            return handler.interactive_auth(root=root)
        # No interactive auth available
        return None


def _clear_staging(out_dir) -> None:
    """Empty a job's staging folder (only ever one under _staging), before another backend tries the link."""
    folder = Path(out_dir)
    if folder.parent.name != '_staging' or not folder.is_dir():
        return
    for child in folder.iterdir():
        if child.is_dir():
            shutil.rmtree(child, ignore_errors=True)
        else:
            child.unlink(missing_ok=True)
