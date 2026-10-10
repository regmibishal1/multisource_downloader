"""Bluesky handler: gallery-dl for posts (images and video), yt-dlp when gallery-dl is missing."""
from __future__ import annotations

import logging

from ..backends import gallerydl
from .yt_dlp_base import YtDlpHandler


class BlueskyHandler:
    def __init__(self, logger=None):
        self.logger = logger or logging.getLogger(__name__)
        self._ytdlp = YtDlpHandler('Bluesky', logger=self.logger)

    def download(self, url, out_dir, options):
        if gallerydl.available()[0]:
            files = gallerydl.download(url, out_dir, platform_key='bluesky', cancel_event=options.get('cancel_event'))
            return {'backend': gallerydl.NAME, 'files': files}
        self.logger.info('gallery-dl is not installed; trying yt-dlp for %s', url)
        return self._ytdlp.download(url, out_dir, options)


__all__ = ['BlueskyHandler']
