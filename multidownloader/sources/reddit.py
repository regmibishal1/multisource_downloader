"""Reddit handler via yt-dlp."""
from __future__ import annotations

from .yt_dlp_base import YtDlpHandler


class RedditHandler(YtDlpHandler):
    def __init__(self, logger=None):
        super().__init__('Reddit', logger=logger, outtmpl='reddit/%(uploader)s/%(title)s [%(id)s].%(ext)s')

