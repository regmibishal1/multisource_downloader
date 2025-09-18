"""Twitter/X handler via yt-dlp."""
from __future__ import annotations

from .yt_dlp_base import YtDlpHandler


class TwitterHandler(YtDlpHandler):
    def __init__(self, logger=None):
        super().__init__('Twitter', logger=logger, outtmpl='twitter/%(uploader_id)s/%(upload_date)s_%(id)s.%(ext)s')

