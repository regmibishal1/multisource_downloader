"""Core dispatcher for the multidownloader package.

Provides a Downloader class that delegates to source-specific handlers.
"""
from .sources.gdrive import GoogleDriveHandler
from .sources.instagram import InstagramHandler
from .sources.tiktok import TikTokHandler


class Downloader:
    def __init__(self, out_dir, logger=None):
        self.out_dir = out_dir
        self.logger = logger
        self.handlers = {
            'Google Drive': GoogleDriveHandler(logger=logger),
            'Instagram': InstagramHandler(logger=logger),
            'TikTok': TikTokHandler(logger=logger),
        }

    def download(self, source_name, url, options=None):
        if source_name not in self.handlers:
            raise ValueError(f'Unknown source: {source_name}')
        handler = self.handlers[source_name]
        return handler.download(url, self.out_dir, options or {})

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
