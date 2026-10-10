"""Core dispatcher for the multidownloader package.

Provides a Downloader class that delegates to source-specific handlers.
"""
import logging

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

THREADS_IMAGES = 'Image posts from Threads are not supported yet.'


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

    def download(self, source_name, url, options=None, out_dir=None):
        """Download url with the named handler into out_dir (default: the Downloader's folder)."""
        if source_name not in self.handlers:
            raise ValueError(f'Unknown source: {source_name}')
        handler = self.handlers[source_name]
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
                result = self._images_instead(source_name, url, target, options, exc)
        except Exception as exc:
            signins.record_result(source_name, False, str(exc))
            raise
        signins.record_result(source_name, True)
        return result

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
                                   cancel_event=options.get('cancel_event'))
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

