"""TikTok handler using yt-dlp."""
from __future__ import annotations

from .yt_dlp_base import YtDlpHandler


class TikTokHandler(YtDlpHandler):
    def __init__(self, logger=None):
        super().__init__('TikTok', logger=logger, outtmpl='%(uploader)s/%(title)s [%(id)s].%(ext)s')

