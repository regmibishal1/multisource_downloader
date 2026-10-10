"""Core dispatcher for the multidownloader package.

Provides a Downloader class that delegates to source-specific handlers.
"""
from .sources.facebook import FacebookHandler
from .sources.gdrive import GoogleDriveHandler
from .sources.instagram import InstagramHandler
from .sources.reddit import RedditHandler
from .sources.threads import ThreadsHandler
from .sources.tiktok import TikTokHandler
from .sources.twitter import TwitterHandler
from .sources.youtube import YouTubeHandler
from . import platforms, signins


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
        try:
            result = handler.download(url, out_dir or self.out_dir, options or {})
        except Exception as exc:
            signins.record_result(source_name, False, str(exc))
            raise
        signins.record_result(source_name, True)
        return result

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

