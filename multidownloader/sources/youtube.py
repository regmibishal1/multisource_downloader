"""YouTube handler via yt-dlp."""
from __future__ import annotations

from .yt_dlp_base import YtDlpHandler


class YouTubeHandler(YtDlpHandler):
    def __init__(self, logger=None):
        super().__init__('YouTube', logger=logger, outtmpl='youtube/%(channel)s/%(title)s [%(id)s].%(ext)s')

