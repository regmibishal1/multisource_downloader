"""Threads handler via yt-dlp."""
from __future__ import annotations

from .yt_dlp_base import YtDlpHandler


class ThreadsHandler(YtDlpHandler):
    def __init__(self, logger=None):
        super().__init__('Threads', logger=logger)

